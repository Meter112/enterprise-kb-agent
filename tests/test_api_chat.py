"""/chat SSE 端点测试：请求校验 + 事件序列格式（fake agent，不碰真实模型）。"""
from __future__ import annotations

from fastapi.testclient import TestClient

from kb_agent.api.main import app


class FakeAgent:
    """把预设事件灌进队列，模拟一次完整问答的事件流。"""

    def __init__(self, events: list[dict]) -> None:
        self.events = events
        self.calls: list[dict] = []

    def ask(self, query, role, session_id=None, history=None, events=None):
        self.calls.append({"query": query, "role": role, "session_id": session_id})
        for e in self.events:
            events.put(e)
        events.put({"type": "done", "answer": "年假 5 天", "refused": False})
        events.put(None)
        return {}


def test_chat_sse_event_sequence(monkeypatch) -> None:
    fake = FakeAgent(
        [
            {"type": "tool_call", "tools": ["query_kb"], "query": "年假有几天"},
            {"type": "retrieved", "count": 3, "top1": "年假与调休制度"},
            {"type": "token", "text": "年假", "attempt": 0},
            {"type": "token", "text": " 5 天", "attempt": 0},
            {"type": "citations", "citations": ["《年假与调休制度》§1.3"]},
        ]
    )
    monkeypatch.setattr("kb_agent.api.chat._get_agent", lambda: fake)

    client = TestClient(app)
    with client.stream("POST", "/chat", json={"question": "年假有几天", "role": "employee"}) as resp:
        assert resp.status_code == 200
        body = "".join(resp.iter_text())

    assert "event: tool_call" in body
    assert "event: retrieved" in body
    assert "event: token" in body
    assert "event: citations" in body
    assert "event: done" in body
    # SSE data 是 UTF-8 JSON，中文原样可读
    assert "年假与调休制度" in body
    # 事件顺序：tool_call 先于 done
    assert body.index("event: tool_call") < body.index("event: done")


def test_chat_passes_role_and_session(monkeypatch) -> None:
    fake = FakeAgent([])
    monkeypatch.setattr("kb_agent.api.chat._get_agent", lambda: fake)
    client = TestClient(app)
    with client.stream(
        "POST", "/chat", json={"question": "它规定几天", "role": "contractor", "session_id": "s42"}
    ) as resp:
        assert resp.status_code == 200
        "".join(resp.iter_text())  # 消费完，确保线程跑完
    assert fake.calls == [{"query": "它规定几天", "role": "contractor", "session_id": "s42"}]


def test_chat_rejects_bad_role(monkeypatch) -> None:
    monkeypatch.setattr("kb_agent.api.chat._get_agent", lambda: FakeAgent([]))
    client = TestClient(app)
    resp = client.post("/chat", json={"question": "年假有几天", "role": "superadmin"})
    assert resp.status_code == 400
    assert "role" in resp.json()["detail"]


def test_chat_rejects_empty_question(monkeypatch) -> None:
    monkeypatch.setattr("kb_agent.api.chat._get_agent", lambda: FakeAgent([]))
    client = TestClient(app)
    resp = client.post("/chat", json={"question": "   ", "role": "employee"})
    assert resp.status_code == 400
