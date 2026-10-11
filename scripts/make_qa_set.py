"""构造评估 QA 集：32 条 × 4 场景，gold chunk 从实时索引解析并快照。

gold 解析方式：按 (文档标题, 章节关键词) 在索引 payload 中匹配 chunk，
取全部命中的 point id 作为 gold 集合（Hit@5/MRR 精确匹配）。

用法: uv run python scripts/make_qa_set.py
"""
from __future__ import annotations

from kb_agent.config import settings
from kb_agent.eval.qa_set import QAItem, save_qa_set
from kb_agent.store.qdrant_store import QdrantStore

OUT = settings.data_dir / "qa_sets" / "qa_set_v1.json"

# (id, category, question, roles, answer_hint, expect_refusal, [(标题, [章节关键词]), ...])
QA_SPECS: list[tuple] = [
    # ---------------- single_hop：单文档事实（16 条） ----------------
    ("qa-001", "single_hop", "年假有几天", ["employee"], "年假天数与工龄对照", False,
     [("年假与调休制度", ["年假天数"])]),
    ("qa-002", "single_hop", "员工迟到会怎么处理", ["employee"], "迟到扣款阶梯", False,
     [("考勤制度", ["迟到"])]),
    ("qa-003", "single_hop", "出差住宿标准是多少", ["employee"], "城市等级住宿标准", False,
     [("差旅报销制度", ["住宿标准"])]),
    ("qa-004", "single_hop", "绩效考核的评级有哪些", ["employee"], "评级与系数", False,
     [("绩效考核制度", ["评级分布"])]),
    ("qa-005", "single_hop", "代码提交有什么规范", ["employee"], "提交规范", False,
     [("代码规范", ["提交规范"])]),
    ("qa-006", "single_hop", "发布失败如何回滚", ["employee"], "回滚流程步骤", False,
     [("发布流程规范", ["回滚流程"])]),
    ("qa-007", "single_hop", "公司数据分为几级", ["employee"], "公开/内部/机密/绝密", False,
     [("数据权限说明", ["数据分级"])]),
    ("qa-008", "single_hop", "社保和公积金如何缴纳", ["employee"], "缴纳比例与时间", False,
     [("HR FAQ", ["社保"])]),
    ("qa-009", "single_hop", "数据备份的频率是多少", ["admin"], "备份频率对照", False,
     [("数据备份制度", ["备份频率"])]),
    ("qa-010", "single_hop", "安全事件分为几级", ["admin"], "L1-L4 分级", False,
     [("信息安全制度", ["应急响应"])]),
    ("qa-011", "single_hop", "面试流程包括哪些环节", ["admin"], "面试轮次", False,
     [("招聘流程规范", ["面试流程"])]),
    ("qa-012", "single_hop", "云盘有哪些核心功能", ["public"], "核心功能列表", False,
     [("云盘产品白皮书", ["核心功能"])]),
    ("qa-013", "single_hop", "公开API如何认证", ["public"], "认证方式", False,
     [("公开API文档", ["认证方式"])]),
    ("qa-014", "single_hop", "入职需要准备什么材料", ["public"], "材料清单", False,
     [("入职FAQ", ["入职材料"])]),
    ("qa-015", "single_hop", "外包人员如何结算", ["contractor"], "结算周期", False,
     [("外包人员管理规范", ["结算"])]),
    ("qa-016", "single_hop", "第三方API的限流规则是什么", ["contractor"], "限流表格", False,
     [("第三方接入API文档", ["限流规则"])]),
    # ---------------- multi_doc：多文档对比（5 条） ----------------
    ("qa-017", "multi_doc", "年假和病假的申请流程有什么不同", ["employee"],
     "年假需提前申请、病假可事后补交（考勤制度 vs 年假与调休制度）", False,
     [("考勤制度", ["请假"]), ("年假与调休制度", ["申请流程"])]),
    ("qa-018", "multi_doc", "内部订单API和公开API的鉴权方式有什么不同", ["employee"],
     "内部鉴权 vs 公开认证", False,
     [("内部订单API文档", ["鉴权"]), ("公开API文档", ["认证方式"])]),
    ("qa-019", "multi_doc", "员工行为准则和外包驻场人员行为规范有什么不同", ["employee"],
     "两份规范的差异", False,
     [("员工手册-公开版", ["基本行为准则"]), ("驻场人员行为规范", [])]),
    ("qa-020", "multi_doc", "数据备份和应急预案分别要求多久演练一次", ["admin"],
     "备份恢复演练 vs 应急演练频率", False,
     [("数据备份制度", ["恢复演练"]), ("应急预案", ["演练要求"])]),
    ("qa-021", "multi_doc", "正式员工和外包人员的工时要求有什么区别", ["contractor"],
     "考勤制度 vs 外包考勤与工时", False,
     [("考勤制度", ["工作时间"]), ("外包人员管理规范", ["考勤与工时"])]),
    # ---------------- no_answer：语料外（5 条） ----------------
    ("qa-022", "no_answer", "公司健身房在哪里", ["employee"], "语料外", True, []),
    ("qa-023", "no_answer", "员工食堂提供什么菜", ["employee"], "语料外", True, []),
    ("qa-024", "no_answer", "如何申请办公用车", ["employee"], "语料外", True, []),
    ("qa-025", "no_answer", "公司年会什么时候举办", ["employee"], "语料外", True, []),
    ("qa-026", "no_answer", "公司附近有班车吗", ["employee"], "语料外", True, []),
    # ---------------- permission：权限受限（6 条，gold 指向不可见文档，仅报告用） ----------------
    ("qa-027", "permission", "年假有几天", ["contractor"], "employee 文档对 contractor 不可见", True,
     [("年假与调休制度", ["年假天数"])]),
    ("qa-028", "permission", "薪酬保密制度的内容是什么", ["employee"], "admin 文档对 employee 不可见", True,
     [("薪酬保密制度", ["保密范围"])]),
    ("qa-029", "permission", "财务付款流程是什么", ["employee"], "admin 文档对 employee 不可见", True,
     [("财务FAQ", ["付款流程"])]),
    ("qa-030", "permission", "内部订单API如何创建订单", ["contractor"], "employee 文档对 contractor 不可见", True,
     [("内部订单API文档", ["订单创建"])]),
    ("qa-031", "permission", "考勤迟到扣款标准是什么", ["contractor"], "employee 文档对 contractor 不可见", True,
     [("考勤制度", ["迟到"])]),
    ("qa-032", "permission", "研发部向谁汇报", ["employee"], "admin 文档对 employee 不可见", True,
     [("组织架构与岗位职责", ["汇报关系"])]),
]


