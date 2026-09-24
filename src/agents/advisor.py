"""税务顾问 Agent（Update 6.1）：面向行动的**合规税务意见**。

与 Red/Blue/Green **同源**：读同一份公用数据池（F*/P*/C*/诊断/条件/计算/证据）
+ 辩论记录 + 决策结论；在此基础上**自行补充 RAG 检索**（实施类查询，A*）。

原则：**确定性骨架**（政策/条件/计算/证据/风险由程序给定）+ **LLM 叙述**；
无 LLM 时用模板兜底。结论沿用 Green，不得更改、不得编造政策。
"""
from __future__ import annotations

import json

from src.agents.prompts import ADVISOR_SYSTEM
from src.common import get_logger

log = get_logger()

DISCLAIMER = ("本意见由系统基于企业公开数据与政策检索自动生成，属专业分析参考，"
              "不构成正式税务鉴证或法律意见；最终以主管税务机关口径与专业机构确认为准。")

# 实施类检索查询（在 Skill 自身 rag_queries 之外补充）
IMPL_QUERIES: dict[str, list[str]] = {
    "研发筹划": ["研发费用辅助账 设置与归集 要求", "研发费用加计扣除 申报表 填报"],
    "高新技术企业": ["高新技术企业 认定条件 研发费用占比", "高新技术企业 资格维持 复审"],
    "资产减值": ["资产损失 税前扣除 留存备查资料", "资产损失 清单申报 专项申报 要求"],
    "费用扣除": ["业务招待费 广告费 扣除限额 申报", "期间费用 税前扣除凭证 要求"],
    "折旧摊销": ["固定资产 加速折旧 一次性扣除 申报", "税会折旧差异 台账"],
    "融资筹划": ["关联方利息 债资比 资本弱化 调整", "利息支出 税前扣除 凭证"],
    "政府补助": ["政府补助 不征税收入 三条件", "不征税收入 对应支出 不得扣除"],
    "政府补助不征税": ["政府补助 不征税收入 三条件"],
    "租赁": ["经营租赁 税会差异 纳税调整"],
    "职工薪酬": ["职工福利费 工会经费 职工教育经费 扣除限额"],
    "跨境筹划": ["境外所得 抵免 分国不分项", "受控外国企业 规则"],
    "投资收益": ["居民企业 股息红利 免税", "股权转让所得 计税基础"],
    "增值税": ["增值税 进项税额 抵扣 凭证", "留抵退税 条件"],
}
_GENERIC = ["{d} 税务处理 要求", "{d} 留存备查资料 清单", "{d} 申报 流程"]


def _extra_refs(direction: str, kb, top_k: int = 3) -> list[dict]:
    """补充实施类检索（政策 + 案例），返回带 A* 编号的参考。"""
    if kb is None:
        return []
    queries = list(IMPL_QUERIES.get(direction, [])) + [q.format(d=direction) for q in _GENERIC]
    out: list[dict] = []
    seen: set[str] = set()
    for q in queries:
        try:
            hits = kb.search(q, "policy", top_k=top_k)
        except Exception as exc:  # noqa: BLE001
            log.warning("Advisor 检索失败：%s", str(exc)[:120])
            hits = []
        for h in hits:
            key = h.get("doc_no") or h.get("title")
            if not key or key in seen:
                continue
            seen.add(key)
            out.append({"id": f"A{len(out) + 1}", "doc_no": h.get("doc_no"), "title": h.get("title"),
                        "url": h.get("url"), "channel": h.get("channel"), "date": h.get("date"),
                        "excerpt": (h.get("text") or "")[:200]})
        if len(out) >= 6:
            break
    return out


def _skeleton(doc: dict, extra_refs: list[dict]) -> dict:
    """从方案文档抽取"确定性骨架"（结论/条件/计算/政策/证据/风险）。

    骨架由程序给定、不依赖模型，是 LLM 叙述与模板兜底的共同事实基础。
    """
    lin = doc.get("lineage") or {}
    dp = lin.get("data_pool") or {}
    proc = lin.get("ai_process") or {}
    green = proc.get("green") or {}
    blue = proc.get("blue") or {}
    # 条件/政策可能来自数据池或 lineage 顶层，做兼容回退
    conditions = dp.get("conditions") or lin.get("conditions") or {}
    calc = lin.get("calculation") or {}
    policies = dp.get("policies") or lin.get("policies") or []
    return {
        "direction": doc.get("direction"),
        "decision": doc.get("final_decision"),
        "decision_code": doc.get("decision_code"),
        "reasons": green.get("reasons") or [],
        "conditions": conditions,
        "calculation_type": calc.get("calculation_type"),
        "confirmed_tax_impact": (calc.get("results") or {}).get("confirmed_tax_impact"),
        "scenario_tax_impact": (calc.get("results") or {}).get("scenario_tax_impact"),
        "policies": [{"id": p.get("id"), "doc_no": p.get("doc_no"), "title": p.get("title"),
                      "url": p.get("url")} for p in policies],
        "extra_refs": extra_refs,
        "evidence_required": lin.get("evidence_required") or [],
        # 去重合并蓝方风险与数据池风险，保留首次出现顺序
        "risks": list(dict.fromkeys((blue.get("risks") or []) + (dp.get("risks") or []))),
        "blue_issues": blue.get("issues") or [],
    }


