"""通用表处理：把未精选的数据集聚合成"公司×年"宽表并并入主表。

原则：
- 过滤合并报表、本期；
- 依据 DES 中文名区分聚合方式：
    率/比例/占比类 -> 取均值；金额/数量类 -> 求和；
    键/标志/文本类（证券代码、报表类型、币种、项目、名称、日期等）-> 剔除；
- 列名加数据集前缀 `d<dataset_id>__`，并保留 n_rows（明细条数）；
- 无股票代码/年份的参考表跳过。
"""
from __future__ import annotations

import re

import pandas as pd

from src.common import get_logger, norm_col
from src.data.aggregate import KEY_COLS, coerce_numeric
from src.data.loader import load_normalized
from src.data.normalize import filter_current_period, filter_report_type
from src.data.registry import Dataset, read_des

log = get_logger()
GROUP = ["stock_code", "year"]

# 中文名含这些关键词 -> 非指标，剔除
EXCLUDE_KEYWORDS = ["币种", "说明", "项目", "名称", "类型", "编码", "标识", "日期", "简称",
                    "地址", "范围", "单位", "关系", "方式", "性质", "层级", "来源", "机构"]
# 中文名含这些关键词 -> 比例，取均值
RATE_KEYWORDS = ["率", "比例", "占比", "百分比"]


def _classify(cn_name: str) -> str:
    name = str(cn_name)
    if any(k in name for k in EXCLUDE_KEYWORDS):
        return "exclude"
    if any(k in name for k in RATE_KEYWORDS):
        return "rate"
    return "amount"


def build_generic_frame(ds: Dataset, min_coverage: float = 0.02) -> pd.DataFrame | None:
    try:
        df = load_normalized(ds)
    except Exception as exc:  # noqa: BLE001
        log.warning("通用处理 %s 读取失败: %s", ds.dataset_id, exc)
        return None
    if df.empty or "stock_code" not in df.columns or "year" not in df.columns:
        return None

    work = df.dropna(subset=GROUP).copy()
    if work.empty:
        return None
    work = filter_report_type(work, "合并")
    work = filter_current_period(work)
    if work.empty:
        return None
    from src.data.aggregate import drop_total_and_memo
    work = drop_total_and_memo(work)

    des = read_des(ds)
    des_lut = {norm_col(k): v for k, v in des.items()}
    work = coerce_numeric(work)

    amount_cols, rate_cols = [], []
    for c in work.columns:
        if c in KEY_COLS or not pd.api.types.is_numeric_dtype(work[c]):
            continue
        kind = _classify(des_lut.get(norm_col(c), ""))
        if kind == "exclude":
            continue
        (rate_cols if kind == "rate" else amount_cols).append(c)

    if not amount_cols and not rate_cols:
        return None

    g = work.groupby(GROUP)
    parts = []
    if amount_cols:
        s = g[amount_cols].sum(min_count=1)
        s.columns = [f"d{ds.dataset_id}__{c}" for c in amount_cols]
        parts.append(s)
    if rate_cols:
        r = g[rate_cols].mean()
        r.columns = [f"d{ds.dataset_id}__{c}" for c in rate_cols]
        parts.append(r)

    out = pd.concat(parts, axis=1)
    out[f"d{ds.dataset_id}__n_rows"] = g.size()
    out = out.reset_index()

    if min_coverage > 0:
        keep = [c for c in out.columns if c in GROUP or out[c].notna().mean() >= min_coverage]
        out = out[keep]
    return out
