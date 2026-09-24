"""数据使用台账：统计每张表/每个字段是否被下游使用。

输出：
    data_processed/field_usage.csv   逐字段
    data_processed/data_usage.md     按数据集汇总
"""
from __future__ import annotations

import glob
import os
import re
from pathlib import Path

import pandas as pd

from src.common import CONFIG, get_logger
from src.data.registry import discover_datasets
from src.evidence.sources import FIELD_SOURCES

log = get_logger()
ROOT = Path(CONFIG["paths"]["workspace"])
PROC = Path(CONFIG["paths"]["data_processed"])

# 下游模块目录（用于判断字段是否被引用）
MODULE_DIRS = {
    "skills": "src/skills",
    "rules": "src/rules",
    "agents": "src/agents",
    "evidence": "src/evidence",
    "derived": "src/derived",
    "report": "src/report",
    "pipeline": "src/pipeline",
}


def _module_texts() -> dict[str, str]:
    """拼接各下游模块（py/yaml）的全部文本，用于字段引用检测。"""
    texts = {}
    for name, rel in MODULE_DIRS.items():
        buf = []
        for f in glob.glob(str(ROOT / rel / "**" / "*"), recursive=True):
            if f.endswith((".py", ".yaml")):
                buf.append(Path(f).read_text(encoding="utf-8", errors="ignore"))
        texts[name] = "\n".join(buf)
    return texts


def build_usage() -> tuple[pd.DataFrame, pd.DataFrame]:
    """构建字段使用台账：逐字段是否被引用 + 按数据集汇总。

    返回 (字段明细表, 数据集汇总表)。
    """
    master = pd.read_parquet(PROC / "master.parquet")
    cols = [c for c in master.columns if c not in ("stock_code", "year")]
    texts = _module_texts()

    rows = []
    for c in cols:
        # 以词边界匹配字段名，降低子串误判
        used_by = [name for name, t in texts.items() if re.search(r"\b" + re.escape(c) + r"\b", t)]
        # 来源数据集
        src_ds = ""
        m = re.match(r"d(\d{6,})__", c)
        if m:
            src_ds = m.group(1)
        elif c in FIELD_SOURCES:
            src_ds = FIELD_SOURCES[c][0]
        rows.append({
            "column": c,
            "source_dataset": src_ds,
            "used": bool(used_by),
            "used_by": ",".join(used_by),
            "coverage": round(master[c].notna().mean(), 4),
        })
    field_df = pd.DataFrame(rows).sort_values(["used", "source_dataset", "column"],
                                              ascending=[False, True, True])

    # 按数据集汇总
    ds = {d.dataset_id: d.dataset_name for d in discover_datasets()}
    summary = []
    for did, name in sorted(ds.items()):
        cols_of = [r for r in rows if r["source_dataset"] == did]
        n_used = sum(1 for r in cols_of if r["used"])
        summary.append({
            "dataset_id": did, "dataset_name": name,
            "columns_in_master": len(cols_of),
            "columns_used": n_used,
            # 空数据集避免除零
            "usage_ratio": round(n_used / len(cols_of), 3) if cols_of else 0.0,
        })
    summary_df = pd.DataFrame(summary)
    return field_df, summary_df


def save_usage() -> None:
    """写出字段使用台账 CSV 与 Markdown 汇总。"""
    field_df, summary_df = build_usage()
    field_df.to_csv(PROC / "field_usage.csv", index=False, encoding="utf-8-sig")
    summary_df.to_csv(PROC / "data_usage.csv", index=False, encoding="utf-8-sig")

    total_cols = len(field_df)
    used_cols = int(field_df["used"].sum())
    lines = ["# 数据使用台账", "",
             f"- 主表字段总数：{total_cols}",
             f"- 被下游引用字段：{used_cols}（{used_cols/total_cols:.1%}）",
             f"- 未引用字段：{total_cols-used_cols}",
             f"- 涉及数据集：{len(summary_df)}",
             "",
             "## 各数据集字段使用情况", "",
             "| 数据集ID | 数据集 | 主表列数 | 被引用 | 使用率 |",
             "|---|---|---|---|---|"]
    for _, r in summary_df.iterrows():
        lines.append(f"| {r['dataset_id']} | {r['dataset_name']} | {r['columns_in_master']} | "
                     f"{r['columns_used']} | {r['usage_ratio']:.1%} |")
    lines += ["", "## 未被引用的字段", "",
              "| 字段 | 来源数据集 | 覆盖率 |", "|---|---|---|"]
    for _, r in field_df[~field_df["used"]].iterrows():
        lines.append(f"| {r['column']} | {r['source_dataset']} | {r['coverage']:.2f} |")
    (PROC / "data_usage.md").write_text("\n".join(lines), encoding="utf-8")
    log.info("台账写入 field_usage.csv / data_usage.csv / data_usage.md")


if __name__ == "__main__":
    save_usage()
