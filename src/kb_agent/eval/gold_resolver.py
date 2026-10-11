"""gold chunk 解析（D30 从 make_qa_set.py 抽出，供迁移/生成脚本共用）。

按 (文档标题, 章节关键词) 在索引 payload 中匹配 chunk，取全部命中的 point id
作为 gold 集合（Hit@5/MRR 精确匹配）。滚动全量 payload 一次性建索引，多目标
批量解析时只滚一遍。
"""
from __future__ import annotations

from kb_agent.store.qdrant_store import QdrantStore


def load_payload_index(store: QdrantStore) -> dict[str, dict]:
    """滚动全量 points，返回 {point_id: payload}。"""
    points: dict[str, dict] = {}
    offset = None
    while True:
        page, offset = store.client.scroll(
            collection_name=store.collection,
            with_payload=True,
            with_vectors=False,
            limit=256,
            offset=offset,
        )
        for p in page:
            points[str(p.id)] = p.payload or {}
        if offset is None:
            break
    return points


def resolve_gold(
    points: dict[str, dict], targets: list[tuple[str, list[str]]]
) -> tuple[list[str], list[str]]:
    """解析 gold point id 集合。

    targets: [(标题, [章节关键词]), ...]，关键词为空列表时匹配该标题下所有 chunk。
    返回 (gold_ids, warnings)，warnings 列出未解析到任何 chunk 的目标。
    """
    gold: list[str] = []
    warnings: list[str] = []
    for title, keywords in targets:
        matched: list[str] = []
        for pid, pl in points.items():
            if pl.get("title") != title:
                continue
            haystack = " ".join(pl.get("title_path") or []) + " " + (pl.get("text") or "")[:300]
            if not keywords or any(k in haystack for k in keywords):
                matched.append(pid)
        gold.extend(matched)
        if not matched:
            warnings.append(f"《{title}》未解析到 gold chunk（关键词 {keywords}）")
    return sorted(set(gold)), warnings
