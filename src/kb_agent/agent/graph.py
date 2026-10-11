"""手写 StateGraph（D15-19）：route → rewrite → tool_router → exec_tools → generate/refuse → validate → retry。

状态机（面试核心，每一条边都能解释）：

    START → route ──> rewrite ──> tool_router ──(无工具)→ generate ┐
                                  │                              │
                               exec_tools <──────────────────────┤
                                  │                              │
                       ┌──────────┴─────────┐                    │
                       │ (query_kb 无候选/低置信)                 │
                       ▼                    ▼                    │
                    refuse             generate ──validate──┐    │
                       │                    ▲  校验失败且 retry<2 │
                       └──────► END ◄───────┴─────────────────┘

节点职责：
- route：入口登记（query/roles 校验，trace 起点）
- rewrite：多轮指代消解（messages 排除当前消息后取最近 4 条；无历史直通）
- tool_router：LLM function-calling 选择 query_kb / summarize_doc / 不调工具
- exec_tools：手写执行工具调用；query_kb 结果做拒答阈值判定并把候选转 dict
  （Redis checkpoint JSON 序列化安全，同时预生成引用标签）
- generate：候选拼接 + 引用规则生成答案（LLM 真流式，token 逐段发事件）
- refuse：按 reason 生成拒答话术（同样流式；输入级错误走预置话术）
- validate：程序化引用校验，失败回 generate 重试 ≤2，超限进 refuse(citation_failed)

多轮记忆（D18-19）：graph.compile(checkpointer=RedisSaver) + thread_id=session_id；
messages 带 add reducer，checkpoint 自动累积对话历史；generate/refuse 把回复写回
messages，下一轮 rewrite 即可看到。显式 history 参数用于无 checkpointer 的手动多轮。

SSE 事件（D18-19）：deps["events"] 为 queue.Queue 时节点发事件：
  tool_call → retrieved → token(attempt=N) → citations → refused / done(由 ask 收尾)
同步 graph.invoke 跑在 API 层后台线程，事件经队列桥接到异步 SSE 生成器。
"""
from __future__ import annotations

import json
import re
import threading
import uuid
from typing import Any

from langgraph.graph import END, START, StateGraph

from kb_agent.agent.llm import LLMClient
from kb_agent.agent.prompts import (
    GENERATE_SYSTEM,
    REFUSE_SYSTEM,
    REWRITE_SYSTEM,
    TOOL_ROUTER_SYSTEM,
)
from kb_agent.agent.state import AgentState
from kb_agent.agent.tools import TOOL_SCHEMAS, query_kb, summarize_doc
from kb_agent.ingest.sparse_bm25 import BM25Encoder
from kb_agent.models.embedder import Embedder
from kb_agent.models.reranker import Reranker
from kb_agent.retrieval.citations import format_citation, validate_citations
from kb_agent.retrieval.hybrid_retriever import HybridRetriever, RetrievedChunk
from kb_agent.store.qdrant_store import QdrantStore, expand_roles

MAX_RETRY = 2  # 引用校验失败的重试上限
REWRITE_HISTORY_LIMIT = 4  # 指代消解只回看最近 4 条消息

# 输入级错误用预置话术，不烧 LLM 调用
INPUT_ERROR_ANSWERS = {
    "empty_query": "请输入问题。",
    "no_role": "缺少访问角色信息。",
}


# 请求级事件队列：API 单例 agent 的 deps 在构建时固定，per-request 队列经
# thread-local 注入——每个请求跑独立后台线程，节点在同一线程内取到自己请求的队列
_event_queue = threading.local()


def _emit(deps: dict[str, Any], event: dict[str, Any]) -> None:
    """事件入队（无队列时静默，单测/评估路径不受影响）。

    优先 thread-local 队列（API 请求路径），fallback 到 deps["events"]（测试直调）。
    """
    events = getattr(_event_queue, "queue", None) or deps.get("events")
    if events is not None:
        events.put(event)


def _chunk_to_dict(c: RetrievedChunk) -> dict[str, Any]:
    """RetrievedChunk → dict：checkpoint JSON 序列化安全 + 预生成引用标签。"""
    return {
        "chunk_id": c.chunk_id,
        "doc_id": c.doc_id,
        "title": c.title,
        "text": c.text,
        "title_path": c.title_path,
        "page": c.page,
        "section": c.section,
        "role": c.role,
        "is_code": c.is_code,
        "rerank_score": c.rerank_score,
        "citation": format_citation(c),
    }


