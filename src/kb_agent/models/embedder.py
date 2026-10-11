"""BGE-M3 dense 向量封装（FlagEmbedding BGEM3FlagModel，CPU 推理）。

注意：BGE-M3 的 sparse 输出用 sentence-transformers 加载支持不完善，
必须用 FlagEmbedding 的 BGEM3FlagModel（本模块只取 dense；sparse 走手写 BM25）。
"""
from __future__ import annotations

import numpy as np
from FlagEmbedding import BGEM3FlagModel

from kb_agent.config import settings


class Embedder:
    def __init__(
        self, model_dir: str = str(settings.bge_m3_dir), threads: int = settings.embed_threads
    ) -> None:
        import torch

        torch.set_num_threads(threads)  # 不显式设置时线程竞争可慢 3 倍
        self.model = BGEM3FlagModel(model_dir, use_fp16=False, device="cpu")

    def encode(self, texts: list[str], batch_size: int = 8) -> np.ndarray:
        """批量编码，返回 (n, 1024) float32，已 L2 归一化。"""
        if not texts:
            return np.zeros((0, 1024), dtype=np.float32)
        out = self.model.encode(
            texts,
            batch_size=batch_size,
            max_length=512,
            return_dense=True,
            return_sparse=False,
            return_colbert_vecs=False,
        )
        dense = np.asarray(out["dense_vecs"], dtype=np.float32)
        norms = np.linalg.norm(dense, axis=1, keepdims=True)
        return dense / np.maximum(norms, 1e-9)

    def encode_query(self, text: str) -> np.ndarray:
        return self.encode([text])[0]
