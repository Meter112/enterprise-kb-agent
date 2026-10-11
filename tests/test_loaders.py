"""解析器单测：MD（标题/代码块/表格/章节编号）、TXT、分发逻辑。"""
from pathlib import Path

import pytest

from kb_agent.ingest.loaders import doc_id_for_path, parse_document, parse_markdown, parse_txt

MD_SAMPLE = """# 员工手册

## 1. 考勤制度

员工应于工作日 9:00 前到岗。

| 时段 | 要求 |
| --- | --- |
| 上午 | 9:00 前打卡 |

## 2. 请假流程

```python
def apply_leave(days):
    return days <= 5
```

以上为示例代码。
"""


@pytest.fixture
def md_file(tmp_path: Path) -> Path:
    f = tmp_path / "handbook.md"
    f.write_text(MD_SAMPLE, encoding="utf-8")
    return f


def test_markdown_blocks(md_file: Path) -> None:
    doc = parse_markdown(md_file, "handbook")
    kinds = [b.kind for b in doc.blocks]
    assert kinds[0] == "heading" and doc.blocks[0].content == "员工手册"
    assert "table" in kinds
    assert "code" in kinds
    code = next(b for b in doc.blocks if b.kind == "code")
    assert "apply_leave" in code.content


def test_markdown_section_numbers(md_file: Path) -> None:
    doc = parse_markdown(md_file, "handbook")
    headings = {b.content: b.section for b in doc.blocks if b.kind == "heading"}
    assert headings["员工手册"] == "1"
    assert headings["1. 考勤制度"] == "1.1"
    assert headings["2. 请假流程"] == "1.2"


def test_markdown_table_content(md_file: Path) -> None:
    doc = parse_markdown(md_file, "handbook")
    table = next(b for b in doc.blocks if b.kind == "table")
    assert "时段" in table.content and "9:00 前打卡" in table.content


def test_txt_paragraph_sections(tmp_path: Path) -> None:
    f = tmp_path / "notes.txt"
    f.write_text("第一段内容。\n\n第二段内容。", encoding="utf-8")
    doc = parse_txt(f, "notes")
    assert len(doc.blocks) == 2
    assert [b.section for b in doc.blocks] == ["§1", "§2"]


def test_dispatch_and_unsupported(tmp_path: Path) -> None:
    f = tmp_path / "a.txt"
    f.write_text("hello", encoding="utf-8")
    doc = parse_document(f)
    assert doc.doc_id == "a#txt"

    bad = tmp_path / "a.docx"
    bad.write_text("x", encoding="utf-8")
    with pytest.raises(ValueError, match="不支持的文档格式"):
        parse_document(bad)


def test_doc_id_does_not_collide_same_stem(tmp_path: Path) -> None:
    """同名 MD/PDF 的默认 doc_id 必须不同（回归：曾共享 stem 导致互相覆盖）。"""
    md = tmp_path / "handbook.md"
    md.write_text("# 手册\n\n正文", encoding="utf-8")
    assert parse_document(md).doc_id == "handbook#md"
    assert doc_id_for_path(tmp_path / "handbook.pdf") == "handbook#pdf"
