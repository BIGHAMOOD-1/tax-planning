"""精选字段映射：把关键数据集抽取成面向税务筹划的语义列（键 = 股票代码 × 年份）。

设计约定：
- 每个 `h_*` 函数对应一个数据集，输入标准化表、输出以 (stock_code, year) 为主键的语义列；
- 财务报表类统一先 `_base`（合并报表 + 最新披露去重），明细类先剔除合计/备忘行再按项目文本或代码聚合；
- 汇总一律用 `sum(min_count=1)`，保证"全空"不被误算为 0，从而与"无数据"区分；
- 注册表 `CURATED` 把 dataset_id 映射到处理函数，供 merge 阶段调用。
"""
from __future__ import annotations

from typing import Callable

import pandas as pd

from src.common import get_logger, norm_col
from src.data.normalize import filter_report_type

log = get_logger()

GROUP = ["stock_code", "year"]


# ------------------------------------------------------------------ 工具

def _col(df: pd.DataFrame, candidates: list[str]) -> str | None:
    """按候选名（归一化后）定位实际列名，命中首个即返回。"""
    lut = {norm_col(c): c for c in df.columns}
    for cand in candidates:
        c = lut.get(norm_col(cand))
        if c is not None:
            return c
    return None


def _num(df: pd.DataFrame, candidates: list[str]) -> pd.Series | None:
    """取数值列并强制转数值；列不存在返回 None，无法解析的取值转 NaN。"""
    c = _col(df, candidates)
    if c is None:
        return None
    return pd.to_numeric(df[c], errors="coerce")


def _txt(df: pd.DataFrame, candidates: list[str]) -> pd.Series | None:
    """取文本列；列不存在返回 None。"""
    return df[_col(df, candidates)] if _col(df, candidates) else None


def _base(df: pd.DataFrame) -> pd.DataFrame:
    """取合并报表、按 (股票代码, 年份) 去重。"""
    work = filter_report_type(df, "合并")
    if "stock_code" not in work.columns or "year" not in work.columns:
        return work.iloc[0:0]
    work = work.dropna(subset=["stock_code", "year"])
    # 先按报告日排序，drop_duplicates keep="last" 保留最新披露
    work = work.sort_values("report_date").drop_duplicates(GROUP, keep="last")
    return work


def _frame(df: pd.DataFrame, cols: dict[str, pd.Series | None]) -> pd.DataFrame:
    """按列名 -> Series 映射组装结果表，跳过 None 列，索引对齐 df。"""
    out = df[GROUP].copy()
    for name, series in cols.items():
        if series is not None:
            out[name] = series.reindex(df.index)
    return out


# ------------------------------------------------------------------ 各数据集

def h_company_info(df: pd.DataFrame) -> pd.DataFrame:
    """上市公司基本信息年度表 044219154"""
    w = _base(df)
    # 企业基础属性：用于画像展示与行业/地区维度分析
    return _frame(w, {
        "short_name": _txt(w, ["ShortName"]),
        "industry_name": _txt(w, ["IndustryName"]),
        "province": _txt(w, ["PROVINCE", "Province"]),
        "city": _txt(w, ["CITY", "City"]),
        "listing_date": _txt(w, ["LISTINGDATE", "ListingDate"]),
        "register_capital": _num(w, ["RegisterCapital"]),
        "main_business": _txt(w, ["MAINBUSSINESS", "MainBussiness"]),
        "listing_state": _txt(w, ["LISTINGSTATE", "ListingState"]),
    })


def h_fin_index(df: pd.DataFrame) -> pd.DataFrame:
    """财务指标 212335916（列名已是英文语义）"""
    w = _base(df)
    # 该表列名本身即为语义英文，直接逐列提取；统一加 csmar__ 前缀避免与财报表列冲突
    fields = [
        "TotalAssets", "TotalLiabilities", "OperatingRevenue", "OperatingNetCashFlow",
        "ROA", "AssetLiabilityRatio", "FinancialLiability", "OperatingLiability",
        "BookToMarketRatio", "ManagementExpenseRate", "TangibleAssetRatio", "CurrentRatio",
        "InventoryTurnover", "WorkingCapitalTurnover", "CashEquivalentsTurnover",
        "OperatingRevenueGrowth", "NonDebtTaxShield", "OperatingRevenueGrowthB",
        "IncomeTaxTate", "ProfitsVolatility", "CashFlowVolatility", "InterestCoverageRatio",
        "TaxBearing", "BankLoanRatio", "ShortLoanDependence", "ShareholdersOccupy",
        "IndustryCode", "IndustryName",
    ]
    cols = {f"csmar__{f}": _num(w, [f]) for f in fields}
    cols["csmar__IndustryCode"] = _txt(w, ["IndustryCode"])
    cols["csmar__IndustryName"] = _txt(w, ["IndustryName"])
    return _frame(w, cols)


