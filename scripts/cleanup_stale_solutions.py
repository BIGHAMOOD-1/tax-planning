"""清理过期案例：方案库中对应 plan 文件已不存在的条目。

背景：方案库（案例评价）保存的是历史快照；若某方向后来未被深度分析
（例如从"深度分析"降为"观察/候选"，对应 plan 文件被移除），
点「查看案例」会跳到空方向页。此脚本删除这类失效条目。

用法：
    python scripts/cleanup_stale_solutions.py --dry-run   # 只列出，不删除
    python scripts/cleanup_stale_solutions.py             # 真正删除
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.api import adapters  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只列出过期条目，不删除")
    args = ap.parse_args()

    sols = adapters.list_solutions()
    stale = []
    for s in sols:
        code = str(s.get("stock_code") or "").zfill(6)
        year = int(s.get("year") or 0)
        direction = str(s.get("direction") or "")
        if adapters.load_plan(code, year, direction) is None:
            stale.append(s)

    print(f"方案库 {len(sols)} 条，其中过期（无对应 plan）{len(stale)} 条：")
    for s in stale:
        sid = s.get("solution_id") or s.get("id")
        print(f"  {s.get('stock_code')} {s.get('year')} {s.get('direction')} "
              f"[{sid}] status={s.get('review_status')}")

    if args.dry_run:
        print("（dry-run，未删除）")
        return 0

    for s in stale:
        sid = s.get("solution_id") or s.get("id")
        adapters.delete_solution(sid)
    print(f"已删除 {len(stale)} 条过期案例。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
