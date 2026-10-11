"""LLM 客户端封装：OpenAI 兼容接口（DeepSeek）。

注意：deepseek-chat 当前被路由到 deepseek-flash（推理模型），
推理文本在 reasoning_content、生成在 content，max_tokens 必须给足
（否则 reasoning 吃光预算导致正文截断，见 docs/plan.md 已知问题 1）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from openai import OpenAI

# 推理模型预算：reasoning + 正文都要空间
DEFAULT_MAX_TOKENS = 8192
TOOL_ROUTE_MAX_TOKENS = 4096


@dataclass
class LLMReply:
    content: str
    tool_calls: list[dict[str, Any]] | None = None
    reasoning: str = ""
    finish_reason: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)


class LLMClient:
    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> None:
        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self.model = model
        self.max_tokens = max_tokens
        self.last_finish_reason: str | None = None

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int | None = None,
        temperature: float = 0.1,
    ) -> LLMReply:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens or self.max_tokens,
            "temperature": temperature,
        }
        if tools:
            kwargs["tools"] = tools
        resp = self.client.chat.completions.create(**kwargs)
        msg = resp.choices[0].message
        return LLMReply(
            content=msg.content or "",
            tool_calls=[
                {
                    "id": tc.id,
                    "name": tc.function.name,
                    "arguments": tc.function.arguments,
                }
                for tc in (msg.tool_calls or [])
            ],
            reasoning=getattr(msg, "reasoning_content", "") or "",
            finish_reason=resp.choices[0].finish_reason,
            usage={
                "prompt_tokens": resp.usage.prompt_tokens if resp.usage else 0,
                "completion_tokens": resp.usage.completion_tokens if resp.usage else 0,
            },
        )

    def chat_stream(
        self,
        messages: list[dict[str, Any]],
        max_tokens: int | None = None,
        temperature: float = 0.1,
    ):
        """流式生成，逐段 yield 正文 delta（跳过推理模型的 reasoning delta）。

        结束后 last_finish_reason 记录终止原因：stop 正常；length 说明
        max_tokens 被推理吃光、正文被截断（调用方据此诊断）。
        """
        resp = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            max_tokens=max_tokens or self.max_tokens,
            temperature=temperature,
            stream=True,
        )
        for chunk in resp:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if delta and delta.content:
                yield delta.content
            if chunk.choices[0].finish_reason:
                self.last_finish_reason = chunk.choices[0].finish_reason
