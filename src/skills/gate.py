"""Discovery + Evidence Gate：三级触发 + 证据门槛。

三级触发（对应"事实存在 → 异常信号 → 政策匹配"）：
    Level 0  事实存在：科目/事件存在（候选池，仅计数）
    Level 1  异常/相对信号：行业分位≥P75、偏离行业中位数、趋势恶化、比值异常（值得调查）
    Level 2  政策匹配：L1 + 资格条件（部分/全部）满足且缺口可定位（进入深度分析）

Evidence Gate 只作用于 Level 2：
    PASS  : 关键数据齐全
    WEAK  : 有信号但缺关键数据
    NO_BASIS: 连"事实存在"都不满足 -> 排除

关键设计：
    - 信号分 existence / anomaly 两级，anomaly 再分核心(core)/辅助；只有核心异常
      达到 min_core_anomalies 才允许升级，避免仅凭行业分位这类弱信号过度触发；
    - 行业覆盖：profile.industry_name 命中 industry_overrides 时替换阈值；
    - 诊断引擎结果作为额外 L1 异常信号注入（提示级不注入）。
不变量：
    - level=-1 表示排除，0/1/2 分别对应三级；status 仅在 L2 有意义；
    - 信号判定只读 profile/history，不产生副作用，保证可复现。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

import yaml

from src.common import ROOT, get_logger

log = get_logger()

THRESHOLD_PATH = ROOT / "config" / "thresholds.yaml"

LEVEL_NAMES = {0: "L0 事实存在", 1: "L1 异常信号", 2: "L2 政策匹配", -1: "排除"}


def load_thresholds() -> dict:
    with open(THRESHOLD_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


@dataclass
class GateResult:
    """单方向 Gate 判定结果：级别 + 证据门槛状态 + 命中信号与理由。

    level：-1 排除 / 0 存在 / 1 异常 / 2 政策匹配；
    status：NO_BASIS / CANDIDATE / WATCH / WEAK / PASS（仅 L2 的 WEAK/PASS 有证据门槛含义）。
    """
    direction: str
    level: int = -1                 # -1 排除 / 0 存在 / 1 异常 / 2 政策匹配
    status: str = "NO_BASIS"        # PASS / WEAK / NO_BASIS（仅 L2 有意义）
    signals: list[dict] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    existence_count: int = 0
    anomaly_count: int = 0
    core_anomaly_count: int = 0
    policy_match: bool = False
    industry: str = ""

    @property
    def level_name(self) -> str:
        """级别中文名（L0/L1/L2/排除），未知级别回退「未知」。"""
        return LEVEL_NAMES.get(self.level, "未知")

    def to_dict(self) -> dict:
        """转为普通 dict，并附加 level_name 便于报告/UI 直接展示。"""
        d = asdict(self)
        d["level_name"] = self.level_name
        return d


def _num(profile: dict, key: str):
    """安全取数：转 float，NaN/缺失/不可转一律返回 None（用 f != f 判 NaN）。"""
    v = profile.get(key)
    try:
        f = float(v)
        return None if f != f else f
    except (TypeError, ValueError):
        return None


def _ratio(a, b):
    """安全比值：任一转 float 失败或分母为 0 时返回 None，避免除零异常。"""
    try:
        a = float(a); b = float(b)
        return None if b == 0 else a / b
    except (TypeError, ValueError):
        return None


def _hist_series(history: dict, field: str) -> list[float]:
    """按年份升序取历史序列；缺失年份填 NaN，保证与年份位置对齐。"""
    if not history:
        return []
    out = []
    for y in sorted(history.keys()):
        try:
            out.append(float(history[y].get(field)))
        except (TypeError, ValueError):
            out.append(float("nan"))   # 缺失年份补 NaN，保持与年份位置对齐
    return out


def _sig(name, passed, value, threshold, desc="", level="anomaly", core=True):
    """构造一条信号：level=existence（事实存在）或 anomaly（异常/相对信号）。

    core=True 表示「核心异常」，只有核心异常达到 min_core 才允许升级到 L2；
    辅助信号（core=False，如行业分位）只增强证据、单独不足以触发深度分析。
    """
    return {"name": name, "passed": bool(passed), "value": value,
            "threshold": threshold, "desc": desc, "level": level, "core": bool(core)}


def _pct(profile, field):
    """取该字段的行业分位（profile 中预计算的 <field>_industry_pct）。"""
    return _num(profile, f"{field}_industry_pct")


def _median(profile, field):
    """取该字段的行业中位数（profile 中预计算的 <field>_industry_median）。"""
    return _num(profile, f"{field}_industry_median")


# ------------------------------------------------------------------ 信号

def _sig_rd(p, h, th):
    """研发筹划信号：事实存在（有研发投入）+ 规模/趋势/口径风险异常。

    核心异常：投入规模、3年复合增长率；辅助：行业分位（core=False）。
    资本化率偏高作为「口径风险」信号，提示费用化/资本化划分可能影响加计口径。
    """
    s = th["signals"]
    # 事实存在项：研发投入 > 0 即计存在（决定该方向是否被直接排除）
    out = [_sig("存在研发投入", (_num(p, "rd_spend_sum") or 0) > 0, _num(p, "rd_spend_sum"), 0,
                "事实存在", level="existence")]
    r = _num(p, "rd_spend_sum")
    if r is not None:
        out.append(_sig("研发投入规模≥%.1f亿元" % (s["rd_scale_min"] / 1e8),
                        r >= s["rd_scale_min"], round(r, 2), s["rd_scale_min"], "绝对规模"))
    pct = _pct(p, "rd_expense_ratio")
    if pct is not None:
        out.append(_sig("研发费用率行业分位≥P75", pct >= s["p75"], round(pct, 3), s["p75"],
                        "相对行业（辅助）", core=False))
    cagr = _num(p, "rd_spend_cagr_3y")
    if cagr is not None:
        out.append(_sig("研发投入3年复合增长率≥%.0f%%" % (s["rd_cagr_3y"] * 100),
                        cagr >= s["rd_cagr_3y"], round(cagr, 4), s["rd_cagr_3y"], "趋势"))
    cap = _num(p, "rd_capitalize_rate")
    if cap is not None:
        out.append(_sig("研发资本化率≥%.0f%%" % (s["rd_capitalize_rate"] * 100),
                        cap >= s["rd_capitalize_rate"], round(cap, 4), s["rd_capitalize_rate"], "口径风险"))
    return out


def _sig_finance(p, h, th):
    """融资筹划信号：事实存在（有息/财务费用）+ 有息负债率异常；行业分位为辅助。"""
    s = th["signals"]
    out = [_sig("存在利息/财务费用", (_num(p, "interest_expense_best") or 0) > 0,
                _num(p, "interest_expense_best"), 0, "事实存在", level="existence")]
    idr = _num(p, "interest_debt_ratio")
    if idr is not None:
        out.append(_sig("有息负债率≥%.0f%%" % (s["interest_debt_ratio"] * 100),
                        idr >= s["interest_debt_ratio"], round(idr, 4), s["interest_debt_ratio"], "负债水平"))
    pct = _pct(p, "finance_expense_ratio")
    if pct is not None:
        out.append(_sig("财务费用率行业分位≥P75", pct >= s["p75"], round(pct, 3), s["p75"],
                        "相对行业（辅助）", core=False))
    return out


def _sig_asset(p, h, th):
    """资产筹划信号：事实存在（有固定资产）+ 固定资产占比/资本开支强度异常。"""
    s = th["signals"]
    out = [_sig("存在固定资产", (_num(p, "bs_fixed_assets") or 0) > 0, _num(p, "bs_fixed_assets"), 0,
                "事实存在", level="existence")]
    fa = _num(p, "fixed_asset_ratio")
    if fa is not None:
        out.append(_sig("固定资产占比≥%.0f%%" % (s["fixed_asset_ratio"] * 100),
                        fa >= s["fixed_asset_ratio"], round(fa, 4), s["fixed_asset_ratio"], "资产结构"))
    r = _ratio(_num(p, "cf_capex"), _num(p, "is_revenue"))
    if r is not None:
        out.append(_sig("资本支出/营业收入≥%.0f%%" % (s["capex_to_revenue"] * 100),
                        r >= s["capex_to_revenue"], round(r, 4), s["capex_to_revenue"], "资本开支强度"))
    return out


def _sig_gov(p, h, th):
    """政府补助信号：事实存在（有补助）+ 补助/收入占比异常。"""
    s = th["signals"]
    out = [_sig("存在政府补助", (_num(p, "gov_subsidy_total") or 0) > 0,
                _num(p, "gov_subsidy_total"), 0, "事实存在", level="existence")]
    r = _ratio(_num(p, "gov_subsidy_total"), _num(p, "is_revenue"))
    if r is not None:
        out.append(_sig("政府补助/营业收入≥%.1f%%" % (s["gov_subsidy_to_revenue"] * 100),
                        r >= s["gov_subsidy_to_revenue"], round(r, 4), s["gov_subsidy_to_revenue"], "补助规模"))
    return out


def _sig_gov_non_tax(p, h, th):
    """政府补助不征税信号：以「与收益相关补助」是否存在为事实存在判据。"""
    s = th["signals"]
    out = [_sig("存在与收益相关补助", _num(p, "gov_subsidy_income_related") is not None,
                _num(p, "gov_subsidy_income_related"), 0, "事实存在", level="existence")]
    r = _ratio(_num(p, "gov_subsidy_income_related"), _num(p, "is_revenue"))
    if r is not None:
        out.append(_sig("与收益相关补助/营业收入≥%.1f%%" % (s["gov_subsidy_to_revenue"] * 100),
                        r >= s["gov_subsidy_to_revenue"], round(r, 4), s["gov_subsidy_to_revenue"], "补助规模"))
    return out


def _sig_hightech(p, h, th):
    """高新技术企业信号：研发活动事实存在 + 高新资质（资格项）+ 研发占比达标。"""
    s = th["signals"]
    out = [_sig("存在研发活动", (_num(p, "rd_spend_sum") or 0) > 0, _num(p, "rd_spend_sum"), 0,
                "事实存在", level="existence")]
    if _num(p, "is_hightech") == 1:
        out.append(_sig("具备高新技术企业资质", True, 1, 1, "资格"))
    r = _num(p, "rd_spend_ratio")
    if r is not None:
        out.append(_sig("研发投入占收入≥%.0f%%（认定门槛）" % (s["hightech_rd_ratio"] * 100),
                        r >= s["hightech_rd_ratio"], round(r, 4), s["hightech_rd_ratio"], "政策门槛"))
    return out


def _sig_cross_border(p, h, th):
    """跨境筹划信号：存在海外子公司 + 海外子公司占比异常。"""
    s = th["signals"]
    ov = _num(p, "subsidiary_overseas_count")
    out = [_sig("存在海外子公司", bool(ov and ov > 0), ov, 1, "事实存在", level="existence")]
    if ov and ov > 0:
        ratio = _num(p, "overseas_subsidiary_ratio")
        out.append(_sig("海外子公司占比≥%.0f%%" % (s["overseas_ratio"] * 100),
                        (ratio or 0) >= s["overseas_ratio"],
                        round(ratio, 4) if ratio is not None else None, s["overseas_ratio"], "跨境比重"))
    return out


def _sig_tax_diff(p, h, th):
    """所得税税会差异信号：存在所得税费用 + |ETR - 名义税率| 超过阈值。

    注意 ETR 为小数、名义税率为百分数，需先 /100 再比较，否则量纲不一致会误报。
    """
    s = th["signals"]
    out = [_sig("存在所得税费用", (_num(p, "is_income_tax") or 0) > 0, _num(p, "is_income_tax"), 0,
                "事实存在", level="existence")]
    etr = _num(p, "etr"); nom = _num(p, "nominal_tax_rate")
    if etr is not None and nom is not None:
        gap = abs(etr - nom / 100.0)
        out.append(_sig("|实际税率-名义税率|≥%.0f%%" % (s["etr_gap"] * 100),
                        gap >= s["etr_gap"], round(gap, 4), s["etr_gap"], "税会差异"))
    return out


def _sig_impairment(p, h, th):
    """资产减值信号：存在减值损失 + 减值/|利润总额| 占比异常（用绝对值避免亏损企业符号干扰）。"""
    s = th["signals"]
    imp = _num(p, "impairment_total")
    out = [_sig("存在减值损失", bool(imp and imp > 0), imp, 0, "事实存在", level="existence")]
    prof = _num(p, "is_total_profit")
    r = _ratio(imp, abs(prof)) if (imp and prof) else None
    if r is not None:
        out.append(_sig("减值损失/|利润总额|≥%.0f%%" % (s["impairment_to_profit"] * 100),
                        r >= s["impairment_to_profit"], round(r, 4), s["impairment_to_profit"], "减值规模"))
    return out


def _sig_payroll(p, h, th):
    """职工薪酬信号：存在职工薪酬 + 薪酬/收入占比异常（人力密集度）。"""
    s = th["signals"]
    base = _num(p, "employee_compensation_base")
    out = [_sig("存在职工薪酬", bool(base and base > 0), base, 0, "事实存在", level="existence")]
    r = _ratio(base, _num(p, "is_revenue"))
    if r is not None:
        out.append(_sig("职工薪酬/营业收入≥%.0f%%" % (s["payroll_to_revenue"] * 100),
                        r >= s["payroll_to_revenue"], round(r, 4), s["payroll_to_revenue"], "人力密集度"))
    return out


def _sig_vat(p, h, th):
    """增值税：存在营业收入只能算 L0；必须命中异常信号才算值得调查。"""
    s = th["signals"]
    out = [_sig("存在增值税业务（营业收入>0）", (_num(p, "is_revenue") or 0) > 0,
                _num(p, "is_revenue"), 0, "事实存在", level="existence")]
    cash = _ratio(_num(p, "cf_tax_paid"), _num(p, "is_revenue"))
    if cash is not None:
        lo, hi = s["vat_cash_ratio_low"], s["vat_cash_ratio_high"]
        abnormal = cash <= lo or cash >= hi
        out.append(_sig("税费现金比显著异常(≤%.1f%% 或 ≥%.0f%%)" % (lo * 100, hi * 100),
                        abnormal, round(cash, 4), f"{lo*100:.1f}%~{hi*100:.0f}%", "税负异常"))
    return out


def _sig_expense(p, h, th):
    """费用扣除信号：存在期间费用 + 业务招待费/广告宣传费占收入比超限风险。"""
    s = th["signals"]
    out = [_sig("存在期间费用", ((_num(p, "is_admin_expense") or 0) + (_num(p, "is_selling_expense") or 0)) > 0,
                None, 0, "事实存在", level="existence")]
    rev = _num(p, "is_revenue")
    ent = _ratio(_num(p, "expense_entertainment"), rev)
    adv = _ratio(_num(p, "expense_advertising"), rev)
    if ent is not None:
        out.append(_sig("业务招待费/营业收入≥%.1f%%" % (s["entertainment_to_revenue"] * 100),
                        ent >= s["entertainment_to_revenue"], round(ent, 4),
                        s["entertainment_to_revenue"], "限额风险"))
    if adv is not None:
        out.append(_sig("广告宣传费/营业收入≥%.0f%%" % (s["advertising_to_revenue"] * 100),
                        adv >= s["advertising_to_revenue"], round(adv, 4),
                        s["advertising_to_revenue"], "限额风险"))
    return out


def _sig_depreciation(p, h, th):
    """折旧摊销信号：复用资产筹划信号（折旧与资产结构同源）。"""
    return _sig_asset(p, h, th)


def _sig_loss(p, h, th):
    """亏损弥补信号：存在未分配利润 + 未分配利润为负（可能含可弥补亏损）。"""
    r = _num(p, "bs_retained_earnings")
    out = [_sig("存在未分配利润", r is not None, r, None, "事实存在", level="existence")]
    if r is not None:
        out.append(_sig("未分配利润为负（可能含可弥补亏损）", r < 0, round(r, 2), 0, "累计亏损"))
    return out


def _sig_investment(p, h, th):
    """投资收益信号：存在投资收益 + 投资收益/|利润总额| 占比异常（利润结构）。"""
    s = th["signals"]
    inv = _num(p, "is_investment_income")
    out = [_sig("存在投资收益", bool(inv and inv > 0), inv, 0, "事实存在", level="existence")]
    r = _ratio(inv, abs(_num(p, "is_total_profit") or 0) or None)
    if r is not None:
        out.append(_sig("投资收益/|利润总额|≥%.0f%%" % (s["investment_income_to_profit"] * 100),
                        abs(r) >= s["investment_income_to_profit"], round(r, 4),
                        s["investment_income_to_profit"], "利润结构"))
    return out


def _sig_tax_cash(p, h, th):
    """税负现金流信号：存在税费现金支出 + 实缴/计提比异常（过高或过低均提示）。"""
    s = th["signals"]
    paid = _num(p, "cf_tax_paid")
    out = [_sig("存在税费现金支出", bool(paid and paid > 0), paid, 0, "事实存在", level="existence")]
    r = _ratio(paid, (_num(p, "is_income_tax") or 0) + (_num(p, "is_tax_surcharge") or 0) or None)
    if r is not None:
        abnormal = r < s["tax_cash_ratio_low"] or r > s["tax_cash_ratio_high"]
        out.append(_sig("实缴/计提比异常(<%.1f 或 >%.1f)" % (s["tax_cash_ratio_low"], s["tax_cash_ratio_high"]),
                        abnormal, round(r, 4), f"{s['tax_cash_ratio_low']}~{s['tax_cash_ratio_high']}", "现金流异常"))
    return out


def _sig_receivable(p, h, th):
    """应收账款信号：存在应收账款 + 占收入比偏高 + 近3年占比上升趋势。

    趋势判定要求至少 2 个可比年度（zip 同时过滤 NaN），避免样本不足时误报。
    """
    s = th["signals"]
    ar = _num(p, "bs_accounts_receivable")
    out = [_sig("存在应收账款", bool(ar and ar > 0), ar, 0, "事实存在", level="existence")]
    r = _ratio(ar, _num(p, "is_revenue"))
    if r is not None:
        out.append(_sig("应收账款/营业收入≥%.0f%%" % (s["ar_to_revenue"] * 100),
                        r >= s["ar_to_revenue"], round(r, 4), s["ar_to_revenue"], "占比"))
    ars = _hist_series(h, "bs_accounts_receivable")
    revs = _hist_series(h, "is_revenue")
    ratios = [a / b for a, b in zip(ars, revs) if b and b == b and a == a]
    if len(ratios) >= 2:
        rises = sum(1 for i in range(1, len(ratios)) if ratios[i] > ratios[i - 1])
        out.append(_sig("近3年应收占收入比上升≥%d年" % s["ar_rise_years"],
                        rises >= s["ar_rise_years"], rises, s["ar_rise_years"], "趋势恶化"))
    return out


def _sig_small_taxes(p, h, th):
    """小税种：无房产/土地等明细，仅事实存在（L0 候选），不主动升级为异常。"""
    return [_sig("存在税金及附加", (_num(p, "is_tax_surcharge") or 0) > 0,
                 _num(p, "is_tax_surcharge"), 0, "事实存在", level="existence")]


def _sig_equity(p, h, th):
    """股权结构信号：存在股权结构信息 + 两权分离度（治理结构）异常。"""
    s = th["signals"]
    sep = _num(p, "separation")
    out = [_sig("存在股权结构信息", _num(p, "largest_holder_rate") is not None, sep, None,
                "事实存在", level="existence")]
    if sep is not None:
        out.append(_sig("两权分离度≥%.0f%%" % (s["equity_separation"] * 100),
                        sep >= s["equity_separation"], round(sep, 4), s["equity_separation"], "治理结构"))
    return out


def _sig_intangible(p, h, th):
    """无形资产摊销信号：存在无形资产 + 无形资产/总资产占比异常。"""
    s = th["signals"]
    ia = _num(p, "bs_intangible_assets")
    out = [_sig("存在无形资产", bool(ia and ia > 0), ia, 0, "事实存在", level="existence")]
    r = _ratio(ia, _num(p, "bs_total_assets"))
    if r is not None:
        out.append(_sig("无形资产/总资产≥%.0f%%" % (s["intangible_to_assets"] * 100),
                        r >= s["intangible_to_assets"], round(r, 4), s["intangible_to_assets"], "资产结构"))
    return out


def _sig_share_based(p, h, th):
    """股份支付信号：仅事实存在（有股份支付费用），无行业基准可比。"""
    v = _num(p, "expense_share_based")
    return [_sig("存在股份支付费用", bool(v and v > 0), v, 0, "事实存在", level="existence")]


def _sig_lease(p, h, th):
    """租赁信号：存在使用权资产/租赁负债 + 使用权资产/总资产占比异常。"""
    s = th["signals"]
    rou = _num(p, "d205618410__EndingBalance")
    out = [_sig("存在使用权资产/租赁负债",
                bool((rou and rou > 0) or (_num(p, "bs_lease_liability") or 0) > 0),
                rou, 0, "事实存在", level="existence")]
    r = _ratio(rou, _num(p, "bs_total_assets"))
    if r is not None:
        out.append(_sig("使用权资产/总资产≥%.0f%%" % (s["rou_to_assets"] * 100),
                        r >= s["rou_to_assets"], round(r, 4), s["rou_to_assets"], "租赁规模"))
    return out


def _sig_related_party(p, h, th):
    """关联交易信号：存在关联交易事件 + 关联交易占比/前五大集中度异常。"""
    s = th["signals"]
    ma = _num(p, "ma_related_party_count")
    out = [_sig("存在关联交易事件", bool(ma and ma > 0), ma, 0, "事实存在", level="existence")]
    r = _num(p, "related_party_deal_ratio")
    if r is not None:
        out.append(_sig("关联交易事件占比≥%.0f%%" % (s["related_party_ratio"] * 100),
                        r >= s["related_party_ratio"], round(r, 4), s["related_party_ratio"], "关联交易"))
    tr = _ratio(_num(p, "top5_customer_supplier_amount"), _num(p, "is_revenue"))
    if tr is not None:
        out.append(_sig("前五大客户/供应商金额/收入≥%.0f%%" % (s["top5_concentration"] * 100),
                        tr >= s["top5_concentration"], round(tr, 4), s["top5_concentration"], "集中度"))
    return out


def _sig_org(p, h, th):
    """组织架构信号：当期有子公司退出（组织变动事件，仅事实存在）。"""
    exited = _num(p, "d045152435__n_rows")
    return [_sig("当期有子公司退出", bool(exited and exited > 0), exited, 1, "组织变动事件",
                 level="existence")]


SIGNAL_RULES = {
    # 方向名 -> 信号函数；未登记方向无信号（existence_count=0 → 直接排除）
    "研发筹划": _sig_rd, "融资筹划": _sig_finance, "资产筹划": _sig_asset,
    "政府补助": _sig_gov, "政府补助不征税": _sig_gov_non_tax, "高新技术企业": _sig_hightech,
    "跨境筹划": _sig_cross_border, "所得税税会差异": _sig_tax_diff, "资产减值": _sig_impairment,
    "职工薪酬": _sig_payroll, "增值税": _sig_vat, "费用扣除": _sig_expense,
    "折旧摊销": _sig_depreciation, "亏损弥补": _sig_loss, "投资收益": _sig_investment,
    "税负现金流": _sig_tax_cash, "应收账款": _sig_receivable, "小税种": _sig_small_taxes,
    "股权结构": _sig_equity, "无形资产摊销": _sig_intangible, "股份支付": _sig_share_based,
    "租赁": _sig_lease, "关联交易": _sig_related_party, "组织架构": _sig_org,
}


def _policy_match(skill_result: dict | None) -> bool:
    """政策匹配：资格条件无失败、且至少有通过项（缺口可定位由 evidence_required 体现）。

    口径：只要有一条资格条件不满足即视为不匹配；全部为 unknown（无通过项）也不算匹配，
    避免在资格完全未核实的情况下贸然进入 L2。
    """
    if not skill_result:
        return False
    failed = skill_result.get("eligibility_failed") or []
    passed = skill_result.get("conditions_passed") or []
    return (not failed) and bool(passed)


def evaluate_gate(direction: str, profile: dict, history: dict,
                  skill_result: dict | None, thresholds: dict,
                  diagnostics: list[dict] | None = None) -> GateResult:
    """对单个方向执行三级触发 + Evidence Gate 判定。

    参数：
        direction    筹划方向（映射到 SIGNAL_RULES 中的信号函数）；
        profile      企业画像；history 历史序列；
        skill_result Skill 评估结果（提供资格条件通过/缺口，用于政策匹配）；
        thresholds   阈值配置；diagnostics 诊断引擎结果（作为 L1 异常信号）。
    返回：GateResult（level/status/signals/reasons/计数）。
    关键口径：
        - 行业覆盖：命中行业名时用 industry_overrides 覆盖信号阈值；
        - 诊断信号仅「观察/关注」且 gate!=false 才计入，提示级不触发；
        - 只有存在核心异常且资格匹配才升到 L2；缺关键数据则 L2 为 WEAK。
    """
    # 行业校准：命中行业时覆盖信号阈值
    # resolved 是本次判定的最终阈值（基础 signals + 行业 overrides），不修改入参 thresholds
    resolved = {"signals": dict(thresholds.get("signals", {})),
                "gate": thresholds.get("gate", {})}
    industry = str(profile.get("industry_name") or "")
    # 行业名采用「包含」匹配（行业名可能带后缀/细分），首个命中即生效
    for ind, ov in (thresholds.get("industry_overrides", {}) or {}).items():
        if ind and ind in industry:
            resolved["signals"].update(ov)
            break

    res = GateResult(direction=direction)
    res.industry = industry
    fn = SIGNAL_RULES.get(direction)
    # 未登记方向无信号函数 → 无存在信号 → 会在下方被判为排除
    signals = fn(profile, history, resolved) if fn else []
    # 诊断引擎结果作为 L1 异常信号（仅 观察/关注 且 gate!=false 计入；提示 级不触发）
    for d in (diagnostics or []):
        if (d.get("direction") == direction and d.get("severity") in ("观察", "关注")
                and d.get("gate", True)):
            signals.append(_sig(f"观察信号：{d.get('name')}", True, d.get("deviation"),
                                None, d.get("statement") or d.get("note", ""), level="anomaly"))
    res.signals = signals
    # 三个计数是分级判定的依据：存在数决定是否排除，异常数决定是否升级，核心异常数决定能否到 L2
    res.existence_count = sum(1 for s in signals if s["level"] == "existence" and s["passed"])
    res.anomaly_count = sum(1 for s in signals if s["level"] == "anomaly" and s["passed"])
    res.core_anomaly_count = sum(1 for s in signals
                                 if s["level"] == "anomaly" and s["passed"] and s.get("core", True))
    res.policy_match = _policy_match(skill_result)
    # min_core 来自 gate 配置：允许行业/全局调整「核心异常」门槛
    min_core = int(resolved.get("gate", {}).get("min_core_anomalies", 1))

    if res.existence_count == 0:
        # 连「科目/事件存在」都不满足 → 排除（NO_BASIS），不进入任何后续分析
        res.level = -1
        res.status = "NO_BASIS"
        res.reasons = ["不满足事实存在（Level 0）"]
        return res
    if res.anomaly_count == 0:
        # 仅事实存在、无任何异常信号 → 候选池（L0），值得记录但不主动调查
        res.level = 0
        res.status = "CANDIDATE"
        res.reasons = ["仅事实存在（Level 0），未命中异常信号"]
        return res
    if res.core_anomaly_count < min_core:
        # 只命中辅助信号（如行业分位）而核心异常不足 → 降为待观察（L1）
        res.level = 1
        res.status = "WATCH"
        res.reasons = [f"仅命中辅助异常信号（核心异常 {res.core_anomaly_count}/{min_core}），"
                       f"关键异常未成立，降为待观察"]
        return res
    if not res.policy_match:
        # 有异常但资格条件未匹配 → 待观察（L1），避免对不适用政策的方向做深度分析
        res.level = 1
        res.status = "WATCH"
        res.reasons = ["命中异常信号（Level 1），但资格条件尚未匹配"]
        return res

    res.level = 2
    gaps = (skill_result or {}).get("data_gaps", [])
    unknown = (skill_result or {}).get("conditions_unknown", [])
    if gaps or unknown:
        # L2 政策匹配但缺关键数据/条件无法判断 → WEAK（有信号但证据不足）
        res.status = "WEAK"
        res.reasons = [f"政策匹配但缺关键数据：{', '.join(gaps) if gaps else ''}"
                       f"{'；条件无法判断：' + ', '.join(c.get('field', '') for c in unknown) if unknown else ''}"]
    else:
        # 政策匹配且关键数据齐全 → PASS（进入深度分析并可能支撑 CONFIRMED）
        res.status = "PASS"
        res.reasons = [f"命中 {res.anomaly_count} 条异常信号且关键数据齐全"]
    return res
