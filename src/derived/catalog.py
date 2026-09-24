"""指标台账：登记每个衍生指标的定义。"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path

import pandas as pd

REGISTRY: list["MetricDef"] = []


@dataclass
class MetricDef:
    """单个衍生指标的定义（名称、中文名、类别、公式、输入字段、单位、来源与备注）。"""
    name: str
    cn_name: str
    category: str
    formula: str
    inputs: str
    unit: str = ""
    source_tables: str = ""
    note: str = ""


def register(name: str, cn_name: str, category: str, formula: str, inputs: str,
             unit: str = "", source_tables: str = "", note: str = "") -> str:
    """登记指标定义；同名指标只登记一次（幂等），返回指标名。"""
    if not any(m.name == name for m in REGISTRY):
        REGISTRY.append(MetricDef(name, cn_name, category, formula, inputs, unit, source_tables, note))
    return name


def catalog_df() -> pd.DataFrame:
    """把台账转为 DataFrame，便于导出 CSV/Markdown。"""
    return pd.DataFrame([asdict(m) for m in REGISTRY])


def save_catalog(out_dir: str | Path) -> None:
    """导出台账为 derived_catalog.csv 与 derived_catalog.md。"""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    df = catalog_df()
    df.to_csv(out_dir / "derived_catalog.csv", index=False, encoding="utf-8-sig")
    lines = ["# 衍生指标台账", "", f"共 {len(df)} 个指标。", "",
             "| 指标名 | 中文名 | 类别 | 公式 | 输入字段 | 单位 | 来源表 | 备注 |",
             "|---|---|---|---|---|---|---|---|"]
    for _, r in df.iterrows():
        lines.append(f"| {r['name']} | {r['cn_name']} | {r['category']} | {r['formula']} | "
                     f"{r['inputs']} | {r['unit']} | {r['source_tables']} | {r['note']} |")
    (out_dir / "derived_catalog.md").write_text("\n".join(lines), encoding="utf-8")
