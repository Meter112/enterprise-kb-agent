"""bge-reranker-base 重排封装（FlagEmbedding FlagReranker，CPU 推理）。

cross-encoder 对 (query, doc) 全交互打分，比 bi-encoder 单向量余弦更准；
normalize=True 输出 0-1 分数，供拒答阈值直接使用。
"""
from __future__ import annotations

from FlagEmbedding import FlagReranker

from kb_agent.config import settings


class Reranker:
    def __init__(
        self, model_dir: str = str(settings.reranker_dir), use_fp16: bool = False
    ) -> None:
        self.model = FlagReranker(model_dir, use_fp16=use_fp16, device="cpu")

    def rerank(self, query: str, texts: list[str], batch_size: int = 16) -> list[float]:
        """对 (query, text) 对打分，返回与 texts 对齐的 0-1 分数列表。"""
        if not texts:
            return []
        scores = self.model.compute_score(
            [[query, t] for t in texts], batch_size=batch_size, normalize=True
        )
        if isinstance(scores, float):  # 单条输入时 compute_score 返回标量
            return [scores]
        return [float(s) for s in scores]
