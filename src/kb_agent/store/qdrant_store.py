"""Qdrant 存储层：双命名向量（dense + sparse）+ prefetch + FusionQuery RRF。

- upsert_chunks：写入 chunk（dense 向量 + BM25 sparse 向量 + payload）
- query_hybrid：dense/sparse 双路 prefetch 召回 top-N，RRF 融合
- 权限隔离：检索期 payload filter（role ∈ 请求角色 ∪ {"public"}）
- 支持按 doc_id 删除（删除文档）、重建（drop collection）
"""
from __future__ import annotations

import uuid

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    Fusion,
    FusionQuery,
    MatchAny,
    PayloadSchemaType,
    PointStruct,
    PointVectors,
    Prefetch,
    ScoredPoint,
    SparseVector,
    SparseVectorParams,
    VectorParams,
)

from kb_agent.config import settings

PUBLIC_ROLE = "public"

# 可见层级（向上兼容）：低层级文档对高层级角色可见，反之不可见
ROLE_LEVELS = {"public": 0, "contractor": 1, "employee": 2, "admin": 3}


def expand_roles(roles: list[str]) -> set[str]:
    """把请求角色展开为可匹配的文档角色集合（含 public 与未知角色字面匹配）。"""
    allowed = {PUBLIC_ROLE}
    for r in roles:
        lvl = ROLE_LEVELS.get(r)
        if lvl is not None:
            allowed |= {k for k, v in ROLE_LEVELS.items() if v <= lvl}
        else:
            allowed.add(r)  # 未知角色：按字面匹配，保守处理
    return allowed


