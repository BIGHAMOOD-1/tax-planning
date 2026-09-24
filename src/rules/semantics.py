"""计算器语义登记（Update 7.2 · P0-B）。

`config/calculator_semantics.yaml` 是"语义登记层"：只登记每个计算器的
`calculation_type / impact_direction / primary_output / economic_meaning / baseline / action / formula_role`，
**不做业务计算**。`run_calculator` 据此决定"金额的经济方向与含义"。

不变量：`confirmed_tax_impact` 仅来自 `CONFIRMED ∧ tax_reduce`，恒 ≥ 0；
        `tax_increase` 仅作风险提示，不进任何"确认"字段。
"""
from __future__ import annotations

from functools import lru_cache

import yaml

from src.common import ROOT, get_logger

log = get_logger()
_PATH = ROOT / "config" / "calculator_semantics.yaml"

# 未登记计算器时的方向默认（按金额语义推导）
_DEFAULT_DIRECTION = {
    "INCREMENTAL_TAX_BENEFIT": "tax_reduce",
    "SCENARIO_TAX": "tax_reduce",
    "TAX_SHIELD": "tax_reduce",
    "NO_CALCULATION": "none",
    "NOT_CALCULABLE": "none",
    "BASELINE_TAX": "none",
}


@lru_cache(maxsize=1)
def load_semantics() -> dict:
    try:
        data = yaml.safe_load(_PATH.read_text(encoding="utf-8")) or {}
        return data.get("calculators", {}) or {}
    except Exception as exc:  # noqa: BLE001
        log.warning("calculator_semantics 加载失败：%s", str(exc)[:120])
        return {}


def semantics_of(cid: str) -> dict:
    return load_semantics().get(str(cid), {}) or {}


def default_direction(calculation_type: str) -> str:
    return _DEFAULT_DIRECTION.get(str(calculation_type), "none")
