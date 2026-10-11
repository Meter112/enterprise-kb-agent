"""冒烟 5：混合检索全链路（RRF 召回 → rerank 重排 → 拒答判定 → 引用格式）。

需要先完成索引: uv run python scripts/index_corpus.py
用法: uv run python scripts/smoke_retrieval.py
"""
from __future__ import annotations

import time
from pathlib import Path

from kb_agent.config import settings
from kb_agent.ingest.sparse_bm25 import BM25Encoder
from kb_agent.models.embedder import Embedder
from kb_agent.models.reranker import Reranker
from kb_agent.retrieval.citations import format_citation
from kb_agent.retrieval.hybrid_retriever import HybridRetriever
from kb_agent.retrieval.refusal import refuse_decision
from kb_agent.store.qdrant_store import QdrantStore

CASES: list[tuple[str, list[str]]] = [
    ("年假有几天", ["employee"]),  # 单跳事实，应命中《年假与调休制度》
    ("服务器日常巡检有哪些项目", ["admin"]),  # admin 专属文档
    ("公司健身房在哪里", ["employee"]),  # 语料外 → 低置信拒答
    ("年假有几天", ["contractor"]),  # 权限演示：employee 文档对 contractor 不可见
]


def main() -> None:
    t0 = time.time()
    store = QdrantStore()
    embedder = Embedder()
    reranker = Reranker()
    bm25 = BM25Encoder.load(settings.data_dir / "index_cache" / "bm25_meta.json")
    retriever = HybridRetriever(store, embedder, bm25, reranker)
    print(f"[load] 模型加载总耗时 {time.time() - t0:.1f}s")

    latencies: list[float] = []
    for query, roles in CASES:
        t = time.time()
        chunks = retriever.retrieve(query, roles=roles, top_k=3)
        latencies.append(time.time() - t)
        refused, reason = refuse_decision(chunks)
        print(f"\n[query] {query} (roles={roles}) 端到端 {latencies[-1]:.1f}s")
        if refused:
            print(f"  [refuse] {reason}")
            continue
        for i, c in enumerate(chunks, 1):
            score = f"{c.rerank_score:.3f}" if c.rerank_score is not None else "n/a"
            text = c.text[:50].replace("\n", " ")
            print(f"  {i}. [{score}] {format_citation(c)} | {text}")

    bench = Path(__file__).resolve().parents[1] / "docs" / "benchmarks.md"
    bench.write_text(
        bench.read_text(encoding="utf-8")
        + "\n## 混合检索全链路冒烟实测（RRF top-50 召回 + bge-reranker 重排）\n"
        + f"- 时间: {time.strftime('%Y-%m-%d %H:%M')}\n"
        + "- 硬件: CPU-only, torch.set_num_threads(8)\n"
        + "- rerank 模型: bge-reranker-base (use_fp16=False)\n"
        + "- 单查询端到端延迟（含 embed + RRF + rerank top-50）: "
        + f"平均 {sum(latencies) / len(latencies):.1f}s（n={len(latencies)}）\n",
        encoding="utf-8",
    )
    print("\n[bench] 实测数据已写入 docs/benchmarks.md")


if __name__ == "__main__":
    main()
