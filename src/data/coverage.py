"""关键事实覆盖率台账（Update 5.2 · P1）。

对关键事实字段给出**总体与按年**覆盖率，并标注低覆盖字段的应对方式：
    proxy   —— 已有显式代理字段（线索，不支撑 CONFIRMED）
    evidence—— 需外部证据（证书/申报表/辅助账等）
    gap     —— 数据源缺失，只能 DATA_GAP

用途：避免把"字段缺失"误当成"事实为否"，并为 Skill 审查提供数据可用性依据。
"""
from __future__ import annotations

import pandas as pd

# 关键事实 -> 应对方式
KEY_FACTS: dict[str, str] = {
    "is_hightech": "proxy",
    "qualification_hightech_count": "proxy",
    "bs_development_expense": "evidence",
    "d045152435__n_rows": "evidence",
    "subsidiary_overseas_count": "evidence",
    "subsidiary_hk_mo_tw_count": "evidence",
    "interest_debt_ratio": "evidence",
    "rd_spend_sum": "evidence",
    "rd_expense_ratio": "evidence",
    "rd_person_ratio_cross": "evidence",
    "employee_compensation_base": "proxy",
    "gov_subsidy_total": "evidence",
    "is_income_tax": "proxy",
    "nominal_tax_rate": "proxy",
}

LOW_COVERAGE = 0.30


def coverage_ledger(df: pd.DataFrame) -> list[dict]:
    rows: list[dict] = []
    n = len(df)
    years = sorted(pd.to_numeric(df["year"], errors="coerce").dropna().unique().tolist()) \
        if "year" in df.columns else []
    for field, mitigation in KEY_FACTS.items():
        if field not in df.columns:
            rows.append({"field": field, "coverage": 0.0, "n": 0,
                         "mitigation": mitigation, "low": True, "by_year": {}})
            continue
        cov = float(df[field].notna().mean()) if n else 0.0
        by_year = {}
        if years:
            for y in years:
                sub = df[pd.to_numeric(df["year"], errors="coerce") == y]
                by_year[int(y)] = round(float(sub[field].notna().mean()), 4) if len(sub) else None
        rows.append({"field": field, "coverage": round(cov, 4), "n": int(df[field].notna().sum()),
                     "mitigation": mitigation, "low": cov < LOW_COVERAGE, "by_year": by_year})
    return rows


def render_ledger(rows: list[dict]) -> str:
    L = ["# 关键事实覆盖率台账（Update 5.2）", "",
         f"- 低覆盖阈值：{LOW_COVERAGE:.0%}",
         "- 应对：proxy=已有显式代理线索；evidence=需外部证据；gap=数据源缺失。", "",
         "| 字段 | 覆盖率 | 非空 | 低覆盖 | 应对 | 按年 |", "|---|---|---|---|---|---|"]
    for r in sorted(rows, key=lambda x: x["coverage"]):
        by = "、".join(f"{y}:{v:.0%}" for y, v in (r.get("by_year") or {}).items() if v is not None)
        L.append(f"| {r['field']} | {r['coverage']:.1%} | {r['n']} | "
                 f"{'⚠' if r['low'] else ''} | {r['mitigation']} | {by} |")
    return "\n".join(L) + "\n"
