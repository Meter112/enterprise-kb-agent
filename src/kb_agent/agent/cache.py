"""L1 精确缓存（D20-21）：hash(normalize(query) + role + kb_version) → answer。

设计取舍（面试可讲）：
- 只缓存单轮问答：多轮请求依赖 checkpoint 上下文（「它规定几天」无法独立成 key），
  会话记忆本身已是一种上下文缓存，不做重复投资
- kb_version 进 key 前缀：文档上传/删除会推进版本，旧缓存自然失效，无需主动清
- 拒答结果同样缓存：语料外问题重复查询会重复烧检索+LLM 成本，且 kb_version 已
  保证语料更新后旧拒答失效
- 输入级错误（空 query 等）不进缓存，无价值且 key 会被污染
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

import redis

from kb_agent.config import settings

# 缓存键前缀（含 kb_version 的完整 key 形如 answer_cache:v5:employee:<sha256>）
KEY_PREFIX = "answer_cache"
DEFAULT_TTL = 7 * 24 * 3600  # 7 天；kb_version 变化时前缀不同，自然失效


def normalize_query(query: str) -> str:
    """归一化：去所有空白 + 小写（中文不受影响，英文大小写/空格差异合并）。"""
    return re.sub(r"\s+", "", query).lower()


def _kb_version() -> int:
    version_file = settings.data_dir / "index_cache" / "kb_version.txt"
    try:
        return int(version_file.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return 0


class ExactCache:
    """Redis 精确缓存。Redis 不可用时上层降级为 None（不缓存），问答不受影响。"""

    def __init__(self, redis_url: str, ttl: int = DEFAULT_TTL) -> None:
        self.redis = redis.from_url(redis_url, decode_responses=True)
        self.ttl = ttl

    def _key(self, query: str, role: str, kb_version: int) -> str:
        digest = hashlib.sha256(normalize_query(query).encode("utf-8")).hexdigest()
        return f"{KEY_PREFIX}:v{kb_version}:{role}:{digest}"

    def get(self, query: str, role: str) -> dict[str, Any] | None:
        raw = self.redis.get(self._key(query, role, _kb_version()))
        return json.loads(raw) if raw else None

    def set(self, query: str, role: str, result: dict[str, Any]) -> None:
        self.redis.set(
            self._key(query, role, _kb_version()),
            json.dumps(result, ensure_ascii=False),
            ex=self.ttl,
        )
