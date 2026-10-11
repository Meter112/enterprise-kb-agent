"""冒烟 4：LangGraph 手写 StateGraph + Redis checkpoint（多轮对话记忆持久化）。

验证要点：
- StateGraph 状态机（节点/边/条件路由基础）
- RedisSaver 跨调用恢复状态：每轮传入新消息，checkpoint 提供历史（真实多轮记忆模式）
用法: uv run python scripts/smoke_langgraph.py
"""
import operator
from typing import Annotated, TypedDict
from uuid import uuid4

import redis
from langgraph.checkpoint.redis import RedisSaver
from langgraph.graph import END, START, StateGraph

from kb_agent.config import settings


class State(TypedDict):
    messages: Annotated[list[str], operator.add]


def reply(state: State) -> dict:
    last = state["messages"][-1]
    return {"messages": [f"回复: {last}"]}


def main() -> None:
    # from_conn_string 返回上下文管理器；长驻进程用 redis_client 直接构造后需手动 setup() 建索引
    saver = RedisSaver(redis_client=redis.Redis.from_url(settings.redis_url))
    saver.setup()
    builder = StateGraph(State)
    builder.add_node("reply", reply)
    builder.add_edge(START, "reply")
    builder.add_edge("reply", END)
    graph = builder.compile(checkpointer=saver)

    # 每次运行用唯一 thread_id，避免上次失败运行残留的状态污染
    config = {"configurable": {"thread_id": f"smoke-{uuid4().hex[:8]}"}}
    r1 = graph.invoke({"messages": ["你好"]}, config)
    r2 = graph.invoke({"messages": ["再见"]}, config)  # checkpoint 恢复历史 + 追加新消息
    print(f"[checkpoint] 第一轮 messages={r1['messages']}")
    print(f"[checkpoint] 第二轮 messages={r2['messages']}")
    assert len(r1["messages"]) == 2
    assert len(r2["messages"]) == 4 and r2["messages"][0] == "你好", "Redis checkpoint 恢复失败"
    print("[OK] smoke_langgraph: StateGraph + Redis checkpoint 跑通")


if __name__ == "__main__":
    main()
