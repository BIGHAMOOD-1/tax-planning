"""通用 xlsx 读取器。

处理要点：
1. xlsx 前两行为脏表头（第2行中文名、第3行单位），数据从第4行开始。
   采用「按主键形态自动跳过」的稳健策略。
2. 大表使用 openpyxl read_only 流式读取，仅保留目标年份的行。
3. 只保留年报（月=12），并按 (股票代码, 年份) 去重取最新。
4. 结果缓存为 parquet。
"""
from __future__ import annotations

import io
import re
import zipfile
from datetime import datetime
from pathlib import Path

import pandas as pd
from openpyxl import load_workbook

from src.common import CONFIG, get_logger, norm_col, norm_stock_code
from src.data.registry import (
    CODE_ALIASES,
    DATE_ALIASES,
    Dataset,
    PERIOD_ALIASES,
    REPORT_TYPE_ALIASES,
    SOURCE_ALIASES,
)

log = get_logger()

PROC_DIR = Path(CONFIG["paths"]["data_processed"])
TABLES_DIR = Path(CONFIG["paths"]["tables_dir"])      # 标准化结果
RAW_CACHE_DIR = PROC_DIR / "_raw_cache"               # 原始读取缓存
YEARS = set(CONFIG["params"]["years"])


def _find_idx(header_lower: list[str], aliases: list[str]) -> int | None:
    """在归一化表头中按别名顺序定位列下标，未命中返回 None。"""
    for alias in aliases:
        a = norm_col(alias)
        if a in header_lower:
            return header_lower.index(a)
    return None


def _parse_year(value) -> int | None:
    """从日期/时间戳/字符串中解析 4 位年份；无法解析返回 None。"""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.year
    if isinstance(value, pd.Timestamp):
        return value.year
    s = str(value).strip()
    # 取字符串开头的 4 位数字作为年份
    m = re.match(r"^(\d{4})", s)
    if m:
        y = int(m.group(1))
        return y
    return None


def _parse_month(value) -> int | None:
    """从日期/字符串中解析月份；无法解析返回 None。"""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.month
    if isinstance(value, pd.Timestamp):
        return value.month
    s = str(value).strip()
    m = re.match(r"^\d{4}[-/](\d{1,2})", s)
    if m:
        return int(m.group(1))
    return None


def read_dataset(
    ds: Dataset,
    years: set[int] | None = None,
    drop_header_rows: bool = True,
) -> pd.DataFrame:
    """流式读取一个数据集，返回清洗到目标年份的 DataFrame（保留原始列名）。"""
    years = years or YEARS
    if not ds.xlsx_entry:
        raise ValueError(f"{ds.dataset_id} 无 xlsx")

    with zipfile.ZipFile(ds.zip_path) as z:
        data = z.read(ds.xlsx_entry)
    # read_only + data_only：流式读且取计算后的值，避免加载整表与公式
    wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    ws = wb[wb.sheetnames[0]]
    # 这些 xlsx 缺少正确的 dimension 信息，read_only 下会被截断成 1 列，需要重置
    try:
        ws.reset_dimensions()
    except Exception:  # noqa: BLE001
        pass

    rows = ws.iter_rows(values_only=True)
    try:
        header = next(rows)
    except StopIteration:
        wb.close()
        return pd.DataFrame()

    header = list(header)
    header_lower = [norm_col(h) for h in header]
    code_idx = _find_idx(header_lower, CODE_ALIASES)
    date_idx = _find_idx(header_lower, DATE_ALIASES)
    if code_idx is None and date_idx is None:
        # 无法识别主键，退化为读前若干行
        code_idx = 0

    kept: list[tuple] = []
    for row in rows:
        if row is None:
            continue
        code = row[code_idx] if code_idx is not None and code_idx < len(row) else None
        # 主键位无法解析为合法股票代码即丢弃，可自动过滤脏表头/单位行/汇总行
        if code is None or norm_stock_code(code) is None:
            continue  # 跳过中文名行 / 单位行 / 空行 / 汇总行
        if date_idx is not None and date_idx < len(row):
            year = _parse_year(row[date_idx])
            if year is None:
                continue
            # 仅保留配置年份范围内的行，显著减小后续处理量
            if year not in years:
                continue
        kept.append(row)
    wb.close()

    if not kept:
        return pd.DataFrame(columns=header)

    cols = _dedupe_columns(header)
    ncol = len(cols)
    # openpyxl 会省略行尾空单元格，导致行长短不一，需补齐/截断
    fixed = []
    for r in kept:
        r = tuple(r)
        if len(r) < ncol:
            r = r + (None,) * (ncol - len(r))
        elif len(r) > ncol:
            r = r[:ncol]
        fixed.append(r)
    df = pd.DataFrame(fixed, columns=cols)
    return df


