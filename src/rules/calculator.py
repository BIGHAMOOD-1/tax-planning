"""确定性计算模块：只做可程序化、易算错的部分，并保留计算过程用于回溯。

职责：
    以「分层（layers）+ 步骤（steps）+ 结果（results）」三段式，把税务口径换算、
    限额判定、税会差异等易算错的确定性逻辑显式化，供报告与 UI 复用。
关键设计：
    - 计算器通过 @calculator(cid) 注册到 CALCULATORS，按计算器 id 调度；
    - 每个计算器只产出「会计观察/估算」中间量，真正的税务语义（确认/情景/税盾）
      由 run_calculator 统一裁定，避免各计算器各自解释金额含义。
不变量：
    - 所有金额单位统一为「元」；
    - 结果必须带 amount_is_estimate/caliber_verified 标注，未验证不得冒充确认值；
    - 计算结果只依赖传入 row，不做 IO、不写全局状态，保证可复现。
"""
from __future__ import annotations

from typing import Any, Callable

import numpy as np

from src.common import get_logger

log = get_logger()

CALCULATORS: dict[str, Callable[..., dict]] = {}

# 已注册计算器一览（cid -> 用途）：
#   rnd_super_deduction        研发费用加计扣除（会计口径 -> 税法口径分层估算）
#   fixed_asset_accelerated    固定资产一次性扣除/加速折旧的规模与时间性税盾
#   interest_deduction         利息支出与关联方债资比 2:1 限额
#   hightech_rate_benefit      高新技术企业 15% 优惠税率节税估算
#   income_tax_reconciliation  所得税税会差异（理论税负 vs 实际所得税费用）
#   asset_impairment           资产/信用减值损失的纳税调整影响
#   employee_compensation      职工薪酬扣除规模 + 三项经费超限调增
#   vat_burden                 增值税税负水平（税费现金比近似）
#   expense_deduction_limit    期间费用 + 招待费/广告费限额测算
#   depreciation_amortization  折旧摊销规模与税盾
#   loss_carryforward          亏损弥补的情景测算
#   investment_income          投资收益与股权转让所得的税务处理
#   tax_cash_flow              税费现金流与计提的差异
#   receivable_bad_debt        应收账款与坏账/信用减值结构
#   gov_subsidy_nontaxable     政府补助不征税收入判定提示
#   intangible_amortization    无形资产摊销年限税会差异
#   share_based_payment        股份支付税前扣除时点与影响
#   lease_tax_difference       租赁（使用权资产/租赁负债）税会差异
#   gov_subsidy_split          政府补助按与资产/收益相关拆分
#   overseas_tax_credit        境外所得抵免限额测算
#   small_taxes_structure      小税种结构拆分

# 「口径可验证」计算器（部分打通 CONFIRMED）：其关键输入全为 DIRECT 时，
# 结果可作为 CONFIRMED_IMPACT（确定性影响），否则仍降级为情景测算。
# 仅纳入"输出即税法调整/影响"的计算器（如期间费用超限调增）；
# 职工薪酬的 tax_shield 是"工资扣除税盾"（非筹划收益），故不纳入。
VERIFIABLE_CALCULATORS = {"expense_deduction_limit", "rnd_super_deduction"}

# ---- 金额语义（calculation_type）：计算结果的税务经济含义 ----
# BASELINE_TAX            不采取筹划措施时的基准税负
# SCENARIO_TAX            基于假设条件的情景税负
# INCREMENTAL_TAX_BENEFIT 相对于基准情形新增的税收收益（才算"筹划价值"）
# TAX_SHIELD              已存在的扣除/优惠产生的税盾，不属于新增筹划收益
# NOT_CALCULABLE          理论上需要计算，但当前证据/数据不足（运行时判定）
# NO_CALCULATION          该方向本身不要求金额计算
CALCULATION_TYPES = (
    "BASELINE_TAX", "SCENARIO_TAX", "INCREMENTAL_TAX_BENEFIT",
    "TAX_SHIELD", "NOT_CALCULABLE", "NO_CALCULATION",
)

# 静态主类型（运行时若关键输入缺失 → 覆盖为 NOT_CALCULABLE）。
# 判定口径：
#   INCREMENTAL_TAX_BENEFIT：结果 = 相对基准新增的节税（如加计扣除、超限调增规避）
#   TAX_SHIELD            ：结果 = 已存在扣除/优惠的税盾（工资、折旧、股份支付、税率优惠）
#   SCENARIO_TAX          ：结果 = 基于假设的情景税额（亏损弥补、股权转让、政府补助、境外抵免）
#   NO_CALCULATION        ：结果 = 比例/结构/诊断，不产出税额
CALCULATION_TYPE_BY_CALCULATOR: dict[str, str] = {
    "rnd_super_deduction": "INCREMENTAL_TAX_BENEFIT",
    "expense_deduction_limit": "INCREMENTAL_TAX_BENEFIT",
    "asset_impairment": "INCREMENTAL_TAX_BENEFIT",
    "fixed_asset_accelerated": "TAX_SHIELD",
    "hightech_rate_benefit": "TAX_SHIELD",
    "employee_compensation": "TAX_SHIELD",
    "depreciation_amortization": "TAX_SHIELD",
    "share_based_payment": "TAX_SHIELD",
    "loss_carryforward": "SCENARIO_TAX",
    "investment_income": "SCENARIO_TAX",
    "gov_subsidy_nontaxable": "SCENARIO_TAX",
    "gov_subsidy_split": "SCENARIO_TAX",
    "overseas_tax_credit": "SCENARIO_TAX",
    "interest_deduction": "NO_CALCULATION",
    "income_tax_reconciliation": "NO_CALCULATION",
    "vat_burden": "NO_CALCULATION",
    "tax_cash_flow": "NO_CALCULATION",
    "receivable_bad_debt": "NO_CALCULATION",
    "intangible_amortization": "NO_CALCULATION",
    "lease_tax_difference": "NO_CALCULATION",
    "small_taxes_structure": "NO_CALCULATION",
}


# 各计算器金额语义的判定理由（便于审阅时快速理解为何如此归类）：
#   INCREMENTAL_TAX_BENEFIT：加计扣除、期间费用超限规避、资产减值调增——结果即相对基准的节税
#   TAX_SHIELD            ：加速折旧、高新税率、职工薪酬、折旧摊销、股份支付——已存在优惠的税盾
#   SCENARIO_TAX          ：亏损弥补、投资收益/股权转让、政府补助、境外抵免——依赖假设的情景税额
#   NO_CALCULATION        ：利息/所得税勾稽/增值税/税费现金流/坏账/无形资产/租赁/小税种——结构诊断
def calculation_type_of(cid: str) -> str:
    """计算器的静态金额语义主类型（未登记视为 NO_CALCULATION）。"""
    return CALCULATION_TYPE_BY_CALCULATOR.get(cid, "NO_CALCULATION")


def calculator(cid: str):
    """计算器注册装饰器：把被装饰函数登记到 CALCULATORS[cid]，供 run_calculator 调度。"""
    def deco(fn):
        CALCULATORS[cid] = fn
        return fn
    return deco


def _f(row: dict, field: str, default: float | None = None) -> float | None:
    """安全取数：把 row[field] 转 float；缺失/NaN/不可转一律返回 default。

    统一在此处理 NaN，避免下游出现「NaN 参与比较为 False」导致的静默错误。
    """
    v = row.get(field)
    if v is None:
        return default
    try:
        f = float(v)
        if np.isnan(f):
            return default
        return f
    except (TypeError, ValueError):
        return default


def _rate(row: dict, default: float = 0.25) -> tuple[float, str]:
    """返回 (税率小数, 口径说明)。

    口径：nominal_tax_rate 以百分数存储（如 25 表示 25%），故除以 100；
    缺失时按法定 25% 估算，并在说明中显式标注「估算」以便报告提示。
    """
    nominal = _f(row, "nominal_tax_rate")
    if nominal is None:
        return default, "名义税率缺失，按 25% 估算"
    return nominal / 100.0, f"名义税率 {nominal}%"


_RATIO_SUFFIX = ("率", "比例", "占比", "比", "倍数")
# 层名含这些词时按比率展示（ETR 等缩写需显式登记，避免被当作金额）
_RATIO_CONTAINS = ("债资比", "现金比", "比值", "倍数", "ETR")


def infer_layer_kind(name: str, value) -> str:
    """推断计算分层的展示类型：amount / ratio(0~1) / percent(已是百分数) / text。

    仅凭层名含"税率/比例"会误判（如"按名义税率计算的所得税"是金额），
    故按"层名后缀 + (%)标记"判定。
    """
    if isinstance(value, str):
        return "text"
    try:
        float(value)
    except (TypeError, ValueError):
        return "text"
    n = str(name or "")
    if "(%)" in n or "（%）" in n:
        return "percent"   # 层名自带 (%) 标记，值已是百分数
    if n.endswith(_RATIO_SUFFIX) or any(k in n for k in _RATIO_CONTAINS):
        return "ratio"     # 比率类，值域 0~1
    return "amount"        # 默认按金额展示


