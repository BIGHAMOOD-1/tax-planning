"""字段来源映射：语义字段 -> 原始数据集 / 字段代码 / 中文名。

用于证据链回溯：每个结论都能追到「哪张表、哪个字段、什么含义」。

结构：
    - FIELD_SOURCES：原始字段（可直接从数据集列取值）；
    - DERIVED_FIELDS：衍生字段（由公式计算，需写明口径）；
    - DERIVED_CN / EXTRA_LABELS：中文展示名兜底，保证 UI 不露英文字段名。
约定：一个语义字段只登记一种来源；若同名字段在不同数据集含义不同，以业务主来源为准。
"""
from __future__ import annotations

# field -> (dataset_id, dataset_name, column_code, column_cn)
# 说明：同一数据集的多科目字段可能共用 column_code（按科目类型归类），
# 故来源映射表达的是「该语义字段源自哪张表的哪一列」，而非唯一物理列。
FIELD_SOURCES: dict[str, tuple[str, str, str, str]] = {
    "industry_name": ("044219154", "上市公司基本信息年度表", "IndustryName", "行业名称"),
    "province": ("044219154", "上市公司基本信息年度表", "PROVINCE", "所属省份"),
    "equity_nature": ("212541585", "中国上市公司股权性质文件", "EquityNature", "股权性质"),
    "rd_spend_sum": ("213111405", "研发投入情况表", "RDSpendSum", "研发投入金额"),
    "rd_person": ("213111405", "研发投入情况表", "RDPerson", "研发人员数量"),
    "rd_expenses": ("213111405", "研发投入情况表", "RDExpenses", "费用化研发投入"),
    "rd_invest": ("213111405", "研发投入情况表", "RDInvest", "资本化研发投入"),
    "is_revenue": ("050312367", "利润表", "B001101000", "营业收入"),
    "is_cost": ("050312367", "利润表", "B001201000", "营业成本"),
    "is_rd_expense": ("050312367", "利润表", "B001216000", "研发费用"),
    "is_interest_expense": ("050312367", "利润表", "B001211101", "利息费用(财务费用)"),
    "interest_expense_best": ("050312367", "利润表", "B001211101/B001211000", "利息费用（缺失时以财务费用近似）"),
    "current_tax_expense": ("211134056", "所得税费用明细", "FN_Fn06502", "当期(当年)所得税费用"),
    "deferred_tax_expense": ("211134056", "所得税费用明细", "FN_Fn06502", "递延所得税费用"),
    "fe_interest_expense": ("210836951", "财务费用明细", "Fn05102", "利息支出"),
    "fe_interest_income": ("210836951", "财务费用明细", "Fn05102", "利息收入"),
    "fe_other": ("210836951", "财务费用明细", "Fn05102", "财务费用-其他"),
    "fe_exchange_loss": ("210836951", "财务费用明细", "Fn05102", "汇兑损失"),
    "fe_exchange_gain": ("210836951", "财务费用明细", "Fn05102", "汇兑收益"),
    "fe_total": ("210836951", "财务费用明细", "Fn05102", "财务费用合计(不含合计行)"),
    "is_total_profit": ("050312367", "利润表", "B001000000", "利润总额"),
    "is_income_tax": ("050312367", "利润表", "B002100000", "所得税费用"),
    "is_net_profit": ("050312367", "利润表", "B002000000", "净利润"),
    "bs_total_assets": ("050348744", "资产负债表", "A001000000", "资产总计"),
    "bs_total_liabilities": ("050348744", "资产负债表", "A002000000", "负债合计"),
    "bs_total_equity": ("050348744", "资产负债表", "A003000000", "所有者权益合计"),
    "bs_fixed_assets": ("050348744", "资产负债表", "A001212000", "固定资产净额"),
    "bs_short_loan": ("050348744", "资产负债表", "A002101000", "短期借款"),
    "bs_long_loan": ("050348744", "资产负债表", "A002201000", "长期借款"),
    "bs_bonds_payable": ("050348744", "资产负债表", "A002203000", "应付债券"),
    "cf_capex": ("053202606", "现金流量表(直接法)", "C002006000",
                 "购建固定资产、无形资产和其他长期资产支付的现金"),
    "gov_subsidy_total": ("213143417", "政府补助", "Amount", "本期计入当期损益的金额"),
    "income_tax_rate": ("204837437", "税项名义税率", "Fn00410", "企业所得税税率"),
    "subsidiary_overseas_count": ("165406195", "上市公司子公司情况表", "FN_Fn06107", "区域标识(1=海外)"),
    "subsidiary_total_revenue": ("165406195", "上市公司子公司情况表", "FN_Fn06112", "营业收入"),
    "subsidiary_total_netprofit": ("165406195", "上市公司子公司情况表", "FN_Fn06113", "净利润"),
    "ma_related_party_count": ("212658357", "交易信息总表", "RelevanceSign", "关联交易标识"),
    "is_hightech": ("212918837", "上市公司资质认定信息文件", "Protype", "认定项目类型"),
    "employee_amount_sum": ("044926026", "上市公司人员结构表", "Amount", "人数"),
    # 通用并入字段（前缀 d<数据集ID>__）
    "d210230772__Fn03203": ("210230772", "应付职工薪酬", "Fn03203", "本期增加额"),
    "d210230772__Fn03204": ("210230772", "应付职工薪酬", "Fn03204", "本期减少额"),
    "d210230772__Fn03205": ("210230772", "应付职工薪酬", "Fn03205", "期末数"),
    "d205606775__Fn02605": ("205606775", "递延所得税资产、递延所得税负债", "Fn02605", "年末数"),
    "d205606775__Fn02604": ("205606775", "递延所得税资产、递延所得税负债", "Fn02604", "年初数"),
    "d211406861__FN_Fn01005": ("211406861", "前五大客户、供应商情况表", "FN_Fn01005", "金额"),
    "d211406861__FN_Fn01006": ("211406861", "前五大客户、供应商情况表", "FN_Fn01006", "占年度业务总额比例(%)"),
    "d045152435__n_rows": ("045152435", "子公司退出明细表", "n_rows", "子公司退出条数"),
    # 职工薪酬明细（210230772 归类）
    "payroll_wages": ("210230772", "应付职工薪酬", "Fn03203", "工资、奖金、津贴和补贴"),
    "payroll_welfare": ("210230772", "应付职工薪酬", "Fn03203", "职工福利费"),
    "payroll_union": ("210230772", "应付职工薪酬", "Fn03203", "工会经费"),
    "payroll_education": ("210230772", "应付职工薪酬", "Fn03203", "职工教育经费"),
    "payroll_social_insurance": ("210230772", "应付职工薪酬", "Fn03203", "社会保险费"),
    "payroll_housing_fund": ("210230772", "应付职工薪酬", "Fn03203", "住房公积金"),
    # 税金及附加（210833737 归类）
    "surcharge_urban_maintenance": ("210833737", "营业税金及附加", "Fn04903", "城市维护建设税"),
    "surcharge_education": ("210833737", "营业税金及附加", "Fn04903", "教育费附加（含地方）"),
    "surcharge_stamp": ("210833737", "营业税金及附加", "Fn04903", "印花税"),
    "surcharge_property": ("210833737", "营业税金及附加", "Fn04903", "房产税"),
    "surcharge_land_use": ("210833737", "营业税金及附加", "Fn04903", "土地使用税"),
    "surcharge_vehicle_vessel": ("210833737", "营业税金及附加", "Fn04903", "车船税"),
    "surcharge_environmental": ("210833737", "营业税金及附加", "Fn04903", "环境保护税"),
    # 应交税费（210307520 归类）
    "taxpay_vat": ("210307520", "应交税费", "FN_Fn04303", "应交增值税期末"),
    "taxpay_income_tax": ("210307520", "应交税费", "FN_Fn04303", "应交企业所得税期末"),
    "taxpay_individual_income_tax": ("210307520", "应交税费", "FN_Fn04303", "应交个人所得税期末"),
    # 固定资产明细（205523719 按科目类型）
    "fa_original_end": ("205523719", "固定资产、累计折旧、减值准备", "Fn02013", "固定资产原值期末"),
    "fa_accum_dep_end": ("205523719", "固定资产、累计折旧、减值准备", "Fn02013", "累计折旧期末"),
    "fa_accum_dep_increase": ("205523719", "固定资产、累计折旧、减值准备", "Fn02004", "本期计提折旧"),
    "fa_net_end": ("205523719", "固定资产、累计折旧、减值准备", "Fn02013", "固定资产净额期末"),
    "investment_income_detail_sum": ("210855392", "投资收益", "Fn05302", "投资收益明细合计"),
    "asset_imp_total": ("211127049", "资产减值损失", "FN_Fn05902", "资产减值损失明细合计"),
    "asset_imp_receivable": ("211127049", "资产减值损失", "FN_Fn05902", "应收/坏账类减值"),
    "asset_imp_inventory": ("211127049", "资产减值损失", "FN_Fn05902", "存货类减值"),
    "asset_imp_fixed_asset": ("211127049", "资产减值损失", "FN_Fn05902", "固定资产类减值"),
    "asset_imp_goodwill": ("211127049", "资产减值损失", "FN_Fn05902", "商誉类减值"),
    "credit_imp_total": ("211210533", "信用减值损失明细", "Fn10102", "信用减值损失明细合计"),
    "ma_evaluation_value": ("212658357", "交易信息总表", "EvaluationValue", "标的评估价值"),
    "ma_book_value": ("212658357", "交易信息总表", "BookValue", "标的账面价值"),
    "ma_evaluation_change": ("212658357", "交易信息总表", "EvaluationChange", "评估增值"),
    "ma_expense_value": ("212658357", "交易信息总表", "ExpenseValue", "买方支出价值"),
    "d210431887__n_rows": ("210431887", "应付债券", "n_rows", "应付债券明细条数"),
    "d205404528__Fn01804": ("205404528", "长期股权投资", "Fn01804", "期末余额"),
    "d205622632__FN_Fn017A04": ("205622632", "在建工程基本情况表", "FN_Fn017A04", "期末余额"),
    "d101955005__Fn02207": ("101955005", "无形资产", "Fn02207", "期末余额"),
    "d101955005__Fn02204": ("101955005", "无形资产", "Fn02204", "本期增加数"),
    "d101955005__Fn02203": ("101955005", "无形资产", "Fn02203", "期初数"),
    "d205618410__EndingBalance": ("205618410", "使用权资产", "EndingBalance", "期末余额"),
    "d205618410__IncreaseDuringYear": ("205618410", "使用权资产", "IncreaseDuringYear", "本期增加额"),
    "d205618410__Provision": ("205618410", "使用权资产", "Provision", "其中：计提（折旧）"),
    "d210441016__EndingBalance": ("210441016", "租赁负债", "EndingBalance", "期末余额"),
    "d205559837__FN_Fn01509": ("205559837", "商誉", "FN_Fn01509", "期末余额"),
    "d165419839__FN_Fn07315": ("165419839", "费用明细表", "FN_Fn07315", "本期发生额"),
    "d210940194__FN_Fn00902": ("210940194", "非经常性损益", "FN_Fn00902", "金额"),
}

