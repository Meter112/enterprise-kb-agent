"""评估指标实现（计划 5 指标）。

- Hit@5 / MRR：gold chunk 精确匹配（gold 为 Qdrant point id 集合）
- 拒答准确率：双面统计（应拒侧 / 应答侧分开，避免混在一起掩盖单侧崩坏）
- 分阶段延迟：embed / recall / rerank 三段计时（HybridRetriever 内埋点）
- 引用正确率：程序化校验（[n] 越界/空候选引用），语义支撑由 judge 抽查
"""
from __future__ import annotations

from dataclasses import dataclass

from kb_agent.retrieval.citations import CITATION_RE, validate_citations
from kb_agent.retrieval.hybrid_retriever import RetrievedChunk


def hit_at_k(chunks: list[RetrievedChunk], gold_ids: set[str], k: int = 5) -> bool:
    return any(c.chunk_id in gold_ids for c in chunks[:k])


def reciprocal_rank(chunks: list[RetrievedChunk], gold_ids: set[str]) -> float:
    """第一个 gold 命中位置的倒数，未命中为 0。"""
    for i, c in enumerate(chunks, 1):
        if c.chunk_id in gold_ids:
            return 1.0 / i
    return 0.0


@dataclass
class RefusalEval:
    """拒答准确率（双面）。"""

    refuse_correct: int = 0
    refuse_total: int = 0
    answer_correct: int = 0
    answer_total: int = 0

    @property
    def accuracy(self) -> float:
        total = self.refuse_total + self.answer_total
        return (self.refuse_correct + self.answer_correct) / total if total else 0.0

    @property
    def false_answer_rate(self) -> float:
        """应拒却答（漏拒）：越界信息风险，最危险的一侧。"""
        return (self.refuse_total - self.refuse_correct) / self.refuse_total if self.refuse_total else 0.0

    @property
    def false_refusal_rate(self) -> float:
        """应答却拒（误拒）：阈值标定中调高阈值的代价侧。"""
        return (self.answer_total - self.answer_correct) / self.answer_total if self.answer_total else 0.0

    def record(self, expect_refusal: bool, refused: bool) -> None:
        if expect_refusal:
            self.refuse_total += 1
            self.refuse_correct += int(refused)
        else:
            self.answer_total += 1
            self.answer_correct += int(not refused)


def citation_correctness(answer: str, chunks: list[RetrievedChunk]) -> float:
    """程序化引用正确率 = 1 - 非法引用数/引用总数；无引用视为 1.0。"""
    refs = len(list(CITATION_RE.finditer(answer)))
    if refs == 0:
        return 1.0
    problems = validate_citations(answer, chunks)
    return 1.0 - len(problems) / refs


def mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0
