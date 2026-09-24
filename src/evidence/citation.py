"""政策引用校验（防"张冠李戴/编造引用"）。

规则：Red 若引用某条政策 [P*]，必须给出**逐字原文片段** quote；
程序校验该 quote 是否确实出现在所引政策的检索原文（chunk text）中。
校验不通过 → 标注 ⚠，但不自动删除（由 Blue/人工裁决）。
"""
from __future__ import annotations

import re

_WS = re.compile(r"\s+")


def _norm(s) -> str:
    """归一化：去空白 + 全角括号/顿号统一，便于子串比对。"""
    t = _WS.sub("", str(s or ""))
    return (t.replace("（", "(").replace("）", ")")
             .replace("，", ",").replace("。", ".").replace("；", ";").replace("：", ":"))


def validate_citations(citations: list[dict] | None, policies: list[dict]) -> list[dict]:
    """校验 Red 的政策引用。返回每条引用的校验结果。"""
    by_id = {p.get("id"): p for p in (policies or [])}
    out: list[dict] = []
    for c in (citations or []):
        pid = str(c.get("policy_id") or c.get("id") or "").strip()
        quote = str(c.get("quote") or "").strip()
        pol = by_id.get(pid)
        rec = {"policy_id": pid, "quote": quote,
               "title": (pol or {}).get("title"), "doc_no": (pol or {}).get("doc_no")}
        if pol is None:
            rec.update(valid=False, reason="引用了不存在的政策编号")
        elif not quote:
            rec.update(valid=False, reason="缺少原文引用片段")
        else:
            text = pol.get("text") or pol.get("excerpt") or ""
            if _norm(quote) and _norm(quote) in _norm(text):
                rec.update(valid=True, reason="引用片段确属该政策原文")
            else:
                rec.update(valid=False, reason="引用片段未在所引政策原文中找到")
        out.append(rec)
    return out