# 衍生指标来源（公式来自 derived_catalog）
# 这些字段不直接对应某列，而是由口径公式计算；公式文本同时用于 UI 展示与证据说明。
DERIVED_FIELDS = {
    "nominal_tax_rate": "income_tax_rate（名义税率，源自 税项名义税率 表）",
    "rd_expense_ratio": "is_rd_expense / is_revenue",
    "rd_spend_ratio": "rd_spend_sum / is_revenue",
    "asset_liability_ratio": "bs_total_liabilities / bs_total_assets",
    "interest_debt_ratio": "(短期借款+长期借款+应付债券+一年内到期非流动负债) / 资产总计",
    "interest_debt_to_equity": "有息负债 / 所有者权益合计",
    "finance_expense_ratio": "is_finance_expense / is_revenue",
    "implied_interest_rate": "利息费用 / 有息负债",
    "etr": "is_income_tax / is_total_profit",
    "fixed_asset_ratio": "bs_fixed_assets / bs_total_assets",
    "revenue_growth": "营业收入同比",
    "rd_super_deduction_potential": "研发投入 × 100% × 适用税率",
    "impairment_total": "is_asset_impairment + is_credit_impairment（资产减值损失 + 信用减值损失）",
    "employee_compensation_base": "d210230772__Fn03203（应付职工薪酬本期增加额），缺失时用 bs_payroll_payable",
    "top5_customer_supplier_amount": "前五大客户、供应商情况表 金额合计",
    "rd_person_ratio_cross": "rd_person / employee_amount_sum",
    "expense_entertainment": "管理费用/销售费用明细中『招待费』类项目合计（210935890/210924591 归类）",
    "expense_advertising": "管理费用/销售费用明细中『广告宣传费』类项目合计（210935890/210924591 归类）",
    "expense_depreciation_in_expense": "费用明细中折旧摊销类项目合计",
    "expense_payroll_in_expense": "费用明细中职工薪酬类项目合计",
    "expense_rd_in_expense": "费用明细中研发费用类项目合计",
    "expense_transport": "费用明细中运输仓储类项目合计",
    "rd_payroll": "研发费用明细中人员人工类项目合计（211127976 归类）",
    "rd_direct_input": "研发费用明细中直接投入类项目合计（211127976 归类）",
    "rd_depreciation_amortization": "研发费用明细中折旧摊销类项目合计（211127976 归类）",
    "rd_design_test": "研发费用明细中设计试验类项目合计（211127976 归类）",
    "rd_other": "研发费用明细中其他相关费用类项目合计（211127976 归类）",
    "rd_first_five": "前五项（人员人工+直接投入+折旧摊销+设计试验）合计",
    "rd_other_limit": "其他相关费用限额 = 前五项合计 × 10% / (1-10%)",
    "rd_other_excess": "其他相关费用超限额部分",
    "expense_share_based": "管理费用/销售费用明细中股份支付/股权激励类项目合计（210935890/210924591 归类）",
    "gov_subsidy_income_related": "政府补助中与收益相关的金额（213143417 归类）",
}


