"""Evidence Resolution：把 Evidence 层"采用"为 Profile（当前采用视图）。

模型（用户约定）：
    Evidence 是事实的历史记录，Profile 是当前采用结果。

采用规则：
| 输入              | Profile 空值 | Profile 已有值     | 冲突        |
|-------------------|--------------|--------------------|-------------|
| manual            | 写入         | 允许覆盖 + 记旧值  | superseded  |
| user_structured   | 写入         | 默认不覆盖*        | conflict    |
| csmar             | 基准值       | 不覆盖             | conflict    |
| user_document     | 候选         | 不直接覆盖         | conflict    |

* user_structured 仅在 import_mode="authoritative" 时允许覆盖。
排序键：SOURCE_PRECEDENCE → extraction_confidence → 新旧。
"""
from __future__ import annotations

import math

from src.common import get_logger
from src.evidence.contract import (normalize_source_type, resolve_linked_field,
                                   source_precedence)

log = get_logger()

REL_TOL = 0.01          # 数值"接近"判断（不代表口径一致）
BASE_SOURCE = "csmar"   # 画像基准来源


def _num(v):
    try:
        f = float(v)
        return None if math.isnan(f) or math.isinf(f) else f
    except (TypeError, ValueError):
        return None


def _may_override(ev: dict) -> bool:
    st = normalize_source_type(ev.get("source_type"))
    if st == "manual":
        return True
    if st == "user_structured":
        return str(ev.get("import_mode") or "supplement") == "authoritative"
    return False


def _auth_rank(ev: dict) -> int:
    """同优先级时，authoritative 优先于 supplement。"""
    return 1 if str(ev.get("import_mode") or "supplement") == "authoritative" else 0


def resolve_profile(profile: dict, store=None, apply_overrides: bool = True,
                    persist: bool = False) -> tuple[dict, dict]:
    """把已确认证据采用到画像。

    返回 (增强后的 profile, result)，result = {adopted, superseded, conflicts}。
    """
    from src.evidence.store import EvidenceStore

    prof = dict(profile)
    stock, year = prof.get("stock_code"), prof.get("year")
    result: dict = {"adopted": [], "superseded": [], "conflicts": []}
    if not stock:
        return prof, result

    try:
        st = store or EvidenceStore()
        evs = st.confirmed(str(stock), int(year) if year is not None else None)
    except Exception as exc:  # noqa: BLE001
        log.warning("证据库不可用，跳过采用：%s", str(exc)[:120])
        return prof, result

    groups: dict[str, list[dict]] = {}
    for e in evs:
        lf = resolve_linked_field(e.get("fact_type"))
        if not lf or _num(e.get("value_num")) is None:
            continue
        groups.setdefault(lf, []).append(e)

    for field, items in groups.items():
        items.sort(key=lambda e: (-source_precedence(e.get("source_type"), e.get("extraction_method")),
                                  -float(e.get("extraction_confidence") or 0),
                                  -_auth_rank(e),
                                  str(e.get("created_at") or "")))
        top = items[0]
        val = _num(top.get("value_num"))
        base = _num(prof.get(field))
        rec = {"field": field, "value": val, "evidence_id": top.get("evidence_id"),
               "source_type": normalize_source_type(top.get("source_type")),
               "extraction_method": top.get("extraction_method"),
               "precedence": source_precedence(top.get("source_type"), top.get("extraction_method")),
               "import_mode": top.get("import_mode")}

        if base is None:
            prof[field] = val
            result["adopted"].append(rec)
            if persist:
                st.mark_resolution(top["evidence_id"], "accepted")
            continue

        if abs(base - val) <= REL_TOL * max(abs(base), 1.0):
            continue  # 数值接近，视为一致

        if _may_override(top) and apply_overrides:
            prof[field] = val
            sup = {**rec, "old_value": base, "old_source": BASE_SOURCE}
            result["superseded"].append(sup)
            if persist:
                st.mark_resolution(top["evidence_id"], "accepted")
        else:
            result["conflicts"].append({**rec, "profile_value": base,
                                        "note": "证据与画像不一致，按采用规则不覆盖"})
            if persist:
                st.mark_resolution(top["evidence_id"], "conflict")

    if result["adopted"] or result["superseded"]:
        prof["_evidence_resolution"] = result
    if result["adopted"]:
        log.info("证据采用 %s 个空字段：%s", len(result["adopted"]),
                 [a["field"] for a in result["adopted"]])
    if result["superseded"]:
        log.info("证据覆盖 %s 个字段（保留旧值）：%s", len(result["superseded"]),
                 [s["field"] for s in result["superseded"]])
    if result["conflicts"]:
        log.info("证据与画像冲突（不覆盖）%s 条：%s", len(result["conflicts"]),
                 [c["field"] for c in result["conflicts"]])
    return prof, result


def direct_evidence_fields(stock_code, year, store=None) -> set[str]:
    """已确认且为「直接口径」证据所覆盖的字段集合。

    用于把字段默认的 PROXY 升级为 DIRECT：
    例如上传「研发辅助账（税法口径研发费用）」后，`rd_spend_sum` 不再视为代理口径，
    相应计算器（研发加计）在条件满足时可产出 CONFIRMED。
    """
    from src.evidence.contract import normalize_source_type, resolve_linked_field
    from src.evidence.store import EvidenceStore

    try:
        st = store or EvidenceStore()
        evs = st.confirmed(str(stock_code), int(year) if year is not None else None)
    except Exception as exc:  # noqa: BLE001
        log.warning("直接口径证据查询失败：%s", str(exc)[:120])
        return set()

    out: set[str] = set()
    for e in evs:
        src = normalize_source_type(e.get("source_type"))
        is_direct = src == "manual" or str(e.get("import_mode") or "") == "authoritative"
        if not is_direct:
            continue
        lf = resolve_linked_field(e.get("fact_type"))
        if lf:
            out.add(lf)
    if out:
        log.info("直接口径证据覆盖字段（PROXY→DIRECT）：%s", sorted(out))
    return out
