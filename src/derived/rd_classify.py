"""研发费用构成归类：把研发费用明细项按税法加计口径归类。

来源：研发费用明细(211127976)  项目 FN_Fn06001 / 本期发生额 FN_Fn06002

按 财税〔2015〕119号 的加计口径归类：
    人员人工费用 / 直接投入费用 / 折旧费用 / 无形资产摊销 / 设计试验费 / 其他相关费用
并计算"其他相关费用"10% 限额：
    其他相关费用限额 = 前五项合计 × 10% / (1-10%)
输出（公司×年）：rd_payroll, rd_direct_input, rd_depreciation_amortization,
    rd_design_test, rd_other, rd_first_five, rd_other_limit, rd_other_excess。
"""
from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from src.common import CONFIG, get_logger
from src.data.normalize import filter_current_period, filter_report_type

log = get_logger()
TABLES = Path(CONFIG["paths"]["tables_dir"])
GROUP = ["stock_code", "year"]

# 顺序敏感：先匹配到的类别生效
RULES: list[tuple[str, str]] = [
    ("rd_payroll", r"人员人工|人工费|人工成本|职工薪酬|工资|薪金|薪酬"),
    ("rd_direct_input", r"直接投入|材料|物料|燃料|动力|试制|检测|检验|模具|工艺装备"),
    ("rd_depreciation_amortization", r"折旧|摊销"),
    ("rd_design_test", r"设计|试验|测试"),
    ("rd_other", r"其他|差旅|办公|招待|会议|通讯|租赁|水电|技术|咨询|知识产权|评估|验"),
]
OUT_FIELDS = [c for c, _ in RULES]


def _classify(item: str) -> str | None:
    s = str(item)
    if "合计" in s or "小计" in s or s.strip() in {"", "nan"}:
        return None
    for cat, rx in RULES:
        if re.search(rx, s):
            return cat
    return None


def compute_rd_classification() -> pd.DataFrame:
    path = TABLES / "211127976.parquet"
    if not path.exists():
        return pd.DataFrame(columns=GROUP)
    df = pd.read_parquet(path)
    item_col = next((c for c in df.columns if "Fn06001" in c), None)
    amt_col = next((c for c in df.columns if "Fn06002" in c), None)
    if item_col is None or amt_col is None or "stock_code" not in df.columns:
        return pd.DataFrame(columns=GROUP)

    df = filter_report_type(df, "合并")
    df = filter_current_period(df).dropna(subset=GROUP)
    if df.empty:
        return pd.DataFrame(columns=GROUP)

    work = df[GROUP + [item_col, amt_col]].copy()
    work["_cat"] = work[item_col].map(_classify)
    work["_amt"] = pd.to_numeric(work[amt_col], errors="coerce")
    work = work.dropna(subset=["_cat"])
    if work.empty:
        return pd.DataFrame(columns=GROUP)

    pivot = work.pivot_table(index=GROUP, columns="_cat", values="_amt", aggfunc="sum", observed=True)
    for c in OUT_FIELDS:
        if c not in pivot.columns:
            pivot[c] = pd.NA
    pivot = pivot[OUT_FIELDS].reset_index()

    # 前五项合计 + 其他相关费用 10% 限额
    first_five = pivot[["rd_payroll", "rd_direct_input", "rd_depreciation_amortization",
                        "rd_design_test"]].fillna(0).sum(axis=1)
    pivot["rd_first_five"] = first_five.where(first_five > 0)
    limit = first_five * 0.1 / 0.9
    pivot["rd_other_limit"] = limit.where(first_five > 0)
    excess = (pivot["rd_other"].fillna(0) - limit).clip(lower=0)
    pivot["rd_other_excess"] = excess.where(first_five > 0)

    # 仅单条"其他"披露（无前五项构成）时，不作为可加计口径依据
    log.info("研发费用构成归类: %s 行（其中含前五项构成 %s 行）",
             len(pivot), int((first_five > 0).sum()))
    return pivot


if __name__ == "__main__":
    print(compute_rd_classification().head())
