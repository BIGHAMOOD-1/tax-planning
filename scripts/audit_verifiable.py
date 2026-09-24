"""可验证计算器审计（Update 6.4 · F）。

对每个 Skill 的计算器，按其"技能语境"计算关键输入的有效 resolution，
判断是否"全 DIRECT 且为增量收益"→ 可产出 CONFIRMED（可验证）。
输出 validation/verifiable_audit.md。

用法：python scripts/audit_verifiable.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.common import ROOT, get_logger  # noqa: E402
from src.rules.calculator import VERIFIABLE_CALCULATORS, calculation_type_of  # noqa: E402
from src.rules.fact_resolution import resolution_of  # noqa: E402
from src.skills.engine import get_skills  # noqa: E402

log = get_logger()
OUT = ROOT / "validation" / "verifiable_audit.md"


def main():
    """审计每个 Skill 的计算器：判断其是否可产出 CONFIRMED（可验证）结论。"""
    rows = []
    for s in get_skills().values():
        cid = s.calculation
        if not cid:
            continue
        # 关键输入优先取 calculation_core_inputs，缺失时退化为全部输入
        core = list(s.calculation_core_inputs or s.calculation_inputs)
        res = {f: resolution_of(f, s.id) for f in core}
        # 代理/不足的输入会阻断 CONFIRMED，只能情景测算
        proxies = [f for f, r in res.items() if r in ("PROXY", "INSUFFICIENT")]
        ctype = calculation_type_of(cid)
        all_direct = not proxies
        verifiable = cid in VERIFIABLE_CALCULATORS
        # 可确认三条件：全 DIRECT + 增量收益 + 在可验证计算器集合
        conf_capable = all_direct and ctype == "INCREMENTAL_TAX_BENEFIT" and verifiable
        rows.append({"skill": s.id, "direction": s.direction, "calc": cid,
                     "ctype": ctype, "inputs": res, "proxies": proxies,
                     "all_direct": all_direct, "verifiable": verifiable,
                     "conf_capable": conf_capable})

    # 可确认的排前面，其余按金额语义与 Skill 名排序
    rows.sort(key=lambda r: (not r["conf_capable"], r["ctype"], r["skill"]))
    L = ["# 可验证计算器审计（Update 6.4）", "",
         "判定：`可确认` = 全 DIRECT ∧ 增量收益 ∧ 在 VERIFIABLE 集合。", "",
         "| Skill | 方向 | 计算器 | 金额语义 | 关键输入(有效口径) | 代理输入 | 可确认 |",
         "|---|---|---|---|---|---|---|"]
    for r in rows:
        inputs = "、".join(f"{f}:{v}" for f, v in r["inputs"].items())
        L.append(f"| {r['skill']} | {r['direction']} | {r['calc']} | {r['ctype']} | "
                 f"{inputs} | {'、'.join(r['proxies']) or '—'} | {'✅' if r['conf_capable'] else ''} |")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(L) + "\n", encoding="utf-8")

    conf = [r["skill"] for r in rows if r["conf_capable"]]
    inc_proxy = [r["skill"] for r in rows
                 if r["ctype"] == "INCREMENTAL_TAX_BENEFIT" and not r["all_direct"]]
    log.info("审计写入 %s", OUT)
    log.info("可确认（开箱）：%s", conf)
    log.info("增量收益但关键输入为代理（需直接证据）：%s", inc_proxy)


if __name__ == "__main__":
    main()
