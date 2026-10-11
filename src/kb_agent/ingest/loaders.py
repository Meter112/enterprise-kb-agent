"""多格式文档解析：PDF / Markdown / TXT → ParsedDocument。

设计要点：
- PDF 有真页码（渲染期确定），MD/TXT 无页码，用章节/段落编号定位引用
- 表格统一转成 Markdown 表格字符串保留结构
- 代码块与正文分开标注（后续支撑"代码检索"过滤）
"""
from __future__ import annotations

import re
from pathlib import Path

from kb_agent.ingest.documents import Block, ParsedDocument

_MD_TABLE_RE = re.compile(r"^\s*\|.*\|\s*$")


# ---------- PDF ----------


def _table_to_markdown(rows: list[list[str | None]]) -> str:
    """pdfplumber 表格 → Markdown 表格字符串。"""
    clean = [[(c or "").replace("\n", " ").strip() for c in row] for row in rows]
    if not clean or not any(any(c for c in row) for row in clean):
        return ""
    width = max(len(row) for row in clean)
    lines = []
    for i, row in enumerate(clean):
        padded = row + [""] * (width - len(row))
        lines.append("| " + " | ".join(padded) + " |")
        if i == 0:
            lines.append("|" + "---|" * width)
    return "\n".join(lines)


def parse_pdf(path: Path, doc_id: str) -> ParsedDocument:
    import pdfplumber

    blocks: list[Block] = []
    with pdfplumber.open(path) as pdf:
        for page_no, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            for para in _split_paragraphs(text):
                if para:
                    blocks.append(Block(kind="text", content=para, page=page_no))
            for table in page.extract_tables():
                md = _table_to_markdown(table)
                if md:
                    blocks.append(Block(kind="table", content=md, page=page_no))
    return ParsedDocument(doc_id=doc_id, title=path.stem, blocks=blocks)


def _split_paragraphs(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]


# ---------- Markdown ----------


def parse_markdown(path: Path, doc_id: str) -> ParsedDocument:
    """手写 Markdown 解析：标题 / 围栏代码块 / 表格 / 段落。

    章节编号按标题层级自动生成（如 "2.3"），正文/代码/表格继承所在章节编号，
    作为无页码格式的引用定位。
    """
    blocks: list[Block] = []
    counters: list[int] = []  # 各层级标题计数器
    para_lines: list[str] = []
    para_section: str | None = None
    code_lines: list[str] = []
    code_section: str | None = None
    in_code = False
    table_lines: list[str] = []
    table_section: str | None = None
    para_no = 0  # 无标题时的段落序号

    def section_no(level: int) -> str:
        while len(counters) < level:
            counters.append(0)
        counters[level - 1] += 1
        del counters[level:]  # 更深层级计数器作废
        return ".".join(str(c) for c in counters[:level])

    def current_section() -> str | None:
        for b in reversed(blocks):
            if b.kind == "heading":
                return b.section
        return None

    def flush_para() -> None:
        nonlocal para_lines, para_section
        if para_lines:
            blocks.append(
                Block(kind="text", content="\n".join(para_lines), section=para_section)
            )
            para_lines, para_section = [], None

    def flush_table() -> None:
        nonlocal table_lines, table_section
        if table_lines:
            blocks.append(Block(kind="table", content="\n".join(table_lines), section=table_section))
            table_lines, table_section = [], None

    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if in_code:
            if stripped.startswith("```"):
                blocks.append(Block(kind="code", content="\n".join(code_lines), section=code_section))
                in_code = False
            else:
                code_lines.append(line)
            continue
        if stripped.startswith("```"):
            flush_para()
            flush_table()
            in_code = True
            code_lines = []
            code_section = current_section()
        elif stripped.startswith("#"):
            flush_para()
            flush_table()
            level = len(stripped) - len(stripped.lstrip("#"))
            blocks.append(
                Block(kind="heading", content=stripped.lstrip("#").strip(), level=level, section=section_no(level))
            )
        elif _MD_TABLE_RE.match(stripped):
            flush_para()
            if set(stripped.replace("|", "")) <= {" ", "-", ":"}:
                continue  # 表头分隔行
            table_lines.append(stripped)
            if table_section is None:
                table_section = current_section()
        elif not stripped:
            flush_para()
            flush_table()
        else:
            flush_table()
            if para_section is None:
                para_no += 1
                para_section = current_section() or f"§{para_no}"
            para_lines.append(line)
    flush_para()
    flush_table()
    if in_code:
        blocks.append(Block(kind="code", content="\n".join(code_lines), section=code_section))
    return ParsedDocument(doc_id=doc_id, title=path.stem, blocks=blocks)


# ---------- TXT ----------


def parse_txt(path: Path, doc_id: str) -> ParsedDocument:
    text = path.read_text(encoding="utf-8")
    blocks: list[Block] = []
    for i, para in enumerate(_split_paragraphs(text), start=1):
        blocks.append(Block(kind="text", content=para, section=f"§{i}"))
    return ParsedDocument(doc_id=doc_id, title=path.stem, blocks=blocks)


# ---------- 分发 ----------

_PARSERS = {
    ".pdf": parse_pdf,
    ".md": parse_markdown,
    ".markdown": parse_markdown,
    ".txt": parse_txt,
}


def doc_id_for_path(path: Path) -> str:
    """默认 doc_id = 文件名 stem + 格式标签。

    同名 MD/PDF（如《员工手册》两种格式对照）必须共存在索引里；
    只用 stem 会让后索引的格式把先索引的 chunk 全部覆盖删除。
    """
    return f"{path.stem}#{path.suffix.lower().lstrip('.')}"


def parse_document(path: Path, doc_id: str | None = None) -> ParsedDocument:
    """按扩展名分发解析。doc_id 缺省用 doc_id_for_path()。"""
    doc_id = doc_id or doc_id_for_path(path)
    parser = _PARSERS.get(path.suffix.lower())
    if parser is None:
        raise ValueError(f"不支持的文档格式: {path.suffix}（支持: {', '.join(_PARSERS)}）")
    return parser(path, doc_id)