def h_equity_nature(df: pd.DataFrame) -> pd.DataFrame:
    """中国上市公司股权性质文件 212541585"""
    w = _base(df)
    # 股权性质/控股比例/两权分离度：用于股权结构与关联方分析
    return _frame(w, {
        "equity_nature": _txt(w, ["EquityNature"]),
        "equity_nature_id": _num(w, ["EquityNatureID"]),
        "largest_holder": _txt(w, ["LargestHolder"]),
        "largest_holder_rate": _num(w, ["LargestHolderRate"]),
        "top_ten_holders_rate": _num(w, ["TopTenHoldersRate"]),
        "separation": _num(w, ["Seperation"]),
        "hierarchy": _txt(w, ["Hierarchy"]),
        "actual_controller_nature_id": _num(w, ["ActualControllerNatureID"]),
    })


def h_rd_invest(df: pd.DataFrame) -> pd.DataFrame:
    """研发投入情况表 213111405"""
    w = _base(df)
    # 研发人员/研发费用金额及占比：研发加计与高企认定的核心口径
    return _frame(w, {
        "rd_person": _num(w, ["RDPerson"]),
        "rd_person_ratio": _num(w, ["RDPersonRatio"]),
        "rd_spend_sum": _num(w, ["RDSpendSum"]),
        "rd_spend_sum_ratio": _num(w, ["RDSpendSumRatio"]),
        "rd_expenses": _num(w, ["RDExpenses"]),
        "rd_invest": _num(w, ["RDInvest"]),
        "rd_invest_ratio": _num(w, ["RDInvestRatio"]),
        "rd_invest_netprofit_ratio": _num(w, ["RDInvestNetprofitRatio"]),
    })


def h_rd_expense_detail(df: pd.DataFrame) -> pd.DataFrame:
    """研发费用明细 211127976 -> 汇总（仅明细行，排除合计/其中）"""
    w = df.dropna(subset=["stock_code", "year"])
    w = filter_report_type(w, "合并")
    # 只取明细行，避免合计行被重复求和
    if "_row_type" in w.columns:
        w = w[w["_row_type"] == "item"]
    work = w.copy()
    work["_amt"] = pd.to_numeric(_txt(work, ["FN_Fn06002", "本期发生额"]), errors="coerce")
    g = work.groupby(GROUP)
    # 按企业-年度汇总研发费用明细：金额合计 + 明细项数（反映归集颗粒度）
    out = pd.DataFrame({
        "rd_expense_detail_sum": g["_amt"].sum(min_count=1),
        "rd_expense_detail_items": g.size(),
    }).reset_index()
    return out


def _parse_rate(series: pd.Series | None) -> pd.Series | None:
    """从"3%、5%、6%"这类文本里解析出税率(百分比)，取其中最小值。"""
    import re
    if series is None:
        return None
    def take(v):
        if v is None:
            return None
        nums = [float(x) for x in re.findall(r"(\d+(?:\.\d+)?)\s*%", str(v))]
        return min(nums) if nums else None
    return series.map(take)


def h_tax_rate(df: pd.DataFrame) -> pd.DataFrame:
    """税项名义税率 204837437 / 053541380（文本型税率，解析为百分比最小值）"""
    w = _base(df)
    # 税率文本可能含多个档位（如"3%、5%"），_parse_rate 统一取最小值作为名义税率
    return _frame(w, {
        "vat_rate": _parse_rate(_txt(w, ["Fn00401"])),
        "consumption_tax_rate": _parse_rate(_txt(w, ["Fn00402"])),
        "business_tax_rate": _parse_rate(_txt(w, ["Fn00403"])),
        "urban_maintenance_rate": _parse_rate(_txt(w, ["Fn00404"])),
        "education_surcharge_rate": _parse_rate(_txt(w, ["Fn00405"])),
        "resource_tax_rate": _parse_rate(_txt(w, ["Fn00406"])),
        "land_appreciation_rate": _parse_rate(_txt(w, ["Fn00407"])),
        "property_tax_rate": _parse_rate(_txt(w, ["Fn00408"])),
        "deed_tax_rate": _parse_rate(_txt(w, ["Fn00409"])),
        "income_tax_rate": _parse_rate(_txt(w, ["Fn00410"])),
    })


def h_gov_subsidy(df: pd.DataFrame) -> pd.DataFrame:
    """政府补助 213143417 / 121521378"""
    from src.data.aggregate import drop_total_and_memo

    w = df.dropna(subset=["stock_code", "year"])
    w = filter_report_type(w, "合并") if "report_type" in w.columns else w
    w = drop_total_and_memo(w)
    work = w.copy()
    work["_amt"] = pd.to_numeric(_txt(work, ["Amount", "本期计入当期损益的金额"]), errors="coerce")
    work["_prev"] = pd.to_numeric(_txt(work, ["PreviousAmount"]), errors="coerce")
    # 标记"与收益相关"的补助，供不征税收入判定（与资产相关的处理口径不同）
    ai = _txt(work, ["AssetsIncome", "与资产相关/与收益相关"])
    work["_is_income"] = ai.astype(str).str.contains("收益", na=False) if ai is not None else False
    g = work.groupby(GROUP)
    out = pd.DataFrame({
        "gov_subsidy_total": g["_amt"].sum(min_count=1),
        "gov_subsidy_prev": g["_prev"].sum(min_count=1),
        "gov_subsidy_items": g.size(),
    }).reset_index()
    # 单独汇总"与收益相关"的补助金额，用于不征税收入三条件分析
    income_only = work[work["_is_income"]]
    if not income_only.empty:
        out = out.merge(
            income_only.groupby(GROUP)["_amt"].sum(min_count=1)
            .rename("gov_subsidy_income_related").reset_index(),
            on=GROUP, how="left",
        )
    return out