# 对比题拆分（确定性兜底）：router LLM 有时把完整对比问题原样塞进 query_kb，
# 完整问题的词面会偏向一侧导致另一侧检索不到。exec 层拆成两侧各自检索。
_COMPARE_TAIL_RE = re.compile(r"(?:有什么不同|有何不同|有什么区别|有什么差异|有何区别|的区别|的差异|不同点|差异|区别|对比|比较)$")
_COMPARE_CONJ_RE = re.compile(r"和|与|以及")


def _split_compare_query(query: str) -> list[str]:
    """对比题拆成两侧独立 query：'A和B的X有什么区别' → ['A的X', 'B的X']。

    'B的X' 里 '的X' 是两侧共享属性时补到左侧；拆不出（非对比句式）返回空列表。
    """
    q = (query or "").strip()
    if not _COMPARE_TAIL_RE.search(q):
        return []
    body = _COMPARE_TAIL_RE.sub("", q)  # 剥掉尾部比较词
    m = _COMPARE_CONJ_RE.search(body)
    if not m:
        return []
    left, right = body[: m.start()], body[m.end():]
    if not left or not right:
        return []
    # 共享属性：'A和B的X' → right='B的X' 且 left 无属性 → attr 补到左侧，右侧保持
    if "的" in right and "的" not in left:
        left = left + right[right.index("的"):]
    if not right or left == right:
        return []
    return [left, right]


# ---------- 节点 ----------


def _route(state: AgentState) -> dict[str, Any]:
    query = (state.get("query") or "").strip()
    roles = state.get("roles") or []
    if not query:
        return {"refused": True, "refuse_reason": "empty_query", "answer": "请输入问题。"}
    if not roles:
        return {"refused": True, "refuse_reason": "no_role", "answer": "缺少访问角色信息。"}
    # 正常入口显式声明 refused=False，保证任意路径结束的最终 state 都含该键
    return {"refused": False, "trace": [{"node": "route", "query": query, "roles": roles}]}


def _rewrite(state: AgentState, llm: LLMClient) -> dict[str, Any]:
    """多轮指代消解。messages 末尾是当前用户消息，排除后取最近 N 条作上下文。"""
    query = state["query"]
    history = (state.get("messages") or [])[:-1][-REWRITE_HISTORY_LIMIT:]
    if not history:
        return {"rewritten_query": query}
    reply = llm.chat(
        [
            {"role": "system", "content": REWRITE_SYSTEM},
            {
                "role": "user",
                "content": "对话历史：\n"
                + "\n".join(f"{m['role']}: {m.get('content', '')}" for m in history)
                + f"\n\n当前问题：{query}",
            },
        ],
        max_tokens=1024,
    )
    rewritten = reply.content.strip() or query
    return {"rewritten_query": rewritten, "trace": [{"node": "rewrite", "rewritten": rewritten}]}


def _tool_router(state: AgentState, llm: LLMClient, deps: dict[str, Any]) -> dict[str, Any]:
    """LLM function-calling 决定工具。无工具调用（寒暄/常识问题）→ 直答。"""
    reply = llm.chat(
        [
            {"role": "system", "content": TOOL_ROUTER_SYSTEM},
            {"role": "user", "content": state["rewritten_query"]},
        ],
        tools=TOOL_SCHEMAS,
        max_tokens=4096,
    )
    calls = reply.tool_calls or []
    _emit(
        deps,
        {
            "type": "tool_call",
            "tools": [c["name"] for c in calls],
            "query": state["rewritten_query"],
        },
    )
    if not calls:
        return {"tool_calls": [], "trace": [{"node": "tool_router", "tools": []}]}
    return {
        "tool_calls": calls,
        "trace": [{"node": "tool_router", "tools": [c["name"] for c in calls]}],
    }


