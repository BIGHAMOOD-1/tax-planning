"""生成数据质检报告 + 字段血缘。用法: python scripts/build_quality.py

先跑数据质检（对账/覆盖/冲突），再重建字段级血缘，
两者产物共同支撑数据可追溯性核查。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.data.lineage import build_field_lineage  # noqa: E402
from src.data.quality import run as run_quality  # noqa: E402

if __name__ == "__main__":
    # 先质检后血缘：质检结果不依赖血缘，顺序仅为便于一次性产出全部报告
    run_quality()
    build_field_lineage()
