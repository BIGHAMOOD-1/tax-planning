"""键与字段标准化。

把各来源表的异构列名/取值统一为标准键（stock_code/report_date/year/...），
供后续过滤（合并报表、本期）与合并使用。列名匹配采用「归一化后查别名」的策略。
"""
from __future__ import annotations

from datetime import datetime

import pandas as pd

from src.common import norm_col, norm_stock_code
from src.data.registry import (
    CODE_ALIASES,
    DATE_ALIASES,
    PERIOD_ALIASES,
    REPORT_TYPE_ALIASES,
    SOURCE_ALIASES,
)

CODE_OUT = "stock_code"
DATE_OUT = "report_date"
YEAR_OUT = "year"
MONTH_OUT = "report_month"
RTYPE_OUT = "report_type"
PERIOD_OUT = "period"
SOURCE_OUT = "source"

# 报表类型归一：字母/数字/中文多种编码统一为「合并/母公司」
RTYPE_MAP = {
    "a": "合并", "b": "母公司",
    "1": "合并", "2": "母公司",
    "1.0": "合并", "2.0": "母公司",
    "合并": "合并", "母公司": "母公司",
    "合并报表": "合并", "母公司报表": "母公司",
}


def _find_col(df: pd.DataFrame, aliases: list[str]) -> str | None:
    """在 df 中按别名查找原始列名：先归一化列名建立映射，再按别名顺序命中。"""
    lut = {norm_col(c): c for c in df.columns}
    for a in aliases:
        key = norm_col(a)
        if key in lut:
            return lut[key]
    return None


def map_report_type(value) -> str | None:
    """把原始报表类型取值映射为「合并/母公司」；无法识别返回 None。"""
    if value is None:
        return None
    key = str(value).strip().lower()
    return RTYPE_MAP.get(key)


def normalize_keys(df: pd.DataFrame) -> pd.DataFrame:
    """生成统一的 stock_code / report_date / year / report_month / report_type 等字段。"""
    if df.empty:
        return df.copy()
    out = df.copy()

    code_col = _find_col(out, CODE_ALIASES)
    date_col = _find_col(out, DATE_ALIASES)
    rtype_col = _find_col(out, REPORT_TYPE_ALIASES)
    period_col = _find_col(out, PERIOD_ALIASES)
    source_col = _find_col(out, SOURCE_ALIASES)

    # 仅在找到对应来源列时才派生标准列，避免产生全空列
    if code_col is not None:
        out[CODE_OUT] = out[code_col].map(norm_stock_code)
    if date_col is not None:
        # 非法日期转 NaT，其 year/month 自然为空，便于后续剔除
        out[DATE_OUT] = pd.to_datetime(out[date_col], errors="coerce")
        out[YEAR_OUT] = out[DATE_OUT].dt.year
        out[MONTH_OUT] = out[DATE_OUT].dt.month
    if rtype_col is not None:
        out[RTYPE_OUT] = out[rtype_col].map(map_report_type)
    if period_col is not None:
        out[PERIOD_OUT] = out[period_col]
    if source_col is not None:
        out[SOURCE_OUT] = out[source_col]

    # 标准键列前置，原始列后置，便于人工查看
    keep = [CODE_OUT] + [c for c in [DATE_OUT, YEAR_OUT, MONTH_OUT, RTYPE_OUT, PERIOD_OUT, SOURCE_OUT] if c in out.columns]
    rest = [c for c in out.columns if c not in keep]
    return out[keep + rest]


def filter_report_type(df: pd.DataFrame, target: str = "合并") -> pd.DataFrame:
    """只保留目标报表类型（默认合并报表）；无该列时原样返回。"""
    if RTYPE_OUT not in df.columns:
        return df
    return df[df[RTYPE_OUT] == target]


def filter_current_period(df: pd.DataFrame) -> pd.DataFrame:
    """期间归属只保留"本期"(1)。"""
    if PERIOD_OUT not in df.columns:
        return df
    vals = df[PERIOD_OUT].astype(str).str.strip()
    mask = vals.isin(["1", "1.0", "本期"])
    # 全部无法识别时不误删，返回原表（部分数据源缺 period 语义）
    if mask.any():
        return df[mask]
    return df
