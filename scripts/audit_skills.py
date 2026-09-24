"""Tax Skill 全量审查（Update 3.1，粗判断）。

产出 `validation/skill_audit.csv`：
    自动列 + 启发式初判（由代码+政策给出）+ 人工复核列。
目标：发现"离谱"错误，不追求穷尽。

依赖：先运行 scripts/skill_data_dependency.py。

用法：python scripts/audit_skills.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.common import ROOT, get_logger  # noqa: E402
from src.skills.engine import get_skills  # noqa: E402

log = get_logger()
DEP = ROOT / "validation" / "skill_data_dependency.csv"
OUT = ROOT / "validation" / "skill_audit.csv"


def _conclusion(calc: str, stats: dict) -> str:
    """按依赖统计给出初判结论（粗判断，供人工复核参考）。"""
    if not calc:
        return "REVIEW_REQUIRED"          # 无确定性计算器，需专业判断
    if stats.get("missing", 0) > 0 or stats.get("external", 0) > 0:
        return "DATA_GAP"                 # 关键事实缺失/需外部证据
    if stats.get("proxy", 0) > 0:
        return "CONDITIONAL"              # 含代理输入，只能情景测算
    return "READY"


def main():
    """遍历全部 Skill，结合数据依赖统计生成审查表并落盘。"""
    skills = get_skills()
    # 依赖表缺失时用空表兜底，保证脚本仍可产出（全部按无依赖处理）
    dep = pd.read_csv(DEP) if DEP.exists() else pd.DataFrame(
        columns=["skill_id", "availability", "default_resolution"])

    rows = []
    for sid, sk in skills.items():
        d = dep[dep["skill_id"] == sid] if not dep.empty else dep
        # 统计该 Skill 依赖字段的可用性分布与代理情况
        stats = {
            "available": int((d["availability"] == "AVAILABLE").sum()) if not d.empty else 0,
            "partial": int((d["availability"] == "PARTIAL").sum()) if not d.empty else 0,
            "external": int((d["availability"] == "EXTERNAL_REQUIRED").sum()) if not d.empty else 0,
            "missing": int((d["availability"] == "MISSING").sum()) if not d.empty else 0,
            "proxy": int((d["default_resolution"] == "PROXY").sum()) if not d.empty else 0,
            "qual": int(d["qualification_proxy"].astype(bool).sum()) if not d.empty else 0,
        }
        elig_conds = [c for c in sk.conditions if c.stage == "eligibility"]
        has_goal = bool(str(sk.goal or "").strip())
        has_elig = bool(elig_conds)
        conclusion = _conclusion(sk.calculation, stats)
        # 适用场景完整度：目标与准入条件都有=明确，只有目标=部分，其余=缺失
        scene = "明确" if (has_goal and has_elig) else ("部分" if has_goal else "缺失")
        caliber = "含代理输入（见依赖表）" if stats["proxy"] > 0 else "无明显代理输入"
        risk = "已列" if sk.risks else "缺失"
        rows.append({
            "skill_id": sid,
            "name": sk.name,
            "direction": sk.direction,
            "version": sk.version,
            "effective_from": sk.effective_from,
            "effective_to": sk.effective_to,
            "has_goal": has_goal,
            "policies_n": len(sk.policies),
            "required_facts_n": len(sk.required_facts),
            "conditions_n": len(sk.conditions),
            "calc": sk.calculation or "",
            "evidence_required_n": len(sk.evidence_required),
            "risks_n": len(sk.risks),
            "rag_queries_n": len(sk.rag_queries),
            "facts_available_n": stats["available"],
            "facts_partial_n": stats["partial"],
            "facts_external_n": stats["external"],
            "facts_missing_n": stats["missing"],
            "has_proxy_input": stats["proxy"] > 0,
            "has_qualification_proxy": stats["qual"] > 0,
            "初判_适用场景": scene,
            "初判_口径": caliber,
            "初判_风险": risk,
            "初判_结论": conclusion,
            "备注": "",
            "人工复核": "",
        })
    OUT.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(OUT, index=False, encoding="utf-8-sig")
    from collections import Counter
    # 输出初判结论分布，便于快速了解整体健康度
    dist = Counter(r["初判_结论"] for r in rows)
    log.info("已写 %s（%s 个 Skill；初判分布：%s）", OUT, len(rows), dict(dist))


if __name__ == "__main__":
    main()
