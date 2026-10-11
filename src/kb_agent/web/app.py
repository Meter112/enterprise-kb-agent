"""企业知识库问答 Agent - Web UI（Streamlit，D22-23）。

启动: uv run streamlit run src/kb_agent/web/app.py
依赖: FastAPI 服务（/chat SSE + /documents）已在 8000 端口运行
      （compose 里 web 通过 KB_API_URL=http://api:8000 访问）

功能：SSE 流式问答（token 增量渲染）/ 角色切换 / 会话多轮 /
      文档上传 / 缓存命中统计 / 引用高亮 / 工具轨迹折叠面板
"""
from __future__ import annotations

import json
import os
import time
from typing import Any

import requests
import streamlit as st

API_BASE = os.environ.get("KB_API_URL", "http://localhost:8000")
ROLES = ("public", "contractor", "employee", "admin")
ROLE_LABELS = {
    "public": "公众（公开文档）",
    "contractor": "外包人员",
    "employee": "正式员工",
    "admin": "管理员（全部文档）",
}

st.set_page_config(page_title="云帆科技知识库问答", page_icon="📚", layout="wide")


def init_state() -> None:
    st.session_state.setdefault("messages", [])  # [{"role", "content", "meta"}]
    st.session_state.setdefault("session_id", None)
    st.session_state.setdefault("requests", 0)
    st.session_state.setdefault("cache_hits", 0)
    st.session_state.setdefault("total_latency", 0.0)


def reset_session() -> None:
    st.session_state.messages = []
    st.session_state.session_id = None


init_state()  # 必须先于任何 session_state 读取（含侧边栏）


# ---------- 状态 ----------



# ---------- SSE 消费 ----------


def ask_stream(question: str, role: str, session_id: str | None):
    """POST /chat 消费 SSE，逐事件 yield 生成器（保持 HTTP 连接，前端逐 token 渲染）。"""
    payload: dict[str, Any] = {"question": question, "role": role}
    if session_id:
        payload["session_id"] = session_id
    with requests.post(
        f"{API_BASE}/chat", json=payload, stream=True, timeout=180
    ) as resp:
        resp.raise_for_status()
        for line in resp.iter_lines(decode_unicode=True):
            if line.startswith("data: "):
                yield json.loads(line[len("data: "):])


# ---------- 渲染 ----------


def render_answer(msg: dict) -> None:
    """渲染一条 assistant 消息：答案 + 引用 + 元信息徽章。"""
    meta = msg.get("meta") or {}
    if meta.get("refused"):
        st.warning(msg["content"] or "（拒答）")
    else:
        st.markdown(msg["content"])
    badges = []
    if meta.get("cached"):
        badges.append("⚡ 缓存命中")
    if meta.get("rewritten_query") and meta["rewritten_query"] != msg.get("question"):
        badges.append(f"🔍 改写：{meta['rewritten_query']}")
    if meta.get("latency"):
        badges.append(f"⏱ {meta['latency']:.1f}s")
    if meta.get("citations"):
        badges.append("  ".join(f"📖 {c}" for c in meta["citations"]))
    if badges:
        st.caption("  |  ".join(badges))


def render_tool_trace(msg: dict) -> None:
    """工具轨迹折叠面板：延迟拆解 + 检索候选 + 事件序列（面试展示友好）。"""
    meta = msg.get("meta") or {}
    trace = meta.get("trace") or {}
    if not trace:
        return
    with st.expander("🔎 工具轨迹", expanded=False):
        st.write(f"事件序列：`{' → '.join(trace['event_types'])}`")
        breakdown = trace.get("latency_breakdown") or {}
        if breakdown:
            rows = [
                (label, f"{secs:.2f}s")
                for label, secs in sorted(
                    breakdown.items(), key=lambda kv: kv[1], reverse=True
                )
            ]
            st.markdown("**延迟拆解**：" + "，".join(f"{label} {secs}" for label, secs in rows))
        for c in trace.get("candidates", []):
            st.markdown(
                f"- **{c['citation']}**（rerank {c['rerank_score']:.2f}）\n\n"
                f"  {c['text'][:120]}…"
            )


# ---------- 侧边栏 ----------

