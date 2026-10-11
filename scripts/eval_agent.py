"""D24-25 端到端评估 CLI：Agent 全链路跑 QA 集两遍（cold 写缓存 / warm 测 L1 命中）。

用法:
  uv run python scripts/eval_agent.py              # 全量 32 条 + judge
  uv run python scripts/eval_agent.py --no-judge   # 只统计拒答/延迟/缓存（省钱）
  uv run python scripts/eval_agent.py --ids qa-029 # 只跑指定条目

依赖：Qdrant + Redis 容器在跑（与 host 开发环境一致）。
输出：data/reports/eval_agent_*.json（含 cold/warm 两轮 + RAG 基线对比字段）。
"""
from __future__ import annotations

import argparse
import json
import time

from openai import OpenAI

from kb_agent.agent.cache import ExactCache
from kb_agent.agent.graph import KBAgent
from kb_agent.agent.llm import LLMClient
from kb_agent.config import settings
from kb_agent.eval.agent_runner import AgentEvalRunner
from kb_agent.eval.qa_set import load_qa_set
from kb_agent.ingest.sparse_bm25 import BM25Encoder
from kb_agent.models.embedder import Embedder
from kb_agent.models.reranker import Reranker
from kb_agent.store.qdrant_store import QdrantStore


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--qa", default=str(settings.data_dir / "qa_sets" / "qa_set_v1.json"))
    ap.add_argument("--no-judge", action="store_true", help="跳过 flash judge（只测拒答/延迟/缓存）")
    ap.add_argument("--ids", nargs="*", default=[], help="只跑指定 item id（调试/复测用）")
    args = ap.parse_args()

    items = load_qa_set(args.qa)
    if args.ids:
        items = [i for i in items if i.id in args.ids]
    print(f"QA 集 {len(items)} 条")

    judge_client = None
    if not args.no_judge:
        assert settings.deepseek_api_key, "judge 需要 DEEPSEEK_API_KEY"
        judge_client = OpenAI(api_key=settings.deepseek_api_key, base_url=settings.deepseek_base_url)

    # 与 API 服务同构的 agent（单轮评估：checkpointer=None；cache 供 warm 轮测命中）
    llm = LLMClient(
        settings.deepseek_api_key, settings.deepseek_base_url, settings.deepseek_chat_model
    )
    agent = KBAgent(
        QdrantStore(),
        Embedder(),
        BM25Encoder.load(settings.data_dir / "index_cache" / "bm25_meta.json"),
        Reranker(),
        llm,
        checkpointer=None,
        cache=ExactCache(settings.redis_url),
    )
    runner = AgentEvalRunner(agent, judge_client=judge_client)

    t0 = time.time()
    cold = runner.run(items)
    cold_secs = time.time() - t0
    print(f"cold 轮完成：{cold_secs / 60:.1f} min，平均 {cold.latency_mean:.1f}s/条")

    t1 = time.time()
    warm = runner.run(items, warm_cache=True)
    warm_secs = time.time() - t1
    print(f"warm 轮完成：{warm_secs:.1f}s（平均 {warm.latency_mean:.3f}s/条）")

    r = cold.refusal
    print("\n==== Agent 端到端评估 ====")
    print(f"答案正确率(judge): {cold.answer_accuracy:.3f}（{cold.answer_judged} 条判分）")
    print(f"拒答准确率: {r.accuracy:.3f}（应拒 {r.refuse_correct}/{r.refuse_total}，"
          f"应答 {r.answer_correct}/{r.answer_total}，漏拒率 {r.false_answer_rate:.3f}）")
    c = cold.citations
    print(f"引用覆盖: {c['covered']}/{c['total']}（应答题答案含 [n] 引用）")
    print(f"端到端延迟: cold 平均 {cold.latency_mean:.1f}s/条")
    print(f"L1 缓存(warm 轮): 命中 {warm.cache.get('hits', 0)}/{warm.cache.get('total', 0)}"
          f"（{warm.cache.get('rate', 0):.0%}），平均 {warm.cache.get('latency_mean', 0):.3f}s/条")
    if cold.leaks:
        print(f"漏拒 {len(cold.leaks)} 条: {cold.leaks}")
    if cold.failures:
        print(f"失败 {len(cold.failures)} 条: {cold.failures[:5]}")

    out = settings.data_dir / "reports" / f"eval_agent_{time.strftime('%Y%m%d_%H%M%S')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            {
                "qa_set": args.qa.split("/")[-1],
                "judge_enabled": judge_client is not None,
                "ids_filter": args.ids,
                "cold": {
                    "answer_accuracy": cold.answer_accuracy,
                    "answer_judged": cold.answer_judged,
                    "answer_total": cold.answer_total,
                    "refusal": {
                        "accuracy": r.accuracy,
                        "refuse_correct": r.refuse_correct,
                        "refuse_total": r.refuse_total,
                        "answer_correct": r.answer_correct,
                        "answer_total": r.answer_total,
                        "false_answer_rate": r.false_answer_rate,
                        "false_refusal_rate": r.false_refusal_rate,
                    },
                    "citations": cold.citations,
                    "latency_mean": cold.latency_mean,
                    "wall_secs": cold_secs,
                    "leaks": cold.leaks,
                    "failures": cold.failures,
                    "per_item": cold.per_item,
                },
                "warm": {"cache": warm.cache, "wall_secs": warm_secs},
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"[report] {out}")


if __name__ == "__main__":
    main()
