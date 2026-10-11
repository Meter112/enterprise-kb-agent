"""构造模拟企业文档集：44 篇 × 4 类 × 4 个权限层级，Markdown 为主。

- 内容由 deepseek-flash 按规格生成（约 0.1 元，闲时更省）
- 同内容生成 PDF 版本（reportlab + 黑体），供多格式解析对照
- 目录约定: data/corpus/<role>/<category>/<标题>.md，子目录名即权限标签

权限模型（最低可见层级）: public < contractor < employee < admin
用法: uv run python scripts/make_mock_corpus.py [--pdf]
"""
from __future__ import annotations

import argparse
import re
import time
from pathlib import Path

from openai import OpenAI

from kb_agent.config import settings

COMPANY = "云帆科技"
CORPUS_DIR = settings.corpus_dir

# (标题, 类别, 权限, 章节大纲, 内容要点)
SPECS: list[tuple[str, str, str, list[str], str]] = [
    # ---------------- public（8 篇） ----------------
    ("公司介绍", "产品资料", "public", ["公司概况", "发展历程", "主营业务", "企业文化"],
     "含一张核心数据表格（成立年份/员工规模/分支机构）"),
    ("云盘产品白皮书", "产品资料", "public", ["产品定位", "核心功能", "技术架构概览", "安全与合规", "版本计划"],
     "含功能对比表格；技术架构用文字描述+一张组件表"),
    ("公开API文档", "接口文档", "public", ["概述", "认证方式", "接口列表", "错误码"],
     "含 3 个 REST 接口的请求/响应 JSON 示例代码块、参数表格、错误码表格"),
    ("公开API变更日志", "接口文档", "public", ["v2.1 变更", "v2.0 变更", "v1.x 变更"],
     "每个版本列出变更接口与时间；含 JSON 示例"),
    ("员工手册-公开版", "企业制度", "public", ["公司简介", "入职指引", "基本行为准则", "办公环境"],
     "不含敏感内容；含办公地点表格"),
    ("服务条款", "企业制度", "public", ["服务范围", "用户义务", "免责声明", "条款变更"],
     "条款式内容，分条列项"),
    ("隐私政策", "企业制度", "public", ["信息收集范围", "信息使用方式", "用户权利", "联系方式"],
     "含信息分类表格"),
    ("入职FAQ", "FAQ", "public", ["入职材料", "报到流程", "试用期常见问题", "办公设备"],
     "问答式（Q/A），10 个以上问答；含材料清单表格"),
    # ---------------- contractor（8 篇） ----------------
    ("外包人员管理规范", "企业制度", "contractor", ["适用范围", "考勤与工时", "保密义务", "考核与结算"],
     "含工时登记规则；含结算周期表格"),
    ("外包项目交付规范", "企业制度", "contractor", ["交付物要求", "验收标准", "变更流程", "违约处理"],
     "含验收评分表"),
    ("驻场人员行为规范", "企业制度", "contractor", ["办公区域管理", "设备使用", "网络访问", "离场流程"],
     "含违规行为与处理对照表"),
    ("第三方接入API文档", "接口文档", "contractor", ["接入准备", "鉴权流程", "回调接口", "限流规则"],
     "含鉴权时序说明、代码示例（Python）、限流表格"),
    ("外部协作FAQ", "FAQ", "contractor", ["账号权限", "系统访问", "报销与工时", "沟通渠道"],
     "10 个以上问答"),
    ("保密协议摘要", "企业制度", "contractor", ["保密范围", "保密期限", "违约责任"],
     "不含具体金额；条款式"),
    ("远程办公规范-外包版", "企业制度", "contractor", ["适用情形", "工时要求", "工具要求", "数据安全"],
     "含远程工具清单表格"),
    ("供应商管理规范", "企业制度", "contractor", ["供应商准入", "评估机制", "退出机制"],
     "含准入评分表"),
    # ---------------- employee（16 篇） ----------------
    ("考勤制度", "企业制度", "employee", ["工作时间", "打卡规则", "迟到早退", "请假类型", "加班管理"],
     "含各类假期天数表格、迟到扣款阶梯表格"),
    ("差旅报销制度", "企业制度", "employee", ["报销范围", "交通工具标准", "住宿标准", "报销流程", "常见问题"],
     "含城市等级差旅标准表格（高铁/机票/住宿/餐补）、报销流程图用文字描述"),
    ("年假与调休制度", "企业制度", "employee", ["年假天数", "调休规则", "申请流程", "过期规则"],
     "含工龄-年假对照表"),
    ("绩效考核制度", "企业制度", "employee", ["考核周期", "考核指标", "评级分布", "绩效申诉"],
     "含评级与系数对照表"),
    ("员工培训制度", "企业制度", "employee", ["新员工培训", "技能培训", "培训积分", "外训申请"],
     "含培训积分规则表"),
    ("内部订单API文档", "接口文档", "employee", ["概述", "鉴权", "订单查询", "订单创建", "订单取消", "错误码"],
     "含 3 个接口的请求/响应 JSON 示例、字段说明表"),
    ("内部API变更日志", "接口文档", "employee", ["最近变更", "历史变更"],
     "含变更时间表"),
    ("云盘用户手册", "产品资料", "employee", ["快速上手", "文件管理", "共享与协作", "版本历史", "常见问题"],
     "含快捷键表格"),
    ("内部工具使用手册", "产品资料", "employee", ["工具清单", "权限申请", "使用规范"],
     "含工具与适用场景对照表"),
    ("代码规范", "企业制度", "employee", ["命名规范", "提交规范", "评审流程", "示例"],
     "含代码示例块（Python）"),
    ("发布流程规范", "企业制度", "employee", ["发布窗口", "变更审批", "灰度策略", "回滚流程"],
     "含流程步骤编号；含发布窗口表格"),
    ("值班制度", "企业制度", "employee", ["值班安排", "响应要求", "交接流程", "升级路径"],
     "含响应时效表格（P0-P3）"),
    ("数据权限说明", "企业制度", "employee", ["权限分级", "数据分级", "申请流程"],
     "含数据分级表（公开/内部/机密/绝密）"),
    ("HR FAQ", "FAQ", "employee", ["薪酬发放", "社保公积金", "福利", "证明开具"],
     "12 个以上问答"),
    ("报销FAQ", "FAQ", "employee", ["发票要求", "审批时效", "垫付规则"],
     "10 个以上问答"),
    ("会议与协作规范", "企业制度", "employee", ["会议类型", "会议纪要", "沟通工具"],
     "含会议类型对照表"),
    # ---------------- admin（12 篇） ----------------
    ("薪酬保密制度", "企业制度", "admin", ["保密范围", "违规处理", "特殊情况"],
     "含违规处理对照表"),
    ("信息安全制度", "企业制度", "admin", ["账号管理", "设备管理", "网络安全", "应急响应"],
     "含安全事件分级表（L1-L4）"),
    ("管理后台API文档", "接口文档", "admin", ["概述", "用户管理接口", "权限管理接口", "审计日志接口"],
     "含 3 个接口的 JSON 示例与参数表"),
    ("财务FAQ", "FAQ", "admin", ["预算管理", "付款流程", "发票管理"],
     "10 个以上问答"),
    ("招聘流程规范", "企业制度", "admin", ["需求审批", "面试流程", "Offer 审批", "背调"],
     "含审批权限表"),
    ("组织架构与岗位职责", "企业制度", "admin", ["组织架构", "部门职责", "汇报关系"],
     "含部门与负责人对照表"),
    ("年度预算制度", "企业制度", "admin", ["预算编制", "预算调整", "执行监控"],
     "含预算科目表"),
    ("服务器运维手册", "产品资料", "admin", ["环境清单", "日常巡检", "故障处理", "备份策略"],
     "含巡检项目表格、命令示例代码块"),
    ("数据备份制度", "企业制度", "admin", ["备份范围", "备份频率", "恢复演练"],
     "含备份频率对照表"),
    ("审计制度", "企业制度", "admin", ["审计范围", "审计流程", "结果处理"],
     "含流程步骤"),
    ("权限审批流程", "企业制度", "admin", ["审批矩阵", "紧急权限", "权限回收"],
     "含审批矩阵表格"),
    ("应急预案", "企业制度", "admin", ["应急组织", "事件分级", "响应流程", "演练要求"],
     "含事件分级与响应时限表"),
]

