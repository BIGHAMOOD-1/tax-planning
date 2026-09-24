"""数字版 PDF 解析：从年报/公告抽取**候选事实**（必须经人工确认）。

定位：从文档获得**可追溯 Evidence**，不是做 PDF 产品。
- 仅数字版 PDF（有文本层）；不做 OCR。
- 抽取结果一律为 candidate，带页码/表格定位；人工确认后才进 Evidence Pool。
- 单位统一归一到"元"，原始文本保留在 raw_context。
- **来源优先级最低**：提取证据的优先级低于直接输入/结构化/数据库（见 contract.SOURCE_PRIORITY）。

抽取规则（2.2 收紧）：
1. 文本抽取**以行为界**：关键词与数值必须在同一行；
2. 金额事实**量级校验**（≥ 1 万元），过滤百分比/序号/页码噪声；
3. 表格抽取优先于文本；同值去重保留表格版；
4. 每个事实类型按"评分"限流 top-N。
"""
from __future__ import annotations

import re
from pathlib import Path

from src.common import get_logger
from src.evidence.contract import Evidence, infer_value_kind, make_evidence

log = get_logger()

# 常见财报事实的关键词（中文年报口径）
DEFAULT_KEYWORDS: dict[str, list[str]] = {
    "rd_spend_sum": ["研发投入合计", "研发投入总额", "研发投入", "研发费用合计"],
    "is_rd_expense": ["研发费用"],
    "rd_person": ["研发人员数量", "研发人员"],
    "gov_subsidy_total": ["计入当期损益的政府补助", "政府补助"],
    "is_income_tax": ["所得税费用"],
    "is_interest_expense": ["利息支出", "利息费用"],
    "is_revenue": ["营业收入"],
    "bs_total_assets": ["资产总计", "总资产"],
    "bs_total_liabilities": ["负债合计", "总负债"],
    "bs_fixed_assets": ["固定资产"],
}

UNIT_FACTORS = {"元": 1.0, "万元": 1e4, "万": 1e4, "亿元": 1e8, "亿": 1e8, "千元": 1e3}
_NUM_RE = re.compile(r"(-?\d[\d,]*(?:\.\d+)?)\s*(亿元|万元|千元|亿|万|元)?")

MIN_AMOUNT = 1.0e4       # 金额下限：1 万元（过滤百分比/序号/页码）
MAX_AMOUNT = 1.0e14      # 金额上限：100 万亿（防御异常）
MAX_PER_TYPE = 6         # 每类事实保留候选上限


def _to_amount(text: str):
    """从文本解析金额并归一到元；返回 (value, unit_original) 或 (None, None)。"""
    m = _NUM_RE.search(str(text))
    if not m:
        return None, None
    try:
        v = float(m.group(1).replace(",", ""))
    except ValueError:
        return None, None
    unit = m.group(2) or ""
    return v * UNIT_FACTORS.get(unit, 1.0), unit


def _plausible(kind: str, value) -> bool:
    """量级/形态校验：过滤明显不是该事实的数值。"""
    if value is None:
        return False
    try:
        v = float(value)
    except (TypeError, ValueError):
        return False
    if kind == "count":
        return 1 <= abs(v) <= 1e7 and float(v).is_integer()
    if kind == "amount":
        return MIN_AMOUNT <= abs(v) <= MAX_AMOUNT
    return True


def _score(method: str, unit: str, kind: str, value) -> float:
    """给候选事实打分（0.45 起）：表格 > 文本，有单位、量级合理再加分，封顶 0.95。"""
    s = 0.45
    if unit:
        s += 0.15
    if method == "pdf_table":
        s += 0.25
    elif method == "pdf_text":
        s += 0.05
    if _plausible(kind, value):
        s += 0.10
    return round(min(s, 0.95), 2)


def _seq_factory():
    """返回自增序号生成器，用于给抽取的事实分配稳定序号。"""
    i = 0

    def nxt():
        nonlocal i
        i += 1
        return i
    return nxt


def _mk(seq, stock_code, year, fact_type, value, source_ref, method,
        raw_context, page, confidence, caliber="", doc_type="annual_report",
        document_id="", document_date="", quote="") -> Evidence:
    """构造一条候选 Evidence：统一单位为元（金额类）并保留页码/原文定位。"""
    kind = infer_value_kind(fact_type, value)
    ev = make_evidence(seq, stock_code, year, fact_type, value,
                       unit=("元" if kind == "amount" else ""),
                       source_type="user_document", doc_type=doc_type,
                       document_id=document_id, document_date=document_date,
                       extraction_method=method, extraction_confidence=confidence,
                       caliber=caliber, value_kind=kind,
                       raw_context=f"P{page} | {raw_context}",
                       source_ref=source_ref, location=f"P{page}", quote=quote)
    return ev