def resolve_gold(store: QdrantStore, specs: list[tuple]) -> tuple[list[QAItem], list[str]]:
    """滚动全量 payload，按 (标题, 关键词) 解析 gold point id 集合。"""
    points: dict[str, dict] = {}
    offset = None
    while True:
        page, offset = store.client.scroll(
            collection_name=store.collection,
            with_payload=True,
            with_vectors=False,
            limit=256,
            offset=offset,
        )
        for p in page:
            points[str(p.id)] = p.payload or {}
        if offset is None:
            break

    items: list[QAItem] = []
    warnings: list[str] = []
    for qid, category, question, roles, hint, expect_refusal, targets in specs:
        gold: list[str] = []
        for title, keywords in targets:
            matched = []
            for pid, pl in points.items():
                if pl.get("title") != title:
                    continue
                haystack = " ".join(pl.get("title_path") or []) + " " + (pl.get("text") or "")[:300]
                if not keywords or any(k in haystack for k in keywords):
                    matched.append(pid)
            gold.extend(matched)
            if not matched:
                warnings.append(f"{qid}: 《{title}》未解析到 gold chunk（关键词 {keywords}）")
        items.append(
            QAItem(
                id=qid,
                category=category,
                question=question,
                roles=roles,
                answer_hint=hint,
                expect_refusal=expect_refusal,
                gold_chunk_ids=sorted(set(gold)),
            )
        )
    return items, warnings


def main() -> None:
    store = QdrantStore()
    items, warnings = resolve_gold(store, QA_SPECS)
    meta = {
        "version": 1,
        "total": len(items),
        "by_category": {c: sum(1 for i in items if i.category == c) for c in
                        ("single_hop", "multi_doc", "no_answer", "permission")},
        "gold_total": sum(len(i.gold_chunk_ids) for i in items),
    }
    save_qa_set(items, OUT, meta)
    print(f"[done] QA 集 {len(items)} 条 → {OUT}")
    print(f"[meta] {meta}")
    for w in warnings:
        print(f"[warn] {w}")


if __name__ == "__main__":
    main()