GEN_PROMPT = """你是{company}的文档专员。请撰写一篇企业内部文档《{title}》，输出纯 Markdown，要求：

【结构】
# {title}

{outline}

【硬性要求】
1. 严格按照上面的大纲写，大纲里每个条目是一个二级标题（##）
2. 正文 800-1500 字，使用符合中国企业内部文档风格的正式中文
3. {notes}
4. 表格用 Markdown 表格语法；代码用 ``` 围栏代码块并标注语言
5. 数据要具体、自洽（金额、天数、比例、时限等），不同文档间不矛盾
6. 只输出 Markdown 正文，不要输出任何解释、前后缀说明
"""


def generate_docs(client: OpenAI, with_pdf: bool, force: bool = False) -> list[Path]:
    written: list[Path] = []
    for title, category, role, outline, notes in SPECS:
        dest_dir = CORPUS_DIR / role / category
        dest = dest_dir / f"{title}.md"
        if dest.exists() and not force:
            print(f"[skip] 已存在: {dest.relative_to(CORPUS_DIR)}")
            written.append(dest)
            continue
        outline_md = "\n".join(f"## {s}" for s in outline)
        prompt = GEN_PROMPT.format(
            company=COMPANY, title=title, outline=outline_md, notes=notes
        )
        content = ""
        for attempt in range(3):
            resp = client.chat.completions.create(
                model=settings.deepseek_flash_model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.8,
                # flash 是推理模型：reasoning 会占用大量输出 token，必须留足余量，
                # 否则正文还没写完就 finish_reason=length（曾导致 42/44 篇为空）
                max_tokens=12000,
            )
            content = resp.choices[0].message.content or ""
            content = re.sub(r"^```markdown\s*|```$", "", content.strip()).strip()
            if len(content) >= 200:
                break
            print(f"[retry] {title} 第 {attempt + 1} 次生成过短 ({len(content)} 字)")
            time.sleep(1)
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest.write_text(content + "\n", encoding="utf-8")
        written.append(dest)
        print(f"[gen] {dest.relative_to(CORPUS_DIR)} ({len(content)} 字)")
        time.sleep(0.5)  # 限速，避免触发 API 频控
    return written


