"""Agent 状态机单测（fake LLM / fake retriever / fake store，不碰真实模型与索引）。

覆盖：空输入拒答、单轮全链路、拒答三分支（无候选/低置信/引用失败）、
重试上限、寒暄直答、多轮改写、SSE 事件流、checkpoint 多轮记忆。
"""
from __future__ import annotations

import queue

from langgraph.checkpoint.memory import InMemorySaver

from kb_agent.agent.graph import MAX_RETRY, _split_compare_query, build_graph
from kb_agent.agent.llm import LLMReply
from kb_agent.agent.state import AgentState
from kb_agent.retrieval.hybrid_retriever import RetrievedChunk

# ---------- fakes ----------


class FakeLLM:
    """chat 按 replies 顺序吐回复；chat_stream 按 streams 顺序吐 token 流。"""

    def __init__(
        self,
        replies: list[LLMReply] | None = None,
        streams: list[list[str]] | None = None,
    ) -> None:
        self.replies = list(replies or [])
        self.streams = list(streams or [])
        self.calls: list[dict] = []
        self.stream_calls: list[dict] = []

    def chat(self, messages, tools=None, max_tokens=None, temperature=0.1):
        self.calls.append({"messages": messages, "tools": tools, "max_tokens": max_tokens})
        return self.replies.pop(0)

    def chat_stream(self, messages, max_tokens=None, temperature=0.1):
        self.stream_calls.append({"messages": messages, "max_tokens": max_tokens})
        return iter(self.streams.pop(0) if self.streams else [])


class FakeRetriever:
    def __init__(self, chunks: list[RetrievedChunk] | None) -> None:
        self.chunks = chunks
        self.calls: list[dict] = []

    def retrieve(self, query, roles, top_k=5, with_rerank=True, timings=None):
        self.calls.append({"query": query, "roles": roles, "top_k": top_k})
        return list(self.chunks or [])


class FakeStore:
    """query_kb 路径不碰 store；summarize_doc 路径由 tools 单测覆盖。"""

    @property
    def client(self):
        raise AssertionError("单测不应访问真实 store")


def _chunk(cid: str = "c1", rerank: float = 0.95, title: str = "年假与调休制度") -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=cid,
        doc_id="doc1#md",
        title=title,
        text="年假满 1 年不满 10 年为 5 天。",
        title_path=[],
        page=None,
        section="2.1",
        role="employee",
        is_code=False,
        rrf_score=0.9,
        rerank_score=rerank,
    )


def _tool_call(
    name: str = "query_kb",
    arguments: str = '{"query": "年假有几天"}',
    call_id: str = "call-1",
) -> dict:
    return {"id": call_id, "name": name, "arguments": arguments}


class QueryKeyedRetriever:
    """按 query 关键词返回不同 chunk 集合（多 query_kb 测试用），无匹配返回空。"""

    def __init__(self, mapping: dict[str, list[RetrievedChunk]]) -> None:
        self.mapping = mapping
        self.calls: list[dict] = []

    def retrieve(self, query, roles, top_k=5, with_rerank=True, timings=None):
        self.calls.append({"query": query, "roles": roles, "top_k": top_k})
        for key, chunks in self.mapping.items():
            if key in query:
                return list(chunks)
        return []


def _build(
    replies: list[LLMReply],
    streams: list[list[str]] | None = None,
    chunks: list[RetrievedChunk] | None = None,
    checkpointer=None,
    events: queue.Queue | None = None,
):
    """编译图，返回 (graph, llm)。"""
    llm = FakeLLM(replies, streams)
    retriever = FakeRetriever(chunks)
    deps = {"llm": llm, "retriever": retriever, "store": FakeStore()}
    if events is not None:
        deps["events"] = events
    builder = build_graph(deps)
    graph = builder.compile(checkpointer=checkpointer) if checkpointer else builder.compile()
    return graph, llm


def _invoke(
    graph,
    query: str = "年假有几天",
    roles: list[str] | None = None,
    history: list[dict] | None = None,
    config: dict | None = None,
) -> dict:
    state: AgentState = {
        "query": query,
        "roles": roles or ["public", "contractor", "employee"],
        "messages": (history or []) + [{"role": "user", "content": query}],
        "retry_count": 0,
    }
    return graph.invoke(state, config)


def _drain(q: queue.Queue) -> list[dict]:
    """非阻塞取空队列（graph.invoke 同步完成，事件应已全部入队）。"""
    out = []
    while True:
        try:
            item = q.get_nowait()
        except queue.Empty:
            break
        if item is None:
            break
        out.append(item)
    return out


# ---------- 用例 ----------


def test_empty_query_refuses_before_llm() -> None:
    graph, llm = _build([])
    out = _invoke(graph, query="   ")
    assert out["refused"] and out["refuse_reason"] == "empty_query"
    assert llm.calls == [] and llm.stream_calls == []  # 不浪费任何 LLM 调用


