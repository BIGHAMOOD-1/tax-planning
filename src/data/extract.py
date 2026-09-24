"""清单与字段字典：数据集清单 + 字段代码 -> 中文名。

产出的 manifest.csv / dictionary.csv 是后续数据加载、字段翻译与血缘追溯的基础。
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.common import CONFIG, get_logger
from src.data.registry import Dataset, discover_datasets, read_des

log = get_logger()
PROC = Path(CONFIG["paths"]["data_processed"])


def build_manifest(datasets: list[Dataset] | None = None) -> pd.DataFrame:
    """生成数据集清单表（id/名称/压缩包/xlsx 入口/字段说明入口）。"""
    datasets = datasets or discover_datasets()
    rows = []
    for ds in datasets:
        rows.append({
            "dataset_id": ds.dataset_id,
            "dataset_name": ds.dataset_name,
            "zip_file": ds.zip_path.name,
            "xlsx_entry": ds.xlsx_entry or "",
            "des_entry": ds.des_entry or "",
        })
    return pd.DataFrame(rows)


def build_dictionary(datasets: list[Dataset] | None = None) -> pd.DataFrame:
    """生成字段字典表（数据集 × 字段代码 × 中文名），供字段翻译使用。"""
    datasets = datasets or discover_datasets()
    rows = []
    for ds in datasets:
        for code, cn in read_des(ds).items():
            rows.append({
                "dataset_id": ds.dataset_id,
                "dataset_name": ds.dataset_name,
                "column_code": code,
                "column_cn": cn,
            })
    return pd.DataFrame(rows)


def run() -> tuple[Path, Path]:
    """构建并落盘清单与字典（UTF-8-SIG 便于 Excel 正确识别中文）。"""
    ds = discover_datasets()
    man = build_manifest(ds)
    dic = build_dictionary(ds)
    p1 = PROC / "manifest.csv"
    p2 = PROC / "dictionary.csv"
    man.to_csv(p1, index=False, encoding="utf-8-sig")
    dic.to_csv(p2, index=False, encoding="utf-8-sig")
    log.info("数据集清单 %s 行 -> %s", len(man), p1)
    log.info("字段字典 %s 行 -> %s", len(dic), p2)
    return p1, p2


if __name__ == "__main__":
    run()
