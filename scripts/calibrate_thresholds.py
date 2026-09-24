"""诊断阈值校准 + 诊断健康检查（纯 Python，不走 LLM）。

目的：
1. 对全体"公司·年"向量化重算每条诊断的偏差，按类型统计分位，反推阈值
   （提示=P75 / 观察=P90 / 关注=P99，可参数化）。
2. **逐诊断**保存 sample_count / nonzero_count / coverage / p50..p99 / applied 阈值 / trigger_rate。
3. **诊断健康检查**（固定输出）：NO_OP / COVERAGE_LOW / SATURATED / HEALTHY。

注意：
- 方向性：`abs` 双向偏离；`up` 仅向上偏离（如应收账款占比）。
- 分位基于**非零偏差**（避免"完全相等=0"把 P75 拉成 0）。
- 类别触发率是**混合值**，不能跨诊断比较；以逐诊断统计为准。
- 只生成建议文件 `config/thresholds.suggested.yaml`，不自动覆盖 config。

用法：
    python scripts/calibrate_thresholds.py
    python scripts/calibrate_thresholds.py --percentiles 75,90,99 --years 2020-2024
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import yaml  # noqa: E402

from src.common import CONFIG, ROOT, get_logger  # noqa: E402

log = get_logger()
PROC = Path(CONFIG["paths"]["data_processed"])
THRESHOLD_PATH = ROOT / "config" / "thresholds.yaml"

# 健康检查默认参数（数据可运行性阈值，与税务异常阈值无关）
HEALTH_DEFAULT = {"coverage_min": 0.20, "saturation_ratio_max": 0.10,
                  "zero_ratio_max": 0.95, "upper_bound": 1.0}


def _rel(a, b, direction: str = "abs"):
    """相对偏差：|a-b| / max(|a|,|b|)；direction='up' 时只取 a>b 的正向偏离。"""
    a = pd.to_numeric(a, errors="coerce")
    b = pd.to_numeric(b, errors="coerce")
    denom = pd.concat([a.abs(), b.abs()], axis=1).max(axis=1).replace(0, np.nan)
    diff = (a - b).clip(lower=0) if direction == "up" else (a - b).abs()
    return diff / denom


def build_deviations(df: pd.DataFrame, diag_cfg: dict) -> dict[str, pd.Series]:
    """按诊断项构造"实际值 vs 基准值"的相对偏差序列。

    基准率（利息/折旧/应收占比）从配置读取，缺失时用内置默认值。
    """
    items = diag_cfg.get("items", {}) or {}
    bench_int = (items.get("interest", {}) or {}).get("benchmark_rate", 0.05)
    bench_dep = (items.get("depreciation", {}) or {}).get("benchmark_rate", 0.08)
    bench_ar = (items.get("ar_ratio", {}) or {}).get("benchmark", 0.20)

    def col(name):
        # 列缺失时返回全 NaN，保证偏差计算不报错
        return pd.to_numeric(df[name], errors="coerce") if name in df.columns else pd.Series(np.nan, index=df.index)

    devs: dict[str, pd.Series] = {}

    # 勾稽类
    cur, dfr, it = col("current_tax_expense"), col("deferred_tax_expense"), col("is_income_tax")
    devs["tax_reconcile"] = _rel(cur + dfr, it)
    devs["rd_detail"] = _rel(col("rd_spend_sum"), col("rd_expense_detail_sum"))
    devs["rd_caliber"] = _rel(col("rd_spend_sum"), col("is_rd_expense"))

    # 推算类
    debt = col("interest_debt_ratio") * col("bs_total_assets")
    expected_int = debt * bench_int
    # 有息负债为 0 时不适用，置空
    devs["interest"] = _rel(col("interest_expense_best"), expected_int).where(debt > 0)
    devs["depreciation"] = _rel(col("fa_accum_dep_increase"), col("fa_original_end") * bench_dep)
    # 应收账款占比：直接由 应收账款/营业收入 计算（不依赖缺失字段 ar_to_revenue）
    ar_ratio = col("bs_accounts_receivable") / col("is_revenue").replace(0, np.nan)
    devs["ar_ratio"] = _rel(ar_ratio, pd.Series(bench_ar, index=df.index), direction="up")
    devs["revenue_gap"] = _rel(col("is_total_revenue"), col("is_revenue"))
    cfo, npf, dep = col("cf_operating_net"), col("is_net_profit"), col("fa_accum_dep_increase")
    # 仅对盈利企业评估经营现金流偏差
    devs["cashflow"] = _rel(cfo, npf + dep).where(npf > 0)

    # 确认类
    devs["gov_subsidy"] = _rel(col("gov_subsidy_total"),
                               col("is_other_income").fillna(0) + col("is_non_operating_income").fillna(0))
    devs["tax_payable_change"] = _rel(col("tax_payable_end"), col("tax_payable_begin"))

    # 趋势类
    if {"stock_code", "year"} <= set(df.columns):
        d = df.sort_values(["stock_code", "year"])
        for field, key in (("etr", "etr_yoy"), ("rd_expense_ratio", "rd_ratio_yoy")):
            s = pd.to_numeric(d[field], errors="coerce") if field in d.columns else pd.Series(np.nan, index=d.index)
            # 同比偏差：本期 vs 上期
            prev = s.groupby(d["stock_code"]).shift(1)
            devs[key] = _rel(s, prev).reindex(df.index)

    return devs


def _health(n: int, nz_count: int, dev, health) -> tuple[str, str]:
    """诊断健康检查：NO_OP / COVERAGE_LOW / SATURATED / HEALTHY。"""
    if n == 0:
        return "NO_OP", "required_field_missing"
    coverage = nz_count / n
    if coverage < health["coverage_min"]:
        return "COVERAGE_LOW", f"coverage={coverage:.0%} < {health['coverage_min']:.0%}"
    if nz_count == 0:
        return "COVERAGE_LOW", "all_zero"
    if len(dev):
        # 接近上界的比例过高说明该诊断"全触发"，失去区分度
        sat = float((dev >= health["upper_bound"] * 0.999).mean())
        if sat > health["saturation_ratio_max"]:
            return "SATURATED", f"saturation_ratio={sat:.0%}"
    return "HEALTHY", ""


def calibrate(years=(2020, 2024), percentiles=(75, 90, 99)):
    """按年份区间计算各诊断偏差的分位，返回 (逐诊断表, 分类表, 健康参数)。"""
    prof = pd.read_parquet(Path(CONFIG["paths"]["features_dir"]) / "profile_features.parquet")
    prof["stock_code"] = prof["stock_code"].astype(str)
    prof = prof[(prof["year"] >= years[0]) & (prof["year"] <= years[1])].reset_index(drop=True)

    with open(THRESHOLD_PATH, "r", encoding="utf-8") as f:
        diag_cfg = (yaml.safe_load(f) or {}).get("diagnostics", {}) or {}
    items = diag_cfg.get("items", {}) or {}
    cats = diag_cfg.get("category_thresholds", {}) or {}
    # 健康参数：配置覆盖内置默认
    health = {**HEALTH_DEFAULT, **(diag_cfg.get("health", {}) or {})}

    devs = build_deviations(prof, diag_cfg)
    rows = []
    cat_frames: dict[str, list[pd.Series]] = {}
    for item_id, s in devs.items():
        s = pd.to_numeric(s, errors="coerce")
        valid = s.dropna()
        # 分位基于非零偏差，避免"完全相等=0"把 P75 拉成 0
        nz = valid[valid > 0]
        cat = (items.get(item_id, {}) or {}).get("category", "estimate")
        cat_frames.setdefault(cat, []).append(nz)
        q = {f"p{p}": float(np.percentile(nz, p)) if len(nz) else None for p in (50, 75, 90, 95, 99)}
        th = cats.get(cat, {})
        trig = {f"trigger_{k}": (float((valid >= v).mean()) if (v is not None and len(valid)) else None)
                for k, v in (("提示", th.get("提示")), ("观察", th.get("观察")), ("关注", th.get("关注")))}
        status, reason = _health(len(s), len(nz), valid, health)
        rows.append({
            "diagnostic_id": item_id, "category": cat,
            "sample_count": int(len(s)), "nonzero_count": int(len(nz)),
            "coverage": round(len(nz) / len(s), 4) if len(s) else 0.0,
            "zero_ratio": round(1 - len(nz) / len(s), 4) if len(s) else 1.0,
            **q,
            "applied_提示": th.get("提示"), "applied_观察": th.get("观察"), "applied_关注": th.get("关注"),
            **trig,
            "health_status": status, "health_reason": reason,
        })
    item_df = pd.DataFrame(rows)

    cat_rows = []
    for cat, series_list in cat_frames.items():
        allv = pd.concat(series_list, ignore_index=True).dropna()
        q = {f"p{p}": float(np.percentile(allv, p)) if len(allv) else None for p in (50, 75, 90, 95, 99)}
        cur = cats.get(cat, {})
        cat_rows.append({
            "category": cat, "n": int(len(allv)), **q,
            "cur_提示": cur.get("提示"), "cur_观察": cur.get("观察"), "cur_关注": cur.get("关注"),
            "sug_提示": q.get(f"p{percentiles[0]}"), "sug_观察": q.get(f"p{percentiles[1]}"),
            "sug_关注": q.get(f"p{percentiles[2]}"),
        })
    cat_df = pd.DataFrame(cat_rows)
    return item_df, cat_df, health


def main():
    """命令行入口：校准阈值并输出 CSV/Markdown 与建议 YAML（不覆盖正式配置）。"""
    ap = argparse.ArgumentParser()
    ap.add_argument("--percentiles", default="75,90,99")
    ap.add_argument("--years", default="2020-2024")
    args = ap.parse_args()
    ps = tuple(int(x) for x in args.percentiles.split(","))
    y0, y1 = (int(x) for x in args.years.split("-"))

    item_df, cat_df, health = calibrate((y0, y1), ps)
    item_df.to_csv(PROC / "diagnostic_calibration.csv", index=False, encoding="utf-8-sig")

    # 建议文件结构：分类阈值 + 逐诊断阈值
    suggested = {"diagnostics": {"levels": ["提示", "观察", "关注"], "category_thresholds": {}}}
    for _, r in cat_df.iterrows():
        suggested["diagnostics"]["category_thresholds"][r["category"]] = {
            "提示": round(r["sug_提示"], 4), "观察": round(r["sug_观察"], 4),
            "关注": round(r["sug_关注"], 4),
        }

    # 逐诊断建议阈值（样本充分且非 NO_OP/COVERAGE_LOW 才给；仍为建议文件，不自动覆盖）
    items_sug: dict[str, dict] = {}
    for _, r in item_df.iterrows():
        # 样本过少或数据不可用时不建议阈值，避免噪声
        if int(r["nonzero_count"]) < 30 or r["health_status"] in ("NO_OP", "COVERAGE_LOW"):
            continue
        th = {}
        for label, p in zip(("提示", "观察", "关注"), ps):
            v = r.get(f"p{p}")
            if v is not None and not (isinstance(v, float) and np.isnan(v)):
                th[label] = round(float(v), 4)
        if len(th) == 3:
            # 合理性守卫：提示须 > 0 且严格递增，否则不建议逐项阈值（避免"全触发"）
            if th["提示"] > 0 and th["提示"] < th["观察"] < th["关注"]:
                items_sug[str(r["diagnostic_id"])] = {"thresholds": th}
    if items_sug:
        suggested["diagnostics"]["items"] = items_sug
    out_yaml = ROOT / "config" / "thresholds.suggested.yaml"
    with open(out_yaml, "w", encoding="utf-8") as f:
        yaml.safe_dump(suggested, f, allow_unicode=True, sort_keys=False)

    def pct(v):
        return "—" if v is None or (isinstance(v, float) and np.isnan(v)) else f"{v:.2%}"

    lines = [f"# 诊断阈值校准与健康检查（{y0}-{y1}，分位 {ps}）", "",
             "## 一、逐诊断统计（权威；类别触发率为混合值，不可跨诊断比较）", "",
             "| 诊断 | 类型 | 样本 | 非零 | coverage | p75 | p90 | p99 | "
             "触发(提示/观察/关注) | 健康 |", "|---|---|---|---|---|---|---|---|---|---|"]
    for _, r in item_df.sort_values(["health_status", "category"]).iterrows():
        lines.append(
            f"| {r['diagnostic_id']} | {r['category']} | {r['sample_count']} | {r['nonzero_count']} | "
            f"{r['coverage']:.1%} | {pct(r['p75'])} | {pct(r['p90'])} | {pct(r['p99'])} | "
            f"{pct(r['trigger_提示'])}/{pct(r['trigger_观察'])}/{pct(r['trigger_关注'])} | "
            f"{r['health_status']}{('（' + r['health_reason'] + '）') if r['health_reason'] else ''} |")

    lines += ["", "## 二、诊断健康检查（固定输出）", "",
              f"阈值：coverage_min={health['coverage_min']:.0%}、"
              f"saturation_ratio_max={health['saturation_ratio_max']:.0%}、"
              f"upper_bound={health['upper_bound']}", "",
              "| 诊断 | 状态 | 原因 |", "|---|---|---|"]
    for _, r in item_df.iterrows():
        lines.append(f"| {r['diagnostic_id']} | {r['health_status']} | {r['health_reason'] or '—'} |")
    bad = item_df[item_df["health_status"] != "HEALTHY"]
    lines += ["", f"**非健康诊断 {len(bad)} / {len(item_df)} 项**："
              + "、".join(f"{r['diagnostic_id']}({r['health_status']})" for _, r in bad.iterrows())
              if len(bad) else "", ""]

    lines += ["", "## 三、按类型（混合，仅参考）", "",
              "| 类型 | 样本 | P50 | P75 | P90 | P95 | P99 | 当前 | 建议 |",
              "|---|---|---|---|---|---|---|---|---|"]
    for _, r in cat_df.iterrows():
        lines.append(f"| {r['category']} | {r['n']} | {pct(r['p50'])} | {pct(r['p75'])} | {pct(r['p90'])} | "
                     f"{pct(r['p95'])} | {pct(r['p99'])} | "
                     f"{r['cur_提示']:.0%}/{r['cur_观察']:.0%}/{r['cur_关注']:.0%} | "
                     f"{pct(r['sug_提示'])}/{pct(r['sug_观察'])}/{pct(r['sug_关注'])} |")

    if items_sug:
        lines += ["", "## 四、逐诊断建议阈值（写入 thresholds.suggested.yaml 的 diagnostics.items.<id>.thresholds）", "",
                  "| 诊断 | 提示 | 观察 | 关注 |", "|---|---|---|---|"]
        for did, v in items_sug.items():
            t = v["thresholds"]
            lines.append(f"| {did} | {t['提示']:.2%} | {t['观察']:.2%} | {t['关注']:.2%} |")

    (PROC / "diagnostic_calibration.md").write_text("\n".join(lines), encoding="utf-8")

    log.info("校准+健康检查完成：%s / %s / %s", PROC / "diagnostic_calibration.csv",
             PROC / "diagnostic_calibration.md", out_yaml)
    print("\n健康检查：")
    print(item_df[["diagnostic_id", "coverage", "p99", "trigger_关注", "health_status", "health_reason"]]
          .to_string(index=False))


if __name__ == "__main__":
    main()
