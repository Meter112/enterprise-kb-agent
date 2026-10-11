"""QA 集数据模型与读写（v2，D30 扩容）。

场景 6 类（每类一个 jsonl 文件，目录默认 data/qa_sets/v2/）：
  - single_hop：单文档事实问答
  - cross_doc：跨文档信息聚合（多篇拼装回答，非对比）
  - comparison：多文档对比（A vs B 异同）
  - out_of_corpus：语料外问题（expect_refusal=True，无 gold）
  - permission：权限受限（expect_refusal=True，gold 指向不可见文档，仅作报告用）
  - multi_turn：多轮对话（turns 记每轮问题与要点，末轮判分）

v2 字段：qa_id / question / role / gold_chunk_ids / answer_points / expect_refusal /
scenario_type / difficulty。answer_points 是答案必须覆盖的要点列表（v1 的
answer_hint 自由文本升级为要点列表，judge 按要点判分，判分口径更细）。

gold_chunk_ids 是 Qdrant point id（uuid5(chunk_id)），由 gold_resolver.py 从实时
索引按 (标题, 章节关键词) 解析并快照，保证 Hit@5/MRR 是精确 chunk 匹配。

v1 JSON（qa_set_v1.json）由 scripts/migrate_qa_set.py 迁移，load_qa_set 只读 v2。
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

SCENARIO_TYPES = {
    "single_hop",
    "cross_doc",
    "comparison",
    "out_of_corpus",
    "permission",
    "multi_turn",
}
REFUSAL_TYPES = {"out_of_corpus", "permission"}
DIFFICULTIES = {"easy", "medium", "hard"}


@dataclass
class QATurn:
    """multi_turn 单轮：question + 该轮要点（仅末轮参与 judge 判分）。"""

    question: str
    answer_points: list[str] = field(default_factory=list)


@dataclass
class QAItem:
    qa_id: str
    question: str
    role: str
    gold_chunk_ids: list[str]
    answer_points: list[str]
    expect_refusal: bool
    scenario_type: str
    difficulty: str = "medium"
    turns: list[QATurn] | None = None  # 仅 multi_turn：全轮次（末轮即 question/answer_points）

    def __post_init__(self) -> None:
        if self.scenario_type not in SCENARIO_TYPES:
            raise ValueError(f"未知场景: {self.scenario_type}（可选 {sorted(SCENARIO_TYPES)}）")
        if self.difficulty not in DIFFICULTIES:
            raise ValueError(f"未知难度: {self.difficulty}（可选 {sorted(DIFFICULTIES)}）")
        if self.scenario_type in REFUSAL_TYPES and not self.expect_refusal:
            raise ValueError(f"{self.scenario_type} 必须是应拒题（expect_refusal=True）")
        if self.expect_refusal != (self.scenario_type in REFUSAL_TYPES):
            raise ValueError(
                f"{self.scenario_type} 与 expect_refusal={self.expect_refusal} 不一致"
            )


def _item_to_dict(item: QAItem) -> dict:
    return asdict(item)


def load_qa_set(path: Path | str) -> list[QAItem]:
    """加载 v2 QA 集：目录（合并 *.jsonl）或单个 .jsonl 文件。"""
    p = Path(path)
    files = sorted(p.glob("*.jsonl")) if p.is_dir() else [p]
    items: list[QAItem] = []
    for f in files:
        for line in f.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            data = json.loads(line)
            turns = (
                [QATurn(**t) for t in data["turns"]] if data.get("turns") else None
            )
            items.append(QAItem(**{k: v for k, v in data.items() if k != "turns"}, turns=turns))
    return items


def save_qa_set_by_type(items: list[QAItem], out_dir: Path | str, meta: dict | None = None) -> dict[str, int]:
    """按 scenario_type 分文件写 jsonl，返回 {type: 条数}。"""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}
    for scenario in sorted(SCENARIO_TYPES):
        lines = [_item_to_dict(i) for i in items if i.scenario_type == scenario]
        counts[scenario] = len(lines)
        (out / f"{scenario}.jsonl").write_text(
            "\n".join(json.dumps(d, ensure_ascii=False) for d in lines) + ("\n" if lines else ""),
            encoding="utf-8",
        )
    if meta:
        (out / "_meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return counts