def h_borrowing(df: pd.DataFrame) -> pd.DataFrame:
    """借款抵押 210126462：Fn02701 1短期 2长期 3一年内到期；Fn02704 年末数"""
    w = df.dropna(subset=["stock_code", "year"])
    w = filter_report_type(w, "合并") if "report_type" in w.columns else w
    if "_row_type" in w.columns:
        w = w[w["_row_type"] != "total"]
    work = w.copy()
    # Fn02701：借款类别（1短期 2长期 3一年内到期）；Fn02704：年末余额
    work["_cat"] = pd.to_numeric(_txt(work, ["Fn02701", "借款类别"]), errors="coerce")
    work["_amt"] = pd.to_numeric(_txt(work, ["Fn02704", "年末数"]), errors="coerce")
    g = work.groupby(GROUP)
    # 按借款类别拆分，用于利息扣除与资本弱化分析
    out = pd.DataFrame({
        "borrow_short_term": work[work["_cat"] == 1].groupby(GROUP)["_amt"].sum(min_count=1),
        "borrow_long_term": work[work["_cat"] == 2].groupby(GROUP)["_amt"].sum(min_count=1),
        "borrow_current_portion": work[work["_cat"] == 3].groupby(GROUP)["_amt"].sum(min_count=1),
        "borrow_detail_total": g["_amt"].sum(min_count=1),
        "borrow_detail_items": g.size(),
    }).reset_index()
    return out


def h_balance(df: pd.DataFrame) -> pd.DataFrame:
    """资产负债表 050348744"""
    w = _base(df)
    # 资产/负债/权益主要科目：A001=资产 A002=负债 A003=权益
    return _frame(w, {
        "bs_cash": _num(w, ["A001101000"]),
        "bs_accounts_receivable": _num(w, ["A001111000"]),
        "bs_inventory": _num(w, ["A001123000"]),
        "bs_current_assets": _num(w, ["A001100000"]),
        "bs_total_assets": _num(w, ["A001000000"]),
        "bs_long_term_equity_invest": _num(w, ["A001205000"]),
        "bs_fixed_assets": _num(w, ["A001212000"]),
        "bs_cip": _num(w, ["A001213000"]),
        "bs_intangible_assets": _num(w, ["A001218000"]),
        "bs_development_expense": _num(w, ["A001219000"]),
        "bs_goodwill": _num(w, ["A001220000"]),
        "bs_deferred_tax_asset": _num(w, ["A001222000"]),
        "bs_non_current_assets": _num(w, ["A001200000"]),
        "bs_short_loan": _num(w, ["A002101000"]),
        "bs_accounts_payable": _num(w, ["A002108000"]),
        "bs_contract_liability": _num(w, ["A002128000"]),
        "bs_payroll_payable": _num(w, ["A002112000"]),
        "bs_tax_payable": _num(w, ["A002113000"]),
        "bs_interest_payable": _num(w, ["A002114000"]),
        "bs_current_liabilities": _num(w, ["A002100000"]),
        "bs_long_loan": _num(w, ["A002201000"]),
        "bs_bonds_payable": _num(w, ["A002203000"]),
        "bs_lease_liability": _num(w, ["A002211000"]),
        "bs_deferred_income_noncurrent": _num(w, ["A002210000"]),
        "bs_deferred_tax_liability": _num(w, ["A002208000"]),
        "bs_non_current_liabilities": _num(w, ["A002200000"]),
        "bs_current_portion_noncurrent_liability": _num(w, ["A002125000"]),
        "bs_total_liabilities": _num(w, ["A002000000"]),
        "bs_paid_in_capital": _num(w, ["A003101000"]),
        "bs_capital_reserve": _num(w, ["A003102000"]),
        "bs_surplus_reserve": _num(w, ["A003103000"]),
        "bs_retained_earnings": _num(w, ["A003105000"]),
        "bs_equity_parent": _num(w, ["A003100000"]),
        "bs_minority_equity": _num(w, ["A003200000"]),
        "bs_total_equity": _num(w, ["A003000000"]),
    })


