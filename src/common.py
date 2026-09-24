"""公共工具：路径、配置、日志、文本/数值规范化。"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Any

import yaml

# ---------------------------------------------------------------- 路径 / 配置

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "config.yaml"
SETTINGS_PATH = ROOT / "config" / "settings.json"   # UI 设置覆盖层（含密钥，勿提交）

# 加载项目根目录 .env（若存在）。已存在的环境变量优先，不覆盖。
# encoding="utf-8-sig" 以兼容带 BOM 的 Windows 编辑器。
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=False, encoding="utf-8-sig")
except Exception:  # noqa: BLE001
    pass

_STOCK_RE = re.compile(r"^\d{6}$")
_DIGIT_RUN_RE = re.compile(r"(\d{6,})")
_YEAR_MIN, _YEAR_MAX = 1900, 2100


def _resolve_path(p: str) -> str:
    """相对路径基于项目根目录解析为绝对路径（已是绝对路径则原样返回）。"""
    path = Path(str(p))
    return str(path if path.is_absolute() else (ROOT / path))


def load_config(path: Path | None = None) -> dict:
    with open(path or CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    # 把 config.yaml 中的相对路径统一解析为基于 ROOT 的绝对路径
    for k, v in list((cfg.get("paths") or {}).items()):
        if isinstance(v, str):
            cfg["paths"][k] = _resolve_path(v)
    for k, v in list((cfg.get("rag") or {}).items()):
        if isinstance(v, str) and k.endswith("_dir"):
            cfg["rag"][k] = _resolve_path(v)
    return cfg


CONFIG = load_config()


def load_settings() -> dict:
    """读取 UI 设置覆盖层（config/settings.json）；不存在则返回 {}。"""
    try:
        if SETTINGS_PATH.exists():
            return json.loads(SETTINGS_PATH.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001
        pass
    return {}


def save_settings(data: dict) -> None:
    """写入 UI 设置覆盖层（仅存用户显式填写项）。"""
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

YEAR_START, YEAR_END = (
    min(CONFIG["params"]["years"]),
    max(CONFIG["params"]["years"]),
)
YEARS = list(range(YEAR_START, YEAR_END + 1))


def ensure_dirs() -> None:
    for key in ("tables_dir", "derived_dir", "features_dir", "knowledge", "outputs"):
        Path(CONFIG["paths"][key]).mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------- 日志

def get_logger(name: str = "tax") -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(CONFIG.get("logging", {}).get("level", "INFO"))
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s %(message)s", "%H:%M:%S"))
    logger.addHandler(handler)
    logger.propagate = False
    return logger


# ---------------------------------------------------------------- 文本规范化

def norm_col(name: Any) -> str:
    """列名规范化：去空白、转小写。"""
    if name is None:
        return ""
    return str(name).strip().lower()


def dataset_id_from_name(filename: str) -> str | None:
    """从文件名提取数据集编号（最长数字串）。"""
    stem = os.path.basename(filename)
    runs = _DIGIT_RUN_RE.findall(stem)
    if not runs:
        return None
    return max(runs, key=len)


def dataset_name_from_name(filename: str) -> str:
    m = re.match(r"^(.+?)\d{6,}", os.path.basename(filename))
    base = m.group(1) if m else os.path.splitext(os.path.basename(filename))[0]
    return base.strip("_ -")


# ---------------------------------------------------------------- 股票代码

def norm_stock_code(value: Any) -> str | None:
    """把各种股票代码写法统一成 6 位字符串。"""
    if value is None:
        return None
    s = str(value).strip()
    if s == "":
        return None
    s = s.split(".")[0]
    if s.lower() in {"nan", "none", "-", "没有单位"}:
        return None
    digits = re.sub(r"\D", "", s)
    if not digits:
        return None
    if len(digits) <= 6:
        return digits.zfill(6)
    return digits[:6]


def looks_like_stock(value: Any) -> bool:
    return norm_stock_code(value) is not None and str(value).strip().replace(".0", "").isdigit()


# ---------------------------------------------------------------- JSON 落盘

def save_json(obj: Any, path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2, default=str)


# ---------------------------------------------------------------- URL

POLICY_URL_BASE = "https://fgk.chinatax.gov.cn"


def abs_url(u: Any) -> str:
    """把政策相对链接（/zcfgk/...）补全为绝对链接。"""
    s = str(u or "").strip()
    if not s:
        return ""
    if s.startswith("http://") or s.startswith("https://"):
        return s
    if s.startswith("/"):
        return POLICY_URL_BASE + s
    return s
