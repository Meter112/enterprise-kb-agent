"""HybridRetriever 编排逻辑单测（fake store/模型，不加载真实权重）。"""
import numpy as np

from kb_agent.retrieval.hybrid_retriever import HybridRetriever


class _Hit:
    def __init__(self, id: str, title: str, score: float, role: str = "public") -> None:
        self.id = id
        self.score = score
        self.payload = {
            "doc_id": id,
            "title": title,
            "text": f"{title} 正文",
            "title_path": [title],
            "page": None,
            "section": "1.1",
            "role": role,
            "is_code": False,
        }


class _FakeStore:
    def __init__(self) -> None:
        self.calls: list[tuple[list[str], int]] = []

    def query_hybrid(self, dense, sparse, roles, limit=50):  # noqa: ANN001
        self.calls.append((list(roles), limit))
        return [_Hit("a", "文档A", 0.9), _Hit("b", "文档B", 0.8), _Hit("c", "文档C", 0.7)]


class _FakeEmbedder:
    def encode_query(self, text: str) -> np.ndarray:  # noqa: ANN001
        return np.zeros(1024, dtype=np.float32)


class _FakeBM25:
    def encode(self, text, for_query=False):  # noqa: ANN001
        return ([1, 2], [0.5, 0.5])


class _FakeReranker:
    def rerank(self, query, texts, batch_size=16):  # noqa: ANN001
        # 与召回顺序相反的分：验证"按 rerank 分重排"确实生效
        return [0.1, 0.3, 0.2]


def _make() -> tuple[HybridRetriever, _FakeStore]:
    store = _FakeStore()
    retriever = HybridRetriever(store, _FakeEmbedder(), _FakeBM25(), _FakeReranker())
    return retriever, store


def test_retrieve_reranks_and_truncates() -> None:
    retriever, store = _make()
    chunks = retriever.retrieve("测试", roles=["admin"], top_k=2)
    assert [c.title for c in chunks] == ["文档B", "文档C"]  # 0.3 > 0.2 > 0.1 后截断
    assert chunks[0].rerank_score == 0.3
    assert chunks[0].rrf_score == 0.8  # RRF 分保留


def test_retrieve_passes_roles_and_limit_to_store() -> None:
    retriever, store = _make()
    retriever.retrieve("测试", roles=["contractor"], top_k=1)
    roles, limit = store.calls[0]
    assert roles == ["contractor"] and limit == 20  # 召回用 top_n，截断在重排后


def test_retrieve_without_rerank_keeps_rrf_order() -> None:
    retriever, _ = _make()
    chunks = retriever.retrieve("测试", roles=["public"], top_k=3, with_rerank=False)
    assert [c.title for c in chunks] == ["文档A", "文档B", "文档C"]
    assert all(c.rerank_score is None for c in chunks)


def test_retrieve_maps_payload_fields() -> None:
    retriever, _ = _make()
    chunk = retriever.retrieve("测试", roles=["public"], top_k=1)[0]
    assert chunk.doc_id == "b" and chunk.section == "1.1" and chunk.role == "public"