def lookup(field: str) -> dict:
    """查询字段来源：原始/衍生/未知三类之一。

    返回 type=raw 时给 dataset/column 信息，type=derived 时给 formula，
    未登记字段返回 type=unknown（调用方据此降级展示，不臆造来源）。
    """
    # 顺序：先查原始表，再查衍生公式，最后 unknown
    if field in FIELD_SOURCES:
        # 原始字段：解包四元组并标注 type=raw
        did, dname, code, cn = FIELD_SOURCES[field]
        return {"field": field, "type": "raw", "dataset_id": did, "dataset_name": dname,
                "column_code": code, "column_cn": cn}
    if field in DERIVED_FIELDS:
        return {"field": field, "type": "derived", "formula": DERIVED_FIELDS[field]}
    # 未登记字段：不臆造来源，交由 field_label 的 EXTRA_LABELS/字段名兜底
    return {"field": field, "type": "unknown"}


# 衍生字段的中文名（用于 UI 一级展示）
# 与 DERIVED_FIELDS 分离：公式（来源）与中文名（展示）职责不同，便于分别维护。
DERIVED_CN: dict[str, str] = {
    "nominal_tax_rate": "名义税率",
    "rd_expense_ratio": "研发费用率",
    "rd_spend_ratio": "研发投入占收入比",
    "asset_liability_ratio": "资产负债率",
    "interest_debt_ratio": "有息负债率",
    "interest_debt_to_equity": "债资比",
    "finance_expense_ratio": "财务费用率",
    "implied_interest_rate": "隐含利率",
    "etr": "实际税率 ETR",
    "fixed_asset_ratio": "固定资产占比",
    "revenue_growth": "营收增长率",
    "rd_super_deduction_potential": "研发加计扣除潜力",
    "impairment_total": "减值损失合计",
    "employee_compensation_base": "职工薪酬计提额",
    "top5_customer_supplier_amount": "前五大客户/供应商金额",
    "rd_person_ratio_cross": "研发人员占比",
    "rd_first_five": "研发前五项合计",
    "rd_other": "研发其他相关费用",
    "rd_other_limit": "其他相关费用限额",
    "rd_other_excess": "其他相关费用超限",
    "gov_subsidy_income_related": "与收益相关政府补助",
    "rd_payroll": "研发人员人工",
    "rd_direct_input": "研发直接投入",
    "rd_depreciation_amortization": "研发折旧摊销",
    "rd_design_test": "研发设计试验费",
}


