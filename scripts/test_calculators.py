"""Skill 两层测试（Update 3.1）。

产出 `validation/skill_test_cases.csv`：

第一层 Calculator Test：输入 → 计算结果
    normal / zero / missing / extreme
第二层 Skill Decision Test：Facts → Conditions → Resolution → Calculator → Decision
    normal / missing / condition_fail

用法：python scripts/test_calculators.py
"""
from __future__ import annotations

import copy
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.common import ROOT, get_logger  # noqa: E402
from src.evidence.pool import build_pool  # noqa: E402
from src.pipeline.plan_pipeline import load_profile  # noqa: E402
from src.rules.calculator import run_calculator  # noqa: E402
from src.skills.engine import evaluate_skill, get_skills  # noqa: E402
from src.agents import green  # noqa: E402

log = get_logger()
OUT = ROOT / "validation" / "skill_test_cases.csv"
BASE_STOCK = "600004"
NUM_KEYS = ("is_revenue", "bs_total_assets", "rd_spend_sum", "is_total_profit", "is_income_tax",
            "bs_fixed_assets", "cf_capex", "interest_expense_best", "gov_subsidy_total",
            "bs_retained_earnings", "cf_tax_paid", "bs_tax_payable", "is_tax_surcharge",
            "employee_compensation_base", "bs_accounts_receivable", "bs_intangible_assets",
            "is_investment_income", "expense_share_based", "bs_cash", "bs_cip")


def _variant(base: dict, key_inputs: list[str], mode: str) -> dict:
    """基于基准画像构造变体输入：missing/zero/extreme。"""
    r = copy.deepcopy(base)
    if mode == "missing":
        # 置空关键输入，验证缺失时的降级行为
        for k in key_inputs:
            r[k] = None
    elif mode == "zero":
        # 归零，验证除零/零值边界
        for k in key_inputs:
            if isinstance(r.get(k), (int, float)):
                r[k] = 0.0
    elif mode == "extreme":
        # 放大 1e6，验证极端量级不崩溃
        for k in key_inputs:
            if isinstance(r.get(k), (int, float)):
                r[k] = float(r[k]) * 1e6
    return r


def _calc_case(cid: str, key_inputs: list[str], base: dict, mode: str) -> dict:
    """第一层 Calculator Test：直接调用计算器，按模式判定是否通过。"""
    row = _variant(base, key_inputs, mode)
    rec = {"layer": "calculator", "skill_id": "", "calculator": cid, "case": mode}
    try:
        res = run_calculator(cid, row, key_inputs=key_inputs) or {}
        ok = bool(res.get("ok"))
        impact = res.get("impact_type")
        rec.update(ok=ok, impact_type=impact or "",
                   not_calculable=",".join(res.get("not_calculable_inputs") or []),
                   proxy=",".join(x["field"] for x in (res.get("proxy_inputs") or [])),
                   error="")
        if mode in ("normal", "zero", "extreme"):
            # 这三种模式只要不抛异常即通过
            rec["pass"] = True
        elif mode == "missing":
            # 缺失关键输入时，应明确不可算（ok=False 或 NOT_CALCULABLE）
            rec["pass"] = (not ok) or impact == "NOT_CALCULABLE"
        else:
            rec["pass"] = True
    except Exception as exc:  # noqa: BLE001
        rec.update(ok="", impact_type="", not_calculable="", proxy="",
                   error=str(exc)[:120])
        rec["pass"] = False
    return rec


def _fail_row(base: dict, skill) -> dict | None:
    """构造"条件明确失败"的输入行。"""
    for c in skill.conditions:
        if c.stage != "eligibility":
            continue
        r = copy.deepcopy(base)
        try:
            if c.op in ("gt", "ge"):
                r[c.field] = float(c.value) - 1
            elif c.op in ("lt", "le"):
                r[c.field] = float(c.value) + 1
            elif c.op == "eq":
                r[c.field] = "___none___"
            elif c.op == "not_in":
                r[c.field] = (c.value or ["___"])[0]
            elif c.op == "in":
                r[c.field] = "___none___"
            else:
                continue
            return r
        except (TypeError, ValueError):
            continue
    return None


def _decision(skill, row: dict) -> tuple[str, str]:
    """跑完整决策链（条件评估 -> 数据池 -> 绿方决策），返回 (结论, 金额语义)。"""
    sd = evaluate_skill(skill, row, kb=None).to_dict()
    pool = build_pool(skill.direction, sd, row, kb=None)
    g = green.decide(skill.direction, pool, {}, {}, use_llm=False)
    calc = sd.get("calculation") or {}
    return g.get("decision", ""), calc.get("impact_type", "")


def _skill_case(skill, base: dict, mode: str) -> dict:
    """第二层 Skill Decision Test：normal/missing/condition_fail 三态。"""
    rec = {"layer": "skill_decision", "skill_id": skill.id, "calculator": skill.calculation,
           "case": mode}
    try:
        if mode == "normal":
            row = copy.deepcopy(base)
        elif mode == "missing":
            # 清空条件字段（无则退回必需事实），应给出"证据不足/需人工确认"
            row = copy.deepcopy(base)
            fields = [c.field for c in skill.conditions] or skill.required_facts
            for f in fields:
                row[f] = None
        elif mode == "condition_fail":
            # 构造条件明确失败的输入；无可失败条件则跳过
            row = _fail_row(base, skill)
            if row is None:
                return {**rec, "decision": "SKIPPED", "impact_type": "", "pass": True,
                        "error": "无可用 eligibility 条件"}
        else:
            row = copy.deepcopy(base)
        decision, impact = _decision(skill, row)
        rec.update(decision=decision, impact_type=impact, error="")
        if mode == "condition_fail":
            rec["pass"] = decision == "不推荐"
        elif mode == "missing":
            rec["pass"] = decision in ("证据不足", "需要专业人员进一步确认")
        else:
            # normal 模式允许任一合法结论
            rec["pass"] = decision in ("推荐", "有条件推荐", "证据不足",
                                       "需要专业人员进一步确认", "不推荐")
    except Exception as exc:  # noqa: BLE001
        rec.update(decision="", impact_type="", error=str(exc)[:120])
        rec["pass"] = False
    return rec


def main():
    """运行全部 Skill 的两层测试并落盘 validation/skill_test_cases.csv。"""
    base = load_profile(BASE_STOCK, 2024)
    rows = []
    for sid, sk in get_skills().items():
        if not sk.calculation:
            continue
        keys = list(sk.calculation_core_inputs or sk.calculation_inputs)
        # 第一层：4 种输入模式
        for mode in ("normal", "zero", "missing", "extreme"):
            r = _calc_case(sk.calculation, keys, base, mode)
            r["skill_id"] = sid
            rows.append(r)
        # 第二层：3 种场景
        for mode in ("normal", "missing", "condition_fail"):
            rows.append(_skill_case(sk, base, mode))
    df = pd.DataFrame(rows)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT, index=False, encoding="utf-8-sig")
    n = len(df)
    npass = int(df["pass"].sum())
    log.info("已写 %s（%s 用例，通过 %s，失败 %s）", OUT, n, npass, n - npass)
    bad = df[~df["pass"].astype(bool)]
    if not bad.empty:
        log.warning("未通过用例：\n%s", bad[["layer", "skill_id", "case", "decision", "impact_type"]]
                    .to_string(index=False))


if __name__ == "__main__":
    main()
