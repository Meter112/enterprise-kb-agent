"""评估执行器：检索 → 指标 → 报告。

mode 支持（D14 消融直接复用）：
  - hybrid（默认）：dense+sparse RRF + rerank
  - hybrid_no_rerank：RRF 后不重排
  - dense / sparse：单路检索（消融组 1/2）
"""
from __future__ import annotations

from dataclasses import dataclass, field

from openai import OpenAI

from kb_agent.eval.judge import judge_relevance
from kb_agent.eval.metrics import RefusalEval, hit_at_k, mean, reciprocal_rank
from kb_agent.eval.qa_set import QAItem
from kb_agent.ingest.sparse_bm25 import BM25Encoder
from kb_agent.models.embedder import Embedder
from kb_agent.models.reranker import Reranker
from kb_agent.retrieval.hybrid_retriever import HybridRetriever, RetrievedChunk
from kb_agent.retrieval.refusal import DEFAULT_RERANK_THRESHOLD, refuse_decision
from kb_agent.store.qdrant_store import QdrantStore

MODES = {"hybrid", "hybrid_no_rerank", "dense", "sparse"}


@dataclass
class EvalReport:
    mode: str
    total: int
    hit_at_5: float = 0.0
    mrr: float = 0.0
    refusal: RefusalEval = field(default_factory=RefusalEval)
    latency: dict[str, float] = field(default_factory=dict)  # stage → mean seconds
    failures: list[str] = field(default_factory=list)
    per_item: list[dict] = field(default_factory=list)
    judge: dict | None = None


class EvalRunner:
    def __init__(
        self,
        store: QdrantStore,
        embedder: Embedder,
        bm25: BM25Encoder,
        reranker: Reranker,
        mode: str = "hybrid",
        top_k: int = 5,
        judge_frac: float = 0.0,
        judge_client: OpenAI | None = None,
        threshold: float = DEFAULT_RERANK_THRESHOLD,
    ) -> None:
        if mode not in MODES:
            raise ValueError(f"未知模式: {mode}（可选 {sorted(MODES)}）")
        self.store = store
        self.embedder = embedder
        self.bm25 = bm25
        self.reranker = reranker
        self.mode = mode
        self.top_k = top_k
        self.judge_frac = judge_frac
        self.judge_client = judge_client
        self.threshold = threshold
        self.retriever = HybridRetriever(store, embedder, bm25, reranker)

    def _retrieve(
        self, query: str, roles: list[str]
    ) -> tuple[list[RetrievedChunk], dict[str, float], list[str]]:
        timings: dict[str, float] = {}
        pre_ids: list[str] = []
        chunks = self.retriever.retrieve(
            query,
            roles=roles,
            top_k=self.top_k,
            with_rerank=self.mode == "hybrid",
            mode=self.mode,
            timings=timings,
            pre_rerank_ids=pre_ids,
        )
        return chunks, timings, pre_ids

    def run(self, items: list[QAItem]) -> EvalReport:
        report = EvalReport(mode=self.mode, total=len(items))
        lat_stage: dict[str, list[float]] = {}
        judge_hits = judge_total = 0
        judge_cases: list[dict] = []

        for idx, item in enumerate(items):
            try:
                chunks, timings, pre_ids = self._retrieve(item.question, [item.role])
                for stage, secs in timings.items():
                    lat_stage.setdefault(stage, []).append(secs)
                refused, reason = refuse_decision(chunks, threshold=self.threshold)
                report.refusal.record(item.expect_refusal, refused)
                gold = set(item.gold_chunk_ids)
                hit = hit_at_k(chunks, gold, k=self.top_k)
                rr = reciprocal_rank(chunks, gold)
                # top-k 分数快照：D14 阈值扫描离线重算用（rerank 分缺省退回 rrf）
                report.per_item.append(
                    {
                        "id": item.qa_id,
                        "category": item.scenario_type,
                        "difficulty": item.difficulty,
                        "question": item.question,
                        "roles": [item.role],
                        "expect_refusal": item.expect_refusal,
                        "refused": refused,
                        "refuse_reason": reason,
                        "hit": hit,
                        "rr": rr,
                        "gold_chunk_ids": item.gold_chunk_ids,
                        "pre_rerank_ids": pre_ids,
                        "top1": chunks[0].title if chunks else None,
                        "top5_scores": [
                            c.rerank_score if c.rerank_score is not None else c.rrf_score
                            for c in chunks
                        ],
                    }
                )
                # 抽样 judge：top-1 相关性 vs gold 命中一致性
                if (
                    self.judge_client is not None
                    and self.judge_frac > 0
                    and chunks
                    and idx % max(1, int(1 / self.judge_frac)) == 0
                ):
                    relevant, _ = judge_relevance(
                        self.judge_client, item.question, chunks[0].text
                    )
                    judge_total += 1
                    judge_hits += int(relevant == hit)
                    judge_cases.append(
                        {"id": item.qa_id, "relevant": relevant, "hit": hit, "top1": chunks[0].title}
                    )
            except Exception as e:  # noqa: BLE001
                report.failures.append(f"{item.qa_id}: {e}")

        n_hit = sum(1 for p in report.per_item if p["hit"])
        n_gold = sum(1 for i in items if i.gold_chunk_ids)
        report.hit_at_5 = n_hit / n_gold if n_gold else 0.0
        report.mrr = sum(p["rr"] for p in report.per_item) / n_gold if n_gold else 0.0
        report.latency = {s: mean(v) for s, v in lat_stage.items()}
        if judge_total:
            report.judge = {
                "sampled": judge_total,
                "agreement": judge_hits / judge_total,
                "cases": judge_cases,
            }
        return report