@calculator("rnd_super_deduction")
def calc_rnd_super_deduction(row: dict) -> dict:
    """研发加计扣除：会计口径 -> 税法口径 的分层估算。

    关键：会计口径研发投入 ≠ 税法口径可加计研发费用。
    本计算器显式区分费用化/资本化，并给出假设与证据要求；
    不做"会计口径 × 比例 × 税率"的粗暴近似。
    """
    accounting_rd = _f(row, "rd_spend_sum")
    expensed = _f(row, "rd_expenses")
    capitalized = _f(row, "rd_invest")
    rate, rate_note = _rate(row)
    super_rate = 1.0

    layers: list[dict] = []
    assumptions: list[str] = []
    evidence_required: list[str] = []

    layers.append({"layer": "会计口径研发投入", "value": accounting_rd, "source": "rd_spend_sum"})
    if expensed is not None:
        layers.append({"layer": "其中：费用化金额", "value": expensed, "source": "rd_expenses"})
    if capitalized is not None:
        layers.append({"layer": "其中：资本化金额", "value": capitalized, "source": "rd_invest"})

    if accounting_rd is None or accounting_rd <= 0:
        return {"calculator": "rnd_super_deduction", "ok": False, "reason": "无研发投入",
                "layers": layers, "steps": [], "results": {}, "assumptions": assumptions,
                "amount_is_estimate": False, "evidence_required": []}

    # 税法口径可加计基础（当期）
    first_five = _f(row, "rd_first_five")
    other = _f(row, "rd_other")
    other_limit = _f(row, "rd_other_limit")
    component_based = False
    if first_five is not None and first_five > 0:
        # 明细口径：其他相关费用受 10% 限额约束，可计入额 = min(发生额, 限额)
        other_allowed = min(other or 0.0, other_limit or 0.0)
        tax_eligible = first_five + other_allowed
        component_based = True
        for label, field in [("人员人工费用", "rd_payroll"),
                             ("直接投入费用", "rd_direct_input"),
                             ("折旧与摊销", "rd_depreciation_amortization"),
                             ("设计试验费", "rd_design_test")]:
            layers.append({"layer": label, "value": _f(row, field), "source": field})
        layers.append({"layer": "前五项合计", "value": round(first_five, 2)})
        layers.append({"layer": "其他相关费用（发生额）", "value": other, "source": "rd_other"})
        layers.append({"layer": "其他相关费用限额", "value": round(other_limit, 2) if other_limit is not None else None,
                       "note": "前五项合计 × 10% / (1-10%)"})
        layers.append({"layer": "其他相关费用可计入", "value": round(other_allowed, 2)})
        assumptions.append("按研发费用明细归类计算，其他相关费用已按 10% 限额调整")
    elif expensed is not None and expensed > 0:
        tax_eligible = expensed
        assumptions.append("以费用化研发投入作为当期加计基础；资本化部分形成无形资产后按成本200%摊销，未计入当期")
        assumptions.append("未逐项剔除不适用加计的支出，未做『其他相关费用不超过10%』限额调整，未冲减特殊收入")
    else:
        tax_eligible = accounting_rd
        assumptions.append("缺少费用化/资本化拆分与费用构成，暂按会计口径研发投入全额近似，存在高估风险")
        assumptions.append("未逐项剔除不适用加计的支出，未做『其他相关费用不超过10%』限额调整，未冲减特殊收入")

    layers.append({"layer": "税法口径可加计研发费用（估算）", "value": tax_eligible,
                   "note": ("已按费用构成归类并做其他相关费用10%限额调整" if component_based
                            else "需按归集范围调整，尚未经证据验证")})
    layers.append({"layer": "加计扣除比例", "value": super_rate,
                   "note": "自2023.1.1起符合条件研发费用按实际发生额100%加计扣除"})
    deduction = tax_eligible * super_rate
    layers.append({"layer": "可加计扣除额（估算）", "value": round(deduction, 2)})
    layers.append({"layer": "适用税率", "value": rate, "note": rate_note})
    tax_saving = deduction * rate
    layers.append({"layer": "预计税收影响（估算）", "value": round(tax_saving, 2)})

    # 区间：保守 ≤ 主口径 ≤ 上限
    if component_based:
        conservative_base = first_five
        upper_base = accounting_rd
        range_note = "保守=前五项（不含其他相关费用）；上限=会计口径全额（理论上限）"
    elif expensed is not None and expensed > 0:
        conservative_base = expensed
        upper_base = accounting_rd
        range_note = "保守=费用化金额；上限=会计口径全额（理论上限）"
    else:
        conservative_base = accounting_rd
        upper_base = accounting_rd
        range_note = "仅有会计口径，区间与主口径一致"
    saving_conservative = conservative_base * super_rate * rate
    saving_upper = upper_base * super_rate * rate
    layers.append({"layer": "税收影响区间-保守", "value": round(saving_conservative, 2), "note": range_note})
    layers.append({"layer": "税收影响区间-上限", "value": round(saving_upper, 2), "note": "会计口径全额可加计时的理论上限"})

    evidence_required += [
        "研发支出辅助账（按项目与费用类型归集）",
        "研发费用构成明细（人员人工/直接投入/折旧/无形资产摊销/设计试验费/其他相关费用）",
        "费用化与资本化划分依据",
        "研发项目立项决议与项目计划书",
    ]

    steps = [
        {"step": "取得会计口径研发投入", "field": "rd_spend_sum", "value": accounting_rd},
        {"step": "拆分费用化/资本化", "value": {"费用化": expensed, "资本化": capitalized}},
        {"step": "调整至税法口径可加计基础", "formula": "费用化金额（或会计口径近似）",
         "value": round(tax_eligible, 2)},
        {"step": "计算可加计扣除额", "formula": "税法口径基础 × 100%", "value": round(deduction, 2)},
        {"step": "估算税收影响", "formula": "加计扣除额 × 适用税率", "value": round(tax_saving, 2)},
    ]

    return {
        "calculator": "rnd_super_deduction",
        "ok": True,
        "unit": "元",
        "layers": layers,
        "steps": steps,
        "results": {
            "accounting_rd": round(accounting_rd, 2),
            "expensed_rd": round(expensed, 2) if expensed is not None else None,
            "capitalized_rd": round(capitalized, 2) if capitalized is not None else None,
            "tax_eligible_rd": round(tax_eligible, 2),
            "super_deduction_rate": super_rate,
            "deduction_amount": round(deduction, 2),
            "tax_rate": rate,
            "tax_saving": round(tax_saving, 2),
            "tax_saving_conservative": round(saving_conservative, 2),
            "tax_saving_upper": round(saving_upper, 2),
            "conservative_base": round(conservative_base, 2),
            "upper_base": round(upper_base, 2),
            "range_note": range_note,
            "component_based": component_based,
            "rd_other_excess": _f(row, "rd_other_excess"),
        },
        "assumptions": assumptions,
        "amount_is_estimate": True,
        "caliber_verified": False,
        "evidence_required": evidence_required,
        "note": ("估算值。会计口径研发投入未经税法口径调整，实际可加计金额取决于研发费用归集、"
                 "费用化/资本化划分、其他相关费用10%限额与特殊收入冲减，须以辅助账与专业确认为准。"),
    }


@calculator("fixed_asset_accelerated")
def calc_fixed_asset_accelerated(row: dict) -> dict:
    """固定资产一次性扣除/加速折旧的规模估算。

    参数：row 需含 cf_capex（购建长期资产现金支出）与可选 nominal_tax_rate。
    返回：税盾估算（base × 税率），标注为时间性税盾而非新增筹划收益。
    口径边界：以资本支出近似可适用资产规模，未区分设备/房屋、未校验单价 500 万上限。
    """
    base = _f(row, "cf_capex") or 0.0
    rate, rate_note = _rate(row)
    layers = [
        {"layer": "可适用资产支出规模", "value": base, "source": "cf_capex"},
        {"layer": "适用税率", "value": rate, "note": rate_note},
    ]
    steps = [
        {"step": "取得购建长期资产现金支出", "field": "cf_capex", "value": base},
        {"step": "确定适用税率", "value": rate, "note": rate_note},
    ]
    if base <= 0:
        return {"calculator": "fixed_asset_accelerated", "ok": False, "reason": "无资本支出",
                "layers": layers, "steps": steps, "results": {}}
    tax_shield = base * rate   # 时间性税盾：一次性扣除改变扣除时点，不改变总额
    layers.append({"layer": "估算税盾（时间性）", "value": round(tax_shield, 2)})
    steps.append({"step": "估算当年税盾", "formula": "资本支出 × 适用税率", "value": round(tax_shield, 2)})
    return {
        "calculator": "fixed_asset_accelerated",
        "ok": True,
        "unit": "元",
        "layers": layers,
        "steps": steps,
        "results": {"base_amount": round(base, 2), "tax_rate": rate, "tax_shield": round(tax_shield, 2)},
        "assumptions": ["以购建长期资产现金支出近似可适用一次性扣除/加速折旧的资产规模，未区分设备与房屋建筑物、未校验单价500万元上限"],
        "amount_is_estimate": True,
        "caliber_verified": False,
        "evidence_required": ["设备器具购置清单与单价", "资产类别（是否房屋建筑物）", "税会差异台账"],
        "note": "一次性扣除仅适用于新购进设备器具且单价≤500万元、房屋建筑物不适用；此处为规模估算。",
    }