def _exec_tools(state: AgentState, deps: dict[str, Any]) -> dict[str, Any]:
    """手写工具执行：query_kb 结果做拒答阈值判定；summarize_doc 产出摘要。"""
    llm: LLMClient = deps["llm"]
    retriever: HybridRetriever = deps["retriever"]
    store: QdrantStore = deps["store"]
    roles = state["roles"]

    results: list[dict[str, Any]] = []
    chunk_dicts: list[dict[str, Any]] = []
    # 多次 query_kb（对比题拆分检索）按 chunk_id 去重合并，避免后一次覆盖前一次
    by_chunk_id: dict[str, dict[str, Any]] = {}
    citations: list[str] = []
    refused, reason = False, ""
    kb_called = kb_ok = 0
    kb_reason = ""
    for call in state["tool_calls"]:
        try:
            args = json.loads(call["arguments"])
        except json.JSONDecodeError:
            args = {}
        if call["name"] == "query_kb":
            kb_called += 1
            q = args.get("query", state["rewritten_query"]) or state["rewritten_query"]
            # 对比题兜底拆分：拆出两侧就各自检索，拆不出按原 query 检索
            for sub_q in _split_compare_query(q) or [q]:
                r = query_kb(sub_q, roles, retriever)
                results.append({"tool": "query_kb", "query": sub_q, "result": r})
                # 单次调用的候选（事件用）；并入全局去重集合
                call_dicts = [_chunk_to_dict(c) for c in r["chunks"]]
                for d in call_dicts:
                    prev = by_chunk_id.get(d["chunk_id"])
                    if prev is None or d["rerank_score"] > prev["rerank_score"]:
                        by_chunk_id[d["chunk_id"]] = d
                chunk_dicts = list(by_chunk_id.values())
                citations = [d["citation"] for d in chunk_dicts]
                _emit(
                    deps,
                    {
                        "type": "retrieved",
                        "query": sub_q,
                        "count": len(call_dicts),
                        "top1": call_dicts[0]["title"] if call_dicts else None,
                        # 候选摘要（截断文本）供前端轨迹面板展示
                        "candidates": [
                            {
                                "citation": d["citation"],
                                "rerank_score": d["rerank_score"],
                                "text": (d["text"] or "")[:120],
                            }
                            for d in call_dicts
                        ],
                    },
                )
                if r["refused"]:
                    kb_reason = kb_reason or r["refuse_reason"]
                else:
                    kb_ok += 1
        elif call["name"] == "summarize_doc":
            r = summarize_doc(
                args.get("title", ""),
                args.get("question", ""),
                roles,
                store,
                llm,
            )
            results.append({"tool": "summarize_doc", "title": args.get("title"), "result": r})
            if r["refused"]:
                refused, reason = True, r["refuse_reason"]
        else:
            results.append({"tool": call["name"], "error": "unknown_tool"})
            refused, reason = True, "unknown_tool"

    # query_kb 全部拒绝才整体拒绝（部分命中即视为有证据）；summarize/unknown 任一即拒
    if kb_called and kb_ok == 0:
        refused, reason = True, kb_reason or "low_confidence"

    return {
        "tool_results": results,
        "chunks": chunk_dicts,
        "citations": citations,
        "refused": refused,
        "refuse_reason": reason,
        "trace": [{"node": "exec_tools", "tools": [r["tool"] for r in results]}],
    }


def _generate(state: AgentState, llm: LLMClient, deps: dict[str, Any]) -> dict[str, Any]:
    """候选拼接 → 流式生成带 [n] 引用的答案；回复写回 messages 供下一轮改写。"""
    summaries = [r for r in state.get("tool_results") or [] if r.get("tool") == "summarize_doc"]
    chunks = state.get("chunks") or []

    if summaries and not chunks:
        answer = summaries[0]["result"]["summary"] or "该文档没有可总结的内容。"
        _emit(deps, {"type": "token", "text": answer, "attempt": state.get("retry_count", 0)})
        return {
            "answer": answer,
            "messages": [{"role": "assistant", "content": answer}],
            "trace": [{"node": "generate", "mode": "summary"}],
        }

    context_lines = []
    for i, c in enumerate(chunks, 1):
        context_lines.append(f"[{i}] {c['citation']}\n{c['text']}")
    feedback = state.get("retry_feedback", "")
    if feedback:
        context_lines.append(f"\n（上次回答被拒原因：{feedback}，请修正后重新生成）")

    messages = [
        {"role": "system", "content": GENERATE_SYSTEM.format(context="\n\n".join(context_lines))},
        {"role": "user", "content": state["rewritten_query"]},
    ]
    attempt = state.get("retry_count", 0)
    parts: list[str] = []
    for delta in llm.chat_stream(messages, max_tokens=8192):
        parts.append(delta)
        _emit(deps, {"type": "token", "text": delta, "attempt": attempt})
    answer = "".join(parts).strip()
    _emit(deps, {"type": "citations", "citations": state.get("citations") or []})
    return {
        "answer": answer,
        "messages": [{"role": "assistant", "content": answer}],
        "trace": [
            {
                "node": "generate",
                "mode": "retrieval",
                "candidate_count": len(chunks),
                "finish_reason": getattr(llm, "last_finish_reason", None),
            }
        ],
    }


