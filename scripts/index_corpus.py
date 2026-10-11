"""索引 CLI：增量索引 / 全量重建 / 人工抽查 top-k。

用法:
  uv run python scripts/index_corpus.py                    # 增量索引（hash 跳过未变更）
  uv run python scripts/index_corpus.py --rebuild          # drop collection 全量重建
  uv run python scripts/index_corpus.py --spot "年假有几天"  # 索引后抽查检索结果
"""
from __future__ import annotations

import argparse

from kb_agent.config import settings
from kb_agent.ingest.indexer import Indexer
from kb_agent.ingest.sparse_bm25 import BM25Encoder
from kb_agent.models.embedder import Embedder
from kb_agent.store.qdrant_store import QdrantStore


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rebuild", action="store_true", help="drop collection 全量重建")
    parser.add_argument("--spot", metavar="QUERY", help="索引完成后抽查该查询的 top-5")
    args = parser.parse_args()

    store = QdrantStore()
    embedder = Embedder()
    indexer = Indexer(store, embedder)

    print(f"[index] 语料目录: {settings.corpus_dir}")
    report = indexer.rebuild() if args.rebuild else indexer.index_directory()
    print(
        f"[done] 索引 {report.indexed} 篇, 跳过 {report.skipped} 篇, "
        f"删除 {report.deleted} 篇, 总 chunks {report.chunks}, "
        f"kb_version={report.kb_version}, 耗时 {report.elapsed_seconds:.1f}s"
    )
    for err in report.errors:
        print(f"[error] {err}")

    if args.spot:
        bm25 = BM25Encoder.load(indexer.bm25_file)
        query_vec = embedder.encode_query(args.spot)
        hits = store.query_hybrid(
            query_vec.tolist(),
            bm25.encode(args.spot, for_query=True),
            roles=["admin"],  # 抽查用最高权限，验证召回不受过滤影响
            limit=5,
        )
        print(f"\n[spot] 查询: {args.spot}")
        for i, h in enumerate(hits, 1):
            p = h.payload or {}
            text = (p.get("text") or "")[:60].replace("\n", " ")
            print(f"  {i}. [{h.score:.3f}] {p.get('title')} ({p.get('role')}) {text}")


if __name__ == "__main__":
    main()
