"""政策时效最小安全门（Update 5.0 · 产品 P0-D）。

`policy_validity ∈ {VALID, EXPIRED, NOT_YET_EFFECTIVE, UNKNOWN}`

不提前做完整政策元数据系统，只保证：**Demo 所引用的政策能判断"是否当前有效"**，
避免出现"AI 发现方向 → RAG 找到政策 → 计算正确 → 最后发现政策已失效"。

判定来源（优先顺序）：
1. 政策记录上的显式字段 `effective_from / effective_to / status`；
2. 人工校验层 `config/policy_validity.yaml`（按文号覆盖）；
3. 标题含「废止 / 失效」→ EXPIRED；
4. 有可靠发布日期且 ≤ 基准日、且无失效记录 → VALID（推定现行有效）；
5. 其余 → UNKNOWN。

`UNKNOWN` 不阻断运行，但**不得单独支撑 `CONFIRMED`**（见 `struct2.md` §4.2 / §6.5）。
"""
from __future__ import annotations

import datetime as _dt

from src.common import ROOT, get_logger

log = get_logger()

VALID = "VALID"
EXPIRED = "EXPIRED"
NOT_YET_EFFECTIVE = "NOT_YET_EFFECTIVE"
UNKNOWN = "UNKNOWN"

_INVALID = (EXPIRED, NOT_YET_EFFECTIVE)
_OVERRIDE_PATHS = (ROOT / "config" / "policy_metadata.yaml",
                   ROOT / "config" / "policy_validity.yaml")


def _parse_date(s) -> _dt.date | None:
    if not s:
        return None
    t = str(s).strip()[:10].replace("/", "-").replace(".", "-")
    for fmt in ("%Y-%m-%d", "%Y-%m", "%Y"):
        try:
            d = _dt.datetime.strptime(t, fmt).date()
            return d
        except ValueError:
            continue
    return None


_CACHE: dict = {"key": None, "data": {}}


def _overrides() -> dict:
    """合并人工校验层（policy_metadata.yaml 优先，兼容 policy_validity.yaml）。

    按文件 mtime 自动失效：长驻服务下编辑配置**无需重启**。
    """
    key = tuple((str(p), (p.stat().st_mtime if p.exists() else None)) for p in _OVERRIDE_PATHS)
    if _CACHE["key"] == key:
        return _CACHE["data"]
    merged: dict = {}
    for path in reversed(_OVERRIDE_PATHS):  # 后者（metadata）覆盖前者
        if not path.exists():
            continue
        try:
            import yaml
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            merged.update({str(k): (v or {}) for k, v in (data.get("policies") or {}).items()})
        except Exception as exc:  # noqa: BLE001
            log.warning("政策覆盖表加载失败 %s：%s", path.name, str(exc)[:120])
    _CACHE["key"] = key
    _CACHE["data"] = merged
    return merged


def classify_policy(policy: dict, as_of: str | None = None) -> dict:
    """判定单条政策相对基准日的时效。"""
    policy = policy or {}
    doc_no = str(policy.get("doc_no") or "")
    title = str(policy.get("title") or "")
    ov = _overrides().get(doc_no) or {}

    as_of_d = _parse_date(as_of) or _dt.date.today()
    ef = _parse_date(policy.get("effective_from")) or _parse_date(ov.get("effective_from"))
    et = _parse_date(policy.get("effective_to")) or _parse_date(ov.get("effective_to"))
    status = str(ov.get("status") or policy.get("status") or "").upper()

    base = {"doc_no": doc_no, "title": title,
            "effective_from": ef.isoformat() if ef else None,
            "effective_to": et.isoformat() if et else None}

    if status in ("EXPIRED", "废止", "失效") or (et and et < as_of_d):
        return {**base, "validity": EXPIRED, "basis": "explicit"}
    if status in ("NOT_YET_EFFECTIVE", "未生效") or (ef and ef > as_of_d):
        return {**base, "validity": NOT_YET_EFFECTIVE, "basis": "explicit"}
    if ef and ef <= as_of_d and (not et or et >= as_of_d):
        return {**base, "validity": VALID, "basis": "explicit"}
    if any(k in title for k in ("废止", "失效")):
        return {**base, "validity": EXPIRED, "basis": "title"}
    pub = _parse_date(policy.get("date"))
    if pub and pub <= as_of_d:
        return {**base, "validity": VALID, "basis": "publish_date"}
    return {**base, "validity": UNKNOWN, "basis": "none"}


def summarize(policies: list[dict], as_of: str | None = None) -> dict:
    """汇总一组政策的时效：overall ∈ {VALID, EXPIRED, NOT_YET_EFFECTIVE, UNKNOWN}。

    - 任一失效/未生效 → 取最严重者；
    - 否则任一 VALID → VALID；
    - 否则（无政策或全 UNKNOWN）→ UNKNOWN。
    """
    items = [classify_policy(p, as_of) for p in (policies or [])]
    invalid = [i for i in items if i["validity"] in _INVALID]
    if invalid:
        overall = invalid[0]["validity"]
    elif any(i["validity"] == VALID for i in items):
        overall = VALID
    else:
        overall = UNKNOWN
    return {
        "overall": overall,
        "as_of": (_parse_date(as_of) or _dt.date.today()).isoformat(),
        "count": len(items),
        "expired": [i for i in items if i["validity"] == EXPIRED],
        "not_yet_effective": [i for i in items if i["validity"] == NOT_YET_EFFECTIVE],
        "valid": [i for i in items if i["validity"] == VALID],
        "unknown": [i for i in items if i["validity"] == UNKNOWN],
    }
