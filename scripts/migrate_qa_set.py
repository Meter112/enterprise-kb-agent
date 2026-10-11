"""QA 集 v1 → v2 迁移（D30 扩容第一步）。

- 场景映射：single_hop 不变；multi_doc 按语义拆分（对比题 → comparison，
  聚合题 → cross_doc）；no_answer → out_of_corpus；permission 不变
- answer_hint 自由文本 → answer_points 要点列表：deepseek-chat 对照 gold chunk
  原文扩充（2-5 条可核查要点），并顺带标注 difficulty
- roles → role（v1 每条只有一个角色）

用法: uv run python scripts/migrate_qa_set.py
输出: data/qa_sets/v2/*.jsonl（6 类文件，先写迁移的 32 条）
"""
from __future__ import annotations

import json
import re
import sys

from kb_agent.agent.llm import LLMClient
from kb_agent.config import settings
from kb_agent.eval.gold_resolver import load_payload_index
from kb_agent.eval.qa_set import QAItem, save_qa_set_by_type
from kb_agent.store.qdrant_store import QdrantStore

V1 = settings.data_dir / "qa_sets" / "qa_set_v1.json"
OUT_DIR = settings.data_dir / "qa_sets" / "v2"

# v1 multi_doc 拆分：对比题（"有什么不同/区别"）→ comparison，聚合题 → cross_doc
MULTI_DOC_SPLIT = {
    "qa-017": "comparison",  # 年假 vs 病假申请流程
    "qa-018": "comparison",  # 内部 vs 公开 API 鉴权
    "qa-019": "comparison",  # 行为准则 vs 驻场规范
    "qa-020": "cross_doc",   # 备份/应急演练频率（聚合两文档，非对比）
    "qa-021": "comparison",  # 正式 vs 外包工时
}

ENRICH_PROMPT = """你是企业知识库 QA 集标注员。给定一个问题、原始参考提示和支撑回答的文档片段，输出：

1. answer_points：答案必须覆盖的要点列表（3-5 条，每条一句话、可独立核查、不引用片段编号）
2. difficulty：easy（单文档单点事实）/ medium（单文档多要点或简单筛选）/ hard（多文档聚合、对比或需要推理）

只输出 JSON：{{"answer_points": ["..."], "difficulty": "easy|medium|hard"}}

问题：{question}
原始参考：{hint}
文档片段：
{context}"""


def parse_json(raw: str) -> dict | None:
    m = re.search(r"\{.*\}", raw or "", re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def main() -> None:
    data = json.loads(V1.read_text(encoding="utf-8"))
    v1_items = data["items"]
    points = load_payload_index(QdrantStore())
    llm = LLMClient(
        settings.deepseek_api_key, settings.deepseek_base_url, settings.deepseek_chat_model
    )

    items: list[QAItem] = []
    warnings: list[str] = []
    for i, raw in enumerate(v1_items, 1):
        if raw["category"] == "multi_doc":
            scenario = MULTI_DOC_SPLIT.get(raw["id"], "comparison")
        else:
            scenario = {
                "single_hop": "single_hop",
                "no_answer": "out_of_corpus",
                "permission": "permission",
            }[raw["category"]]

        # gold 片段原文（应拒题 permission 的 gold 指向不可见文档，仅扩充要点用）
        ctx = "\n".join(
            f"- {points[pid].get('text', '')[:400]}" for pid in raw["gold_chunk_ids"][:4] if pid in points
        ) or "（无 gold 片段：语料外问题）"

        answer_points: list[str] = []
        difficulty = "medium"
        try:
            reply = llm.chat(
                [
                    {
                        "role": "user",
                        "content": ENRICH_PROMPT.format(
                            question=raw["question"], hint=raw["answer_hint"], context=ctx
                        ),
                    }
                ],
                max_tokens=2000,
            )
            parsed = parse_json(reply.content)
            if parsed and isinstance(parsed.get("answer_points"), list):
                answer_points = [str(p) for p in parsed["answer_points"]][:5]
                difficulty = parsed.get("difficulty", "medium")
            else:
                warnings.append(f"{raw['id']}: LLM 输出不可解析，answer_points 降级为原 hint")
        except Exception as e:  # noqa: BLE001 - 单条失败降级不中断
            warnings.append(f"{raw['id']}: 扩充失败 {e}，降级为原 hint")
        if not answer_points:
            answer_points = [raw["answer_hint"]]

        items.append(
            QAItem(
                qa_id=raw["id"],
                question=raw["question"],
                role=raw["roles"][0],
                gold_chunk_ids=raw["gold_chunk_ids"],
                answer_points=answer_points,
                expect_refusal=raw["expect_refusal"],
                scenario_type=scenario,
                difficulty=difficulty,
            )
        )
        print(f"[{i}/{len(v1_items)}] {raw['id']} → {scenario} ({difficulty}) "
              f"{len(answer_points)} 要点", flush=True)

    counts = save_qa_set_by_type(items, OUT_DIR)
    print(f"\n[done] 迁移 {len(items)} 条 → {OUT_DIR}")
    print(f"[counts] {counts}")
    for w in warnings:
        print(f"[warn] {w}")
    if warnings:
        sys.exit(0)  # 降级警告不视为失败，但要人工过一眼


if __name__ == "__main__":
    main()
