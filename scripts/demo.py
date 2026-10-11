"""5 分钟面试演示预演脚本：走 HTTP API 跑完整演示链路，确认不出岔子。

6 步覆盖：单跳事实 / 跨文档对比（拆分检索）/ 权限拒答 / 多轮指代 / L1 缓存命中。
每步打印轨迹事件摘要（工具选择、检索次数、缓存/拒答）与回答节选。
面试现场建议用 Streamlit UI（轨迹面板可视化）；本脚本用于开场前预演和自动化验证。

用法: uv run python scripts/demo.py          # API 需在跑（compose up 或 make api）
"""
from __future__ import annotations

import json
import time

import requests

API = "http://localhost:8000"

STEPS = [
    ("1. 单跳事实型：employee 问年假", "年假有几天", "employee", None),
    ("2. 跨文档对比：拆分检索 + 逐点对照", "正式员工和外包人员的工时要求有什么区别", "contractor", None),
    ("3. 权限拒答：contractor 看不到员工制度", "年假有几天", "contractor", None),
    ("4. 多轮第 1 问：同 session 建上下文", "年假有几天", "employee", "demo-session"),
    ("5. 多轮第 2 问：指代消解「那病假呢」", "那病假呢", "employee", "demo-session"),
    ("6. L1 缓存：重复第 1 步", "年假有几天", "employee", None),
]


def ask(question: str, role: str, session_id: str | None) -> tuple[list[dict], float]:
    events: list[dict] = []
    t0 = time.time()
    with requests.post(
        f"{API}/chat",
        json={"question": question, "role": role, "session_id": session_id},
        stream=True,
        timeout=120,
    ) as r:
        r.raise_for_status()
        for line in r.iter_lines(decode_unicode=True):
            if line and line.startswith("data: "):
                events.append(json.loads(line[6:]))
    return events, time.time() - t0


def render(title: str, question: str, role: str, events: list[dict], secs: float) -> None:
    print(f"\n=== {title}")
    print(f"Q[{role}] {question}")
    tools = [e.get("tools") for e in events if e.get("type") == "tool_call"]
    n_retrieved = sum(1 for e in events if e.get("type") == "retrieved")
    cached = any(e.get("type") == "cached" for e in events)
    refused = next((e.get("reason") for e in events if e.get("type") == "refused"), None)
    trail = []
    if tools:
        trail.append(f"tools={tools[0]}")
    if n_retrieved:
        trail.append(f"检索 {n_retrieved} 次")
    if cached:
        trail.append("L1 缓存命中")
    if refused:
        trail.append(f"拒答({refused})")
    print(f"[轨迹] {' | '.join(trail)} | {secs:.1f}s")
    answer = "".join(e.get("text", "") for e in events if e.get("type") == "token")
    if not answer:  # 缓存命中不走 token 流，answer 在 done 事件里
        answer = next((e.get("answer", "") for e in events if e.get("type") == "done"), "")
    print(f"[回答] {answer[:280]}{'…' if len(answer) > 280 else ''}")


def main() -> None:
    health = requests.get(f"{API}/health", timeout=10)
    if health.status_code != 200:
        print(f"API 不可用（{health.status_code}），先 `docker compose up -d` 或 `make infra && make api`")
        raise SystemExit(1)
    print(f"health: {health.json().get('status')}（索引 {health.json().get('kb_version', '?')}）")

    for title, question, role, session_id in STEPS:
        events, secs = ask(question, role, session_id)
        render(title, question, role, events, secs)


if __name__ == "__main__":
    main()
