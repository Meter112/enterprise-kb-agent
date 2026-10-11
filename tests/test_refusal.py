"""拒答判定单测。"""
from kb_agent.retrieval.hybrid_retriever import RetrievedChunk
from kb_agent.retrieval.refusal import (
    REASON_LOW_CONFIDENCE,
    REASON_NO_CANDIDATES,
    refuse_decision,
)


def _chunk(rerank: float | None) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id="c1",
        doc_id="d1",
        title="t",
        text="x",
        title_path=[],
        page=None,
        section=None,
        role="public",
        is_code=False,
        rrf_score=0.5,
        rerank_score=rerank,
    )


def test_empty_candidates_refuse() -> None:
    refused, reason = refuse_decision([])
    assert refused and reason == REASON_NO_CANDIDATES


def test_best_score_below_threshold_refuse() -> None:
    refused, reason = refuse_decision([_chunk(0.29), _chunk(0.1)], threshold=0.3)
    assert refused and reason == REASON_LOW_CONFIDENCE


def test_best_score_above_threshold_pass() -> None:
    refused, reason = refuse_decision([_chunk(0.29), _chunk(0.31)], threshold=0.3)
    assert not refused and reason == ""


def test_no_rerank_scores_pass() -> None:
    # 未走重排（消融组 1/2）时没有判定依据，不拒答
    refused, _ = refuse_decision([_chunk(None)])
    assert not refused


def test_default_threshold_is_calibrated_0_7() -> None:
    """回归：D14 标定后默认阈值为 0.7，0.69 应拒、0.71 应过。"""
    assert refuse_decision([_chunk(0.69)])[0]
    assert not refuse_decision([_chunk(0.71)])[0]
