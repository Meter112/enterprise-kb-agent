"""摄取管道的统一数据模型。

解析器（loaders.py）输出 ParsedDocument（Block 流），
chunker.py 把 Block 流切成 Chunk，索引层只消费 Chunk。
"""
from dataclasses import dataclass, field


@dataclass
class Block:
    """文档中的一个语义块：标题 / 正文段落 / 代码块 / 表格。"""

    kind: str  # "heading" | "text" | "code" | "table"
    content: str
    level: int = 0  # heading 层级（1-6），其他 kind 为 0
    page: int | None = None  # 页码（仅 PDF 有真页码）
    section: str | None = None  # 章节/段落编号（MD/TXT 无页码时用于引用定位）


@dataclass
class ParsedDocument:
    doc_id: str
    title: str
    blocks: list[Block] = field(default_factory=list)


@dataclass
class Chunk:
    """送入向量库的检索单元。"""

    chunk_id: str  # 稳定 ID：f"{doc_id}-{序号}"，增量索引依赖其确定性
    doc_id: str
    text: str
    title_path: list[str]  # 该 chunk 所属标题路径，如 ["员工手册", "考勤制度", "请假流程"]
    page: int | None = None
    section: str | None = None
    is_code: bool = False
