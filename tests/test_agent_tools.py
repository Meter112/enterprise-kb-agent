"""Agent 工具单测：标题反查 doc_id（精确/模糊/权限）、query_kb 拒答透传。"""
from __future__ import annotations

from kb_agent.agent.tools import _resolve_doc_ids, query_kb
from kb_agent.retrieval.hybrid_retriever import RetrievedChunk


class _Point:
    def __init__(self, payload: dict) -> None:
        self.payload = payload


class _FakeClient:
    """单页 scroll，返回预设点（payload 直接展开，对齐真实 ScoredPoint）。"""

    def __init__(self, points: list[dict]) -> None:
        self.points = points

    def scroll(self, **kwargs):
        return ([_Point(p) for p in self.points], None)


class _FakeStore:
    def __init__(self, points: list[dict]) -> None:
        self.client = _FakeClient(points)
        self.collection = "test"

    def get_document_chunks(self, doc_id, roles):
        return [h for h in self.client.points if h.payload.get("doc_id") == doc_id]


def _store() -> _FakeStore:
    return _FakeStore(
        [
            {"doc_id": "手册#md", "title": "员工手册-公开版", "role": "public"},
            {"doc_id": "手册#pdf", "title": "员工手册-公开版", "role": "public"},
            {"doc_id": "薪酬#md", "title": "薪酬保密制度", "role": "admin"},
        ]
    )


def test_resolve_exact_match_returns_all_formats() -> None:
    ids = _resolve_doc_ids(_store(), "员工手册-公开版", ["public", "employee"])
    assert sorted(ids) == ["手册#md", "手册#pdf"]


def test_resolve_normalizes_book_marks_and_space() -> None:
    ids = _resolve_doc_ids(_store(), "《员工手册 公开版》", ["public"])
    assert ids  # 归一化后全等匹配


def test_resolve_fuzzy_contains() -> None:
    ids = _resolve_doc_ids(_store(), "员工手册", ["public"])
    assert sorted(ids) == ["手册#md", "手册#pdf"]


def test_resolve_filters_invisible_roles() -> None:
    # employee 看不到 admin 文档
    assert _resolve_doc_ids(_store(), "薪酬保密制度", ["public", "employee"]) == []
    assert _resolve_doc_ids(_store(), "薪酬保密制度", ["public", "admin"]) == ["薪酬#md"]


def test_resolve_unknown_title_empty() -> None:
    assert _resolve_doc_ids(_store(), "不存在的文档", ["public"]) == []


class _FakeRetriever:
    def __init__(self, chunks: list[RetrievedChunk]) -> None:
        self.chunks = chunks

    def retrieve(self, query, roles, top_k=5, with_rerank=True, timings=None):
        return list(self.chunks)


def _chunk(rerank: float) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id="c1", doc_id="d#md", title="t", text="x", title_path=[],
        page=None, section="1", role="public", is_code=False,
        rrf_score=0.5, rerank_score=rerank,
    )


def test_query_kb_tool_transparent_passthrough() -> None:
    r = query_kb("问题", ["public"], _FakeRetriever([_chunk(0.95)]))
    assert not r["refused"] and len(r["chunks"]) == 1


def test_query_kb_tool_empty_refuses() -> None:
    r = query_kb("问题", ["public"], _FakeRetriever([]))
    assert r["refused"] and r["refuse_reason"] == "no_candidates"


def test_query_kb_tool_low_confidence_refuses() -> None:
    r = query_kb("问题", ["public"], _FakeRetriever([_chunk(0.4)]))
    assert r["refused"] and r["refuse_reason"] == "low_confidence"
