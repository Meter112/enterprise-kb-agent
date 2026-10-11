"""增量索引：语料目录 → 解析 → chunking → 双向量 → Qdrant。

增量策略（双维度）：
- 文档内容 hash（sha256）：与上次索引记录一致的文件跳过（不重解析不重算向量）
- kb_version：文档集版本号，任何增删改都会 +1（缓存失效与语义缓存校验依赖它）

语料目录约定：data/corpus/<role>/.../*.{md,txt,pdf}，子目录名即角色标签。
元数据持久化在 data/index_cache/：doc_hashes.json、kb_version.txt、bm25_meta.json。
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path

from kb_agent.config import settings
from kb_agent.ingest.chunker import chunk_document
from kb_agent.ingest.loaders import doc_id_for_path, parse_document
from kb_agent.ingest.sparse_bm25 import BM25Encoder
from kb_agent.models.embedder import Embedder
from kb_agent.store.qdrant_store import QdrantStore

SUPPORTED_SUFFIXES = {".md", ".markdown", ".txt", ".pdf"}


@dataclass
class IndexReport:
    indexed: int
    skipped: int
    deleted: int
    chunks: int
    kb_version: int
    elapsed_seconds: float
    errors: list[str]


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def role_for_path(path: Path, corpus_dir: Path) -> str:
    """目录约定 data/corpus/<role>/<category>/<title>.md：role 是 corpus_dir 下第一层。

    不能取 path.parent.name——那是 category（企业制度/产品资料/…）。
    """
    return path.relative_to(corpus_dir).parts[0]


class Indexer:
    def __init__(
        self,
        store: QdrantStore,
        embedder: Embedder,
        cache_dir: Path | None = None,
    ) -> None:
        self.store = store
        self.embedder = embedder
        self.cache_dir = cache_dir or settings.data_dir / "index_cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.hashes_file = self.cache_dir / "doc_hashes.json"
        self.version_file = self.cache_dir / "kb_version.txt"
        self.bm25_file = self.cache_dir / "bm25_meta.json"

    # ---------- 版本管理 ----------

    def current_version(self) -> int:
        if self.version_file.exists():
            return int(self.version_file.read_text(encoding="utf-8").strip())
        return 0

    def _bump_version(self) -> int:
        v = self.current_version() + 1
        self.version_file.write_text(str(v), encoding="utf-8")
        return v

    def _load_hashes(self) -> dict[str, str]:
        if self.hashes_file.exists():
            return json.loads(self.hashes_file.read_text(encoding="utf-8"))
        return {}

    def _save_hashes(self, hashes: dict[str, str]) -> None:
        self.hashes_file.write_text(json.dumps(hashes, ensure_ascii=False, indent=2), encoding="utf-8")

    def known_file_names(self) -> set[str]:
        """已记录索引 hash 的文件名集合（/documents 列表的 indexed 标记用）。"""
        return set(self._load_hashes())

    # ---------- 主流程 ----------

    def index_directory(self, corpus_dir: Path = settings.corpus_dir, rebuild: bool = False) -> IndexReport:
        t0 = time.time()
        errors: list[str] = []
        files = sorted(p for p in corpus_dir.rglob("*") if p.suffix.lower() in SUPPORTED_SUFFIXES)

        self.store.ensure_collection(recreate=rebuild)
        old_hashes = {} if rebuild else self._load_hashes()
        new_hashes: dict[str, str] = {}

        # 已索引但文件已消失的 → 删除对应文档
        deleted = 0
        current_files = {p.name for p in files}
        for name, _ in old_hashes.items():
            if name not in current_files and not rebuild:
                self.store.delete_document(doc_id_for_path(Path(name)))
                deleted += 1

        # 解析 + 分块 + 编码（批量，控制内存与耗时）
        pending: list[tuple[Path, str]] = []  # (path, role)
        indexed = skipped = 0
        for path in files:
            h = _file_hash(path)
            new_hashes[path.name] = h
            if not rebuild and old_hashes.get(path.name) == h:
                skipped += 1
                continue
            role = role_for_path(path, corpus_dir)
            pending.append((path, role))

        # 解析待索引文档（单文档失败不阻塞整体）
        all_texts: list[str] = []
        all_parsed: list[tuple[Path, str, list]] = []
        for path, role in pending:
            try:
                doc = parse_document(path)
                chunks = chunk_document(doc)
                all_parsed.append((path, role, chunks))
                all_texts.extend(c.text for c in chunks)
            except Exception as e:  # noqa: BLE001
                errors.append(f"{path.name}: {e}")

        # BM25 是全局统计模型（IDF 依赖全语料）：增量新增文档时词表必须全量重拟合，
        # 且存量点的 sparse 向量要用新词表重算（dense 不受影响，不动）
        bm25 = BM25Encoder()
        existing_texts: dict[str, str] = {}
        if not rebuild and all_parsed:
            existing_texts = self.store.all_point_texts()
        if all_texts or existing_texts:
            bm25.fit(list(existing_texts.values()) + all_texts)
            bm25.save(self.bm25_file)
            if existing_texts:
                self.store.update_sparse_vectors(
                    list(
                        zip(
                            existing_texts.keys(),
                            bm25.encode_many(list(existing_texts.values())),
                            strict=True,
                        )
                    )
                )

        for path, role, chunks in all_parsed:
            try:
                # 内容变化的文档：先清旧点（chunk 边界可能漂移，旧 id 会残留成孤儿）
                self.store.delete_document(chunks[0].doc_id)
                dense = self.embedder.encode([c.text for c in chunks])
                sparse = bm25.encode_many([c.text for c in chunks])
                payloads = [
                    {
                        "doc_id": c.doc_id,
                        "title": path.stem,
                        "text": c.text,
                        "title_path": c.title_path,
                        "page": c.page,
                        "section": c.section,
                        "is_code": c.is_code,
                        "role": role,
                        "kb_version": self.current_version(),
                    }
                    for c in chunks
                ]
                self.store.upsert_chunks(
                    chunk_ids=[c.chunk_id for c in chunks],
                    texts=[c.text for c in chunks],
                    dense_vectors=dense.tolist(),
                    sparse_vectors=sparse,
                    payloads=payloads,
                )
                indexed += 1
            except Exception as e:  # noqa: BLE001
                errors.append(f"{path.name}: {e}")

        self._save_hashes(new_hashes)
        version = self._bump_version()
        return IndexReport(
            indexed=indexed,
            skipped=skipped,
            deleted=deleted,
            chunks=self.store.count(),
            kb_version=version,
            elapsed_seconds=time.time() - t0,
            errors=errors,
        )

    def delete_document(self, doc_id: str) -> None:
        """删除单个文档并推进版本。"""
        self.store.delete_document(doc_id)
        hashes = self._load_hashes()
        removed = [k for k in hashes if doc_id_for_path(Path(k)) == doc_id]
        for k in removed:
            del hashes[k]
        self._save_hashes(hashes)
        self._bump_version()

    def rebuild(self, corpus_dir: Path = settings.corpus_dir) -> IndexReport:
        """全量重建（drop collection 重新入库）。"""
        return self.index_directory(corpus_dir, rebuild=True)
