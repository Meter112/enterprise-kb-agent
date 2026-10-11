"""混合检索编排（D8-10）：Qdrant 双路 RRF 召回 → cross-encoder 重排 → 结构化候选。

链路：
    query → dense+sparse prefetch 各 top-N → FusionQuery RRF → payload role 过滤
          → bge-reranker 重排 → 按 rerank 分降序 → top-K 候选（含引用定位信息）

权限过滤发生在 Qdrant 检索期（store 层），无权限文档根本不进候选集；
本层只负责召回后的重排与结构化，不做权限判断。
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from kb_agent.ingest.sparse_bm25 import BM25Encoder
from kb_agent.models.embedder import Embedder
from kb_agent.models.reranker import Reranker
from kb_agent.store.qdrant_store import QdrantStore


@dataclass
class RetrievedChunk:
    """一次检索命中的 chunk：来源元数据 + RRF/重排两级分数。"""

    chunk_id: str
    doc_id: str
    title: str
    text: str
    title_path: list[str]
    page: int | None
    section: str | None
    role: str
    is_code: bool
    rrf_score: float
    rerank_score: float | None = None


class HybridRetriever:
    def __init__(
        self,
        store: QdrantStore,
        embedder: Embedder,
        bm25: BM25Encoder,
        reranker: Reranker,
        top_n: int = 20,
    ) -> None:
        # top_n 是 rerank 的候选数（延迟主瓶颈：CPU 交叉编码 ~0.3s/候选）。
        # D26 优化 50→20：370 chunks 语料下前 20 召回已覆盖最终 top-k（评估验证无指标损失）
        self.store = store
        self.embedder = embedder
        self.bm25 = bm25
        self.reranker = reranker
        self.top_n = top_n

    def retrieve(
        self,
        query: str,
        roles: list[str],
        top_k: int = 5,
        with_rerank: bool = True,
        timings: dict[str, float] | None = None,
    ) -> list[RetrievedChunk]:
        """召回 top_n → 重排 → 返回 top_k 候选（按 rerank 分降序）。

        timings 传入时按 embed / recall / rerank 三段埋点（评估的"分阶段延迟"指标用）。
        """
        t0 = time.perf_counter()
        qvec = self.embedder.encode_query(query)
        t1 = time.perf_counter()
        hits = self.store.query_hybrid(
            qvec.tolist(),
            self.bm25.encode(query, for_query=True),
            roles=roles,
            limit=self.top_n,
        )
        t2 = time.perf_counter()
        chunks = [self._to_chunk(h) for h in hits]
        if with_rerank and chunks:
            scores = self.reranker.rerank(query, [c.text for c in chunks])
            for c, s in zip(chunks, scores, strict=True):
                c.rerank_score = s
            chunks.sort(key=lambda c: (c.rerank_score or 0.0), reverse=True)
        t3 = time.perf_counter()
        if timings is not None:
            timings.update(embed=t1 - t0, recall=t2 - t1, rerank=t3 - t2)
        return chunks[:top_k]

    @staticmethod
    def _to_chunk(hit) -> RetrievedChunk:  # noqa: ANN001
        p = hit.payload or {}
        return RetrievedChunk(
            chunk_id=str(hit.id),
            doc_id=p.get("doc_id", ""),
            title=p.get("title", ""),
            text=p.get("text", ""),
            title_path=p.get("title_path") or [],
            page=p.get("page"),
            section=p.get("section"),
            role=p.get("role", ""),
            is_code=bool(p.get("is_code")),
            rrf_score=hit.score,
        )