@calculator("interest_deduction")
def calc_interest_deduction(row: dict) -> dict:
    """利息支出与关联方债资比（2:1）限额测算。

    参数：row 含 interest_expense_best / is_interest_expense / fe_interest_expense、
          interest_debt_to_equity 及借款明细字段。
    返回：利息支出、债资比、是否超限及超限部分占比（潜在不得扣除）。
    口径边界：以「整体有息负债/权益」近似关联方债资比，未区分关联/非关联债务。
    """
    interest = (_f(row, "interest_expense_best") or _f(row, "is_interest_expense")
                or _f(row, "fe_interest_expense") or 0.0)
    d2e = _f(row, "interest_debt_to_equity") or _f(row, "interest_debt_to_equity_cross")
    limit = 2.0  # 非金融企业 2:1（关联方债资比上限，超过部分利息不得扣除）
    steps = [
        {"step": "取得利息支出", "field": "interest_expense_best", "value": interest,
         "note": "优先取利息费用，缺失时以财务费用近似"},
        {"step": "取得债资比", "field": "interest_debt_to_equity", "value": d2e},
        {"step": "适用债资比上限", "value": limit, "note": "非金融企业关联方债资比 2:1（财税〔2008〕121号）"},
    ]
    results: dict[str, Any] = {"interest_expense": round(interest, 2)}
    bs = _f(row, "borrow_short_term"); bl = _f(row, "borrow_long_term")
    bc = _f(row, "borrow_current_portion")
    layers = [
        {"layer": "利息支出（或财务费用）", "value": round(interest, 2), "source": "interest_expense_best"},
        {"layer": "短期借款（明细）", "value": bs, "source": "borrow_short_term"},
        {"layer": "长期借款（明细）", "value": bl, "source": "borrow_long_term"},
        {"layer": "一年内到期借款（明细）", "value": bc, "source": "borrow_current_portion"},
        {"layer": "债资比（有息负债/权益）", "value": d2e, "source": "interest_debt_to_equity"},
        {"layer": "允许债资比上限", "value": limit, "note": "非金融企业关联方 2:1"},
    ]
    if interest and interest > 0 and (bs or bl or bc):
        results["implied_rate_on_borrowings"] = round(interest / ((bs or 0) + (bl or 0) + (bc or 0) + 1e-9), 4)
    if d2e is not None:
        results["debt_to_equity"] = round(d2e, 3)
        results["limit_ratio"] = limit
        results["over_limit"] = bool(d2e > limit)
        layers.append({"layer": "是否超过债资比上限", "value": "是" if d2e > limit else "否"})
        if d2e > limit:
            excess = (d2e - limit) / d2e
            results["potential_disallowance_ratio"] = round(excess, 4)
            layers.append({"layer": "超出部分占比", "value": round(excess, 4),
                           "note": "超出部分对应利息可能不得税前扣除"})
            steps.append({"step": "超出债资比部分占比", "value": round(excess, 4)})
    return {
        "calculator": "interest_deduction",
        "ok": interest > 0,
        "unit": "元",
        "layers": layers,
        "steps": steps,
        "results": results,
        "assumptions": ["以整体有息负债/权益近似关联方债资比，未区分关联方与非关联方债务"],
        "amount_is_estimate": True,
        "caliber_verified": False,
        "evidence_required": ["关联方借款明细（本金/利率/期限）", "权益投资金额", "同期同类贷款利率证明"],
        "note": "债资比以整体有息负债/权益近似关联方债资比，非精确口径，需结合关联方明细复核。",
    }


@calculator("hightech_rate_benefit")
def calc_hightech_rate_benefit(row: dict) -> dict:
    """高新技术企业 15% 优惠税率相对 25% 法定税率的节税估算。

    参数：row 含 is_total_profit（利润总额）。
    返回：利润总额 × (25% - 15%) 的税率优惠估算额（税盾性质）。
    口径边界：以利润总额为基数，未考虑弥补亏损、其他优惠叠加。
    """
    profit = _f(row, "is_total_profit") or 0.0
    pref, base = 0.15, 0.25
    layers = [
        {"layer": "利润总额", "value": profit, "source": "is_total_profit"},
        {"layer": "法定税率", "value": base},
        {"layer": "高新技术企业优惠税率", "value": pref},
    ]
    steps = [
        {"step": "取得利润总额", "field": "is_total_profit", "value": profit},
        {"step": "高新技术企业优惠税率", "value": pref},
        {"step": "法定税率", "value": base},
    ]
    if profit <= 0:
        return {"calculator": "hightech_rate_benefit", "ok": False,
                "reason": "利润总额非正，税率优惠无当期影响",
                "layers": layers, "steps": steps, "results": {}}
    benefit = profit * (base - pref)   # 税率优惠节税额 = 利润总额 × (25% - 15%)
    layers.append({"layer": "税率优惠估算节税额", "value": round(benefit, 2)})
    steps.append({"step": "估算税率优惠节税额", "formula": "利润总额 × (25% - 15%)",
                  "value": round(benefit, 2)})
    return {
        "calculator": "hightech_rate_benefit", "ok": True, "unit": "元",
        "layers": layers, "steps": steps,
        "results": {"total_profit": round(profit, 2), "preferential_rate": pref,
                    "statutory_rate": base, "tax_saving": round(benefit, 2)},
        "assumptions": ["以利润总额为基数、未考虑弥补亏损与其他优惠叠加"],
        "amount_is_estimate": True,
        "caliber_verified": False,
        "evidence_required": ["高新技术企业证书及有效期", "研发费用占比/科技人员占比/高新收入占比达标证明"],
        "note": "为优惠税率相对 25% 法定税率的估算影响，未考虑弥补亏损、其他优惠叠加等。",
    }


@calculator("income_tax_reconciliation")
def calc_income_tax_reconciliation(row: dict) -> dict:
    """所得税税会差异：按名义税率理论税负 vs 实际所得税费用。

    参数：row 含 is_total_profit、is_income_tax，可选 nominal_tax_rate、
          current_tax_expense/deferred_tax_expense 拆分。
    返回：理论税额、实际税额、差异、ETR。
    口径边界：名义税率近似法定税率；差异未拆分永久性/暂时性。
    """
    tp = _f(row, "is_total_profit")
    it = _f(row, "is_income_tax")
    nominal = _f(row, "nominal_tax_rate")
    deferred = _f(row, "d205606775__Fn02605")  # 递延所得税年末数（辅助）
    rate = (nominal / 100.0) if nominal is not None else 0.25
    layers = [
        {"layer": "利润总额", "value": tp, "source": "is_total_profit"},
        {"layer": "名义税率", "value": rate, "source": "nominal_tax_rate"},
    ]
    steps = [
        {"step": "取得利润总额", "field": "is_total_profit", "value": tp},
        {"step": "取得实际所得税费用", "field": "is_income_tax", "value": it},
    ]
    if tp is None or it is None:
        return {"calculator": "income_tax_reconciliation", "ok": False,
                "reason": "缺少利润总额或所得税费用", "layers": layers, "steps": steps, "results": {}}
    theoretical = tp * rate
    diff = it - theoretical
    etr = it / tp if tp else None   # 实际税率 = 实际所得税费用 / 利润总额
    layers += [
        {"layer": "按名义税率计算的所得税", "value": round(theoretical, 2)},
        {"layer": "实际所得税费用", "value": round(it, 2), "source": "is_income_tax"},
        {"layer": "税会差异（实际-理论）", "value": round(diff, 2)},
        {"layer": "实际税率 ETR", "value": etr},
    ]
    if deferred is not None:
        layers.append({"layer": "递延所得税年末数（辅助）", "value": deferred,
                       "source": "d205606775__Fn02605"})
    cur = _f(row, "current_tax_expense")
    dfr = _f(row, "deferred_tax_expense")
    if cur is not None:
        layers.append({"layer": "其中：当期所得税费用", "value": round(cur, 2),
                       "source": "current_tax_expense"})
    if dfr is not None:
        layers.append({"layer": "其中：递延所得税费用", "value": round(dfr, 2),
                       "source": "deferred_tax_expense"})
    steps.append({"step": "计算税会差异", "formula": "实际所得税费用 - 利润总额×名义税率",
                  "value": round(diff, 2)})
    return {
        "calculator": "income_tax_reconciliation", "ok": True, "unit": "元",
        "layers": layers, "steps": steps,
        "results": {"total_profit": round(tp, 2), "nominal_rate": rate,
                    "theoretical_tax": round(theoretical, 2), "actual_tax": round(it, 2),
                    "difference": round(diff, 2), "etr": round(etr, 4) if etr is not None else None},
        "assumptions": ["以名义税率近似法定税率；差异未区分永久性差异与暂时性差异"],
        "amount_is_estimate": True,
        "caliber_verified": False,
        "evidence_required": ["纳税调整明细表", "递延所得税资产/负债明细", "免税收入、加计扣除等优惠明细"],
        "note": "差异为会计与税务口径的综合结果，需进一步拆分永久性/暂时性差异方可判断筹划空间。",
    }


@calculator("asset_impairment")
def calc_asset_impairment(row: dict) -> dict:
    """资产减值损失/信用减值损失的纳税调整影响（一般不得税前扣除）。

    参数：row 含 is_asset_impairment、is_credit_impairment 及可选减值明细。
    返回：减值合计 × 税率的纳税调整影响估算。
    口径边界：会计计提的减值准备不得税前扣除，实际发生损失符合条件方可扣除。
    """
    a = _f(row, "is_asset_impairment") or 0.0
    c = _f(row, "is_credit_impairment") or 0.0
    total = a + c
    rate, rate_note = _rate(row)
    layers = [
        {"layer": "资产减值损失", "value": round(a, 2), "source": "is_asset_impairment"},
        {"layer": "信用减值损失", "value": round(c, 2), "source": "is_credit_impairment"},
        {"layer": "减值损失合计", "value": round(total, 2)},
        {"layer": "适用税率", "value": rate, "note": rate_note},
    ]
    if total <= 0:
        return {"calculator": "asset_impairment", "ok": False, "reason": "无减值损失",
                "layers": layers, "steps": [], "results": {}}
    effect = total * rate
    layers.append({"layer": "纳税调整影响（估算）", "value": round(effect, 2),
                   "note": "一般不得税前扣除，若已扣除需纳税调增"})
    # 明细结构（资产减值 / 信用减值明细）
    # 仅在明细非空且非零时展示，避免报告出现大量 0 值行
    for label, field in [("其中：应收/坏账类", "asset_imp_receivable"),
                         ("其中：存货类", "asset_imp_inventory"),
                         ("其中：固定资产类", "asset_imp_fixed_asset"),
                         ("其中：商誉类", "asset_imp_goodwill"),
                         ("信用减值明细合计", "credit_imp_total")]:
        v = _f(row, field)
        if v is not None and v != 0:
            layers.append({"layer": label, "value": round(v, 2), "source": field})
    layers.append({"layer": "申报方式", "value": "填报年度申报表 + 资料留存备查",
                   "note": "国家税务总局公告2018年第15号：资产损失不再区分清单/专项申报，改为填报申报表并留存备查资料"})
    return {
        "calculator": "asset_impairment", "ok": True, "unit": "元",
        "layers": layers,
        "steps": [{"step": "汇总减值损失", "value": round(total, 2)},
                  {"step": "估算纳税调整影响", "formula": "减值损失 × 税率", "value": round(effect, 2)}],
        "results": {"asset_impairment": round(a, 2), "credit_impairment": round(c, 2),
                    "total_impairment": round(total, 2), "tax_rate": rate, "adjustment_effect": round(effect, 2),
                    "asset_imp_detail_sum": _f(row, "asset_imp_total"),
                    "credit_imp_detail_sum": _f(row, "credit_imp_total")},
        "assumptions": ["会计计提的减值准备不得税前扣除；实际发生损失符合条件方可扣除"],
        "amount_is_estimate": True, "caliber_verified": False,
        "evidence_required": ["资产减值明细及计提依据", "资产损失税前扣除留存备查资料（清单）",
                              "坏账核销/资产处置证明", "纳税调整明细表"],
        "note": "资产损失税前扣除现行采取'填报申报表 + 留存备查资料'（2018年第15号），需准备完整损失证据链。",
    }


