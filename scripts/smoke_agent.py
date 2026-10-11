"""Agent 全链路冒烟（D15-17 验证）：真实 BGE-M3 + reranker + Qdrant + DeepSeek。

7 个场景：单跳事实 / 多文档对比 / 权限拒答 / 语料外拒答 / 寒暄直答 /
文档摘要(map-reduce) / 多轮指代改写。

用法: uv run python scripts/smoke_agent.py
"""
from __future__ import annotations

import time

from kb_agent.agent.graph import KBAgent
from kb_agent.agent.llm import LLMClient
from kb_agent.config import settings
from kb_agent.ingest.sparse_bm25 import BM25Encoder
from kb_agent.models.embedder import Embedder
from kb_agent.models.reranker import Reranker
from kb_agent.store.qdrant_store import QdrantStore

CASES: list[tuple[str, str, str | None, list[dict] | None]] = [
    # (名称, 问题, 角色, 多轮 history)
    ("单跳事实", "年假有几天", "employee", None),
    ("多文档对比", "年假和病假的申请流程有什么不同", "employee", None),
    ("权限拒答(admin文档)", "财务付款流程是什么", "employee", None),
    ("语料外拒答", "公司健身房在哪里", "employee", None),
    ("寒暄直答", "你好", "employee", None),
    ("文档摘要", "总结一下《员工手册-公开版》的核心内容", "employee", None),
    (
        "多轮指代改写",
        "它规定几天",
        "employee",
        [
            {"role": "user", "content": "员工有哪些假期福利"},
            {"role": "assistant", "content": "公司提供年假、调休、病假等多种假期福利。"},
        ],
    ),
]


def main() -> None:
    assert settings.deepseek_api_key, ".env 缺少 DEEPSEEK_API_KEY"
    print("加载模型与索引…")
    t0 = time.time()
    store = QdrantStore()
    embedder = Embedder()
    reranker = Reranker()
    bm25 = BM25Encoder.load(settings.data_dir / "index_cache" / "bm25_meta.json")
    llm = LLMClient(
        settings.deepseek_api_key,
        settings.deepseek_base_url,
        settings.deepseek_chat_model,
    )
    agent = KBAgent(store, embedder, bm25, reranker, llm)
    print(f"初始化完成 {time.time() - t0:.1f}s，共 {len(CASES)} 个场景\n")

    for name, question, role, history in CASES:
        t1 = time.time()
        out = agent.ask(question, role=role, history=history)
        dt = time.time() - t1
        nodes = " → ".join(t["node"] for t in out["trace"])
        print(f"【{name}】{question}")
        print(f"  角色={role} 拒绝={out['refused']}({out['refuse_reason']}) 耗时={dt:.1f}s")
        print(f"  轨迹: {nodes}")
        answer = out["answer"].replace("\n", " ")[:150]
        print(f"  答案: {answer}")
        if out["citations"]:
            print(f"  引用: {', '.join(out['citations'][:5])}")
        print()


if __name__ == "__main__":
    main()
