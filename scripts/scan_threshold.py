"""拒答阈值标定（D14）：读评估报告的 top5_scores 快照，离线扫描阈值网格。

一次 hybrid 评估跑完后，各阈值下的拒答准确率可以纯离线重算，
不必为每个候选阈值重跑检索（每条 ~19s × 32 条）。

用法: uv run python scripts/scan_threshold.py [report.json]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from kb_agent.eval.metrics import RefusalEval

GRID = [0.2, 0.3, 0.4, 0.5, 0.55, 0.6, 0.65, 0.7, 0.75, 0.8, 0.85, 0.9]


def scan(report_path: Path) -> list[tuple[float, RefusalEval]]:
    report = json.loads(report_path.read_text(encoding="utf-8"))
    per_item = report["per_item"]
    results: list[tuple[float, RefusalEval]] = []
    for t in GRID:
        r = RefusalEval()
        for p in per_item:
            scores = p.get("top5_scores") or []
            # 与 refuse_decision 同语义：无 rerank 分（no_rerank/dense/sparse 模式）不判低置信
            refused = bool(scores) and max(scores) < t
            r.record(p["expect_refusal"], refused)
        results.append((t, r))
    return results


def main() -> None:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else max(
        Path("data/reports").glob("eval_hybrid_*.json"), key=lambda p: p.stat().st_mtime
    )
    print(f"report: {path}")
    print(f"{'阈值':>6} {'准确率':>8} {'漏拒率':>8} {'误拒率':>8}")
    for t, r in scan(path):
        print(f"{t:>6.2f} {r.accuracy:>8.3f} {r.false_answer_rate:>8.3f} {r.false_refusal_rate:>8.3f}")


if __name__ == "__main__":
    main()
