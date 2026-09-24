"""共用数据池（DataPool）。

概念：在红蓝辩论**之前**，把该筹划方向涉及的
  企业事实 / 政策 / 案例 / 计算 / 缺口 / 待补证据
统一汇总成一份"公用数据池"，红、蓝、绿三方读同一份，保证"所见即所证"。

每条数据带稳定编号（F*/P*/C*），Agent 被要求引用编号，
从而可以回溯"红方用了哪几条、蓝方用了哪几条"。

不变量：
    - 编号在单次构建内稳定且唯一，跨运行对同一输入可复现；
    - 政策必须带时效判定结果（policy_validity），UNKNOWN/EXPIRED 不得支撑 CONFIRMED；
    - 外部证据按 source_precedence 排序，直接口径优先于文档提取。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from src.common import CONFIG, get_logger
from src.evidence.sources import lookup

log = get_logger()


def _clean(v):
    """清洗取值：None / NaN / Inf 统一归一为 None，避免把无效值写进证据池。"""
    try:
        import math
        if v is None or (isinstance(v, float) and (math.isnan(v) or math.isinf(v))):
            return None
    except Exception:  # noqa: BLE001
        pass
    return v


# 各方向"相关但本次未直接使用"的字段（加宽证据链，让数据可见）
# 说明：这些字段不进入条件/计算，但作为 R* 相关字段一并呈现，帮助 Red/Blue 发现线索；
# 与 company_facts（F*）的区别在于「是否被本方向直接使用」。
DIRECTION_FIELDS: dict[str, list[str]] = {
    "研发筹划": ["rd_spend_sum", "rd_expenses", "rd_invest", "rd_person", "is_rd_expense",
                 "rd_expense_ratio", "rd_spend_ratio", "rd_capitalize_rate", "rd_person_ratio_cross",
                 "rd_super_deduction_potential", "d101955005__Fn02207", "bs_development_expense",
                 "rd_payroll", "rd_direct_input", "rd_depreciation_amortization", "rd_design_test",
                 "rd_other", "rd_first_five", "rd_other_limit", "rd_other_excess"],
    "融资筹划": ["interest_expense_best", "interest_debt_ratio", "interest_debt_to_equity",
                 "implied_interest_rate", "bs_short_loan", "bs_long_loan", "bs_bonds_payable",
                 "d210431887__n_rows", "d210200544__FN_Fn02802", "bs_interest_payable",
                 "borrow_short_term", "borrow_long_term", "borrow_current_portion"],
    "资产筹划": ["bs_fixed_assets", "cf_capex", "fixed_asset_ratio", "asset_intensity",
                 "d205622632__FN_Fn017A04", "d205345963__Fn01903", "bs_cip"],
    "政府补助": ["gov_subsidy_total", "gov_subsidy_income_related", "gov_subsidy_ratio", "is_other_income"],
    "高新技术企业": ["is_hightech", "hightech_signal_proxy", "hightech_signal_reason",
                     "rd_spend_ratio", "rd_person_ratio_cross", "nominal_tax_rate", "etr"],
    "跨境筹划": ["subsidiary_overseas_count", "subsidiary_hk_mo_tw_count", "overseas_subsidiary_ratio",
                 "subsidiary_total_revenue", "subsidiary_count"],
    "关联交易": ["ma_related_party_count", "related_party_deal_ratio", "top5_customer_supplier_amount",
                 "d211406861__FN_Fn01006", "related_party_count"],
    "组织架构": ["subsidiary_count", "d045152435__n_rows", "related_party_count", "subsidiary_overseas_count"],
    "所得税税会差异": ["etr", "nominal_tax_rate", "is_income_tax", "is_total_profit", "tax_rate_gap",
                       "d205606775__Fn02605", "d211134056__FN_Fn06502"],
    "资产减值": ["impairment_total", "is_asset_impairment", "is_credit_impairment",
                 "d210940194__FN_Fn00902", "asset_imp_total", "asset_imp_receivable",
                 "asset_imp_inventory", "asset_imp_fixed_asset", "asset_imp_goodwill",
                 "credit_imp_total"],
    "职工薪酬": ["employee_compensation_base", "bs_payroll_payable", "d210230772__Fn03203",
                 "d210230772__Fn03204", "d210230772__Fn03205", "employee_amount_sum",
                 "payroll_wages", "payroll_welfare", "payroll_union", "payroll_education",
                 "payroll_social_insurance", "payroll_housing_fund"],
    "增值税": ["vat_rate", "is_revenue", "cf_tax_paid", "bs_tax_payable", "is_tax_surcharge",
               "tax_surcharge_ratio", "taxpay_vat", "cf_tax_refund"],
    "费用扣除": ["is_admin_expense", "is_selling_expense", "is_finance_expense",
                 "admin_expense_ratio", "selling_expense_ratio", "period_expense_ratio",
                 "management_expense_rate_csmar", "is_revenue",
                 "expense_entertainment", "expense_advertising", "expense_payroll_in_expense",
                 "expense_depreciation_in_expense", "expense_rd_in_expense", "expense_transport"],
    "折旧摊销": ["bs_fixed_assets", "bs_intangible_assets", "bs_development_expense",
                 "csmar__NonDebtTaxShield", "fixed_asset_ratio", "asset_intensity",
                 "d101955005__Fn02207", "bs_cip", "fa_original_end", "fa_accum_dep_end",
                 "fa_accum_dep_increase", "fa_net_end"],
    "亏损弥补": ["bs_retained_earnings", "is_total_profit", "is_income_tax", "etr", "is_net_profit"],
    "投资收益": ["is_investment_income", "d205404528__Fn01804", "is_total_profit", "is_other_income",
                 "investment_income_detail_sum", "ma_evaluation_value", "ma_book_value",
                 "ma_evaluation_change", "ma_expense_value"],
    "税负现金流": ["cf_tax_paid", "bs_tax_payable", "is_income_tax", "is_tax_surcharge",
                   "cf_operating_net"],
    "应收账款": ["bs_accounts_receivable", "is_credit_impairment", "is_revenue", "impairment_total"],
    "政府补助不征税": ["gov_subsidy_total", "gov_subsidy_income_related", "gov_subsidy_ratio",
                       "is_other_income"],
    "小税种": ["is_tax_surcharge", "vat_rate", "tax_surcharge_ratio", "property_tax_rate",
               "land_appreciation_rate", "surcharge_urban_maintenance", "surcharge_education",
               "surcharge_stamp", "surcharge_property", "surcharge_land_use",
               "surcharge_vehicle_vessel", "surcharge_environmental"],
    "股权结构": ["equity_nature", "largest_holder_rate", "top_ten_holders_rate", "separation",
                 "hierarchy", "equity_nature_id", "largest_holder"],
    "无形资产摊销": ["bs_intangible_assets", "bs_development_expense", "d101955005__Fn02207",
                     "d101955005__Fn02204", "d101955005__Fn02203", "rd_capitalize_rate"],
    "股份支付": ["expense_share_based", "is_admin_expense", "employee_compensation_base",
                 "expense_payroll_in_expense"],
    "租赁": ["d205618410__EndingBalance", "d205618410__Provision", "d210441016__EndingBalance",
             "bs_lease_liability", "d205618410__IncreaseDuringYear"],
}

# 所有方向都会纳入的公共字段（便于跨方向引用）
# 注意：这些字段在 _add_cross_references 中会被剔除，不作为「跨方向共享」证据
COMMON_FIELDS = ["is_revenue", "bs_total_assets", "nominal_tax_rate", "etr", "is_income_tax"]


@dataclass
class DataPool:
    """红蓝绿三方共享的数据池：事实(F)/相关字段(R)/政策(P)/案例(C)/计算/缺口/证据。

    编号稳定是核心不变量——Agent 引用编号、报告回溯编号，编号一旦生成不再变动。
    """
    direction: str
    company_facts: list[dict] = field(default_factory=list)   # F1..
    related_fields: list[dict] = field(default_factory=list)  # R1.. 相关但未直接使用
    policies: list[dict] = field(default_factory=list)        # P1..
    cases: list[dict] = field(default_factory=list)           # C1..
    calculation: dict = field(default_factory=dict)
    data_gaps: list[str] = field(default_factory=list)
    evidence_required: list[str] = field(default_factory=list)
    conditions: dict = field(default_factory=dict)
    amount_requires_evidence: bool = False
    risks: list[str] = field(default_factory=list)
    warnings: list[dict] = field(default_factory=list)
    diagnostics: list[dict] = field(default_factory=list)
    external_evidence: list[dict] = field(default_factory=list)
    qualification_proxy_only: bool = False
    policy_validity: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "direction": self.direction,
            "company_facts": self.company_facts,
            "related_fields": self.related_fields,
            "policies": self.policies,
            "cases": self.cases,
            "calculation": self.calculation,
            "data_gaps": self.data_gaps,
            "evidence_required": self.evidence_required,
            "conditions": self.conditions,
            "risks": self.risks,
            "warnings": self.warnings,
            "diagnostics": self.diagnostics,
            "external_evidence": self.external_evidence,
            "policy_validity": self.policy_validity,
            "visible_to": ["red", "blue", "green"],
        }

    def prompt_view(self, role: str = "red") -> dict:
        """给 LLM 的精简视图（带编号，便于引用）。role 决定案例排序偏好。"""
        weights = (CONFIG.get("rag", {}).get("channel_weights", {}) or {}).get(role, {})
        cases = self.cases
        if weights:
            # 按角色渠道权重降序：让 Red/Blue/Green 各自优先看到其偏好的案例来源
            cases = sorted(cases, key=lambda c: -float(weights.get(c.get("channel"), 1.0)))
        return {
            "direction": self.direction,
            "company_facts": [
                {"id": f["id"], "field": f["field"], "value": f["value"], "source": f.get("source_text")}
                for f in self.company_facts
            ],
            "related_fields": [
                {"id": f["id"], "field": f["field"], "value": f["value"], "source": f.get("source_text")}
                for f in self.related_fields
            ],
            "policies": [
                {"id": p["id"], "doc_no": p.get("doc_no"), "title": p.get("title"),
                 "date": p.get("date"), "excerpt": p.get("excerpt")}
                for p in self.policies
            ],
            "cases": [
                {"id": c["id"], "title": c.get("title"), "channel": c.get("channel"),
                 "date": c.get("date"), "excerpt": c.get("excerpt")}
                for c in cases
            ],
            "calculation": {
                # 只暴露结果/假设/是否估算，隐藏内部步骤细节，控制 prompt 长度
                "results": self.calculation.get("results"),
                "assumptions": self.calculation.get("assumptions"),
                "amount_is_estimate": self.calculation.get("amount_is_estimate"),
            },
            "conditions": self.conditions,
            "data_gaps": self.data_gaps,
            "evidence_required": self.evidence_required,
            "diagnostics": [
                {"id": d.get("id"), "name": d.get("name"), "deviation": d.get("deviation"),
                 "severity": d.get("severity"), "actual": d.get("actual"),
                 "expected": d.get("expected"), "note": d.get("note")}
                for d in self.diagnostics
            ],
            "external_evidence": [
                {"evidence_id": e.get("evidence_id"), "fact_type": e.get("fact_type"),
                 "value": e.get("value_num") if e.get("value_num") is not None else e.get("value_text"),
                 "unit": e.get("unit"), "caliber": e.get("caliber"),
                 "source_type": e.get("source_type"), "doc_type": e.get("doc_type"),
                 "source": e.get("source_ref") or e.get("location")}
                for e in self.external_evidence
            ],
        }


def build_pool(direction: str, skill_result: dict, profile: dict, kb=None,
               top_k: int = 8, case_top_k: int = 3,
               diagnostics: list[dict] | None = None) -> DataPool:
    """为一个方向构建共用数据池。

    参数：direction 方向；skill_result Skill 评估结果（提供条件/计算/政策/检索词）；
          profile 画像；kb 知识库；diagnostics 诊断结果（只保留本方向的）。
    返回：DataPool，含 F/R/P/C 编号数据与计算/缺口/外部证据。
    关键设计：
        - 企业事实字段来自「条件字段 + 计算步骤/分层 source」，按出现顺序去重编号；
        - 政策经 policy_meta 补元数据、经 policy_validity 判时效；
        - 外部已确认证据按 source_precedence 降序排列（直接输入 > 结构化 > 数据库 ≥ 文档）。
    """
    pool = DataPool(direction=direction)
    # 诊断结果按方向过滤：数据池只承载本方向的观察信号
    pool.diagnostics = [d for d in (diagnostics or []) if d.get("direction") == direction]
    # 政策时效判定基准日固定为分析年份年末，保证同一批政策在不同时间运行得到一致结论
    as_of = f"{int(profile.get('year'))}-12-31" if profile.get("year") else None

    # 1) 企业事实（条件字段 + 计算用到的字段）
    # 来源：资格条件的 field + 计算步骤 field + 计算分层 source，合并去重
    fields: list[str] = []
    for group in ("conditions_passed", "conditions_failed", "conditions_unknown"):
        for c in skill_result.get(group, []):
            if c.get("field"):
                fields.append(c["field"])
    calc = skill_result.get("calculation") or {}
    for s in calc.get("steps", []):
        if s.get("field"):
            fields.append(s["field"])
    for layer in calc.get("layers", []):
        if layer.get("source"):
            fields.append(layer["source"])
    # dict.fromkeys 去重同时保留首次出现顺序，保证 F 编号在多次运行间稳定
    fields = list(dict.fromkeys(fields))

    for i, f in enumerate(fields, 1):
        src = lookup(f)
        # 来源描述：原始字段给「数据集 · 字段代码」，衍生字段给公式，便于证据回溯
        source_text = (f"{src.get('dataset_name','')} · {src.get('column_code','')}"
                       if src.get("type") == "raw" else src.get("formula", ""))
        from src.evidence.sources import field_label
        pool.company_facts.append({
            "id": f"F{i}",
            "field": f,
            "label": field_label(f).get("label") or f,
            "value": _clean(profile.get(f)),
            "source": src,
            "source_text": source_text,
        })

    # 2) 政策（引擎已检索）
    # 为每条政策补元数据（来源/置信度/核验状态）并标注三方可见
    for i, p in enumerate(skill_result.get("policies", []), 1):
        item = dict(p)
        item["id"] = f"P{i}"
        item["visible_to"] = ["red", "blue", "green"]
        try:
            from src.evidence.policy_meta import build_metadata
            item["meta"] = build_metadata(item, as_of)
        except Exception as exc:  # noqa: BLE001
            log.warning("政策元数据构建失败：%s", str(exc)[:120])
        pool.policies.append(item)

    # 2b) 相关但未直接使用的字段（加宽证据链）
    used = {f["field"] for f in pool.company_facts}
    candidates = list(DIRECTION_FIELDS.get(direction, [])) + COMMON_FIELDS
    for i, f in enumerate(candidates, 1):
        if f in used:
            continue
        v = _clean(profile.get(f))
        if v is None:
            continue
        src = lookup(f)
        source_text = (f"{src.get('dataset_name','')} · {src.get('column_code','')}"
                       if src.get("type") == "raw" else src.get("formula", ""))
        pool.related_fields.append({
            "id": f"R{i}", "field": f, "value": v, "source": src, "source_text": source_text,
        })

    # 3) 案例（从案例库检索）
    if kb is not None:
        # 用 Skill 的 rag_queries（可多条）分别检索，按 title 去重，避免同一案例重复占位
        queries = skill_result.get("rag_queries", []) or [direction]
        seen = set()
        idx = 1
        for q in queries:
            for hit in kb.search(q, "case", top_k=max(case_top_k * 2, 6)):
                key = hit.get("title")
                if key in seen:
                    continue
                seen.add(key)
                pool.cases.append({
                    "id": f"C{idx}",
                    "title": hit.get("title"),
                    "channel": hit.get("channel"),
                    "date": hit.get("date"),
                    "score": round(hit.get("score", 0.0), 4),
                    "excerpt": (hit.get("text") or "")[:300],
                    "visible_to": ["red", "blue", "green"],
                })
                idx += 1

    # 4) 计算 / 缺口 / 证据 / 条件
    # 直接透传 Skill 计算结果与缺口清单，保持与引擎口径一致
    pool.calculation = calc
    pool.data_gaps = skill_result.get("data_gaps", [])
    pool.evidence_required = skill_result.get("evidence_required", [])
    pool.amount_requires_evidence = bool(skill_result.get("amount_requires_evidence", False))
    pool.qualification_proxy_only = bool(skill_result.get("qualification_proxy_only", False))
    pool.risks = skill_result.get("risks", [])
    # 外部已确认证据（Evidence Acquisition）——按采用优先级排序（直接输入 > 结构化 > 数据库 ≥ 文档提取）
    try:
        from src.evidence.contract import source_precedence
        from src.evidence.store import EvidenceStore
        st = EvidenceStore()
        evs = st.confirmed(profile.get("stock_code"), int(profile.get("year")))
        pool.external_evidence = sorted(
            evs, key=lambda e: -source_precedence(e.get("source_type"), e.get("extraction_method")))
    except Exception as exc:  # noqa: BLE001
        log.warning("外部证据库不可用：%s", str(exc)[:120])
        pool.external_evidence = []
    pool.warnings = skill_result.get("warnings", [])
    # 条件三态（通过/不通过/未知）原样透传，供 Red/Blue/Green 与报告区分「不满足」与「无法判断」
    pool.conditions = {        "passed": skill_result.get("conditions_passed", []),
        "failed": skill_result.get("conditions_failed", []),
        "unknown": skill_result.get("conditions_unknown", []),
    }
    # 政策时效最小安全门（P0-D）：UNKNOWN/EXPIRED 不得支撑 CONFIRMED
    try:
        from src.evidence.policy_validity import summarize as _summarize_validity
        pool.policy_validity = _summarize_validity(pool.policies, as_of)
    except Exception as exc:  # noqa: BLE001
        log.warning("政策时效判定失败：%s", str(exc)[:120])
        pool.policy_validity = {}
    return pool


def summarize_pool(pool: DataPool) -> dict:
    """运行级汇总用：统计本次调用了多少数据。"""
    return {
        "direction": pool.direction,
        "company_facts": len(pool.company_facts),
        "related_fields": len(pool.related_fields),
        "policies": len(pool.policies),
        "cases": len(pool.cases),
    }
