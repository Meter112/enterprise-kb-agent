"""冒烟 1：Qdrant 双命名向量（dense+sparse）+ prefetch + FusionQuery RRF。

验证要点（对应原方案的版本顾虑）：
- 当前 Qdrant server / qdrant-client 是否支持 named sparse vectors
- prefetch 多路召回 + FusionQuery(RRF) 融合是否可用
用法: uv run python scripts/smoke_qdrant.py
"""
import numpy as np
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    Fusion,
    FusionQuery,
    PointStruct,
    Prefetch,
    SparseVector,
    SparseVectorParams,
    VectorParams,
)

COLLECTION = "smoke_hybrid"


def main() -> None:
    client = QdrantClient(url="http://localhost:6333")
    info = client.info()
    print(f"[qdrant] version={info.version}")

    if client.collection_exists(COLLECTION):
        client.delete_collection(COLLECTION)
    client.create_collection(
        collection_name=COLLECTION,
        vectors_config={"dense": VectorParams(size=8, distance=Distance.COSINE)},
        sparse_vectors_config={"sparse": SparseVectorParams()},
    )

    # 8 个点：dense 按 i%4 分成 4 个主题簇；sparse 模拟 BM25 词频
    rng = np.random.default_rng(42)
    points = []
    for i in range(8):
        dense = rng.standard_normal(8)
        dense[i % 4] += 3.0
        dense = dense / np.linalg.norm(dense)
        sparse = SparseVector(indices=[i % 4, 4 + i % 2], values=[1.0, 0.5])
        points.append(
            PointStruct(
                id=i,
                vector={"dense": dense.tolist(), "sparse": sparse},
                payload={"text": f"文档片段 {i}", "theme": i % 4},
            )
        )
    client.upsert(collection_name=COLLECTION, points=points)

    # 查询：dense 偏向主题 0，sparse 命中词 0
    q_dense = rng.standard_normal(8)
    q_dense[0] += 3.0
    q_dense = q_dense / np.linalg.norm(q_dense)
    q_sparse = SparseVector(indices=[0], values=[1.0])

    result = client.query_points(
        collection_name=COLLECTION,
        prefetch=[
            Prefetch(query=q_dense.tolist(), using="dense", limit=5),
            Prefetch(query=q_sparse, using="sparse", limit=5),
        ],
        query=FusionQuery(fusion=Fusion.RRF),
        with_payload=True,
        limit=3,
    )
    top = [(p.id, round(p.score, 3), p.payload["text"]) for p in result.points]
    print(f"[hybrid] top-3 (id, score, text): {top}")

    assert len(result.points) == 3, "融合结果应返回 3 条"
    assert result.points[0].id in (0, 4), "第一位应是 dense 主题 0 且 sparse 命中词 0 的点"
    client.delete_collection(COLLECTION)
    print("[OK] smoke_qdrant: 双向量 + prefetch + FusionQuery RRF 跑通")


if __name__ == "__main__":
    main()
