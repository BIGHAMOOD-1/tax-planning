"""Tax Skill 执行引擎。

流程：读画像 -> 缺口检测 -> 资格判断(Eligibility) -> 计算(Calculation) -> 政策检索。
区分 eligibility 与 calculation 两层条件，并把"口径调整所需证据"与"计算假设"带出来。
"""
from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass, field
from difflib import SequenceMatcher

import pandas as pd

from src.common import abs_url, get_logger
from src.rules.calculator import run_calculator
from src.skills.schema import TaxSkill, load_all_skills

log = get_logger()

_PUNCT_RE = re.compile(r"[\s（）()\[\]【】、,，/与和及]+")


def _norm_evidence(text: str) -> str:
    return _PUNCT_RE.sub("", str(text))


def dedup_evidence(items: list[str], threshold: float = 0.85) -> list[str]:
    """证据清单去重：归一化后精确去重 + 近似文本（SequenceMatcher）去重。

    Skill 与 Calculator 常以相近措辞描述同一份材料（顿号 vs 斜杠、有无"研发"等），
    仅用 dict.fromkeys 无法去除，这里做近似去重。
    """
    out: list[str] = []
    norms: list[str] = []
    for it in items:
        s = str(it).strip()
        if not s:
            continue
        n = _norm_evidence(s)
        if not n:
            continue
        if n in norms:
            continue
        if any(SequenceMatcher(None, n, o).ratio() >= threshold for o in norms):
            continue
        norms.append(n)
        out.append(s)
    return out


def row_to_dict(row: pd.Series | dict) -> dict:
    src = row if isinstance(row, dict) else row.to_dict()
    out = {}
    for k, v in src.items():
        if v is None:
            out[k] = None
        elif isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
            out[k] = None
        else:
            out[k] = v
    return out


@dataclass
class SkillResult:
    skill_id: str
    name: str
    direction: str
    goal: str
    required_facts: list[str] = field(default_factory=list)
    data_gaps: list[str] = field(default_factory=list)
    conditions_passed: list[dict] = field(default_factory=list)
    conditions_failed: list[dict] = field(default_factory=list)
    conditions_unknown: list[dict] = field(default_factory=list)
    eligibility_failed: list[dict] = field(default_factory=list)
    eligibility_unknown: list[dict] = field(default_factory=list)
    calculation: dict | None = None
    policies: list[dict] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    evidence_required: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    amount_is_estimate: bool = False
    amount_requires_evidence: bool = False
    qualification_proxy_only: bool = False
    rag_queries: list[str] = field(default_factory=list)
    warnings: list[dict] = field(default_factory=list)

    @property
    def eligible(self) -> bool | None:
        if self.eligibility_failed:
            return False
        if self.eligibility_unknown:
            return None
        return bool(self.conditions_passed)

    def to_dict(self) -> dict:
        return asdict(self)


def evaluate_skill(skill: TaxSkill, profile: dict, kb=None, top_k: int = 3,
                   direct_fields: set[str] | None = None) -> SkillResult:
    res = SkillResult(skill_id=skill.id, name=skill.name, direction=skill.direction, goal=skill.goal)
    res.required_facts = list(skill.required_facts)

    # 1) 缺口检测
    for f in skill.required_facts:
        v = profile.get(f)
        if v is None or (isinstance(v, float) and math.isnan(v)):
            res.data_gaps.append(f)

    # 2) 条件判断（按阶段区分资格/计算）
    for cond in skill.conditions:
        ok, actual = cond.evaluate(profile)
        item = {"field": cond.field, "op": cond.op, "expect": cond.value,
                "actual": actual, "desc": cond.desc, "stage": cond.stage}
        if ok is True:
            res.conditions_passed.append(item)
        elif ok is False:
            res.conditions_failed.append(item)
            if cond.stage == "eligibility":
                res.eligibility_failed.append(item)
        else:
            res.conditions_unknown.append(item)
            if cond.stage == "eligibility":
                res.eligibility_unknown.append(item)

    # 资格类：eligibility 是否仅由 qualification_proxy / 资格类字段满足
    from src.rules.fact_resolution import is_qualification_field
    elig_passed = [c for c in res.conditions_passed if c.get("stage") == "eligibility"]
    elig_fields = [c.get("field") for c in elig_passed if c.get("field")]
    res.qualification_proxy_only = bool(elig_fields) and all(
        is_qualification_field(f) for f in elig_fields)

    # 3) 计算
    if skill.calculation:
        key_inputs = list(skill.calculation_core_inputs or skill.calculation_inputs)
        res.calculation = run_calculator(skill.calculation, profile, key_inputs=key_inputs,
                                         direct_fields=direct_fields, skill_id=skill.id)
        res.warnings = list((res.calculation or {}).get("warnings", []) or [])

    # 4) 口径调整所需证据 + 计算假设
    res.evidence_required = dedup_evidence(
        list(skill.evidence_required) + list((res.calculation or {}).get("evidence_required", [])))
    res.assumptions = (res.calculation or {}).get("assumptions", [])
    res.amount_is_estimate = bool((res.calculation or {}).get("amount_is_estimate", False))
    res.amount_requires_evidence = skill.amount_requires_evidence

    # 5) 政策检索
    res.rag_queries = skill.rag_queries
    if kb is not None:
        from src.rag.tax_filter import policy_filters
        pf = policy_filters(skill.direction)
        seen = set()
        for q in skill.rag_queries:
            for hit in kb.search(q, "policy", top_k=top_k, filters=pf):
                key = hit.get("doc_no") or hit.get("title")
                if key in seen:
                    continue
                seen.add(key)
                res.policies.append({
                    "title": hit.get("title"),
                    "doc_no": hit.get("doc_no"),
                    "date": hit.get("date"),
                    "channel": hit.get("channel"),
                    "url": abs_url(hit.get("url")),
                    "chunk_id": hit.get("chunk_id"),
                    "score": round(hit.get("score", 0.0), 4),
                    "excerpt": hit.get("text", "")[:300],
                    "text": hit.get("text", ""),
                })

    # 6) 风险
    res.risks = skill.risks

    # 7) 输入合理性校验（与计算 warnings 合并，不覆盖）
    from src.rules.validate import validate_inputs
    res.warnings = list(res.warnings) + list(validate_inputs(profile))
    return res


SKILLS = None


def get_skills() -> dict[str, TaxSkill]:
    global SKILLS
    if SKILLS is None:
        SKILLS = load_all_skills()
    return SKILLS
