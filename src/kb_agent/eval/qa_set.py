"""QA 集数据模型与读写。

场景（计划 4 类）：
  - single_hop：单文档事实问答
  - multi_doc：多文档对比（gold 覆盖多个文档）
  - no_answer：语料外问题（expect_refusal=True，无 gold）
  - permission：权限受限问题（expect_refusal=True，gold 指向不可见文档，仅作报告用）

gold_chunk_ids 是 Qdrant point id（uuid5(chunk_id)），由 make_qa_set.py 从
实时索引按 (标题, 章节关键词) 解析并快照，保证 Hit@5/MRR 是精确 chunk 匹配。
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

CATEGORIES = {"single_hop", "multi_doc", "no_answer", "permission"}


@dataclass
class QAItem:
    id: str
    category: str
    question: str
    roles: list[str]
    answer_hint: str
    expect_refusal: bool
    gold_chunk_ids: list[str] = field(default_factory=list)


def load_qa_set(path: Path | str) -> list[QAItem]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return [QAItem(**item) for item in data["items"]]


def save_qa_set(items: list[QAItem], path: Path | str, meta: dict | None = None) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {"meta": meta or {}, "items": [asdict(i) for i in items]}
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
