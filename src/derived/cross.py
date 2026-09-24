"""交叉计算类衍生指标。"""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.derived.catalog import register
from src.derived.ratios import safe_div

CATEGORY = "交叉计算"


def _get(df, col):
    return df[col] if col in df.columns else None


def _add(d, name, cn, formula, inputs, series, unit="元", sources="", note=""):
    d[name] = series if series is not None else np.nan
    register(name, cn, CATEGORY, formula, inputs, unit=unit, source_tables=sources, note=note)


def compute(master: pd.DataFrame) -> pd.DataFrame:
    d = master[["stock_code", "year"]].copy()

    rd_sum = _get(master, "rd_spend_sum")
    rd_exp = _get(master, "is_rd_expense")
    nominal = _get(master, "income_tax_rate")
    # 名义税率为百分比，缺失按 25%
    rate = (pd.to_numeric(nominal, errors="coerce") / 100.0).fillna(0.25)

    # 研发加计扣除潜在空间：研发投入 × 加计比例(100%) × 适用税率
    rd_for_deduction = rd_sum if rd_sum is not None else rd_exp
    _add(d, "rd_super_deduction_potential", "研发加计扣除潜在节税空间",
         "研发投入 × 100% × 适用税率", "rd_spend_sum,income_tax_rate",
         rd_for_deduction * 1.0 * rate if rd_for_deduction is not None else None,
         sources="研发投入情况表,利润表,税项名义税率", note="按100%加计、名义税率估算，非最终结论")

    # 利息：有息负债、隐含利率、债权融资依赖
    short_loan = _get(master, "bs_short_loan"); long_loan = _get(master, "bs_long_loan")
    bonds = _get(master, "bs_bonds_payable"); cur_portion = _get(master, "bs_current_portion_noncurrent_liability")
    ie = None
    for s in (short_loan, long_loan, bonds, cur_portion):
        if s is not None:
            ie = s.fillna(0) if ie is None else ie + s.fillna(0)
    int_exp = _get(master, "is_interest_expense")
    if int_exp is None:
        int_exp = _get(master, "fe_interest_expense")
    fin_exp = _get(master, "is_finance_expense")
    int_best = int_exp.fillna(fin_exp) if int_exp is not None else fin_exp
    _add(d, "interest_expense_best", "利息费用(财务费用兜底)", "利息费用；缺失时用财务费用近似",
         "is_interest_expense,fe_interest_expense,is_finance_expense", int_best, unit="元",
         sources="利润表,财务费用明细", note="部分企业未单独披露利息费用，以财务费用近似")
    _add(d, "implied_interest_rate", "隐含债务利率", "利息费用/有息负债", "is_interest_expense,bs_short_loan,bs_long_loan",
         safe_div(int_exp, ie), unit="%", sources="利润表,资产负债表")
    _add(d, "interest_debt_to_equity_cross", "债资比(有息负债/权益)", "有息负债/所有者权益合计",
         "bs_short_loan,bs_long_loan,bs_bonds_payable,bs_total_equity", safe_div(ie, _get(master, "bs_total_equity")),
         unit="倍", sources="资产负债表", note="以整体有息负债近似关联方债务，非精确债资比")

    # 固定资产一次性扣除空间（粗略）：购建长期资产现金支出 × 税率
    capex = _get(master, "cf_capex")
    _add(d, "capex_tax_shield_potential", "资本支出税前影响空间", "购建长期资产现金支出 × 税率",
         "cf_capex,income_tax_rate", (capex * rate) if capex is not None else None,
         sources="现金流量表,税项名义税率", note="粗略估算，需结合一次性扣除政策条件")

    # 非债务税盾
    _add(d, "non_debt_tax_shield", "非债务税盾(CSMAR)", "固定资产折旧等/总资产", "csmar__NonDebtTaxShield",
         _get(master, "csmar__NonDebtTaxShield"), unit="%", sources="财务指标")

    # 政府补助与研发重叠
    gov = _get(master, "gov_subsidy_total")
    _add(d, "gov_subsidy_over_rd", "政府补助/研发投入", "政府补助/研发投入金额", "gov_subsidy_total,rd_spend_sum",
         safe_div(gov, rd_sum), unit="%", sources="政府补助,研发投入情况表")

    # 关联交易占比
    ma_rel = _get(master, "ma_related_party_count"); ma_all = _get(master, "ma_event_count")
    _add(d, "related_party_deal_ratio", "关联交易事件占比", "关联交易事件数/交易事件总数",
         "ma_related_party_count,ma_event_count", safe_div(ma_rel, ma_all), unit="%", sources="交易信息总表")

    # 海外子公司占比
    ov = _get(master, "subsidiary_overseas_count"); sub = _get(master, "subsidiary_count")
    _add(d, "overseas_subsidiary_ratio", "海外子公司占比", "海外子公司数/子公司总数",
         "subsidiary_overseas_count,subsidiary_count", safe_div(ov, sub), unit="%", sources="上市公司子公司情况表")

    # 资产密集度
    fa = _get(master, "bs_fixed_assets"); cip = _get(master, "bs_cip"); rev = _get(master, "is_revenue")
    _add(d, "asset_intensity", "资产密集度", "(固定资产+在建工程)/营业收入", "bs_fixed_assets,bs_cip,is_revenue",
         safe_div((fa.fillna(0) + cip.fillna(0)) if fa is not None and cip is not None else None, rev),
         unit="倍", sources="资产负债表,利润表")

    # 研发人员占比（来自研发投入表）
    _add(d, "rd_person_ratio_cross", "研发人员占比", "研发人员数量/员工总数", "rd_person,employee_amount_sum",
         safe_div(_get(master, "rd_person"), _get(master, "employee_amount_sum")), unit="%",
         sources="研发投入情况表,上市公司人员结构表")

    # 减值损失合计（资产减值 + 信用减值）
    ai = _get(master, "is_asset_impairment"); ci = _get(master, "is_credit_impairment")
    if ai is not None or ci is not None:
        total_imp = (ai.fillna(0) if ai is not None else 0) + (ci.fillna(0) if ci is not None else 0)
        _add(d, "impairment_total", "减值损失合计", "资产减值损失 + 信用减值损失",
             "is_asset_impairment,is_credit_impairment", total_imp, unit="元", sources="利润表")

    # 职工薪酬基数（本期计提，缺失用应付职工薪酬期末）
    accrued = _get(master, "d210230772__Fn03203")
    payable = _get(master, "bs_payroll_payable")
    if accrued is not None or payable is not None:
        base = (accrued if accrued is not None else payable)
        if accrued is not None and payable is not None:
            base = accrued.fillna(payable)
        _add(d, "employee_compensation_base", "职工薪酬计提基数", "应付职工薪酬本期增加额（缺失用期末余额）",
             "d210230772__Fn03203,bs_payroll_payable", base, unit="元",
             sources="应付职工薪酬,资产负债表")

    # 关联交易金额（前五大客户/供应商）
    top5_amt = _get(master, "d211406861__FN_Fn01005")
    if top5_amt is not None:
        _add(d, "top5_customer_supplier_amount", "前五大客户/供应商金额", "前五大客户与供应商业务往来金额合计",
             "d211406861__FN_Fn01005", top5_amt, unit="元", sources="前五大客户、供应商情况表")

    return d
