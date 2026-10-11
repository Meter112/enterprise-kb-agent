"""检索消融一键脚本（D29）：dense / sparse / RRF / RRF+rerank 四组 + 可选 rank_bm25 对照。

用法:
  uv run python scripts/ablation_retrieval.py                  # 4 组
  uv run python scripts/ablation_retrieval.py --with-rankbm25  # 5 组（需 uv sync --extra bm25-baseline）

结果写 data/reports/ablation_retrieval.json，控制台打印对比表。
rank_bm25 对照组只报 Hit@5/MRR：库版打分尺度与 rerank 分不同，拒答阈值（0.7 为 rerank 分标定）不适用。
"""
from __future__ import annotations

import argparse
import json
import time

from kb_agent.config import settings
from kb_agent.eval.qa_set import load_qa_set
from kb_agent.eval.runner import EvalRunner
from kb_agent.ingest.sparse_bm25 import BM25Encoder, RankBM25Baseline
from kb_agent.models.embedder import Embedder
from kb_agent.models.reranker import Reranker
from kb_agent.retrieval.hybrid_retriever import HybridRetriever, RetrievedChunk
from kb_agent.store.qdrant_store import QdrantStore, expand_roles

MODES = ["dense", "sparse", "hybrid_no_rerank", "hybrid"]


def _evaluate_rankbm25(
    bm25: RankBM25Baseline, chunks: list[RetrievedChunk], items: list
) -> dict:
    """库版对照：按每条 QA 的可见层级过滤后打分，只报 Hit@5/MRR。"""
    n_hit = mrr_sum = n_gold = 0
    for item in items:
        gold = set(item.gold_chunk_ids)
        if not gold:
            continue
        n_gold += 1
        visible = {i for i, c in enumerate(chunks) if c.role in expand_roles([item.role])}
        top_ids = [
            chunks[i].chunk_id for i, _ in bm25.search(item.question, top_k=5) if i in visible
        ][:5]
        if any(cid in gold for cid in top_ids):
            n_hit += 1
        for rank, cid in enumerate(top_ids, start=1):
            if cid in gold:
                mrr_sum += 1.0 / rank
                break
    return {
        "hit_at_5": n_hit / n_gold if n_gold else 0.0,
        "mrr": mrr_sum / n_gold if n_gold else 0.0,
        "n_gold": n_gold,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--qa", default=str(settings.data_dir / "qa_sets" / "v2"))
    parser.add_argument("--with-rankbm25", action="store_true", help="第 5 组：rank_bm25 库版对照")
    parser.add_argument("--only-rankbm25", action="store_true", help="只跑 rank_bm25 组（调试）")
    parser.add_argument("--top-k", type=int, default=5)
    args = parser.parse_args()

    t0 = time.time()
    store = QdrantStore()
    embedder = Embedder()
    reranker = Reranker()
    bm25 = BM25Encoder.load(settings.data_dir / "index_cache" / "bm25_meta.json")
    items = load_qa_set(args.qa)

    results: dict[str, dict] = {}
    for mode in ([] if args.only_rankbm25 else MODES):
        report = EvalRunner(store, embedder, bm25, reranker, mode=mode, top_k=args.top_k).run(
            items
        )
        results[mode] = {
            "hit_at_5": report.hit_at_5,
            "mrr": report.mrr,
            "refusal_accuracy": report.refusal.accuracy,
            "false_answer_rate": report.refusal.false_answer_rate,
            "latency_per_query": sum(report.latency.values()),
        }
        print(
            f"[{mode}] Hit@{args.top_k}={report.hit_at_5:.3f} MRR={report.mrr:.3f} "
            f"拒答={report.refusal.accuracy:.3f} 延迟={results[mode]['latency_per_query']:.2f}s/条"
        )

    if args.with_rankbm25 or args.only_rankbm25:
        chunks = [HybridRetriever._to_chunk(h) for h in store.scroll_all(roles=["admin"])]
        baseline = RankBM25Baseline([c.text for c in chunks])
        results["rank_bm25"] = _evaluate_rankbm25(baseline, chunks, items)
        r = results["rank_bm25"]
        print(f"[rank_bm25] Hit@{args.top_k}={r['hit_at_5']:.3f} MRR={r['mrr']:.3f}（n_gold={r['n_gold']}，无拒答）")

    out = settings.data_dir / "reports" / "ablation_retrieval.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            {
                "qa_set": args.qa,
                "top_k": args.top_k,
                "wall_secs": round(time.time() - t0, 1),
                "note": "rank_bm25 组只报 Hit/MRR：分数尺度与 rerank 不同，0.7 拒答阈值不适用",
                "results": results,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print("\n==== 对比表 ====")
    print(f"{'mode':<18} {'Hit@5':>7} {'MRR':>7} {'拒答准确率':>9} {'漏拒率':>7} {'延迟/条':>8}")
    for mode, r in results.items():
        ra = r.get("refusal_accuracy")
        ra_s = "—" if ra is None else f"{ra:.3f}"
        far = r.get("false_answer_rate")
        far_s = "—" if far is None else f"{far:.3f}"
        print(
            f"{mode:<18} {r['hit_at_5']:>7.3f} {r['mrr']:>7.3f} "
            f"{ra_s:>9} {far_s:>7} {r.get('latency_per_query', float('nan')):>8.2f}"
        )
    print(f"\n[report] {out}")


if __name__ == "__main__":
    main()