# 兜底中文名：覆盖未登记在 FIELD_SOURCES/DERIVED_CN 的关键字段（保证 UI 不露出字段名）
EXTRA_LABELS: dict[str, str] = {
    "bs_accounts_receivable": "资产负债表 · 应收账款",
    "bs_development_expense": "资产负债表 · 开发支出",
    "bs_intangible_assets": "资产负债表 · 无形资产",
    "bs_retained_earnings": "资产负债表 · 未分配利润",
    "bs_tax_payable": "资产负债表 · 应交税费",
    "bs_payroll_payable": "资产负债表 · 应付职工薪酬",
    "bs_inventory": "资产负债表 · 存货",
    "bs_cip": "资产负债表 · 在建工程",
    "cf_tax_paid": "现金流量表 · 支付的各项税费",
    "cf_tax_refund": "现金流量表 · 收到的税费返还",
    "cf_operating_net": "现金流量表 · 经营活动现金流净额",
    "csmar__NonDebtTaxShield": "CSMAR · 非债务税盾（折旧/总资产）",
    "expense_share_based": "费用明细 · 股份支付/股权激励",
    "is_admin_expense": "利润表 · 管理费用",
    "is_selling_expense": "利润表 · 销售费用",
    "is_finance_expense": "利润表 · 财务费用",
    "is_asset_impairment": "利润表 · 资产减值损失",
    "is_credit_impairment": "利润表 · 信用减值损失",
    "is_investment_income": "利润表 · 投资收益",
    "is_other_income": "利润表 · 其他收益",
    "is_non_operating_income": "利润表 · 营业外收入",
    "is_tax_surcharge": "利润表 · 税金及附加",
    "is_operating_profit": "利润表 · 营业利润",
    "vat_rate": "增值税名义税率",
    "gov_subsidy_total": "政府补助 · 合计",
    "subsidiary_count": "子公司数量",
    "subsidiary_overseas_count": "海外子公司数量",
    "subsidiary_hk_mo_tw_count": "港澳台子公司数量",
    "subsidiary_total_revenue": "子公司合计营业收入",
    "subsidiary_total_netprofit": "子公司合计净利润",
    "is_hightech": "高新技术企业资格（1/0）",
    "rd_person": "研发人员数量",
}


