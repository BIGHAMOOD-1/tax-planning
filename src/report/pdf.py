"""PDF 报告导出（竞赛要求：结构化输出需含 PDF）。

用 reportlab 把 Markdown 报告 / 证据链 / 意见书渲染为 PDF。
- 中文用系统字体（simhei.ttf / msyh.ttc / simsun.ttc），无需联网、无需 GTK；
- 轻量 Markdown 解析：标题 / 段落 / 引用 / 列表 / 表格 / 分隔线 / 粗斜体 / 链接；
- 每页页脚固定标注「AI 生成」，满足伦理合规要求。

实现约定：
    - 字体惰性注册一次（_ensure_font），避免重复注册开销；
    - Markdown 解析为「逐行状态机」：连续 | 行聚合为表格，其余按行内语法处理；
    - 所有文本先 HTML 转义再套 reportlab 标记，防止正文内容破坏排版。
"""
from __future__ import annotations

import io
import re
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    HRFlowable,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from src.report.export import render_chain_markdown, render_opinion_markdown, load_report_markdown
from src.report.notice import AI_BADGE

_FONT = "CJK"
_registered = False

# 依次尝试：黑体（单字体 TTF，最稳）→ 微软雅黑 → 宋体 → Linux 文泉驿
_FONT_CANDIDATES = [
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\simsun.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/System/Library/Fonts/PingFang.ttc",
]

_MARGIN_X = 18 * mm
_MARGIN_TOP = 18 * mm
_MARGIN_BOTTOM = 16 * mm
_AVAIL = A4[0] - 2 * _MARGIN_X   # 正文可用宽度，表格列宽按此均分


def _ensure_font() -> None:
    """注册一个可用的中文字体；已注册则直接返回。

    依次尝试候选字体，首个成功即锁定；全部失败抛 RuntimeError，
    让调用方明确知道 PDF 不可用，而不是产出乱码。
    """
    global _registered
    if _registered:
        return
    for path in _FONT_CANDIDATES:
        p = Path(path)
        if not p.exists():
            continue
        try:
            if p.suffix.lower() == ".ttc":  # 字体集合需指定子字体
                pdfmetrics.registerFont(TTFont(_FONT, str(p), subfontIndex=0))
            else:
                pdfmetrics.registerFont(TTFont(_FONT, str(p)))
            pdfmetrics.registerFontFamily(_FONT, normal=_FONT, bold=_FONT, italic=_FONT, boldItalic=_FONT)
            _registered = True
            return
        except Exception:  # noqa: BLE001 单个字体失败则尝试下一个
            continue
    raise RuntimeError("未找到可用的中文字体（simhei/msyh/simsun 均不可用）")


def _styles() -> dict:
    """构建各层级段落样式；wordWrap="CJK" 保证中文按字断行不溢出。"""
    def ps(name, size, leading, **kw):
        return ParagraphStyle(name, fontName=_FONT, fontSize=size, leading=leading, wordWrap="CJK", **kw)

    return {
        "body": ps("body", 9.5, 14, spaceAfter=3),
        "bullet": ps("bullet", 9.5, 14, leftIndent=11, firstLineIndent=-9, spaceAfter=2),
        "quote": ps("quote", 9, 13, textColor=colors.HexColor("#555f6e"), leftIndent=8, spaceAfter=3),
        "cell": ps("cell", 8, 11),
        "cellhead": ps("cellhead", 8, 11, textColor=colors.HexColor("#1f2a44")),
        "h1": ps("h1", 18, 24, spaceBefore=2, spaceAfter=8, textColor=colors.HexColor("#16233d")),
        "h2": ps("h2", 14, 19, spaceBefore=10, spaceAfter=5, textColor=colors.HexColor("#1f3a63")),
        "h3": ps("h3", 11.5, 16, spaceBefore=8, spaceAfter=4, textColor=colors.HexColor("#2a4a78")),
        "h4": ps("h4", 10.5, 15, spaceBefore=6, spaceAfter=3, textColor=colors.HexColor("#33507d")),
    }


def _inline(text: str) -> str:
    """把一行 Markdown 行内语法转为 reportlab 段落标记（先转义再替换）。"""
    # 先转义 & < >，再替换 Markdown 标记；顺序不可颠倒，否则会破坏已生成的标签
    t = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)
    t = re.sub(r"(?<!\*)\*(?!\s)(.+?)(?<!\s)\*(?!\*)", r"<i>\1</i>", t)
    t = re.sub(r"`(.+?)`", r"\1", t)
    t = re.sub(r"\[(.+?)\]\((.+?)\)", r'<link href="\2">\1</link>', t)
    return t


def _is_sep_row(cells: list[str]) -> bool:
    """判断是否为 Markdown 表格的 |---|---| 对齐行（单元格仅含 - 和 :）。"""
    return bool(cells) and all(c and set(c) <= set("-:") for c in cells)


