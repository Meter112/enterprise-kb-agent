"""冒烟 3：DeepSeek API（OpenAI 兼容协议）+ function calling。

需要 .env 中已配置 DEEPSEEK_API_KEY。
用法: uv run python scripts/smoke_deepseek.py
"""
from openai import OpenAI

from kb_agent.config import settings


def main() -> None:
    assert settings.deepseek_api_key, "请先在 .env 中配置 DEEPSEEK_API_KEY"
    client = OpenAI(api_key=settings.deepseek_api_key, base_url=settings.deepseek_base_url)

    tools = [
        {
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "查询指定城市的天气",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "city": {"type": "string", "description": "城市名"}
                    },
                    "required": ["city"],
                },
            },
        }
    ]
    resp = client.chat.completions.create(
        model=settings.deepseek_chat_model,
        messages=[{"role": "user", "content": "北京今天天气怎么样？"}],
        tools=tools,
    )
    msg = resp.choices[0].message
    print(f"[model] {resp.model}")
    print(f"[tool_calls] {msg.tool_calls}")
    assert msg.tool_calls and msg.tool_calls[0].function.name == "get_weather"
    print("[OK] smoke_deepseek: chat + function calling 跑通")


if __name__ == "__main__":
    main()
