"""数据集清单与元信息：扫描 data 目录下所有 CSMAR zip。"""
from __future__ import annotations

import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from src.common import CONFIG, dataset_id_from_name, dataset_name_from_name, get_logger

log = get_logger()

# 键列别名（统一为内部名）
CODE_ALIASES = ["symbol", "stkcd", "证券代码", "股票代码"]
DATE_ALIASES = [
    "enddate", "accper", "统计截止日期",
    "prgrsdt", "firstdeclaredate", "latestdeclaredate", "finishdeclaredate",
]
REPORT_TYPE_ALIASES = ["typrep", "statetype", "statetypecode", "报表类型"]
PERIOD_ALIASES = ["sgnyea", "期间归属"]
SOURCE_ALIASES = ["datasources", "列报会计科目"]
CURRENCY_ALIASES = ["currency", "币种", "fn_fnn"]


@dataclass
class Dataset:
    """单个 CSMAR 数据集的元信息。

    字段：dataset_id（6 位编号）、dataset_name、压缩包路径，
    以及从压缩包内探测到的 xlsx 数据入口与 DES 字段说明入口。
    """
    dataset_id: str
    dataset_name: str
    zip_path: Path
    xlsx_entry: str | None = None
    des_entry: str | None = None
    extra: dict = field(default_factory=dict)

    @property
    def slug(self) -> str:
        """稳定标识（当前即 dataset_id），供文件命名与列前缀使用。"""
        return f"{self.dataset_id}"


def discover_datasets(raw_data: Path | None = None) -> list[Dataset]:
    """扫描原始数据目录下的所有 zip，解析出数据集列表。

    从压缩包清单中自动定位 xlsx（数据）与含 DES 的 TXT（字段说明）入口；
    无法识别编号的文件跳过并告警。
    """
    raw_data = Path(raw_data or CONFIG["paths"]["raw_data"])
    datasets: list[Dataset] = []
    for zp in sorted(raw_data.glob("*.zip")):
        did = dataset_id_from_name(zp.name)
        if not did:
            log.warning("跳过无法识别编号的文件: %s", zp.name)
            continue
        ds = Dataset(did, dataset_name_from_name(zp.name), zp)
        with zipfile.ZipFile(zp) as z:
            names = z.namelist()
            # 数据入口取第一个 xlsx；字段说明取含 DES 的 TXT
            xlsx = [n for n in names if n.lower().endswith(".xlsx")]
            des = [n for n in names if n.upper().endswith(".TXT") and "DES" in n.upper()]
            ds.xlsx_entry = xlsx[0] if xlsx else None
            ds.des_entry = des[0] if des else None
        datasets.append(ds)
    return datasets


def read_des(ds: Dataset) -> dict[str, str]:
    """解析 DES 文件 -> {字段代码: 中文名}。"""
    if not ds.des_entry:
        return {}
    import re

    with zipfile.ZipFile(ds.zip_path) as z:
        text = z.read(ds.des_entry).decode("utf-8", "ignore")
    mapping: dict[str, str] = {}
    for line in text.splitlines():
        m = re.match(r"^([A-Za-z0-9_]+)\s*\[([^\]]*)\]", line.strip())
        if m:
            mapping[m.group(1)] = m.group(2)
    return mapping


_CODE_PAIR_RE = None


def read_code_maps(ds: Dataset) -> dict[str, dict[str, str]]:
    """解析 DES 破折号后的编码说明 -> {字段代码: {代码值: 含义}}。

    例如 `Fn05101 [项目] - 1=利息支出；2=利息收入；…`
    -> {"Fn05101": {"1": "利息支出", "2": "利息收入", ...}}
    """
    global _CODE_PAIR_RE
    if not ds.des_entry:
        return {}
    import re
    if _CODE_PAIR_RE is None:
        _CODE_PAIR_RE = re.compile(r"(\d+)\s*[=、:：]\s*([^；;，,\n]+)")

    with zipfile.ZipFile(ds.zip_path) as z:
        text = z.read(ds.des_entry).decode("utf-8", "ignore")
    out: dict[str, dict[str, str]] = {}
    for line in text.splitlines():
        m = re.match(r"^([A-Za-z0-9_]+)\s*\[([^\]]*)\]\s*[-—]\s*(.+)$", line.strip())
        if not m:
            continue
        field, desc = m.group(1), m.group(3)
        pairs = {}
        # 形式一：1=利息支出；2=利息收入
        for code, meaning in _CODE_PAIR_RE.findall(desc):
            meaning = meaning.strip()
            if meaning:
                pairs[code] = meaning
        # 形式二：Q3301 企业所得税；Q3302 土地增值税
        if not pairs:
            for seg in re.split(r"[；;]", desc):
                seg = seg.strip()
                mm = re.match(r"^([A-Za-z]{0,3}\d+)\s+(.+)$", seg)
                if mm:
                    pairs[mm.group(1)] = mm.group(2).strip()
        if pairs:
            out[field] = pairs
    return out


_REGISTRY: dict | None = None


def load_registry() -> dict:
    """读取 config/dataset_registry.yaml -> {dataset_id: 配置}。"""
    global _REGISTRY
    if _REGISTRY is not None:
        return _REGISTRY
    import yaml
    from src.common import ROOT
    path = ROOT / "config" / "dataset_registry.yaml"
    if not path.exists():
        log.warning("未找到注册表 %s，回退自动识别", path)
        _REGISTRY = {}
        return _REGISTRY
    with open(path, "r", encoding="utf-8") as f:
        _REGISTRY = (yaml.safe_load(f) or {}).get("datasets", {})
    return _REGISTRY


if __name__ == "__main__":
    ds = discover_datasets()
    print(f"发现 {len(ds)} 个数据集")
    for d in ds[:5]:
        print(d.dataset_id, d.dataset_name, d.xlsx_entry)
