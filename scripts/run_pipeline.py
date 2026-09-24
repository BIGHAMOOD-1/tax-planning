"""一键运行数据处理流水线。

用法:
    python scripts/run_pipeline.py                 # A + B
    python scripts/run_pipeline.py --step A        # 仅标准化合并
    python scripts/run_pipeline.py --step B        # 仅衍生指标
    python scripts/run_pipeline.py --full-tables   # A 阶段额外输出全部表的标准化结果
"""
from __future__ import annotations

import argparse
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

# 允许直接以 python scripts/run_pipeline.py 运行
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from src.common import CONFIG, ensure_dirs, get_logger
from src.data import extract as extract_mod
from src.data import validate as validate_mod
from src.data.loader import load_normalized
from src.data.merge import run_merge
from src.data.registry import discover_datasets
from src.derived import benchmark, catalog as catalog_mod, cross, ratios, trend
from src.profile.build_profile import build_profile, save_profile

log = get_logger()
PROC = Path(CONFIG["paths"]["data_processed"])
LOG_RECORDS: list[dict] = []


def record(step: str, name: str, detail: str, rows=None, cols=None, seconds=None):
    """记录一条流水线运行日志（内存累积，最后统一写盘）。"""
    LOG_RECORDS.append({
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "step": step,
        "item": name,
        "detail": detail,
        "rows": rows,
        "cols": cols,
        "seconds": round(seconds, 1) if seconds is not None else None,
    })
    log.info("[%s] %s | %s", step, name, detail)


def step_a(full_tables: bool = False) -> pd.DataFrame:
    """A 阶段：清点数据集 -> 清单/字典 -> 标准化合并 -> 主表校验，返回主表。"""
    t0 = time.time()
    datasets = discover_datasets()
    record("A", "数据集清点", f"发现 {len(datasets)} 个数据集", rows=len(datasets))
    extract_mod.run()

    if full_tables:
        # 逐个数据集标准化并落盘（耗时较长，默认关闭）
        for i, ds in enumerate(datasets, 1):
            try:
                t = time.time()
                work = load_normalized(ds)
                record("A", f"标准化表 {ds.dataset_id}", ds.dataset_name,
                       rows=len(work), cols=work.shape[1], seconds=time.time() - t)
            except Exception as exc:  # noqa: BLE001
                record("A", f"标准化表 {ds.dataset_id}", f"失败: {exc}")
    else:
        record("A", "标准化表", "跳过全表输出（加 --full-tables 可输出）")

    master = run_merge()
    record("A", "合并主表", "master.parquet", rows=len(master), cols=master.shape[1],
           seconds=time.time() - t0)
    for issue in validate_mod.check_master(master):
        record("A", "主表检查", issue)
    validate_mod.write_report(master, PROC)
    return master


