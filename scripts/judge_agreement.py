"""LLM judge 可信度校验（D30）：20 条 LLM-judge vs 人工标注，Cohen's kappa。

流程：
1. uv run python scripts/judge_agreement.py --template data/reports/eval_agent_*.json
   → 从评估报告按场景分层抽 20 条应答题，写模板
     data/qa_sets/judge_agreement_template.jsonl（含 LLM 判分，human_correct 待填）
2. 人工逐条填 human_correct（true/false）
3. uv run python scripts/judge_agreement.py --report data/qa_sets/judge_agreement_template.jsonl
   → 一致率 + Cohen's kappa + 分歧明细

只抽应答题：拒答题的「正确性」语义是拒答行为本身（程序化判定），不与
内容 judge 混算 kappa。
"""
from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

from kb_agent.eval.metrics import cohens_kappa

TEMPLATE = "data/qa_sets/judge_agreement_template.jsonl"


def stratified_sample(records: list[dict], n: int = 20, seed: int = 42) -> list[dict]:
    """按 scenario_type 分层抽样：每类 ≥2（不足全取），余量按占比分配。"""
    rng = random.Random(seed)
    by_type: dict[str, list[dict]] = {}
    for r in records:
        by_type.setdefault(r.get("scenario_type", "?"), []).append(r)
    picked: list[dict] = []
    for rs in by_type.values():
        picked.extend(rng.sample(rs, min(2, len(rs))))
    rest = [r for r in records if r not in picked]
    need = n - len(picked)
    if rest and need > 0:
        picked.extend(rng.sample(rest, min(need, len(rest))))
    return picked


def make_template(report_path: str, out: str) -> None:
    data = json.loads(Path(report_path).read_text(encoding="utf-8"))
    per_item = data["cold"]["per_item"]
    # 应答题且 judge 有结论（判对判错都行，人工要核对的就是 judge 的判定）
    pool = [
        r
        for r in per_item
        if r.get("scenario_type")
        and r.get("judge_correct") is not None
    ]
    sample = stratified_sample(pool)
    lines = []
    for r in sorted(sample, key=lambda x: x["qa_id"]):
        lines.append(
            {
                "qa_id": r["qa_id"],
                "scenario_type": r.get("scenario_type"),
                "question": r.get("question"),
                "answer": r.get("answer", ""),
                "answer_points": r.get("answer_points", []),
                "llm_correct": bool(r["judge_correct"]),
                "human_correct": None,  # 人工填 true/false
            }
        )
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(
        "\n".join(json.dumps(rec, ensure_ascii=False) for rec in lines) + "\n",
        encoding="utf-8",
    )
    print(f"[template] 抽 {len(lines)} 条 → {out}（人工填 human_correct 后跑 --report）")


def report(template_path: str) -> None:
    lines = [
        json.loads(rec)
        for rec in Path(template_path).read_text(encoding="utf-8").splitlines()
        if rec.strip()
    ]
    filled = [rec for rec in lines if rec.get("human_correct") is not None]
    if len(filled) < len(lines):
        print(f"[warn] {len(lines) - len(filled)} 条未标注，只算已标注 {len(filled)} 条")
    if len(filled) < 5:
        print("[abort] 已标注不足 5 条，kappa 无意义")
        return
    llm = [bool(rec["llm_correct"]) for rec in filled]
    human = [bool(rec["human_correct"]) for rec in filled]
    agree = sum(1 for x, y in zip(llm, human, strict=True) if x == y)
    print("\n==== judge 一致性 ====")
    print(f"标注 {len(filled)} 条，一致 {agree}（{agree / len(filled):.0%}），"
          f"Cohen's kappa = {cohens_kappa(llm, human):.3f}")
    print("\n分歧明细：")
    for rec in filled:
        if bool(rec["llm_correct"]) != bool(rec["human_correct"]):
            print(f"- {rec['qa_id']}（{rec.get('scenario_type')}）{rec.get('question', '')[:40]}："
                  f"LLM={rec['llm_correct']} 人工={rec['human_correct']}")
    print(f"\n判分分布：LLM 判对 {sum(llm)} / 人工判对 {sum(human)}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--template", metavar="REPORT_JSON", help="从评估报告抽 20 条生成模板")
    ap.add_argument("--report", metavar="TEMPLATE_JSONL", help="读已标注模板算 kappa")
    ap.add_argument("--out", default=TEMPLATE, help="模板输出路径")
    args = ap.parse_args()
    if args.template:
        make_template(args.template, args.out)
    elif args.report:
        report(args.report)
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
