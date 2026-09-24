"""对现有画像执行低覆盖字段回填（Update 5.2）。

- 备份原文件为 `profile_features.parquet.bak`；
- 写入回填后的 `profile_features.parquet`（新增 `*_proxy` 列，不改变资格事实）；
- 输出 `data_processed/backfill_report.md`。

用法:
    python scripts/backfill_low_coverage.py            # 备份并回填
    python scripts/backfill_low_coverage.py --dry-run  # 只出报告，不写回
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402

from src.common import CONFIG, ROOT, get_logger  # noqa: E402

log = get_logger()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只生成报告，不写回画像")
    args = ap.parse_args()

    from src.data.backfill import apply_backfill, render_report

    path = Path(CONFIG["paths"]["features_dir"]) / "profile_features.parquet"
    if not path.exists():
        log.error("未找到画像文件：%s", path)
        sys.exit(1)

    prof = pd.read_parquet(path)
    before_cols = prof.shape[1]
    out, report = apply_backfill(prof)

    report_path = ROOT / "data_processed" / "backfill_report.md"
    report_path.write_text(render_report(report), encoding="utf-8")
    log.info("回填报告：%s", report_path)

    # 关键事实覆盖率台账
    from src.data.coverage import coverage_ledger, render_ledger
    ledger = coverage_ledger(out)
    ledger_path = ROOT / "data_processed" / "low_coverage_report.md"
    ledger_path.write_text(render_ledger(ledger), encoding="utf-8")
    low = [r["field"] for r in ledger if r["low"]]
    log.info("覆盖率台账：%s（低覆盖 %s 项：%s）", ledger_path, len(low), low)

    log.info("列数 %s -> %s", before_cols, out.shape[1])
    log.info("合并：%s", report.get("coalesced"))
    log.info("结构性0：%s", report.get("derived_zero"))
    log.info("代理：%s", {k: v["coverage"] for k, v in report.get("proxy", {}).items()})

    if args.dry_run:
        log.info("dry-run：未写回画像")
        return
    backup = path.with_suffix(".parquet.bak")
    if not backup.exists():
        shutil.copy2(path, backup)
        log.info("已备份：%s", backup)
    out.to_parquet(path, index=False)
    log.info("已写回画像：%s（%s 行 × %s 列）", path, *out.shape)


if __name__ == "__main__":
    main()
