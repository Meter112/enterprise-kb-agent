"""引用格式与引用校验单测。"""
from kb_agent.retrieval.citations import format_citation, validate_citations
from kb_agent.retrieval.hybrid_retriever import RetrievedChunk


def _chunk(**kw) -> RetrievedChunk:  # noqa: ANN003
    base = dict(
        chunk_id="d1-0",
        doc_id="d1",
        title="考勤制度",
        text="正文",
        title_path=["考勤制度", "请假流程"],
        page=None,
        section=None,
        role="employee",
        is_code=False,
        rrf_score=0.8,
        rerank_score=0.6,
    )
    base.update(kw)
    return RetrievedChunk(**base)


def test_format_citation_pdf_uses_page() -> None:
    assert format_citation(_chunk(page=3)) == "《考勤制度》第3页"


def test_format_citation_md_uses_section() -> None:
    assert format_citation(_chunk(section="2.3")) == "《考勤制度》§2.3"


def test_format_citation_fallback_title_path() -> None:
    assert format_citation(_chunk()) == "《考勤制度》考勤制度 > 请假流程"


def test_validate_citations_ok() -> None:
    chunks = [_chunk(), _chunk(chunk_id="d1-1")]
    assert validate_citations("年假 12 天[1]，病假需证明[2]。", chunks) == []


def test_validate_citations_out_of_range() -> None:
    problems = validate_citations("参见[3]", [_chunk()])
    assert len(problems) == 1 and "越界" in problems[0]


def test_validate_citations_empty_candidates_with_ref() -> None:
    problems = validate_citations("参见[1]", [])
    assert len(problems) == 1 and "无候选集" in problems[0]


def test_validate_citations_ignores_markdown_links() -> None:
    chunks = [_chunk()]
    assert validate_citations("参考链接[1](https://x.com) 与正文[1]。", chunks) == []
