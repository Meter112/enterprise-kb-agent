"""D24-25：AgentEvalRunner 判分逻辑单测（fake agent + fake judge，不调真实 LLM）。"""
from __future__ import annotations

import pytest

from kb_agent.eval.agent_runner import AgentEvalRunner
from kb_agent.eval.qa_set import QAItem


class FakeAgent:
    """按 item.id 返回预设结果。"""

    def __init__(self, outcomes: dict[str, dict]) -> None:
        self.outcomes = outcomes
        self.asked: list[str] = []

    def ask(self, question: str, role: str) -> dict:
        self.asked.append(question)
        return self.outcomes[question]


class FakeJudge:
    """按 question 返回 (correct, reason)。"""

    def __init__(self, verdicts: dict[str, bool]) -> None:
        self.verdicts = verdicts

    def chat(self):
        raise NotImplementedError


@pytest.fixture()
def fake_judge(monkeypatch):
    """monkeypatch judge_answer/judge_leak，FakeJudge 无需实现 OpenAI 协议。"""
    verdicts: dict[str, bool] = {}
    leak_verdicts: dict[str, bool] = {}

    def fake_judge_answer(client, question, answer, hint, model=None):
        return verdicts.get(question, True), "ok"

    def fake_judge_leak(client, question, answer, model=None):
        return leak_verdicts.get(question, True), "ok"

    monkeypatch.setattr("kb_agent.eval.agent_runner.judge_answer", fake_judge_answer)
    monkeypatch.setattr("kb_agent.eval.agent_runner.judge_leak", fake_judge_leak)
    return {"answer": verdicts, "leak": leak_verdicts}


def item(qid: str, expect_refusal: bool = False, hint: str = "要点") -> QAItem:
    return QAItem(
        id=qid,
        category="single_hop",
        question=f"问题{qid}",
        roles=["employee"],
        answer_hint=hint,
        expect_refusal=expect_refusal,
    )


def test_answer_correct_and_citations(fake_judge):
    agent = FakeAgent({
        "问题a": {"refused": False, "answer": "答案[1]", "citations": ["制度A"]},
    })
    report = AgentEvalRunner(agent, judge_client=object()).run([item("a")])
    assert report.answer_accuracy == 1.0
    assert report.answer_judged == 1
    assert report.citations == {"covered": 1, "total": 1}


def test_answer_judged_wrong(fake_judge):
    fake_judge["answer"]["问题a"] = False
    agent = FakeAgent({"问题a": {"refused": False, "answer": "无关内容", "citations": []}})
    report = AgentEvalRunner(agent, judge_client=object()).run([item("a")])
    assert report.answer_accuracy == 0.0
    assert report.citations == {"covered": 0, "total": 1}


def test_refusal_both_sides(fake_judge):
    fake_judge["leak"]["问题b"] = True  # judge_leak 判实质回答了
    agent = FakeAgent({
        "问题a": {"refused": True, "answer": "", "refuse_reason": "low_confidence"},
        "问题b": {"refused": False, "answer": "泄漏内容", "citations": []},
    })
    report = AgentEvalRunner(agent, judge_client=object()).run(
        [item("a"), item("b", expect_refusal=True)]
    )
    # a 误拒：answer_total=1, answer_correct=0；b 漏拒：refuse_total=1, refuse_correct=0
    assert report.refusal.accuracy == 0.0
    assert report.refusal.false_refusal_rate == 1.0
    assert report.refusal.false_answer_rate == 1.0
    assert len(report.leaks) == 1 and report.leaks[0].startswith("b")


def test_soft_refusal_not_counted_as_leak(fake_judge):
    """refused=False 但话术上「未找到足够信息」：judge_leak 判未实质回答，不记漏拒。"""
    fake_judge["leak"]["问题a"] = False
    agent = FakeAgent({
        "问题a": {
            "refused": False,
            "answer": "知识库中未找到足够信息，无法回答。",
            "citations": [],
        },
    })
    report = AgentEvalRunner(agent, judge_client=object()).run([item("a", expect_refusal=True)])
    assert report.leaks == []  # judge_leak 口径：软拒答不算泄漏
    # RefusalEval 原始口径仍记 refused=False（漏拒率分母），两条口径并存
    assert report.refusal.false_answer_rate == 1.0
    assert report.per_item[0]["leak_answered"] is False


def test_warm_cache_round(fake_judge):
    agent = FakeAgent({
        "问题a": {"refused": False, "answer": "x", "citations": [], "cached": True},
        "问题b": {"refused": False, "answer": "y", "citations": [], "cached": False},
    })
    report = AgentEvalRunner(agent, judge_client=object()).run(
        [item("a"), item("b")], warm_cache=True
    )
    assert report.cache["hits"] == 1 and report.cache["total"] == 2
    assert report.cache["rate"] == 0.5
    # warm 轮不判分
    assert report.answer_total == 0 and report.answer_judged == 0


def test_agent_exception_recorded_as_failure(fake_judge):
    class BoomAgent(FakeAgent):
        def ask(self, question: str, role: str) -> dict:
            raise RuntimeError("boom")

    report = AgentEvalRunner(BoomAgent({}), judge_client=object()).run([item("a")])
    assert len(report.failures) == 1 and "boom" in report.failures[0]
    assert report.latency_mean == 0.0


def test_judge_exception_recorded_but_no_crash(fake_judge, monkeypatch):
    def broken_judge(client, question, answer, hint, model=None):
        raise RuntimeError("judge down")

    monkeypatch.setattr("kb_agent.eval.agent_runner.judge_answer", broken_judge)
    agent = FakeAgent({"问题a": {"refused": False, "answer": "x", "citations": []}})
    report = AgentEvalRunner(agent, judge_client=object()).run([item("a")])
    assert report.answer_judged == 0 and report.answer_accuracy == 0.0
    assert any("judge" in f for f in report.failures)
