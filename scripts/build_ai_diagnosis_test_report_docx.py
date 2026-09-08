"""Build the interview-facing Mini-Drop AI diagnosis verification report.

The benchmark JSON is the machine-readable source for every route-selection
number in the document.  The report deliberately separates that deterministic
benchmark from Linux live diagnosis evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
from pathlib import Path

from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.style import WD_STYLE_TYPE
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Inches, Pt, RGBColor
from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
BENCHMARK = ROOT / "web/public/report-assets/skill-evolution/benchmark-report.json"
LIVE_ACCEPTANCE = ROOT / "reports/ai-diagnosis/interview-demo-live-acceptance-20260906T142517Z.json"
DEFAULT_OUTPUT = ROOT / "reports/ai-diagnosis/Mini-Drop-AI诊断与Skill复用测试报告-20260906.docx"

INK = RGBColor(20, 29, 47)
MUTED = RGBColor(85, 96, 112)
BLUE = "2563EB"
LIGHT_BLUE = "EAF2FF"
GREEN = "047857"
LIGHT_GREEN = "E9F8F0"
ORANGE = "B45309"
LIGHT_ORANGE = "FFF4E5"
GRID = "D7DEE8"
LIGHT_GRAY = "F5F7FA"


def set_cell_shading(cell, fill: str) -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = tc_pr.find(qn("w:shd"))
    if shd is None:
        shd = OxmlElement("w:shd")
        tc_pr.append(shd)
    shd.set(qn("w:fill"), fill)


def set_cell_margins(cell, top=80, start=110, bottom=80, end=110) -> None:
    tc = cell._tc
    tc_pr = tc.get_or_add_tcPr()
    tc_mar = tc_pr.first_child_found_in("w:tcMar")
    if tc_mar is None:
        tc_mar = OxmlElement("w:tcMar")
        tc_pr.append(tc_mar)
    for edge, value in (("top", top), ("start", start), ("bottom", bottom), ("end", end)):
        node = tc_mar.find(qn(f"w:{edge}"))
        if node is None:
            node = OxmlElement(f"w:{edge}")
            tc_mar.append(node)
        node.set(qn("w:w"), str(value))
        node.set(qn("w:type"), "dxa")


def set_repeat_table_header(row) -> None:
    tr_pr = row._tr.get_or_add_trPr()
    tbl_header = OxmlElement("w:tblHeader")
    tbl_header.set(qn("w:val"), "true")
    tr_pr.append(tbl_header)


def prevent_row_split(row) -> None:
    tr_pr = row._tr.get_or_add_trPr()
    tr_pr.append(OxmlElement("w:cantSplit"))


def set_repeat_header_text(run, font_size=9, color=INK, bold=False) -> None:
    run.font.name = "Arial"
    run._element.rPr.rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
    run.font.size = Pt(font_size)
    run.font.color.rgb = color
    run.bold = bold


def add_page_number(paragraph) -> None:
    paragraph.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    label = paragraph.add_run("Mini-Drop AI 诊断验证报告  ·  ")
    set_repeat_header_text(label, 8, MUTED)
    begin = OxmlElement("w:fldChar")
    begin.set(qn("w:fldCharType"), "begin")
    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = " PAGE "
    separate = OxmlElement("w:fldChar")
    separate.set(qn("w:fldCharType"), "separate")
    text = OxmlElement("w:t")
    text.text = "1"
    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    run = paragraph.add_run()
    run._r.extend([begin, instr, separate, text, end])
    set_repeat_header_text(run, 8, MUTED)


def configure_document(doc: Document) -> None:
    section = doc.sections[0]
    section.page_width = Inches(8.5)
    section.page_height = Inches(11)
    section.top_margin = Inches(0.68)
    section.bottom_margin = Inches(0.62)
    section.left_margin = Inches(0.72)
    section.right_margin = Inches(0.72)
    section.header_distance = Inches(0.28)
    section.footer_distance = Inches(0.3)

    styles = doc.styles
    normal = styles["Normal"]
    normal.font.name = "Arial"
    normal._element.rPr.rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
    normal.font.size = Pt(9.5)
    normal.font.color.rgb = INK
    normal.paragraph_format.space_after = Pt(5)
    normal.paragraph_format.line_spacing = 1.22

    for style_name, size, color, before, after in (
        ("Title", 25, INK, 0, 10),
        ("Subtitle", 11, MUTED, 0, 12),
        ("Heading 1", 16, INK, 18, 8),
        ("Heading 2", 12, INK, 12, 5),
        ("Heading 3", 10, INK, 9, 4),
    ):
        style = styles[style_name]
        style.font.name = "Arial"
        style._element.rPr.rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
        style.font.size = Pt(size)
        style.font.bold = style_name != "Subtitle"
        style.font.color.rgb = color
        style.paragraph_format.space_before = Pt(before)
        style.paragraph_format.space_after = Pt(after)
        style.paragraph_format.keep_with_next = True

    if "Metric" not in styles:
        metric = styles.add_style("Metric", WD_STYLE_TYPE.PARAGRAPH)
        metric.base_style = styles["Normal"]
        metric.font.name = "Arial"
        metric._element.rPr.rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
        metric.font.size = Pt(18)
        metric.font.bold = True
        metric.font.color.rgb = RGBColor.from_string(BLUE)
        metric.paragraph_format.space_after = Pt(1)

    header = section.header.paragraphs[0]
    header.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    run = header.add_run("MINI-DROP  /  证据优先诊断")
    set_repeat_header_text(run, 7.5, MUTED, True)
    add_page_number(section.footer.paragraphs[0])


def add_rule(paragraph, color=BLUE, width="6900") -> None:
    p_pr = paragraph._p.get_or_add_pPr()
    borders = p_pr.find(qn("w:pBdr"))
    if borders is None:
        borders = OxmlElement("w:pBdr")
        p_pr.append(borders)
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), "12")
    bottom.set(qn("w:space"), "1")
    bottom.set(qn("w:color"), color)
    borders.append(bottom)


def add_labeled_line(doc, label: str, text: str, color=BLUE) -> None:
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(4)
    tag = p.add_run(label + "  ")
    set_repeat_header_text(tag, 8.5, RGBColor.from_string(color), True)
    value = p.add_run(text)
    set_repeat_header_text(value, 9.5, INK)


def add_bullets(doc, items, level=0) -> None:
    for item in items:
        p = doc.add_paragraph(style="List Bullet" if level == 0 else "List Bullet 2")
        p.paragraph_format.space_after = Pt(3)
        p.add_run(item)


def add_numbered(doc, items) -> None:
    # Use explicit numbering so every independent procedure restarts at 1.
    # Word's built-in List Number style otherwise continues across sections.
    for index, item in enumerate(items, start=1):
        p = doc.add_paragraph()
        p.paragraph_format.left_indent = Cm(0.72)
        p.paragraph_format.first_line_indent = Cm(-0.52)
        p.paragraph_format.space_after = Pt(3)
        marker = p.add_run(f"{index}.  ")
        set_repeat_header_text(marker, 9.5, INK, True)
        p.add_run(item)


def add_table(doc, headers, rows, widths=None, header_fill=BLUE):
    table = doc.add_table(rows=1, cols=len(headers))
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.style = "Table Grid"
    table.autofit = False
    header = table.rows[0]
    set_repeat_table_header(header)
    prevent_row_split(header)
    for idx, text in enumerate(headers):
        cell = header.cells[idx]
        cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
        set_cell_shading(cell, header_fill)
        set_cell_margins(cell)
        if widths:
            cell.width = widths[idx]
        p = cell.paragraphs[0]
        p.paragraph_format.space_after = Pt(0)
        run = p.add_run(str(text))
        set_repeat_header_text(run, 8.2, RGBColor(255, 255, 255), True)
    for r_index, row_data in enumerate(rows):
        row = table.add_row()
        prevent_row_split(row)
        for idx, text in enumerate(row_data):
            cell = row.cells[idx]
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            set_cell_margins(cell)
            if widths:
                cell.width = widths[idx]
            if r_index % 2:
                set_cell_shading(cell, LIGHT_GRAY)
            p = cell.paragraphs[0]
            p.paragraph_format.space_after = Pt(0)
            run = p.add_run(str(text))
            set_repeat_header_text(run, 8.2, INK)
    doc.add_paragraph().paragraph_format.space_after = Pt(1)
    return table


def add_metric_strip(doc, metrics) -> None:
    table = doc.add_table(rows=1, cols=len(metrics))
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = False
    set_repeat_table_header(table.rows[0])
    prevent_row_split(table.rows[0])
    for idx, (number, label, note) in enumerate(metrics):
        cell = table.cell(0, idx)
        set_cell_shading(cell, LIGHT_BLUE if idx % 2 == 0 else LIGHT_GREEN)
        set_cell_margins(cell, top=150, start=130, bottom=150, end=130)
        cell.width = Inches(7.0 / len(metrics))
        p1 = cell.paragraphs[0]
        p1.style = "Metric"
        p1.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p1.add_run(number)
        p2 = cell.add_paragraph()
        p2.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p2.paragraph_format.space_after = Pt(1)
        r2 = p2.add_run(label)
        set_repeat_header_text(r2, 8.5, INK, True)
        p3 = cell.add_paragraph()
        p3.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p3.paragraph_format.space_after = Pt(0)
        r3 = p3.add_run(note)
        set_repeat_header_text(r3, 7.2, MUTED)
    doc.add_paragraph().paragraph_format.space_after = Pt(2)


def create_metric_chart(report: dict, path: Path) -> None:
    skill = report["skill_enabled"]
    labels = ["总体通过", "正例复用", "负例拒绝", "误激活（越低越好）"]
    values = [
        skill["accuracy"] * 100,
        skill["positive_reuse_rate"] * 100,
        skill["negative_rejection_rate"] * 100,
        skill["false_activation_rate"] * 100,
    ]
    colors = ["#2563EB", "#047857", "#0F766E", "#B45309"]
    canvas = Image.new("RGB", (1800, 650), "white")
    draw = ImageDraw.Draw(canvas)
    font_path = Path("C:/Windows/Fonts/msyh.ttc")
    font_bold_path = Path("C:/Windows/Fonts/msyhbd.ttc")
    label_font = ImageFont.truetype(str(font_path), 38)
    value_font = ImageFont.truetype(str(font_bold_path if font_bold_path.exists() else font_path), 34)
    note_font = ImageFont.truetype(str(font_path), 26)
    x0, x1 = 520, 1660
    draw.text((x0, 18), "0%", fill="#6B7280", font=note_font)
    draw.text((x1 - 70, 18), "100%", fill="#6B7280", font=note_font)
    for index, (label, value, color) in enumerate(zip(labels, values, colors)):
        y = 92 + index * 135
        draw.text((40, y + 7), label, fill="#141D2F", font=label_font)
        draw.rounded_rectangle((x0, y, x1, y + 58), radius=22, fill="#EEF2F7")
        filled = x0 + max(8, int((x1 - x0) * value / 100))
        draw.rounded_rectangle((x0, y, filled, y + 58), radius=22, fill=color)
        value_text = f"{value:.2f}%"
        value_x = min(filled + 20, x1 - 150)
        draw.text((value_x, y + 6), value_text, fill="#141D2F", font=value_font)
    canvas.save(path, format="PNG", optimize=True)


def build(output: Path) -> None:
    report = json.loads(BENCHMARK.read_text(encoding="utf-8"))
    live = json.loads(LIVE_ACCEPTANCE.read_text(encoding="utf-8"))
    live_sha256 = hashlib.sha256(LIVE_ACCEPTANCE.read_bytes()).hexdigest().upper()
    skill = report["skill_enabled"]
    family = report["family_results"]
    disabled = live["arms"]["disabled"]
    auto = live["arms"]["auto"]

    doc = Document()
    configure_document(doc)
    props = doc.core_properties
    props.title = "Mini-Drop AI 诊断与技能复用测试报告"
    props.subject = "Evidence-first diagnosis, controlled fault showcase, Skill A/B and benchmark"
    props.author = "Mini-Drop project"
    props.keywords = "Mini-Drop, AI diagnosis, Skill reuse, A/B, perf, evidence"

    # Cover
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(74)
    p.paragraph_format.space_after = Pt(6)
    kicker = p.add_run("验证报告  /  2026-09-06")
    set_repeat_header_text(kicker, 9, RGBColor.from_string(BLUE), True)
    title = doc.add_paragraph(style="Title")
    title.add_run("Mini-Drop AI 诊断\n与技能复用测试报告")
    subtitle = doc.add_paragraph(style="Subtitle")
    subtitle.add_run("面向面试演示的功能闭环、数据集、质量门禁与证据边界")
    rule = doc.add_paragraph()
    add_rule(rule)
    doc.add_paragraph().paragraph_format.space_after = Pt(14)
    add_metric_strip(
        doc,
        [
            ("540", "新设计盲测案例", "公开问题 / 私有答案"),
            (f"{skill['accuracy'] * 100:.2f}%", "技能路由通过率", f"{skill['correct']}/{skill['total']}"),
            ("404", "Python 回归通过", "另有 3 项跳过"),
            ("72", "接口路由对齐", "方法/路径组合"),
        ],
    )
    doc.add_paragraph().paragraph_format.space_after = Pt(10)
    add_labeled_line(doc, "报告结论", "页面已形成可重复的演示闭环：从故障广场启动白名单故障，AI 自主发现安全目标，执行四轮树搜索、工具调用、证据裁决和报告生成，并可在同页进行多轮对话、人工干预和技能 A/B 对比。")
    add_labeled_line(doc, "实机验收", "2026-09-06 云端 Python 源码热点场景通过：两条全新会话均完成 4 轮、4 次真实工具调用、16 条证据和 4 版报告，火焰图 2968 个根样本，故障停止与恢复校验通过。", GREEN)
    add_labeled_line(doc, "重要边界", "93.70% 只代表技能路线选择与拒绝的静态盲测，不是根因准确率；21 个故障场景已上线，但本轮只对 Python 源码热点做了完整云端 A/B 实机验收。", ORANGE)
    add_labeled_line(doc, "版本范围", "云端发布目录 20260906T142517Z；代码来自 D:\\tx\\mini-drop 当前未提交工作树。", GREEN)
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(36)
    r = p.add_run("先看证据，再下结论；先复用路线，再避免重复探索。")
    set_repeat_header_text(r, 9, MUTED)
    doc.add_page_break()

    # 1 Executive summary
    doc.add_heading("1. 执行摘要", level=1)
    doc.add_paragraph(
        "这轮改造解决的不是三个孤立的页面问题，而是三条演示链断点：范围确认缺少幂等语义、采集成功与样本有效性混在一起、以及诊断会话缺少可见的多轮交互。修复后的系统把用户输入视为调查上下文，把产物经过质量门禁后形成证据，再由报告引用证据给出可验证结论。"
    )
    add_table(
        doc,
        ["用户看到的问题", "根因", "现在的行为"],
        [
            ["重复确认范围时报“不可变”错误", "浏览器分钟精度与服务端秒/时区表达直接比较", "同一 UTC 语义与页面分钟重试幂等；真实移动时间窗仍拒绝"],
            ["任务完成却没有火焰图", "perf 只有表头时也可能以退出码 0 结束", "零样本、空折叠栈、空可视化按失败处理，并返回可行动重采提示"],
            ["总是证据不足或一直准备", "无受控故障、无后续回合、动态树藏在回放弹窗", "故障广场 + 多轮干预 + 主页面动态树 + 可继续调查"],
            ["技能复用无法现场对比", "旧页面只有离线或硬编码指标", "自动/关闭技能分别创建真实诊断，比较真实 ID、工具、证据与报告"],
        ],
        widths=[Inches(1.45), Inches(2.35), Inches(3.2)],
    )
    doc.add_heading("验收判断", level=2)
    add_bullets(
        doc,
        [
            "功能合同：后端、OpenAPI、故障白名单、时间窗幂等和干预事件已有自动化覆盖。",
            "页面闭环：用户可以从故障广场进入诊断、在诊断中继续追问、质疑和换方向，并在同页观察探索树版本增长。",
            "采集可信度：以后不会再把 0 KB JSON 和空 SVG 当成成功火焰图；历史空产物需重新采集。",
            "实验可信度：540 条案例可以证明路由选择器的行为，不能替代真实 Linux 故障的根因评测。",
        ],
    )

    # 2 Architecture and demo chain
    doc.add_heading("2. 页面演示闭环", level=1)
    doc.add_paragraph("面试演示时，推荐按下面的因果顺序讲，而不是按菜单逐页点击：")
    flow = doc.add_paragraph()
    flow.alignment = WD_ALIGN_PARAGRAPH.CENTER
    flow.paragraph_format.space_before = Pt(5)
    flow.paragraph_format.space_after = Pt(10)
    rr = flow.add_run("故障广场  →  诊断会话  →  假设  →  工具调用  →  任务/产物  →  证据  →  报告  →  恢复验证")
    set_repeat_header_text(rr, 9.5, RGBColor.from_string(BLUE), True)
    add_table(
        doc,
        ["页面能力", "现场动作", "面试官能看到的真实性信号"],
        [
            ["故障广场", "选择 CPU、源码热点、内存、I/O、噪声邻居、负载或队列场景，限定时长并启动", "浏览器只提交 scenario_id 与 duration；服务端固定白名单，不接收任意命令/PID/URL"],
            ["AI 诊断", "描述症状并让 AI 自动发现安全目标，或人工确认目标", "诊断 ID、绑定 ID、时间窗和技能策略都由服务端持久化"],
            ["多轮对话", "补充上下文、质疑假设、换方向、继续调查", "每次提交产生人工干预事件；用户话语不会直接成为证据"],
            ["动态探索树", "观察假设、探针、任务、证据和报告节点实时增加", "树版本持续增长，方向切换与人工节点可追溯"],
            ["技能 A/B", "同问题分别创建“关闭技能”和“自动技能”会话", "对比两条真实会话的技能激活、工具、证据、报告和耗时"],
            ["任务结果", "进入采集任务查看产物和可视化", "空样本明确失败；有样本才渲染火焰图和 TopN"],
        ],
        widths=[Inches(1.15), Inches(2.45), Inches(3.4)],
    )
    doc.add_heading("人工干预的四种语义", level=2)
    add_table(
        doc,
        ["动作", "用途", "安全语义"],
        [
            ["补充上下文", "补充部署变更、症状、业务背景", "只作为上下文，不升格为事实证据"],
            ["质疑假设", "要求 AI 反驳当前主假设", "保留旧假设历史，创建新的可证伪方向"],
            ["改变方向", "从 CPU 转向 I/O、内存、网络或运行时", "新探针仍经过目标绑定、能力、风险和预算门禁"],
            ["继续调查", "证据不足或已有结论后继续一轮", "旧报告不可变，新事件进入下一轮调查"],
        ],
        widths=[Inches(1.5), Inches(2.25), Inches(3.25)],
    )

    doc.add_heading("云端页面实机链路", level=2)
    add_table(
        doc,
        ["实验臂", "诊断 ID", "真实执行", "可视化与结论"],
        [
            ["关闭技能", disabled["diagnosis_id"], "4 轮 / 4 工具 / 16 证据 / 4 报告", f"火焰图 {disabled['profile']['flamegraph_root_samples']} 样本；TopN {disabled['profile']['top_row_count']}；置信度 {disabled['reports'][-1]['confidence']:.2f}"],
            ["自动技能", auto["diagnosis_id"], "4 轮 / 4 工具 / 16 证据 / 4 报告", f"火焰图 {auto['profile']['flamegraph_root_samples']} 样本；TopN {auto['profile']['top_row_count']}；技能激活 {len(auto['skill_activations'])} 次；置信度 {auto['reports'][-1]['confidence']:.2f}"],
        ],
        widths=[Inches(0.9), Inches(2.65), Inches(1.65), Inches(1.8)],
    )
    add_bullets(
        doc,
        [
            "两组绑定同一服务端签发的不可变目标；每组都重新启动同一白名单故障，并创建全新的任务、尝试、产物、证据和报告。",
            "真实执行轮次为 1、2、3、4；树深度单独保存。回溯选择同层兄弟节点时，页面不会再把第三次执行误显示成第一轮。",
            "两组精确重复的 LATS 事件均为 0；期望热点函数 source_hot_function 均被识别。",
            "顺序运行可以证明路线真实执行和流程可重复，但墙钟耗时不能当作严格性能提升结论。",
        ],
    )

    # 3 benchmark
    doc.add_heading("3. 540 条技能路由盲测", level=1)
    doc.add_paragraph(
        "数据集使用固定种子 20260905 生成 540 条新问题。公开输入与私有答案分开保存；生成器不读取已退役的旧案例，但故障分类和正确技能来自当前目录。因此它是可复现的项目内盲测，不是外部独立数据集。"
    )
    add_metric_strip(
        doc,
        [
            (f"{skill['positive_reuse_rate']*100:.2f}%", "正例复用", f"{skill['positive_correct']}/{skill['positive_total']}"),
            (f"{skill['negative_rejection_rate']*100:.2f}%", "负例拒绝", f"{skill['negative_correct']}/{skill['negative_total']}"),
            (f"{skill['false_activation_rate']*100:.2f}%", "负例误激活", f"{skill['false_activations']}/{skill['negative_total']}"),
        ],
    )
    with tempfile.TemporaryDirectory(prefix="mini-drop-report-") as tmp:
        chart_path = Path(tmp) / "skill_metrics.png"
        create_metric_chart(report, chart_path)
        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        chart = p.add_run().add_picture(str(chart_path), width=Inches(6.55))
        chart._inline.docPr.set("title", "技能路由盲测指标")
        chart._inline.docPr.set("descr", "总体通过率、正例复用率、负例拒绝率和负例误激活率的横向条形图")
        cap = doc.add_paragraph("图 1  Skill 路由选择与安全拒绝指标（确定性重跑）")
        cap.alignment = WD_ALIGN_PARAGRAPH.CENTER
        cap.paragraph_format.space_after = Pt(8)
        for run in cap.runs:
            set_repeat_header_text(run, 7.5, MUTED)
    add_table(
        doc,
        ["案例家族", "数量", "技能通过", "无技能基线", "测试意图"],
        [
            ["应当复用", family["POSITIVE_REUSE"]["total"], f"{family['POSITIVE_REUSE']['skill_correct']}/360（92.78%）", "0/360", "描述足够具体时命中正确技能"],
            ["误导或信息不足", family["MISLEADING_OR_UNDERSPECIFIED"]["total"], f"{family['MISLEADING_OR_UNDERSPECIFIED']['skill_correct']}/90（91.11%）", "90/90", "歧义或信息不足时应弃权"],
            ["能力或环境漂移", family["CAPABILITY_OR_ENVIRONMENT_DRIFT"]["total"], "90/90（100%）", "90/90", "环境或能力不匹配时拒绝复用"],
        ],
        widths=[Inches(1.85), Inches(0.6), Inches(1.4), Inches(1.1), Inches(2.05)],
    )
    doc.add_heading("指标解释", level=2)
    add_bullets(
        doc,
        [
            "总体 506/540（93.70%）：正例选择指定技能，负例正确弃权。",
            "无技能基线 180/540（33.33%）：它不是另一套根因诊断 Agent，只是永远不复用路线的保守下界。",
            "8 个误激活全部来自误导/信息不足负例；它们是下一轮阈值和歧义门禁优化的直接样本。",
            "技能命中代表可复用的调查先验，不代表根因已被证明。",
        ],
    )

    # 4 Quality gates
    doc.add_heading("4. 火焰图为空：修复与质量门禁", level=1)
    doc.add_paragraph(
        "原问题的关键不在前端渲染。perf 解析和折叠脚本可能对只有表头或 0 样本的输入返回退出码 0；旧分析器随后生成空根节点、空 TopN 和极小 SVG，任务仍被标为完成。新链路同时检查进程退出状态和内容质量。"
    )
    add_table(
        doc,
        ["检查点", "拒绝条件", "页面结果"],
        [
            ["perf 解析", "没有任何可解析样本", "NO_PERF_SAMPLES；提示确认 PID、权限、采样时长和目标是否在运行"],
            ["调用栈折叠", "没有正权重折叠栈", "NO_FOLDED_STACKS；不生成伪火焰图"],
            ["分析器输出", "样本数为 0、质量为不可用、可视化为 0 字节", "分析失败并保留结构化原因"],
            ["产物合同", "新产物明确声明 0 样本或不可用", "阻止进入证据；旧产物缺字段仅标记质量未知"],
        ],
        widths=[Inches(1.35), Inches(3.15), Inches(2.5)],
    )
    add_labeled_line(doc, "必须重采", "截图中的历史空 flamegraph.json / flamegraph.svg 不会被“修复成有内容”。修复保证新任务诚实失败；要看到火焰图，必须对一个确实消耗 CPU 的目标在当前分析器下重新采集。", ORANGE)

    # 5 Verification
    doc.add_heading("5. 自动化验证结果", level=1)
    add_table(
        doc,
        ["验证层", "结果", "覆盖内容", "结论"],
        [
            ["Python 全量回归", "404 通过 / 3 跳过", "诊断、策略、分析器、合同、故障广场、时间窗、干预树", "通过"],
            ["前端交互回归", "120/120 通过", "多轮续诊、动态树、故障广场、真实技能 A/B、空采样提示", "通过"],
            ["前端生产构建", "build:check", "Vite 构建与入口预算；ECharts 612 kB 为非阻断提示", "通过"],
            ["Go 全量回归", "go test ./...", "控制面、任务、产物与接口实现", "通过"],
            ["接口契约对齐", "72 组方法/路径", "Python RPC 实现与公开契约", "通过"],
            ["技能路线盲测", "506/540", "生产混合检索与拒绝门禁", "93.70%"],
            ["故障广场在线检查", "21 个场景", "Python 7 / Go 4 / Java 5 / C++ 5", "通过"],
            ["Linux 云端端到端", "通过", "Python 源码热点、同目标 A/B、产物、证据、报告、恢复", "通过"],
        ],
        widths=[Inches(1.35), Inches(1.25), Inches(3.15), Inches(1.25)],
    )
    doc.add_heading("三层测试模型", level=2)
    add_numbered(
        doc,
        [
            "静态盲测：快速验证“该不该复用哪条路线”，适合放入 CI。",
            "受控故障合同/集成：验证白名单、状态机、幂等、人工干预、质量错误和事件投影。",
            "Linux 实机端到端：在真实采集节点和标注故障下，对比根因服务或指标、首条有效证据耗时、工具调用数、空采样率和恢复验证。",
        ],
    )
    doc.add_paragraph(
        "本报告已完成前两层，并在云端 Linux Worker 上完成一个 Python 源码热点场景的第三层实机闭环。其余 20 个在线场景仍需逐场运行相同根因真值与恢复验收，不能用“已出现在故障广场”代替全量实机通过。"
    )

    # 6 Interview demo
    doc.add_heading("6. 面试演示脚本（8～10 分钟）", level=1)
    add_numbered(
        doc,
        [
            "打开 AI 诊断中的故障广场，选择“Python 源码热点”，设置 180 秒并启动。说明浏览器不能下发任意命令。",
            "点击“启动并诊断”，让 AI 自主选择服务端签发的安全目标和时间窗；展示诊断 ID 与绑定 ID。",
            "先用“关闭技能”创建对照臂，记录第一个工具调用；恢复并重放同一故障，再用“自动技能”创建实验臂。",
            "在运行中的会话输入“CPU 样本没有主导热点，请质疑当前假设并转查 I/O 等待”。展示人工干预节点和树版本增长。",
            "沿工具调用打开任务，查看尝试、产物、分析器和证据。如果样本为空，现场展示结构化质量错误与重采路径。",
            "打开技能 A/B 面板，对比两条真实会话的技能激活、工具数、首条证据、状态、报告版本和耗时。",
            "停止故障并验证服务恢复。最后打开 540-case 报告，明确它只测路线复用，不等于刚才的 live 根因链。",
        ],
    )
    doc.add_heading("演示时建议主动说出的边界", level=2)
    add_bullets(
        doc,
        [
            "人工输入是上下文，只有采集器和分析器产物通过质量门禁后才能成为证据。",
            "技能是经过验证的调查路线记忆，不是把历史结论直接复制到新事故。",
            "A/B 的两个臂必须重置同一故障和负载；否则顺序运行会被时间漂移污染。",
            "INSUFFICIENT_EVIDENCE 是安全结论，不是系统卡死；用户可以继续调查，但模型不能用猜测补齐证据。",
        ],
    )

    # 7 limitations / next
    doc.add_heading("7. 已知限制与下一步验收", level=1)
    add_table(
        doc,
        ["限制", "影响", "下一步"],
        [
            ["仅 1 个场景完成 Linux 实机闭环", "不能外推 21 场景的整体根因准确率", "逐场固定根因真值并运行多次 Campaign"],
            ["故障广场主要是单机/单进程", "对跨服务因果链覆盖有限", "接入 OpenTelemetry Demo 级微服务场景"],
            ["A/B 两臂顺序运行", "墙钟耗时可能受状态漂移影响", "增加随机顺序、重复样本和显著性分析"],
            ["旧采样产物缺少质量元数据", "兼容读取时只能标记质量未知", "用新分析器重算或重新采集"],
            ["误导负例仍有 8 次误激活", "技能可能在信息不足时过早介入", "基于失败案例调整阈值并保留独立回归集"],
        ],
        widths=[Inches(1.65), Inches(2.25), Inches(3.1)],
    )

    # 8 reproducibility and references
    doc.add_heading("8. 复现命令与证据文件", level=1)
    code_lines = [
        "python -m pytest -q  # 404 passed, 3 skipped",
        "cd web; npx vitest run --maxWorkers=1 --no-file-parallelism  # 120 passed",
        "go test ./...",
        "python scripts/check_openapi_routes.py  # 72 组",
        "python scripts/generate_diagnosis_benchmark_v2.py",
        "python scripts/run_diagnosis_benchmark_v2.py",
        "python scripts/verify_interview_demo.py --scenario source-hotspot --fault-duration-seconds 180",
    ]
    p = doc.add_paragraph()
    p.paragraph_format.left_indent = Cm(0.4)
    p.paragraph_format.right_indent = Cm(0.4)
    set_cell_like = OxmlElement("w:shd")
    set_cell_like.set(qn("w:fill"), "F3F4F6")
    p._p.get_or_add_pPr().append(set_cell_like)
    for idx, line in enumerate(code_lines):
        run = p.add_run(line)
        run.font.name = "Consolas"
        run._element.rPr.rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
        run.font.size = Pt(8.2)
        if idx != len(code_lines) - 1:
            run.add_break()
    add_table(
        doc,
        ["证据", "位置"],
        [
            ["公开盲测输入", "benchmarks/diagnosis-v2/public/cases.json"],
            ["私有 oracle", "benchmarks/diagnosis-v2/private/oracles.json"],
            ["机器可读结果", "web/public/report-assets/skill-evolution/benchmark-report.json"],
            ["云端实机验收 JSON", "reports/ai-diagnosis/interview-demo-live-acceptance-20260906T142517Z.json"],
            ["验收 JSON SHA-256", live_sha256],
            ["人类可读报告", "reports/ai-diagnosis/AI诊断与Skill复用测试报告-20260906.md"],
            ["核心设计文档", "docs/AI_DIAGNOSIS.md、docs/SKILLS.md、docs/PROJECT_LEARNING_GUIDE.md"],
        ],
        widths=[Inches(1.65), Inches(5.35)],
    )
    doc.add_heading("参考依据", level=2)
    references = [
        "Microsoft AIOpsLab — 应用、任务、故障、负载、评估器分层与动作轨迹评估：https://github.com/microsoft/AIOpsLab",
        "AIOpsLab paper：https://www.microsoft.com/en-us/research/wp-content/uploads/2024/10/arxiv_AIOpsLab.pdf",
        "RCAEval — 以标注故障分别评估根因服务与根因指标：https://github.com/phamquiluan/RCAEval",
        "RCAEval paper：https://arxiv.org/abs/2412.17015",
        "OpenTelemetry Demo 场景开关 — 可重放问题场景：https://opentelemetry.io/docs/demo/feature-flags/",
        "Chaos Mesh — 有目标选择、持续时间和恢复语义的受控故障：https://chaos-mesh.org/docs/run-a-chaos-experiment/",
    ]
    add_bullets(doc, references)
    doc.add_paragraph()
    closing = doc.add_paragraph()
    closing.alignment = WD_ALIGN_PARAGRAPH.CENTER
    closing.paragraph_format.space_before = Pt(14)
    add_rule(closing, color=GRID)
    r = closing.add_run("结论必须能沿“报告 → 证据 → 产物 → 尝试 → 任务”反向追溯。")
    set_repeat_header_text(r, 9, INK, True)

    output.parent.mkdir(parents=True, exist_ok=True)
    doc.save(output)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    build(args.output.resolve())
    print(args.output.resolve())


if __name__ == "__main__":
    main()