@calculator("employee_compensation")
def calc_employee_compensation(row: dict) -> dict:
    """职工薪酬税前扣除规模估算。

    参数：row 含 employee_compensation_base（缺失回退应付职工薪酬）、
          payroll_wages 及三项经费明细。
    返回：工资薪金税盾估算 + 三项经费（福利14%/工会2%/教育8%）超限调增。
    口径边界：以本期计提近似合理工资薪金；三项经费以工资薪金总额为基数。
    """
    accrued = _f(row, "employee_compensation_base")
    if accrued is None:
        accrued = _f(row, "d210230772__Fn03203")
    payable = _f(row, "bs_payroll_payable")
    base = accrued if accrued is not None else payable
    rate, rate_note = _rate(row)
    layers = []
    if accrued is not None:
        layers.append({"layer": "本期计提职工薪酬", "value": round(accrued, 2), "source": "employee_compensation_base"})
    if payable is not None:
        layers.append({"layer": "应付职工薪酬期末余额", "value": round(payable, 2), "source": "bs_payroll_payable"})
    layers.append({"layer": "适用税率", "value": rate, "note": rate_note})
    if not base or base <= 0:
        return {"calculator": "employee_compensation", "ok": False, "reason": "无职工薪酬数据",
                "layers": layers, "steps": [], "results": {}}
    effect = base * rate
    layers.append({"layer": "工资薪金扣除的估算税盾", "value": round(effect, 2)})

    # 三项经费限额（以工资薪金总额为基数）
    # 税务规则：福利费≤14%、工会经费≤2%、教育经费≤8%，超限部分纳税调增
    wages = _f(row, "payroll_wages")
    results_extra: dict[str, Any] = {}
    adjustment = 0.0
    if wages and wages > 0:
        for name, field, limit_rate in [
            ("职工福利费", "payroll_welfare", 0.14),
            ("工会经费", "payroll_union", 0.02),
            ("职工教育经费", "payroll_education", 0.08),
        ]:
            actual = _f(row, field)
            if actual is None:
                continue
            limit = wages * limit_rate
            excess = max(0.0, actual - limit)
            adjustment += excess
            layers.append({"layer": f"{name}（发生额）", "value": round(actual, 2), "source": field})
            layers.append({"layer": f"{name}扣除限额（工资×{limit_rate:.0%}）", "value": round(limit, 2)})
            layers.append({"layer": f"{name}超限（调增）", "value": round(excess, 2)})
            results_extra[f"{field}_excess"] = round(excess, 2)
        results_extra["three_funds_adjustment"] = round(adjustment, 2)
        results_extra["three_funds_tax_effect"] = round(adjustment * rate, 2)
        layers.append({"layer": "三项经费超限合计（调增）", "value": round(adjustment, 2)})

    return {
        "calculator": "employee_compensation", "ok": True, "unit": "元",
        "layers": layers,
        "steps": [{"step": "取得职工薪酬计提额", "value": round(base, 2)},
                  {"step": "估算税盾", "formula": "职工薪酬 × 税率", "value": round(effect, 2)},
                  {"step": "三项经费超限调增", "value": round(adjustment, 2)}],
        "results": {"compensation_base": round(base, 2), "tax_rate": rate,
                    "tax_shield": round(effect, 2), **results_extra},
        "assumptions": ["以本期计提职工薪酬近似可扣除的合理工资薪金；三项经费限额以工资薪金总额为基数"],
        "amount_is_estimate": True, "caliber_verified": False,
        "evidence_required": ["工资薪金发放明细及个税申报表", "社保/公积金缴纳凭证",
                              "职工福利费、工会经费、教育经费明细"],
        "note": "合理工资薪金需实际发放且已代扣代缴个税；三项经费超限额部分需纳税调增。",
    }


@calculator("vat_burden")
def calc_vat_burden(row: dict) -> dict:
    """增值税税负水平（CSMAR 无销项/进项，只能近似税负）。

    参数：row 含 vat_rate、is_revenue、cf_tax_paid、cf_tax_refund 等。
    返回：税费现金比（支付各项税费/营业收入）等税负水平指标。
    口径边界：结构化数据无销项/进项税额，无法精确计算应纳增值税与留抵税额。
    """
    vat = _f(row, "vat_rate")
    rev = _f(row, "is_revenue")
    tax_paid = _f(row, "cf_tax_paid")
    tax_surcharge = _f(row, "is_tax_surcharge")
    payable = _f(row, "bs_tax_payable")
    vat_payable = _f(row, "taxpay_vat")
    tax_refund = _f(row, "cf_tax_refund")
    # 分层仅展示可得指标；增值税精确税负需销项/进项，此处为近似水平
    layers = [
        {"layer": "增值税名义税率(%)", "value": vat, "source": "vat_rate"},
        {"layer": "营业收入", "value": rev, "source": "is_revenue"},
        {"layer": "支付的各项税费", "value": tax_paid, "source": "cf_tax_paid"},
        {"layer": "收到的税费返还", "value": tax_refund, "source": "cf_tax_refund",
         "note": "可能含出口退税/留抵退税，无法区分"},
        {"layer": "应交增值税期末余额", "value": vat_payable, "source": "taxpay_vat"},
        {"layer": "税金及附加", "value": tax_surcharge, "source": "is_tax_surcharge"},
        {"layer": "应交税费期末余额", "value": payable, "source": "bs_tax_payable"},
    ]
    if rev is None or rev <= 0:
        return {"calculator": "vat_burden", "ok": False, "reason": "无营业收入",
                "layers": layers, "steps": [], "results": {}}
    cash_tax_ratio = (tax_paid / rev) if tax_paid is not None else None
    if cash_tax_ratio is not None:
        layers.append({"layer": "税费现金比（支付税费/收入）", "value": cash_tax_ratio})
    return {
        "calculator": "vat_burden", "ok": True, "unit": "元",
        "layers": layers,
        "steps": [{"step": "计算税费现金比", "formula": "支付的各项税费 / 营业收入",
                   "value": round(cash_tax_ratio, 4) if cash_tax_ratio is not None else None}],
        "results": {"vat_rate": vat, "revenue": round(rev, 2),
                    "tax_paid": round(tax_paid, 2) if tax_paid is not None else None,
                    "tax_refund_received": tax_refund,
                    "vat_payable_end": vat_payable,
                    "cash_tax_ratio": round(cash_tax_ratio, 4) if cash_tax_ratio is not None else None},
        "assumptions": ["CSMAR 无增值税销项/进项税额，无法精确计算增值税与留抵税额"],
        "amount_is_estimate": True, "caliber_verified": False,
        "evidence_required": ["增值税申报表（销项、进项、留抵税额）", "税负率行业对比资料",
                              "留抵退税/出口退税资料"],
        "note": "**留抵税额需增值税申报表，结构化数据无法获取**；本项为税负水平提示，非应纳增值税额。",
    }


