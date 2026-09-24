"""数据质检：合计与明细对账 + 覆盖率 + 异常清单。

对账口径：同一 (企业, 年度, 报表类型) 分组下，明细行之和与合计行对比，
相对误差 ≤5% 记为 match；仅告警不删行，避免误伤原始数据。
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.common import CONFIG, get_logger
from src.data.aggregate import KEY_COLS
from src.data.loader import load_normalized
from src.data.registry import discover_datasets, load_registry

log = get_logger()
PROC = Path(CONFIG["paths"]["data_processed"])
GROUP = ["stock_code", "year", "report_type"]


# 标识/文本类列，不应被选作"金额列"参与对账
FLAG_COLS = {"stkcd", "symbol", "shortname", "accper", "enddate", "typrep", "statetype",
             "statetypecode", "datasources", "source", "sgnyea", "sgnrgn", "currency",
             "institutionid", "listedcoid", "subjectcategoryid", "statetypeid",
             "report_month"}


def _amount_column(df: pd.DataFrame) -> str | None:
    """挑选用于对账的金额列：数值型、非主键、非标识列，取非空率最高者。"""
    # 以非空率最高的数值列作为对账依据，通常是该表的金额主列
    num = [c for c in df.columns
           if pd.api.types.is_numeric_dtype(df[c]) and c not in KEY_COLS
           and not c.startswith("_") and c.lower() not in FLAG_COLS]
    if not num:
        return None
    return max(num, key=lambda c: df[c].notna().mean())


def total_reconciliation() -> pd.DataFrame:
    """明细表：sum(明细行) vs 合计行 的对账（只告警，不删行）。"""
    rows = []
    for ds in discover_datasets():
        reg = load_registry().get(ds.dataset_id, {})
        # 仅对"明细型"数据集做合计对账
        if reg.get("shape") != "detail":
            continue
        try:
            df = load_normalized(ds)
        except Exception:  # noqa: BLE001
            continue
        if "_row_type" not in df.columns or df.empty:
            continue
        col = _amount_column(df)
        if col is None:
            continue
        d = df.dropna(subset=["stock_code", "year"]).copy()
        d["_v"] = pd.to_numeric(d[col], errors="coerce")
        keys = [k for k in GROUP if k in d.columns]
        # 分别汇总明细行与合计行；min_count=1 保证全空分组返回 NaN 而非 0
        item = d[d["_row_type"] == "item"].groupby(keys, dropna=False)["_v"].sum(min_count=1)
        tot = d[d["_row_type"] == "total"].groupby(keys, dropna=False)["_v"].sum(min_count=1)
        j = pd.concat([item.rename("item_sum"), tot.rename("total")], axis=1).dropna()
        if j.empty:
            continue
        diff = (j["item_sum"] - j["total"]).abs()
        # 相对误差 5% 阈值；合计为 0 时要求严格相等
        match = diff <= j["total"].abs() * 0.05
        rows.append({
            "dataset_id": ds.dataset_id,
            "dataset_name": ds.dataset_name,
            "amount_column": col,
            "groups": len(j),
            "match": int(match.sum()),
            "mismatch": int((~match).sum()),
            "match_rate": round(match.mean(), 4),
        })
    out = pd.DataFrame(rows).sort_values("match_rate", ascending=False)
    return out


def coverage_report() -> pd.DataFrame:
    """汇总各数据集的行数、覆盖公司数与年份区间。"""
    rows = []
    for ds in discover_datasets():
        try:
            df = load_normalized(ds)
        except Exception:  # noqa: BLE001
            continue
        if df.empty:
            continue
        rows.append({
            "dataset_id": ds.dataset_id,
            "dataset_name": ds.dataset_name,
            "rows": len(df),
            # 缺 stock_code 时公司数记 0，避免 KeyError
            "companies": df["stock_code"].nunique() if "stock_code" in df.columns else 0,
            # 年份区间：无有效年份时留空
            "years": (f"{int(df['year'].min())}-{int(df['year'].max())}"
                      if "year" in df.columns and df["year"].notna().any() else ""),
        })
    return pd.DataFrame(rows)


def classification_conflicts() -> pd.DataFrame:
    """合计识别的代码/文本冲突（当前数据结构下通常为 0，作为面向未来的校验）。"""
    rows = []
    for ds in discover_datasets():
        try:
            df = load_normalized(ds)
        except Exception:  # noqa: BLE001
            continue
        # 仅当标准化阶段标注了冲突列时才统计
        if "_item_conflict" not in df.columns or df.empty:
            continue
        n = int(df["_item_conflict"].fillna(False).astype(bool).sum())
        if n:
            rows.append({"dataset_id": ds.dataset_id, "dataset_name": ds.dataset_name,
                         "conflicts": n, "rows": len(df)})
    return pd.DataFrame(rows, columns=["dataset_id", "dataset_name", "conflicts", "rows"])


def run() -> None:
    """执行全部质检并落盘 CSV 与 Markdown 报告。"""
    # 三块质检相互独立：对账、覆盖、合计识别冲突
    rec = total_reconciliation()
    cov = coverage_report()
    conf = classification_conflicts()
    rec.to_csv(PROC / "total_reconciliation.csv", index=False, encoding="utf-8-sig")
    cov.to_csv(PROC / "dataset_coverage.csv", index=False, encoding="utf-8-sig")
    conf.to_csv(PROC / "classification_conflicts.csv", index=False, encoding="utf-8-sig")

    # 组装 Markdown 报告（对账 / 冲突 / 覆盖三节）
    lines = ["# 数据质检报告", "",
             "## 一、合计与明细对账（相对误差 ≤5% 记为 match）", "",
             "| 数据集 | 金额列 | 分组数 | match | mismatch | 匹配率 |",
             "|---|---|---|---|---|---|"]
    for _, r in rec.iterrows():
        lines.append(f"| {r['dataset_id']} {r['dataset_name']} | {r['amount_column']} | "
                     f"{r['groups']} | {r['match']} | {r['mismatch']} | {r['match_rate']:.1%} |")
    lines += ["", "## 二、合计识别冲突（代码 vs 文本）", ""]
    if conf.empty:
        lines.append("无冲突。")
    else:
        lines.append("| 数据集 | 冲突行数 | 总行数 |")
        lines.append("|---|---|---|")
        for _, r in conf.iterrows():
            lines.append(f"| {r['dataset_id']} {r['dataset_name']} | {r['conflicts']} | {r['rows']} |")
    lines += ["", "## 三、数据集覆盖", "",
              "| 数据集 | 行数 | 公司数 | 年份 |", "|---|---|---|---|"]
    for _, r in cov.iterrows():
        lines.append(f"| {r['dataset_id']} {r['dataset_name']} | {r['rows']} | {r['companies']} | {r['years']} |")
    (PROC / "quality_report.md").write_text("\n".join(lines), encoding="utf-8")
    log.info("质检写入 total_reconciliation.csv / classification_conflicts.csv / "
             "dataset_coverage.csv / quality_report.md")


if __name__ == "__main__":
    run()