def _dedupe_columns(header: list) -> list[str]:
    """重名列去重：空表头记为 unnamed，重名追加 __N 后缀，保证 DataFrame 列名唯一。"""
    seen: dict[str, int] = {}
    out: list[str] = []
    for h in header:
        name = str(h).strip() if h is not None else "unnamed"
        if name in seen:
            seen[name] += 1
            name = f"{name}__{seen[name]}"
        else:
            seen[name] = 0
        out.append(name)
    return out


def _match_cols(work: pd.DataFrame, names: list[str]) -> list[str]:
    """按列名（大小写不敏感）匹配实际存在的列。"""
    lut = {norm_col(c): c for c in work.columns}
    out = []
    for n in names or []:
        c = lut.get(norm_col(n))
        if c is not None:
            out.append(c)
    return out


def _norm_text(s) -> str:
    """安全归一：全角→半角、去空白（不做同义词扩展）。"""
    import re
    import unicodedata
    s = unicodedata.normalize("NFKC", str(s))
    return re.sub(r"\s+", "", s)


def _label_rows(work: pd.DataFrame, reg: dict) -> pd.DataFrame:
    """为明细表标注行类型：total / memo / item（合计是标签，不删除行）。

    合计识别 = "合计/小计"文本规则 + CSMAR 代码字典规则（不做总额/总计等同义词扩展）；
    代码与文本冲突时标记 `_item_conflict`，交质检，不静默覆盖。
    """
    dims = _match_cols(work, reg.get("dimensions", []))
    if not dims:
        work["_row_type"] = "item"
        work["_item_name"] = ""
        work["_item_code"] = ""
        work["_item_conflict"] = False
        return work

    code_maps_all = reg.get("code_maps", {}) or {}
    code_cols = [d for d in dims if d in code_maps_all]
    text_cols = [d for d in dims if d not in code_maps_all]
    total_kw = reg.get("total_keywords", ["合计", "小计"])
    memo_pfx = reg.get("memo_prefixes", ["其中", "减"])

    # 主维度：优先文本列，否则代码列
    primary = (text_cols or code_cols or dims)[0]
    code_col = code_cols[0] if code_cols else None
    # 代码字典做归一化键，兼容 "1" 与 "1.0" 两种写法
    code_map = {_norm_text(k): v for k, v in (code_maps_all.get(code_col, {}) if code_col else {}).items()}

    def _kw_hit(t: str) -> bool:
        return any(k in t for k in total_kw)

    def _code_meaning(t: str):
        return code_map.get(t) or code_map.get(t.replace(".0", ""))

    text = work[primary].astype(str).map(_norm_text)
    code_text = work[code_col].astype(str).map(_norm_text) if code_col else None

    row_type = pd.Series("item", index=work.index, dtype=object)
    conflict = pd.Series(False, index=work.index, dtype=object)

    # 仅当注册表显式声明"文本列↔代码列"为同一项目时，才做冲突检测
    pairs = reg.get("code_text_pairs", []) or []
    pair = None
    for tp in pairs:
        tcol, ccol = (tp if isinstance(tp, (list, tuple)) else (tp.get("text"), tp.get("code")))
        tcol_m = _match_cols(work, [tcol])
        ccol_m = _match_cols(work, [ccol])
        if tcol_m and ccol_m:
            pair = (tcol_m[0], ccol_m[0])
            break

    if pair:
        tcol, ccol = pair
        tval = work[tcol].astype(str).map(_norm_text)
        cmap = {_norm_text(k): v for k, v in (code_maps_all.get(ccol, {}) or {}).items()}
        cval = work[ccol].astype(str).map(_norm_text)
        text_total = tval.map(_kw_hit)
        code_total = cval.map(lambda t: bool((cmap.get(t) or cmap.get(t.replace(".0", ""))) and _kw_hit(cmap.get(t) or cmap.get(t.replace(".0", "")))))
        conflict = text_total != code_total
        is_total = text_total | code_total
        work["_item_name"] = tval
        work["_item_code"] = cval
    elif code_col:
        # 有代码列（可能同时有文本列）：文本规则 OR 代码字典规则
        src = code_text if code_text is not None else text
        code_meaning = src.map(_code_meaning)
        code_total = code_meaning.map(lambda m: bool(m and _kw_hit(m)))
        is_total = text.map(_kw_hit) | code_total
    else:
        is_total = text.map(_kw_hit)

    row_type[is_total] = "total"
    # 备忘行（"其中/减"开头）单独标注，避免与项目行/合计行混淆
    memo_mask = text.str.match(r"^(其中|减)\s*[:：]") & (row_type == "item")
    row_type[memo_mask] = "memo"

    if not pair:
        def _name(t: str) -> str:
            return _code_meaning(t) or t
        work["_item_name"] = text.map(_name)
        work["_item_code"] = code_text if code_col is not None else text
    work["_item_conflict"] = conflict
    work["_row_type"] = row_type
    return work