def test_single_hop_full_chain() -> None:
    graph, llm = _build(
        [LLMReply(content="", tool_calls=[_tool_call()])],
        streams=[["年假满 1 年不满 10 年为 5 天[1]。"]],
        chunks=[_chunk()],
    )
    out = _invoke(graph)
    assert not out["refused"] and "5 天" in out["answer"]
    assert out["citations"] == ["《年假与调休制度》§2.1"]
    assert [t["node"] for t in out["trace"]] == ["route", "tool_router", "exec_tools", "generate", "validate"]
    assert llm.stream_calls[0]["max_tokens"] == 8192  # 推理模型预算


def test_chunks_are_serializable_dicts() -> None:
    graph, _ = _build(
        [LLMReply(content="", tool_calls=[_tool_call()])],
        streams=[["答案[1]"]],
        chunks=[_chunk()],
    )
    out = _invoke(graph)
    c = out["chunks"][0]
    assert isinstance(c, dict) and c["citation"] == "《年假与调休制度》§2.1"
    assert c["rerank_score"] == 0.95


def test_query_kb_no_candidates_refuses() -> None:
    graph, _ = _build(
        [LLMReply(content="", tool_calls=[_tool_call()])],
        streams=[["知识库中未找到相关内容。"]],
        chunks=[],
    )
    out = _invoke(graph)
    assert out["refused"] and out["refuse_reason"] == "no_candidates"
    assert out["trace"][-1]["node"] == "refuse"


def test_query_kb_low_confidence_refuses() -> None:
    graph, _ = _build(
        [LLMReply(content="", tool_calls=[_tool_call()])],
        streams=[["已有资料不足以可靠回答。"]],
        chunks=[_chunk(rerank=0.5)],  # 低于标定阈值 0.7
    )
    out = _invoke(graph)
    assert out["refused"] and out["refuse_reason"] == "low_confidence"


def test_split_compare_query_variants() -> None:
    """对比句式拆成两侧独立 query；非对比句式不拆。"""
    assert _split_compare_query("内部订单API和公开API的鉴权方式有什么不同") == [
        "内部订单API的鉴权方式",
        "公开API的鉴权方式",
    ]
    assert _split_compare_query("正式员工和外包人员的工时要求有什么区别") == [
        "正式员工的工时要求",
        "外包人员的工时要求",
    ]
    assert _split_compare_query("员工行为准则与外包驻场人员行为规范的差异") == [
        "员工行为准则",
        "外包驻场人员行为规范",
    ]
    # 无比较词尾/无并列连词不拆
    assert _split_compare_query("年假有几天") == []
    assert _split_compare_query("新员工入职流程") == []
    # 并列但非比较语义（尾部锚定不匹配）不拆
    assert _split_compare_query("年假和病假都算考勤吗") == []
    # 结尾标点（v2 QA 集统一带「？」）不影响拆分
    assert _split_compare_query("《服务条款》和《隐私政策》在用户数据处理与责任约定上有什么不同？") == [
        "《服务条款》在用户数据处理与责任约定上",
        "《隐私政策》在用户数据处理与责任约定上",
    ]
    assert _split_compare_query("正式员工与外包人员的工时要求有什么区别。") == [
        "正式员工的工时要求",
        "外包人员的工时要求",
    ]
    # 《》标题 + 对X 属性（qa-120 句式）也按书名号边界补属性
    assert _split_compare_query("《外包人员管理规范》和《驻场人员行为规范》对外包/驻场人员的约束重点有什么不同？") == [
        "《外包人员管理规范》对外包/驻场人员的约束重点",
        "《驻场人员行为规范》对外包/驻场人员的约束重点",
    ]


def test_multiple_query_kb_merges_and_partial_hit_not_refused() -> None:
    """对比题拆成两次 query_kb：结果按 chunk_id 合并，一侧命中即不整体拒绝。"""
    llm = FakeLLM(
        [
            LLMReply(
                content="",
                tool_calls=[
                    _tool_call(call_id="call-1", arguments='{"query": "内部订单API的鉴权方式"}'),
                    _tool_call(call_id="call-2", arguments='{"query": "公开API的鉴权方式"}'),
                ],
            )
        ],
        streams=[["内部 API 用 OAuth[1]，公开 API 用 API Key[2]。"]],
    )
    retriever = QueryKeyedRetriever({
        "内部订单API": [_chunk(cid="c1", title="订单服务API文档")],
        "公开API": [_chunk(cid="c2", title="公开API规范")],
    })
    graph = build_graph({"llm": llm, "retriever": retriever, "store": FakeStore()}).compile()
    out = _invoke(graph)
    assert not out["refused"]
    assert [c["chunk_id"] for c in out["chunks"]] == ["c1", "c2"]
    assert len(out["citations"]) == 2
    assert [c["query"] for c in retriever.calls] == ["内部订单API的鉴权方式", "公开API的鉴权方式"]


