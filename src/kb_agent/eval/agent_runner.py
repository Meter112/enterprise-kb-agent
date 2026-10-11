"""D24-25：Agent 全链路端到端评估（对比 Week 2 RAG 基线）。

RAG 基线（D14）判检索级指标（Hit@5/MRR/拒答阈值），Agent 端到端判的是
「生成的答案是否正确」：flash judge 对照 QA 集 answer_hint。指标：
- answer_accuracy：应答题中 judge 判对的比例（端到端核心，RAG 基线没有）
- 拒答双面：沿用 RefusalEval（应拒侧/应答侧分开）；漏拒（应拒却答）单列 leaks，
  口径用 judge_leak 判「是否实质回答」——软拒答话术（「未找到足够信息」）不算泄漏
- citations：应答题引用覆盖率（答案含 [n] 引用）
- latency：端到端每查询耗时（含 LLM 生成，与 RAG 检索延迟分列披露）
- cache：warm 轮（第二遍）L1 命中率与延迟

用法见 scripts/eval_agent.py。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from kb_agent.eval.judge import judge_answer, judge_leak
from kb_agent.eval.metrics import RefusalEval, mean
from kb_agent.eval.qa_set import QAItem
from kb_agent.retrieval.citations import CITATION_RE


@dataclass
class AgentEvalReport:
    total: int = 0
    answer_accuracy: float = 0.0  # 应答题 judge 正确率（judge 失败不计入分母）
    answer_judged: int = 0
    answer_total: int = 0
    refusal: RefusalEval = field(default_factory=RefusalEval)
    leaks: list[str] = field(default_factory=list)  # 应拒却答：item id + 答案开头
    citations: dict[str, int] = field(default_factory=dict)  # {"covered": n, "total": n}
    latency_mean: float = 0.0
    cache: dict[str, Any] = field(default_factory=dict)  # warm 轮统计
    failures: list[str] = field(default_factory=list)
    per_item: list[dict] = field(default_factory=list)


class AgentEvalRunner:
    """跑 QA 集端到端。judge_client 为 None 时跳过 judge（只统计拒答/延迟/缓存）。"""

    def __init__(self, agent: Any, judge_client: Any | None = None) -> None:
        self.agent = agent
        self.judge_client = judge_client

    def run(self, items: list[QAItem], warm_cache: bool = False) -> AgentEvalReport:
        report = AgentEvalReport(total=len(items))
        latencies: list[float] = []
        n_correct = 0
        n_cited = 0
        n_answered = 0
        n_cache_hits = 0

        for item in items:
            t0 = time.perf_counter()
            try:
                result = self.agent.ask(item.question, item.roles[0])
            except Exception as e:  # noqa: BLE001
                report.failures.append(f"{item.id}: {e}")
                continue
            lat = time.perf_counter() - t0
            latencies.append(lat)

            refused = bool(result.get("refused"))
            answer = result.get("answer", "")
            cached = bool(result.get("cached"))

            if warm_cache:
                n_cache_hits += int(cached)
                report.per_item.append(
                    {"id": item.id, "cached": cached, "latency": round(lat, 3)}
                )
                continue

            if item.expect_refusal:
                report.refusal.record(True, refused)
                if not refused:
                    # 漏拒口径：refused=False 只是软拒答（话术上「未找到」）也算守住了，
                    # 用 judge_leak 判「是否实质回答」，实质回答才记 leak
                    answered: bool | None = None
                    if self.judge_client is not None:
                        try:
                            answered, _ = judge_leak(self.judge_client, item.question, answer)
                        except Exception as e:  # noqa: BLE001
                            report.failures.append(f"{item.id}: judge_leak: {e}")
                            answered = True  # judge 挂了保守记 leak
                    else:
                        answered = True  # 无 judge 时保守记 leak
                    if answered:
                        report.leaks.append(f"{item.id}: {answer[:80]}")
                    report.per_item.append(
                        {
                            "id": item.id,
                            "category": item.category,
                            "question": item.question,
                            "expect_refusal": True,
                            "refused": refused,
                            "leak_answered": answered,
                            "latency": round(lat, 3),
                        }
                    )
            else:
                report.refusal.record(False, refused)
                if refused:
                    report.per_item.append(
                        {
                            "id": item.id,
                            "category": item.category,
                            "question": item.question,
                            "expect_refusal": False,
                            "refused": True,
                            "refuse_reason": result.get("refuse_reason", ""),
                            "judge_correct": None,
                            "latency": round(lat, 3),
                        }
                    )
                    continue
                n_answered += 1
                n_cited += int(bool(CITATION_RE.findall(answer)))
                judge_correct: bool | None = None
                if self.judge_client is not None:
                    try:
                        judge_correct, _ = judge_answer(
                            self.judge_client, item.question, answer, item.answer_hint
                        )
                        if not judge_correct:
                            # flash judge 对边界情形有判分噪音：再判 2 次，3 次里 ≥2 次判错才定错
                            votes = 1  # 首次已判 False
                            for _ in range(2):
                                v, _ = judge_answer(
                                    self.judge_client, item.question, answer, item.answer_hint
                                )
                                votes += int(not v)
                            judge_correct = votes < 2
                        n_correct += int(judge_correct)
                        report.answer_judged += 1
                    except Exception as e:  # noqa: BLE001
                        report.failures.append(f"{item.id}: judge: {e}")
                report.per_item.append(
                    {
                        "id": item.id,
                        "category": item.category,
                        "question": item.question,
                        "expect_refusal": False,
                        "refused": False,
                        "judge_correct": judge_correct,
                        "latency": round(lat, 3),
                        "answer_head": answer[:120],
                    }
                )

        report.latency_mean = mean(latencies)
        report.answer_total = n_answered
        report.answer_accuracy = (
            n_correct / report.answer_judged if report.answer_judged else 0.0
        )
        report.citations = {"covered": n_cited, "total": n_answered}
        if warm_cache:
            report.cache = {
                "hits": n_cache_hits,
                "total": len(latencies),
                "rate": n_cache_hits / len(latencies) if latencies else 0.0,
                "latency_mean": report.latency_mean,
            }
        return report
