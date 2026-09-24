"""筹划方向识别 Agent（含规则兜底）。

职责：Discovery 阶段做高召回，产出「值得进一步调查」的筹划方向候选。
设计：
- 三个来源取并集：规则（画像阈值）、LLM（模型识别）、诊断（诊断发现），
  同一方向合并 confidence 并记录 discovery_source，交由 Gate 阶段控精度；
- 规则兜底保证无 LLM 时仍可用；所有方向经 `canonical_direction` 归一，避免同义重复。
"""
from __future__ import annotations

import json
import math

from src.agents.prompts import OPPORTUNITY_SYSTEM
from src.common import get_logger
from src.llm.client import get_llm

log = get_logger()


def _num(p: dict, k: str):
    """安全取数值：无法转 float 或为 NaN 时返回 None（用于存在性判断）。"""
    v = p.get(k)
    try:
        f = float(v)
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None


def rule_based_opportunities(profile: dict) -> list[dict]:
    """无 LLM 时的规则兜底：依据画像标签与关键数值。"""
    ops: list[dict] = []

    def add(direction, conf, fields, gaps=(), rationale=""):
        # 统一构造候选结构，evidence_values 仅回填该方向相关字段
        ops.append({
            "direction": direction,
            "confidence": conf,
            "evidence_fields": fields,
            "evidence_values": {f: profile.get(f) for f in fields},
            "data_gaps": list(gaps),
            "rationale": rationale,
        })

    # 研发投入 > 0：存在加计扣除空间；缺失研发人员数时列入数据缺口
    rd = _num(profile, "rd_spend_sum")
    if rd and rd > 0:
        add("研发筹划", 0.8, ["rd_spend_sum", "rd_expense_ratio", "rd_spend_ratio"],
            gaps=[g for g in ("rd_person",) if _num(profile, g) is None],
            rationale=f"研发投入 {rd:.0f} 元，研发费用率 {profile.get('rd_expense_ratio')}，存在加计扣除空间")

    # 高企资格：已确认资质置信度高；仅有"接近门槛"的代理信号时降置信并提示需证书确认
    if _num(profile, "is_hightech") == 1 or (profile.get("label_hightech") == 1):
        add("高新技术企业", 0.7, ["is_hightech", "rd_spend_ratio", "nominal_tax_rate"],
            rationale="企业具备高新技术企业资质，可核查 15% 优惠税率适用情况")
    elif _num(profile, "hightech_signal_proxy") == 1:
        # 资格线索（非资格事实）：名义税率/研发指标接近门槛 → 建议核实资质
        add("高新技术企业", 0.45,
            ["hightech_signal_proxy", "nominal_tax_rate", "rd_expense_ratio", "rd_person_ratio_cross"],
            gaps=["高新技术企业证书及有效期"],
            rationale=(f"高企资格线索：{profile.get('hightech_signal_reason') or '税率/研发指标接近门槛'}"
                       "（仅为线索，需证书证据确认，不得据此认定资格）"))

    # 有息负债率 > 10% 或财务费用率 > 1%：提示资本弱化与利息扣除风险
    fin = _num(profile, "finance_expense_ratio")
    idebt = _num(profile, "interest_debt_ratio")
    if (fin is not None and fin > 0.01) or (idebt is not None and idebt > 0.1):
        add("融资筹划", 0.6, ["finance_expense_ratio", "interest_debt_ratio", "implied_interest_rate"],
            gaps=["关联方借款明细"],
            rationale=f"有息负债率 {idebt}，财务费用率 {fin}，存在利息扣除与资本弱化关注点")

    capex = _num(profile, "cf_capex")
    fa = _num(profile, "bs_fixed_assets")
    if (capex and capex > 0) or (fa and fa > 0):
        add("资产筹划", 0.55, ["bs_fixed_assets", "cf_capex", "fixed_asset_ratio"],
            rationale="存在固定资产及资本支出，可核查加速折旧/一次性扣除政策")

    gov = _num(profile, "gov_subsidy_total")
    if gov and gov > 0:
        add("政府补助", 0.5, ["gov_subsidy_total", "gov_subsidy_ratio"],
            rationale=f"取得政府补助 {gov:.0f} 元，需核查不征税收入与应税收入处理")

    ov = _num(profile, "subsidiary_overseas_count")
    if ov and ov > 0:
        add("跨境筹划", 0.5, ["subsidiary_overseas_count", "overseas_subsidiary_ratio"],
            rationale=f"存在 {ov:.0f} 家海外子公司，涉及跨境税务安排")

    rel = _num(profile, "ma_related_party_count")
    if rel and rel > 0:
        add("关联交易", 0.45, ["ma_related_party_count", "related_party_deal_ratio",
                              "top5_customer_supplier_amount"],
            rationale=f"存在 {rel:.0f} 笔关联交易事件，需关注转让定价")

    imp = _num(profile, "impairment_total")
    if imp and imp > 0:
        add("资产减值", 0.5, ["impairment_total", "is_asset_impairment", "is_credit_impairment"],
            rationale=f"当期减值损失合计 {imp:.0f} 元，一般不得税前扣除，存在纳税调整")

    comp = _num(profile, "employee_compensation_base")
    if comp and comp > 0:
        add("职工薪酬", 0.4, ["employee_compensation_base", "bs_payroll_payable"],
            rationale="存在职工薪酬计提，可核查工资薪金及三项经费扣除")

    # 实际税率与名义税率偏离超 3 个百分点：提示存在税会差异需分析
    etr = _num(profile, "etr")
    nominal = _num(profile, "nominal_tax_rate")
    if etr is not None and nominal is not None and abs(etr - nominal / 100.0) > 0.03:
        add("所得税税会差异", 0.5, ["etr", "nominal_tax_rate", "is_income_tax", "is_total_profit"],
            rationale=f"实际税率 {etr:.2%} 与名义税率 {nominal:.1f}% 存在差异，需分析税会差异")

    sub = _num(profile, "subsidiary_count")
    exited = _num(profile, "d045152435__n_rows")
    if (sub and sub > 0) or (exited and exited > 0):
        add("组织架构", 0.35, ["subsidiary_count", "d045152435__n_rows"],
            rationale="存在子公司或子公司退出，涉及重组税务处理")

    # ---- 新增方向 ----
    if _num(profile, "vat_rate") is not None and (rev := _num(profile, "is_revenue")) and rev > 0:
        add("增值税", 0.5, ["vat_rate", "is_revenue", "cf_tax_paid", "bs_tax_payable"],
            rationale="存在营业收入与增值税名义税率，可核查增值税税负水平与留抵")

    # 期间费用合计 > 0：关注业务招待费/广告费的限额扣除
    adm = _num(profile, "is_admin_expense") or 0
    sell = _num(profile, "is_selling_expense") or 0
    if adm + sell > 0:
        add("费用扣除", 0.45, ["is_admin_expense", "is_selling_expense", "expense_ratio", "is_revenue"],
            rationale="存在期间费用，需关注业务招待费/广告宣传费限额扣除")

    if (_num(profile, "fixed_asset_ratio") or 0) > 0.1 or (_num(profile, "bs_fixed_assets") or 0) > 0:
        add("折旧摊销", 0.45, ["bs_fixed_assets", "bs_intangible_assets", "csmar__NonDebtTaxShield"],
            rationale="固定资产/无形资产规模较大，存在折旧摊销税会差异")

    # 累计未分配利润为负：可能存在可结转弥补的亏损
    if (_num(profile, "bs_retained_earnings") or 0) < 0:
        add("亏损弥补", 0.5, ["bs_retained_earnings", "is_total_profit"],
            rationale="累计未分配利润为负，可能存在可弥补亏损")

    if (_num(profile, "is_investment_income") or 0) > 0:
        add("投资收益", 0.45, ["is_investment_income", "d205404528__Fn01804"],
            rationale="存在投资收益，需区分股息红利与股权转让所得")

    if (_num(profile, "cf_tax_paid") or 0) > 0:
        add("税负现金流", 0.4, ["cf_tax_paid", "bs_tax_payable", "is_income_tax"],
            rationale="存在税费现金支出，可分析实缴与计提差异")

    # 应收账款占收入比 > 20%：关注坏账准备与信用减值
    ar = _num(profile, "bs_accounts_receivable"); rev2 = _num(profile, "is_revenue")
    if ar and ar > 0 and (rev2 is None or ar / rev2 > 0.2):
        add("应收账款", 0.4, ["bs_accounts_receivable", "is_credit_impairment", "is_revenue"],
            rationale="应收账款占收入比较高，需关注坏账与信用减值")

    if (_num(profile, "is_tax_surcharge") or 0) > 0:
        add("小税种", 0.35, ["is_tax_surcharge", "vat_rate"],
            rationale="存在税金及附加，可核查房产税/土地增值税/印花税等")

    if _num(profile, "largest_holder_rate") is not None:
        add("股权结构", 0.35, ["equity_nature", "largest_holder_rate", "top_ten_holders_rate", "separation"],
            rationale="存在股权结构信息，可分析控股股东与两权分离")

    if _num(profile, "gov_subsidy_income_related") is not None:
        add("政府补助不征税", 0.45, ["gov_subsidy_total", "gov_subsidy_income_related"],
            rationale="存在与收益相关的政府补助，需判定是否可作为不征税收入")

    if (_num(profile, "bs_intangible_assets") or 0) > 0:
        add("无形资产摊销", 0.4, ["bs_intangible_assets", "d101955005__Fn02207", "d101955005__Fn02204"],
            rationale="存在无形资产，需关注摊销年限税会差异（税法最低10年）")

    if (_num(profile, "expense_share_based") or 0) > 0:
        add("股份支付", 0.45, ["expense_share_based", "is_admin_expense"],
            rationale="存在股份支付费用，等待期不得扣除、行权时可扣除")

    if ((_num(profile, "d205618410__EndingBalance") or 0) > 0
            or (_num(profile, "bs_lease_liability") or 0) > 0):
        add("租赁", 0.4, ["d205618410__EndingBalance", "d210441016__EndingBalance", "bs_lease_liability"],
            rationale="存在使用权资产/租赁负债，涉及经营租赁税会差异")

    return ops