def h_income(df: pd.DataFrame) -> pd.DataFrame:
    """利润表 050312367"""
    w = _base(df)
    # 损益主要科目：B001=收入与成本费用 B002=所得税与净利润
    return _frame(w, {
        "is_total_revenue": _num(w, ["B001100000"]),
        "is_revenue": _num(w, ["B001101000"]),
        "is_cost": _num(w, ["B001201000"]),
        "is_tax_surcharge": _num(w, ["B001207000"]),
        "is_selling_expense": _num(w, ["B001209000"]),
        "is_admin_expense": _num(w, ["B001210000"]),
        "is_rd_expense": _num(w, ["B001216000"]),
        "is_finance_expense": _num(w, ["B001211000"]),
        "is_interest_expense": _num(w, ["B001211101"]),
        "is_interest_income": _num(w, ["B001211203"]),
        "is_other_income": _num(w, ["B001305000"]),
        "is_investment_income": _num(w, ["B001302000"]),
        "is_asset_impairment": _num(w, ["B001212000"]),
        "is_credit_impairment": _num(w, ["B001307000"]),
        "is_asset_disposal": _num(w, ["B001308000"]),
        "is_fair_value_change": _num(w, ["B001301000"]),
        "is_operating_profit": _num(w, ["B001300000"]),
        "is_non_operating_income": _num(w, ["B001400000"]),
        "is_non_operating_expense": _num(w, ["B001500000"]),
        "is_total_profit": _num(w, ["B001000000"]),
        "is_income_tax": _num(w, ["B002100000"]),
        "is_net_profit": _num(w, ["B002000000"]),
        "is_net_profit_parent": _num(w, ["B002000101"]),
    })


def h_cashflow(df: pd.DataFrame) -> pd.DataFrame:
    """现金流量表(直接法) 053202606"""
    w = _base(df)
    # 现金流科目：C001=经营 C002=投资 C003=筹资 C006=期末现金
    return _frame(w, {
        "cf_sales_cash": _num(w, ["C001001000"]),
        "cf_tax_refund": _num(w, ["C001012000"]),
        "cf_operating_inflow": _num(w, ["C001100000"]),
        "cf_goods_cash": _num(w, ["C001014000"]),
        "cf_payroll_cash": _num(w, ["C001020000"]),
        "cf_tax_paid": _num(w, ["C001021000"]),
        "cf_operating_outflow": _num(w, ["C001200000"]),
        "cf_operating_net": _num(w, ["C001000000"]),
        "cf_capex": _num(w, ["C002006000"]),
        "cf_investing_net": _num(w, ["C002000000"]),
        "cf_borrow_received": _num(w, ["C003002000"]),
        "cf_repay_debt": _num(w, ["C003005000"]),
        "cf_interest_dividend_paid": _num(w, ["C003006000"]),
        "cf_financing_net": _num(w, ["C003000000"]),
        "cf_end_cash": _num(w, ["C006000000"]),
    })


def h_fixed_assets(df: pd.DataFrame) -> pd.DataFrame:
    """固定资产 205523719：通用数值列求和 + 按科目类型提取（原值/累计折旧/减值/净额）。"""
    from src.data.aggregate import KEY_COLS, coerce_numeric

    w = df.dropna(subset=["stock_code", "year"])
    w = filter_report_type(w, "合并") if "report_type" in w.columns else w
    if "_row_type" in w.columns:
        w = w[w["_row_type"] != "total"]
    work = coerce_numeric(w)
    # 标识/文本列不参与求和
    block = {"stkcd", "symbol", "shortname", "accper", "enddate", "typrep",
             "sgnyea", "datasources", "currency", "source", "typkod"}
    num = [c for c in work.columns
           if pd.api.types.is_numeric_dtype(work[c]) and c not in KEY_COLS
           and norm_col(c) not in block]
    g = work.groupby(GROUP)
    # 通用数值列整体求和（前缀 fa__），保留科目类型明细供折旧口径使用
    out = g[num].sum(min_count=1)
    out.columns = [f"fa__{c}" for c in num]
    out["fa__n_rows"] = g.size()
    out = out.reset_index()

    typed = h_fixed_asset_depreciation(df)
    return out.merge(typed, on=GROUP, how="outer")


def h_subsidiary(df: pd.DataFrame) -> pd.DataFrame:
    """上市公司子公司情况表 165406195：数量与海外标识（FN_Fn06107 1=海外）"""
    w = df.dropna(subset=["stock_code", "year"])
    work = w.copy()
    # FN_Fn06107：地区标识（1=海外，2=港澳台）；06111/06112/06113 为子公司规模指标
    region = pd.to_numeric(_txt(work, ["FN_Fn06107"]), errors="coerce")
    work["_overseas"] = region == 1
    work["_hk_mo_tw"] = region == 2
    work["_assets"] = pd.to_numeric(_txt(work, ["FN_Fn06111"]), errors="coerce")
    work["_revenue"] = pd.to_numeric(_txt(work, ["FN_Fn06112"]), errors="coerce")
    work["_netprofit"] = pd.to_numeric(_txt(work, ["FN_Fn06113"]), errors="coerce")
    g = work.groupby(GROUP)
    out = pd.DataFrame({
        "subsidiary_count": g.size(),
        "subsidiary_overseas_count": g["_overseas"].sum(),
        "subsidiary_hk_mo_tw_count": g["_hk_mo_tw"].sum(),
        "subsidiary_total_assets": g["_assets"].sum(min_count=1),
        "subsidiary_total_revenue": g["_revenue"].sum(min_count=1),
        "subsidiary_total_netprofit": g["_netprofit"].sum(min_count=1),
    }).reset_index()
    return out