@calculator("expense_deduction_limit")
def calc_expense_deduction_limit(row: dict) -> dict:
    """期间费用规模 + 业务招待费/广告宣传费限额测算。

    参数：row 含三项期间费用、is_revenue、expense_entertainment/advertising。
    返回：期间费用率、招待费（发生额60%与收入5‰孰低）与广告费（收入15%）超限调增。
    口径边界：广告费超限可结转以后年度，此处计入当期调增；特殊行业广告费限额为30%。
    """
    adm = _f(row, "is_admin_expense") or 0.0
    sell = _f(row, "is_selling_expense") or 0.0
    fin = _f(row, "is_finance_expense") or 0.0
    rev = _f(row, "is_revenue")
    total = adm + sell + fin

    entertainment = _f(row, "expense_entertainment")
    if entertainment is None:
        e1 = _f(row, "d165419839__FN_Fn07315") or 0.0
        e2 = _f(row, "d165419839__FN_Fn07316") or 0.0
        entertainment = e1 + e2 if (e1 or e2) else None
    advertising = _f(row, "expense_advertising")
    rate, rate_note = _rate(row)

    layers = [
        {"layer": "管理费用", "value": round(adm, 2), "source": "is_admin_expense"},
        {"layer": "销售费用", "value": round(sell, 2), "source": "is_selling_expense"},
        {"layer": "财务费用", "value": round(fin, 2), "source": "is_finance_expense"},
        {"layer": "期间费用合计", "value": round(total, 2)},
    ]
    if entertainment is not None:
        layers.append({"layer": "业务招待费", "value": round(entertainment, 2), "source": "expense_entertainment"})
    if advertising is not None:
        layers.append({"layer": "广告宣传费", "value": round(advertising, 2), "source": "expense_advertising"})
    layers.append({"layer": "适用税率", "value": rate, "note": rate_note})

    if rev is None or rev <= 0:
        return {"calculator": "expense_deduction_limit", "ok": False, "reason": "无营业收入",
                "layers": layers, "steps": [], "results": {}}

    ratio = total / rev
    layers.insert(4, {"layer": "期间费用率", "value": ratio})

    results: dict[str, Any] = {
        "admin": round(adm, 2), "selling": round(sell, 2), "finance": round(fin, 2),
        "total": round(total, 2), "revenue": round(rev, 2), "expense_ratio": round(ratio, 4),
    }
    adjustment = 0.0

    # 业务招待费：发生额60% 与 收入5‰ 孰低
    # 税务规则：两者取小作为扣除限额，超出部分永久性调增（不可结转）
    if entertainment is not None and entertainment > 0:
        ent_limit = min(entertainment * 0.6, rev * 0.005)
        ent_excess = max(0.0, entertainment - ent_limit)
        adjustment += ent_excess
        layers.append({"layer": "业务招待费扣除限额", "value": round(ent_limit, 2),
                       "note": "发生额×60% 与 营业收入×5‰ 孰低"})
        layers.append({"layer": "业务招待费超限（纳税调增）", "value": round(ent_excess, 2)})
        results.update({"entertainment": round(entertainment, 2),
                        "entertainment_limit": round(ent_limit, 2),
                        "entertainment_excess": round(ent_excess, 2)})

    # 广告宣传费：一般不超过收入15%
    # 税务规则：一般行业 15%，超限部分可结转以后年度（与招待费不同）
    if advertising is not None and advertising > 0:
        adv_limit = rev * 0.15
        adv_excess = max(0.0, advertising - adv_limit)
        adjustment += adv_excess
        layers.append({"layer": "广告宣传费扣除限额", "value": round(adv_limit, 2),
                       "note": "一般不超过营业收入15%（特殊行业30%）"})
        layers.append({"layer": "广告宣传费超限（可结转）", "value": round(adv_excess, 2)})
        results.update({"advertising": round(advertising, 2),
                        "advertising_limit": round(adv_limit, 2),
                        "advertising_excess": round(adv_excess, 2)})

    tax_effect = adjustment * rate
    layers.append({"layer": "估算纳税调整影响", "value": round(tax_effect, 2)})
    results.update({"total_adjustment": round(adjustment, 2), "tax_rate": rate,
                    "tax_effect": round(tax_effect, 2)})

    return {
        "calculator": "expense_deduction_limit", "ok": True, "unit": "元",
        "layers": layers,
        "steps": [{"step": "计算期间费用率", "formula": "(管理+销售+财务)/营业收入", "value": round(ratio, 4)},
                  {"step": "计算超限调增", "value": round(adjustment, 2)}],
        "results": results,
        "assumptions": ["广告宣传费超限部分可结转以后年度扣除，此处计入当期调增"],
        "amount_is_estimate": True, "caliber_verified": False,
        "evidence_required": ["费用明细表（招待费/广告费金额）", "费用税前扣除凭证", "广告费结转台账"],
        "note": "业务招待费按发生额60%且不超收入5‰扣除；广告宣传费一般不超过收入15%（特殊行业30%）。",
    }


@calculator("depreciation_amortization")
def calc_depreciation_amortization(row: dict) -> dict:
    """折旧摊销规模与税盾估算。

    参数：row 含固定资产/无形资产/开发支出净额，及 fa_accum_dep_increase 本期折旧。
    返回：折旧摊销类资产合计、本期折旧税盾估算。
    口径边界：以资产净额近似可折旧摊销规模，未按税法最低年限与残值率重算。
    """
    fa = _f(row, "bs_fixed_assets") or 0.0
    ia = _f(row, "bs_intangible_assets") or 0.0
    dev = _f(row, "bs_development_expense") or 0.0
    ndts = _f(row, "csmar__NonDebtTaxShield")
    rate, rate_note = _rate(row)
    layers = [
        {"layer": "固定资产净额", "value": round(fa, 2), "source": "bs_fixed_assets"},
        {"layer": "固定资产原值（明细）", "value": _f(row, "fa_original_end"), "source": "fa_original_end"},
        {"layer": "本期计提折旧（明细）", "value": _f(row, "fa_accum_dep_increase"),
         "source": "fa_accum_dep_increase"},
        {"layer": "累计折旧期末（明细）", "value": _f(row, "fa_accum_dep_end"), "source": "fa_accum_dep_end"},
        {"layer": "无形资产净额", "value": round(ia, 2), "source": "bs_intangible_assets"},
        {"layer": "开发支出", "value": round(dev, 2), "source": "bs_development_expense"},
        {"layer": "非债务税盾(折旧/总资产)", "value": ndts, "source": "csmar__NonDebtTaxShield"},
        {"layer": "适用税率", "value": rate, "note": rate_note},
    ]
    dep_inc = _f(row, "fa_accum_dep_increase")
    if dep_inc is not None:
        # 本期折旧的税盾 = 本期计提折旧 × 税率（税盾非新增收益，仅表时间性影响）
        layers.append({"layer": "本期折旧的估算税盾", "value": round(dep_inc * rate, 2)})
    base = fa + ia + dev
    if base <= 0:
        return {"calculator": "depreciation_amortization", "ok": False, "reason": "无折旧摊销类资产",
                "layers": layers, "steps": [], "results": {}}
    return {
        "calculator": "depreciation_amortization", "ok": True, "unit": "元",
        "layers": layers,
        "steps": [{"step": "汇总折旧摊销类资产", "value": round(base, 2)}],
        "results": {"fixed_assets": round(fa, 2), "intangible_assets": round(ia, 2),
                    "development": round(dev, 2), "base": round(base, 2),
                    "fa_original_end": _f(row, "fa_original_end"),
                    "fa_accum_dep_increase": dep_inc,
                    "tax_shield": round(dep_inc * rate, 2) if dep_inc is not None else None,
                    "non_debt_tax_shield": ndts, "tax_rate": rate},
        "assumptions": ["以资产净额近似可折旧摊销规模，未按税法最低年限与残值率计算年度折旧额"],
        "amount_is_estimate": True, "caliber_verified": False,
        "evidence_required": ["固定资产/无形资产明细及折旧摊销政策", "税会折旧年限差异台账"],
        "note": "税会折旧年限/方法差异需台账管理；本项为规模提示。",
    }


@calculator("loss_carryforward")
def calc_loss_carryforward(row: dict) -> dict:
    """亏损弥补：以未分配利润与利润总额判断可弥补亏损规模。

    参数：row 含 is_total_profit、bs_retained_earnings。
    返回：情景测算的可弥补亏损规模与潜在税盾（非结论）。
    口径边界：会计亏损 ≠ 税法亏损，税法口径可弥补亏损需以历年申报表认定。
    """
    profit = _f(row, "is_total_profit")
    retained = _f(row, "bs_retained_earnings")
    rate, rate_note = _rate(row)
    layers = [
        {"layer": "会计观察：利润总额", "value": profit, "source": "is_total_profit"},
        {"layer": "会计观察：未分配利润", "value": retained, "source": "bs_retained_earnings"},
        {"layer": "适用税率", "value": rate, "note": rate_note},
    ]
    if retained is None and profit is None:
        return {"calculator": "loss_carryforward", "ok": False, "reason": "缺少利润数据",
                "layers": layers, "steps": [], "results": {}}
    carry = abs(retained) if (retained is not None and retained < 0) else 0.0
    tax_shield = carry * rate   # 潜在税盾：仅为情景测算，不代表已认定的可弥补亏损
    layers.append({"layer": "税法口径：可弥补亏损", "value": None,
                   "note": "无法确认（需历年企业所得税年度申报表认定，会计亏损≠税法亏损）"})
    layers.append({"layer": "情景测算：可弥补亏损规模（假设）", "value": round(carry, 2),
                   "note": "假设全部未分配利润均为可弥补税法亏损"})
    layers.append({"layer": "情景测算：潜在税盾（假设）", "value": round(tax_shield, 2)})
    return {
        "calculator": "loss_carryforward", "ok": carry > 0, "unit": "元",
        "layers": layers,
        "steps": [{"step": "会计观察：未分配利润为负", "value": round(carry, 2)},
                  {"step": "税法口径可弥补亏损", "value": None, "note": "无法确认"}],
        "results": {"total_profit": profit, "retained_earnings": retained,
                    "carryforward_scenario": round(carry, 2), "tax_rate": rate,
                    "tax_shield": round(tax_shield, 2)},
        "assumptions": ["会计亏损 ≠ 税法亏损；需以历年申报表认定可弥补亏损及结转期限"],
        "amount_is_estimate": True, "caliber_verified": False,
        "evidence_required": ["历年企业所得税年度申报表（可弥补亏损额）", "亏损弥补台账"],
        "note": "情景测算仅供规模参考，不代表可实现收益；税法可弥补亏损以申报表认定为准。",
    }