# 方向同义词归一（Discovery 高召回，Gate 控精度）
DIRECTION_ALIASES = {
    "研发费用加计扣除": "研发筹划", "研发加计": "研发筹划", "研发费用": "研发筹划",
    "高新技术企业": "高新技术企业", "高企": "高新技术企业",
    "资本弱化": "融资筹划", "利息扣除": "融资筹划", "利息支出": "融资筹划",
    "固定资产加速折旧": "资产筹划", "一次性扣除": "资产筹划", "加速折旧": "资产筹划",
    "转让定价": "关联交易",
    "税会差异": "所得税税会差异",
    "亏损结转": "亏损弥补", "弥补亏损": "亏损弥补",
    "坏账": "应收账款",
    "股权转让": "投资收益",
    "增值税留抵": "增值税",
    "职工福利费": "职工薪酬",
    "使用权资产": "租赁",
}


def _load_known_directions() -> set[str]:
    """从 Skill 引擎加载全部已注册方向名，用于同义归一的目标集合。"""
    try:
        from src.skills.engine import get_skills
        return {s.direction for s in get_skills().values()}
    except Exception:  # noqa: BLE001
        return set()


def canonical_direction(direction: str, known: set[str] | None = None) -> str:
    """把方向名归一为 Skill 已注册的标准方向。

    优先级：精确命中已知方向 > 命中同义词表 > 与已知方向互为子串 > 原样返回。
    """
    d = str(direction or "").strip()
    if not d:
        return ""
    known = known or _load_known_directions()
    if d in known:
        return d
    for k, v in DIRECTION_ALIASES.items():
        # 同义词按子串匹配，兼容"研发费用加计扣除政策"等带修饰的表述
        if k in d:
            return v
    for k in known:
        if k and (k in d or d in k):
            return k
    return d