def test_single_query_kb_splits_comparison_with_trailing_mark() -> None:
    """router 把带问号的完整对比问题原样塞进 query_kb：exec 层兜底拆两侧。"""
    llm = FakeLLM(
        [
            LLMReply(
                content="",
                tool_calls=[_tool_call(arguments='{"query": "内部订单API和公开API的鉴权方式有什么不同？"}')],
            )
        ],
        streams=[["内部用 OAuth[1]，公开用 Key[2]。"]],
    )
    retriever = QueryKeyedRetriever({
        "内部订单API的鉴权方式": [_chunk(cid="c1")],
        "公开API的鉴权方式": [_chunk(cid="c2")],
    })
    graph = build_graph({"llm": llm, "retriever": retriever, "store": FakeStore()}).compile()
    out = _invoke(graph)
    assert [c["query"] for c in retriever.calls] == ["内部订单API的鉴权方式", "公开API的鉴权方式"]
    assert [c["chunk_id"] for c in out["chunks"]] == ["c1", "c2"]


def test_single_query_kb_appends_original_when_reformulated() -> None:
    """router 改写丢词面匹配时（qa-103），原题兜底再检索一遍，结果合并。"""
    llm = FakeLLM(
        [
            LLMReply(
                content="",
                tool_calls=[_tool_call(arguments='{"query": "外包人员有哪些福利"}')],
            )
        ],
        streams=[["外包人员不适用公司福利制度，节日礼品也不适用[1]。"]],
    )
    retriever = QueryKeyedRetriever({
        "外包人员可以享受公司节日礼品吗": [_chunk(cid="c1")],
    })
    graph = build_graph({"llm": llm, "retriever": retriever, "store": FakeStore()}).compile()
    out = _invoke(graph, query="外包人员可以享受公司节日礼品吗")
    # 原题兜底在前：词面命中的 gold 先进入候选序列
    assert [c["query"] for c in retriever.calls] == ["外包人员可以享受公司节日礼品吗", "外包人员有哪些福利"]
    assert not out["refused"]
    assert [c["chunk_id"] for c in out["chunks"]] == ["c1"]


def test_multiple_query_kb_dedupes_same_chunk() -> None:
    llm = FakeLLM(
        [
            LLMReply(
                content="",
                tool_calls=[
                    _tool_call(call_id="call-1", arguments='{"query": "A 的规则"}'),
                    _tool_call(call_id="call-2", arguments='{"query": "B 的规则"}'),
                ],
            )
        ],
        streams=[["规则[1]。"]],
    )
    retriever = QueryKeyedRetriever({
        "A 的规则": [_chunk(cid="c1")],
        "B 的规则": [_chunk(cid="c1")],  # 同一 chunk 两侧都命中，只保留一条
    })
    graph = build_graph({"llm": llm, "retriever": retriever, "store": FakeStore()}).compile()
    out = _invoke(graph)
    assert [c["chunk_id"] for c in out["chunks"]] == ["c1"]
    assert len(out["citations"]) == 1


def test_multiple_query_kb_all_empty_refuses() -> None:
    llm = FakeLLM(
        [
            LLMReply(
                content="",
                tool_calls=[
                    _tool_call(call_id="call-1", arguments='{"query": "A 的规则"}'),
                    _tool_call(call_id="call-2", arguments='{"query": "B 的规则"}'),
                ],
            )
        ],
        streams=[["知识库中未找到相关内容。"]],
    )
    graph = build_graph(
        {"llm": llm, "retriever": QueryKeyedRetriever({}), "store": FakeStore()}
    ).compile()
    out = _invoke(graph)
    assert out["refused"] and out["refuse_reason"] == "no_candidates"


def test_greeting_without_tool_answers_directly() -> None:
    graph, _ = _build(
        [LLMReply(content="", tool_calls=[])],
        streams=[["你好！有什么可以帮您？"]],
        chunks=[],
    )
    out = _invoke(graph, query="你好")
    assert not out["refused"] and "你好" in out["answer"]


def test_citation_failure_retries_then_passes() -> None:
    graph, _ = _build(
        [LLMReply(content="", tool_calls=[_tool_call()])],
        streams=[["答案引用了一个不存在的编号[9]。"], ["年假 5 天[1]。"]],
        chunks=[_chunk()],
    )
    out = _invoke(graph)
    assert not out["refused"]
    assert out["retry_count"] == 1
    assert [t["node"] for t in out["trace"]].count("generate") == 2


