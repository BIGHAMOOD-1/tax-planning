"""明细/长表按 (股票代码, 年份) 聚合。"""
from __future__ import annotations

import pandas as pd

from src.common import get_logger

log = get_logger()

GROUP = ["stock_code", "year"]
KEY_COLS = {"stock_code", "year", "report_date", "report_month", "report_type", "period", "source",
            "_row_type", "_item_name", "_item_code", "_item_conflict"}


def coerce_numeric(df: pd.DataFrame, exclude: set[str] | None = None) -> pd.DataFrame:
    """把"看起来像数字"的 object 列转成数值。"""
    exclude = (exclude or set()) | KEY_COLS
    out = df.copy()
    for c in out.columns:
        if c.startswith("_") or c in exclude:
            continue
        if pd.api.types.is_numeric_dtype(out[c]):
            continue
        if pd.api.types.is_datetime64_any_dtype(out[c]):
            continue
        coerced = pd.to_numeric(out[c], errors="coerce")
        non_null = out[c].notna().sum()
        if non_null and coerced.notna().sum() >= 0.5 * non_null:
            out[c] = coerced
    return out


def drop_total_and_memo(df: pd.DataFrame) -> pd.DataFrame:
    """聚合前剔除合计行；若同一(公司,年)存在明细行，则同时剔除"其中/减"(memo)行。

    合计与 memo 行仍保留在 normalized 层（仅打标签），此处只影响聚合结果。
    """
    if "_row_type" not in df.columns:
        return df
    work = df[df["_row_type"] != "total"]
    if "stock_code" in work.columns and "year" in work.columns and not work.empty:
        has_item = work.groupby(GROUP)["_row_type"].transform(lambda s: (s == "item").any())
        work = work[(work["_row_type"] == "item") | (~has_item)]
    return work


def aggregate_detail(df: pd.DataFrame) -> pd.DataFrame:
    """数值列求和，附带 n_rows、非空计数。合计/其中行不参与求和。"""
    if df.empty:
        return df
    work = drop_total_and_memo(df)
    work = coerce_numeric(work)
    numeric_cols = [c for c in work.columns if pd.api.types.is_numeric_dtype(work[c]) and c not in KEY_COLS]
    if not numeric_cols:
        counts = work.groupby(GROUP).size().rename("n_rows").reset_index()
        return counts
    grouped = work.groupby(GROUP)
    agg = grouped[numeric_cols].sum(min_count=1)
    agg.columns = [f"sum__{c}" for c in numeric_cols]
    agg["n_rows"] = grouped.size()
    return agg.reset_index()


def aggregate_event(df: pd.DataFrame) -> pd.DataFrame:
    """事件表按年计数（金额列如有则一并求和）。"""
    return aggregate_detail(df)