def _refuse(state: AgentState, llm: LLMClient, deps: dict[str, Any]) -> dict[str, Any]:
    """按 reason 生成拒答话术。不传候选、不编造；输入级错误走预置话术。"""
    reason = state.get("refuse_reason") or "no_candidates"
    preset = INPUT_ERROR_ANSWERS.get(reason)
    if preset:
        _emit(deps, {"type": "token", "text": preset, "attempt": 0})
        return {
            "refused": True,
            "refuse_reason": reason,
            "answer": preset,
            "messages": [{"role": "assistant", "content": preset}],
            "trace": [{"node": "refuse", "reason": reason}],
        }
    messages = [
        {"role": "system", "content": REFUSE_SYSTEM},
        {"role": "user", "content": f"用户问题：{state['query']}\n拒答原因：{reason}"},
    ]
    parts: list[str] = []
    for delta in llm.chat_stream(messages, max_tokens=2048):
        parts.append(delta)
        _emit(deps, {"type": "token", "text": delta, "attempt": 0})
    answer = "".join(parts).strip()
    _emit(deps, {"type": "refused", "reason": reason})
    return {
        "refused": True,
        "refuse_reason": reason,
        "answer": answer,
        "messages": [{"role": "assistant", "content": answer}],
        "trace": [{"node": "refuse", "reason": reason}],
    }


def _validate(state: AgentState) -> dict[str, Any]:
    """程序化引用校验。失败 → 回 generate 重试（≤2）；重试耗尽 → refuse。

    通过/耗尽分支必须显式清空 retry_feedback——langgraph 的 state 更新是
    last-write-wins，残留的 retry_feedback 会让条件边永远拉回 generate。
    """
    chunks = state.get("chunks") or []
    problems = validate_citations(state.get("answer", ""), chunks)
    if not problems:
        return {"retry_feedback": "", "trace": [{"node": "validate", "ok": True}]}
    retry = state.get("retry_count", 0)
    if retry < MAX_RETRY:
        return {
            "retry_count": retry + 1,
            "retry_feedback": "；".join(problems),
            "trace": [{"node": "validate", "ok": False, "retry": retry + 1, "problems": problems}],
        }
    return {
        "refused": True,
        "refuse_reason": "citation_failed",
        "retry_feedback": "",
        "trace": [{"node": "validate", "ok": False, "exhausted": True, "problems": problems}],
    }


# ---------- 条件路由 ----------


def _after_route(state: AgentState) -> str:
    return "refuse" if state.get("refused") else "rewrite"


def _after_tool_router(state: AgentState) -> str:
    return "exec_tools" if state.get("tool_calls") else "generate"


def _after_exec(state: AgentState) -> str:
    return "refuse" if state.get("refused") else "generate"


def _after_validate(state: AgentState) -> str:
    if state.get("refused"):
        return "refuse"
    if state.get("retry_feedback"):
        return "generate"
    return END


# ---------- 构建 ----------


