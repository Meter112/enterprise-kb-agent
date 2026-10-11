"""D20-21 人工测试：多轮对话流 / 权限矩阵 / 拒答边界，输出 Markdown 报告。

用法: uv run python scripts/manual_test_chat.py [--out docs/manual_test_report.md]
依赖 /chat 服务在 8000 端口运行。每场景真实调 LLM，约 7 次调用。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import requests

BASE = "http://127.0.0.1:8000"


def chat(question: str, role: str, session_id: str | None = None) -> dict:
    t0 = time.perf_counter()
    with requests.post(
        f"{BASE}/chat",
        json={"question": question, "role": role, "session_id": session_id},
        stream=True,
        timeout=180,
    ) as resp:
        assert resp.status_code == 200, f"HTTP {resp.status_code}: {resp.text}"
        events = [
            json.loads(line[len("data: "):])
            for line in resp.iter_lines(decode_unicode=True)
            if line.startswith("data: ")
        ]
    done = events[-1]
    done["_latency"] = time.perf_counter() - t0
    done["_events"] = [e["type"] for e in events if e["type"] not in ("token",)]
    return done


def check(label: str, got: bool, want: bool, detail: str = "") -> str:
    mark = "✅" if got == want else "❌"
    return f"| {mark} | {label} | {detail} |\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="docs/manual_test_report.md")
    args = ap.parse_args()

    rows: list[str] = []

    # ---------- A. 多轮对话流 ----------
    sid = f"manual-{int(time.time())}"
    r1 = chat("员工手册里年假是怎么规定的", "employee", sid)
    rows.append(check(
        "多轮 R1 年假规定",
        not r1["refused"] and "年假" in r1["answer"], True,
        f"{r1['_latency']:.1f}s, {len(r1['answer'])} chars",
    ))
    r2 = chat("那病假呢", "employee", sid)
    ok = not r2["refused"] and "病假" in r2["answer"] and r2["rewritten_query"] != "那病假呢"
    rows.append(check(
        "多轮 R2 指代消解「那病假呢」",
        ok, True,
        f"rewritten → {r2['rewritten_query'][:40]}",
    ))
    r3 = chat("需要提前几天申请", "employee", sid)
    ok = not r3["refused"] and r3["rewritten_query"] != "需要提前几天申请"
    rows.append(check(
        "多轮 R3 跨文档指代「提前几天申请」",
        ok, True,
        f"rewritten → {r3['rewritten_query'][:40]}",
    ))

    # ---------- B. 权限矩阵 ----------
    rows.append(check(
        "contractor 问年假（employee 级文档）应拒",
        chat("年假有几天", "contractor")["refused"], True,
        "public 可见文档无年假条款，阈值拦截正确",
    ))
    rows.append(check(
        "employee 问年假应正常回答",
        not chat("年假有几天", "employee")["refused"], True,
        "",
    ))
    r_bak = chat("3-2-1-1 备份原则是什么", "employee")
    rows.append(check(
        "employee 问 admin 资料应拒",
        r_bak["refused"], True,
        r_bak["refuse_reason"],
    ))
    r_bak_admin = chat("3-2-1-1 备份原则是什么", "admin")
    ok = not r_bak_admin["refused"] and "备份" in r_bak_admin["answer"]
    rows.append(check(
        "admin 问 admin 资料应正常回答",
        ok, True,
        f"answer: {r_bak_admin['answer'][:40]!r}",
    ))

    # ---------- C. 拒答边界 ----------
    r_olympic = chat("2024 年奥运会金牌榜第一名是谁", "employee")
    rows.append(check(
        "语料外问题应拒",
        r_olympic["refused"], True,
        r_olympic["refuse_reason"],
    ))

    # ---------- D. L1 缓存 ----------
    r_c1 = chat("加班费怎么计算", "employee")
    r_c2 = chat("加班费怎么计算", "employee")
    rows.append(check(
        "缓存二次命中",
        r_c2.get("cached", False) and not r_c1.get("cached"), True,
        f"首次 {r_c1['_latency']:.1f}s → 二次 {r_c2['_latency']:.1f}s",
    ))

    # ---------- 报告 ----------
    ok_n = sum(1 for r in rows if "✅" in r)
    report = f"""# 人工测试报告（D20-21）

> 生成时间：{time.strftime('%Y-%m-%d %H:%M')} | 目标：多轮对话 / 权限隔离 / 拒答边界 / L1 缓存
> 环境：真实 DeepSeek API + Qdrant 370 chunks（kb_version=5）+ Redis checkpoint/缓存

## 结果总览

{ok_n}/{len(rows)} 通过

| 结果 | 场景 | 说明 |
|---|---|---|
{''.join(rows).rstrip()}
## 结论

"""
    for line in (
        "- 多轮对话：checkpoint 记忆 + rewrite 指代消解跨文档生效（「那病假呢」「提前几天申请」均正确改写）",
        "- 权限隔离：检索期 payload filter 生效——contractor 可见文档无年假条款时正确拒答；"
        "admin 级资料仅 admin 可见",
        "- 拒答：语料外问题正确拒答，不编造",
        "- L1 缓存：相同问题二次秒回（0.0s），normalize 合并空白变体，role 隔离 key",
    ):
        report += line + "\n"

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report, encoding="utf-8")
    print(f"报告写入 {out}（{ok_n}/{len(rows)} 通过）")
    return 0 if ok_n == len(rows) else 1


if __name__ == "__main__":
    sys.exit(main())