@calculator("investment_income")
def calc_investment_income(row: dict) -> dict:
    """投资收益与股权投资收益的税务处理提示。

    参数：row 含 is_investment_income 及交易信息总表字段（评估/账面价值等）。
    返回：投资收益结构 + 股权/资产转让所得估算与估算税额。
    口径边界：股息红利（居民企业间符合条件可免税）与转让所得未完全区分；
              转让所得以评估增值近似，未扣除股权原值与合理费用。
    """
    inv = _f(row, "is_investment_income")
    ltei = _f(row, "d205404528__Fn01804")
    detail_sum = _f(row, "investment_income_detail_sum")
    rate, rate_note = _rate(row)
    layers = [
        {"layer": "投资收益（利润表）", "value": inv, "source": "is_investment_income"},
        {"layer": "投资收益明细合计", "value": detail_sum, "source": "investment_income_detail_sum"},
        {"layer": "长期股权投资", "value": ltei, "source": "d205404528__Fn01804"},
        {"layer": "适用税率", "value": rate, "note": rate_note},
    ]
    # 股权转让/重组交易（交易信息总表）
    events = _f(row, "ma_event_count") or 0
    eval_v = _f(row, "ma_evaluation_value")
    book_v = _f(row, "ma_book_value")
    change = _f(row, "ma_evaluation_change")
    expense = _f(row, "ma_expense_value")
    results_extra: dict[str, Any] = {}
    if events and (eval_v is not None or expense is not None):
        layers += [
            {"layer": "交易/重组事件数", "value": events, "source": "ma_event_count"},
            {"layer": "标的评估价值", "value": eval_v, "source": "ma_evaluation_value"},
            {"layer": "标的账面价值", "value": book_v, "source": "ma_book_value"},
            {"layer": "评估增值", "value": change, "source": "ma_evaluation_change"},
            {"layer": "买方支出价值", "value": expense, "source": "ma_expense_value"},
        ]
        gain = change
        if gain is None and expense is not None and book_v is not None:
            # 缺评估增值时，用「买方支出 - 标的账面价值」近似转让所得
            gain = expense - book_v
        if gain is not None:
            layers.append({"layer": "股权/资产转让所得（估算）", "value": round(gain, 2),
                           "note": "转让收入 - 股权（资产）净值；以评估增值近似"})
            layers.append({"layer": "转让所得估算税额", "value": round(max(0.0, gain) * rate, 2)})
            results_extra = {"equity_transfer_gain": round(gain, 2),
                             "equity_transfer_tax": round(max(0.0, gain) * rate, 2),
                             "ma_event_count": events}
    if inv is None and not results_extra:
        return {"calculator": "investment_income", "ok": False, "reason": "无投资收益数据",
                "layers": layers, "steps": [], "results": {}}
    return {
        "calculator": "investment_income", "ok": True, "unit": "元",
        "layers": layers,
        "steps": [{"step": "取得投资收益", "value": inv},
                  {"step": "股权转让所得估算", "value": results_extra.get("equity_transfer_gain")}],
        "results": {"investment_income": inv, "long_term_equity_investment": ltei,
                    "tax_rate": rate, **results_extra},
        "assumptions": ["股息红利（符合条件的居民企业间可免税）与股权转让所得未完全区分",
                        "股权转让所得以评估增值近似，未扣除股权原值与合理费用"],
        "amount_is_estimate": True, "caliber_verified": False,
        "evidence_required": ["投资收益明细（股息/转让/权益法）", "被投资企业分红决议",
                              "股权转让合同与作价依据", "股权原值（投资成本）证明"],
        "note": "符合条件的居民企业之间的股息红利为免税收入；股权转让所得应计入应纳税所得额。",
    }


@calculator("tax_cash_flow")
def calc_tax_cash_flow(row: dict) -> dict:
    """税费现金流与计提的差异。

    参数：row 含 cf_tax_paid、bs_tax_payable、is_income_tax、is_tax_surcharge。
    返回：实缴/计提比（支付各项税费 / (所得税费用+税金及附加)）。
    口径边界：支付的各项税费含增值税等全部税费，与所得税+附加口径不完全可比。
    """
    paid = _f(row, "cf_tax_paid")
    payable = _f(row, "bs_tax_payable")
    it = _f(row, "is_income_tax")
    surcharge = _f(row, "is_tax_surcharge")
    layers = [
        {"layer": "支付的各项税费", "value": paid, "source": "cf_tax_paid"},
        {"layer": "应交税费期末余额", "value": payable, "source": "bs_tax_payable"},
        {"layer": "所得税费用", "value": it, "source": "is_income_tax"},
        {"layer": "税金及附加", "value": surcharge, "source": "is_tax_surcharge"},
    ]
    if paid is None:
        return {"calculator": "tax_cash_flow", "ok": False, "reason": "无税费现金流数据",
                "layers": layers, "steps": [], "results": {}}
    accrued = (it or 0) + (surcharge or 0)
    ratio = (paid / accrued) if accrued > 0 else None   # 实缴/计提比，分母为 0 则不计算
    if ratio is not None:
        layers.append({"layer": "实缴/计提比", "value": round(ratio, 4)})
    return {
        "calculator": "tax_cash_flow", "ok": True, "unit": "元",
        "layers": layers,
        "steps": [{"step": "计算实缴/计提比", "formula": "支付的各项税费 / (所得税费用+税金及附加)",
                   "value": round(ratio, 4) if ratio is not None else None}],
        "results": {"tax_paid": round(paid, 2), "tax_payable": payable,
                    "income_tax_expense": it, "surcharge": surcharge,
                    "paid_accrued_ratio": round(ratio, 4) if ratio is not None else None},
        "assumptions": ["支付的各项税费含增值税等全部税费，与所得税+附加口径不完全可比"],
        "amount_is_estimate": True, "caliber_verified": False,
        "evidence_required": ["各税种申报表与缴款凭证", "应交税费明细"],
        "note": "实缴与计提差异需结合各税种申报表分析。",
    }


@calculator("receivable_bad_debt")
def calc_receivable_bad_debt(row: dict) -> dict:
    """应收账款与坏账/信用减值。

    参数：row 含 bs_accounts_receivable、is_credit_impairment、is_revenue。
    返回：应收账款占收入比等结构指标。
    口径边界：计提的坏账准备不得税前扣除，实际发生坏账损失需符合扣除办法。
    """
    ar = _f(row, "bs_accounts_receivable") or 0.0
    ci = _f(row, "is_credit_impairment") or 0.0
    rev = _f(row, "is_revenue")
    rate, rate_note = _rate(row)
    # 结构指标：应收账款规模、信用减值、适用税率
    layers = [
        {"layer": "应收账款净额", "value": round(ar, 2), "source": "bs_accounts_receivable"},
        {"layer": "信用减值损失", "value": round(ci, 2), "source": "is_credit_impairment"},
        {"layer": "适用税率", "value": rate, "note": rate_note},
    ]
    if ar <= 0:
        return {"calculator": "receivable_bad_debt", "ok": False, "reason": "无应收账款",
                "layers": layers, "steps": [], "results": {}}
    ar_ratio = (ar / rev) if (rev and rev > 0) else None   # 占收入比，收入缺失/非正则不计算
    if ar_ratio is not None:
        layers.append({"layer": "应收账款占收入比", "value": ar_ratio})
    return {
        "calculator": "receivable_bad_debt", "ok": True, "unit": "元",
        "layers": layers,
        "steps": [{"step": "计算应收账款占比", "value": round(ar_ratio, 4) if ar_ratio is not None else None}],
        "results": {"accounts_receivable": round(ar, 2), "credit_impairment": round(ci, 2),
                    "ar_to_revenue": round(ar_ratio, 4) if ar_ratio is not None else None, "tax_rate": rate},
        "assumptions": ["计提的坏账准备不得税前扣除，实际发生坏账损失需符合扣除办法"],
        "amount_is_estimate": True, "caliber_verified": False,
        "evidence_required": ["应收账款账龄分析", "坏账核销审批与税前扣除资料", "信用减值明细"],
        "note": "坏账损失税前扣除需按规定申报并留存资料。",
    }


@calculator("gov_subsidy_nontaxable")
def calc_gov_subsidy_nontaxable(row: dict) -> dict:
    """政府补助的不征税收入判定提示。

    参数：row 含 gov_subsidy_total、gov_subsidy_income_related。
    返回：若全额应税的当期影响估算。
    口径边界：不征税收入需同时满足专项拨付文件、专门管理办法、单独核算三条件，
              且对应支出/折旧不得扣除，需权衡。
    """
    total = _f(row, "gov_subsidy_total") or 0.0
    income_rel = _f(row, "gov_subsidy_income_related")
    rate, rate_note = _rate(row)
    # 不征税判定需三条件（拨付文件/管理办法/单独核算），此处先展示规模与应税影响
    layers = [
        {"layer": "政府补助合计", "value": round(total, 2), "source": "gov_subsidy_total"},
        {"layer": "与收益相关", "value": income_rel, "source": "gov_subsidy_income_related"},
        {"layer": "适用税率", "value": rate, "note": rate_note},
    ]
    if total <= 0:
        return {"calculator": "gov_subsidy_nontaxable", "ok": False, "reason": "无政府补助",
                "layers": layers, "steps": [], "results": {}}
    taxable_effect = total * rate   # 若全额应税的当期影响（不征税需满足三条件）
    layers.append({"layer": "若全额应税的影响（估算）", "value": round(taxable_effect, 2)})
    return {
        "calculator": "gov_subsidy_nontaxable", "ok": True, "unit": "元",
        "layers": layers,
        "steps": [{"step": "估算应税影响", "formula": "政府补助 × 税率", "value": round(taxable_effect, 2)}],
        "results": {"gov_subsidy_total": round(total, 2), "income_related": income_rel,
                    "tax_rate": rate, "taxable_effect": round(taxable_effect, 2)},
        "assumptions": ["不征税收入需同时满足专项拨付文件、专门管理办法、单独核算三条件"],
        "amount_is_estimate": True, "caliber_verified": False,
        "evidence_required": ["资金拨付文件", "专项资金管理办法", "单独核算凭证"],
        "note": "不征税收入用于支出形成的费用/资产折旧摊销不得扣除，需权衡是否选择不征税处理。",
    }


@calculator("intangible_amortization")
def calc_intangible_amortization(row: dict) -> dict:
    """无形资产摊销年限税会差异。

    参数：row 含无形资产净额/期末原值/本期增加数。
    返回：按税法最低年限（一般不低于 10 年）的年摊销下限。
    口径边界：会计摊销年限短于税法最低年限时，超出部分需纳税调增（时间性差异）。
    """
    net = _f(row, "bs_intangible_assets") or 0.0
    end = _f(row, "d101955005__Fn02207")
    inc = _f(row, "d101955005__Fn02204")
    rate, rate_note = _rate(row)
    base = end if (end and end > 0) else net
    # 摊销下限口径：税法一般无形资产按不低于 10 年摊销（法律规定/合同约定的从其约定）
    layers = [
        {"layer": "无形资产净额", "value": round(net, 2), "source": "bs_intangible_assets"},
        {"layer": "无形资产期末原值", "value": end, "source": "d101955005__Fn02207"},
        {"layer": "本期增加数", "value": inc, "source": "d101955005__Fn02204"},
        {"layer": "税法最低摊销年限", "value": 10, "note": "一般无形资产不低于10年；法律规定/合同约定年限的从其约定"},
        {"layer": "适用税率", "value": rate, "note": rate_note},
    ]
    if base <= 0:
        return {"calculator": "intangible_amortization", "ok": False, "reason": "无无形资产",
                "layers": layers, "steps": [], "results": {}}
    annual_min = base / 10.0
    # 年摊销下限：按税法最低 10 年直线摊销，会计年限更短时超出部分需调增
    layers.append({"layer": "按最低年限的年摊销下限", "value": round(annual_min, 2)})
    return {
        "calculator": "intangible_amortization", "ok": True, "unit": "元",
        "layers": layers,
        "steps": [{"step": "按10年计算年摊销下限", "formula": "无形资产原值 / 10", "value": round(annual_min, 2)}],
        "results": {"intangible_net": round(net, 2), "intangible_end": end,
                    "increase": inc, "annual_amortization_min": round(annual_min, 2), "tax_rate": rate},
        "assumptions": ["会计摊销年限短于税法最低年限时，超出部分需纳税调增（时间性差异）"],
        "amount_is_estimate": True, "caliber_verified": False,
        "evidence_required": ["无形资产明细及摊销政策", "摊销年限依据（法律/合同/预计使用年限）", "税会摊销差异台账"],
        "note": "税会摊销年限差异需台账管理；本项为规模与下限提示。",
    }


