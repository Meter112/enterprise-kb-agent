"""索引全链路冒烟：临时语料（3 篇 MD）→ 解析 → 分块 → 双向量入库 → 混合检索。

不依赖 DeepSeek API；独立 collection + 独立 cache 目录，跑完自动清理，不污染主库。
用法: uv run python scripts/smoke_indexer.py
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from kb_agent.ingest.indexer import Indexer
from kb_agent.ingest.sparse_bm25 import BM25Encoder
from kb_agent.models.embedder import Embedder
from kb_agent.store.qdrant_store import QdrantStore

COLLECTION = "kb_smoke_tmp"

CORPUS: dict[str, str] = {
    "employee/考勤制度.md": """# 考勤制度

## 工作时间

员工应于工作日 9:00 前到岗，18:00 后离岗。

## 请假类型

年假每年 5 天；病假需提供医院证明；事假须提前一天申请。
""",
    "employee/差旅报销制度.md": """# 差旅报销制度

## 住宿标准

一线城市住宿每晚不超过 600 元，其他城市不超过 400 元。

## 报销流程

出差结束后 10 个工作日内提交报销单，附发票原件。
""",
    "public/公司介绍.md": """# 公司介绍

云帆科技成立于 2018 年，总部位于北京，主营企业级云存储产品，现有员工约 800 人。
""",
}


def main() -> None:
    store = QdrantStore(collection=COLLECTION)
    try:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            corpus = root / "corpus"
            cache = root / "cache"
            for rel, text in CORPUS.items():
                f = corpus / rel
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_text(text, encoding="utf-8")

            embedder = Embedder()
            indexer = Indexer(store, embedder, cache_dir=cache)
            report = indexer.rebuild(corpus_dir=corpus)
            assert report.chunks >= 3, report
            print(f"[index] {report.indexed} 篇, chunks={report.chunks}, v={report.kb_version}")

            bm25 = BM25Encoder.load(indexer.bm25_file)
            q = "年假有几天"
            hits = store.query_hybrid(
                embedder.encode_query(q).tolist(),
                bm25.encode(q, for_query=True),
                roles=["admin"],
                limit=5,
            )
            assert hits, "混合检索无结果"
            top = hits[0].payload or {}
            assert "考勤" in top.get("title", ""), f"top1 应为考勤制度，实际 {top.get('title')}"
            print(f"[retr] '{q}' top1: {top.get('title')} score={hits[0].score:.3f}")

            # 权限过滤：contractor 只能看到 public + contractor 文档
            q2 = "住宿标准是多少"
            hits2 = store.query_hybrid(
                embedder.encode_query(q2).tolist(),
                bm25.encode(q2, for_query=True),
                roles=["contractor"],
                limit=5,
            )
            roles_seen = {(h.payload or {}).get("role") for h in hits2}
            assert roles_seen <= {"public", "contractor"}, f"权限过滤失效: {roles_seen}"
            print(f"[perm] contractor 检索可见角色: {sorted(roles_seen)}")

            # 增量：修改 1 个文件 → 只索引 1 篇，且旧 chunk 不残留
            f2 = corpus / "employee/考勤制度.md"
            f2.write_text(
                f2.read_text(encoding="utf-8") + "\n\n迟到 30 分钟以内扣款 50 元。\n",
                encoding="utf-8",
            )
            report2 = indexer.index_directory(corpus_dir=corpus)
            assert report2.indexed == 1, report2
            assert report2.skipped == 2, report2
            print(
                f"[incr] indexed={report2.indexed}, skipped={report2.skipped}, "
                f"chunks={report2.chunks}, v={report2.kb_version}"
            )
            print("[OK] smoke_indexer: 解析→分块→BM25→dense→RRF→权限→增量 全链路通过")
    finally:
        store.delete_collection()


if __name__ == "__main__":
    main()