def step_b(master: pd.DataFrame | None = None) -> pd.DataFrame:
    """B 阶段：计算比值/交叉/趋势/分类/基准 -> 衍生指标 -> 画像特征，返回衍生表。"""
    t0 = time.time()
    if master is None:
        master = pd.read_parquet(PROC / "master.parquet")
        master["stock_code"] = master["stock_code"].astype(str)
    record("B", "读取主表", "master.parquet", rows=len(master), cols=master.shape[1])

    parts = []
    for name, mod in (("比值类", ratios), ("交叉计算", cross), ("趋势", trend)):
        t = time.time()
        part = mod.compute(master)
        parts.append(part)
        record("B", name, f"{part.shape[1] - 2} 个指标", rows=len(part), cols=part.shape[1],
               seconds=time.time() - t)

    # 按主键横向合并各模块产出（均以 stock_code+year 为键）
    derived = parts[0]
    for part in parts[1:]:
        derived = derived.merge(part, on=["stock_code", "year"], how="left")

    # 费用明细归类（管理/销售费用明细 -> 招待费/广告费/折旧摊销/职工薪酬/研发费…）
    try:
        from src.derived.expense_classify import compute_expense_classification
        exp = compute_expense_classification()
        if not exp.empty:
            derived = derived.merge(exp, on=["stock_code", "year"], how="left")
            record("B", "费用明细归类", f"{exp.shape[1] - 2} 个类别字段", rows=len(exp))
    except Exception as exc:  # noqa: BLE001
        # 单个可选模块失败不阻断整体流水线
        record("B", "费用明细归类", f"失败: {exc}")

    # 研发费用构成归类（人员人工/直接投入/折旧摊销/设计试验/其他 + 10%限额）
    try:
        from src.derived.rd_classify import compute_rd_classification
        rd = compute_rd_classification()
        if not rd.empty:
            derived = derived.merge(rd, on=["stock_code", "year"], how="left")
            record("B", "研发费用构成归类", f"{rd.shape[1] - 2} 个类别字段", rows=len(rd))
    except Exception as exc:  # noqa: BLE001
        record("B", "研发费用构成归类", f"失败: {exc}")

    t = time.time()
    # 基准类需同时依赖主表与已算出的衍生指标
    bench = benchmark.compute(master, derived)
    derived = derived.merge(bench, on=["stock_code", "year"], how="left")
    record("B", "基准", f"{bench.shape[1] - 2} 个指标", rows=len(bench), cols=bench.shape[1],
           seconds=time.time() - t)

    out = PROC / "derived" / "derived_metrics.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    derived.to_parquet(out, index=False)
    record("B", "衍生指标全集", "derived_metrics.parquet", rows=len(derived), cols=derived.shape[1])

    catalog_mod.save_catalog(PROC)
    record("B", "指标台账", f"{len(catalog_mod.REGISTRY)} 个指标定义写入 derived_catalog.*")

    prof = build_profile(master, derived)
    save_profile(prof)
    record("B", "画像特征", "profile_features.parquet", rows=len(prof), cols=prof.shape[1],
           seconds=time.time() - t0)
    return derived


def write_log():
    """把运行日志写为 CSV 与 Markdown 两种形式。"""
    if not LOG_RECORDS:
        return
    df = pd.DataFrame(LOG_RECORDS)
    out_csv = PROC / "pipeline_log.csv"
    df.to_csv(out_csv, index=False, encoding="utf-8-sig")
    lines = ["# 数据处理流水线运行记录", "",
             f"运行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", "",
             "| 时间 | 步骤 | 项目 | 说明 | 行数 | 列数 | 耗时(s) |",
             "|---|---|---|---|---|---|---|"]
    for r in LOG_RECORDS:
        lines.append(f"| {r['time']} | {r['step']} | {r['item']} | {r['detail']} | "
                     f"{r['rows'] if r['rows'] is not None else ''} | "
                     f"{r['cols'] if r['cols'] is not None else ''} | "
                     f"{r['seconds'] if r['seconds'] is not None else ''} |")
    (PROC / "pipeline_log.md").write_text("\n".join(lines), encoding="utf-8")


def main():
    """命令行入口：按 --step 执行 A/B/all，并保证日志最终落盘。"""
    ap = argparse.ArgumentParser()
    ap.add_argument("--step", choices=["A", "B", "all"], default="all")
    ap.add_argument("--full-tables", action="store_true")
    args = ap.parse_args()

    ensure_dirs()
    master = None
    try:
        if args.step in ("A", "all"):
            master = step_a(full_tables=args.full_tables)
        if args.step in ("B", "all"):
            # all 模式下复用 A 产出的主表，避免重复读盘
            step_b(master)
    except Exception:  # noqa: BLE001
        record("!", "异常", traceback.format_exc())
        log.error("流水线失败:\n%s", traceback.format_exc())
        raise
    finally:
        # 无论成败都写日志，便于事后排查
        write_log()


if __name__ == "__main__":
    main()
