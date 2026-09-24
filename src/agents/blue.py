"""🔵 Blue Agent：合规审查（多轮，读共用数据池）。

职责：审查红方主张是否站得住（政策依据→适用条件→证据充分性→计算口径→风险）。
设计：
- 三态判定 compliant/conditions_met：有失败条件=False；有未知/缺口=None；全部通过=True；
- LLM 失败时走 `_fallback` 规则模式，且规则模式也会主动做一次证据检索；
- `_evidence_search` 支持蓝方主动取证，命中证据以 N* 编号回流给下一轮红方。
"""
from __future__ import annotations

import json

from src.agents.prompts import BLUE_SYSTEM
from src.common import get_logger
from src.evidence.pool import DataPool
from src.llm.client import get_llm

log = get_logger()


def _fallback(pool: DataPool, red: dict) -> dict:
    """规则兜底：按数据池条件三态 + 计算假设 + 红方回应情况拼装审查结论。"""
    failed = pool.conditions.get("failed", [])
    unknown = pool.conditions.get("unknown", [])
    gaps = pool.data_gaps
    calc = pool.calculation or {}
    assumptions = calc.get("assumptions", [])
    # 金额需证据但口径未验证时，标记为"条件性结论"，不能直接采信
    conditional = bool(pool.amount_requires_evidence and not calc.get("caliber_verified", False))
    # 三态判定：失败→不合规；未知/缺口→不确定；否则合规
    compliant = False if failed else (None if (unknown or gaps) else True)

    issues = [f"不满足条件：{c.get('desc') or c.get('field')}（实际值 {c.get('actual')}）" for c in failed]
    issues += [f"条件无法判断（数据缺失）：{c.get('field')}" for c in unknown]
    # 审查方必须对"未验证的计算假设"提出质疑（即使条件满足）
    for a in assumptions:
        issues.append(f"计算口径未经验证：{a}")
    if conditional:
        issues.append("金额为估算值，缺少支撑证据，不能直接作为可执行结论")
    for r in red.get("responses_to_blue", []):
        # 红方承认无法回应的质疑应保留在问题清单，形成待办
        if "无法回应" in str(r.get("response", "")):
            issues.append(f"Red 未能回应：{r.get('issue')}")

    return {
        "position": "规则审查：条件逐项核对 + 计算口径与证据审查",
        "compliant": compliant,
        "conditions_met": False if failed else (None if unknown else True),
        "conditional": conditional,
        "evidence_sufficient": not gaps and not unknown,
        "issues": issues,
        "required_evidence": gaps + [c.get("field") for c in unknown] + pool.evidence_required,
        "risks": [f"企业风险提示：{g}" for g in pool.data_gaps] + list(pool.risks),
        "reasoning": "根据数据池逐项核对条件、计算假设与证据充分性",
        "used_pool_ids": [f["id"] for f in pool.company_facts[:3]] + [p["id"] for p in pool.policies[:1]],
    }


def _evidence_search(issues: list[str], kb, direction: str | None = None) -> list[dict]:
    """Blue 主动取证：优先政策原文，其次案例库，寻找可支撑/反驳的替代证据。"""
    if kb is None or not issues:
        return []
    from src.common import CONFIG
    from src.rag.tax_filter import policy_filters
    blue_w = (CONFIG.get("rag", {}).get("channel_weights", {}) or {}).get("blue", {})
    found = []
    seen = set()
    # 仅取前 3 个问题检索，控制耗时；每个问题分别查政策与案例
    for issue in issues[:3]:
        for corpus, label in (("policy", "政策"), ("case", "案例")):
            try:
                hits = kb.search(issue, corpus, top_k=1,
                                 filters=policy_filters(direction) if corpus == "policy" else None,
                                 channel_weights=blue_w if corpus == "case" else None)
            except Exception:  # noqa: BLE001
                continue
            for h in hits:
                # 以 doc_no/title 去重，避免同一政策被多问题重复纳入
                key = (h.get("doc_no") or h.get("title"))
                if not key or key in seen:
                    continue
                seen.add(key)
                found.append({"issue": issue, "source": label,
                              "title": h.get("title"), "doc_no": h.get("doc_no"),
                              "channel": h.get("channel"), "date": h.get("date"),
                              "tax_type": h.get("tax_type", ""),
                              "score": round(h.get("score", 0.0), 4)})
    # 统一分配 N* 临时编号，供红方下一轮 adopted_new_evidence 引用
    for i, e in enumerate(found, 1):
        e["nid"] = f"N{i}"
    return found


def review(pool: DataPool, red: dict, use_llm: bool = True,
           round_no: int = 1, history: list[dict] | None = None, kb=None) -> dict:
    """执行一轮蓝方合规审查。

    参数：
    - pool：共用数据池；red：本轮红方结论；kb：知识库（用于主动取证，可为 None）；
    - use_llm：是否调用大模型；round_no：轮次；history：既往轮次摘要。

    返回：蓝方结构化结论，含 `evidence_found`（N* 编号新证据）与 `_source`。
    """
    history = history or []
    if use_llm:
        llm = get_llm()
        payload = {
            "round": round_no,
            "data_pool": pool.prompt_view("blue"),
            "red_current": red,
            "previous_rounds": [
                {"round": h.get("round"),
                 "red_position": (h.get("red") or {}).get("position"),
                 "your_issues": (h.get("blue") or {}).get("issues")}
                for h in history
            ],
        }
        data = llm.try_chat_json([
            {"role": "system", "content": BLUE_SYSTEM},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False, default=str)},
        ])
        # 仅当返回含 compliant 字段才采用，否则转规则兜底
        if isinstance(data, dict) and "compliant" in data:
            data.setdefault("used_pool_ids", [])
            data["_source"] = "llm"
            data["_round"] = round_no
            # LLM 未自带取证结果时，由系统补齐一次主动检索
            data.setdefault("evidence_found", _evidence_search(data.get("issues", []), kb, pool.direction))
            return data
    res = _fallback(pool, red)
    res["_source"] = "rule"
    res["_round"] = round_no
    res["evidence_found"] = _evidence_search(res.get("issues", []), kb, pool.direction)
    return res
