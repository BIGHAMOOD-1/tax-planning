"""诊断引擎：跨字段一致性 / 基准偏离 / 时点差异 / 趋势突变（确定性、可复现）。

定位：诊断只负责**发现"哪里值得查"**（observation / 信号），不宣布结论。
    statement 为中性事实陈述；严重度按类型分三档（提示 / 观察 / 关注）。
结论链条：诊断(观察) -> Red 解释成因 -> Blue 验证 -> Evidence -> 税务结论。

四类诊断（category）：
    reconcile 勾稽类：同一口径的不同来源应相互印证（如当期+递延 vs 利润表所得税）；
    estimate  推算类：以基准/行业值推算应有规模，偏离即为信号；
    timing    确认类：跨期/时点差异（如政府补助确认、应交税费变动）；
    trend     趋势类：同比突变（ETR、研发费用率、毛利率）。
不变量：
    - 所有偏差均做分母/空值保护，缺失不制造 100% 假偏差；
    - 严重度阈值逐项优先、类别兜底，数据质量类可封顶严重度。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path

import yaml

from src.common import ROOT, get_logger

log = get_logger()
THRESHOLD_PATH = ROOT / "config" / "thresholds.yaml"

CATEGORY_CN = {"reconcile": "勾稽类", "estimate": "推算类",
               "timing": "确认类", "trend": "趋势类"}
# 证据强度：勾稽类两来源互证最强；推算/确认/趋势依赖基准或假设，定为中等
EVIDENCE_STRENGTH = {"reconcile": "强", "estimate": "中", "timing": "中", "trend": "中"}


def load_diag_config() -> dict:
    """读取 thresholds.yaml 中的 diagnostics 配置段（缺失返回空 dict）。"""
    with open(THRESHOLD_PATH, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    return cfg.get("diagnostics", {}) or {}


@dataclass
class Diagnostic:
    """单条诊断观察：中性事实陈述 + 严重度，不宣布结论。

    kind 固定为 observation；gate 控制是否作为 Gate 的 L1 异常信号；
    severity 三档（提示/观察/关注）；evidence_strength 由 category 推导。
    """
    id: str
    name: str
    category: str          # reconcile / estimate / timing / trend
    direction: str
    kind: str = "observation"
    statement: str = ""    # 中性事实陈述
    fields: list[str] = field(default_factory=list)
    expected: float | None = None
    actual: float | None = None
    deviation: float | None = None
    severity: str = "提示"   # 提示 / 观察 / 关注
    evidence_strength: str = "中"
    gate: bool = True        # 是否作为 Gate 的 L1 异常信号（false=仅报告观察）
    note: str = ""
    evidence_needed: list[str] = field(default_factory=list)

    @property
    def category_cn(self) -> str:
        """类别中文名（勾稽类/推算类/确认类/趋势类），未登记时回退原值。"""
        return CATEGORY_CN.get(self.category, self.category)

    def to_dict(self) -> dict:
        """转为普通 dict，并附加 category_cn 便于报告/UI 直接展示。"""
        d = asdict(self)
        d["category_cn"] = self.category_cn
        return d


def _f(row: dict, k: str):
    """安全取数：转 float，NaN（f != f）/缺失返回 None。"""
    v = row.get(k)
    try:
        f = float(v)
        return None if f != f else f
    except (TypeError, ValueError):
        return None


def _rel(a, b):
    """相对偏差 = |a-b| / max(|a|,|b|)，归一化到 0~1；分母为 0 或空值返回 None。"""
    if a is None or b is None:
        return None
    denom = max(abs(a), abs(b))
    return None if denom == 0 else abs(a - b) / denom


def _pct(x):
    """把小数转为一位小数的百分比字符串（用于陈述文案）。"""
    return f"{x*100:.1f}%"


class _Builder:
    """诊断构造器：封装阈值查找、严重度封顶与 Diagnostic 落库。"""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.items = cfg.get("items", {}) or {}
        self.cats = cfg.get("category_thresholds", {}) or {}
        self.out: list[Diagnostic] = []

    def sev(self, category: str, dev: float, item_id: str | None = None) -> str | None:
        """按偏差给出严重度：逐项阈值优先，其次类别阈值；低于提示阈值返回 None。"""
        # 逐项阈值优先（items.<id>.thresholds），否则回退类别阈值
        lv = {}
        if item_id:
            lv = (self.items.get(item_id, {}) or {}).get("thresholds", {}) or {}
        if not lv:
            lv = self.cats.get(category, {})
        if dev >= lv.get("关注", 0.5):
            return "关注"
        if dev >= lv.get("观察", 0.25):
            return "观察"
        if dev >= lv.get("提示", 0.1):
            return "提示"
        return None

    def add(self, id_, name, direction, expected, actual, dev, statement, ev):
        """构造并登记一条诊断；dev 为 None 或未达提示阈值则不登记。

        max_severity 允许对数据质量类诊断封顶（例如最高只到「提示」），
        避免数据缺失被误判为高优先级业务异常。
        """
        if dev is None:
            return
        meta = (self.items.get(id_, {}) or {})
        category = meta.get("category", "estimate")
        gate = bool(meta.get("gate", True))
        sev = self.sev(category, dev, item_id=id_)
        if sev is None:
            return
        cap = meta.get("max_severity")          # 数据质量类可封顶严重度（如 提示）
        if cap:
            order = ["提示", "观察", "关注"]
            if cap in order and order.index(sev) > order.index(cap):
                sev = cap
        self.out.append(Diagnostic(
            id_, name, category, direction, "observation",
            statement, [], expected, actual, round(dev, 4), sev,
            EVIDENCE_STRENGTH.get(category, "中"), gate,
            f"{CATEGORY_CN.get(category, category)}·{sev}", ev))


def _prev_year_value(history: dict, field_name: str):
    """取分析年份与上一年该字段值。"""
    # 历史不足两年或字段不可转数值时返回 (None, None)，调用方据此跳过趋势诊断
    if not history:
        return None, None
    years = sorted(history.keys())
    if len(years) < 2:
        return None, None
    y, y0 = years[-1], years[-2]
    try:
        return float(history[y].get(field_name)), float(history[y0].get(field_name))
    except (TypeError, ValueError):
        return None, None


def run_diagnostics(profile: dict, history: dict | None = None) -> list[Diagnostic]:
    """跑一遍全部确定性诊断，返回按偏差从大到小排序的观察列表。

    参数：profile 企业画像；history 历年关键字段（用于趋势类诊断）。
    返回：list[Diagnostic]（已按 |deviation| 降序）。
    口径：每个诊断只产出「中性事实 + 偏差 + 严重度」，不判定对错；
          计算前先做分母/空值保护，避免缺失字段制造 100% 假偏差。

    覆盖的检查（item_id）：
        勾稽类 tax_reconcile（当期+递延 vs 利润表所得税）、rd_detail（研发明细 vs 投入）、
              rd_caliber（研发投入 vs 利润表研发费用）；
        推算类 interest（利息 vs 有息负债×基准利率）、depreciation（折旧 vs 原值×基准率）、
              ar_ratio（应收占收入比）、revenue_gap（营业总收入 vs 营业收入）、
              cashflow（经营现金流 vs 净利润+折旧）、cash_debt_both_high（存贷双高）、
              cip_to_fixed_assets（在建工程占比）；
        确认类 gov_subsidy（补助 vs 其他收益+营业外收入）、tax_payable_change（应交税费变动）；
        趋势类 etr_yoy、rd_ratio_yoy、gross_margin_yoy（同比突变）；
        通用类 stock_flow（存量-流量不匹配，字段对可配置）。
    """
    cfg = load_diag_config()
    b = _Builder(cfg)

    it = _f(profile, "is_income_tax")

    # 勾稽类：当期+递延 vs 利润表所得税
    # 两者理论上应相等，差异通常来自口径/重分类，值得核对所得税明细
    cur = _f(profile, "current_tax_expense"); dfr = _f(profile, "deferred_tax_expense")
    if cur is not None and dfr is not None and it is not None:
        dev = _rel(cur + dfr, it)
        b.add("tax_reconcile", "所得税拆分与利润表差异", "所得税税会差异", it, cur + dfr, dev,
              f"当期所得税+递延所得税（{cur+dfr:,.0f}）与利润表所得税费用（{it:,.0f}）差异 {_pct(dev or 0)}",
              ["所得税费用明细", "纳税调整明细表"])

    # 勾稽类：研发明细 vs 研发投入
    # 研发投入表与辅助账明细合计应一致，差异提示归集口径或资本化处理问题
    rd_tab = _f(profile, "rd_spend_sum"); rd_detail = _f(profile, "rd_expense_detail_sum")
    if rd_tab is not None and rd_detail is not None:
        dev = _rel(rd_tab, rd_detail)
        b.add("rd_detail", "研发明细与研发投入差异", "研发筹划", rd_tab, rd_detail, dev,
              f"研发投入（{rd_tab:,.0f}）与研发费用明细合计（{rd_detail:,.0f}）差异 {_pct(dev or 0)}",
              ["研发支出辅助账", "研发费用构成明细"])

    # 勾稽类：研发投入(表) vs 研发费用(利润表)
    rd_is = _f(profile, "is_rd_expense")
    if rd_tab is not None and rd_is is not None:
        dev = _rel(rd_tab, rd_is)
        b.add("rd_caliber", "研发投入与利润表研发费用差异", "研发筹划", rd_tab, rd_is, dev,
              f"研发投入（{rd_tab:,.0f}）与利润表研发费用（{rd_is:,.0f}）差异 {_pct(dev or 0)}",
              ["研发费用构成明细", "费用化/资本化依据"])

    # 推算类：利息 vs 有息负债×基准利率（仅当有息负债>0，避免分母为 0 的饱和假象）
    intr = _f(profile, "interest_expense_best")
    idr = _f(profile, "interest_debt_ratio"); ta = _f(profile, "bs_total_assets")
    if intr is not None and idr is not None and ta is not None:
        debt = idr * ta
        if debt > 0:
            bench = (cfg.get("items", {}).get("interest", {}) or {}).get("benchmark_rate", 0.05)
            expected = debt * bench
            dev = _rel(intr, expected)
            b.add("interest", "利息支出与推算值差异", "融资筹划", expected, intr, dev,
                  f"利息支出（{intr:,.0f}）与有息负债×基准利率（{expected:,.0f}）差异 {_pct(dev or 0)}",
                  ["关联方借款明细", "同期同类贷款利率证明"])

    # 推算类：折旧 vs 原值×基准折旧率
    # 用固定资产原值 × 基准折旧率推算「应有折旧」，偏离过大提示折旧政策或转固异常
    dep = _f(profile, "fa_accum_dep_increase"); fa = _f(profile, "fa_original_end")
    if dep is not None and fa is not None:
        bench = (cfg.get("items", {}).get("depreciation", {}) or {}).get("benchmark_rate", 0.08)
        expected = fa * bench
        dev = _rel(dep, expected)
        b.add("depreciation", "折旧计提与推算值差异", "折旧摊销", expected, dep, dev,
              f"本期折旧（{dep:,.0f}）与固定资产原值×基准折旧率（{expected:,.0f}）差异 {_pct(dev or 0)}",
              ["固定资产明细及折旧政策"])

    # 推算类：应收账款占比（直接由 应收账款/营业收入 计算，不依赖缺失字段）
    # 偏离仅取「高于基准」一侧：占比过高才提示回款/坏账风险，过低不算异常
    ar = _f(profile, "bs_accounts_receivable"); rev = _f(profile, "is_revenue")
    if ar is not None and rev is not None and rev > 0:
        ar_ratio = ar / rev
        bench = (cfg.get("items", {}).get("ar_ratio", {}) or {}).get("benchmark", 0.20)
        dev = max(0.0, ar_ratio - bench) / bench if bench else None
        b.add("ar_ratio", "应收账款占比偏离基准", "应收账款", bench, ar_ratio, dev,
              f"应收账款/营业收入 = {ar_ratio:.2%}（基准 {bench:.0%}）", ["应收账款账龄分析"])

    # 推算类：营业总收入 vs 营业收入
    trev = _f(profile, "is_total_revenue"); rev = _f(profile, "is_revenue")
    dev = _rel(trev, rev)
    b.add("revenue_gap", "营业总收入与营业收入差异", "增值税", trev, rev, dev,
          f"营业总收入（{trev if trev is None else format(trev, ',.0f')}）与营业收入"
          f"（{rev if rev is None else format(rev, ',.0f')}）差异 {_pct(dev or 0)}", ["收入明细"])

    # 推算类：经营现金流 vs 净利润+折旧（仅盈利企业）
    cfo = _f(profile, "cf_operating_net"); npf = _f(profile, "is_net_profit")
    if cfo is not None and npf is not None and npf > 0:
        expected = npf + (dep or 0)
        dev = _rel(cfo, expected)
        b.add("cashflow", "经营现金流与净利润+折旧差异", "税负现金流", expected, cfo, dev,
              f"经营现金流净额（{cfo:,.0f}）与净利润+折旧（{expected:,.0f}）差异 {_pct(dev or 0)}",
              ["现金流量表附注"])

    # 确认类：政府补助 vs 其他收益+营业外收入
    # 政府补助总额应与利润表确认口径（其他收益 + 营业外收入）相互印证
    gov = _f(profile, "gov_subsidy_total")
    other = _f(profile, "is_other_income"); nonop = _f(profile, "is_non_operating_income")
    if gov is not None and (other is not None or nonop is not None):
        expected = (other or 0) + (nonop or 0)
        dev = _rel(gov, expected)
        b.add("gov_subsidy", "政府补助与利润表确认差异", "政府补助", expected, gov, dev,
              f"政府补助（{gov:,.0f}）与其他收益+营业外收入（{expected:,.0f}）差异 {_pct(dev or 0)}",
              ["补助文件与会计处理"])

    # 确认类：应交税费期末余额变动（辅助观察，非税费现金支出）
    tpe = _f(profile, "tax_payable_end"); tpb = _f(profile, "tax_payable_begin")
    if tpe is not None and tpb is not None:
        dev = _rel(tpe, tpb)
        b.add("tax_payable_change", "应交税费期末余额变动", "税负现金流", tpb, tpe, dev,
              f"应交税费期末余额（{tpe:,.0f}）较期初（{tpb:,.0f}）变动 {_pct(dev or 0)}"
              f"（辅助观察；受计提、缴纳、抵扣、税种结构、重分类等影响，不等于税费现金支出）",
              ["应交税费明细", "各税种申报表与缴款凭证"])

    # 趋势类：实际税率同比、研发费用率同比
    # _prev_year_value 取最近两年，只有历史≥2 年才有意义，否则 dev 为 None 自动跳过
    cur_etr, prev_etr = _prev_year_value(history or {}, "etr")
    dev = _rel(cur_etr, prev_etr)
    b.add("etr_yoy", "实际税率同比突变", "所得税税会差异", prev_etr, cur_etr, dev,
          f"实际税率由上期 {prev_etr if prev_etr is None else format(prev_etr, '.2%')} "
          f"变为 {cur_etr if cur_etr is None else format(cur_etr, '.2%')}（变动 {_pct(dev or 0)}）",
          ["纳税调整明细表"])

    cur_rd, prev_rd = _prev_year_value(history or {}, "rd_expense_ratio")
    dev = _rel(cur_rd, prev_rd)
    b.add("rd_ratio_yoy", "研发费用率同比突变", "研发筹划", prev_rd, cur_rd, dev,
          f"研发费用率由上期 {prev_rd if prev_rd is None else format(prev_rd, '.2%')} "
          f"变为 {cur_rd if cur_rd is None else format(cur_rd, '.2%')}（变动 {_pct(dev or 0)}）",
          ["研发费用构成明细"])

    # 推算类：存贷双高（高货币资金 + 高有息负债）
    cash = _f(profile, "bs_cash"); ta2 = _f(profile, "bs_total_assets")
    idr2 = _f(profile, "interest_debt_ratio")
    it_cd = (cfg.get("items", {}).get("cash_debt_both_high", {}) or {})
    cash_min = float(it_cd.get("cash_ratio_min", 0.15))
    debt_min = float(it_cd.get("debt_ratio_min", 0.10))
    # 存贷双高：先要求有息负债率≥下限，再看货币资金占比是否超上限，两者同时成立才是信号
    if cash is not None and ta2 and ta2 > 0 and idr2 is not None and idr2 >= debt_min:
        cash_ratio = cash / ta2
        if cash_ratio > cash_min:
            dev = (cash_ratio - cash_min) / cash_min
            b.add("cash_debt_both_high", "存贷双高（高货币资金+高有息负债）", "融资筹划",
                  cash_min, round(cash_ratio, 4), dev,
                  f"货币资金/总资产 = {cash_ratio:.2%}（≥{cash_min:.0%}）且有息负债率 {idr2:.2%}"
                  f"（≥{debt_min:.0%}），存在存贷双高特征，需核实资金归集与利息安排",
                  ["货币资金明细", "借款合同与利率", "资金归集/存贷双高说明"])

    # 趋势类：毛利率同比突变
    cur_gm, prev_gm = _prev_year_value(history or {}, "gross_margin")
    dev = _rel(cur_gm, prev_gm)
    b.add("gross_margin_yoy", "毛利率同比突变", "所得税税会差异", prev_gm, cur_gm, dev,
          f"毛利率由上期 {prev_gm if prev_gm is None else format(prev_gm, '.2%')} "
          f"变为 {cur_gm if cur_gm is None else format(cur_gm, '.2%')}（变动 {_pct(dev or 0)}）",
          ["收入成本明细", "毛利率变动说明"])

    # 推算类：在建工程占固定资产比偏高（延迟转固/折旧）
    # 在建工程长期挂账不转固会延迟折旧，形成税盾时间性差异，故占比偏高即观察
    cip = _f(profile, "bs_cip"); fa2 = _f(profile, "bs_fixed_assets")
    it_cip = (cfg.get("items", {}).get("cip_to_fixed_assets", {}) or {})
    ratio_min = float(it_cip.get("ratio_min", 0.30))
    if cip is not None and fa2 and fa2 > 0:
        r = cip / fa2
        if r >= ratio_min:
            dev = (r - ratio_min) / ratio_min
            b.add("cip_to_fixed_assets", "在建工程占固定资产比偏高", "资产筹划",
                  ratio_min, round(r, 4), dev,
                  f"在建工程/固定资产 = {r:.2%}（≥{ratio_min:.0%}），大额在建工程未转固可能延迟折旧与税盾",
                  ["在建工程明细与转固计划", "竣工结算资料"])

    # 通用「存量-流量」诊断（可配置字段对）：大额存量应有相应流量
    for sf in (cfg.get("stock_flow", []) or []):
        stock = _f(profile, sf.get("stock"))
        flow = _f(profile, sf.get("flow"))
        min_stock = float(sf.get("min_stock", 1e8))
        bench = float(sf.get("benchmark_ratio", 0.02))
        if stock is None or abs(stock) < min_stock:
            continue
        # 流量缺失/为 0/为负 → 属"数据不可用"，不作为"不匹配"异常（避免误报 100%）
        if flow is None or flow <= 0:
            continue
        expected = abs(stock) * bench
        dev = _rel(expected, flow)
        b.add(sf["id"], sf["name"], sf.get("direction", ""), expected, flow, dev,
              f"{sf['name']}：存量 {stock:,.0f} 元，本期流量 {flow:,.0f} 元，"
              f"按基准比例 {bench:.0%} 推算应约 {expected:,.0f} 元（偏差 {_pct(dev or 0)}）；"
              f"该字段含处置/转出，非纯折旧，仅作观察、需以租赁附注核实",
              sf.get("evidence_needed", []))

    b.out.sort(key=lambda d: -(d.deviation or 0))
    # 按偏差绝对值降序：偏差越大越靠前，报告与 Gate 优先关注
    return b.out


if __name__ == "__main__":
    import pandas as pd
    from src.common import CONFIG
    from src.pipeline.plan_pipeline import row_to_dict, load_history
    p = pd.read_parquet(Path(CONFIG["paths"]["features_dir"]) / "profile_features.parquet")
    p["stock_code"] = p.stock_code.astype(str)
    for code in ["000063", "000031"]:
        r = row_to_dict(p[(p.stock_code == code) & (p.year == 2024)].iloc[0])
        h = load_history(code, 2024)
        for d in run_diagnostics(r, h):
            print(code, d.severity, d.category_cn, d.name, f"{d.deviation:.1%}", "|", d.statement)
