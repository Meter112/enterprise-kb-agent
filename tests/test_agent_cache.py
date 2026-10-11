"""L1 精确缓存测试（D20-21）：KBAgent 集成（fake cache）+ ExactCache 真 Redis。

缓存规则：只缓存单轮（无 session_id/history）；命中不发任何 LLM/检索调用；
输入级错误不写缓存；kb_version 变化 key 前缀不同自然失效。
"""
from __future__ import annotations

import pytest

from kb_agent.agent.cache import ExactCache, normalize_query
from kb_agent.agent.graph import KBAgent
from kb_agent.agent.llm import LLMReply
from kb_agent.retrieval.hybrid_retriever import RetrievedChunk

# ---------- fakes ----------


class FakeLLM:
    def __init__(self, replies=None, streams=None) -> None:
        self.replies = list(replies or [])
        self.streams = list(streams or [])
        self.calls: list[dict] = []
        self.stream_calls: list[dict] = []

    def chat(self, messages, tools=None, max_tokens=None, temperature=0.1):
        self.calls.append({"messages": messages, "tools": tools})
        return self.replies.pop(0)

    def chat_stream(self, messages, max_tokens=None, temperature=0.1):
        self.stream_calls.append({"messages": messages})
        return iter(self.streams.pop(0) if self.streams else [])


class FakeStore:
    @property
    def client(self):
        raise AssertionError("单测不应访问真实 store")


class FakeCache:
    """dict 实现 ExactCache 接口，按 (normalize(query), role) 存。"""

    def __init__(self, data: dict | None = None) -> None:
        self.data = dict(data or {})
        self.set_calls: list[tuple] = []

    def get(self, query: str, role: str):
        return self.data.get((normalize_query(query), role))

    def set(self, query: str, role: str, result: dict) -> None:
        self.set_calls.append((normalize_query(query), role, result))
        self.data[(normalize_query(query), role)] = result


def _chunk(rerank: float = 0.95) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id="c1",
        doc_id="doc1#md",
        title="年假与调休制度",
        text="年假满 1 年不满 10 年为 5 天。",
        title_path=[],
        page=None,
        section="2.1",
        role="employee",
        is_code=False,
        rrf_score=0.9,
        rerank_score=rerank,
    )


def _tool_call(arguments: str = '{"query": "年假有几天"}') -> dict:
    return {"id": "call-1", "name": "query_kb", "arguments": arguments}


def _agent(cache, replies=None, streams=None, chunks=None, monkeypatch=None):
    """构造 (agent, llm)；miss 路径经 monkeypatch 把内部 retriever 的 retrieve 换成 fake。"""
    llm = FakeLLM(replies, streams)
    agent = KBAgent(FakeStore(), None, None, None, llm, cache=cache)
    if monkeypatch is not None:

        def fake_retrieve(query, roles, top_k=5, with_rerank=True, timings=None):
            return list(chunks or [])

        monkeypatch.setattr(agent.retriever, "retrieve", fake_retrieve)
    return agent, llm


CACHED_VALUE = {
    "answer": "年假 5 天。",
    "citations": ["《年假与调休制度》§2.1"],
    "refused": False,
    "refuse_reason": "",
}

# ---------- KBAgent 集成 ----------


def test_cache_hit_skips_llm_and_retriever(monkeypatch) -> None:
    agent, llm = _agent(
        FakeCache({(normalize_query("年假有几天"), "employee"): CACHED_VALUE}),
        monkeypatch=monkeypatch,
    )
    out = agent.ask("年假有几天", "employee")
    assert out["cached"] and out["answer"] == "年假 5 天。"
    assert out["trace"] == [{"node": "cache_hit"}]
    assert out["rewritten_query"] == "年假有几天"
    # 命中路径不触发任何 LLM 调用（retriever 未被 monkeypatch 到也证明没被调用）
    assert llm.calls == [] and llm.stream_calls == []


