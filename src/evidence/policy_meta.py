"""政策元数据（Update 5.1 · 三层来源）。

把政策语料已有的结构化字段（doc_no / date / tax_type / effect_level / url / department）
统一规范化为带**治理字段**的元数据：

    source               rule / manual / llm_candidate
    metadata_confidence  HIGH / MEDIUM / LOW
    verification         VERIFIED / PENDING

三层来源（与 Evidence 原则一致）：
    1) 规则 / 已有结构化元数据（优先，不重复抽取）；
    2) 规则 + 人工校验（`config/policy_metadata.yaml`，同时供 policy_validity 使用）；
    3) LLM 抽取候选 → validation / review → 采纳（`extract_candidates`，**绝不直接写库**）。
"""
from __future__ import annotations

from functools import lru_cache

from src.common import ROOT, get_logger
from src.evidence.policy_validity import classify_policy

log = get_logger()

SOURCE_RULE = "rule"
SOURCE_MANUAL = "manual"
SOURCE_LLM = "llm_candidate"

HIGH, MEDIUM, LOW = "HIGH", "MEDIUM", "LOW"
VERIFIED, PENDING = "VERIFIED", "PENDING"

_META_PATH = ROOT / "config" / "policy_metadata.yaml"

_META_FIELDS = ("effective_from", "effective_to", "status", "subject", "level", "conditions")


@lru_cache(maxsize=1)
def _overrides() -> dict:
    if not _META_PATH.exists():
        return {}
    try:
        import yaml
        data = yaml.safe_load(_META_PATH.read_text(encoding="utf-8")) or {}
        return {str(k): (v or {}) for k, v in (data.get("policies") or {}).items()}
    except Exception as exc:  # noqa: BLE001
        log.warning("policy_metadata 覆盖表加载失败：%s", str(exc)[:120])
        return {}


def _split(v) -> list[str]:
    if not v:
        return []
    if isinstance(v, (list, tuple, set)):
        return [str(x) for x in v if str(x)]
    return [x.strip() for x in str(v).split(",") if x.strip()]


def build_metadata(policy: dict, as_of: str | None = None) -> dict:
    """规范化单条政策的元数据（含治理字段）。"""
    policy = policy or {}
    doc_no = str(policy.get("doc_no") or "")
    ov = _overrides().get(doc_no) or {}
    validity = classify_policy(policy, as_of)

    meta = {
        "doc_no": doc_no,
        "title": policy.get("title"),
        "tax_type": _split(policy.get("tax_type")),
        "level": ov.get("level") or policy.get("effect_level") or policy.get("channel"),
        "department": policy.get("department"),
        "publish_date": policy.get("date"),
        "effective_from": validity.get("effective_from"),
        "effective_to": validity.get("effective_to"),
        "subject": _split(ov.get("subject")),
        "conditions": list(ov.get("conditions") or []),
        "citation_url": [policy.get("url")] if policy.get("url") else [],
        "status": validity.get("validity"),
        "validity_basis": validity.get("basis"),
    }

    if ov:
        meta["source"] = ov.get("source") or SOURCE_MANUAL
        meta["metadata_confidence"] = ov.get("metadata_confidence") or HIGH
        meta["verification"] = ov.get("verification") or VERIFIED
    else:
        meta["source"] = SOURCE_RULE
        has_key = bool(doc_no and policy.get("date"))
        meta["metadata_confidence"] = HIGH if has_key else (MEDIUM if policy.get("date") else LOW)
        meta["verification"] = VERIFIED if has_key else PENDING
    return meta


def extract_candidates(text: str, doc_no: str = "") -> dict:
    """第三层：LLM 抽取候选元数据（source=llm_candidate, verification=PENDING）。

    仅返回候选，供人工校验后写入 `config/policy_metadata.yaml`；**不自动写库**。
    """
    from src.llm.client import get_llm
    system = ("你是税务政策元数据抽取助手。只输出 JSON，不要解释。"
              "字段：\n"
              "- effective_from / effective_to：生效/失效日期（YYYY-MM-DD），无法确定留空字符串；\n"
              "- subject：数组，**适用主体**（如 企业 / 个人 / 个体工商户 / 非居民企业）；不要填税种或主题；\n"
              "- tax_type：数组，涉及**税种**（如 企业所得税 / 增值税）；\n"
              "- level：**效力层级**，只能取 法律 / 行政法规 / 规章 / 规范性文件 之一；\n"
              "- conditions：数组，适用条件。\n"
              "无法确定的字段留空字符串或空数组，禁止编造。")
    data = get_llm().try_chat_json([
        {"role": "system", "content": system},
        {"role": "user", "content": f"文号：{doc_no}\n正文片段：\n{str(text)[:4000]}"},
    ])
    if not isinstance(data, dict):
        return {}
    out = {"doc_no": doc_no, "source": SOURCE_LLM, "verification": PENDING,
           "metadata_confidence": MEDIUM}
    for k in _META_FIELDS:
        out[k] = data.get(k)
    out["tax_type"] = data.get("tax_type")
    return out
