"""费用明细归类：把管理费用/销售费用明细项按关键词归入税务关注类别。

来源：
    管理费用(210935890)  项目 FN05201 / 金额 FN05202
    销售费用(210924591)  项目 FN05001 / 金额 FN05002

输出（公司×年）：业务招待费、广告宣传费、折旧摊销、职工薪酬、研发费用、运输费、股份支付、其他。
注意：剔除"合计"行，避免与分项重复计算。
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

# 类别 -> 关键词正则
CATEGORIES: dict[str, str] = {
    "entertainment": r"招待",
    "advertising": r"广告|宣传|推广|展览|促销",
    "depreciation": r"折旧|摊销",
    "payroll": r"职工薪酬|工资|薪酬|福利",
    "rd": r"研发",
    "transport": r"运输|运杂|仓储|包装|装卸",
    "share_based": r"股份支付|股权激励",
}

OUT_COLS = {
    "entertainment": "expense_entertainment",
    "advertising": "expense_advertising",
    "depreciation": "expense_depreciation_in_expense",
    "payroll": "expense_payroll_in_expense",
    "rd": "expense_rd_in_expense",
    "transport": "expense_transport",
    "share_based": "expense_share_based",
}

SOURCES = [
    ("210935890", "FN05201", "FN05202"),
    ("210924591", "FN05001", "FN05002"),
]


def _classify(item: str) -> str | None:
    """按关键词把费用项目名归入类别；合计/小计/空值返回 None（不参与归类）。"""
    s = str(item)
    # 合计/小计行会与分项重复，直接剔除
    if "合计" in s or "小计" in s or s.strip() in {"", "nan"}:
        return None
    for cat, rx in CATEGORIES.items():
        if re.search(rx, s):
            return cat
    return "other"


def compute_expense_classification() -> pd.DataFrame:
    """读取管理/销售费用明细，按关键词归类并汇总为 (企业, 年度) 宽表。

    输出列对应 OUT_COLS；同一类别跨两个来源相加。若明细表可用，
    再用其招待费数据回填，弥补关键词归类遗漏。
    """
    frames = []
    for did, item_col, amt_col in SOURCES:
        path = TABLES / f"{did}.parquet"
        if not path.exists():
            log.warning("缺少标准化表 %s，跳过", did)
            continue
        df = pd.read_parquet(path)
        if "stock_code" not in df.columns or "year" not in df.columns:
            continue
        # 统一口径：合并报表、本期、主键完整
        df = filter_report_type(df, "合并")
        df = filter_current_period(df)
        df = df.dropna(subset=GROUP)
        if df.empty:
            continue
        if item_col not in df.columns or amt_col not in df.columns:
            continue
        work = df[GROUP + [item_col, amt_col]].copy()
        work["_cat"] = work[item_col].map(_classify)
        work["_amt"] = pd.to_numeric(work[amt_col], errors="coerce")
        work = work.dropna(subset=["_cat"])
        if work.empty:
            continue
        # 透视成 企业×年度 × 类别 的宽表
        pivot = work.pivot_table(index=GROUP, columns="_cat", values="_amt",
                                 aggfunc="sum", observed=True)
        pivot.columns = [OUT_COLS.get(c, f"expense_{c}") for c in pivot.columns]
        frames.append(pivot)

    if not frames:
        combined = pd.DataFrame(columns=GROUP)
    else:
        # 管理/销售费用合并：同类相加
        combined = pd.concat(frames, axis=0).groupby(GROUP).sum(min_count=1).reset_index()

    # 补充：费用明细表(165419839) 的招待费（管理+销售），填补归类缺失
    detail = TABLES / "165419839.parquet"
    if detail.exists():
        df = pd.read_parquet(detail)
        if {"stock_code", "year"} <= set(df.columns):
            df = filter_report_type(df, "合并")
            df = filter_current_period(df).dropna(subset=GROUP)
            e1 = pd.to_numeric(df.get("FN_Fn07315"), errors="coerce")
            e2 = pd.to_numeric(df.get("FN_Fn07316"), errors="coerce")
            if e1 is not None or e2 is not None:
                tmp = df[GROUP].copy()
                # 招待费 = 管理口径 + 销售口径（缺失记 0）
                tmp["_ent"] = (e1 if e1 is not None else 0).fillna(0) + (e2 if e2 is not None else 0).fillna(0)
                ent = tmp.groupby(GROUP)["_ent"].sum(min_count=1).reset_index()
                ent = ent.rename(columns={"_ent": "expense_entertainment_detail"})
                combined = combined.merge(ent, on=GROUP, how="outer")
                # 关键词归类为空时用明细表口径兜底，否则仅填补缺失
                if "expense_entertainment" not in combined.columns:
                    combined["expense_entertainment"] = combined["expense_entertainment_detail"]
                else:
                    combined["expense_entertainment"] = combined["expense_entertainment"].fillna(
                        combined["expense_entertainment_detail"])
                combined = combined.drop(columns=["expense_entertainment_detail"])

    log.info("费用明细归类: %s 行, 类别列 %s", len(combined),
             [c for c in combined.columns if c not in GROUP])
    return combined


if __name__ == "__main__":
    df = compute_expense_classification()
    print(df.head())