def to_annual(df: pd.DataFrame, ds: Dataset | None = None) -> pd.DataFrame:
    """只保留年报（月=12），并按注册表声明的表形态去重。

    - wide     : 按 股票代码+年份+base_keys 去重
    - detail   : 去重键包含 dimensions（保留项目行），并标注 _row_type
    - event    : 按 股票代码+年份+event_key 去重（保留事件行）
    - reference: 不去重
    """
    if df.empty:
        return df
    from src.data.normalize import normalize_keys
    from src.data.registry import load_registry

    work = normalize_keys(df)
    if work.empty:
        return work
    if "stock_code" not in work.columns or "year" not in work.columns:
        return work

    reg = load_registry().get(ds.dataset_id, {}) if ds is not None else {}
    shape = reg.get("shape", "auto")

    # 年报（月=12）过滤只适用于财务报表类（宽表/明细表）；事件/参考表不适用
    if shape in ("wide", "detail", "auto") and "report_month" in work.columns:
        annual = work[work["report_month"] == CONFIG["params"]["annual_month"]]
        if not annual.empty:
            work = annual

    if shape == "reference":
        return work.reset_index(drop=True)

    if "report_date" in work.columns:
        # 先按报告日排序，配合 keep="last" 保留最新披露的记录
        work = work.sort_values("report_date")

    base = ["stock_code", "year"] + _match_cols(work, reg.get("base_keys", []))

    if shape == "event":
        # 事件表：按事件键去重（保留事件行，同一企业-年度可多行）
        keys = base + _match_cols(work, reg.get("event_key", []))
    elif shape == "detail":
        # 明细表：先标注行类型（合计/备忘/项目），去重键含维度列以保留项目行
        work = _label_rows(work, reg)
        keys = base + _match_cols(work, reg.get("dimensions", []))
    else:  # wide / auto
        keys = base

    keys = list(dict.fromkeys(keys))
    # keep="last"：在按 report_date 排序后保留最新一条
    work = work.drop_duplicates(subset=keys, keep="last")
    return work.reset_index(drop=True)


def load_dataset(ds: Dataset, use_cache: bool = True, drop_header_rows: bool = True) -> pd.DataFrame:
    """读取原始数据并缓存到 _raw_cache（不参与标准化）。"""
    RAW_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache = RAW_CACHE_DIR / f"{ds.dataset_id}.parquet"
    if use_cache and cache.exists():
        return pd.read_parquet(cache)
    raw = read_dataset(ds)
    raw.to_parquet(cache, index=False)
    return raw


def load_normalized(ds: Dataset, use_cache: bool = True) -> pd.DataFrame:
    """读取标准化表 tables/<id>.parquet；不存在则标准化后写入。"""
    from src.data.normalize import filter_current_period

    TABLES_DIR.mkdir(parents=True, exist_ok=True)
    out = TABLES_DIR / f"{ds.dataset_id}.parquet"
    if use_cache and out.exists():
        return pd.read_parquet(out)
    # 标准化流水线：原始读取 -> 年度/去重 -> 仅保留本期
    work = filter_current_period(to_annual(load_dataset(ds), ds=ds))
    work.to_parquet(out, index=False)
    return work
