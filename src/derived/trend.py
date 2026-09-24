"""趋势类衍生指标：同比、3 年 CAGR、波动率、趋势方向。"""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.derived.catalog import register

CATEGORY = "趋势"
TREND_FIELDS = {
    "is_revenue": "revenue",
    "is_net_profit": "net_profit",
    "rd_spend_sum": "rd_spend",
    "gov_subsidy_total": "gov_subsidy",
    "bs_total_assets": "total_assets",
}


def _add(d, name, cn, formula, inputs, series, unit="", sources="", note=""):
    """写入趋势指标列并登记台账；series 为 None 时以 NaN 占位。"""
    d[name] = series if series is not None else np.nan
    register(name, cn, CATEGORY, formula, inputs, unit=unit, source_tables=sources, note=note)


def compute(master: pd.DataFrame) -> pd.DataFrame:
    """计算趋势类指标：同比、3 年 CAGR、3 年波动率与营收趋势方向。

    先按 (股票代码, 年份) 排序，再用 groupby.shift 取上期/3 年前值，
    保证同比与 CAGR 严格按企业时序计算。
    """
    d = master[["stock_code", "year"]].copy()
    work = master.sort_values(["stock_code", "year"]).copy()
    g = work.groupby("stock_code")

    for src, slug in TREND_FIELDS.items():
        if src not in work.columns:
            continue
        s = pd.to_numeric(work[src], errors="coerce")
        prev = s.groupby(work["stock_code"]).shift(1)
        # 同比：(本期-上期)/|上期|；上期为 0 时置 NaN 避免除零
        yoy = (s - prev) / prev.abs().replace(0, np.nan)
        _add(d, f"{slug}_yoy", f"{slug}同比增长率", "(本期-上期)/|上期|", src,
             yoy.reindex(master.index), unit="%", sources="按主表汇总")

        # 3 年 CAGR（本期 / 3 年前）^(1/3) - 1
        lag3 = s.groupby(work["stock_code"]).shift(3)
        cagr = (s / lag3.abs().replace(0, np.nan)) ** (1 / 3) - 1
        _add(d, f"{slug}_cagr_3y", f"{slug}3年复合增长率", "(本期/3年前)^(1/3)-1", src,
             cagr.reindex(master.index), unit="%", sources="按主表汇总")

        # 3 年波动率（滚动标准差 / 均值）
        roll = s.groupby(work["stock_code"]).rolling(3, min_periods=2).std().reset_index(level=0, drop=True)
        mean = s.groupby(work["stock_code"]).rolling(3, min_periods=2).mean().reset_index(level=0, drop=True)
        # 变异系数：标准差 / |均值|，均值近 0 时置 NaN
        vol = (roll / mean.abs().replace(0, np.nan))
        _add(d, f"{slug}_volatility_3y", f"{slug}3年波动率", "近3年标准差/近3年均值", src,
             vol.reindex(master.index), unit="%", sources="按主表汇总")

    # 综合趋势方向（营收）
    rev_yoy = d.get("revenue_yoy")
    if rev_yoy is not None:
        # 阈值 ±5%：>5% 上升，<-5% 下降，其间平稳；缺失单独标记
        trend = pd.Series(np.where(rev_yoy > 0.05, "上升", np.where(rev_yoy < -0.05, "下降", "平稳")),
                          index=d.index)
        trend[rev_yoy.isna()] = "缺失"
        _add(d, "revenue_trend", "营业收入趋势方向", "同比增长>5%上升，<-5%下降，否则平稳", "is_revenue",
             trend, unit="", sources="按主表汇总")

    return d
