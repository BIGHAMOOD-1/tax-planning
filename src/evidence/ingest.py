"""外部数据摄取：三类输入 -> Evidence。

三类输入（用户约定）：
1. 直接输入     source_type=manual          （最可信，可直接进 Profile）
2. 结构化数据   source_type=user_structured （CSV/Excel，程序校验后可直接进 Profile）
3. 文本数据     source_type=user_document   （年报/演讲稿/股东会记录…，入 RAG，不强制转 Profile）

文件/文本不直接进 Agent；一律先转成 Evidence，再经采用规则（resolution）进入 Profile。
"""
from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from src.common import get_logger
from src.evidence.contract import Evidence, infer_value_kind, make_evidence, normalize_source_type

log = get_logger()

_STOCK_RE = re.compile(r"^\d{6}$")


def _mk(seq: int, stock_code, year, fact_type, value, **kw) -> Evidence:
    return make_evidence(seq, stock_code, year, fact_type, value, **kw)


def _s(v) -> str:
    """把 None / NaN 统一成空字符串（CSV/Excel 空单元格常为 NaN）。"""
    if v is None:
        return ""
    try:
        if v != v:
            return ""
    except Exception:  # noqa: BLE001
        pass
    return str(v)


# ---------------------------------------------------------------- 校验

def validate_evidence(ev: Evidence) -> list[str]:
    """结构化/文档证据的程序校验（不阻断，只报问题）。"""
    issues: list[str] = []
    if ev.stock_code and not _STOCK_RE.match(ev.stock_code):
        issues.append(f"股票代码不规范：{ev.stock_code}")
    if ev.report_year is not None and not (1990 <= int(ev.report_year) <= 2100):
        issues.append(f"报告年度越界：{ev.report_year}")
    if ev.value_kind == "amount" and not ev.unit:
        issues.append("金额事实缺少单位")
    if ev.linked_field is None:
        issues.append(f"事实类型未映射（unmapped）：{ev.fact_type}")
    return issues


def validate_batch(evs: list[Evidence], source: str = "") -> int:
    n = 0
    for e in evs:
        for msg in validate_evidence(e):
            n += 1
            log.warning("[校验] %s %s：%s", source, e.fact_type, msg)
    return n


# ---------------------------------------------------------------- 1) 直接输入

def manual_fact(stock_code: str, year: int | None, fact_type: str, value,
                unit: str = "", caliber: str = "", source_ref: str = "用户直接输入",
                raw_context: str = "", company_name: str = "", report_year: int | None = None,
                quote: str = "", location: str = "", source_type: str = "manual") -> Evidence:
    """用户直接输入：最可信，自动 confirmed，可直接进 Profile。

    source_type 可覆盖（如测试/CSMAR 基准），非 manual 时不自动 confirmed。
    """
    return _mk(1, stock_code, year, fact_type, value, unit=unit, caliber=caliber,
               source_type=source_type, extraction_method="manual", source_ref=source_ref,
               raw_context=raw_context or str(value), company_name=company_name,
               report_year=report_year if report_year is not None else year,
               quote=quote, location=location)


# ---------------------------------------------------------------- 2) 结构化数据

def ingest_long(df: pd.DataFrame, code_col: str, year_col: str, type_col: str,
                value_col: str, unit_col: str | None = None, caliber_col: str | None = None,
                period: str = "年度", source_type: str = "user_structured", source_ref: str = "",
                extract_method: str = "csv", company_col: str | None = None,
                import_mode: str = "supplement") -> list[Evidence]:
    """长表：每行一条事实。"""
    out: list[Evidence] = []
    for i, r in enumerate(df.to_dict("records"), 1):
        out.append(_mk(
            i, r.get(code_col), r.get(year_col), str(r.get(type_col)), r.get(value_col),
            unit=_s(r.get(unit_col)) if unit_col else "",
            caliber=_s(r.get(caliber_col)) if caliber_col else "",
            period=period, source_type=source_type, import_mode=import_mode,
            source_ref=f"{source_ref}#row{i}", extraction_method=extract_method,
            raw_context=str({k: r.get(k) for k in df.columns}),
            company_name=_s(r.get(company_col)) if company_col else "",
        ))
    validate_batch(out, source_ref)
    return out