def h_related_party(df: pd.DataFrame) -> pd.DataFrame:
    """上市公司子公司联营合营情况表 045225416"""
    w = df.dropna(subset=["stock_code", "year"])
    g = w.groupby(GROUP)
    # 该表每行即一个关联主体，行数即关联方数量
    return pd.DataFrame({"related_party_count": g.size()}).reset_index()


def h_qualification(df: pd.DataFrame) -> pd.DataFrame:
    """上市公司资质认定信息文件 212918837：高新技术企业等"""
    if "stock_code" not in df.columns or "year" not in df.columns:
        if "report_date" in df.columns:
            pass
        else:
            return pd.DataFrame(columns=GROUP)
    # 该表无年份列（有 Prgrsdt 公告日期），按公告年归年
    work = df.copy()
    if "year" not in work.columns or work["year"].isna().all():
        prg = _txt(work, ["Prgrsdt"])
        work["year"] = pd.to_datetime(prg, errors="coerce").dt.year
    work = work.dropna(subset=["stock_code", "year"])
    protype = _txt(work, ["Protype"]).astype(str)
    # 资质类型含"高新技术"即视为高企资质线索（后续仍需证书证据确认）
    work["_hightech"] = protype.str.contains("高新技术", na=False)
    work["_taxrate"] = pd.to_numeric(_txt(work, ["Taxrate"]), errors="coerce")
    work["_level"] = pd.to_numeric(_txt(work, ["Level"]), errors="coerce")
    g = work.groupby(GROUP)
    out = pd.DataFrame({
        "qualification_count": g.size(),
        "qualification_hightech_count": g["_hightech"].sum(),
        "qualification_min_taxrate": g["_taxrate"].min(),
        "qualification_national_count": work[work["_level"] == 1].groupby(GROUP).size(),
    }).reset_index()
    # is_hightech：当年存在任一高企资质记录即置 1
    out["is_hightech"] = (out["qualification_hightech_count"] > 0).astype(int)
    return out


def h_impairment_detail(df: pd.DataFrame, amount_cols: list[str],
                        item_cols: list[str], prefix: str) -> pd.DataFrame:
    """减值损失明细归类（资产减值 211127049 / 信用减值 211210533）。"""
    from src.data.aggregate import drop_total_and_memo

    w = df.dropna(subset=["stock_code", "year"])
    w = filter_report_type(w, "合并") if "report_type" in w.columns else w
    w = drop_total_and_memo(w)
    work = w.copy()
    item = _txt(work, item_cols).astype(str)
    work["_amt"] = pd.to_numeric(_txt(work, amount_cols), errors="coerce")
    g = work.groupby(GROUP)

    def pick(rx: str) -> pd.Series:
        # 按项目名称正则匹配归类（如"应收/坏账/信用"归为应收类减值）
        m = item.str.contains(rx, na=False, regex=True)
        return work[m].groupby(GROUP)["_amt"].sum(min_count=1)

    return pd.DataFrame({
        f"{prefix}_total": g["_amt"].sum(min_count=1),
        f"{prefix}_items": g.size(),
        f"{prefix}_receivable": pick("应收|坏账|信用"),
        f"{prefix}_inventory": pick("存货"),
        f"{prefix}_fixed_asset": pick("固定资产|在建工程"),
        f"{prefix}_goodwill": pick("商誉"),
        f"{prefix}_long_term_equity": pick("长期股权投资"),
        f"{prefix}_intangible": pick("无形资产"),
    }).reset_index()


def h_ma_events(df: pd.DataFrame) -> pd.DataFrame:
    """交易信息总表 212658357 / 212717025：事件计数 + 金额（股权转让/评估增值）。"""
    w = df.dropna(subset=["stock_code", "year"])
    work = w.copy()
    # RelevanceSign/MajorRestructuringSign 以 Y 开头表示关联/重大重组
    rel = _txt(work, ["RelevanceSign"]).astype(str)
    maj = _txt(work, ["MajorRestructuringSign"]).astype(str)
    work["_related"] = rel.str.upper().str.startswith("Y")
    work["_major"] = maj.str.upper().str.startswith("Y")
    # 评估值/账面值/评估增值/交易费用：转让定价与重组税务分析口径
    for c, f in [("_eval", "EvaluationValue"), ("_book", "BookValue"),
                 ("_change", "EvaluationChange"), ("_expense", "ExpenseValue")]:
        work[c] = pd.to_numeric(_txt(work, [f]), errors="coerce")
    g = work.groupby(GROUP)
    return pd.DataFrame({
        "ma_event_count": g.size(),
        "ma_related_party_count": g["_related"].sum(),
        "ma_major_restructuring_count": g["_major"].sum(),
        "ma_evaluation_value": g["_eval"].sum(min_count=1),
        "ma_book_value": g["_book"].sum(min_count=1),
        "ma_evaluation_change": g["_change"].sum(min_count=1),
        "ma_expense_value": g["_expense"].sum(min_count=1),
    }).reset_index()


