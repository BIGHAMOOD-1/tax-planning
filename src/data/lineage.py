"""数据血缘层：字段级来源追溯。

链路：字段 -> 来源数据集(dataset_id/xlsx) -> 标准化表 -> 过滤规则 -> 聚合方式。
落盘 data_processed/lineage/field_lineage.json；结论级由 plan.lineage.data_lineage 引用。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pandas as pd

from src.common import CONFIG, get_logger
from src.data.registry import discover_datasets, load_registry
from src.evidence.sources import DERIVED_FIELDS, FIELD_SOURCES

log = get_logger()
PROC = Path(CONFIG["paths"]["data_processed"])
LINEAGE_DIR = PROC / "lineage"
LINEAGE_FILE = LINEAGE_DIR / "field_lineage.json"


def _registry_index() -> dict[str, dict]:
    """加载数据集注册表（dataset_id -> 元信息），用于补充 shape 等描述。"""
    return load_registry()


def _dataset_index() -> dict[str, dict]:
    """构建 dataset_id -> {名称/xlsx 入口/压缩包} 的索引，便于写血缘时补充来源。"""
    return {d.dataset_id: {"name": d.dataset_name, "xlsx": d.xlsx_entry,
                           "zip": d.zip_path.name} for d in discover_datasets()}


def build_field_lineage() -> dict:
    """扫描主表列并生成字段级血缘，落盘 field_lineage.json 后返回。

    血缘分三类：raw（直接来自某数据集）、curated（精选映射）、derived（衍生公式）、unknown。
    """
    master = pd.read_parquet(PROC / "master.parquet")
    reg = _registry_index()
    dss = _dataset_index()
    out: dict[str, dict] = {}

    for col in master.columns:
        # 主键不参与血缘
        if col in ("stock_code", "year"):
            continue
        # 通用聚合列命名约定 d<dataset_id>__<原字段代码>
        m = re.match(r"^d(\d{6,})__(.+)$", col)
        if m:
            did, code = m.group(1), m.group(2)
            r = reg.get(did, {})
            out[col] = {
                "type": "raw",
                "dataset_id": did,
                "dataset_name": dss.get(did, {}).get("name", r.get("name", "")),
                "xlsx_entry": dss.get(did, {}).get("xlsx", ""),
                "normalized_table": f"data_processed/tables/{did}.parquet",
                "column_code": code,
                "shape": r.get("shape", ""),
                # 标准化表统一施加的口径过滤
                "filters": ["report_type=合并", "period=本期", "row_type=item"],
                "aggregation": "sum",
            }
            continue
        # 精选映射字段：来源信息来自 FIELD_SOURCES
        if col in FIELD_SOURCES:
            did, dname, code, cn = FIELD_SOURCES[col]
            r = reg.get(did, {})
            out[col] = {
                "type": "raw",
                "dataset_id": did,
                "dataset_name": dname,
                "xlsx_entry": dss.get(did, {}).get("xlsx", ""),
                "normalized_table": f"data_processed/tables/{did}.parquet",
                "column_code": code,
                "column_cn": cn,
                "shape": r.get("shape", ""),
                "filters": ["report_type=合并", "period=本期", "row_type=item"],
                "aggregation": "curated",
            }
            continue
        # 衍生字段：记录计算公式
        if col in DERIVED_FIELDS:
            out[col] = {"type": "derived", "formula": DERIVED_FIELDS[col]}
            continue
        # 无法归类：显式标记 unknown，便于后续补血缘
        out[col] = {"type": "unknown"}

    LINEAGE_DIR.mkdir(parents=True, exist_ok=True)
    LINEAGE_FILE.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("字段血缘写入 %s（%s 个字段）", LINEAGE_FILE, len(out))
    return out


_CACHE: dict | None = None


def get_field_lineage() -> dict:
    """获取字段血缘：优先读缓存文件，不存在则实时构建（进程内缓存）。"""
    global _CACHE
    if _CACHE is None:
        if LINEAGE_FILE.exists():
            _CACHE = json.loads(LINEAGE_FILE.read_text(encoding="utf-8"))
        else:
            _CACHE = build_field_lineage()
    return _CACHE


def lineage_for(fields: list[str]) -> list[dict]:
    """批量查询指定字段的血缘；未知字段回退为 type=unknown。"""
    lin = get_field_lineage()
    return [{"field": f, **(lin.get(f) or {"type": "unknown"})} for f in fields]


if __name__ == "__main__":
    build_field_lineage()
