"""拒答判定（检索层，D8-10）。

门槛（计划）：
  1. role 过滤后无候选 → 拒答（权限受限与语料外命中在此层不可区分，
     话术分场景由 Agent 层 D15+ 结合请求角色决定）
  2. 重排后最佳分数低于阈值 → 证据不足拒答（阈值初始 0.3，D14 用评估集标定）
  3. 生成后引用校验失败 → 拒答/重试（Agent 层，配合 citations.validate_citations）
"""
from __future__ import annotations

from kb_agent.retrieval.hybrid_retriever import RetrievedChunk

# D14 标定值：评估集离线阈值扫描（scripts/scan_threshold.py）选定 0.70
# 0.70-0.85 区间拒答准确率 0.938 / 漏拒率 0.182 / 误拒率 0，帕累托最优下界
DEFAULT_RERANK_THRESHOLD = 0.7

REASON_NO_CANDIDATES = "no_candidates"
REASON_LOW_CONFIDENCE = "low_confidence"


def refuse_decision(
    chunks: list[RetrievedChunk],
    threshold: float = DEFAULT_RERANK_THRESHOLD,
) -> tuple[bool, str]:
    """返回 (是否拒答, 原因)。未做重排（无 rerank 分）时不判低置信。"""
    if not chunks:
        return True, REASON_NO_CANDIDATES
    scored = [c.rerank_score for c in chunks if c.rerank_score is not None]
    if scored and max(scored) < threshold:
        return True, REASON_LOW_CONFIDENCE
    return False, ""
