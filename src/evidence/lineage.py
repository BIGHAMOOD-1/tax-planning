"""证据链构建与方案落盘：企业事实 -> 政策 -> 判断 -> 计算 -> 方案，可逐层回溯。"""
from __future__ import annotations

from pathlib import Path

from src.common import save_json
from src.evidence.pool import DataPool
from src.evidence.sources import lookup


def build_evidence(skill_result: dict, profile: dict) -> dict:
    """汇总方案涉及的企业事实字段，并附来源元信息与当前取值。

    字段来源：条件（通过/失败/未知）与计算步骤/层级引用的字段，去重后逐个 lookup。
    """
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
    # 去重且保持首次出现顺序
    fields = list(dict.fromkeys(fields))
    evidence = []
    for f in fields:
        src = lookup(f)
        src["value"] = profile.get(f)
        evidence.append(src)
    return {"items": evidence}


def build_evidence_chain(skill_result: dict, profile: dict) -> list[dict]:
    """构建计算证据链（按层级逆序，从结果回溯到基础数据）。

    对"税法口径"层标注待证据验证，并附带所需证据清单。
    """
    calc = skill_result.get("calculation") or {}
    chain: list[dict] = []
    # reversed：自顶向下展示（结论 -> 中间量 -> 基础字段）
    for layer in reversed(calc.get("layers", [])):
        node = {"layer": layer.get("layer"), "value": layer.get("value")}
        if layer.get("note"):
            node["note"] = layer["note"]
        if layer.get("source"):
            src = lookup(layer["source"])
            node["source"] = src
            node["source_value"] = profile.get(layer["source"])
        # 税法口径层默认未验证，需人工/证据确认
        if "税法口径" in str(layer.get("layer", "")):
            node["status"] = "待证据验证"
            node["evidence_required"] = skill_result.get("evidence_required", [])
        chain.append(node)
    return chain


def _required_facts_view(skill_result: dict, profile: dict) -> list[dict]:
    """Required Fact 的证据状态（供 UI）：present / resolution / 人类名 / 公式。"""
    from src.evidence.contract import resolve_linked_field
    from src.evidence.sources import field_label
    from src.rules.fact_resolution import (is_qualification_proxy, proxy_of,
                                           resolution_of)
    skill_id = skill_result.get("skill_id")
    out: list[dict] = []
    for f in (skill_result.get("required_facts") or []):
        # 先把关联字段解析为实际画像字段，再判定其取值是否存在（含 NaN 判定）
        lf = resolve_linked_field(f) or f
        v = profile.get(lf)
        present = not (v is None or (isinstance(v, float) and v != v))
        lab = field_label(lf)
        out.append({
            "fact": f, "field": lf, "present": present,
            "label": lab["label"], "source": lab["source"], "formula": lab["formula"],
            "resolution": resolution_of(lf, skill_id), "proxy_of": proxy_of(lf, skill_id),
            "qualification_proxy": is_qualification_proxy(lf),
        })
    return out


def build_plan_document(stock: str, year: int, direction: str, opportunity: dict,
                        pool: DataPool, skill_result: dict, red: dict, blue: dict,
                        green: dict, debate: list[dict], profile: dict) -> dict:
    """组装可落盘的方案文档（含结论、金额、血缘、证据链、辩论与计算明细）。

    这是方案产物的核心结构，下游报告/意见/审核均从此文档读取。
    """
    calc = skill_result.get("calculation") or {}
    results = calc.get("results", {}) if calc else {}
    impact = results.get("confirmed_tax_impact")
    scenario = results.get("scenario_tax_impact")

    # 数据血缘（字段级）
    try:
        from src.data.lineage import lineage_for
        # 收集条件与计算层级引用到的字段，去重后查询血缘
        used_fields = [f for f in dict.fromkeys(
            [c.get("field") for c in skill_result.get("conditions_passed", [])] +
            [c.get("field") for c in skill_result.get("conditions_failed", [])] +
            [l.get("source") for l in calc.get("layers", []) if l.get("source")]
        ) if f]
        data_lineage = lineage_for(used_fields)
    except Exception:  # noqa: BLE001
        # 血缘缺失不应阻断方案落盘
        data_lineage = []

    return {
        "stock_code": stock,
        "year": year,
        "direction": direction,
        "final_decision": green.get("decision"),
        "decision_code": green.get("decision_code"),
        "estimated_tax_impact": impact,
        "scenario_tax_impact": scenario,
        "policy_validity": pool.policy_validity or {},
        "amount_is_estimate": bool(calc.get("amount_is_estimate", False)),
        "plan": green.get("plan", {}),
        "lineage": {
            "tax_impact": impact,
            "scenario_tax_impact": scenario,
            "data_lineage": data_lineage,
            "evidence_chain": build_evidence_chain(skill_result, profile),
            "data_pool": pool.to_dict(),
            "debate": debate,
            "calculation": {
                "calculator": calc.get("calculator"),
                "unit": calc.get("unit"),
                "results": results,
                "layers": calc.get("layers", []),
                "steps": calc.get("steps", []),
                "assumptions": calc.get("assumptions", []),
                "amount_is_estimate": calc.get("amount_is_estimate", False),
                "caliber_verified": calc.get("caliber_verified"),
                "calculation_type": calc.get("calculation_type"),
                "calculation_status": calc.get("calculation_status"),
                "direction": calc.get("direction"),
                "impact_amount": calc.get("impact_amount"),
                "impact_basis": calc.get("impact_basis"),
                "baseline": calc.get("baseline"),
                "action": calc.get("action"),
                "caveat": calc.get("caveat"),
                "risk_note": calc.get("risk_note"),
                "shield_amount": calc.get("shield_amount"),
                "impact_type": calc.get("impact_type"),
                "proxy_inputs": calc.get("proxy_inputs", []),
                "not_calculable_inputs": calc.get("not_calculable_inputs", []),
                "note": calc.get("note"),
            },
            "required_facts": _required_facts_view(skill_result, profile),
            "company_facts": build_evidence(skill_result, profile),
            "conditions": {
                "passed": skill_result.get("conditions_passed", []),
                "failed": skill_result.get("conditions_failed", []),
                "unknown": skill_result.get("conditions_unknown", []),
                "eligibility_failed": skill_result.get("eligibility_failed", []),
                "eligibility_unknown": skill_result.get("eligibility_unknown", []),
            },
            "policies": skill_result.get("policies", []),
            "evidence_required": skill_result.get("evidence_required", []),
            "ai_process": {"opportunity": opportunity, "red": red, "blue": blue, "green": green},
            "manual_review": green.get("manual_review"),
            "risks": skill_result.get("risks", []),
            "data_gaps": skill_result.get("data_gaps", []),
        },
    }


def save_plan(doc: dict, run_dir: str | Path) -> str:
    """把方案文档写入 run_dir/plans/<方向 slug>.json，返回文件路径。"""
    run_dir = Path(run_dir)
    plans_dir = run_dir / "plans"
    plans_dir.mkdir(parents=True, exist_ok=True)
    path = plans_dir / f"{_slug(doc['direction'])}.json"
    save_json(doc, path)
    return str(path)


def _slug(text: str) -> str:
    """把方向名转为安全文件名（仅保留字母数字与 -_，截断 20 字符）。"""
    return "".join(ch for ch in str(text) if ch.isalnum() or ch in "-_")[:20] or "plan"