def field_label(field: str) -> dict:
    """字段的一级展示信息：人类名 + 来源 + 公式（衍生字段）。

    优先级：原始字段（数据集·中文名）> 衍生字段（中文名 + 公式）> 兜底标签 > 字段名。
    """
    if field in FIELD_SOURCES:
        # 原始字段：展示「数据集 · 中文列名」，不含公式
        _did, dname, _code, cn = FIELD_SOURCES[field]
        return {"field": field, "label": f"{dname} · {cn}", "source": dname, "formula": ""}
    if field in DERIVED_FIELDS:
        # 衍生字段：展示中文名 + 口径公式
        return {"field": field, "label": DERIVED_CN.get(field, field),
                "source": "", "formula": DERIVED_FIELDS[field]}
    if field in EXTRA_LABELS:
        return {"field": field, "label": EXTRA_LABELS[field], "source": "", "formula": ""}
    # 完全未登记：回退字段名本身（宁可露出英文名，也不编造来源）
    return {"field": field, "label": field, "source": "", "formula": ""}


def all_field_labels() -> dict[str, str]:
    """全量 field -> 人类名（供 UI 统一展示，避免露出字段名）。"""
    out: dict[str, str] = {}
    for f in FIELD_SOURCES:
        out[f] = field_label(f)["label"]
    for f in DERIVED_FIELDS:
        # 衍生字段优先用中文名，缺失时回退 field_label（含公式）的 label
        out[f] = DERIVED_CN.get(f) or field_label(f)["label"]
    for f, lab in EXTRA_LABELS.items():
        out.setdefault(f, lab)   # setdefault：不覆盖已有来源标签
    return out