with st.sidebar:
    st.header("⚙️ 设置")
    role = st.radio("当前角色", ROLES, format_func=lambda r: ROLE_LABELS[r], index=2)
    st.caption("角色决定可见文档范围（public < contractor < employee < admin）")
    if st.button("🆕 新会话", use_container_width=True):
        reset_session()
        st.rerun()

    st.divider()
    st.header("📊 统计")
    st.metric("请求数", st.session_state.requests)
    st.metric("缓存命中", st.session_state.cache_hits)
    avg = (
        st.session_state.total_latency / st.session_state.requests
        if st.session_state.requests
        else 0.0
    )
    st.metric("平均耗时", f"{avg:.1f}s")

    st.divider()
    st.header("📤 上传文档")
    uploaded = st.file_uploader("选择文件（md/pdf/txt/json）", type=["md", "pdf", "txt", "json"])
    up_role = st.selectbox("文档可见角色", ROLES, format_func=lambda r: ROLE_LABELS[r])
    if st.button("上传并索引", use_container_width=True, disabled=uploaded is None):
        with st.spinner("上传 + 增量索引中…"):
            resp = requests.post(
                f"{API_BASE}/documents",
                files={"file": (uploaded.name, uploaded.getvalue())},
                data={"role": up_role},
                timeout=300,
            )
        if resp.ok:
            data = resp.json()
            st.success(f"已索引 {data['chunks']} chunks（doc_id: {data['doc_id']}，kb_version: {data['kb_version']}）")
        else:
            st.error(f"上传失败: {resp.text[:200]}")


# ---------- 主区 ----------

st.title("📚 云帆科技知识库问答 Agent")
st.caption(
    "RAG + Agent：hybrid 检索 → rerank → 拒答阈值 → 工具调用 → 引用生成 → 多轮记忆 → L1 缓存"
)

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        if msg["role"] == "assistant":
            render_answer(msg)
            render_tool_trace(msg)
        else:
            st.markdown(msg["content"])

question = st.chat_input("输入问题，如「年假有几天」")
if question:
    st.session_state.messages.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)

    t0 = time.perf_counter()
    with st.chat_message("assistant"):
        holder = st.empty()  # token 增量渲染占位
        status = st.empty()
        events = []
        acc = ""  # 累积已收到的 token，逐段刷新渲染
        try:
            for ev in ask_stream(question, role, st.session_state.session_id):
                events.append(ev)
                etype = ev["type"]
                if etype == "tool_call":
                    status.info(f"🔧 调用工具：{ev.get('tools', [])} — {ev.get('query', '')[:40]}")
                elif etype == "retrieved":
                    status.info(f"📚 检索到 {ev.get('count', 0)} 条候选")
                elif etype == "token":
                    acc += ev["text"]
                    holder.markdown(acc + " ▍")
                elif etype == "cached":
                    status.success("⚡ 命中缓存，直接返回")
        except requests.RequestException as e:
            status.error(f"请求失败: {e}")
            st.stop()

        done = events[-1] if events else {}
        answer = done.get("answer", "")
        holder.markdown(answer)

        latency = time.perf_counter() - t0
        st.session_state.requests += 1
        st.session_state.total_latency += latency
        if done.get("cached"):
            st.session_state.cache_hits += 1
        if done.get("type") == "refused" or done.get("refused"):
            status.warning("已拒答（证据不足或无权限）")
        else:
            status.empty()

        candidates = []
        latency_breakdown = {}
        for ev in events:
            if ev["type"] == "retrieved":
                candidates = ev.get("candidates", [])
            elif ev["type"] == "latency":
                latency_breakdown = ev.get("breakdown", {})
        trace = {
            "event_types": [e["type"] for e in events if e["type"] != "token"],
            "candidates": candidates,
            "latency_breakdown": latency_breakdown,
        }

    st.session_state.messages.append(
        {
            "role": "assistant",
            "content": answer,
            "question": question,
            "meta": {
                "cached": done.get("cached", False),
                "refused": done.get("refused", False),
                "citations": done.get("citations") or [],
                "rewritten_query": done.get("rewritten_query"),
                "latency": latency,
                "trace": trace,
            },
        }
    )
    if st.session_state.session_id is None:
        # 首次问答后固定 session，之后进入多轮模式
        st.session_state.session_id = f"web-{int(time.time())}"
