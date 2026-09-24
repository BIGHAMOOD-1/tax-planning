"""效果评估 v3 · M6 建议适用率（公平、非循环、零 LLM 成本）。

定义：系统给出的每一条**方向建议**，其**前置条件是否被该公司数据满足**。
    不满足 = 不适用 = 错。

前置条件**自动推导**（不手写中文、不人工判断）：
    由 Skill 的 `required_facts` 去掉通用字段后得到"方向特异字段"，
    只要其中任一字段在 2024 数据中存在且为正，即视为"适用"。

对比对象（复用 v3 缓存，**不调用任何大模型**）：
    B0' 大模型直答（给数据） / B1' 大模型+检索（给数据）
    B2 Deterministic System / B3 Full System

用法：python scripts/eval_v3_applicability.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

OUT = ROOT / "validation" / "eval_v3"
PROFILE = ROOT / "data_processed" / "features" / "profile_features.parquet"
POS = ROOT / "validation" / "ground_truth_2025.csv"
CTL = ROOT / "validation" / "control_group_2025.csv"

# 通用字段：几乎所有公司都有，不构成"方向特异前置条件"
GENERIC = {"nominal_tax_rate", "is_revenue", "is_total_profit", "is_income_tax",
           "is_tax_surcharge", "cf_tax_paid", "bs_tax_payable", "vat_rate", "industry_name"}


def _skills():
    from src.skills.engine import get_skills
    by_dir = {}
    for s in get_skills().values():
        by_dir.setdefault(s.direction, s)
    return by_dir


def _specific_fields(direction: str, by_dir: dict) -> list[str] | None:
    """方向的特异前置字段（去掉通用字段）。"""
    from src.agents.opportunity import canonical_direction
    s = by_dir.get(canonical_direction(direction))
    if not s:
        return None
    fs = [f for f in s.required_facts if f not in GENERIC]
    return fs or None


def _applicable(direction: str, row: dict, by_dir: dict) -> bool | None:
    """返回 True/False/None（None = 无法判定，不计入分母）。"""
    from src.agents.opportunity import canonical_direction
    cd = canonical_direction(direction)
    if cd == "亏损弥补":                       # 前置：本期亏损
        v = row.get("is_total_profit")
        return None if v is None else (float(v) < 0)
    fs = _specific_fields(direction, by_dir)
    if not fs:
        return None
    vals = [row.get(f) for f in fs if row.get(f) is not None]
    if not vals:
        return None
    return any(float(v) > 0 for v in vals)


def main() -> int:
    by_dir = _skills()
    # 收集所有需要的字段
    fields = set()
    for s in by_dir.values():
        fields |= set(s.required_facts)
    cols = ["stock_code", "year"] + sorted(f for f in fields)
    prof = pd.read_parquet(PROFILE, columns=cols)
    prof = prof[prof["year"] == 2024].copy()
    prof["stock_code"] = prof["stock_code"].astype(str).str.zfill(6)
    prof = prof.drop_duplicates("stock_code").set_index("stock_code")
    data = prof.to_dict(orient="index")

    pos = pd.read_csv(POS, dtype={"stock_code": str})
    ctl = pd.read_csv(CTL, dtype={"stock_code": str})
    groups = {"真阳组": pos, "对照组": ctl}

    systems = {"B0' 大模型直答(给数据)": "b0p", "B1' 大模型+政策检索(给数据)": "b1p",
               "B2 Deterministic System": "b2", "B3 Full System": "b3"}

    rows = []
    for label, key in systems.items():
        cache = json.loads((OUT / f"{key}.json").read_text(encoding="utf-8"))
        for grp, df in groups.items():
            judged = ok = unk = 0
            for _, s in df.iterrows():
                code = str(s["stock_code"]).zfill(6)
                row = data.get(code) or {}
                advice = (cache.get(code) or {}).get("advice") or []
                if key in ("b2", "b3"):        # 只评"推荐（已分析方案）"
                    advice = [a for a in advice if a.get("policy")]
                for a in advice:
                    r = _applicable(a.get("direction") or "", row, by_dir)
                    if r is None:
                        unk += 1
                    else:
                        judged += 1
                        ok += int(r)
            rows.append({"system": label, "group": grp, "advice": judged + unk,
                         "judged": judged, "unknown": unk, "applicable": ok,
                         "applicability": (ok / judged) if judged else None})

    out = pd.DataFrame(rows)
    out.to_csv(OUT / "applicability.csv", index=False, encoding="utf-8-sig")
    print(out.to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
