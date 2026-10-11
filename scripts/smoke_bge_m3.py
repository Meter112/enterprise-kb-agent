"""冒烟 2：BGE-M3 CPU encode 实测（dense + sparse 双向量）。

实测数据（加载耗时 / 批量吞吐 / 单查询延迟）追加写入 docs/benchmarks.md。
用法: uv run python scripts/smoke_bge_m3.py
"""
import time
from pathlib import Path

from FlagEmbedding import BGEM3FlagModel

MODEL_DIR = Path(__file__).resolve().parents[1] / "data" / "models" / "BAAI--bge-m3"


def main() -> None:
    import torch

    torch.set_num_threads(8)

    t0 = time.time()
    model = BGEM3FlagModel(str(MODEL_DIR), use_fp16=False, device="cpu")
    load_time = time.time() - t0
    print(f"[load] {load_time:.1f}s")

    # 模拟批量索引：20 条中等长度中文文本
    texts = [
        "员工年假制度规定，工作满一年的员工享有五天带薪年假，随工龄增长逐年递增。",
        "Qdrant 向量数据库支持命名稀疏向量，配合 prefetch 与 RRF 实现混合检索。",
    ] * 10

    t0 = time.time()
    out = model.encode(
        texts, batch_size=4, max_length=512, return_dense=True, return_sparse=True
    )
    elapsed = time.time() - t0
    dense = out["dense_vecs"]
    sparse = out["lexical_weights"]
    print(
        f"[encode] {len(texts)} 条, 耗时 {elapsed:.2f}s, 吞吐 {len(texts) / elapsed:.1f} docs/s"
    )
    print(f"[shape] dense={dense.shape}, sparse 样例={list(sparse[0].items())[:3]}")
    assert dense.shape == (len(texts), 1024), "dense 维度应为 1024"
    assert sparse[0], "sparse 输出不应为空"

    # 查询侧延迟
    t0 = time.time()
    model.encode(["年假有几天？"], return_dense=True, return_sparse=False)
    query_time = time.time() - t0
    print(f"[query] 单条查询 {query_time:.3f}s")

    # 追加实测记录到 benchmarks 文档
    bench = Path(__file__).resolve().parents[1] / "docs" / "benchmarks.md"
    bench.parent.mkdir(exist_ok=True)
    record = (
        f"\n## BGE-M3 CPU encode 冒烟实测\n"
        f"- 时间: {time.strftime('%Y-%m-%d %H:%M')}\n"
        f"- 硬件: CPU-only, torch.set_num_threads(8)\n"
        f"- 模型加载: {load_time:.1f}s\n"
        f"- 批量索引吞吐: {len(texts) / elapsed:.1f} docs/s（~20 token/条）\n"
        f"- 单条查询延迟: {query_time:.3f}s\n"
    )
    with open(bench, "a", encoding="utf-8") as f:
        f.write(record)
    print("[OK] smoke_bge_m3: dense+sparse 双向量 CPU 编码跑通，实测数据已写入 docs/benchmarks.md")


if __name__ == "__main__":
    main()
