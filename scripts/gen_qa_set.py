"""QA 集 v2 扩容生成（D30）：LLM 批量造题 + 确定性 gold 解析 + 校验。

流程：
1. 从实时索引构建语料清单（标题/可见层级/章节/片段样例）喂给 LLM
2. 按场景分 6 轮批量生成（每轮 8 条），LLM 输出 gold_targets [(标题, 关键词)]
3. gold_targets 走 gold_resolver 确定性解析成 point id 快照（不信任 LLM 的 id）
4. 校验：gold 非空 / 角色-文档可见性一致 / 问题去重，然后与迁移条目合并落盘

目标：每类 ≥15 条，总量 ≥120。语料 52 篇 4 大主题，对比/聚合题要求
LLM 使用不同文档对，避免 gold 高度重复。

用法: uv run python scripts/gen_qa_set.py            # 全量生成
      uv run python scripts/gen_qa_set.py --dry-run  # 只打印计划不写文件
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter

from kb_agent.agent.llm import LLMClient
from kb_agent.config import settings
from kb_agent.eval.gold_resolver import load_payload_index, resolve_gold
from kb_agent.eval.qa_set import QAItem, QATurn, load_qa_set, save_qa_set_by_type
from kb_agent.store.qdrant_store import QdrantStore, expand_roles

OUT_DIR = settings.data_dir / "qa_sets" / "v2"
BATCH = 8
# 迁移后每类已有数（qa_set_v1 映射），生成量 = 目标 - 已有
TARGET = {"single_hop": 24, "cross_doc": 20, "comparison": 20,
          "out_of_corpus": 24, "permission": 20, "multi_turn": 20}
MIN_PER_TYPE = 15

ANSWER_SCHEMA = """只输出 JSON 数组，每项：
{{"question": "问题", "role": "public|contractor|employee|admin",
  "difficulty": "easy|medium|hard",
  "answer_points": ["要点1", "要点2"], "gold_targets": [{{"title": "文档标题", "keywords": ["章节关键词"]}}]}}"""

REFUSAL_SCHEMA = """只输出 JSON 数组，每项：
{{"question": "问题", "role": "public|contractor|employee|admin",
  "difficulty": "easy|medium|hard", "answer_points": [],
  "gold_targets": [] 或 [{{"title": "文档标题", "keywords": ["章节关键词"]}}]}}"""

MULTITURN_SCHEMA = """只输出 JSON 数组，每项：
{{"question": "末轮问题", "role": "public|contractor|employee|admin",
  "difficulty": "easy|medium|hard",
  "answer_points": ["末轮要点"], "gold_targets": [{{"title": "文档标题", "keywords": ["章节关键词"]}}],
  "turns": [{{"question": "第1轮问题", "answer_points": []}}, {{"question": "第2轮问题(可指代前轮)", "answer_points": []}}]}}
