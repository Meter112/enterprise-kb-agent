"""检索评估 CLI：跑 QA 集 → 5 指标 → 报告（JSON + 控制台摘要）。

用法:
  uv run python scripts/eval_retrieval.py                          # hybrid 模式
  uv run python scripts/eval_retrieval.py --mode hybrid_no_rerank  # 消融组
  uv run python scripts/eval_retrieval.py --judge 0.25             # flash 抽查 1/4
"""
from __future__ import annotations

import argparse
import json
import time

from openai import OpenAI

from kb_agent.config import settings
from kb_agent.eval.qa_set import load_qa_set
from kb_agent.eval.runner import EvalRunner
from kb_agent.ingest.sparse_bm25 import BM25Encoder
from kb_agent.models.embedder import Embedder
from kb_agent.models.reranker import Reranker
from kb_agent.retrieval.refusal import DEFAULT_RERANK_THRESHOLD
from kb_agent.store.qdrant_store import QdrantStore


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--qa", default=str(settings.data_dir / "qa_sets" / "qa_set_v1.json"))
    parser.add_argument("--mode", default="hybrid",
                        choices=["hybrid", "hybrid_no_rerank", "dense", "sparse"])
    parser.add_argument("--judge", type=float, default=0.0, help="flash 抽查比例 0-1")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--threshold", type=float, default=DEFAULT_RERANK_THRESHOLD,
                        help="拒答低置信阈值（D14 标定用）")
    args = parser.parse_args()

    t0 = time.time()
    store = QdrantStore()
    embedder = Embedder()
    reranker = Reranker()
    bm25 = BM25Encoder.load(settings.data_dir / "index_cache" / "bm25_meta.json")
    judge_client = None
    if args.judge > 0:
        assert settings.deepseek_api_key, "judge 需要 DEEPSEEK_API_KEY"
        judge_client = OpenAI(api_key=settings.deepseek_api_key, base_url=settings.deepseek_base_url)

    items = load_qa_set(args.qa)
    runner = EvalRunner(
        store, embedder, bm25, reranker,
        mode=args.mode, top_k=args.top_k, judge_frac=args.judge, judge_client=judge_client,
        threshold=args.threshold,
    )
    report = runner.run(items)
    report.latency["total_per_query"] = (
        (time.time() - t0) / len(items) if items else 0.0
    )

    print(f"\n==== 评估报告 mode={report.mode} 共 {report.total} 条 ====")
    print(f"Hit@{args.top_k}: {report.hit_at_5:.3f} | MRR: {report.mrr:.3f}")
    r = report.refusal
    print(f"拒答准确率: {r.accuracy:.3f}（应拒 {r.refuse_correct}/{r.refuse_total}，"
          f"应答 {r.answer_correct}/{r.answer_total}，漏拒率 {r.false_answer_rate:.3f}）")
    print("分阶段延迟(s): " + ", ".join(f"{k}={v:.2f}" for k, v in report.latency.items()))
    if report.judge:
        j = report.judge
        print(f"judge 抽查: {j['sampled']} 条，与 gold 一致性 {j['agreement']:.3f}")
    if report.failures:
        print(f"失败 {len(report.failures)} 条: {report.failures[:5]}")

    out = settings.data_dir / "reports" / f"eval_{report.mode}_{time.strftime('%Y%m%d_%H%M%S')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            {
                "mode": report.mode,
                "top_k": args.top_k,
                "threshold": args.threshold,
                "hit_at_k": report.hit_at_5,
                "mrr": report.mrr,
                "refusal": {
                    "accuracy": r.accuracy,
                    "refuse_correct": r.refuse_correct,
                    "refuse_total": r.refuse_total,
                    "answer_correct": r.answer_correct,
                    "answer_total": r.answer_total,
                    "false_answer_rate": r.false_answer_rate,
                },
                "latency": report.latency,
                "judge": report.judge,
                "failures": report.failures,
                "per_item": report.per_item,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"[report] {out}")


if __name__ == "__main__":
    main()
