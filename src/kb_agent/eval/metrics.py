"""评估指标实现（计划 5 指标）。

- Hit@5 / MRR：gold chunk 精确匹配（gold 为 Qdrant point id 集合）
- 拒答准确率：双面统计（应拒侧 / 应答侧分开，避免混在一起掩盖单侧崩坏）
- 分阶段延迟：embed / recall / rerank 三段计时（HybridRetriever 内埋点）
- 引用正确率：程序化校验（[n] 越界/空候选引用），语义支撑由 judge 抽查
- Wilson 置信区间：小样本（每类 15-20 条）指标的比例区间估计
"""
from __future__ import annotations

from dataclasses import dataclass

from kb_agent.retrieval.citations import CITATION_RE, validate_citations
from kb_agent.retrieval.hybrid_retriever import RetrievedChunk


def wilson_interval(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson 比例置信区间（z=1.96 为 95%）。

    小样本下比正态近似稳健（p̂=0 或 1 时仍给出合理区间），用于按场景分组的
    正确率/Hit@5 披露。
    """
    if n == 0:
        return 0.0, 1.0
    p_hat = successes / n
    denom = 1 + z * z / n
    center = (p_hat + z * z / (2 * n)) / denom
    half = z * ((p_hat * (1 - p_hat) + z * z / (4 * n)) / n) ** 0.5 / denom
    return max(0.0, center - half), min(1.0, center + half)


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


def cohens_kappa(a: list[bool], b: list[bool]) -> float:
    """二分类 Cohen's kappa：两个标注序列（如 LLM judge vs 人工）的一致性。

    0 = 纯碰运气，1 = 完全一致。样本 <2 或无变异性时返回 0.0。
    """
    n = len(a)
    if n != len(b) or n < 2:
        return 0.0
    po = sum(1 for x, y in zip(a, b, strict=True) if x == y) / n
    p_a1 = sum(a) / n
    p_b1 = sum(b) / n
    pe = p_a1 * p_b1 + (1 - p_a1) * (1 - p_b1)
    if pe == 1.0:
        return 0.0
    return (po - pe) / (1 - pe)
