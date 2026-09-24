"""生成数据使用台账。用法: python scripts/build_usage.py"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.data.usage import save_usage  # noqa: E402

if __name__ == "__main__":
    save_usage()