def h_tax_payable(df: pd.DataFrame) -> pd.DataFrame:
    """应交税费 210307520：期末值汇总 + 按税种(Q330x)拆分。"""
    w = df.dropna(subset=["stock_code", "year"])
    w = filter_report_type(w, "合并") if "report_type" in w.columns else w
    if "_row_type" in w.columns:
        w = w[w["_row_type"] != "total"]
    work = w.copy()
    # FN_Fn04303=期末余额，FN_Fn04302=期初余额，FN_Fn04301=项目编码（Q330x 税种）
    work["_end"] = pd.to_numeric(_txt(work, ["FN_Fn04303"]), errors="coerce")
    work["_begin"] = pd.to_numeric(_txt(work, ["FN_Fn04302"]), errors="coerce")
    code = _txt(work, ["FN_Fn04301", "项目编码"]).astype(str).str.strip()
    g = work.groupby(GROUP)

    def pick(codes: list[str], col: str) -> pd.Series:
        # 按项目编码筛选指定税种并汇总
        return work[code.isin(codes)].groupby(GROUP)[col].sum(min_count=1)

    return pd.DataFrame({
        "tax_payable_end": g["_end"].sum(min_count=1),
        "tax_payable_begin": g["_begin"].sum(min_count=1),
        "taxpay_vat": pick(["Q3308"], "_end"),
        "taxpay_income_tax": pick(["Q3301"], "_end"),
        "taxpay_individual_income_tax": pick(["Q3304"], "_end"),
        "taxpay_urban_maintenance": pick(["Q3305"], "_end"),
        "taxpay_property_tax": pick(["Q3307"], "_end"),
        "taxpay_land_use_tax": pick(["Q3313"], "_end"),
        "taxpay_stamp_tax": pick(["Q3314"], "_end"),
        "taxpay_land_appreciation": pick(["Q3302"], "_end"),
        "taxpay_education_surcharge": pick(["Q3306", "Q3311"], "_end"),
    }).reset_index()


def h_employee(df: pd.DataFrame) -> pd.DataFrame:
    """上市公司人员结构表 044926026：员工总数"""
    w = df.dropna(subset=["stock_code", "year"])
    work = w.copy()
    work["_amt"] = pd.to_numeric(_txt(work, ["Amount"]), errors="coerce")
    detail = _txt(work, ["EmployDetail"]).astype(str)
    # "合计/员工总数"行为企业总人数，与明细行区分
    total = work[detail.str.contains("合计|员工总数", na=False)]
    g = work.groupby(GROUP)
    out = pd.DataFrame({
        "employee_amount_sum": g["_amt"].sum(min_count=1),
        "employee_items": g.size(),
    }).reset_index()
    # 有合计行时并入 employee_total，避免与明细求和口径混用
    if not total.empty:
        out = out.merge(
            total.groupby(GROUP)["_amt"].sum(min_count=1).rename("employee_total").reset_index(),
            on=GROUP, how="left",
        )
    return out


def h_finance_expense(df: pd.DataFrame) -> pd.DataFrame:
    """财务费用明细 210836951：Fn05101 1利息支出 2利息收入 3汇兑损失 4汇兑收益 5其他 6合计 7资本化利息"""
    w = df.dropna(subset=["stock_code", "year"])
    w = filter_report_type(w, "合并") if "report_type" in w.columns else w
    if "_row_type" in w.columns:
        w = w[w["_row_type"] != "total"]  # 排除"合计"行
    work = w.copy()
    # Fn05101：财务费用类别（1利息支出 2利息收入 3汇兑损失 4汇兑收益 5其他 6合计 7资本化利息）
    work["_cat"] = pd.to_numeric(_txt(work, ["Fn05101"]), errors="coerce")
    work["_amt"] = pd.to_numeric(_txt(work, ["Fn05102"]), errors="coerce")
    g = work.groupby(GROUP)
    return pd.DataFrame({
        "fe_interest_expense": work[work["_cat"] == 1].groupby(GROUP)["_amt"].sum(min_count=1),
        "fe_interest_income": work[work["_cat"] == 2].groupby(GROUP)["_amt"].sum(min_count=1),
        "fe_exchange_loss": work[work["_cat"] == 3].groupby(GROUP)["_amt"].sum(min_count=1),
        "fe_exchange_gain": work[work["_cat"] == 4].groupby(GROUP)["_amt"].sum(min_count=1),
        "fe_other": work[work["_cat"] == 5].groupby(GROUP)["_amt"].sum(min_count=1),
        "fe_capitalized_interest": work[work["_cat"] == 7].groupby(GROUP)["_amt"].sum(min_count=1),
        "fe_total": g["_amt"].sum(min_count=1),
    }).reset_index()