class QdrantStore:
    def __init__(self, url: str = settings.qdrant_url, collection: str = settings.collection_name) -> None:
        self.client = QdrantClient(url=url)
        self.collection = collection

    # ---------- collection 管理 ----------

    def ensure_collection(self, dense_dim: int = 1024, recreate: bool = False) -> None:
        if self.client.collection_exists(self.collection):
            if recreate:
                self.client.delete_collection(self.collection)
            else:
                return
        self.client.create_collection(
            collection_name=self.collection,
            vectors_config={"dense": VectorParams(size=dense_dim, distance=Distance.COSINE)},
            sparse_vectors_config={"sparse": SparseVectorParams()},
        )
        # payload 索引：按 doc_id 删除、按 role 过滤都要走字段索引
        for field in ("doc_id", "role"):
            self.client.create_payload_index(
                collection_name=self.collection,
                field_name=field,
                field_schema=PayloadSchemaType.KEYWORD,
            )

    def delete_collection(self) -> None:
        if self.client.collection_exists(self.collection):
            self.client.delete_collection(self.collection)

    # ---------- 写入 / 删除 ----------

    def upsert_chunks(
        self,
        chunk_ids: list[str],
        texts: list[str],
        dense_vectors: list[list[float]],
        sparse_vectors: list[tuple[list[int], list[float]]],
        payloads: list[dict],
        batch_size: int = 64,
    ) -> None:
        points = [
            PointStruct(
                id=uuid.uuid5(uuid.NAMESPACE_URL, cid),  # 稳定 UUID，重复 upsert 幂等
                vector={
                    "dense": dense,
                    "sparse": SparseVector(indices=idx, values=vals),
                },
                payload=payload,
            )
            for cid, dense, (idx, vals), payload in zip(
                chunk_ids, dense_vectors, sparse_vectors, payloads, strict=True
            )
        ]
        for i in range(0, len(points), batch_size):
            self.client.upsert(
                collection_name=self.collection, points=points[i : i + batch_size]
            )

    def delete_document(self, doc_id: str) -> None:
        self.client.delete(
            collection_name=self.collection,
            points_selector=Filter(
                must=[FieldCondition(key="doc_id", match=MatchAny(any=[doc_id]))]
            ),
        )

    # ---------- 检索 ----------

    @staticmethod
    def _role_filter(roles: list[str]) -> Filter:
        """请求角色 → 可见层级展开：role ∈ expand_roles(roles)。

        权限模型按"最低可见层级"理解：admin 可见全部，employee 可见 employee
        及以下，以此类推。过滤在检索期执行（不是召回后过滤），无权限文档
        根本不进入候选集。
        """
        return Filter(
            must=[
                FieldCondition(key="role", match=MatchAny(any=sorted(expand_roles(roles))))
            ]
        )

    def _query(
        self,
        prefetch: list[Prefetch],
        roles: list[str],
        limit: int,
    ) -> list[ScoredPoint]:
        return self.client.query_points(
            collection_name=self.collection,
            prefetch=prefetch,
            query=FusionQuery(fusion=Fusion.RRF),
            query_filter=self._role_filter(roles),
            with_payload=True,
            limit=limit,
        ).points

    def query_hybrid(
        self,
        dense: list[float],
        sparse: tuple[list[int], list[float]],
        roles: list[str],
        limit: int = 50,
    ) -> list[ScoredPoint]:
        """dense + sparse 双路召回 top-limit，RRF 融合。"""
        return self._query(
            prefetch=[
                Prefetch(query=dense, using="dense", limit=limit),
                Prefetch(
                    query=SparseVector(indices=sparse[0], values=sparse[1]),
                    using="sparse",
                    limit=limit,
                ),
            ],
            roles=roles,
            limit=limit,
        )

    def query_dense_only(self, dense: list[float], roles: list[str], limit: int = 50) -> list[ScoredPoint]:
        """纯向量检索（消融组 1）。"""
        return self._query(prefetch=[Prefetch(query=dense, using="dense", limit=limit)], roles=roles, limit=limit)

    def query_sparse_only(
        self, sparse: tuple[list[int], list[float]], roles: list[str], limit: int = 50
    ) -> list[ScoredPoint]:
        """纯稀疏检索（消融组 2）。"""
        return self._query(
            prefetch=[
                Prefetch(
                    query=SparseVector(indices=sparse[0], values=sparse[1]),
                    using="sparse",
                    limit=limit,
                )
            ],
            roles=roles,
            limit=limit,
        )

    def get_document_chunks(self, doc_id: str, roles: list[str]) -> list[ScoredPoint]:
        """取某文档的全部 chunks（summarize_document 工具用），同样受层级过滤。"""
        hits, _ = self.client.scroll(
            collection_name=self.collection,
            scroll_filter=Filter(
                must=[
                    FieldCondition(key="doc_id", match=MatchAny(any=[doc_id])),
                    FieldCondition(
                        key="role", match=MatchAny(any=sorted(expand_roles(roles)))
                    ),
                ]
            ),
            with_payload=True,
            with_vectors=False,
            limit=500,
        )
        return hits

    def scroll_all(self, roles: list[str]) -> list[ScoredPoint]:
        """全量 chunks（rank_bm25 对照的语料源），同样受层级过滤。"""
        hits, _ = self.client.scroll(
            collection_name=self.collection,
            scroll_filter=Filter(
                must=[
                    FieldCondition(
                        key="role", match=MatchAny(any=sorted(expand_roles(roles)))
                    )
                ]
            ),
            with_payload=True,
            with_vectors=False,
            limit=500,
        )
        return hits

    def count(self) -> int:
        return self.client.count(collection_name=self.collection, exact=True).count

    # ---------- 增量索引辅助 ----------

    def all_point_texts(self) -> dict[str, str]:
        """滚动取出全部点的 (id, text)——BM25 词表全量重拟合用。"""
        out: dict[str, str] = {}
        offset = None
        while True:
            points, offset = self.client.scroll(
                collection_name=self.collection,
                with_payload=["text"],
                with_vectors=False,
                limit=256,
                offset=offset,
            )
            for p in points:
                text = (p.payload or {}).get("text")
                if isinstance(text, str):
                    out[str(p.id)] = text
            if offset is None:
                return out

    def update_sparse_vectors(
        self, pairs: list[tuple[str, tuple[list[int], list[float]]]]
    ) -> None:
        """仅更新存量点的 sparse 向量（BM25 词表变化后重算；dense 不受影响）。"""
        points = [
            PointVectors(
                id=pid,
                vector={
                    "sparse": SparseVector(indices=idx, values=vals),
                },
            )
            for pid, (idx, vals) in pairs
        ]
        for i in range(0, len(points), 256):
            self.client.update_vectors(
                collection_name=self.collection, points=points[i : i + 256]
            )
