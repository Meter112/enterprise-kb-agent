"""D30 失败归因分类单测（纯函数，不碰模型/索引）。"""
from kb_agent.eval.error_analysis import classify, classify_item, summary_table


def test_gold_missed() -> None:
    rec = {
        "expect_refusal": False, "refused": False, "judge_correct": False,
        "hit": False, "gold_chunk_ids": ["g1"], "pre_rerank_ids": ["x", "y"],
    }
    assert classify_item(rec) == "gold_missed"


def test_rerank_demoted() -> None:
    """gold 在 rerank 前候选里但没进最终 top-k → rerank 压下去。"""
    rec = {
        "expect_refusal": False, "refused": False, "judge_correct": False,
        "hit": False, "gold_chunk_ids": ["g1"], "pre_rerank_ids": ["x", "g1"],
    }
    assert classify_item(rec) == "rerank_demoted"


def test_chunk_truncated_heuristic() -> None:
    rec = {
        "expect_refusal": False, "refused": False, "judge_correct": False,
        "hit": False, "gold_chunk_ids": ["g1"], "pre_rerank_ids": ["x"],
    }
    assert classify_item(rec, gold_texts={"g1": "被切碎的片段"}) == "chunk_truncated"


def test_gen_error_when_hit_but_judged_wrong() -> None:
    rec = {
        "expect_refusal": False, "refused": False, "judge_correct": False,
        "hit": True, "gold_chunk_ids": ["g1"], "pre_rerank_ids": ["g1"],
    }
    assert classify_item(rec) == "gen_error"


def test_ambiguous_on_split_votes() -> None:
    rec = {
        "expect_refusal": False, "refused": False, "judge_correct": False,
        "hit": True, "gold_chunk_ids": ["g1"], "judge_votes": 2,
    }
    assert classify_item(rec) == "ambiguous"


def test_false_refusal() -> None:
    rec = {"expect_refusal": False, "refused": True, "judge_correct": None}
    assert classify_item(rec) == "false_refusal"


def test_leak() -> None:
    rec = {"expect_refusal": True, "refused": False, "leak_answered": True}
    assert classify_item(rec) == "leak"


def test_no_failure_returns_none() -> None:
    ok = {
        "expect_refusal": False, "refused": False, "judge_correct": True,
        "hit": True, "gold_chunk_ids": ["g1"],
    }
    assert classify_item(ok) is None


def test_classify_aggregates_and_table() -> None:
    per_item = [
        {"id": "a", "expect_refusal": False, "refused": True, "judge_correct": None},
        {"id": "b", "expect_refusal": False, "refused": False, "judge_correct": False,
         "hit": False, "gold_chunk_ids": ["g"], "pre_rerank_ids": []},
        {"id": "c", "expect_refusal": False, "refused": False, "judge_correct": True,
         "hit": True, "gold_chunk_ids": ["g"]},
    ]
    out = classify(per_item)
    assert [r["id"] for r in out["false_refusal"]] == ["a"]
    assert [r["id"] for r in out["gold_missed"]] == ["b"]
    table = summary_table(out)
    assert "误拒" in table and "a" in table and "gold 未召回" in table and "b" in table
    assert "c" not in table
