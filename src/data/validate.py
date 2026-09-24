"""数据质量检查。

提供字段覆盖率统计与主表主键/关键字段校验，并输出覆盖率报告，
用于在合并后快速发现空列、重复主键等问题。
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.common import get_logger

log = get_logger()

# 主表主键：股票代码 × 年度，二者组合应唯一
KEY = ["stock_code", "year"]


def coverage(df: pd.DataFrame) -> pd.DataFrame:
    """逐列统计非空数、覆盖率与去重值数，并按覆盖率降序返回。"""
    n = len(df)
    rows = []
    for c in df.columns:
        non_null = int(df[c].notna().sum())
        rows.append({
            "column": c,
            "dtype": str(df[c].dtype),
            "non_null": non_null,
            # 空表时避免除零，覆盖率记 0
            "coverage": round(non_null / n, 4) if n else 0.0,
            "n_unique": int(df[c].nunique(dropna=True)),
        })
    return pd.DataFrame(rows).sort_values("coverage", ascending=False)


def check_master(master: pd.DataFrame) -> list[str]:
    """主表校验：重复主键、空代码、年份范围，返回问题清单。"""
    issues: list[str] = []
    if master.duplicated(KEY).any():
        issues.append(f"主表存在重复主键 {KEY}")
    if master["stock_code"].isna().any():
        issues.append("存在空的 stock_code")
    yrs = set(master["year"].dropna().unique())
    issues.append(f"年份范围: {sorted(yrs)}")
    return issues


def write_report(master: pd.DataFrame, out_dir: str | Path) -> Path:
    """写出字段覆盖率报告到 out_dir，返回报告路径。"""
    out_dir = Path(out_dir)
    cov = coverage(master)
    cov.to_csv(out_dir / "master_coverage.csv", index=False, encoding="utf-8-sig")
    log.info("字段覆盖率报告写入 %s/master_coverage.csv", out_dir)
    return out_dir / "master_coverage.csv"
