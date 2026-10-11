"""chunker 单测：块边界保留、标题路径、overlap、超长块切分、is_code。"""
from kb_agent.ingest.chunker import chunk_document
from kb_agent.ingest.documents import Block, ParsedDocument


def _doc() -> ParsedDocument:
    return ParsedDocument(
        doc_id="d1",
        title="手册",
        blocks=[
            Block(kind="heading", content="第一章", level=1, section="1"),
            Block(kind="text", content="短段落甲", section="1"),
            Block(kind="heading", content="第一节", level=2, section="1.1"),
            Block(kind="text", content="短段落乙", section="1.1"),
        ],
    )


def test_chunk_ids_stable() -> None:
    a = chunk_document(_doc(), chunk_size=10, overlap=0)
    b = chunk_document(_doc(), chunk_size=10, overlap=0)
    assert [c.chunk_id for c in a] == [c.chunk_id for c in b]
    assert [c.chunk_id for c in a] == ["d1-0", "d1-1"]


def test_title_path() -> None:
    chunks = chunk_document(_doc(), chunk_size=10, overlap=0)
    # chunk 0 以 第一章 标题开头，路径含该标题
    assert chunks[0].title_path == ["第一章"]
    # chunk 1 在 第一章 > 第一节 下
    assert chunks[1].title_path == ["第一章", "第一节"]


def test_overlap_appends_tail() -> None:
    doc = ParsedDocument(
        doc_id="d2",
        title="手册",
        blocks=[Block(kind="text", content="字" * 200), Block(kind="text", content="符" * 200)],
    )
    chunks = chunk_document(doc, chunk_size=100, overlap=20)
    assert len(chunks) >= 2
    assert chunks[1].text[:20] == chunks[0].text[-20:]


def test_long_block_split() -> None:
    doc = ParsedDocument(
        doc_id="d3", title="手册", blocks=[Block(kind="text", content="文" * 500)]
    )
    chunks = chunk_document(doc, chunk_size=100, overlap=0)
    assert len(chunks) == 5
    assert all(len(c.text) <= 100 for c in chunks)


def test_code_chunk_flagged() -> None:
    doc = ParsedDocument(
        doc_id="d4",
        title="手册",
        blocks=[Block(kind="code", content="print('hello')")],
    )
    chunks = chunk_document(doc, chunk_size=100, overlap=0)
    assert chunks[0].is_code


def test_invalid_params() -> None:
    try:
        chunk_document(_doc(), chunk_size=50, overlap=60)
    except AssertionError:
        return
    raise AssertionError("overlap >= chunk_size 应报错")
