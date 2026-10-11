"""引用格式与程序化引用校验（D8-10）。

格式规则（计划）：
  - PDF：文档名 + 页码（pdfplumber 真页码）
  - MD/TXT：文档名 + 章节/段落编号（section，如 §2.3）；无编号时退回标题路径
  - 无渲染期页码的格式不伪造页码（本仓库只有 PDF/MD/TXT）

校验规则（程序化部分）：答案中的每个 [n] 必须满足 1 ≤ n ≤ len(候选集)，
防止引用越界/无中生有；"引用支撑该句"的语义校验由 LLM judge 交叉抽查（D11-13）。
"""
from __future__ import annotations

import re

from kb_agent.retrieval.hybrid_retriever import RetrievedChunk

CITATION_RE = re.compile(r"\[(\d{1,2})\](?!\()")  # 1-99 号引用；[n]( 形如 markdown 链接不误伤


def format_citation(chunk: RetrievedChunk) -> str:
    """《标题》+ 定位：PDF 用页码，MD/TXT 用章节编号，都没有则退回标题路径。"""
    if chunk.page is not None:
        return f"《{chunk.title}》第{chunk.page}页"
    if chunk.section:
        return f"《{chunk.title}》§{chunk.section}"
    if chunk.title_path:
        return f"《{chunk.title}》{' > '.join(chunk.title_path[-2:])}"
    return f"《{chunk.title}》"


def validate_citations(answer: str, chunks: list) -> list[str]:
    """返回问题列表（空 = 通过）：候选为空却出现引用 / 引用编号越界。

    chunks 只需支持 len()：Agent 层的 state 存 dict 列表（checkpoint 序列化），
    检索层评估传 RetrievedChunk 列表，两种都兼容。
    """
    problems: list[str] = []
    matches = list(CITATION_RE.finditer(answer))
    if not chunks:
        if matches:
            problems.append("无候选集却出现 [n] 引用")
        return problems
    for m in matches:
        n = int(m.group(1))
        if n < 1 or n > len(chunks):
            problems.append(f"引用越界: [{n}]（候选共 {len(chunks)} 条）")
    return problems