def h_income_tax_detail(df: pd.DataFrame) -> pd.DataFrame:
    """所得税费用明细 211134056：拆分当期所得税费用 / 递延所得税费用。"""
    import re

    w = df.dropna(subset=["stock_code", "year"])
    w = filter_report_type(w, "合并") if "report_type" in w.columns else w
    if "_row_type" in w.columns:
        w = w[w["_row_type"] != "total"]
    work = w.copy()
    item = _txt(work, ["FN_Fn06501", "项目"]).astype(str)
    work["_amt"] = pd.to_numeric(_txt(work, ["FN_Fn06502"]), errors="coerce")

    def classify(t: str) -> str:
        # 归类当期/递延所得税费用；剔除合计与"其中/减"子项防止重复计数
        t = t.strip()
        if t.startswith("其中") or t.startswith("减") or "合计" in t or "小计" in t:
            return "skip"   # 合计行 / 其中·减 子项，避免重复计数
        if "递延" in t:
            return "deferred"
        if re.search("当期所得税|本期所得税|当年所得税", t) and not re.search(
                "以前年度|上年|上期|汇算", t):
            return "current"
        return "other"

    work["_cat"] = item.map(classify)
    g = work.groupby(GROUP)
    return pd.DataFrame({
        "current_tax_expense": work[work["_cat"] == "current"].groupby(GROUP)["_amt"].sum(min_count=1),
        "deferred_tax_expense": work[work["_cat"] == "deferred"].groupby(GROUP)["_amt"].sum(min_count=1),
        "tax_expense_other": work[work["_cat"] == "other"].groupby(GROUP)["_amt"].sum(min_count=1),
    }).reset_index()


def h_payroll_detail(df: pd.DataFrame) -> pd.DataFrame:
    """应付职工薪酬 210230772：工资/福利费/工会/教育/社保/公积金（按项目文本归类）。"""
    w = df.dropna(subset=["stock_code", "year"])
    w = filter_report_type(w, "合并") if "report_type" in w.columns else w
    work = w.copy()
    item = _txt(work, ["Fn03201", "项目"]).astype(str)
    amt = pd.to_numeric(_txt(work, ["Fn03203", "本期增加额"]), errors="coerce")
    # 优先用"本期增加额"，缺失时退化为"期末数"
    if amt.isna().all():
        amt = pd.to_numeric(_txt(work, ["Fn03205", "期末数"]), errors="coerce")
    work["_amt"] = amt

    def pick(rx: str) -> pd.Series:
        # 按项目名称正则归类到工资/福利/工会/教育/社保/公积金
        m = item.str.contains(rx, na=False, regex=True)
        return work[m].groupby(GROUP)["_amt"].sum(min_count=1)

    out = pd.DataFrame({
        "payroll_wages": pick("工资|奖金|津贴|补贴"),
        "payroll_welfare": pick("福利费"),
        "payroll_union": pick("工会"),
        "payroll_education": pick("教育"),
        "payroll_social_insurance": pick("社会保险|社保|养老|医疗|失业|工伤|生育"),
        "payroll_housing_fund": pick("住房公积金"),
    }).reset_index()
    return out


def h_tax_surcharge_detail(df: pd.DataFrame) -> pd.DataFrame:
    """营业税金及附加 210833737：按税种归类（税种 Fn04901 / 累计金额 Fn04903）。"""
    w = df.dropna(subset=["stock_code", "year"])
    w = filter_report_type(w, "合并") if "report_type" in w.columns else w
    work = w.copy()
    item = _txt(work, ["Fn04901", "税种"]).astype(str)
    work["_amt"] = pd.to_numeric(_txt(work, ["Fn04903", "累计金额"]), errors="coerce")

    def pick(rx: str) -> pd.Series:
        # 按税种名称正则归类（城建税/教育费附加/印花税等小税种）
        m = item.str.contains(rx, na=False, regex=True)
        return work[m].groupby(GROUP)["_amt"].sum(min_count=1)

    return pd.DataFrame({
        "surcharge_urban_maintenance": pick("城市维护建设税"),
        "surcharge_education": pick("教育费附加|地方教育"),
        "surcharge_stamp": pick("印花税"),
        "surcharge_property": pick("房产税"),
        "surcharge_land_use": pick("土地使用税"),
        "surcharge_vehicle_vessel": pick("车船"),
        "surcharge_environmental": pick("环境保护税"),
        "surcharge_other": pick("其他"),
    }).reset_index()


