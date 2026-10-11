"""D30 QA 集 v2 读写与校验单测。"""
import json

import pytest

from kb_agent.eval.qa_set import (
    QAItem,
    QATurn,
    load_qa_set,
    save_qa_set_by_type,
)


def _item(**kw) -> QAItem:
    base = dict(
        qa_id="qa-1", question="问题", role="employee", gold_chunk_ids=["g1"],
        answer_points=["要点"], expect_refusal=False, scenario_type="single_hop",
    )
    base.update(kw)
    return QAItem(**base)


def test_scenario_refusal_consistency_enforced() -> None:
    with pytest.raises(ValueError, match="不一致"):
        _item(expect_refusal=True)  # single_hop 不允许应拒
    with pytest.raises(ValueError, match="expect_refusal=True"):
        _item(scenario_type="permission", expect_refusal=False)
    with pytest.raises(ValueError, match="未知场景"):
        _item(scenario_type="nope")


def test_roundtrip_jsonl_by_type(tmp_path) -> None:
    items = [
        _item(qa_id="qa-1"),
        _item(qa_id="qa-2", scenario_type="permission", expect_refusal=True,
              gold_chunk_ids=["g2"]),
        _item(qa_id="qa-3", scenario_type="multi_turn",
              turns=[QATurn(question="前一轮", answer_points=[])]),
    ]
    counts = save_qa_set_by_type(items, tmp_path)
    assert counts["single_hop"] == 1 and counts["permission"] == 1 and counts["multi_turn"] == 1
    assert counts["comparison"] == 0 and counts["cross_doc"] == 0 and counts["out_of_corpus"] == 0
    loaded = load_qa_set(tmp_path)
    by_id = {i.qa_id: i for i in loaded}
    assert by_id["qa-1"].role == "employee"
    assert by_id["qa-2"].expect_refusal and by_id["qa-2"].scenario_type == "permission"
    mt = by_id["qa-3"]
    assert mt.turns == [QATurn(question="前一轮", answer_points=[])]


def test_load_single_jsonl_file(tmp_path) -> None:
    f = tmp_path / "single_hop.jsonl"
    f.write_text(
        json.dumps(
            {
                "qa_id": "qa-9", "question": "q", "role": "public",
                "gold_chunk_ids": [], "answer_points": ["p"],
                "expect_refusal": False, "scenario_type": "single_hop",
                "difficulty": "hard",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    items = load_qa_set(f)
    assert len(items) == 1 and items[0].qa_id == "qa-9" and items[0].difficulty == "hard"
