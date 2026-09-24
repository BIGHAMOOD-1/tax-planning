"""🔴 Red Agent：机会发现（多轮，读共用数据池）。

职责：从企业视角论证某筹划方向「是否有收益可能」，并逐条回应 Blue 的质疑。
设计：
- LLM 可用时走 `try_chat_json`，解析失败或字段缺失时回退到规则模式 `_fallback`，
  保证在无模型/接口异常时流程仍能推进（结果标注 `_source` 供溯源）；
- 多轮辩论中只携带必要的历史摘要（上一轮主张、蓝方质疑、蓝方新检索证据），控制上下文长度。
"""
from __future__ import annotations

import json

from src.agents.prompts import RED_SYSTEM
from src.common import get_logger
from src.evidence.pool import DataPool
from src.llm.client import get_llm

log = get_logger()


def _fallback(pool: DataPool, history: list[dict]) -> dict:
    """规则兜底：不依赖 LLM，直接由数据池条件/事实/计算结果拼装红方论点。

    保证在模型不可用时仍产出结构化结论；无新增证据故置 no_new_evidence=True。
    """
    calc = pool.calculation or {}
    results = calc.get("results", {}) if calc else {}
    # 节税口径优先取 tax_saving，缺失时退化为 tax_shield（税盾）
    saving = results.get("tax_saving") or results.get("tax_shield")
    points = []
    used = []
    for c in pool.conditions.get("passed", []):
        points.append(f"满足条件：{c.get('desc') or c.get('field')}（实际值 {c.get('actual')}）")
    # 仅挑选与筹划方向强相关的关键事实，避免论点冗长
    for f in pool.company_facts:
        if f["field"] in ("rd_spend_sum", "is_revenue", "bs_total_assets", "interest_expense_best"):
            points.append(f"企业事实 [{f['id']}] {f['field']} = {f['value']}")
            used.append(f["id"])
    used += [p["id"] for p in pool.policies[:2]]
    if results:
        points.append(f"确定性计算：{json.dumps(results, ensure_ascii=False)}")
    responses = []
    # 逐条回应上一轮蓝方质疑；规则模式无法真正论证，统一提示人工核实
    if history:
        for issue in (history[-1].get("blue", {}) or {}).get("issues", []):
            responses.append({"issue": issue, "response": "规则模式：请结合数据池与证据要求人工核实"})
    return {
        "position": f"{pool.direction}方向存在潜在税务收益" if saving else f"{pool.direction}方向值得进一步核实",
        "opportunity_exist": bool(pool.conditions.get("passed")) or bool(results),
        "benefit_summary": f"{pool.direction}潜在税务影响估算",
        "tax_saving_estimate": saving,
        "supporting_points": points,
        "responses_to_blue": responses,
        "alternatives": [],
        "measures": [],
        "data_gaps": pool.data_gaps,
        "citations": [],
        "adopted_new_evidence": [],
        "no_new_evidence": True,
        "used_pool_ids": used,
    }


def analyze(direction: str, pool: DataPool, profile: dict, use_llm: bool = True,
            round_no: int = 1, history: list[dict] | None = None) -> dict:
    """执行一轮红方机会分析。

    参数：
    - direction：筹划方向；pool：共用数据池；profile：企业画像（当前用于上下文）；
    - use_llm：是否调用大模型，False 或解析失败时走规则兜底；
    - round_no：当前轮次；history：既往各轮摘要（用于逐条回应与避免重复）。

    返回：红方结构化结论 dict，含 `_source`（llm/rule）与 `_round` 便于溯源。
    """
    history = history or []
    if use_llm:
        llm = get_llm()
        payload = {
            "round": round_no,
            "data_pool": pool.prompt_view("red"),
            # 只回传必要历史，避免上下文膨胀；蓝方新检索证据以 N* 编号回流供采用
            "previous_rounds": [
                {"round": h.get("round"),
                 "your_position": (h.get("red") or {}).get("position"),
                 "blue_issues": (h.get("blue") or {}).get("issues"),
                 "blue_required_evidence": (h.get("blue") or {}).get("required_evidence"),
                 # 回流：Blue 上一轮新检索到的政策/案例（带临时编号 N*，可被采用）
                 "blue_found_evidence": [
                     {"nid": e.get("nid"), "source": e.get("source"), "title": e.get("title"),
                      "doc_no": e.get("doc_no"), "date": e.get("date")}
                     for e in ((h.get("blue") or {}).get("evidence_found") or [])
                 ]}
                for h in history
            ],
        }
        data = llm.try_chat_json([
            {"role": "system", "content": RED_SYSTEM},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False, default=str)},
        ])
        # 仅当返回具备红方关键字段时才采用，否则视为解析失败转兜底
        if isinstance(data, dict) and ("opportunity_exist" in data or "position" in data):
            # 补齐可选字段，避免下游 KeyError
            data.setdefault("used_pool_ids", [])
            data.setdefault("citations", [])
            data.setdefault("adopted_new_evidence", [])
            data.setdefault("no_new_evidence", False)
            data["_source"] = "llm"
            data["_round"] = round_no
            return data
    res = _fallback(pool, history)
    res["_source"] = "rule"
    res["_round"] = round_no
    return res
