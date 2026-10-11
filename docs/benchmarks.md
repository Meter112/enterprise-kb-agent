
## BGE-M3 CPU encode 冒烟实测
- 时间: 2026-10-09 19:43
- 硬件: CPU-only, torch.set_num_threads(8)
- 模型加载: 2.5s
- 批量索引吞吐: 7.8 docs/s（~20 token/条）
- 单条查询延迟: 0.243s

## 混合检索全链路冒烟实测（RRF top-50 召回 + bge-reranker 重排）
- 时间: 2026-10-10 18:17
- 硬件: CPU-only, torch.set_num_threads(8)
- rerank 模型: bge-reranker-base (use_fp16=False)
- 单查询端到端延迟（含 embed + RRF + rerank top-50）: 平均 11.4s（n=4）
