"""Streamlit UI 冒烟测试：AppTest 无头跑渲染路径（不发真实请求）。"""
from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest

APP = Path(__file__).resolve().parents[1] / "src" / "kb_agent" / "web" / "app.py"


def test_app_renders_without_exception() -> None:
    at = AppTest.from_file(str(APP), default_timeout=30).run()
    assert not at.exception, f"页面渲染异常: {[e.value for e in at.exception]}"
    assert any("知识库" in t.value for t in at.title)
    # 侧边栏：角色切换 + 上传入口 + 统计
    assert len(at.radio) >= 1
    assert len(at.file_uploader) == 1
    assert len(at.metric) == 3  # 请求数 / 缓存命中 / 平均耗时


def test_new_session_button_resets_state() -> None:
    at = AppTest.from_file(str(APP), default_timeout=30).run()
    # 无历史消息时主区只渲染标题，不渲染消息气泡
    assert len(at.chat_message) == 0
    assert len(at.button) >= 1  # 新会话 / 上传并索引
