"""评估指标单测。"""
from kb_agent.eval.metrics import (
    RefusalEval,
    citation_correctness,
    hit_at_k,
    reciprocal_rank,
)
from kb_agent.retrieval.hybrid_retriever import RetrievedChunk


def _chunk(cid: str, score: float = 0.9) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=cid, doc_id="d", title="t", text="x", title_path=[],
        page=None, section=None, role="public", is_code=False,
        rrf_score=score, rerank_score=score,
    )


def test_hit_at_k() -> None:
    chunks = [_chunk("a"), _chunk("b"), _chunk("c"), _chunk("d"), _chunk("e")]
    assert hit_at_k(chunks, {"e"}, k=5)
    assert not hit_at_k(chunks, {"f"}, k=5)


def test_reciprocal_rank() -> None:
    chunks = [_chunk("a"), _chunk("b"), _chunk("c")]
    assert reciprocal_rank(chunks, {"b"}) == 0.5
    assert reciprocal_rank(chunks, {"a"}) == 1.0
    assert reciprocal_rank(chunks, {"z"}) == 0.0


def test_refusal_eval_two_sided() -> None:
    r = RefusalEval()
    r.record(expect_refusal=True, refused=True)   # 正确拒答
    r.record(expect_refusal=True, refused=False)  # 漏拒
    r.record(expect_refusal=False, refused=False)  # 正确应答
    r.record(expect_refusal=False, refused=True)  # 误拒
    assert r.accuracy == 0.5
    assert r.false_answer_rate == 0.5


def test_citation_correctness_valid() -> None:
    chunks = [_chunk("a"), _chunk("b")]
    assert citation_correctness("答案[1]与[2]。", chunks) == 1.0
    assert citation_correctness("无引用答案。", chunks) == 1.0


def test_citation_correctness_out_of_range() -> None:
    chunks = [_chunk("a")]
    assert citation_correctness("答案[1]和[5]。", chunks) == 0.5


def test_citation_correctness_ref_without_candidates() -> None:
    assert citation_correctness("答案[1]。", []) == 0.0