def build_graph(deps: dict[str, Any]) -> Any:
    """构建编译后的 StateGraph。deps: {llm, retriever, store, events?}。

    checkpointer 由调用方 compile 时挂载（graph.compile(checkpointer=...)），
    此处返回编译前的 builder 便于注入。"""
    g = StateGraph(AgentState)
    g.add_node("route", _route)
    g.add_node("rewrite", lambda s: _rewrite(s, deps["llm"]))
    g.add_node("tool_router", lambda s: _tool_router(s, deps["llm"], deps))
    g.add_node("exec_tools", lambda s: _exec_tools(s, deps))
    g.add_node("generate", lambda s: _generate(s, deps["llm"], deps))
    g.add_node("refuse", lambda s: _refuse(s, deps["llm"], deps))
    g.add_node("validate", _validate)

    g.add_edge(START, "route")
    g.add_conditional_edges("route", _after_route, {"rewrite": "rewrite", "refuse": "refuse"})
    g.add_edge("rewrite", "tool_router")
    g.add_conditional_edges(
        "tool_router", _after_tool_router, {"exec_tools": "exec_tools", "generate": "generate"}
    )
    g.add_conditional_edges("exec_tools", _after_exec, {"refuse": "refuse", "generate": "generate"})
    g.add_edge("generate", "validate")
    g.add_conditional_edges(
        "validate", _after_validate, {"generate": "generate", "refuse": "refuse", END: END}
    )
    g.add_edge("refuse", END)
    return g


class KBAgent:
    """Agent 门面：注入模型/存储依赖，暴露 ask(query, role, session_id, events)。

    - role 入参是单个请求角色（如 "contractor"），内部 expand 为可见层级集合
    - session_id + checkpointer：多轮记忆（messages 自动累积）
    - history：无 checkpointer 时的手动多轮上下文
    - events：queue.Queue，非空时节点发 SSE 事件，ask 收尾发 done
    """

    def __init__(
        self,
        store: QdrantStore,
        embedder: Embedder,
        bm25: BM25Encoder,
        reranker: Reranker,
        llm: LLMClient,
        checkpointer: Any | None = None,
        cache: Any | None = None,
    ) -> None:
        retriever = HybridRetriever(store, embedder, bm25, reranker)
        self.retriever = retriever
        self.checkpointer = checkpointer
        self.cache = cache
        deps: dict[str, Any] = {"llm": llm, "retriever": retriever, "store": store}
        builder = build_graph(deps)
        self.graph = builder.compile(checkpointer=checkpointer) if checkpointer else builder.compile()

    def ask(
        self,
        query: str,
        role: str,
        session_id: str | None = None,
        history: list[dict[str, Any]] | None = None,
        events: Any | None = None,
    ) -> dict[str, Any]:
        """问答入口。返回 answer/citations/refused/refuse_reason/rewritten_query/trace/cached。"""
        # L1 精确缓存：只对单轮请求生效（多轮依赖 checkpoint 上下文，无法独立成 key）
        cacheable = self.cache is not None and not session_id and not history
        if cacheable and (cached := self.cache.get(query, role)) is not None:
            # 缓存值只存回答四要素（不含 trace），命中以 cache_hit 标记供前端识别；
            # 命中必是单轮（无历史 rewrite 直通），rewritten_query 即原始 query
            result = {**cached, "cached": True, "rewritten_query": query, "trace": [{"node": "cache_hit"}]}
            if events is not None:
                events.put({"type": "cached", "latency_ms": 0})
                events.put({"type": "done", **result})
            return result

        state: AgentState = {
            "query": query,
            "roles": sorted(expand_roles([role])),
            "messages": (history or []) + [{"role": "user", "content": query}],
            "retry_count": 0,
        }
        config = None
        if self.checkpointer:
            # 单轮请求没有 session_id 时用一次性 thread_id：
            # RedisSaver 要求 configurable 至少有一个 key，且与多轮走同一代码路径
            config = {"configurable": {"thread_id": session_id or f"oneshot-{uuid.uuid4().hex}"}}
        if events is not None:
            _event_queue.queue = events
        try:
            final = self.graph.invoke(state, config)
        finally:
            if events is not None:
                _event_queue.queue = None
        result = {
            "answer": final.get("answer", ""),
            "citations": final.get("citations") or [],
            "refused": final.get("refused", False),
            "refuse_reason": final.get("refuse_reason", ""),
            "rewritten_query": final.get("rewritten_query", query),
            "trace": final.get("trace") or [],
            "cached": False,
        }
        # 写缓存（精简四要素，trace 不落库）：输入级错误不缓存（key 会被空话术污染）
        if cacheable and result["refuse_reason"] not in INPUT_ERROR_ANSWERS:
            self.cache.set(
                query,
                role,
                {
                    "answer": result["answer"],
                    "citations": result["citations"],
                    "refused": result["refused"],
                    "refuse_reason": result["refuse_reason"],
                },
            )
        if events is not None:
            events.put({"type": "done", **result})
        return result
