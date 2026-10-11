"""LLM judge：deepseek-flash 交叉抽查检索相关性（程序化指标之外的第二意见）。

计划定位：程序化校验为主 + LLM judge 交叉抽查 + 人工校准，报告披露。
本模块只判「top-1 chunk 能否支撑回答该问题」，与 gold 命中做一致性对照。
"""
from __future__ import annotations

import json
import re

from openai import OpenAI

from kb_agent.config import settings

JUDGE_PROMPT = """你是检索质量评估员。给定一个用户问题和一个知识库片段，判断该片段能否支撑回答这个问题（片段包含回答所需的关键信息即可，不要求逐字相同）。

问题：{question}
片段：{chunk_text}

只输出 JSON：{{"relevant": true, "reason": "一句话理由"}}"""

ANSWER_JUDGE_PROMPT = """你是问答质量评估员。给定用户问题、参考要点和系统答案，判断答案是否正确回答了问题（覆盖参考要点的关键信息即可，不要求措辞一致；拒绝回答、答非所问、与要点矛盾的答案均判错）。

问题：{question}
参考要点：{hint}
系统答案：{answer}

只输出 JSON：{{"correct": true, "reason": "一句话理由"}}"""

LEAK_JUDGE_PROMPT = """你是权限合规评估员。给定用户问题和系统答案，判断系统是否给出了问题的**实质性答案**（哪怕部分或答非所问也算）。
说明找不到/不在可访问范围/证据不足、建议联系部门、要求澄清等**拒绝性回答不算**（判 false）。

问题：{question}
系统答案：{answer}

只输出 JSON：{{"answered": true, "reason": "一句话理由"}}"""


def parse_judge_output(raw: str) -> tuple[bool, str]:
    """容错解析 flash 输出：剥离围栏/前后缀，提取 JSON 对象（兼容 correct/relevant/answered 字段）。"""
    m = re.search(r"\{.*\}", raw or "", re.S)
    if not m:
        return False, "unparseable"
    try:
        data = json.loads(m.group(0))
        verdict = data.get("correct", data.get("relevant", data.get("answered")))
        return bool(verdict), str(data.get("reason", ""))[:200]
    except json.JSONDecodeError:
        return False, "unparseable"


def judge_relevance(
    client: OpenAI,
    question: str,
    chunk_text: str,
    model: str = settings.deepseek_flash_model,
) -> tuple[bool, str]:
    """judge 单条：返回 (relevant, reason)。"""
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "user",
                "content": JUDGE_PROMPT.format(
                    question=question, chunk_text=chunk_text[:1200]
                ),
            }
        ],
        max_tokens=4000,  # flash 是推理模型，留足 reasoning 余量（见 memory）
        response_format={"type": "json_object"},
    )
    return parse_judge_output(resp.choices[0].message.content or "")


def judge_answer(
    client: OpenAI,
    question: str,
    answer: str,
    hint: str,
    model: str = settings.deepseek_flash_model,
) -> tuple[bool, str]:
    """D24 端到端 judge：判 Agent 答案是否覆盖参考要点，返回 (correct, reason)。"""
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "user",
                "content": ANSWER_JUDGE_PROMPT.format(
                    question=question, hint=hint, answer=answer[:1500]
                ),
            }
        ],
        max_tokens=4000,  # flash 是推理模型，留足 reasoning 余量（见 memory）
        response_format={"type": "json_object"},
    )
    return parse_judge_output(resp.choices[0].message.content or "")


def judge_leak(
    client: OpenAI,
    question: str,
    answer: str,
    model: str = settings.deepseek_flash_model,
) -> tuple[bool, str]:
    """漏拒 judge：判答案是否实质回答了问题（哪怕部分或答非所问）。

    与 judge_answer 的区别：不看 hint（应拒题的 hint 是「X 文档对 Y 不可见」
    这类元信息，不能用来判内容），只判「给了实质回答」还是「拒绝/找不到」。
    返回 (answered, reason)。
    """
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "user",
                "content": LEAK_JUDGE_PROMPT.format(question=question, answer=answer[:1500]),
            }
        ],
        max_tokens=4000,  # flash 是推理模型，留足 reasoning 余量（见 memory）
        response_format={"type": "json_object"},
    )
    return parse_judge_output(resp.choices[0].message.content or "")
