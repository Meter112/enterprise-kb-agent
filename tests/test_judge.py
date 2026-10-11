"""LLM judge 输出解析单测（不调用 API）。"""
from kb_agent.eval.judge import parse_judge_output


def test_parse_plain_json() -> None:
    relevant, reason = parse_judge_output('{"relevant": true, "reason": "包含年假天数表"}')
    assert relevant and "年假" in reason


def test_parse_fenced_json() -> None:
    raw = "```json\n{\"relevant\": false, \"reason\": \"内容无关\"}\n```"
    relevant, reason = parse_judge_output(raw)
    assert not relevant and "无关" in reason


def test_parse_with_prefix_suffix() -> None:
    raw = "判断结果如下：{\"relevant\": true, \"reason\": \"可以回答\"}，完毕。"
    relevant, _ = parse_judge_output(raw)
    assert relevant


def test_parse_garbage() -> None:
    relevant, reason = parse_judge_output("不是 JSON")
    assert not relevant and reason == "unparseable"


def test_parse_empty() -> None:
    relevant, reason = parse_judge_output("")
    assert not relevant and reason == "unparseable"


def test_parse_answered_field() -> None:
    """judge_leak 输出 answered 字段同样可解析。"""
    answered, reason = parse_judge_output('{"answered": true, "reason": "给出了实质流程"}')
    assert answered and "实质" in reason