def _make_table(block: list[str], styles: dict) -> Table | None:
    """把连续的 `| ... |` 行解析为 reportlab 表格。

    处理：去掉首尾 | 后按 | 切分；跳过第 2 行的 |---| 对齐行；列数不足补空。
    """
    rows: list[list[str]] = []
    for idx, raw in enumerate(block):
        cells = [c.strip() for c in raw.strip("|").split("|")]
        if idx == 1 and _is_sep_row(cells):
            continue  # 跳过 |---|---| 对齐行
        rows.append(cells)
    if not rows:
        return None
    ncol = max(len(r) for r in rows)
    rows = [r + [""] * (ncol - len(r)) for r in rows]   # 列数对齐：短行补空，避免越界

    # 逐单元格构造 Paragraph（支持行内 Markdown 与自动换行）
    data = []
    for ri, r in enumerate(rows):
        st = styles["cellhead"] if ri == 0 else styles["cell"]   # 首行表头样式
        data.append([Paragraph(_inline(c), st) for c in r])

    w = _AVAIL / ncol
    t = Table(data, colWidths=[w] * ncol, repeatRows=1)   # repeatRows=1 让表头跨页重复
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef2fb")),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#d0d6e0")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    return t


def _md_to_flowables(md: str, styles: dict) -> list:
    """极简 Markdown → reportlab 流式元素。"""
    flow: list = []
    lines = md.split("\n")
    n = len(lines)
    i = 0
    while i < n:
        s = lines[i].strip()
        if not s:
            # 空行转为小间距，保留段落呼吸感
            flow.append(Spacer(1, 3))
            i += 1
            continue

        if s.startswith("|"):  # 表格块
            block = []
            # 连续 | 行视为一个表格整体，交给 _make_table 一次性解析
            while i < n and lines[i].strip().startswith("|"):
                block.append(lines[i].strip())
                i += 1
            tbl = _make_table(block, styles)
            if tbl is not None:
                flow.append(tbl)
                flow.append(Spacer(1, 6))
            continue

        if re.fullmatch(r"-{3,}", s):  # 分隔线
            # 3 个以上短横线渲染为水平分隔线
            flow.append(HRFlowable(width="100%", thickness=0.6,
                                   color=colors.HexColor("#c9cfda"), spaceBefore=3, spaceAfter=6))
            i += 1
            continue

        m = re.match(r"^(#{1,6})\s+(.*)$", s)  # 标题
        if m:
            lvl = min(len(m.group(1)), 4)   # 仅定义到 h4，更深标题并入 h4 防止 KeyError
            flow.append(Paragraph(_inline(m.group(2)), styles[f"h{lvl}"]))
            i += 1
            continue

        if s.startswith(">"):  # 引用
            # 引用块去掉前导 > 后用引用样式（灰色）渲染
            flow.append(Paragraph(_inline(s.lstrip(">").strip()), styles["quote"]))
            i += 1
            continue

        m = re.match(r"^[-*+]\s+(.*)$", s)  # 无序列表
        if m:
            # 统一用「•」符号，避免不同 Markdown 源符号不一致
            flow.append(Paragraph("• " + _inline(m.group(1)), styles["bullet"]))
            i += 1
            continue

        m = re.match(r"^(\d+)\.\s+(.*)$", s)  # 有序列表
        if m:
            # 保留原编号，保证报告中的步骤序号与内容对应
            flow.append(Paragraph(f"{m.group(1)}. " + _inline(m.group(2)), styles["bullet"]))
            i += 1
            continue

        # 未命中以上任何标记 → 当作普通正文段落
        flow.append(Paragraph(_inline(s), styles["body"]))  # 普通段落
        i += 1
    return flow

def _footer(canvas, doc) -> None:
    """页脚：AI 生成声明 + 页码。"""
    # 每页固定标注 AI 生成与免责声明，满足伦理合规要求
    canvas.saveState()
    canvas.setFont(_FONT, 7.5)
    canvas.setFillColor(colors.HexColor("#8a93a3"))
    canvas.drawString(_MARGIN_X, 9 * mm, f"{AI_BADGE} · 仅供参考 · 不构成专业意见")
    canvas.drawRightString(A4[0] - _MARGIN_X, 9 * mm, f"第 {doc.page} 页")
    canvas.restoreState()


def pdf_from_markdown(md: str, title: str = "税务分析报告") -> bytes:
    """把 Markdown 文本渲染为 PDF 字节流。

    参数：md Markdown 文本；title PDF 文档标题（元信息）。
    返回：PDF 字节流；首页与后续页均渲染页脚（AI 声明 + 页码）。
    """
    _ensure_font()
    styles = _styles()
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=_MARGIN_X, rightMargin=_MARGIN_X,
        topMargin=_MARGIN_TOP, bottomMargin=_MARGIN_BOTTOM,
        title=title, author="智能税务筹划系统", creator="AI 生成",
    )
    # 首页与后续页共用页脚回调，保证每页都带 AI 声明
    doc.build(_md_to_flowables(md, styles), onFirstPage=_footer, onLaterPages=_footer)
    return buf.getvalue()


def pdf_from_report(code: str, year: int) -> bytes | None:
    """整份分析报告 PDF（读取 report.md）。"""
    # 报告不存在时返回 None，由调用方决定回退策略
    md = load_report_markdown(code, year)
    if not md:
        return None
    return pdf_from_markdown(md, title=f"{str(code).zfill(6)}_{year}_税务筹划分析报告")


def pdf_from_chain(doc: dict) -> bytes:
    """单方向证据链 PDF。"""
    # 复用证据链 Markdown 渲染，再统一转 PDF
    return pdf_from_markdown(render_chain_markdown(doc),
                             title=f"{doc.get('direction')}_证据链报告")


def pdf_from_opinion(doc: dict, opinion: dict) -> bytes:
    """单方向合规税务意见书 PDF。"""
    return pdf_from_markdown(render_opinion_markdown(doc, opinion),
                             title=f"{doc.get('direction')}_税务意见")
