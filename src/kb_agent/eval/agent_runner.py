"""D24-25：Agent 全链路端到端评估（对比 Week 2 RAG 基线）。D30 升级：
v2 QA 集（answer_points 要点判分）+ multi_turn 会话支持 + 场景分组 + 失败归因字段。

RAG 基线（D14）判检索级指标（Hit@5/MRR/拒答阈值），Agent 端到端判的是
「生成的答案是否正确」：flash judge 对照 QA 集 answer_points。指标：
- answer_accuracy：应答题中 judge 判对的比例（端到端核心，RAG 基线没有）
- 拒答双面：沿用 RefusalEval（应拒侧/应答侧分开）；漏拒（应拒却答）单列 leaks，
  口径用 judge_leak 判「是否实质回答」——软拒答话术（「未找到足够信息」）不算泄漏
- citations：应答题引用覆盖率（答案含 [n] 引用）
- latency：端到端每查询耗时（含 LLM 生成，与 RAG 检索延迟分列披露）
- cache：warm 轮（第二遍）L1 命中率与延迟
- by_scenario：按 scenario_type 分组的正确率/Hit@5（小样本用 Wilson 区间披露）
- per_item 带 gold/pre_rerank/retrieved 快照，供 error_analysis 归因

multi_turn：同一 session 顺序跑 turns（前几轮不判分只查误拒），末轮按
answer_points 判分。multi_turn 条目带 session_id，不参与 L1 缓存。
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
    ttft_mean: float = 0.0  # cold 轮首 token 平均耗时（缓存命中无生成，不参与）
    cache: dict[str, Any] = field(default_factory=dict)  # warm 轮统计
    failures: list[str] = field(default_factory=list)
    per_item: list[dict] = field(default_factory=list)
    by_scenario: dict[str, dict] = field(default_factory=dict)


class AgentEvalRunner:
    """跑 QA 集端到端。judge_client 为 None 时跳过 judge（只统计拒答/延迟/缓存）。"""

    def __init__(self, agent: Any, judge_client: Any | None = None) -> None:
        self.agent = agent
        self.judge_client = judge_client

    def _ask(self, item: QAItem, session_id: str | None) -> dict:
        t0 = time.perf_counter()
        result = self.agent.ask(item.question, item.role, session_id=session_id)
        result["_lat"] = time.perf_counter() - t0
        return result

    def run(self, items: list[QAItem], warm_cache: bool = False) -> AgentEvalReport:
        report = AgentEvalReport(total=len(items))
        latencies: list[float] = []
        ttfts: list[float] = []
        n_correct = 0
        n_cited = 0
        n_answered = 0
        n_cache_hits = 0
        # 场景分组：{type: {"n", "correct", "judged", "hit", "n_gold"}}
        grouped: dict[str, dict] = {}

        for item in items:
            g = grouped.setdefault(
                item.scenario_type, {"n": 0, "correct": 0, "judged": 0, "hit": 0, "n_gold": 0}
            )
            g["n"] += 1

            # multi_turn：同一 session 顺序跑历史轮（只查误拒），末轮走正常判分。
            # session 带轮次后缀：warm 轮不能复用 cold 轮 checkpoint（历史会串轮）
            early_refused: list[str] = []
            if item.turns:
                round_tag = "warm" if warm_cache else "cold"
                sid = f"eval-{item.qa_id}-{round_tag}"
                for t in item.turns:
                    try:
                        r = self.agent.ask(t.question, item.role, session_id=sid)
                        if r.get("refused"):
                            early_refused.append(t.question[:40])
                    except Exception as e:  # noqa: BLE001
                        report.failures.append(f"{item.qa_id}/turns: {e}")
                        early_refused.append(f"<异常 {e}>")
                        break
                result = self._ask(item, sid) if not early_refused else {
                    "answer": "", "refused": True, "refuse_reason": "multi_turn_early_fail",
                    "_lat": 0.0, "cached": False,
                }
            else:
                try:
                    result = self._ask(item, None)
                except Exception as e:  # noqa: BLE001
                    report.failures.append(f"{item.qa_id}: {e}")
                    continue

            lat = float(result.get("_lat", 0.0))
            latencies.append(lat)

            refused = bool(result.get("refused"))
            answer = result.get("answer", "")
            cached = bool(result.get("cached"))
            ttft = float((result.get("latency_breakdown") or {}).get("generate_ttft", 0.0))
            if not cached and ttft > 0:
                ttfts.append(ttft)

            # 检索快照：最终候选 ids + gold 命中（失败归因用）
            retrieved_ids = [r["chunk_id"] for r in result.get("retrieved") or []]
            gold = set(item.gold_chunk_ids)
            hit = bool(gold) and bool(gold & set(retrieved_ids))
            if gold:
                g["n_gold"] += 1
                g["hit"] += int(hit)

            if warm_cache:
                n_cache_hits += int(cached)
                report.per_item.append(
                    {"id": item.qa_id, "cached": cached, "latency": round(lat, 3)}
                )
                continue

            rec = {
                "id": item.qa_id,
                "category": item.scenario_type,
                "difficulty": item.difficulty,
                "question": item.question,
                "role": item.role,
                "answer_points": item.answer_points,
                "expect_refusal": item.expect_refusal,
                "refused": refused,
                "gold_chunk_ids": item.gold_chunk_ids,
                "retrieved_ids": retrieved_ids,
                "hit": hit,
                "latency": round(lat, 3),
                "ttft": round(ttft, 3),
                "turns_refused": early_refused,
            }
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
                            report.failures.append(f"{item.qa_id}: judge_leak: {e}")
                            answered = True  # judge 挂了保守记 leak
                    else:
                        answered = True  # 无 judge 时保守记 leak
                    if answered:
                        report.leaks.append(f"{item.qa_id}: {answer[:80]}")
                    rec["leak_answered"] = answered
                report.per_item.append(rec)
            else:
                report.refusal.record(False, refused)
                if refused:
                    rec["refuse_reason"] = result.get("refuse_reason", "")
                    rec["judge_correct"] = None
                    report.per_item.append(rec)
                    continue
                n_answered += 1
                n_cited += int(bool(CITATION_RE.findall(answer)))
                judge_correct: bool | None = None
                votes = 0
                if self.judge_client is not None:
                    try:
                        judge_correct, _ = judge_answer(
                            self.judge_client, item.question, answer, item.answer_points
                        )
                        votes = int(not judge_correct)
                        if not judge_correct:
                            # flash judge 对边界情形有判分噪音：再判 2 次，3 次里 ≥2 次判错才定错
                            for _ in range(2):
                                v, _ = judge_answer(
                                    self.judge_client, item.question, answer, item.answer_points
                                )
                                votes += int(not v)
                            judge_correct = votes < 2
                        n_correct += int(judge_correct)
                        report.answer_judged += 1
                        g["judged"] += 1
                        g["correct"] += int(judge_correct)
                    except Exception as e:  # noqa: BLE001
                        report.failures.append(f"{item.qa_id}: judge: {e}")
                rec.update(
                    {
                        "judge_correct": judge_correct,
                        "judge_votes": votes,  # 判错票数（1/2/3），2 记判分噪音
                        "answer": answer[:600],
                    }
                )
                report.per_item.append(rec)

        report.latency_mean = mean(latencies)
        report.ttft_mean = mean(ttfts)
        report.answer_total = n_answered
        report.answer_accuracy = (
            n_correct / report.answer_judged if report.answer_judged else 0.0
        )
        report.citations = {"covered": n_cited, "total": n_answered}
        report.by_scenario = grouped
        if warm_cache:
            report.cache = {
                "hits": n_cache_hits,
                "total": len(latencies),
                "rate": n_cache_hits / len(latencies) if latencies else 0.0,
                "latency_mean": report.latency_mean,
            }
        return report
