"""手写 BM25 稀疏向量生成（jieba 分词）。

- fit()：在语料上统计 IDF，构建词表（token → 维度 id）
- encode()：BM25 权重（k1=1.5, b=0.75），L2 归一化后作为 Qdrant sparse 向量
- 词表/IDF 持久化到磁盘，重建索引时查询侧必须复用同一份映射

查询侧用 jieba 搜索引擎模式（cut_for_search，更细粒度），索引侧用精确模式（cut）。
"""
from __future__ import annotations

import json
import math
from collections import Counter
from pathlib import Path

import jieba

K1 = 1.5
B = 0.75


class BM25Encoder:
    def __init__(self) -> None:
        self.vocab: dict[str, int] = {}
        self.idf: list[float] = []
        self.avgdl: float = 0.0
        self.doc_count: int = 0

    # ---------- 构建 ----------

    def fit(self, texts: list[str]) -> None:
        tokenized = [list(jieba.cut(t)) for t in texts]
        df: Counter[str] = Counter()
        for toks in tokenized:
            df.update(set(toks))
        n = max(len(texts), 1)
        self.vocab = {w: i for i, w in enumerate(df)}
        self.idf = [math.log((n - c + 0.5) / (c + 0.5) + 1.0) for c in df.values()]
        self.avgdl = sum(len(t) for t in tokenized) / n
        self.doc_count = n

    # ---------- 编码 ----------

    def encode(self, text: str, for_query: bool = False) -> tuple[list[int], list[float]]:
        """返回 (indices, values)。for_query=True 用搜索引擎模式分词。"""
        toks = list(jieba.cut_for_search(text)) if for_query else list(jieba.cut(text))
        if not toks:
            return [], []
        tf = Counter(toks)
        dl = len(toks)
        denom_base = K1 * (1 - B + B * dl / max(self.avgdl, 1e-9))
        indices: list[int] = []
        weights: list[float] = []
        for w, f in tf.items():
            wid = self.vocab.get(w)
            if wid is None:
                continue  # 词表外 token 丢弃（查询侧与索引侧一致）
            score = self.idf[wid] * (f * (K1 + 1)) / (f + denom_base)
            indices.append(wid)
            weights.append(score)
        # L2 归一化（与 Qdrant 稀疏向量的 Cosine 距离语义一致）
        norm = math.sqrt(sum(s * s for s in weights)) or 1.0
        return indices, [s / norm for s in weights]

    def encode_many(
        self, texts: list[str], for_query: bool = False
    ) -> list[tuple[list[int], list[float]]]:
        return [self.encode(t, for_query=for_query) for t in texts]

    # ---------- 持久化（查询侧必须与索引侧共用同一份） ----------

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "vocab": self.vocab,
                    "idf": self.idf,
                    "avgdl": self.avgdl,
                    "doc_count": self.doc_count,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    @classmethod
    def load(cls, path: Path) -> BM25Encoder:
        enc = cls()
        data = json.loads(path.read_text(encoding="utf-8"))
        enc.vocab = data["vocab"]
        enc.idf = data["idf"]
        enc.avgdl = data["avgdl"]
        enc.doc_count = data["doc_count"]
        return enc


class RankBM25Baseline:
    """rank_bm25 库版对照（可选依赖，仅 ablation 用）。

    全量 chunk 文本内存索引 + 库版打分，用于验证手写 BM25 与库版的一致性。
    已知公式差异：库版 idf 为 log((N-df+0.5)/(df+0.5))（无 +1 smoothing），
    手写版为 log((N-df+0.5)/(df+0.5)+1)——对照的意义正是量化该差异对指标的影响。
    库版打分尺度与 rerank 分不同，拒答阈值不适用于对照组（只报 Hit/MRR）。
    """

    def __init__(self, texts: list[str]) -> None:
        try:
            from rank_bm25 import BM25Okapi
        except ImportError as e:
            raise ImportError(
                "rank_bm25 对照需要可选依赖：uv sync --extra bm25-baseline（或 pip install rank-bm25）"
            ) from e
        self.texts = texts
        self._bm25 = BM25Okapi(
            [list(jieba.cut_for_search(t)) for t in texts]
        )

    def search(self, query: str, top_k: int = 5) -> list[tuple[int, float]]:
        """返回 (texts 下标, score)，按分降序。空查询返回空列表。"""
        toks = list(jieba.cut_for_search(query))
        if not toks:
            return []
        scores = self._bm25.get_scores(toks)
        ranked = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
        return [(i, float(scores[i])) for i in ranked[:top_k]]
