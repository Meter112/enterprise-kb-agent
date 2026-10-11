"""Agent 状态定义（StateGraph 的 State）。

字段设计：
- messages：多轮消息历史（Annotated add reducer——D18-19 Redis checkpoint 多轮记忆直接复用）
- query/rewritten_query：原始问题与指代消解后的问题
- tool_calls/tool_results：工具调用轨迹与结果（trace 满足"工具轨迹日志完整"验证要求）
- chunks/citations：query_kb 候选集与格式化引用（generate 用、API 回显用）
- retry_count：引用校验失败重试计数（上限 2）
- refused/refuse_reason：拒答分支（no_candidates / low_confidence / citation_failed）
"""
from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict


class AgentState(TypedDict, total=False):
    messages: Annotated[list[dict[str, Any]], operator.add]
    query: str
    roles: list[str]
    rewritten_query: str
    tool_calls: list[dict[str, Any]]
    tool_results: list[dict[str, Any]]
    # chunks 存 dict（非 RetrievedChunk dataclass）：Redis checkpoint 的
    # JSON 序列化需要；exec_tools 转换时预生成 citation 标签
    chunks: list[dict[str, Any]]
    citations: list[str]
    answer: str
    refused: bool
    refuse_reason: str
    retry_count: int
    retry_feedback: str
    # 延迟拆解（D29）：节点串行执行，各节点读-改-写本 dict（键：retrieval_* / llm_* / validate / generate_ttft）
    latency: dict[str, float]
    trace: Annotated[list[dict[str, Any]], operator.add]