@calculator("share_based_payment")
def calc_share_based_payment(row: dict) -> dict:
    """股份支付税前扣除。

    参数：row 含 expense_share_based（股份支付费用）。
    返回：估算税前扣除影响（费用 × 税率）。
    口径边界：等待期内会计确认的费用一般不得税前扣除，实际行权/解锁时按
              行权价与公允价差额扣除，故此处仅为规模提示。
    """
    sb = _f(row, "expense_share_based")
    rate, rate_note = _rate(row)
    layers = [
        {"layer": "股份支付金额（费用）", "value": sb, "source": "expense_share_based"},
        {"layer": "适用税率", "value": rate, "note": rate_note},
    ]
    if not sb or sb <= 0:
        return {"calculator": "share_based_payment", "ok": False, "reason": "无股份支付费用",
                "layers": layers, "steps": [], "results": {}}
    effect = sb * rate   # 估算税前扣除影响（实际扣除时点为行权/解锁时，非等待期内）
    layers.append({"layer": "税前扣除时点", "value": "实际行权/解锁时",
                   "note": "等待期内会计确认的费用一般不得税前扣除，实际行权时按公允价格差额扣除"})
    layers.append({"layer": "估算税前扣除影响", "value": round(effect, 2)})
    return {
        "calculator": "share_based_payment", "ok": True, "unit": "元",
        "layers": layers,
        "steps": [{"step": "取得股份支付费用", "value": round(sb, 2)},
                  {"step": "估算扣除影响", "formula": "股份支付费用 × 税率", "value": round(effect, 2)}],
        "results": {"share_based_expense": round(sb, 2), "tax_rate": rate, "tax_effect": round(effect, 2)},
        "assumptions": ["等待期内会计费用不得税前扣除，实际行权时按（行权价与公允价差额）扣除"],
        "amount_is_estimate": True, "caliber_verified": False,
        "evidence_required": ["股权激励计划与授予协议", "等待期/行权条件", "行权价格与公允价格资料", "个税递延备案"],
        "note": "股份支付税会差异显著，需按行权情况分年度调整。",
    }


@calculator("lease_tax_difference")
def calc_lease_tax_difference(row: dict) -> dict:
    """租赁（使用权资产/租赁负债）税会差异。

    参数：row 含使用权资产期末余额、本期计提折旧、租赁负债期末余额。
    返回：使用权资产/租赁负债规模；当折旧与存量严重不匹配时给出 warning。
    口径边界：税法上经营租赁仍按租金扣除，与会计「使用权资产折旧+利息」存在时间性差异。
    """
    rou = _f(row, "d205618410__EndingBalance") or 0.0
    dep = _f(row, "d205618410__Provision")
    lease_liab = _f(row, "d210441016__EndingBalance")
    rate, rate_note = _rate(row)
    layers = [
        {"layer": "使用权资产期末余额", "value": round(rou, 2), "source": "d205618410__EndingBalance"},
        {"layer": "其中：本期计提折旧", "value": dep, "source": "d205618410__Provision"},
        {"layer": "租赁负债期末余额", "value": lease_liab, "source": "d210441016__EndingBalance"},
        {"layer": "适用税率", "value": rate, "note": rate_note},
    ]
    if rou <= 0 and not lease_liab:
        return {"calculator": "lease_tax_difference", "ok": False, "reason": "无使用权资产/租赁负债",
                "layers": layers, "steps": [], "results": {}}
    warnings = []
    # 数据质量护栏：大额使用权资产却几乎无折旧（<0.5%），多半是字段口径错误而非真实为零，
    # 此时给出 warning 而非照常计算，避免用错误数据得出误导性结论。
    if rou > 1e8 and (dep is None or dep <= 0 or (dep / rou) < 0.005):
        warnings.append({
            "level": "warning", "field": "d205618410__Provision",
            "message": (f"使用权资产期末 {rou:,.0f} 元，但本期计提折旧仅 {dep}，"
                        f"两者严重不匹配（折旧/存量 < 0.5%），疑似字段口径错误或数据缺失，"
                        f"请核实租赁附注后再据以计算")})
    return {
        "calculator": "lease_tax_difference", "ok": True, "unit": "元",
        "layers": layers,
        "steps": [{"step": "取得使用权资产与租赁负债", "value": round(rou, 2)}],
        "results": {"right_of_use_asset": round(rou, 2), "depreciation": dep,
                    "lease_liability": lease_liab, "tax_rate": rate},
        "assumptions": ["税法上经营租赁仍按租金扣除，与会计使用权资产折旧+利息存在时间性差异"],
        "warnings": warnings,
        "amount_is_estimate": True, "caliber_verified": False,
        "evidence_required": ["租赁合同与租赁期", "使用权资产折旧与租赁负债利息计算表", "税会差异台账"],
        "note": "经营租赁税会差异需按租赁期分摊调整；短期/低价值租赁可简化处理。",
    }


@calculator("gov_subsidy_split")
def calc_gov_subsidy_split(row: dict) -> dict:
    """政府补助：与资产相关 / 与收益相关 的税务处理拆分。

    参数：row 含 gov_subsidy_total、gov_subsidy_income_related。
    返回：与资产相关（递延收益分期）与收益相关（当期损益）的拆分及应税影响。
    口径边界：与资产相关补助会计上递延；是否作不征税收入需满足三条件。
    """
    total = _f(row, "gov_subsidy_total") or 0.0
    income_rel = _f(row, "gov_subsidy_income_related")
    rate, rate_note = _rate(row)
    if income_rel is None:
        income_rel = 0.0
    # 与资产相关 = 合计 - 与收益相关（会计上计入递延收益，按资产寿命分期）
    asset_rel = max(0.0, total - income_rel)
    layers = [
        {"layer": "政府补助合计", "value": round(total, 2), "source": "gov_subsidy_total"},
        {"layer": "其中：与收益相关", "value": round(income_rel, 2),
         "source": "gov_subsidy_income_related", "note": "计入当期损益"},
        {"layer": "其中：与资产相关", "value": round(asset_rel, 2),
         "note": "递延收益，按资产使用寿命分期计入损益"},
        {"layer": "适用税率", "value": rate, "note": rate_note},
    ]
    if total <= 0:
        return {"calculator": "gov_subsidy_split", "ok": False, "reason": "无政府补助",
                "layers": layers, "steps": [], "results": {}}
    layers.append({"layer": "若全额应税的当期影响（估算）", "value": round(total * rate, 2),
                   "note": "与资产相关部分会计递延，税务处理需按不征税收入条件判断"})
    return {
        "calculator": "gov_subsidy_split", "ok": True, "unit": "元",
        "layers": layers,
        "steps": [{"step": "拆分与资产/收益相关", "value": {"资产相关": round(asset_rel, 2),
                                                            "收益相关": round(income_rel, 2)}}],
        "results": {"gov_subsidy_total": round(total, 2), "income_related": round(income_rel, 2),
                    "asset_related": round(asset_rel, 2), "tax_rate": rate,
                    "taxable_effect": round(total * rate, 2)},
        "assumptions": ["与资产相关补助会计上递延；是否作不征税收入需满足三条件"],
        "amount_is_estimate": True, "caliber_verified": False,
        "evidence_required": ["补助文件与用途说明", "与资产/收益相关的划分依据", "递延收益摊销表", "不征税收入三条件资料"],
        "note": "不征税收入对应支出不得扣除，需权衡；与资产相关补助的税会摊销年限可能不一致。",
    }


@calculator("overseas_tax_credit")
def calc_overseas_tax_credit(row: dict) -> dict:
    """境外所得抵免限额测算（以子公司口径近似）。

    参数：row 含海外子公司数量、子公司合计净利润/营业收入。
    返回：抵免限额估算（境外所得 × 境内税率）。
    口径边界：以子公司合计净利润近似境外所得，未区分境内/境外、未按分国分项计算。
    """
    overseas = _f(row, "subsidiary_overseas_count") or 0.0
    netprofit = _f(row, "subsidiary_total_netprofit")
    revenue = _f(row, "subsidiary_total_revenue")
    rate, rate_note = _rate(row)
    layers = [
        {"layer": "海外子公司数量", "value": overseas, "source": "subsidiary_overseas_count"},
        {"layer": "子公司合计营业收入", "value": revenue, "source": "subsidiary_total_revenue"},
        {"layer": "子公司合计净利润", "value": netprofit, "source": "subsidiary_total_netprofit"},
        {"layer": "境内适用税率", "value": rate, "note": rate_note},
    ]
    base = netprofit if (netprofit and netprofit > 0) else None
    if base is None:
        return {"calculator": "overseas_tax_credit", "ok": False,
                "reason": "缺少境外所得数据（子公司净利润）", "layers": layers, "steps": [], "results": {}}
    credit_limit = base * rate   # 抵免限额 = 境外所得 × 境内税率（实际抵免取已纳税额与限额较低者）
    layers.append({"layer": "抵免限额（估算）", "value": round(credit_limit, 2),
                   "note": "境外所得 × 境内税率；实际抵免以分国/分项计算并取较低者"})
    return {
        "calculator": "overseas_tax_credit", "ok": True, "unit": "元",
        "layers": layers,
        "steps": [{"step": "估算抵免限额", "formula": "境外所得 × 境内税率", "value": round(credit_limit, 2)}],
        "results": {"overseas_subsidiaries": overseas, "overseas_income_proxy": round(base, 2),
                    "tax_rate": rate, "credit_limit": round(credit_limit, 2)},
        "assumptions": ["以子公司合计净利润近似境外所得，未区分境内/境外子公司、未考虑分国分项"],
        "amount_is_estimate": True, "caliber_verified": False,
        "evidence_required": ["境外子公司注册地与经营情况", "境外所得及已纳税额证明", "分国（地区）抵免计算表"],
        "note": "境外所得税抵免需按分国不分项或综合抵免法计算，并受抵免限额约束。",
    }


