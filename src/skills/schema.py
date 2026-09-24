"""Tax Skill 规范。

Skill 分两层：
1. **Eligibility（资格）**：企业是否适用该政策 —— 行业/资格/活动性质/负面清单等；
2. **Calculation（计算）**：在符合资格的前提下，哪些金额能算、如何算。

每个 Skill 的 8 要素：
1. 筹划目标 goal
2. 适用政策 policies
3. 所需企业事实 required_facts
4. 可选数据来源 data_sources
5. 判断条件 conditions（Eligibility）
6. 计算方法 calculation（含口径调整与所需证据）
7. 风险条件 risks
8. 输出格式 output_schema

关键原则：**会计口径 ≠ 税法口径**。
calculation.adjustments 描述从"会计口径金额"到"税法可加计口径金额"需要做的调整；
calculation.evidence_required 列出支撑该口径转换所需的证据（进入证据链与 Blue 审查）。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

from src.common import get_logger

log = get_logger()

DEFS_DIR = Path(__file__).resolve().parent / "definitions"


@dataclass
class Condition:
    field: str
    op: str            # ge, le, gt, lt, eq, ne, in, not_in, contains, exists, truthy
    value: Any = None
    desc: str = ""
    stage: str = "eligibility"   # eligibility | calculation

    def evaluate(self, row: dict) -> tuple[bool | None, Any]:
        """返回 (是否满足, 实际值)；数据缺失返回 (None, None)。"""
        actual = row.get(self.field)
        if actual is None or (isinstance(actual, float) and actual != actual):
            return None, None
        try:
            if self.op == "ge":
                return float(actual) >= float(self.value), actual
            if self.op == "le":
                return float(actual) <= float(self.value), actual
            if self.op == "gt":
                return float(actual) > float(self.value), actual
            if self.op == "lt":
                return float(actual) < float(self.value), actual
            if self.op == "eq":
                return actual == self.value, actual
            if self.op == "ne":
                return actual != self.value, actual
            if self.op == "in":
                return actual in self.value, actual
            if self.op == "not_in":
                return actual not in self.value, actual
            if self.op == "between":
                lo, hi = self.value
                return (float(lo) <= float(actual) <= float(hi)), actual
            if self.op == "contains":
                return str(self.value) in str(actual), actual
            if self.op == "exists":
                return True, actual
            if self.op == "truthy":
                return bool(actual), actual
        except (TypeError, ValueError):
            return None, actual
        return None, actual


@dataclass
class TaxSkill:
    id: str
    name: str
    direction: str
    goal: str
    policies: list[str] = field(default_factory=list)
    required_facts: list[str] = field(default_factory=list)
    data_sources: list[str] = field(default_factory=list)
    conditions: list[Condition] = field(default_factory=list)       # eligibility
    calculation: str = ""
    calculation_inputs: list[str] = field(default_factory=list)
    calculation_core_inputs: list[str] = field(default_factory=list)
    calculation_adjustments: list[str] = field(default_factory=list)
    evidence_required: list[str] = field(default_factory=list)
    amount_requires_evidence: bool = False
    risks: list[str] = field(default_factory=list)
    output_schema: dict = field(default_factory=dict)
    rag_queries: list[str] = field(default_factory=list)
    version: str = "1.0"
    effective_from: str = ""
    effective_to: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["conditions"] = [asdict(c) for c in self.conditions]
        return d


def load_skill(path: Path) -> TaxSkill:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))

    # Eligibility：优先 eligibility 段，向后兼容扁平 conditions/required_facts
    elig = raw.get("eligibility", {})
    elig_facts = elig.get("facts", raw.get("required_facts", []))
    elig_conds_raw = elig.get("conditions", raw.get("conditions", []))
    conditions = []
    for c in elig_conds_raw:
        c.setdefault("stage", "eligibility")
        conditions.append(Condition(**c))

    calc = raw.get("calculation", {})
    if isinstance(calc, str):          # 兼容 calculation: calculator_id
        calc = {"calculator": calc}
    calc_conds = calc.get("conditions", [])
    for c in calc_conds:
        c.setdefault("stage", "calculation")
        conditions.append(Condition(**c))

    calc_inputs = calc.get("facts", calc.get("inputs", []))
    calc_core = calc.get("core_inputs", calc_inputs)
    required_facts = list(dict.fromkeys(list(elig_facts) + list(calc_inputs)))

    return TaxSkill(
        id=raw["id"],
        name=raw["name"],
        direction=raw.get("direction", raw["name"]),
        goal=raw.get("goal", ""),
        policies=raw.get("policies", []),
        required_facts=required_facts,
        data_sources=raw.get("data_sources", []),
        conditions=conditions,
        calculation=calc.get("calculator", ""),
        calculation_inputs=calc_inputs,
        calculation_core_inputs=list(dict.fromkeys(calc_core)),
        calculation_adjustments=calc.get("adjustments", []),
        evidence_required=calc.get("evidence_required", []),
        amount_requires_evidence=bool(calc.get("amount_requires_evidence", False)),
        risks=raw.get("risks", []),
        output_schema=raw.get("output_schema", {}),
        rag_queries=raw.get("rag_queries", []),
        version=str(raw.get("version", "1.0")),
        effective_from=str(raw.get("effective_from", "")),
        effective_to=str(raw.get("effective_to", "")),
    )


def load_all_skills(defs_dir: Path | None = None) -> dict[str, TaxSkill]:
    defs_dir = defs_dir or DEFS_DIR
    skills: dict[str, TaxSkill] = {}
    for p in sorted(defs_dir.glob("*.yaml")):
        try:
            s = load_skill(p)
            skills[s.id] = s
        except Exception as exc:  # noqa: BLE001
            log.error("加载 Skill 失败 %s: %s", p.name, exc)
    log.info("已加载 %s 个 Tax Skill: %s", len(skills), list(skills))
    return skills


def match_skill_for_direction(direction: str, skills: dict[str, TaxSkill]) -> TaxSkill | None:
    d = direction.strip()
    for s in skills.values():
        if d in s.direction or s.direction in d or d == s.name or d in s.name:
            return s
    for s in skills.values():
        if any(k in d for k in s.direction.split("/")) and s.direction[:2] in d:
            return s
    return None


def skill_applicable(skill: TaxSkill, year: int) -> bool:
    """按政策生效区间判断 Skill 是否适用于该年度。"""
    try:
        y = int(year)
    except (TypeError, ValueError):
        return True
    for bound, is_from in ((skill.effective_from, True), (skill.effective_to, False)):
        if not bound:
            continue
        try:
            by = int(str(bound)[:4])
        except ValueError:
            continue
        if is_from and y < by:
            return False
        if (not is_from) and y > by:
            return False
    return True