def _template(sk: dict) -> dict:
    """无 LLM 兜底：由确定性骨架拼装（不编造）。"""
    cond = sk["conditions"]
    steps = []
    # 已满足条件 → 落地步骤（留痕备查）；未知条件 → 前置确认事项
    for c in (cond.get("passed") or [])[:8]:
        steps.append({"step": f"满足条件：{c.get('desc') or c.get('field')}",
                      "detail": "留存证明材料备查"})
    for c in (cond.get("unknown") or []):
        steps.append({"step": f"待确认：{c.get('desc') or c.get('field')}",
                      "detail": "先向企业/主管税务机关确认后再推进"})
    # 以证据清单首项作为"关键第一步"的具体动作
    ev = list(sk["evidence_required"])
    first = ev[0] if ev else ""
    return {
        "direction": sk["direction"],
        "decision": sk["decision"],
        "summary": f"结论：{sk['decision']}。本方向若要落地，应先确认适用条件与资料完备性，再按流程推进。",
        "approach": (f"关键第一步：先调取并核对「{first}」，确认口径与限额后，再推进后续步骤。"
                     if first else "关键第一步：先确认适用条件与基础资料，再按实施路径推进。"),
        "applicability": [c.get("desc") or c.get("field") for c in (cond.get("passed") or [])] or ["适用条件已初步满足"],
        "steps": steps or [{"step": "确认适用性", "detail": "核对政策适用条件与企业实际情况"}],
        "materials": list(sk["evidence_required"]) or ["按适用政策的留存备查资料要求准备"],
        "accounting_tax": [],
        "red_lines": sk["risks"] or sk["blue_issues"] or ["确保业务真实、资料留痕，避免被认定为不当筹划"],
        "not_applicable": [c.get("desc") or c.get("field") for c in (cond.get("failed") or [])],
        "references": [p["id"] for p in sk["policies"] if p.get("id")] + [r["id"] for r in sk["extra_refs"]],
        "refs": sk["policies"] + sk["extra_refs"],
        "disclaimer": DISCLAIMER,
        "_source": "rule",
    }


def build_opinion(doc: dict, kb=None, use_llm: bool = True) -> dict:
    """由（已构建或已保存的）方案文档生成合规税务意见。

    参数：
    - doc：方案文档（含 lineage/data_pool/ai_process 等）；kb：知识库（可为 None）；
    - use_llm：是否用 LLM 生成叙述；不可用或异常时回退模板。

    返回：结构化意见 dict，`_source` 标注 llm/rule；decision 始终沿用 Green 结论。
    """
    direction = doc.get("direction")
    extra = _extra_refs(direction, kb)
    sk = _skeleton(doc, extra)

    if not use_llm:
        return _template(sk)
    try:
        from src.llm.client import get_llm
        llm = get_llm()
        # 模型未配置（无 key/网络）时直接走模板，避免无谓等待
        if not llm.available:
            return _template(sk)
        payload = {
            "direction": direction,
            "decision": sk["decision"],
            "reasons": sk["reasons"],
            "conditions": sk["conditions"],
            "calculation": {"calculation_type": sk["calculation_type"],
                            "confirmed_tax_impact": sk["confirmed_tax_impact"],
                            "scenario_tax_impact": sk["scenario_tax_impact"]},
            "policies": sk["policies"],
            "extra_refs": sk["extra_refs"],
            # 辩论记录只保留每轮双方主张，控制上下文长度
            "debate": [{"round": h.get("round"),
                        "red": (h.get("red") or {}).get("position"),
                        "blue": (h.get("blue") or {}).get("position")}
                       for h in (doc.get("lineage", {}).get("debate") or [])],
            "blue_issues": sk["blue_issues"],
            "evidence_required": sk["evidence_required"],
            "risks": sk["risks"],
        }
        data = llm.try_chat_json([
            # 在系统提示后追加硬约束：结论必须沿用，防止模型改判
            {"role": "system", "content": ADVISOR_SYSTEM +
             f"\n注意：Green 结论已判定为「{sk['decision']}」，你必须沿用，不得更改。"},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False, default=str)},
        ])
        if isinstance(data, dict):
            data["direction"] = direction
            data["decision"] = sk["decision"]              # 沿用结论
            data.setdefault("disclaimer", DISCLAIMER)
            # 模型未给引用时用确定性政策/补充检索编号回填，保证可溯源
            if not data.get("references"):
                data["references"] = ([p["id"] for p in sk["policies"] if p.get("id")]
                                      + [r["id"] for r in sk["extra_refs"]])
            data["refs"] = sk["policies"] + sk["extra_refs"]
            data["_source"] = "llm"
            return data
    except Exception as exc:  # noqa: BLE001
        # 任何模型异常都回退模板，保证意见接口永不失败
        log.warning("Advisor 意见生成失败，改用模板：%s", str(exc)[:140])
    return _template(sk)
