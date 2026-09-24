"""骨架构建与合并：以 股票代码 × 年份 左连接所有精选表。

流程：先由基本信息/财务指标构造唯一骨架，再依次左连接精选表与通用聚合表；
精选表走 `curated` 显式映射（口径可控），其余数据集走 `generic` 自动聚合（前缀 d<id>__）。
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.common import CONFIG, get_logger
from src.data.curated import CURATED
from src.data.loader import load_normalized
from src.data.normalize import filter_current_period
from src.data.registry import Dataset, discover_datasets

log = get_logger()
KEYS = ["stock_code", "year"]


def prepare(ds: Dataset) -> pd.DataFrame:
    """读取标准化表（不存在则生成）。"""
    return load_normalized(ds)


def build_skeleton(frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """骨架 = 基本信息年度表 ∪ 财务指标(合并报表)。"""
    bases = []
    if "044219154" in frames:
        bases.append(frames["044219154"][KEYS])
    if "212335916" in frames:
        bases.append(frames["212335916"][KEYS])
    if not bases:
        raise RuntimeError("缺少构建骨架所需的数据集（基本信息 / 财务指标）")
    # 合并两个来源的主键并去重，得到唯一的企业-年度骨架
    skel = pd.concat(bases, ignore_index=True).dropna().drop_duplicates()
    skel[KEYS] = skel[KEYS].astype({"stock_code": str})
    return skel.sort_values(KEYS).reset_index(drop=True)


def build_master() -> pd.DataFrame:
    """构建主表：骨架 + 精选表左连接 + 其余数据集通用聚合后并入。"""
    datasets = {d.dataset_id: d for d in discover_datasets()}
    curated_frames: dict[str, pd.DataFrame] = {}

    for did, handler in CURATED.items():
        ds = datasets.get(did)
        if ds is None:
            log.warning("精选映射引用了不存在的数据集 %s", did)
            continue
        try:
            prepared = prepare(ds)
            frame = handler(prepared)
            if frame is None or frame.empty:
                log.warning("数据集 %s 抽取结果为空", did)
                continue
            # 同一年份可能被两个数据集覆盖（如两版税项名义税率/政府补助），去重
            frame = frame.drop_duplicates(KEYS, keep="first")
            curated_frames[did] = frame
            log.info("精选 %s -> %s 行, %s 列", did, len(frame), len(frame.columns) - len(KEYS))
        except Exception as exc:  # noqa: BLE001
            # 单个数据集失败不阻断整体合并，记录异常后继续
            log.exception("处理数据集 %s 失败: %s", did, exc)

    master = build_skeleton(curated_frames)
    for did, frame in curated_frames.items():
        # 仅并入新列，避免与已有列重名导致 _x/_y 后缀
        new_cols = [c for c in frame.columns if c in KEYS or c not in master.columns]
        if len(new_cols) <= len(KEYS):
            log.warning("数据集 %s 的所有列已存在，跳过合并", did)
            continue
        master = master.merge(frame[new_cols], on=KEYS, how="left")

    # 其余数据集：通用聚合后并入（前缀 d<id>__）
    from src.data.generic import build_generic_frame
    merged = 0
    for did, ds in datasets.items():
        if did in curated_frames:
            continue
        # 覆盖率低于 2% 的通用字段价值有限，丢弃以控制主表宽度
        frame = build_generic_frame(ds, min_coverage=0.02)
        if frame is None or frame.empty:
            continue
        new_cols = [c for c in frame.columns if c in KEYS or c not in master.columns]
        if len(new_cols) <= len(KEYS):
            continue
        master = master.merge(frame[new_cols], on=KEYS, how="left")
        merged += 1
    log.info("通用并入数据集 %s 个，主表现有 %s 列", merged, master.shape[1])

    master["stock_code"] = master["stock_code"].astype(str)
    # 年度用可空整型 Int64，避免出现小数年份或强制转换报错
    master["year"] = master["year"].astype("Int64")
    return master


def save_master(master: pd.DataFrame) -> Path:
    """把主表写入 data_processed/master.parquet，返回路径。"""
    out = Path(CONFIG["paths"]["data_processed"]) / "master.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    master.to_parquet(out, index=False)
    log.info("主表写入 %s：%s 行 × %s 列", out, *master.shape)
    return out


def run_merge() -> pd.DataFrame:
    """构建主表并落盘（命令行入口调用）。"""
    master = build_master()
    save_master(master)
    return master


if __name__ == "__main__":
    run_merge()
