"""Agent 工具：query_kb / summarize_doc（手写执行，不用 langgraph-prebuilt ToolNode）。

工具函数与依赖解耦（retriever/store/llm 由 graph 构建器注入），单测可用 fake。
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from kb_agent.agent.llm import LLMClient
from kb_agent.agent.prompts import MAP_SUMMARY_SYSTEM, REDUCE_SUMMARY_SYSTEM
from kb_agent.retrieval.hybrid_retriever import HybridRetriever
from kb_agent.retrieval.refusal import refuse_decision
from kb_agent.store.qdrant_store import QdrantStore

# OpenAI function-calling schema（DeepSeek 兼容）
TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "query_kb",
            "description": "检索企业知识库，回答制度/流程/规范/接口文档等问题",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "独立完整的问题"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "summarize_doc",
            "description": "总结/概述某一篇文档的整体内容",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "文档标题"},
                    "question": {"type": "string", "description": "用户关注点，无则为空字符串"},
                },
                "required": ["title"],
            },
        },
    },
]

MAP_BATCH_SIZE = 8  # map 阶段每批 chunk 数（控制单次 LLM 输入长度）


def query_kb(
    query: str,
    roles: list[str],
    retriever: HybridRetriever,
    top_k: int = 8,
) -> dict[str, Any]:
    """全链路检索：RRF 召回 + rerank + 拒答阈值判定。

    top_k=8：D24 评估发现 5 个候选对主题相近的约束词区分不足（如
    「内部订单API的鉴权方式」的正确答案 rerank 后排第 6 被截掉），
    放宽到 8 个候选兜底；拒答阈值只依赖最高分，不受影响。
    """
    chunks = retriever.retrieve(query, roles=roles, top_k=top_k)
    refused, reason = refuse_decision(chunks)
    return {"chunks": chunks, "refused": refused, "refuse_reason": reason}


def _norm(s: str) -> str:
    """标题归一化：去书名号/空白/常见标点/大小写，容错 LLM 提取标题的格式差异。"""
    out = s
    for ch in "《》「」 \t-—–·_":
        out = out.replace(ch, "")
    return out.strip().lower()


def _resolve_doc_ids(store: QdrantStore, title: str, roles: list[str]) -> list[str]:
    """按标题反查 doc_id 集合（滚动全量 payload，语料 370 chunks 一次滚完可接受）。

    - roles 是已 expand 的可见角色集合（见 qdrant_store.expand_roles），
      文档 role 不在其中视为不可见
    - 匹配分两级：归一化全等 → 归一化包含；同名 MD/PDF 多格式全部返回
    """
    matches: dict[str, str] = {}  # doc_id → title（精确）
    fuzzy: dict[str, str] = {}  # doc_id → title（包含）
    offset = None
    while True:
        page, offset = store.client.scroll(
            collection_name=store.collection,
            with_payload=["doc_id", "title", "role"],
            with_vectors=False,
            limit=256,
            offset=offset,
        )
        for p in page:
            payload = p.payload or {}
            p_title = str(payload.get("title") or "")
            if payload.get("role") not in roles:
                continue
            if _norm(p_title) == _norm(title):
                matches[str(payload.get("doc_id"))] = p_title
            elif _norm(title) and (_norm(title) in _norm(p_title) or _norm(p_title) in _norm(title)):
                fuzzy[str(payload.get("doc_id"))] = p_title
        if offset is None:
            break
    return list((matches or fuzzy).keys())


def summarize_doc(
    title: str,
    question: str,
    roles: list[str],
    store: QdrantStore,
    llm: LLMClient,
    map_batch: int = MAP_BATCH_SIZE,
) -> dict[str, Any]:
    """按标题取全部 chunks，map-reduce 摘要（每批 map_batch 个 chunk 一段要点）。"""
    doc_ids = _resolve_doc_ids(store, title, roles)
    if not doc_ids:
        return {"title": title, "refused": True, "refuse_reason": "doc_not_found", "summary": ""}
    texts: list[str] = []
    for doc_id in doc_ids:
        hits = store.get_document_chunks(doc_id, roles=roles)
        texts.extend((h.payload or {}).get("text", "") for h in hits)
    if not texts:
        return {"title": title, "refused": True, "refuse_reason": "doc_not_found", "summary": ""}

    focus = question or "整体内容"
    map_notes: list[str] = []
    for i in range(0, len(texts), map_batch):
        batch = "\n\n---\n\n".join(texts[i : i + map_batch])
        reply = llm.chat(
            [
                {
                    "role": "system",
                    "content": MAP_SUMMARY_SYSTEM.format(title=title, question=focus),
                },
                {"role": "user", "content": batch},
            ],
            max_tokens=4096,
        )
        if reply.content.strip():
            map_notes.append(reply.content.strip())

    if not map_notes:
        return {"title": title, "refused": True, "refuse_reason": "doc_not_found", "summary": ""}

    if len(map_notes) == 1:
        summary = map_notes[0]
    else:
        reply = llm.chat(
            [
                {"role": "system", "content": REDUCE_SUMMARY_SYSTEM},
                {"role": "user", "content": f"关注点：{focus}\n\n" + "\n\n---\n\n".join(map_notes)},
            ],
            max_tokens=4096,
        )
        summary = reply.content.strip()
    return {
        "title": title,
        "doc_ids": doc_ids,
        "refused": False,
        "refuse_reason": "",
        "summary": summary,
        "chunk_count": len(texts),
    }


ToolExecutor = Callable[[str, dict[str, Any]], dict[str, Any]]