@calculator("small_taxes_structure")
def calc_small_taxes_structure(row: dict) -> dict:
    """小税种结构：按税种拆分税金及附加。

    参数：row 含各税种（城建/教育附加/印花/房产/土地/车船/环保/其他）金额。
    返回：各税种金额与占比、税金及附加合计。
    口径边界：税金及附加为会计口径，各税种申报以申报表为准。
    """
    fields = [
        ("城市维护建设税", "surcharge_urban_maintenance"),
        ("教育费附加（含地方）", "surcharge_education"),
        ("印花税", "surcharge_stamp"),
        ("房产税", "surcharge_property"),
        ("土地使用税", "surcharge_land_use"),
        ("车船税", "surcharge_vehicle_vessel"),
        ("环境保护税", "surcharge_environmental"),
        ("其他", "surcharge_other"),
    ]
    layers, results = [], {}
    total = 0.0
    # 逐税种累加，仅收录非空且非零项，避免报告出现 0 值行
    for name, f in fields:
        v = _f(row, f)
        if v is not None and v != 0:
            layers.append({"layer": name, "value": round(v, 2), "source": f})
            results[f] = round(v, 2)
            total += v
    if total <= 0:
        return {"calculator": "small_taxes_structure", "ok": False, "reason": "无税金及附加明细",
                "layers": layers, "steps": [], "results": {}}
    layers.append({"layer": "税金及附加合计", "value": round(total, 2)})
    # 二次遍历计算各税种占比（需先得合计），仅展示有值的税种
    for name, f in fields:
        v = results.get(f)
        if v:
            layers.append({"layer": f"{name}占比", "value": round(v / total, 4)})
    return {
        "calculator": "small_taxes_structure", "ok": True, "unit": "元",
        "layers": layers,
        "steps": [{"step": "按税种拆分税金及附加", "value": round(total, 2)}],
        "results": {**results, "surcharge_total": round(total, 2)},
        "assumptions": ["税金及附加为会计口径，各税种申报以申报表为准"],
        "amount_is_estimate": True, "caliber_verified": False,
        "evidence_required": ["房产/土地权属证明与计税依据", "印花税应税凭证台账", "各税种申报表"],
        "note": "小税种以申报表为准；本项为结构提示。",
    }


def run_calculator(cid: str, row: dict, key_inputs: list[str] | None = None,
                   direct_fields: set[str] | None = None, skill_id: str | None = None) -> dict | None:
    """执行计算器并裁定其税务语义（确认/情景/税盾/不可计算）。

    参数：
        cid          计算器 id（未注册返回 None）；
        row          企业画像行；
        key_inputs   关键输入字段名列表，用于字段级 Resolution 判定；
        direct_fields 已由直接口径证据覆盖的字段集合；
        skill_id     当前 Skill，用于证据强度判定。
    返回：在计算器原始结果上补齐 calculation_type / impact_type /
          confirmed_tax_impact / scenario_tax_impact 等语义字段后的 dict。
    关键口径：
        「确认税务影响」只可能来自 INCREMENTAL_TAX_BENEFIT ∧ tax_reduce 且无代理输入；
        税负增加只作风险提示；既有税盾/情景/无需计算一律不得确认。
    """
    fn = CALCULATORS.get(cid)
    if fn is None:
        return None
    res = fn(row)
    if not isinstance(res, dict):
        return res
    res["calculation_type"] = calculation_type_of(cid)
    # 语义登记：金额的经济方向与含义（config/calculator_semantics.yaml）
    from src.rules.semantics import default_direction, semantics_of
    sem = semantics_of(cid)
    res["impact_basis"] = sem.get("economic_meaning")
    res["baseline"] = sem.get("baseline")
    res["action"] = sem.get("action")
    if sem.get("caveat"):
        res["caveat"] = sem["caveat"]
    # 统一为每个计算分层补齐展示类型 kind（amount/ratio/percent/text）
    for layer in (res.get("layers") or []):
        if isinstance(layer, dict) and "kind" not in layer:
            layer["kind"] = infer_layer_kind(layer.get("layer"), layer.get("value"))
    if not res.get("ok"):
        # 计算器自身不成立（如无营业收入/无减值）→ 不可计算
        res["direction"] = "none"
        res["calculation_status"] = "NOT_CALCULABLE"
        res["impact_amount"] = None
        res["confirmed_tax_impact"] = None
        res["scenario_tax_impact"] = None
        res.setdefault("impact_type", "NOT_CALCULABLE")
        return res

    # 字段级 Resolution：对「关键输入」按 INSUFFICIENT/MISSING > PROXY > DIRECT 判定
    from src.rules.fact_resolution import classify, proxy_inputs
    # 未提供 key_inputs 时视为无约束（空判定），保持向后兼容
    cls = (classify(key_inputs, row, direct_fields=direct_fields, skill_id=skill_id) if key_inputs
           else {"missing": [], "insufficient": [], "proxy": [], "direct": []})
    blocked = cls["missing"] + cls["insufficient"]
    direction = sem.get("impact_direction") or default_direction(res.get("calculation_type", ""))
    res["direction"] = direction

    if blocked:
        # 关键输入缺失/不足 → 一律不可计算，禁止用残值硬算（防止误导性金额）
        res["ok"] = False
        res["calculation_type"] = "NOT_CALCULABLE"
        res["impact_type"] = "NOT_CALCULABLE"
        res["calculation_status"] = "NOT_CALCULABLE"
        res["direction"] = "none"
        res["not_calculable_inputs"] = blocked
        results = res.setdefault("results", {})
        results["confirmed_tax_impact"] = None
        results["scenario_tax_impact"] = None
        res["confirmed_tax_impact"] = None
        res["scenario_tax_impact"] = None
        res["impact_amount"] = None
        res.setdefault("reason", "关键输入缺失/不足：" + "、".join(blocked))
        return res

    proxy = cls["proxy"]
    base_verified = bool(res.get("caliber_verified", False)) or (cid in VERIFIABLE_CALCULATORS)
    # 系统不变量：确认的税收影响金额只能来自 INCREMENTAL_TAX_BENEFIT ∧ tax_reduce，且恒 ≥0
    # （既有税盾 / 情景 / 无需计算 / 税负增加 一律不得确认）
    verified = (base_verified and not proxy
                and res.get("calculation_type") == "INCREMENTAL_TAX_BENEFIT"
                and direction == "tax_reduce")
    res["caliber_verified"] = verified
    res["proxy_inputs"] = proxy_inputs(proxy, skill_id=skill_id)

    # 主结果值：优先语义登记的 primary_output，否则回退常用收益键
    results = res.setdefault("results", {})
    keys: list[str] = []
    if sem.get("primary_output"):
        keys.append(sem["primary_output"])
    keys += ["tax_saving", "tax_shield", "tax_effect", "adjustment_effect",
             "equity_transfer_tax", "taxable_effect"]
    val = None
    for k in keys:
        if results.get(k) is not None:
            val = results[k]
            break
    try:
        # 影响金额统一取绝对值（方向由 direction 表达，金额本身恒非负）
        impact_amount = abs(float(val)) if val is not None else None
    except (TypeError, ValueError):
        impact_amount = None

    ctype = res.get("calculation_type")
    if direction == "tax_increase":
        # 方案乙：税负增加仅作风险提示，不进任何"确认/情景"字段
        res["impact_type"] = "NO_CALCULATION"
        res["calculation_status"] = "NO_CALCULATION"
        res["risk_note"] = sem.get("economic_meaning") or "存在纳税调增风险"
        results["confirmed_tax_impact"] = None
        results["scenario_tax_impact"] = None
    elif verified:
        # 唯一可确认路径：增量收益 ∧ 减税方向 ∧ 无代理输入 ∧ 口径已验证
        res["impact_type"] = "CONFIRMED_IMPACT"
        res["calculation_status"] = "CONFIRMED"
        results["confirmed_tax_impact"] = impact_amount
        results["scenario_tax_impact"] = None
    elif ctype in ("INCREMENTAL_TAX_BENEFIT", "SCENARIO_TAX"):
        # 有金额但口径/证据不足 → 只能作情景测算（非结论）
        res["impact_type"] = "SCENARIO_IMPACT"
        res["calculation_status"] = "SCENARIO"
        results["confirmed_tax_impact"] = None
        results["scenario_tax_impact"] = impact_amount
    elif ctype == "TAX_SHIELD":
        # 既有税盾：单列 shield_amount，不混入筹划收益
        res["impact_type"] = "TAX_SHIELD"
        res["calculation_status"] = "NO_CALCULATION"
        results["confirmed_tax_impact"] = None
        results["scenario_tax_impact"] = None
        res["shield_amount"] = impact_amount
    else:
        # NO_CALCULATION：结构/诊断类，不产出税务影响金额
        res["impact_type"] = "NO_CALCULATION"
        res["calculation_status"] = "NO_CALCULATION"
        results["confirmed_tax_impact"] = None
        results["scenario_tax_impact"] = None

    res["impact_amount"] = impact_amount
    res["confirmed_tax_impact"] = results["confirmed_tax_impact"]
    res["scenario_tax_impact"] = results["scenario_tax_impact"]
    return res