turns 是除末轮外的历史轮次，末轮即顶层 question。第 2 轮起必须用「那/它/这个/上述」等
指代词引用前轮答案中的实体，考验多轮改写。每轮都必须是语料内可答的问题。"""

PROMPTS = {
    "single_hop": (
        "你是企业知识库 QA 集生成员。生成 {n} 条单文档事实问答（single_hop）："
        "问题针对语料清单中某个文档的某个章节，答案可从该章节直接读出（部分题目可要求"
        "按条件筛选，如「X 情况下标准是多少」）。角色 role 必须是能看见该文档的层级。\n"
        + ANSWER_SCHEMA
    ),
    "cross_doc": (
        "你是企业知识库 QA 集生成员。生成 {n} 条跨文档聚合问答（cross_doc）："
        "答案需要拼装 2-3 篇不同文档的信息（如「离职需要走哪些流程」涉及交接+结算+"
        "保密多篇），不是对比题。gold_targets 必须包含全部所需文档的章节。\n"
        "可见性规则：所有 gold 文档的可见层级都必须 ≤ role（admin 能看全部层级；"
        "employee 能看 public/contractor/employee；contractor 能看 public/contractor；"
        "public 只能看 public）。跨层级组合题直接选 admin 角色。\n"
        "不要与已存在问题语义重复，每题换不同的文档组合。\n"
        + ANSWER_SCHEMA
    ),
    "comparison": (
        "你是企业知识库 QA 集生成员。生成 {n} 条多文档对比问答（comparison）："
        "问题问两篇文档对同一主题的不同规定/流程/标准（「有什么不同」）。"
        "gold_targets 两侧文档都要给出。不同题目必须用不同的文档对。\n"
        "可见性规则：两侧文档的可见层级都必须 ≤ role（admin 能看全部；employee 能看"
        "public/contractor/employee；contractor 能看 public/contractor；public 只能看 public）。\n"
        + ANSWER_SCHEMA
    ),
    "out_of_corpus": (
        "你是企业知识库 QA 集生成员。生成 {n} 条语料外问题（out_of_corpus）："
        "问题看起来像公司员工会问的，但语料清单中没有任何文档能回答（相邻但缺失的"
        "主题，如福利/设施/活动类）。gold_targets 必须为空数组。\n"
        + REFUSAL_SCHEMA
    ),
    "permission": (
        "你是企业知识库 QA 集生成员。生成 {n} 条权限受限问题（permission）："
        "问题问的是某个文档的明确内容，但 role 层级看不见该文档（public 问 contractor/"
        "employee/admin 文档，contractor 问 employee/admin，employee 问 admin）。"
        "gold_targets 指向该不可见文档（仅作报告用）。\n"
        + REFUSAL_SCHEMA
    ),
    "multi_turn": (
        "你是企业知识库 QA 集生成员。生成 {n} 条多轮对话（multi_turn，共 3 轮）。"
        "末轮 gold 文档的可见层级必须 ≤ role（admin 能看全部；employee 能看 "
        "public/contractor/employee；contractor 能看 public/contractor；public 只能看 public）。"
        "不要与已存在问题语义重复，每组对话选不同主题。\n"
        + MULTITURN_SCHEMA
    ),
}


def parse_json_array(raw: str) -> list[dict]:
    m = re.search(r"\[.*\]", raw or "", re.S)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
        return [d for d in data if isinstance(d, dict)]
    except json.JSONDecodeError:
        return []


def build_inventory(points: dict[str, dict]) -> str:
    """语料清单：标题 + 可见层级 + 章节 + 2 个片段样例（供 LLM 选题）。"""
    docs: dict[str, dict] = {}
    for pl in points.values():
        title = pl.get("title", "")
        docs.setdefault(title, {"roles": Counter(), "sections": [], "samples": []})
        d = docs[title]
        d["roles"][pl.get("role", "")] += 1
        sec = " > ".join(pl.get("title_path") or [])
        if sec and sec not in d["sections"]:
            d["sections"].append(sec)
        if len(d["samples"]) < 2 and pl.get("text"):
            d["samples"].append(pl["text"][:200].replace("\n", " "))
    lines = []
    for title, d in sorted(docs.items()):
        role = d["roles"].most_common(1)[0][0]
        lines.append(
            f"《{title}》（可见层级 {role}）\n"
            f"  章节：{' / '.join(d['sections'][:8])}\n"
            f"  样例：{d['samples'][0] if d['samples'] else ''}"
        )
    return "\n".join(lines)


def validate_item(
    raw: dict, points: dict[str, dict], existing_qs: set[str]
) -> tuple[QAItem | None, str | None]:
    """单条校验：gold 非空 / 角色可见性一致 / 去重。返回 (item, error)。"""
    scenario = raw["_scenario"]
    question = str(raw.get("question", "")).strip()
    if not question or len(question) < 4:
        return None, "question 缺失"
    norm = re.sub(r"[\s，。？！、]", "", question)
    if norm in existing_qs:
        return None, f"问题重复: {question}"
    role = raw.get("role", "employee")
    if role not in ("public", "contractor", "employee", "admin"):
        return None, f"role 非法: {role}"

    gold_targets = [
        (t["title"], [str(k) for k in t.get("keywords", [])])
        for t in raw.get("gold_targets", [])
        if isinstance(t, dict) and t.get("title")
    ]
    expect_refusal = scenario in ("out_of_corpus", "permission")
    gold_ids, gw = resolve_gold(points, gold_targets)

    if not expect_refusal and not gold_ids:
        return None, f"应答题未解析到 gold: {gw or gold_targets}"

    # 可见性校验：应答题 role 必须能看到 gold 文档；permission 必须看不到
    doc_roles = {
        points[pid].get("role", "")
        for pid in gold_ids
        if pid in points and points[pid].get("role")
    }
    if expect_refusal and scenario == "permission":
        if not gold_ids:
            return None, "permission 题 gold 为空"
        if any(dr in expand_roles([role]) for dr in doc_roles):
            return None, f"permission 题 role={role} 却能看到 {doc_roles}"
    elif not expect_refusal and doc_roles:
        visible = expand_roles([role])
        if not doc_roles <= visible:
            return None, f"role={role} 看不到 gold 文档 {doc_roles}"

    if scenario == "out_of_corpus" and gold_ids:
        return None, f"语料外题却解析到 gold（{gold_targets}），需换题"

    points_list = [str(p) for p in raw.get("answer_points", [])][:5]
    if not expect_refusal and not points_list:
        return None, "应答题缺 answer_points"
    difficulty = raw.get("difficulty", "medium")
    if difficulty not in ("easy", "medium", "hard"):
        difficulty = "medium"

    turns = None
    if scenario == "multi_turn":
        raw_turns = [t for t in raw.get("turns", []) if isinstance(t, dict) and t.get("question")]
        if not raw_turns:
            return None, "multi_turn 缺历史轮次"
        turns = [QATurn(question=str(t["question"]).strip(), answer_points=[]) for t in raw_turns]
        for t in turns:
            if len(t.question) < 3:
                return None, "multi_turn 轮次问题过短"

    return QAItem(
        qa_id="",  # 落盘前统一编号
        question=question,
        role=role,
        gold_chunk_ids=gold_ids,
        answer_points=points_list,
        expect_refusal=expect_refusal,
        scenario_type=scenario,
        difficulty=difficulty,
        turns=turns,
    ), None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只打印计划不写文件")
    args = ap.parse_args()

    assert settings.deepseek_api_key, "生成需要 DEEPSEEK_API_KEY"
    llm = LLMClient(
        settings.deepseek_api_key, settings.deepseek_base_url, settings.deepseek_chat_model
    )
    points = load_payload_index(QdrantStore())
    inventory = build_inventory(points)
    n_docs = len({p.get("title") for p in points.values()})
    migrated = load_qa_set(OUT_DIR) if (OUT_DIR / "single_hop.jsonl").exists() else []
    existing_qs = {re.sub(r"[\s，。？！、]", "", i.question) for i in migrated}
    print(f"语料 {n_docs} 篇，已迁移 {len(migrated)} 条\n")

    generated: list[QAItem] = []
    warnings: list[str] = []
    for scenario, target in TARGET.items():
        have = sum(1 for i in migrated if i.scenario_type == scenario)
        need = max(0, target - have)
        print(f"== {scenario}: 已有 {have}，需生成 {need}")
        tried = 0
        while need > 0 and tried < 4:  # 校验失败重试最多 4 批
            tried += 1
            n = min(BATCH, need)
            try:
                reply = llm.chat(
                    [
                        {
                            "role": "user",
                            "content": "语料清单（标题/可见层级/章节/样例）：\n"
                            + inventory[:14000]
                            + "\n\n已存在问题（勿重复）：\n"
                            + "、".join(sorted(existing_qs))[:2000]
                            + "\n\n"
                            + PROMPTS[scenario].format(n=n),
                        }
                    ],
                    max_tokens=8000,
                )
                raw_items = parse_json_array(reply.content)
            except Exception as e:  # noqa: BLE001
                warnings.append(f"{scenario} 批次调用失败: {e}")
                break
            if not raw_items:
                warnings.append(f"{scenario} 批次输出不可解析，重试")
                continue
            ok = 0
            for raw in raw_items:
                raw["_scenario"] = scenario
                item, err = validate_item(raw, points, existing_qs)
                if err:
                    warnings.append(f"{scenario}: {err}")
                    continue
                generated.append(item)
                existing_qs.add(re.sub(r"[\s，。？！、]", "", item.question))
                ok += 1
            need -= ok
            print(f"  批次 {tried}: 产出 {ok}/{len(raw_items)}，剩余 {need}")
        if need > 0:
            warnings.append(f"{scenario} 未达目标（差 {need} 条）")

    # 统一编号：迁移条目保留原号，新条目从已有最大编号 +1 续编（跨轮次不冲突）
    max_id = max(
        (int(m.group(1)) for i in migrated if (m := re.match(r"qa-(\d+)$", i.qa_id))),
        default=100,
    )
    for item in generated:
        max_id += 1
        item.qa_id = f"qa-{max_id}"
    all_items = migrated + generated
    counts = Counter(i.scenario_type for i in all_items)
    total = len(all_items)
    print(f"\n[summary] 共 {total} 条：{dict(counts)}")
    if total < 120 or any(counts[t] < MIN_PER_TYPE for t in TARGET):
        print("[warn] 未达目标（总量 ≥120 且每类 ≥15），见上方 warnings")
    if args.dry_run:
        print("[dry-run] 不写文件")
        return
    save_qa_set_by_type(all_items, OUT_DIR)
    print(f"[done] → {OUT_DIR}")
    for w in warnings[:40]:
        print(f"[warn] {w}")


if __name__ == "__main__":
    main()
