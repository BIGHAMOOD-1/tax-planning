"""计算输入合理性校验：对异常口径做标注，避免"垃圾进垃圾出"。"""
from __future__ import annotations


def _num(row: dict, k: str):
    v = row.get(k)
    try:
        f = float(v)
        return f if f == f else None  # NaN check
    except (TypeError, ValueError):
        return None


def validate_inputs(profile: dict) -> list[dict]:
    warns: list[dict] = []

    def warn(field: str, message: str, level: str = "warning"):
        warns.append({"level": level, "field": field, "message": message})

    rev = _num(profile, "is_revenue")
    rd = _num(profile, "rd_spend_sum")
    if rd is not None and rev is not None and rev > 0 and rd > rev:
        warn("rd_spend_sum", "研发投入超过营业收入，口径可能异常，需核实")

    etr = _num(profile, "etr")
    if etr is not None and (etr > 1 or etr < -1):
        warn("etr", f"实际税率异常（{etr:.2%}），可能因亏损或口径问题")

    nominal = _num(profile, "nominal_tax_rate")
    if nominal is not None and (nominal > 100 or nominal < 0):
        warn("nominal_tax_rate", f"名义税率异常（{nominal}）")

    alr = _num(profile, "asset_liability_ratio")
    if alr is not None and alr > 1.5:
        warn("asset_liability_ratio", f"资产负债率异常高（{alr:.2%}），可能资不抵债")

    interest = _num(profile, "interest_expense_best")
    if interest is not None and interest < 0:
        warn("interest_expense_best", "利息费用为负（可能利息收入大于支出），需核实口径")

    comp = _num(profile, "employee_compensation_base")
    if comp is not None and comp < 0:
        warn("employee_compensation_base", "职工薪酬基数为负，口径异常")

    if rev is not None and rev <= 0:
        warn("is_revenue", "营业收入非正，多数比率指标不可用", level="info")

    return warns