def h_tax_payable_detail(df: pd.DataFrame) -> pd.DataFrame:
    """应交税费 210307520：按项目编码 Q330x 归类（期末值 FN_Fn04303）。"""
    w = df.dropna(subset=["stock_code", "year"])
    w = filter_report_type(w, "合并") if "report_type" in w.columns else w
    work = w.copy()
    code = _txt(work, ["FN_Fn04301", "项目编码"]).astype(str).str.strip()
    work["_amt"] = pd.to_numeric(_txt(work, ["FN_Fn04303", "期末值"]), errors="coerce")
    # 期末值缺失时用期初值兜底，尽量保留该税种余额
    work["_amt"] = work["_amt"].fillna(pd.to_numeric(_txt(work, ["FN_Fn04302", "期初值"]), errors="coerce"))

    def pick(codes: list[str]) -> pd.Series:
        # 按 Q330x 编码精确筛选税种
        m = code.isin(codes)
        return work[m].groupby(GROUP)["_amt"].sum(min_count=1)

    return pd.DataFrame({
        "taxpay_income_tax": pick(["Q3301"]),
        "taxpay_vat": pick(["Q3308"]),
        "taxpay_individual_income_tax": pick(["Q3304"]),
        "taxpay_urban_maintenance": pick(["Q3305"]),
        "taxpay_property_tax": pick(["Q3307"]),
        "taxpay_land_use_tax": pick(["Q3313"]),
        "taxpay_stamp_tax": pick(["Q3314"]),
        "taxpay_consumption_tax": pick(["Q3316"]),
        "taxpay_land_appreciation": pick(["Q3302"]),
        "taxpay_education_surcharge": pick(["Q3306", "Q3311"]),
    }).reset_index()


def h_fixed_asset_depreciation(df: pd.DataFrame) -> pd.DataFrame:
    """固定资产 205523719：按科目类型(1原值 2累计折旧 3净值 4减值准备 5净额)提取。"""
    w = df.dropna(subset=["stock_code", "year"])
    w = filter_report_type(w, "合并") if "report_type" in w.columns else w
    work = w.copy()
    # Fn02001：科目类型（1原值 2累计折旧 3净值 4减值准备 5净额）；Fn02013 期末、Fn02004 本期增加
    cat = pd.to_numeric(_txt(work, ["Fn02001", "科目类型"]), errors="coerce")
    work["_end"] = pd.to_numeric(_txt(work, ["Fn02013", "期末余额"]), errors="coerce")
    work["_inc"] = pd.to_numeric(_txt(work, ["Fn02004", "本期增加"]), errors="coerce")

    def pick(c: float, col: str) -> pd.Series:
        # 按科目类型精确筛选后汇总
        m = cat == c
        return work[m].groupby(GROUP)[col].sum(min_count=1)

    return pd.DataFrame({
        "fa_original_end": pick(1, "_end"),
        "fa_accum_dep_end": pick(2, "_end"),
        "fa_accum_dep_increase": pick(2, "_inc"),
        "fa_impairment_end": pick(4, "_end"),
        "fa_net_end": pick(5, "_end"),
    }).reset_index()


def h_investment_income_detail(df: pd.DataFrame) -> pd.DataFrame:
    """投资收益 210855392：明细合计（Fn05302）。"""
    w = df.dropna(subset=["stock_code", "year"])
    w = filter_report_type(w, "合并") if "report_type" in w.columns else w
    work = w.copy()
    work["_amt"] = pd.to_numeric(_txt(work, ["Fn05302", "金额数"]), errors="coerce")
    g = work.groupby(GROUP)
    # 投资收益明细合计与项数：用于区分股息红利与股权转让所得
    return pd.DataFrame({
        "investment_income_detail_sum": g["_amt"].sum(min_count=1),
        "investment_income_items": g.size(),
    }).reset_index()


# ------------------------------------------------------------------ 注册表

# dataset_id -> 处理函数；merge 阶段按此表逐数据集抽取精选列。
# 减值类用 lambda 传入金额列/项目列/前缀，复用同一套归类逻辑。
CURATED: dict[str, Callable[[pd.DataFrame], pd.DataFrame]] = {
    "044219154": h_company_info,
    "212335916": h_fin_index,
    "212541585": h_equity_nature,
    "213111405": h_rd_invest,
    "211127976": h_rd_expense_detail,
    "204837437": h_tax_rate,
    "213143417": h_gov_subsidy,
    "210126462": h_borrowing,
    "050348744": h_balance,
    "050312367": h_income,
    "053202606": h_cashflow,
    "205523719": h_fixed_assets,
    "165406195": h_subsidiary,
    "045225416": h_related_party,
    "212918837": h_qualification,
    "212658357": h_ma_events,
    "210307520": h_tax_payable,
    "044926026": h_employee,
    "210836951": h_finance_expense,
    "211134056": h_income_tax_detail,
    "210230772": h_payroll_detail,
    "210833737": h_tax_surcharge_detail,
    "210855392": h_investment_income_detail,
    "211127049": lambda df: h_impairment_detail(
        df, ["FN_Fn05902", "本期发生额"], ["FN_Fn05901", "项目"], "asset_imp"),
    "211210533": lambda df: h_impairment_detail(
        df, ["Fn10102", "本期发生额"], ["Fn10101", "项目"], "credit_imp"),
}
