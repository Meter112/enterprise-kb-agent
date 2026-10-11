"""/chat SSE（D18-19）：同步 graph.invoke 跑后台线程，事件经队列桥接异步生成器。

事件序列（前端按顺序消费）：
  tool_call → retrieved → token* → citations → (refused) → done
- token 事件带 attempt 编号：引用校验重试时会出现多轮 token 流，前端以 done 为准
- 权限在 Agent 内部检索期过滤（payload filter），role 随请求传入
"""
from __future__ import annotations

import asyncio
import json
import queue
import threading
from functools import lru_cache
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from kb_agent.agent.graph import KBAgent
from kb_agent.agent.llm import LLMClient
from kb_agent.api.schemas import ChatRequest
from kb_agent.config import settings
from kb_agent.ingest.sparse_bm25 import BM25Encoder
from kb_agent.models.embedder import Embedder
from kb_agent.models.reranker import Reranker
from kb_agent.store.qdrant_store import QdrantStore

ROLES = ("public", "contractor", "employee", "admin")
router = APIRouter(prefix="/chat", tags=["chat"])


def _build_checkpointer():
    """Redis checkpoint（多轮记忆）。Redis 不可用时降级为无状态单轮。"""
    try:
        from langgraph.checkpoint.redis import RedisSaver

        # 直接构造拿实例；from_conn_string 是 contextmanager，退出 with 会关连接，
        # 服务长生命周期内需要长连接。default_ttl 让单轮/多轮 checkpoint 过期，
        # 避免 Redis 无限堆积（单位分钟，24h 覆盖演示与调试周期）
        saver = RedisSaver(redis_url=settings.redis_url, ttl={"default_ttl": 1440})
        # RediSearch 索引是内存对象，不随 RDB 持久化——Redis 重启后索引丢失，
        # langgraph 不会自动建，必须服务启动时显式 setup（幂等）重建
        saver.setup()
        return saver
    except Exception:  # noqa: BLE001 - 降级而非崩溃，单轮问答仍可用
        return None


def _build_cache():
    """L1 精确缓存。Redis 不可用时降级为无缓存（问答不受影响）。"""
    try:
        from kb_agent.agent.cache import ExactCache

        return ExactCache(settings.redis_url)
    except Exception:  # noqa: BLE001 - 降级而非崩溃
        return None


@lru_cache(maxsize=1)
def _get_agent() -> KBAgent:
    """进程内单例：模型加载约 7s，避免每个请求重建。"""
    llm = LLMClient(
        settings.deepseek_api_key,
        settings.deepseek_base_url,
        settings.deepseek_chat_model,
    )
    return KBAgent(
        QdrantStore(),
        Embedder(),
        BM25Encoder.load(settings.data_dir / "index_cache" / "bm25_meta.json"),
        Reranker(),
        llm,
        checkpointer=_build_checkpointer(),
        cache=_build_cache(),
    )


def _sse(event: dict[str, Any]) -> str:
    return f"event: {event['type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"


@router.post("")
async def chat(req: ChatRequest) -> StreamingResponse:
    if req.role not in ROLES:
        raise HTTPException(status_code=400, detail=f"role 必须是 {list(ROLES)} 之一")
    if not req.question.strip():
        raise HTTPException(status_code=400, detail="question 不能为空")

    agent = _get_agent()
    events: queue.Queue[dict[str, Any] | None] = queue.Queue()

    def run() -> None:
        try:
            agent.ask(req.question, req.role, session_id=req.session_id, events=events)
        except Exception as e:  # noqa: BLE001 - 事件带错误，SSE 正常关闭
            events.put({"type": "error", "detail": str(e)})
        finally:
            events.put(None)  # 结束哨兵

    threading.Thread(target=run, daemon=True).start()

    async def stream():
        while True:
            item = await asyncio.to_thread(events.get)
            if item is None:
                break
            yield _sse(item)

    return StreamingResponse(stream(), media_type="text/event-stream")
