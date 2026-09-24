"""冲突检测：多来源事实比对（只检测、不自动覆盖）。

关键规则：容差只判断"数值接近"，不判断"口径一致"。
判定同一事实需依次通过：数值 → 容差 → 口径 → 时间 → 单位。
"""
from __future__ import annotations

from collections import defaultdict

from src.common import get_logger

log = get_logger()

# 冲突检测阈值（仅用于标记，绝不作为自动覆盖阈值）
TOL = {
    "amount": {"consistent": 0.01, "minor": 0.05},   # 相对差
    "ratio": {"consistent": 0.005, "minor": 0.01},   # 绝对差（百分点）
}


def _num(v):
    """安全转数值；无法转换或 NaN 返回 None（非数值走人工核对）。"""
    try:
        f = float(v)
        return f if f == f else None
    except (TypeError, ValueError):
        return None


def compare_values(a, b, kind: str) -> tuple[str, str]:
    """返回 (status, reason)。status ∈ consistent/minor_conflict/conflict/needs_review。"""
    if kind == "text":
        return ("consistent", "") if str(a) == str(b) else ("needs_review", "文本不同，需人工核对")
    if kind == "count":
        return ("consistent", "") if a == b else ("conflict", "数量不一致")
    fa, fb = _num(a), _num(b)
    if fa is None or fb is None:
        return "needs_review", "非数值，需人工核对"
    if kind == "ratio":
        # 比率用绝对差（百分点）：0.5% 内一致，0.5%~1% 轻微，>1% 冲突
        d = abs(fa - fb)
        if d <= TOL["ratio"]["consistent"]:
            return "consistent", ""
        if d <= TOL["ratio"]["minor"]:
            return "minor_conflict", "比率差异 0.5–1 个百分点，需确认"
        return "conflict", "比率差异 >1 个百分点"
    # amount
    # 金额用相对差：以双方绝对值的较大者为分母，避免小数值放大误差
    r = abs(fa - fb) / (max(abs(fa), abs(fb)) or 1)
    if r <= TOL["amount"]["consistent"]:
        return "consistent", ""
    if r <= TOL["amount"]["minor"]:
        return "minor_conflict", "金额差异 1%–5%，需检查口径"
    return "conflict", "金额差异 >5%"


def _gate_same_fact(a: dict, b: dict) -> tuple[bool, str]:
    """口径/期间/单位/币种一致性闸门。"""
    for key, name in (("caliber", "口径"), ("period", "期间"),
                      ("unit", "单位"), ("currency", "币种")):
        va, vb = str(a.get(key) or ""), str(b.get(key) or "")
        if va and vb and va != vb:
            return False, f"{name}不同（{va} vs {vb}），非同一事实"
    return True, ""


def detect(store, stock_code: str | None = None, year: int | None = None,
           mark: bool = False) -> list[dict]:
    """按 (企业, 年, 事实类型) 分组比对；返回冲突清单。mark=True 时把冲突项标记为 conflict。"""
    rows = store.query(stock_code=stock_code, year=year)
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        groups[(r.get("stock_code"), r.get("year"), r.get("fact_type"))].append(r)

    results = []
    for (code, yr, ft), items in groups.items():
        # 单条记录无冲突可比
        if len(items) < 2:
            continue
        base = items[0]
        kind = base.get("value_kind") or "amount"
        statuses = []
        for other in items[1:]:
            # 先过口径/期间/单位/币种闸门，不同口径不算同一事实
            same, why = _gate_same_fact(base, other)
            if not same:
                statuses.append(("different_caliber", why))
                continue
            st, reason = compare_values(base.get("value_num") or base.get("value_text"),
                                        other.get("value_num") or other.get("value_text"), kind)
            statuses.append((st, reason))
        # 取最严重状态作为该组结论（严重度按列表顺序递增）
        worst = max(statuses, key=lambda s: ["consistent", "minor_conflict", "needs_review",
                                             "different_caliber", "conflict"].index(s[0])
                    if s[0] in ["consistent", "minor_conflict", "needs_review",
                                "different_caliber", "conflict"] else 0)
        rec = {
            "stock_code": code, "year": yr, "fact_type": ft, "value_kind": kind,
            "status": worst[0], "reason": worst[1],
            "values": [{"evidence_id": it.get("evidence_id"), "source_type": it.get("source_type"),
                        "value": it.get("value_num") or it.get("value_text"),
                        "unit": it.get("unit"), "caliber": it.get("caliber")}
                       for it in items],
        }
        results.append(rec)
        if mark and worst[0] in ("conflict", "different_caliber"):
            for it in items:
                store.mark_resolution(it["evidence_id"], "conflict")

    log.info("冲突检测：检查 %s 组，发现 %s 组非一致", len(groups), len(results))
    return results


if __name__ == "__main__":
    from src.evidence.ingest import manual_fact
    from src.evidence.store import EvidenceStore
    st = EvidenceStore(":memory:")
    st.add(manual_fact("000063", 2024, "rd_spend_sum", 1.45e8, unit="元",
                       caliber="合并", source_type="csmar", source_ref="CSMAR"))
    st.add(manual_fact("000063", 2024, "rd_spend_sum", 1.46e8, unit="元",
                       caliber="合并", source_type="annual_report", source_ref="2024年报P87"))
    for r in detect(st):
        print(r["status"], r["reason"], r["values"])
