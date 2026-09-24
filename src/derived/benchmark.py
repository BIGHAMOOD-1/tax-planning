"""基准类衍生指标：行业均值/中位数、分位数、规模分组。"""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.derived.catalog import register

CATEGORY = "基准"
KEY = ["stock_code", "year"]

BENCH_FIELDS = {
    "rd_expense_ratio": "研发费用率",
    "rd_spend_ratio": "研发投入占收入比",
    "asset_liability_ratio": "资产负债率",
    "gross_margin": "毛利率",
    "net_margin": "净利率",
    "etr": "实际税率",
    "fixed_asset_ratio": "固定资产占比",
    "finance_expense_ratio": "财务费用率",
}


def _add(d, name, cn, formula, inputs, series, unit="", sources="", note=""):
    """写入指标列并登记台账；series 为 None 时以 NaN 填充占位。"""
    d[name] = series if series is not None else np.nan
    register(name, cn, CATEGORY, formula, inputs, unit=unit, source_tables=sources, note=note)


def _industry(master: pd.DataFrame) -> pd.Series:
    """取行业列（优先中文名，其次 csmar 英文列）；均缺失时返回空 Series。"""
    for c in ("industry_name", "csmar__IndustryName"):
        if c in master.columns:
            return master[c]
    return pd.Series(index=master.index, dtype=object)


def compute(master: pd.DataFrame, derived: pd.DataFrame) -> pd.DataFrame:
    """计算基准类指标：规模分组 + 各字段的行业均值/中位数/分位/相对值。

    行业基准按「同年 + 同行业」分组计算，避免跨年度、跨行业不可比。
    """
    base = master[KEY].copy()
    keys_master = master[KEY].copy()
    keys_master["_industry"] = _industry(master).values
    keys_master["_revenue"] = pd.to_numeric(master["is_revenue"], errors="coerce") if "is_revenue" in master.columns else np.nan

    combined = derived.merge(keys_master, on=KEY, how="left")

    out = base.copy()

    # 规模分组：按年份对营业收入做五分位
    size = pd.Series(index=combined.index, dtype=object)
    for _year, idx in combined.groupby("year").groups.items():
        vals = combined.loc[idx, "_revenue"]
        try:
            # 按年度分箱，保证规模标签在同一年内可比
            size.loc[idx] = pd.qcut(vals, 5, labels=["微型", "小型", "中型", "大型", "超大型"]).astype(str)
        except ValueError:
            # 该年有效样本不足 5 组时无法分箱，标记未分组
            size.loc[idx] = "未分组"
    size = size.fillna("未分组")
    out = out.merge(combined[KEY].assign(size_group=size.values), on=KEY, how="left")
    register("size_group", "规模分组", CATEGORY, "按营业收入五分位分组", "is_revenue",
             unit="", source_tables="利润表")

    bench_cols = {}
    for field, cn in BENCH_FIELDS.items():
        if field not in combined.columns:
            continue
        vals = pd.to_numeric(combined[field], errors="coerce")
        tmp = combined[KEY + ["_industry"]].copy()
        tmp["_v"] = vals
        # 按「年份 × 行业」分组计算基准统计量
        grp = tmp.groupby(["year", "_industry"], dropna=False)["_v"]
        tmp["_mean"] = grp.transform("mean")
        tmp["_median"] = grp.transform("median")
        tmp["_pct"] = grp.rank(pct=True)
        # 相对行业中位数：中位数为 0 时置 NaN，避免除零
        tmp["_rel"] = tmp["_v"] / tmp["_median"].replace(0, np.nan)

        bench_cols[f"{field}_industry_mean"] = tmp[KEY + ["_mean"]].rename(columns={"_mean": f"{field}_industry_mean"})
        bench_cols[f"{field}_industry_median"] = tmp[KEY + ["_median"]].rename(columns={"_median": f"{field}_industry_median"})
        bench_cols[f"{field}_industry_pct"] = tmp[KEY + ["_pct"]].rename(columns={"_pct": f"{field}_industry_pct"})
        bench_cols[f"{field}_rel_industry"] = tmp[KEY + ["_rel"]].rename(columns={"_rel": f"{field}_rel_industry"})

        register(f"{field}_industry_mean", f"{cn}_行业均值", CATEGORY, "同年同行业均值", field, unit="%", source_tables="主表")
        register(f"{field}_industry_median", f"{cn}_行业中位数", CATEGORY, "同年同行业中位数", field, unit="%", source_tables="主表")
        register(f"{field}_rel_industry", f"{cn}_相对行业", CATEGORY, f"{cn}/行业中位数", field, unit="倍", source_tables="主表")
        register(f"{field}_industry_pct", f"{cn}_行业分位", CATEGORY, "同年同行业百分位", field, unit="", source_tables="主表")

    for name, frame in bench_cols.items():
        out = out.merge(frame, on=KEY, how="left")

    return out
