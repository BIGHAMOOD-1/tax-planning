"""Historical Solution KB：以"方案/决策案例"为最小单位的经验库。

与 Policy KB / Case KB 分离；检索命中"某方案及其条件/结论"，而非整份报告。
条目必须带审核状态与证据链；驳回/证据不足/冲突的方案同样保存。
"""
from __future__ import annotations

from src.common import get_logger

log = get_logger()


def solution_from_plan(plan: dict, profile: dict, review_status: str = "candidate") -> dict:
    """把单个方向的 plan 转成一条"方案/决策案例"。"""
    lin = plan.get("lineage") or {}
    pool = lin.get("data_pool") or {}
    calc = lin.get("calculation") or {}

    # 事实条件：适用条件 + 企业事实取值 + 诊断发现，构成方案的前置事实快照
    fact_conditions = {
        "conditions": lin.get("conditions", {}),
        "facts": {f.get("field"): f.get("value") for f in pool.get("company_facts", [])},
        "diagnostics": pool.get("diagnostics", []),
    }
    # 仅保留政策关键标识，控制条目体积
    policies = [{"doc_no": p.get("doc_no"), "title": p.get("title"), "date": p.get("date")}
                for p in pool.get("policies", [])]
    ai_proposal = {
        "decision": plan.get("final_decision"),
        "decision_code": plan.get("decision_code"),
        "confirmed_impact": plan.get("estimated_tax_impact"),
        "scenario_impact": plan.get("scenario_tax_impact"),
        "calculation": calc.get("results"),
        "assumptions": calc.get("assumptions", []),
        "reasons": (lin.get("ai_process", {}).get("green") or {}).get("reasons", []),
    }
    external_evidence = pool.get("external_evidence", [])
    # 证据链：保留证据链、数据血缘、所需证据与数据缺口，供后续复用与审核
    evidence_chain = {
        "evidence_chain": lin.get("evidence_chain", []),
        "data_lineage": lin.get("data_lineage", []),
        "evidence_required": lin.get("evidence_required", []),
        "data_gaps": lin.get("data_gaps", []),
    }
    return {
        "solution_id": "",
        "stock_code": plan.get("stock_code"),
        "company_name": profile.get("short_name", ""),
        "year": plan.get("year"),
        "direction": plan.get("direction"),
        "skill_id": (plan.get("skill") or {}).get("id", ""),
        "fact_conditions": fact_conditions,
        "policies": policies,
        "ai_proposal": ai_proposal,
        "external_evidence": external_evidence,
        "human_review": {},
        "final_decision": plan.get("final_decision"),
        "review_status": review_status,
        "evidence_chain": evidence_chain,
    }


def save_from_plans(store, plans: list[dict], profile: dict,
                    review_status: str = "candidate") -> list[str]:
    """把一次分析的所有方向方案存入方案库（每条方向一个最小单位）。"""
    ids = []
    for p in plans:
        if "lineage" not in p:
            continue
        sol = solution_from_plan(p, profile, review_status=review_status)
        sid = store.save_solution(sol)
        ids.append(sid)
    log.info("方案库写入 %s 条（review_status=%s）", len(ids), review_status)
    return ids


def promote(store, solution_id: str, review_status: str, human_review: dict) -> None:
    """人工审定：采纳/驳回/修改。review_status ∈ accepted/rejected/modified/..."""
    store.update_solution_review(solution_id, review_status, human_review)
    log.info("方案 %s -> %s", solution_id, review_status)
