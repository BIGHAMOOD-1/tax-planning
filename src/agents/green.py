"""Green Agent：综合决策。

设计原则：
- **结论由确定性规则给出**（可复现、可审计），LLM 只补充 reasons 与 plan 文本；
- 四态结论（含英文码）：
    RECOMMEND / NOT_RECOMMEND / INSUFFICIENT_EVIDENCE / MANUAL_REVIEW
- **"不知道" ≠ "不符合"**：条件 unknown 判为"证据不足"；
- 金额若为会计口径估算、且税法口径尚待证据验证 → "需人工复核"。
- 汇总红蓝多轮辩论的全部记录。
"""
from __future__ import annotations

import json

from src.agents.prompts import GREEN_SYSTEM
from src.common import get_logger
from src.evidence.pool import DataPool
from src.llm.client import get_llm

log = get_logger()

DECISION_CODES = {
    "推荐": "RECOMMEND",
    "有条件推荐": "CONDITIONAL",
    "不推荐": "NOT_RECOMMEND",
    "证据不足": "INSUFFICIENT_EVIDENCE",
    "需要专业人员进一步确认": "MANUAL_REVIEW",
}
DECISIONS = set(DECISION_CODES)


def deterministic_decision(pool: DataPool, blue: dict) -> tuple[str, list[str]]:
    calc = pool.calculation or {}
    gaps = list(dict.fromkeys(pool.data_gaps))
    unknown = pool.conditions.get("unknown", [])
    failed = pool.conditions.get("failed", [])

    if failed or blue.get("compliant") is False:
        reasons = ["存在未满足的适用条件"] + \
                  [f"不满足：{c.get('desc') or c.get('field')}" for c in failed]
        return "不推荐", reasons
    if unknown or gaps:
        reasons = ["存在无法判断的条件或关键数据缺口（不等于不符合）"]
        reasons += [f"缺少：{g}" for g in gaps]
        reasons += [f"条件无法判断：{c.get('field')}" for c in unknown]
        return "证据不足", reasons
    if not calc or not calc.get("ok"):
        return "需要专业人员进一步确认", ["适用条件满足，但缺少可确定的计算依据"]
    # 金额依赖未验证的口径/假设 -> 有条件推荐
    conditional = bool(pool.amount_requires_evidence and not calc.get("caliber_verified", False))
    if conditional or blue.get("conditional"):
        reasons = ["适用条件满足，但金额依赖尚未验证的口径/假设，补齐关键证据后方可确认"]
        for a in calc.get("assumptions", []):
            reasons.append(f"前置条件：{a}")
        # 待补证据统一在「待补证据」区展示，此处不逐条罗列（避免重复）
        return "有条件推荐", reasons
    # 资格封顶：仅由 qualification_proxy / 资格线索满足、且无直接资格证据 → 不得 RECOMMEND
    qual_evidence = {"HIGH_TECH_CERT"}
    has_qual_ev = any(str(e.get("fact_type")) in qual_evidence
                      and str(e.get("verification_status")) == "confirmed"
                      for e in (pool.external_evidence or []))
    if getattr(pool, "qualification_proxy_only", False) and not has_qual_ev:
        return "需要专业人员进一步确认", [
            "资格仅由代理指标/资格线索满足，缺少直接资格证据（如高新技术企业证书），不得给出『推荐』",
            "需补充：高新技术企业证书及有效期等直接资格证据",
        ]
    return "推荐", ["适用条件满足，且已完成确定性计算"]


def _build_plan(direction: str, pool: DataPool, gaps: list[str], red: dict | None = None) -> dict:
    calc = pool.calculation or {}
    results = calc.get("results", {}) if calc else {}
    impact = results.get("confirmed_tax_impact")
    scenario = results.get("scenario_tax_impact")
    det = [f"补齐材料：{e}" for e in (pool.evidence_required or [])]
    ai = [str(x) for x in ((red or {}).get("measures") or [])]
    return {
        "direction": direction,
        "measures": det,                    # 确定性行动清单（补齐材料）
        "measures_ai": ai,                  # AI 建议措施（辅助）
        "policies": [p.get("doc_no") or p.get("title") for p in pool.policies][:5],
        "policy_validity": (pool.policy_validity or {}).get("overall"),
        "estimated_tax_impact": impact,
        "scenario_tax_impact": scenario,
        "amount_is_estimate": bool(calc.get("amount_is_estimate", False)),
        "risks": [],
        "missing_data": gaps,
        "evidence_required": pool.evidence_required,
    }


