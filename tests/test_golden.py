"""Golden Cases（Update 7.2 · P0-E）：锁死系统最核心的 5 种业务语义。

这些用例断言的是"业务语义"（方向 / 依据 / 状态），而非覆盖率。
运行：pytest tests/test_golden.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.rules.calculator import run_calculator  # noqa: E402
from src.report.view_model import status_key  # noqa: E402


def test_golden_A_incremental_confirmed_tax_reduce():
    """A：费用扣除（合规归类规避调增）→ tax_reduce → CONFIRMED，且金额 ≥0。"""
    row = {"is_admin_expense": 3e8, "is_selling_expense": 2e8, "is_finance_expense": 1e7,
           "is_revenue": 1e10, "expense_entertainment": 8.6e7, "expense_advertising": 2e8,
           "nominal_tax_rate": 25.0}
    keys = ["is_admin_expense", "is_selling_expense", "is_finance_expense", "is_revenue"]
    r = run_calculator("expense_deduction_limit", row, key_inputs=keys, skill_id="expense_deduction_limit")
    assert r["calculation_type"] == "INCREMENTAL_TAX_BENEFIT"
    assert r["direction"] == "tax_reduce"
    assert r["calculation_status"] == "CONFIRMED"
    assert r["confirmed_tax_impact"] is not None and r["confirmed_tax_impact"] >= 0
    assert r.get("impact_basis") and r.get("baseline") and r.get("action")
    assert r.get("caveat")  # 广告费可结转=时间性，须提示


def test_golden_B_asset_impairment_not_confirmed():
    """B：资产减值（会计计提≠可扣损失）→ 不得 CONFIRMED（已移出 VERIFIABLE）。"""
    row = {"is_asset_impairment": 1e7, "is_credit_impairment": 5e6, "nominal_tax_rate": 25.0}
    keys = ["is_asset_impairment", "is_credit_impairment", "nominal_tax_rate"]
    r = run_calculator("asset_impairment", row, key_inputs=keys, skill_id="asset_impairment")
    assert r["direction"] == "tax_reduce"
    assert r["confirmed_tax_impact"] is None
    assert r["calculation_status"] != "CONFIRMED"


def test_golden_C_tax_shield_never_incremental():
    """C：税盾（折旧）→ 既非确认收益，也非情景收益；单独呈现。"""
    row = {"bs_fixed_assets": 5e9, "fa_accum_dep_increase": 4e8, "nominal_tax_rate": 25.0}
    keys = ["bs_fixed_assets", "fa_accum_dep_increase", "nominal_tax_rate"]
    r = run_calculator("depreciation_amortization", row, key_inputs=keys, skill_id="depreciation_amortization")
    assert r["calculation_type"] == "TAX_SHIELD"
    assert r["confirmed_tax_impact"] is None
    assert r["scenario_tax_impact"] is None
    assert r.get("shield_amount") is not None and r["shield_amount"] >= 0


def test_golden_D_no_calculation_can_be_confirmed_without_amount():
    """D：无需金额计算的"确认性结论"（资格/合规）→ 可 CONFIRMED，但无金额。"""
    row = {"interest_expense_best": 1e7, "interest_debt_to_equity": 3.0, "nominal_tax_rate": 25.0}
    keys = ["interest_expense_best", "interest_debt_to_equity", "nominal_tax_rate"]
    r = run_calculator("interest_deduction", row, key_inputs=keys, skill_id="interest_deduction")
    assert r["calculation_status"] == "NO_CALCULATION"
    assert r["confirmed_tax_impact"] is None
    # 结论态：确认性结论（无金额）也算 CONFIRMED
    assert status_key("RECOMMEND", "NO_CALCULATION") == "CONFIRMED"


def test_golden_E_missing_direct_evidence_is_data_gap():
    """E：缺关键 DIRECT 证据 → NOT_CALCULABLE（DATA_GAP），不得产出确认影响。"""
    row = {"is_admin_expense": 1e8, "is_selling_expense": 1e8, "is_finance_expense": 1e7,
           "is_revenue": None, "nominal_tax_rate": 25.0}
    keys = ["is_admin_expense", "is_selling_expense", "is_finance_expense", "is_revenue"]
    r = run_calculator("expense_deduction_limit", row, key_inputs=keys, skill_id="expense_deduction_limit")
    assert r["ok"] is False
    assert r["calculation_status"] == "NOT_CALCULABLE"
    assert r["confirmed_tax_impact"] is None
    assert status_key("INSUFFICIENT_EVIDENCE", "NOT_CALCULABLE") == "DATA_GAP"