def md_to_pdf(md_path: Path, pdf_path: Path, font_path: str) -> None:
    """极简 Markdown → PDF：标题/段落/表格/代码块 → reportlab flowables。"""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import (
        Paragraph,
        Preformatted,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    pdfmetrics.registerFont(TTFont("SimHei", font_path))
    styles = getSampleStyleSheet()
    h1 = ParagraphStyle("H1cn", parent=styles["Title"], fontName="SimHei", fontSize=18, leading=24)
    h2 = ParagraphStyle("H2cn", parent=styles["Heading2"], fontName="SimHei", fontSize=14, leading=20)
    body = ParagraphStyle("Bodycn", parent=styles["BodyText"], fontName="SimHei", fontSize=10.5, leading=16)

    story = []
    in_code = False
    code_lines: list[str] = []
    table_rows: list[list[str]] = []

    def flush_code() -> None:
        nonlocal in_code, code_lines
        if code_lines:
            story.append(Preformatted("\n".join(code_lines), styles["Code"]))
            code_lines = []

    def flush_table() -> None:
        nonlocal table_rows
        if table_rows:
            data = [[c.strip() for c in r] for r in table_rows]
            t = Table(data, colWidths=[doc_width / len(data[0]) for _ in data[0]])
            t.setStyle(TableStyle([
                ("FONTNAME", (0, 0), (-1, -1), "SimHei"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
            ]))
            story.append(t)
            story.append(Spacer(1, 0.3 * cm))
            table_rows = []

    doc_width = A4[0] - 2 * 2 * cm
    for line in md_path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s.startswith("```"):
            flush_table()
            if in_code:
                flush_code()
                in_code = False
            else:
                in_code = True
        elif in_code:
            code_lines.append(line)
        elif s.startswith("|"):
            flush_code()
            if set(s.replace("|", "")) <= {" ", "-", ":"}:
                continue
            table_rows.append([c for c in s.strip("|").split("|")])
        elif s.startswith("#"):
            flush_code()
            flush_table()
            level = len(s) - len(s.lstrip("#"))
            story.append(Paragraph(s.lstrip("#").strip(), h1 if level == 1 else h2))
            story.append(Spacer(1, 0.2 * cm))
        elif not s:
            flush_code()
            flush_table()
            story.append(Spacer(1, 0.2 * cm))
        else:
            flush_table()
            story.append(Paragraph(s, body))
    flush_code()
    flush_table()

    def footer(canvas, _doc):  # 页码（pdfplumber 可提取，供引用定位）
        canvas.saveState()
        canvas.setFont("SimHei", 9)
        canvas.drawCentredString(A4[0] / 2, 1.2 * cm, str(canvas.getPageNumber()))
        canvas.restoreState()

    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    SimpleDocTemplate(
        str(pdf_path), pagesize=A4, leftMargin=2 * cm, rightMargin=2 * cm
    ).build(story, onFirstPage=footer, onLaterPages=footer)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", action="store_true", help="同时生成 PDF 版本（多格式解析对照）")
    parser.add_argument("--force", action="store_true", help="覆盖已存在的文档重新生成")
    parser.add_argument("--font", default=r"C:\Windows\Fonts\simhei.ttf", help="PDF 中文字体路径")
    args = parser.parse_args()

    assert settings.deepseek_api_key, "请先在 .env 中配置 DEEPSEEK_API_KEY"
    client = OpenAI(api_key=settings.deepseek_api_key, base_url=settings.deepseek_base_url)

    md_files = generate_docs(client, with_pdf=args.pdf, force=args.force)
    print(f"\n共 {len(md_files)} 篇 Markdown")

    if args.pdf:
        if not Path(args.font).exists():
            raise SystemExit(f"字体不存在: {args.font}（可 --font 指定其他中文字体）")
        # 每类挑 2 篇转 PDF（多格式解析对照用，不必全部转换）
        pick = {c: [] for c in ("企业制度", "产品资料", "接口文档", "FAQ")}
        for f in md_files:
            cat = f.parent.name
            if len(pick[cat]) < 2 and cat in pick:
                pick[cat].append(f)
        for f in [p for v in pick.values() for p in v]:
            pdf_path = f.with_suffix(".pdf")
            md_to_pdf(f, pdf_path, args.font)
            print(f"[pdf] {pdf_path.relative_to(CORPUS_DIR)}")


if __name__ == "__main__":
    main()