def test_citation_failure_exhausts_retry_then_refuses() -> None:
    graph, _ = _build(
        [LLMReply(content="", tool_calls=[_tool_call()])],
        streams=[["坏引用[9]。"], ["还是坏引用[9]。"], ["依然是坏引用[9]。"], ["无法提供可靠回答。"]],
        chunks=[_chunk()],
    )
    out = _invoke(graph)
    assert out["refused"] and out["refuse_reason"] == "citation_failed"
    assert out["retry_count"] == MAX_RETRY
    assert [t["node"] for t in out["trace"]].count("generate") == 3


def test_rewrite_passthrough_without_history() -> None:
    graph, llm = _build(
        [LLMReply(content="", tool_calls=[_tool_call()])],
        streams=[["答案[1]。"]],
        chunks=[_chunk()],
    )
    out = _invoke(graph)
    assert not [t for t in out["trace"] if t["node"] == "rewrite"]  # 直通无 trace
    assert llm.calls[0]["tools"]  # 第一个 chat 调用是 tool_router（不是 rewrite）


def test_rewrite_with_history_uses_llm() -> None:
    graph, llm = _build(
        [
            LLMReply(content="年假制度的规定天数是多少"),  # rewrite
            LLMReply(content="", tool_calls=[_tool_call(arguments='{"query": "年假制度的规定天数是多少"}')]),
        ],
        streams=[["5 天[1]。"]],
        chunks=[_chunk()],
    )
    history = [{"role": "user", "content": "员工有哪些福利"}, {"role": "assistant", "content": "…"}]
    out = _invoke(graph, query="它规定几天", history=history)
    assert out["rewritten_query"] == "年假制度的规定天数是多少"
    rewrite_msg = llm.calls[0]["messages"][1]["content"]
    assert "当前问题" in rewrite_msg and "员工有哪些福利" in rewrite_msg


def test_unknown_tool_refuses() -> None:
    graph, _ = _build(
        [LLMReply(content="", tool_calls=[_tool_call(name="delete_everything")])],
        streams=[["无法处理该请求。"]],
        chunks=[],
    )
    out = _invoke(graph)
    assert out["refused"] and out["refuse_reason"] == "unknown_tool"


def test_sse_event_stream_full_chain() -> None:
    events: queue.Queue = queue.Queue()
    graph, _ = _build(
        [LLMReply(content="", tool_calls=[_tool_call()])],
        streams=[["年假", "有 5 天", "[1]"]],
        chunks=[_chunk()],
        events=events,
    )
    _invoke(graph)
    got = [e["type"] for e in _drain(events)]
    assert got == ["tool_call", "retrieved", "token", "token", "token", "citations"]


def test_sse_event_retry_has_attempt_numbers() -> None:
    events: queue.Queue = queue.Queue()
    graph, _ = _build(
        [LLMReply(content="", tool_calls=[_tool_call()])],
        streams=[["坏[9]"], ["好[1]"]],
        chunks=[_chunk()],
        events=events,
    )
    _invoke(graph)
    tokens = [e for e in _drain(events) if e["type"] == "token"]
    assert [t["attempt"] for t in tokens] == [0, 1]


def test_sse_refused_event() -> None:
    events: queue.Queue = queue.Queue()
    graph, _ = _build(
        [LLMReply(content="", tool_calls=[_tool_call()])],
        streams=[["未找到相关内容。"]],
        chunks=[],
        events=events,
    )
    _invoke(graph)
    types = [e["type"] for e in _drain(events)]
    assert "refused" in types and types[-1] == "refused"


def test_checkpoint_multiturn_rewrites_with_history() -> None:
    """同一 thread_id 两轮：第二轮 rewrite 能看到第一轮对话历史。"""
    saver = InMemorySaver()
    graph, llm = _build(
        [
            LLMReply(content="", tool_calls=[_tool_call()]),  # R1 tool_router
            LLMReply(content="年假天数是多少"),  # R2 rewrite
            LLMReply(content="", tool_calls=[_tool_call(arguments='{"query": "年假天数是多少"}')]),
        ],
        streams=[["5 天[1]。"], ["年假 5 天[1]。"]],
        chunks=[_chunk()],
        checkpointer=saver,
    )
    cfg = {"configurable": {"thread_id": "s1"}}
    out1 = _invoke(graph, query="员工有哪些假期福利", config=cfg)
    out2 = _invoke(graph, query="它规定几天", config=cfg)

    assert not out1["refused"] and not out2["refused"]
    assert out2["rewritten_query"] == "年假天数是多少"
    # 第二轮的改写上下文包含第一轮的问题
    assert "员工有哪些假期福利" in llm.calls[1]["messages"][1]["content"]
    # checkpoint 累积了 4 条消息（两轮 user+assistant）
    assert len(out2["messages"]) == 4