def _llm_opportunities(profile: dict) -> list[dict]:
    """调用 LLM 识别方向；仅取画像节选以控制 token，失败返回空列表。"""
    llm = get_llm()
    subset = {k: profile.get(k) for k in [
        "stock_code", "year", "short_name", "industry_name", "equity_nature",
        "is_revenue", "is_net_profit", "rd_spend_sum", "rd_expense_ratio", "rd_spend_ratio",
        "rd_person", "finance_expense_ratio", "interest_debt_ratio", "asset_liability_ratio",
        "bs_fixed_assets", "cf_capex", "fixed_asset_ratio", "gov_subsidy_total",
        "subsidiary_overseas_count", "ma_related_party_count", "nominal_tax_rate",
        "etr", "is_hightech", "size_group",
    ]}
    user = "企业画像(节选)：\n" + json.dumps(subset, ensure_ascii=False, default=str)
    data = llm.try_chat_json([
        {"role": "system", "content": OPPORTUNITY_SYSTEM},
        {"role": "user", "content": user},
    ])
    # 解析失败或模型未给出 opportunities 时静默返回空，由规则/诊断来源兜底
    if data and isinstance(data, dict) and data.get("opportunities"):
        return data["opportunities"]
    return []


# 诊断严重度 → 置信度映射：关注>观察>提示
_SEV_CONF = {"关注": 0.8, "观察": 0.6, "提示": 0.5}


def _diagnostic_opportunities(diagnostics: list[dict] | None) -> list[dict]:
    """把诊断发现转换为方向候选（诊断本身即异常线索，默认参与发现）。"""
    out = []
    for d in diagnostics or []:
        out.append({
            "direction": d.get("direction", ""),
            "confidence": _SEV_CONF.get(d.get("severity"), 0.5),
            "evidence_fields": d.get("fields", []),
            "evidence_values": {},
            "data_gaps": d.get("evidence_needed", []),
            "rationale": d.get("statement", ""),
        })
    return out


def identify(profile: dict, use_llm: bool = True,
             diagnostics: list[dict] | None = None) -> tuple[list[dict], str]:
    """Discovery 并集：Rule ∪ LLM ∪ Diagnostic（高召回），去重归一后交给 Gate（控精度）。"""
    known = _load_known_directions()
    rule = rule_based_opportunities(profile)
    llm_items = _llm_opportunities(profile) if use_llm else []
    diag_items = _diagnostic_opportunities(diagnostics)

    merged: dict[str, dict] = {}
    for src, items in (("rule", rule), ("llm", llm_items), ("diagnostic", diag_items)):
        for o in items:
            cd = canonical_direction(o.get("direction", ""), known)
            if not cd:
                continue
            if cd in merged:
                # 同方向多来源命中：累加来源标签并取最大置信度（任一来源强即保留）
                merged[cd]["discovery_source"] = merged[cd]["discovery_source"] + f"+{src}"
                merged[cd]["confidence"] = max(float(merged[cd].get("confidence") or 0),
                                               float(o.get("confidence") or 0))
            else:
                merged[cd] = {**o, "direction": cd, "discovery_source": src,
                              "confidence": float(o.get("confidence") or 0.4)}
    return list(merged.values()), "union"
