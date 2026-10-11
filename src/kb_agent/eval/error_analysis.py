"""失败归因分类（D30）：把评估失败的条目归类到可行动的根因。

分类口径（优先级从上到下，一条失败只归一类）：
- gold_missed    gold 未召回：gold chunk 不在 rerank 前的候选集合里（召回侧问题）
- rerank_demoted 召回但 rerank 压下去：gold 在 rerank 前候选里，但没进最终 top-k
- gen_error      检索命中但生成错误：gold 在最终 top-k，judge 仍判错（生成/提示词侧）
- false_refusal  误拒：应答题被判拒答
- leak           漏拒：应拒题给了实质回答（权限风险，最危险）
- chunk_truncated chunk 切断疑似：gold 文本过短（<100 字），事实被切碎跨 chunk
- ambiguous      问题歧义/判分噪音：judge 多数表决 2-1，或命中与判分互相矛盾

输入是 runner per_item 记录（需含 pre_rerank_ids/gold_chunk_ids 等字段），
gold_texts 可选（{chunk_id: text}，用于 chunk 切断启发式）。纯函数、可单测。
"""
from __future__ import annotations

CATEGORY_LABELS = {
    "gold_missed": "gold 未召回",
    "rerank_demoted": "召回但 rerank 压下去",
    "gen_error": "检索命中但生成错误",
    "false_refusal": "误拒（应答却拒）",
    "leak": "漏拒（应拒却实质回答）",
    "chunk_truncated": "chunk 切断疑似",
    "ambiguous": "问题歧义/判分噪音",
}

# 检索层失败类别（无生成/拒答语义）
RETRIEVAL_CATEGORIES = ("gold_missed", "rerank_demoted", "chunk_truncated", "ambiguous")


def classify_item(rec: dict, gold_texts: dict[str, str] | None = None) -> str | None:
    """单条归因，无失败返回 None。

    rec 期望字段：expect_refusal / refused / hit / judge_correct（可 None）/
    gold_chunk_ids / pre_rerank_ids（可缺省）/ judge_votes（可缺省）
    """
    expect_refusal = bool(rec.get("expect_refusal"))

    if expect_refusal:
        if rec.get("leak_answered") or (not rec.get("refused") and rec.get("judge_correct") is False):
            return "leak"
        return None

    if rec.get("refused"):
        return "false_refusal"

    judge = rec.get("judge_correct")
    if judge is not False:
        return None  # 判对或未判分不算失败

    gold = set(rec.get("gold_chunk_ids") or [])
    if not gold:
        return None  # 判定失败但无 gold，无法进一步归因

    if rec.get("hit") is not True:
        pre = set(rec.get("pre_rerank_ids") or [])
        if gold & pre:
            return "rerank_demoted"
        if gold_texts and any(len(gold_texts.get(g, "")) < 100 for g in gold):
            return "chunk_truncated"
        return "gold_missed"

    # 检索命中但答案判错：生成侧；三票 2-1 记判分噪音
    if rec.get("judge_votes") == 2:
        return "ambiguous"
    return "gen_error"


def classify(per_item: list[dict], gold_texts: dict[str, str] | None = None) -> dict[str, list[dict]]:
    """按类别聚合失败条目，返回 {category: [rec, ...]}（rec 附带 category 字段）。"""
    out: dict[str, list[dict]] = {c: [] for c in CATEGORY_LABELS}
    for rec in per_item:
        cat = classify_item(rec, gold_texts)
        if cat:
            rec = dict(rec)
            rec["category"] = cat
            out[cat].append(rec)
    return out


def summary_table(classified: dict[str, list[dict]], indent: str = "  ") -> str:
    """Markdown 汇总表：类别 / 数量 / 样例条目。"""
    rows = []
    for cat, label in CATEGORY_LABELS.items():
        items = classified.get(cat, [])
        if not items:
            continue
        sample = "、".join(str(r.get("id") or r.get("qa_id")) for r in items[:5])
        if len(items) > 5:
            sample += f" 等 {len(items)} 条"
        rows.append(f"{indent}| {label} | {len(items)} | {sample} |")
    if not rows:
        return f"{indent}（无失败条目）"
    header = f"{indent}| 根因 | 数量 | 条目 |\n{indent}|---|---|---|"
    return header + "\n" + "\n".join(rows)
