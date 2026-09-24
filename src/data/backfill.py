"""低覆盖字段的多源回填与**显式代理**（Update 5.2 · P1）。

原则：
- **同一事实的多源合并**（coalesce）：仅在语义一致时用备用列填补缺失；
- **不可由财务指标替代的资格事实**：绝不回填 `is_hightech` 本身，
  而是生成**显式代理字段**（`*_proxy`），供 Skill/发现作为"线索"，
  且不改变 `resolution=PROXY` 的语义（仍不得支撑 CONFIRMED）。

产物：
- 回填后的 DataFrame（新增 `*_proxy` 列）
- 回填报告（用于 `data_processed/backfill_report.md`）
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# 1) 同义多源合并：field <- [备用列...]（仅填补缺失）
COALESCE_FIELDS: dict[str, list[str]] = {
    "employee_compensation_base": ["bs_payroll_payable"],
}

# 2) 结构性 0：父字段为 0 时子字段应为 0（而非缺失）
DERIVE_ZERO: dict[str, str] = {
    "subsidiary_overseas_count": "subsidiary_count",
    "subsidiary_hk_mo_tw_count": "subsidiary_count",
}


def _num(df: pd.DataFrame, col: str) -> pd.Series:
    return pd.to_numeric(df[col], errors="coerce") if col in df.columns else pd.Series(np.nan, index=df.index)


def _hightech_signal(df: pd.DataFrame) -> pd.Series:
    """高新技术企业**资格线索**代理（非资格事实）。

    触发条件（任一）：
      1) 名义税率 <= 15%（享受优惠税率，可能是高企/西部大开发/小微等）；
      2) 研发费用率 >= 3% 且 研发人员占比 >= 10%（接近认定门槛）。
    注意：这是**线索**，不得等同于 `is_hightech`，也不得支撑 CONFIRMED。
    """
    rate = _num(df, "nominal_tax_rate")
    rd_r = _num(df, "rd_expense_ratio")
    rd_p = _num(df, "rd_person_ratio_cross")
    cond_rate = rate.notna() & (rate <= 15)
    cond_ratio = rd_r.notna() & (rd_r >= 0.03) & rd_p.notna() & (rd_p >= 0.10)
    sig = (cond_rate | cond_ratio)
    return sig.astype(int).where(rate.notna() | rd_r.notna() | rd_p.notna())


def _hightech_signal_reason(df: pd.DataFrame) -> pd.Series:
    rate = _num(df, "nominal_tax_rate")
    rd_r = _num(df, "rd_expense_ratio")
    rd_p = _num(df, "rd_person_ratio_cross")
    out = pd.Series("", index=df.index, dtype=object)
    out = out.mask(rate.notna() & (rate <= 15), "名义税率<=15%")
    cond_ratio = rd_r.notna() & (rd_r >= 0.03) & rd_p.notna() & (rd_p >= 0.10)
    both = (rate.notna() & (rate <= 15)) & cond_ratio
    out = out.mask(cond_ratio & ~(rate.notna() & (rate <= 15)), "研发费用率>=3%且研发人员占比>=10%")
    out = out.mask(both, "名义税率<=15% 且 研发指标接近门槛")
    return out


PROXY_FIELDS: dict[str, dict] = {
    "hightech_signal_proxy": {"fn": _hightech_signal, "desc": "高新技术企业资格线索（1/0）"},
    "hightech_signal_reason": {"fn": _hightech_signal_reason, "desc": "资格线索触发原因"},
}


def apply_backfill(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """执行多源合并 + 结构性 0 + 显式代理，返回 (df, report)。"""
    df = df.copy()
    report: dict = {"coalesced": {}, "derived_zero": {}, "proxy": {}, "n_rows": int(len(df))}

    for field, alts in COALESCE_FIELDS.items():
        if field not in df.columns:
            continue
        before = float(df[field].notna().mean())
        for alt in alts:
            if alt in df.columns:
                df[field] = df[field].fillna(pd.to_numeric(df[alt], errors="coerce"))
        after = float(df[field].notna().mean())
        if after > before:
            report["coalesced"][field] = {"before": round(before, 4), "after": round(after, 4),
                                          "sources": alts}

    for child, parent in DERIVE_ZERO.items():
        if child not in df.columns or parent not in df.columns:
            continue
        before = float(df[child].notna().mean())
        mask = _num(df, parent).eq(0) & df[child].isna()
        df.loc[mask, child] = 0.0
        after = float(df[child].notna().mean())
        if after > before:
            report["derived_zero"][child] = {"before": round(before, 4), "after": round(after, 4),
                                             "rule": f"{parent}==0 -> 0"}

    for name, spec in PROXY_FIELDS.items():
        df[name] = spec["fn"](df)
        report["proxy"][name] = {"coverage": round(float(df[name].notna().mean()), 4),
                                 "desc": spec["desc"]}
    return df, report


def render_report(report: dict) -> str:
    L = ["# 低覆盖字段回填报告（Update 5.2）", "",
         f"- 样本数：{report.get('n_rows')}",
         "- 原则：同义多源合并；资格事实**不**由财务指标回填，仅生成显式代理（`*_proxy`）。", ""]
    L.append("## 一、同义多源合并（coalesce）")
    if report["coalesced"]:
        L += ["| 字段 | 覆盖率前 | 覆盖率后 | 来源 |", "|---|---|---|---|"]
        for k, v in report["coalesced"].items():
            L.append(f"| {k} | {v['before']:.1%} | {v['after']:.1%} | {', '.join(v['sources'])} |")
    else:
        L.append("（无）")
    L += ["", "## 二、结构性 0（父为 0 → 子为 0）"]
    if report["derived_zero"]:
        L += ["| 字段 | 覆盖率前 | 覆盖率后 | 规则 |", "|---|---|---|---|"]
        for k, v in report["derived_zero"].items():
            L.append(f"| {k} | {v['before']:.1%} | {v['after']:.1%} | {v['rule']} |")
    else:
        L.append("（无）")
    L += ["", "## 三、显式代理字段（线索，不支撑 CONFIRMED）",
          "| 字段 | 覆盖率 | 说明 |", "|---|---|---|"]
    for k, v in report["proxy"].items():
        L.append(f"| {k} | {v['coverage']:.1%} | {v['desc']} |")
    return "\n".join(L) + "\n"
