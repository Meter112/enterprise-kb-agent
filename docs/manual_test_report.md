# 人工测试报告（D20-21）

> 生成时间：2026-10-10 20:20 | 目标：多轮对话 / 权限隔离 / 拒答边界 / L1 缓存
> 环境：真实 DeepSeek API + Qdrant 370 chunks（kb_version=5）+ Redis checkpoint/缓存

## 结果总览

9/9 通过

| 结果 | 场景 | 说明 |
|---|---|---|
| ✅ | 多轮 R1 年假规定 | 21.8s, 758 chars |
| ✅ | 多轮 R2 指代消解「那病假呢」 | rewritten → 员工手册中关于病假的规定是什么？ |
| ✅ | 多轮 R3 跨文档指代「提前几天申请」 | rewritten → 根据员工手册，年假需要提前几天申请？ |
| ✅ | contractor 问年假（employee 级文档）应拒 | public 可见文档无年假条款，阈值拦截正确 |
| ✅ | employee 问年假应正常回答 |  |
| ✅ | employee 问 admin 资料应拒 | low_confidence |
| ✅ | admin 问 admin 资料应正常回答 | answer: '知识库中未找到“3-2-1-1 备份原则”的完整定义。\n\n现有资料中仅提到：A类' |
| ✅ | 语料外问题应拒 | low_confidence |
| ✅ | 缓存二次命中 | 首次 20.5s → 二次 0.0s |
## 结论

- 多轮对话：checkpoint 记忆 + rewrite 指代消解跨文档生效（「那病假呢」「提前几天申请」均正确改写）
- 权限隔离：检索期 payload filter 生效——contractor 可见文档无年假条款时正确拒答；admin 级资料仅 admin 可见
- 拒答：语料外问题正确拒答，不编造
- L1 缓存：相同问题二次秒回（0.0s），normalize 合并空白变体，role 隔离 key