def _iter_text_facts(text: str, keywords: dict):
    """文本抽取：以行为界，关键词与数值必须同一行。"""
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        for fact_type, kws in keywords.items():
            for kw in kws:
                pos = line.find(kw)
                if pos < 0:
                    continue
                seg = line[pos + len(kw): pos + len(kw) + 40]
                val, unit = _to_amount(seg)
                if val is None:
                    continue
                kind = infer_value_kind(fact_type, val)
                if not _plausible(kind, val):
                    continue
                yield fact_type, val, unit, kind, line[:100]


def _iter_table_facts(table, keywords: dict):
    """表格抽取：命中关键词单元格右侧第一个数值单元格。"""
    for row in table or []:
        cells = [str(c).strip() for c in (row or []) if c is not None and str(c).strip()]
        if len(cells) < 2:
            continue
        for fact_type, kws in keywords.items():
            hit = next((i for i, c in enumerate(cells) if any(kw in c for kw in kws)), None)
            if hit is None:
                continue
            for cell in cells[hit + 1:]:
                val, unit = _to_amount(cell)
                if val is None:
                    continue
                kind = infer_value_kind(fact_type, val)
                if not _plausible(kind, val):
                    continue
                yield fact_type, val, unit, kind, " ".join(cells)[:100]
                break


_CALIBER_HEADING_RE = re.compile(r"(合并|母公司).{0,4}(资产负债表|利润表|现金流量表|所有者权益变动表)")


def _infer_caliber(line: str, current: str) -> str:
    """确定性口径兜底：从"合并/母公司…报表"标题推断口径。"""
    m = _CALIBER_HEADING_RE.search(str(line))
    if not m:
        return current
    return "母公司" if m.group(1) == "母公司" else "合并"


def extract_pdf_facts(pdf_path: str | Path, stock_code: str, year: int,
                      source_ref: str = "", keywords: dict | None = None,
                      max_pages: int | None = None, doc_type: str = "annual_report",
                      document_id: str = "", document_date: str = "") -> list[Evidence]:
    """抽取候选事实。返回 candidate Evidence（未入库）。"""
    import pdfplumber

    keywords = keywords or DEFAULT_KEYWORDS
    seq = _seq_factory()
    out: list[Evidence] = []
    ref = source_ref or Path(pdf_path).name
    n_pages = 0
    caliber = ""

    with pdfplumber.open(str(pdf_path)) as pdf:
        pages = pdf.pages[:max_pages] if max_pages else pdf.pages
        n_pages = len(pages)
        for pno, page in enumerate(pages, 1):
            try:
                text = page.extract_text() or ""
            except Exception:  # noqa: BLE001
                text = ""
            for line in text.splitlines():
                caliber = _infer_caliber(line, caliber)
            for fact_type, val, unit, kind, raw in _iter_text_facts(text, keywords):
                out.append(_mk(seq(), stock_code, year, fact_type, val,
                               f"{ref} P{pno}", "pdf_text", f"{fact_type}: {raw}",
                               pno, _score("pdf_text", unit, kind, val), caliber,
                               doc_type, document_id, document_date, quote=raw))
            try:
                tables = page.extract_tables() or []
            except Exception:  # noqa: BLE001
                tables = []
            for ti, table in enumerate(tables, 1):
                for fact_type, val, unit, kind, raw in _iter_table_facts(table, keywords):
                    out.append(_mk(seq(), stock_code, year, fact_type, val,
                                   f"{ref} P{pno} T{ti}", "pdf_table", raw,
                                   pno, _score("pdf_table", unit, kind, val), caliber,
                                   doc_type, document_id, document_date, quote=raw))

    # 去重（同类型同值保留高分/表格版）-> 每类型限流 top-N
    best: dict[tuple, Evidence] = {}
    for e in out:
        # 同事实同值只留置信度最高者（表格版通常高于文本版）
        key = (e.fact_type, round(float(e.value_num or 0), 2))
        cur = best.get(key)
        if cur is None or e.extraction_confidence > cur.extraction_confidence:
            best[key] = e
    per_type: dict[str, list[Evidence]] = {}
    for e in best.values():
        per_type.setdefault(e.fact_type, []).append(e)
    dedup: list[Evidence] = []
    for ft, evs in per_type.items():
        # 每类最多保留 MAX_PER_TYPE 条，防止同义词命中造成候选爆炸
        evs.sort(key=lambda x: -x.extraction_confidence)
        dedup.extend(evs[:MAX_PER_TYPE])
    dedup.sort(key=lambda x: (x.fact_type, -x.extraction_confidence))
    log.info("PDF 抽取：%s 页 -> %s 条候选事实（去重限流前 %s 条）",
             n_pages, len(dedup), len(out))
    return dedup


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 3:
        for e in extract_pdf_facts(sys.argv[1], sys.argv[2], int(sys.argv[3])):
            print(f"{e.fact_type:<20} {e.value_num:>18,.0f} {e.unit} | "
                  f"{e.source_ref} | {e.extraction_method} | conf={e.extraction_confidence}")