def test_cache_miss_runs_graph_then_writes(monkeypatch) -> None:
    cache = FakeCache()
    agent, _ = _agent(
        cache,
        replies=[LLMReply(content="", tool_calls=[_tool_call()])],
        streams=[["5 天[1]"]],
        chunks=[_chunk()],
        monkeypatch=monkeypatch,
    )
    out = agent.ask("年假有几天", "employee")
    assert not out["cached"] and "5 天" in out["answer"]
    assert len(cache.set_calls) == 1
    key, role, value = cache.set_calls[0]
    assert key == normalize_query("年假有几天") and role == "employee"
    assert value["answer"] == out["answer"] and value["citations"] == out["citations"]
    assert "trace" not in value  # 缓存只存四要素


def test_session_id_bypasses_cache(monkeypatch) -> None:
    cache = FakeCache({(normalize_query("年假有几天"), "employee"): CACHED_VALUE})
    agent, _ = _agent(
        cache,
        replies=[LLMReply(content="", tool_calls=[_tool_call()])],
        streams=[["5 天[1]"]],
        chunks=[_chunk()],
        monkeypatch=monkeypatch,
    )
    out = agent.ask("年假有几天", "employee", session_id="s1")
    assert not out["cached"] and "5 天" in out["answer"]
    assert cache.set_calls == []  # 多轮请求不读也不写缓存


def test_history_bypasses_cache(monkeypatch) -> None:
    cache = FakeCache({(normalize_query("它规定几天"), "employee"): CACHED_VALUE})
    agent, _ = _agent(
        cache,
        replies=[
            LLMReply(content="年假天数是多少"),  # rewrite（有历史触发）
            LLMReply(content="", tool_calls=[_tool_call(arguments='{"query": "年假天数是多少"}')]),
        ],
        streams=[["5 天[1]"]],
        chunks=[_chunk()],
        monkeypatch=monkeypatch,
    )
    out = agent.ask(
        "它规定几天", "employee", history=[{"role": "user", "content": "员工有哪些假期"}]
    )
    assert not out["cached"] and out["rewritten_query"] == "年假天数是多少"


def test_input_error_not_cached() -> None:
    agent, _ = _agent(FakeCache())
    out = agent.ask("   ", "employee")
    assert out["refused"] and out["refuse_reason"] == "empty_query"
    assert agent.cache.set_calls == []


# ---------- ExactCache 真 Redis ----------


@pytest.fixture
def rc():
    """db 15 隔离；Redis 不可用（docker 未起）时跳过。"""
    import redis as redis_lib

    test_url = "redis://localhost:6379/15"
    try:
        redis_lib.from_url(test_url).ping()
    except Exception:  # noqa: BLE001
        pytest.skip("Redis 不可用，跳过 ExactCache 集成测试")
    cache = ExactCache(test_url)
    cache.redis.flushdb()
    yield cache
    cache.redis.flushdb()


def test_exact_cache_roundtrip_and_ttl(rc) -> None:
    rc.set("年假有几天", "employee", CACHED_VALUE)
    got = rc.get("年假有几天", "employee")
    assert got == CACHED_VALUE
    # 任何 key 都带 TTL
    keys = rc.redis.keys("answer_cache:*")
    assert keys and rc.redis.ttl(keys[0]) > 0


def test_exact_cache_normalize_merges_whitespace(rc) -> None:
    rc.set("  年假有几天 ", "employee", CACHED_VALUE)
    assert rc.get("年假有几天", "employee") == CACHED_VALUE
    assert rc.get("年假\n有几天", "employee") == CACHED_VALUE


def test_exact_cache_role_isolation(rc) -> None:
    rc.set("年假有几天", "employee", CACHED_VALUE)
    assert rc.get("年假有几天", "contractor") is None


def test_exact_cache_version_changes_key(rc, monkeypatch) -> None:
    from kb_agent.agent import cache as cache_mod

    monkeypatch.setattr(cache_mod, "_kb_version", lambda: 5)
    rc.set("年假有几天", "employee", CACHED_VALUE)
    assert rc.get("年假有几天", "employee") == CACHED_VALUE
    # 版本推进后旧 key 不再命中
    monkeypatch.setattr(cache_mod, "_kb_version", lambda: 6)
    assert rc.get("年假有几天", "employee") is None
