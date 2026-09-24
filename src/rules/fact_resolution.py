"""字段默认 Resolution 登记（Update 3.1）。

`config/fact_resolution.yaml` 给出**字段默认语义属性**（DIRECT / PROXY / INSUFFICIENT /
qualification_proxy）。注意：这是**默认**，不是当前计算上下文的最终 resolution。

机制（`run_calculator`）对「关键输入」按优先级闭合判定：
    INSUFFICIENT/MISSING  >  PROXY  >  DIRECT
"""
from __future__ import annotations

from pathlib import Path

import yaml

from src.common import ROOT, get_logger

log = get_logger()

RESOLUTION_PATH = ROOT / "config" / "fact_resolution.yaml"

DIRECT = "DIRECT"
PROXY = "PROXY"
INSUFFICIENT = "INSUFFICIENT"

_CACHE: dict | None = None
_CACHE_MTIME: float | None = None


def load_resolution(path: Path | None = None, refresh: bool = False) -> dict:
    """加载 resolution 登记表；按文件 mtime 自动失效（改配置无需重启）。"""
    global _CACHE, _CACHE_MTIME
    p = path or RESOLUTION_PATH
    try:
        mtime = p.stat().st_mtime if p.exists() else None
    except OSError:
        mtime = None
    if _CACHE is None or refresh or _CACHE_MTIME != mtime:
        try:
            with open(p, "r", encoding="utf-8") as f:
                _CACHE = yaml.safe_load(f) or {}
        except OSError:
            _CACHE = {}
        _CACHE_MTIME = mtime
    return _CACHE


def _meta(field: str) -> dict:
    return (load_resolution().get(str(field)) or {})


def _by_skill(skill_id: str | None) -> dict:
    if not skill_id:
        return {}
    return (load_resolution().get("by_skill", {}) or {}).get(str(skill_id), {}) or {}


def resolution_of(field: str, skill_id: str | None = None) -> str:
    """字段 resolution：优先技能级覆盖（by_skill），否则全局默认（未登记视为 DIRECT）。"""
    m = _by_skill(skill_id).get(str(field)) or {}
    if m.get("default_resolution"):
        return str(m["default_resolution"]).upper()
    return str(_meta(field).get("default_resolution", DIRECT)).upper()


def proxy_of(field: str, skill_id: str | None = None) -> str | None:
    m = _by_skill(skill_id).get(str(field)) or {}
    if "of" in m:
        return m.get("of")
    return _meta(field).get("of")


def is_qualification_proxy(field: str) -> bool:
    return bool(_meta(field).get("qualification_proxy", False))


def is_qualification_field(field: str) -> bool:
    """字段是否属于"资格类"：显式 qualification_proxy，或其代理对象含"资格"。"""
    if is_qualification_proxy(field):
        return True
    return "资格" in str(proxy_of(field) or "")


def _is_missing(row: dict, field: str) -> bool:
    v = row.get(field)
    if v is None:
        return True
    try:
        return isinstance(v, float) and v != v
    except Exception:  # noqa: BLE001
        return False


def classify(fields: list[str], row: dict, direct_fields: set[str] | None = None,
             skill_id: str | None = None) -> dict:
    """对关键输入分类：missing / insufficient / proxy / direct。

    `direct_fields`：已由**直接口径证据**（直接输入 / 权威结构化）覆盖的字段，
    其默认 PROXY 被升级为 DIRECT（例如上传研发辅助账后，研发费用口径变为可确认）。
    `skill_id`：按技能语境覆盖字段 resolution（见 config/fact_resolution.yaml 的 by_skill）。
    """
    out = {"missing": [], "insufficient": [], "proxy": [], "direct": []}
    direct_fields = direct_fields or set()
    for f in dict.fromkeys(fields or []):
        if _is_missing(row, f):
            out["missing"].append(f)
            continue
        if f in direct_fields:
            out["direct"].append(f)
            continue
        r = resolution_of(f, skill_id)
        if r == INSUFFICIENT:
            out["insufficient"].append(f)
        elif r == PROXY:
            out["proxy"].append(f)
        else:
            out["direct"].append(f)
    return out


def proxy_inputs(fields: list[str], skill_id: str | None = None) -> list[dict]:
    out = []
    for f in dict.fromkeys(fields or []):
        if resolution_of(f, skill_id) != PROXY:
            continue
        try:
            from src.evidence.sources import field_label
            lab = field_label(f).get("label") or f
        except Exception:  # noqa: BLE001
            lab = f
        out.append({"field": f, "label": lab, "of": proxy_of(f, skill_id)})
    return out