def ingest_wide(df: pd.DataFrame, fact_map: dict[str, str], code_col: str = "stock_code",
                year_col: str = "year", unit: str = "", caliber: str = "",
                period: str = "年度", source_type: str = "user_structured", source_ref: str = "",
                extract_method: str = "csv", company_col: str | None = None,
                import_mode: str = "supplement") -> list[Evidence]:
    """宽表：列名映射到事实类型，每行展开为多条事实。"""
    out: list[Evidence] = []
    seq = 0
    for r in df.to_dict("records"):
        for col, fact_type in fact_map.items():
            if col not in r or pd.isna(r[col]):
                continue
            seq += 1
            out.append(_mk(
                seq, r.get(code_col), r.get(year_col), fact_type, r.get(col),
                unit=unit, caliber=caliber, period=period, source_type=source_type,
                import_mode=import_mode,
                source_ref=f"{source_ref}:{col}", extraction_method=extract_method,
                raw_context=f"{col}={r.get(col)}",
                company_name=_s(r.get(company_col)) if company_col else "",
            ))
    validate_batch(out, source_ref)
    return out


def ingest_csv(path: str | Path, mode: str = "long", **kwargs) -> list[Evidence]:
    """CSV 摄取。mode=long/wide。"""
    df = pd.read_csv(path, dtype=str)
    kwargs.setdefault("source_ref", Path(path).name)
    kwargs.setdefault("extract_method", "csv")
    if mode == "wide":
        return ingest_wide(df, **kwargs)
    return ingest_long(df, **kwargs)


def ingest_excel(path: str | Path, sheet: str | int = 0, mode: str = "long", **kwargs) -> list[Evidence]:
    df = pd.read_excel(path, sheet_name=sheet, dtype=str)
    kwargs.setdefault("source_ref", f"{Path(path).name}[{sheet}]")
    kwargs.setdefault("extract_method", "excel")
    if mode == "wide":
        return ingest_wide(df, **kwargs)
    return ingest_long(df, **kwargs)


# ---------------------------------------------------------------- 3) 文本数据

def document_fact(stock_code: str, fact_type: str, value, doc_type: str = "other",
                  document_id: str = "", document_date: str = "", report_year: int | None = None,
                  unit: str = "", caliber: str = "", quote: str = "", location: str = "",
                  extraction_method: str = "llm_assisted",
                  extraction_confidence: float = 0.5,
                  verification_status: str = "candidate") -> Evidence:
    """从文档抽取的一条候选事实（默认需人工确认，不直接进 Profile）。"""
    ev = _mk(1, stock_code, report_year, fact_type, value, unit=unit, caliber=caliber,
             source_type="user_document", doc_type=doc_type, document_id=document_id,
             document_date=document_date, extraction_method=extraction_method,
             extraction_confidence=extraction_confidence,
             verification_status=verification_status,
             source_ref=f"{document_id or doc_type} {location}".strip(),
             quote=quote, location=location)
    ev.report_year = report_year
    return ev


# ---------------------------------------------------------------- CSMAR 基准

def csmar_facts_from_profile(row: dict, fields: list[str] | None = None,
                             caliber: str = "合并") -> list[Evidence]:
    """把 CSMAR 画像中的关键字段注入为证据（source_type=csmar），用于与外部来源比对冲突。"""
    fields = fields or ["rd_spend_sum", "is_rd_expense", "gov_subsidy_total",
                        "is_income_tax", "is_interest_expense", "is_revenue",
                        "bs_total_assets", "bs_fixed_assets", "rd_person"]
    out: list[Evidence] = []
    for i, f in enumerate(fields, 1):
        v = row.get(f)
        try:
            if v is None or v != v:
                continue
        except Exception:  # noqa: BLE001
            continue
        kind = infer_value_kind(f, v)
        out.append(_mk(i, row.get("stock_code"), int(row.get("year")), f, v,
                       unit=("元" if kind == "amount" else ""), caliber=caliber,
                       source_type="csmar", source_ref="CSMAR画像",
                       extraction_method="csmar", raw_context=f"{f}={v}"))
    return out


if __name__ == "__main__":
    ev = manual_fact("000063", 2024, "RD_PROJECT", "研发项目A（预算3000万）",
                     source_ref="用户直接输入", caliber="合并")
    print(ev.to_json())