def _manual_review_detail(pool: DataPool, calc: dict) -> dict:
    """MANUAL_REVIEW 的可执行说明：确认什么、确认后能得什么、为何不是『证据不足』。"""
    confirm_items: list[str] = []
    for c in pool.conditions.get("unknown", []):
        confirm_items.append(f"确认条件：{c.get('desc') or c.get('field')}")
    confirm_items += list(pool.evidence_required or [])
    if not confirm_items:
        confirm_items = ["适用条件与计算口径的最终专业确认"]
    return {
        "why_manual_review": "适用条件已满足，但该方向缺少确定性计算器/需专业判断，"
                             "不属于关键数据缺失，故不判『证据不足』",
        "not_insufficient_reason": "关键条件未缺失（与『证据不足』的区别）；"
                                   "需人工就适用性与口径作出专业判断",
        "confirm_items": list(dict.fromkeys(confirm_items)),
        "possible_conclusions": [
            "确认适用且口径成立 → 可形成可执行方案并量化税务影响",
            "确认不适用 → 转为『不推荐』",
        ],
    }


def _apply_policy_validity_gate(pool: DataPool, decision: str, reasons: list[str],
                                plan: dict) -> tuple[str, list[str]]:
    """政策时效最小安全门（P0-D）：确认影响必须由「当前有效」的政策支撑。

    - `policy_validity == VALID` → 放行；
    - `EXPIRED / NOT_YET_EFFECTIVE` → 撤销确认影响并降级；
    - `UNKNOWN` → 不得单独支撑 CONFIRMED，撤销确认影响并降级。
    """
    if plan.get("estimated_tax_impact") is None:
        return decision, reasons
    overall = (pool.policy_validity or {}).get("overall") or "UNKNOWN"
    if overall == "VALID":
        return decision, reasons

    plan["estimated_tax_impact"] = None
    calc = pool.calculation or {}
    if isinstance(calc.get("results"), dict):
        calc["results"]["confirmed_tax_impact"] = None
    calc["confirmed_tax_impact"] = None
    if overall in ("EXPIRED", "NOT_YET_EFFECTIVE"):
        reasons.append(f"所引用政策时效为「{overall}」，不得作为确认依据")
    else:
        reasons.append("所引用政策时效未知（UNKNOWN），不得单独支撑『确认影响』，需人工核验政策有效性")
    if decision == "推荐":
        decision = "有条件推荐"
    return decision, reasons


def decide(direction: str, pool: DataPool, red: dict, blue: dict,
           history: list[dict] | None = None, use_llm: bool = True) -> dict:
    history = history or []
    decision, reasons = deterministic_decision(pool, blue)
    gaps = list(dict.fromkeys(
        pool.data_gaps + [c.get("field") for c in pool.conditions.get("unknown", [])]))
    plan = _build_plan(direction, pool, gaps, red)
    plan["risks"] = pool.risks
    decision, reasons = _apply_policy_validity_gate(pool, decision, reasons, plan)
    manual_review = None
    if decision == "需要专业人员进一步确认":
        manual_review = _manual_review_detail(pool, pool.calculation or {})
        plan["manual_review"] = manual_review
        reasons = reasons + [manual_review["why_manual_review"]] + \
            [f"待确认：{x}" for x in manual_review["confirm_items"]]

    if use_llm:
        llm = get_llm()
        payload = {
            "direction": direction,
            "fixed_decision": decision,
            "deterministic_reasons": reasons,
            "data_pool": pool.prompt_view(),
            "debate_rounds": [
                {"round": h.get("round"), "red": h.get("red"), "blue": h.get("blue")}
                for h in history
            ],
            "final_red": red, "final_blue": blue,
        }
        data = llm.try_chat_json([
            {"role": "system", "content": GREEN_SYSTEM +
             f"\n注意：本次结论已由确定性规则判定为「{decision}」，你必须沿用该结论，不要更改。"},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False, default=str)},
        ])
        if isinstance(data, dict):
            llm_reasons = data.get("reasons")
            llm_plan = data.get("plan") or {}
            data["decision"] = decision
            data["decision_code"] = DECISION_CODES[decision]
            data["reasons"] = reasons              # 确定性理由为主
            if llm_reasons:
                data["ai_note"] = llm_reasons      # LLM 解释退为辅助「AI 分析说明」
            merged = {**plan, **llm_plan}
            merged["measures"] = plan["measures"]          # 确定性行动清单（补齐材料）
            merged["measures_ai"] = plan["measures_ai"]    # AI 建议措施
            # 确定性字段不得被 LLM 覆盖（尤其：政策时效门禁可能已置空确认影响）
            for k in ("estimated_tax_impact", "scenario_tax_impact",
                      "policy_validity", "missing_data"):
                merged[k] = plan.get(k)
            data["plan"] = merged
            data["manual_review"] = manual_review
            data["_source"] = "llm"
            return data

    return {"decision": decision, "decision_code": DECISION_CODES[decision],
            "reasons": reasons, "plan": plan, "manual_review": manual_review,
            "_source": "rule"}
