"""比值类衍生指标。"""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.derived.catalog import register

CATEGORY = "比值类"


def safe_div(a, b):
    """安全除法：任一为空返回 None，分母为 0 置 NaN，避免除零与类型错误。"""
    if a is None or b is None:
        return None
    a = pd.to_numeric(a, errors="coerce")
    b = pd.to_numeric(b, errors="coerce")
    return a / b.replace(0, np.nan)


def _get(df: pd.DataFrame, col: str):
    """取列，不存在返回 None（供 safe_div 处理缺失字段）。"""
    return df[col] if col in df.columns else None


def _add(d, name, cn, formula, inputs, series, unit="%", sources="", note=""):
    """写入比值列并登记台账；series 为 None 时以 NaN 占位。"""
    d[name] = series if series is not None else np.nan
    register(name, cn, CATEGORY, formula, inputs, unit=unit, source_tables=sources, note=note)


def compute(master: pd.DataFrame) -> pd.DataFrame:
    """计算全部比值类指标（盈利能力/费用结构/偿债/资产结构/税收/成长）。

    口径：金额比率默认单位为 %（除非显式指定倍）；缺失字段由 safe_div 返回 None。
    """
    d = master[["stock_code", "year"]].copy()

    # ---- 盈利能力（利润表 050312367）
    rev = _get(master, "is_revenue"); cost = _get(master, "is_cost")
    tp = _get(master, "is_total_profit"); npf = _get(master, "is_net_profit")
    npf_p = _get(master, "is_net_profit_parent"); op = _get(master, "is_operating_profit")
    ta = _get(master, "bs_total_assets"); eq = _get(master, "bs_total_equity")

    _add(d, "gross_margin", "毛利率", "(营业收入-营业成本)/营业收入", "is_revenue,is_cost",
         safe_div(rev - cost, rev), sources="利润表")
    _add(d, "operating_margin", "营业利润率", "营业利润/营业收入", "is_operating_profit,is_revenue",
         safe_div(op, rev), sources="利润表")
    _add(d, "net_margin", "净利率", "净利润/营业收入", "is_net_profit,is_revenue",
         safe_div(npf, rev), sources="利润表")
    _add(d, "roa", "总资产收益率", "净利润/资产总计", "is_net_profit,bs_total_assets",
         safe_div(npf, ta), sources="利润表,资产负债表")
    _add(d, "roe", "净资产收益率", "净利润/所有者权益合计", "is_net_profit,bs_total_equity",
         safe_div(npf, eq), sources="利润表,资产负债表")
    _add(d, "profit_before_tax_margin", "利润总额率", "利润总额/营业收入", "is_total_profit,is_revenue",
         safe_div(tp, rev), sources="利润表")

    # ---- 费用结构
    rd = _get(master, "is_rd_expense"); rd_sum = _get(master, "rd_spend_sum")
    adm = _get(master, "is_admin_expense"); sell = _get(master, "is_selling_expense")
    fin = _get(master, "is_finance_expense"); taxs = _get(master, "is_tax_surcharge")
    _add(d, "rd_expense_ratio", "研发费用率(利润表)", "研发费用/营业收入", "is_rd_expense,is_revenue",
         safe_div(rd, rev), sources="利润表")
    _add(d, "rd_spend_ratio", "研发投入占收入比", "研发投入金额/营业收入", "rd_spend_sum,is_revenue",
         safe_div(rd_sum, rev), sources="研发投入情况表,利润表")
    _add(d, "rd_expense_rate", "研发费用化率", "费用化研发投入/研发投入金额", "rd_expenses,rd_spend_sum",
         safe_div(_get(master, "rd_expenses"), rd_sum), sources="研发投入情况表")
    _add(d, "rd_capitalize_rate", "研发资本化率", "资本化研发投入/研发投入金额", "rd_invest,rd_spend_sum",
         safe_div(_get(master, "rd_invest"), rd_sum), sources="研发投入情况表")
    _add(d, "admin_expense_ratio", "管理费用率", "管理费用/营业收入", "is_admin_expense,is_revenue",
         safe_div(adm, rev), sources="利润表")
    _add(d, "selling_expense_ratio", "销售费用率", "销售费用/营业收入", "is_selling_expense,is_revenue",
         safe_div(sell, rev), sources="利润表")
    _add(d, "finance_expense_ratio", "财务费用率", "财务费用/营业收入", "is_finance_expense,is_revenue",
         safe_div(fin, rev), sources="利润表")
    _add(d, "period_expense_ratio", "期间费用率", "(销售+管理+财务+研发费用)/营业收入",
         "is_selling_expense,is_admin_expense,is_finance_expense,is_rd_expense,is_revenue",
         safe_div(sell + adm + fin + rd, rev), sources="利润表")
    _add(d, "tax_surcharge_ratio", "税金及附加率", "税金及附加/营业收入", "is_tax_surcharge,is_revenue",
         safe_div(taxs, rev), sources="利润表")
    _add(d, "interest_expense_ratio", "利息费用率(收入比)", "利息费用/营业收入",
         "is_interest_expense,is_revenue", safe_div(_get(master, "is_interest_expense"), rev), sources="利润表")

    # ---- 偿债与资本结构
    tl = _get(master, "bs_total_liabilities"); ca = _get(master, "bs_current_assets")
    cl = _get(master, "bs_current_liabilities"); inv = _get(master, "bs_inventory")
    short_loan = _get(master, "bs_short_loan"); long_loan = _get(master, "bs_long_loan")
    bonds = _get(master, "bs_bonds_payable"); cur_portion = _get(master, "bs_current_portion_noncurrent_liability")
    # 资产负债表的借款科目缺失时，回退用借款明细表口径
    if short_loan is None and long_loan is None:
        short_loan = _get(master, "borrow_short_term")
        long_loan = _get(master, "borrow_long_term")
        cur_portion = _get(master, "borrow_current_portion")
    interest_debt = None
    # 有息负债 = 短期借款 + 长期借款 + 应付债券 + 一年内到期非流动负债
    for s in (short_loan, long_loan, bonds, cur_portion):
        if s is not None:
            interest_debt = s.fillna(0) if interest_debt is None else interest_debt + s.fillna(0)

    _add(d, "asset_liability_ratio", "资产负债率", "负债合计/资产总计", "bs_total_liabilities,bs_total_assets",
         safe_div(tl, ta), sources="资产负债表")
    _add(d, "current_ratio", "流动比率", "流动资产合计/流动负债合计", "bs_current_assets,bs_current_liabilities",
         safe_div(ca, cl), unit="倍", sources="资产负债表")
    _add(d, "quick_ratio", "速动比率", "(流动资产-存货)/流动负债",
         "bs_current_assets,bs_inventory,bs_current_liabilities", safe_div(ca - inv, cl), unit="倍", sources="资产负债表")
    _add(d, "interest_debt_ratio", "有息负债率", "有息负债/资产总计",
         "bs_short_loan,bs_long_loan,bs_bonds_payable,bs_total_assets", safe_div(interest_debt, ta), sources="资产负债表")
    _add(d, "interest_debt_to_equity", "有息负债权益比", "有息负债/所有者权益合计",
         "bs_total_equity", safe_div(interest_debt, eq), unit="倍", sources="资产负债表")
    _add(d, "equity_ratio", "产权比率", "所有者权益合计/资产总计", "bs_total_equity,bs_total_assets",
         safe_div(eq, ta), sources="资产负债表")
    _add(d, "bank_loan_ratio", "银行借款占总资产比", "(短期+长期借款)/资产总计",
         "bs_short_loan,bs_long_loan,bs_total_assets", safe_div((short_loan.fillna(0) + long_loan.fillna(0)) if short_loan is not None and long_loan is not None else None, ta), sources="资产负债表")

    # ---- 资产结构
    fa = _get(master, "bs_fixed_assets"); ia = _get(master, "bs_intangible_assets")
    gw = _get(master, "bs_goodwill"); cash = _get(master, "bs_cash")
    dev = _get(master, "bs_development_expense")
    # 各项资产 / 总资产，反映资产结构与可折旧/摊销基础
    _add(d, "fixed_asset_ratio", "固定资产占比", "固定资产净额/资产总计", "bs_fixed_assets,bs_total_assets",
         safe_div(fa, ta), sources="资产负债表")
    _add(d, "intangible_asset_ratio", "无形资产占比", "无形资产净额/资产总计", "bs_intangible_assets,bs_total_assets",
         safe_div(ia, ta), sources="资产负债表")
    _add(d, "goodwill_ratio", "商誉占比", "商誉净额/资产总计", "bs_goodwill,bs_total_assets",
         safe_div(gw, ta), sources="资产负债表")
    _add(d, "inventory_ratio", "存货占比", "存货净额/资产总计", "bs_inventory,bs_total_assets",
         safe_div(inv, ta), sources="资产负债表")
    _add(d, "cash_ratio", "货币资金占比", "货币资金/资产总计", "bs_cash,bs_total_assets",
         safe_div(cash, ta), sources="资产负债表")
    _add(d, "dev_expense_ratio", "开发支出占比", "开发支出/资产总计", "bs_development_expense,bs_total_assets",
         safe_div(dev, ta), sources="资产负债表")

    # ---- 税收
    it = _get(master, "is_income_tax")
    # ETR = 所得税费用 / 利润总额；名义税率直接取披露值
    _add(d, "etr", "实际税率ETR", "所得税费用/利润总额", "is_income_tax,is_total_profit",
         safe_div(it, tp), sources="利润表")
    _add(d, "tax_burden_ratio", "税负率(所得税/收入)", "所得税费用/营业收入", "is_income_tax,is_revenue",
         safe_div(it, rev), sources="利润表")
    _add(d, "nominal_tax_rate", "名义税率", "企业披露企业所得税名义税率", "income_tax_rate",
         _get(master, "income_tax_rate"), sources="税项名义税率", note="CSMAR披露值")
    _add(d, "tax_rate_gap", "税率差(名义-实际)", "名义税率-实际税率", "income_tax_rate,etr",
         _get(master, "income_tax_rate") - safe_div(it, tp), sources="税项名义税率,利润表")

    # ---- 成长
    # 按企业时序排序后 pct_change 计算同比，再对齐回原索引
    grp = master.sort_values(["stock_code", "year"]).groupby("stock_code")
    _add(d, "revenue_growth", "营业收入增长率", "(本期-上期)/|上期|",
         "is_revenue", grp["is_revenue"].pct_change().reindex(master.index), sources="利润表")
    _add(d, "net_profit_growth", "净利润增长率", "(本期-上期)/|上期|",
         "is_net_profit", grp["is_net_profit"].pct_change().reindex(master.index), sources="利润表")
    _add(d, "rd_growth", "研发投入增长率", "(本期-上期)/|上期|",
         "rd_spend_sum", grp["rd_spend_sum"].pct_change().reindex(master.index), sources="研发投入情况表")
    _add(d, "gov_subsidy_ratio", "政府补助占收入比", "政府补助/营业收入", "gov_subsidy_total,is_revenue",
         safe_div(_get(master, "gov_subsidy_total"), rev), sources="政府补助,利润表")

    return d
