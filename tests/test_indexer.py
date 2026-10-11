"""indexer 的纯逻辑单测：role 目录层级提取（曾把 category 误当 role）。"""
from pathlib import Path

from kb_agent.ingest.indexer import role_for_path


def test_role_for_path_takes_first_level_under_corpus(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    assert role_for_path(corpus / "employee" / "企业制度" / "考勤制度.md", corpus) == "employee"
    assert role_for_path(corpus / "public" / "FAQ" / "入职FAQ.md", corpus) == "public"
    assert role_for_path(corpus / "admin" / "接口文档" / "x.pdf", corpus) == "admin"
    assert role_for_path(corpus / "contractor" / "企业制度" / "保密协议.md", corpus) == "contractor"


def test_role_for_path_nested_deeply_still_first_level(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    p = corpus / "employee" / "产品资料" / "子目录" / "云盘用户手册.md"
    assert role_for_path(p, corpus) == "employee"
