"""评估指标单测。"""
import math

from kb_agent.eval.metrics import (
    RefusalEval,
    citation_correctness,
    cohens_kappa,
    hit_at_k,
    reciprocal_rank,
    wilson_interval,
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


def test_wilson_interval_bounds() -> None:
    """n=0 退化为全区间；p̂=0/1 时区间不越界且不对称。"""
    assert wilson_interval(0, 0) == (0.0, 1.0)
    lo, hi = wilson_interval(0, 10)
    assert 0.0 <= lo < hi < 1.0
    lo, hi = wilson_interval(10, 10)
    assert 0.0 < lo < hi <= 1.0


def test_wilson_interval_centered_on_observed() -> None:
    lo, hi = wilson_interval(8, 16)
    assert lo < 0.5 < hi  # 0.5 落在区间内
    lo2, hi2 = wilson_interval(15, 16)
    assert lo2 > 0.65 and hi2 < 1.0  # 15/16 的下界仍 <1 但明显偏高


def test_cohens_kappa_perfect_agreement() -> None:
    assert cohens_kappa([True, True, False], [True, True, False]) == 1.0
    # 完全相反（3 条）：po=0，pe=4/9 → kappa=-0.8（系统性分歧）
    assert math.isclose(cohens_kappa([True, True, False], [False, False, True]), -0.8)


def test_cohens_kappa_chance_level() -> None:
    # 两标注者完全独立（各 2/4 判对）→ 一致率 0.5 = pe → kappa 0
    k = cohens_kappa([True, True, False, False], [True, False, True, False])
    assert math.isclose(k, 0.0, abs_tol=1e-9)


def test_cohens_kappa_degenerate() -> None:
    assert cohens_kappa([True], [True]) == 0.0  # 样本过少
    assert cohens_kappa([], []) == 0.0
