"""Skill 数据依赖核验（Update 3.1）。

产出 `validation/skill_data_dependency.csv`：
    Skill → Required Fact → Profile Field → Data Source → 是否可计算
并标注 availability（AVAILABLE/PARTIAL/EXTERNAL_REQUIRED/MISSING）与
default_resolution（DIRECT/PROXY/INSUFFICIENT）/ qualification_proxy。

用法：python scripts/skill_data_dependency.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.common import CONFIG, ROOT, get_logger  # noqa: E402
from src.evidence.contract import (DOMAIN_FACT_TYPES, resolve_linked_field)  # noqa: E402
from src.evidence.sources import DERIVED_FIELDS, FIELD_SOURCES  # noqa: E402
from src.rules.fact_resolution import (is_qualification_proxy, proxy_of,  # noqa: E402
                                       resolution_of)
from src.skills.engine import get_skills  # noqa: E402

log = get_logger()
OUT = ROOT / "validation" / "skill_data_dependency.csv"


def _coverage() -> tuple[set[str], dict[str, float]]:
    """读取画像特征表，返回 (字段集合, 各字段非空率)。"""
    p = Path(CONFIG["paths"]["features_dir"]) / "profile_features.parquet"
    df = pd.read_parquet(p)
    cov = {c: float(df[c].notna().mean()) for c in df.columns}
    return set(df.columns), cov


def _source_kind(fact: str, linked: str | None, cols: set[str]) -> str:
    """判定事实来源类型：external/raw/derived/profile/unmapped。"""
    f = linked or fact
    if fact in DOMAIN_FACT_TYPES:
        return "external"
    if fact in FIELD_SOURCES or f in FIELD_SOURCES:
        return "raw"
    if fact in DERIVED_FIELDS or f in DERIVED_FIELDS:
        return "derived"
    if f in cols:
        return "profile"
    return "unmapped"


def _note(fact: str, linked: str | None) -> str:
    """生成字段说明（来源数据集/衍生公式/领域事实描述）。"""
    f = linked or fact
    if fact in DOMAIN_FACT_TYPES:
        return DOMAIN_FACT_TYPES[fact]
    if f in FIELD_SOURCES:
        did, dname, code, cn = FIELD_SOURCES[f]
        return f"{dname} · {code} · {cn}"
    if f in DERIVED_FIELDS:
        return f"衍生：{DERIVED_FIELDS[f]}"
    return ""


def main():
    """遍历所有 Skill 的必需事实，核验其可用性并落盘依赖表。"""
    cols, cov = _coverage()
    rows = []
    for sid, sk in get_skills().items():
        for fact in sk.required_facts:
            linked = resolve_linked_field(fact)
            f = linked or fact
            present = f in cols
            c = cov.get(f) if present else None
            kind = _source_kind(fact, linked, cols)
            # 可用性判定：外部依赖 > 缺失 > 覆盖≥80% 可用 > 有覆盖部分可用
            if kind == "external":
                availability = "EXTERNAL_REQUIRED"
            elif not present:
                availability = "MISSING"
            elif c is not None and c >= 0.8:
                availability = "AVAILABLE"
            elif c and c > 0:
                availability = "PARTIAL"
            else:
                availability = "MISSING"
            rows.append({
                "skill_id": sid,
                "direction": sk.direction,
                "fact": fact,
                "linked_field": linked or "",
                "source_kind": kind,
                "availability": availability,
                "coverage_2020_2024": round(c, 4) if c is not None else "",
                "default_resolution": resolution_of(f),
                "qualification_proxy": is_qualification_proxy(f),
                "proxy_of": proxy_of(f) or "",
                "note": _note(fact, linked),
            })
    OUT.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(OUT, index=False, encoding="utf-8-sig")
    n_av = sum(1 for r in rows if r["availability"] == "AVAILABLE")
    n_part = sum(1 for r in rows if r["availability"] == "PARTIAL")
    n_ext = sum(1 for r in rows if r["availability"] == "EXTERNAL_REQUIRED")
    n_miss = sum(1 for r in rows if r["availability"] == "MISSING")
    log.info("已写 %s（%s 条：AVAILABLE %s / PARTIAL %s / EXTERNAL %s / MISSING %s）",
             OUT, len(rows), n_av, n_part, n_ext, n_miss)


if __name__ == "__main__":
    main()
