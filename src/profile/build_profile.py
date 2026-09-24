"""企业画像特征：合并主表 + 衍生 + 标签。"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from src.common import CONFIG, get_logger

log = get_logger()
KEY = ["stock_code", "year"]


def _num(df, col):
    """安全取数值列；列不存在时返回全 NaN 序列，保证比较运算不报错。"""
    return pd.to_numeric(df[col], errors="coerce") if col in df.columns else pd.Series(np.nan, index=df.index)


def _gt_industry(df, metric):
    """判断指标是否高于同行业均值（要求双方均非空，避免 NaN 比较误判）。"""
    v = _num(df, metric)
    m = _num(df, f"{metric}_industry_mean")
    return (v > m) & v.notna() & m.notna()


def build_labels(df: pd.DataFrame) -> pd.DataFrame:
    """由画像字段派生布尔标签（0/1），供 Skill/Agent 快速筛选方向。

    标签口径示例：高研发（高于行业或研发投入占收入>5%）、高杠杆（负债率>60% 或行业分位>75%）等。
    """
    lab = df[KEY].copy()

    lab["label_high_rd"] = (_gt_industry(df, "rd_expense_ratio") | (_num(df, "rd_spend_ratio") > 0.05)).astype(int)
    lab["label_heavy_asset"] = _gt_industry(df, "fixed_asset_ratio").astype(int)
    # 高杠杆：绝对阈值 60% 或行业分位 >75% 任一满足
    lab["label_high_leverage"] = (
        (_num(df, "asset_liability_ratio") > 0.6)
        | (_num(df, "asset_liability_ratio_industry_pct") > 0.75)
    ).astype(int)
    lab["label_has_gov_subsidy"] = (_num(df, "gov_subsidy_total") > 0).astype(int)
    lab["label_has_overseas_sub"] = (_num(df, "subsidiary_overseas_count") > 0).astype(int)
    lab["label_high_interest"] = _gt_industry(df, "finance_expense_ratio").astype(int)
    trend_col = df["revenue_trend"] if "revenue_trend" in df.columns else pd.Series(index=df.index)
    lab["label_declining_revenue"] = (trend_col == "下降").astype(int)
    lab["label_hightech"] = (_num(df, "is_hightech") > 0).astype(int)
    lab["label_overseas_group"] = (_num(df, "subsidiary_hk_mo_tw_count") > 0).astype(int)
    lab["label_related_party_deals"] = (_num(df, "ma_related_party_count") > 0).astype(int)

    # 名义税率披露为百分数，换算成小数后与实际税率(小数)比较
    etr = _num(df, "etr"); nominal = _num(df, "nominal_tax_rate") / 100.0
    lab["label_tax_burden_above_nominal"] = ((etr > nominal) & etr.notna() & nominal.notna()).astype(int)
    return lab


def build_profile(master: pd.DataFrame, derived: pd.DataFrame) -> pd.DataFrame:
    """画像特征 = 合并主表 + 全部衍生指标 + 标签。

    保留全量字段，便于第二步各 Skill/Agent 按需取用。
    """
    full = master.merge(derived, on=KEY, how="left", suffixes=("", "__d"))
    labels = build_labels(full)
    prof = full.merge(labels, on=KEY, how="left")

    # 低覆盖字段多源回填 + 显式代理（Update 5.2）
    try:
        from src.data.backfill import apply_backfill, render_report
        prof, bf_report = apply_backfill(prof)
        from src.common import ROOT
        (ROOT / "data_processed" / "backfill_report.md").write_text(
            render_report(bf_report), encoding="utf-8")
        log.info("低覆盖回填：合并 %s 项 / 代理 %s 项",
                 len(bf_report.get("coalesced", {})), len(bf_report.get("proxy", {})))
    except Exception as exc:  # noqa: BLE001
        log.warning("低覆盖回填失败：%s", str(exc)[:120])

    # 把关键字段排到前面，便于查看
    front = [c for c in (
        "stock_code", "year", "short_name", "industry_name", "province", "city",
        "listing_state", "equity_nature", "size_group",
    ) if c in prof.columns]
    rest = [c for c in prof.columns if c not in front]
    prof = prof[front + rest]
    return prof


def save_profile(prof: pd.DataFrame) -> Path:
    """把画像特征写入 profile_features.parquet，返回路径。"""
    out = Path(CONFIG["paths"]["features_dir"]) / "profile_features.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    prof.to_parquet(out, index=False)
    log.info("画像特征写入 %s：%s 行 × %s 列", out, *prof.shape)
    return out
