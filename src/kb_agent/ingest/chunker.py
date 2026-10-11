"""chunking：固定长度 + 滑动窗口，保留块边界与标题路径。

策略：
- 以 Block 为最小单元，短块直接入段，超长块（如大表格/长代码）按字符切分
- 贪心合并段直到达到 chunk_size（字符数），下一 chunk 携带上一 chunk 尾部 overlap
- title_path 继承 chunk 首块所在标题路径；纯代码块 chunk 标记 is_code
"""
from __future__ import annotations

from dataclasses import replace
from itertools import pairwise

from kb_agent.config import settings
from kb_agent.ingest.documents import Block, Chunk, ParsedDocument


def _split_long_block(block: Block, limit: int) -> list[Block]:
    """超长块按字符切分（重叠交给 chunk 层处理）。

    短块必须返回原对象——chunker 依赖对象身份（is）把块定位回原文档算标题路径。
    """
    if len(block.content) <= limit:
        return [block]
    return [
        replace(block, content=block.content[i : i + limit])
        for i in range(0, len(block.content), limit)
    ]


def _segments(blocks: list[Block], limit: int) -> list[Block]:
    segs: list[Block] = []
    for b in blocks:
        segs.extend(_split_long_block(b, limit))
    return segs


def chunk_document(
    doc: ParsedDocument,
    chunk_size: int = settings.chunk_size,
    overlap: int = settings.chunk_overlap,
) -> list[Chunk]:
    assert chunk_size > overlap, "chunk_size 必须大于 overlap"
    segs = _segments(doc.blocks, chunk_size)

    chunks: list[Chunk] = []
    buf: list[Block] = []
    buf_len = 0

    def make_chunk(blocks_in: list[Block], idx: int) -> Chunk:
        first = blocks_in[0]
        text = "\n\n".join(b.content for b in blocks_in)
        path = _title_path_before(doc.blocks, first)
        if first.kind == "heading":  # chunk 以标题开头时，该标题属于本 chunk
            path = path + [first.content]
        return Chunk(
            chunk_id=f"{doc.doc_id}-{idx}",
            doc_id=doc.doc_id,
            text=text,
            title_path=path,
            page=first.page,
            section=first.section,
            is_code=all(b.kind == "code" for b in blocks_in),
        )

    for seg in segs:
        if buf_len + len(seg.content) <= chunk_size:
            buf.append(seg)
            buf_len += len(seg.content)
        else:
            if buf:
                chunks.append(make_chunk(buf, len(chunks)))
            buf = [seg]
            buf_len = len(seg.content)
    if buf:
        chunks.append(make_chunk(buf, len(chunks)))

    # 滑动窗口重叠：把上一 chunk 尾部 overlap 字符拼到当前 chunk 头部
    if overlap > 0 and len(chunks) > 1:
        overlapped: list[Chunk] = [chunks[0]]
        for prev, cur in pairwise(chunks):
            tail = prev.text[-overlap:]
            overlapped.append(replace(cur, text=tail + "\n" + cur.text))
        chunks = overlapped
    return chunks


def _title_path_before(blocks: list[Block], target: Block) -> list[str]:
    """target 块之前的标题路径（栈式：同层级标题替换，更深追加）。"""
    stack: list[tuple[int, str]] = []
    for b in blocks:
        if b is target:
            break
        if b.kind == "heading":
            while stack and stack[-1][0] >= b.level:
                stack.pop()
            stack.append((b.level, b.content))
    return [t for _, t in stack]


# 保留旧名兼容引用（部分代码可能按块级调用）
def chunk_blocks(blocks: list[Block], doc_id: str, title: str = "", **kw) -> list[Chunk]:
    return chunk_document(ParsedDocument(doc_id=doc_id, title=title, blocks=blocks), **kw)
