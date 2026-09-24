"""Evidence Contract：外部证据的统一数据结构与事实类型词表。

原则：事实必须与来源绑定；用户输入可不规范，入库必须规范。

2.3 模型要点：
- **Evidence 是事实的历史记录，Profile 是当前采用结果**（见 resolution.py）。
- `source_type`（来源）与 `doc_type`（文档类型）**分离**。
- 采用优先级 `SOURCE_PRECEDENCE`（不是"真实性排名"）。
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from src.common import ROOT
from src.evidence.sources import DERIVED_FIELDS, FIELD_SOURCES

# ---------------------------------------------------------------- 来源采用优先级
# 规则（用户约定）：直接输入 > 结构化数据输入 > 数据库数据 ≥ 提取数据
# 名称是「采用优先级（precedence）」，不是「真实性排名」。
SOURCE_PRECEDENCE: dict[str, int] = {
    "manual": 100,            # 用户直接输入（最可信）
    "user_structured": 80,    # 用户提交的结构化数据（CSV/Excel）
    "csmar": 60,              # 数据库数据（基准值）
    "user_document": 40,      # 文档提取（年报/演讲稿/股东会记录…）
}
# 同一来源内部的提取方式微调（表格 > 文本 > LLM）
METHOD_BONUS: dict[str, int] = {"pdf_table": 5, "pdf_text": 0, "llm_assisted": -10}

# 旧来源名 -> 新来源名（兼容迁移）
LEGACY_SOURCE_MAP = {"csv": "user_structured", "excel": "user_structured",
                     "annual_report": "user_document", "manual_edit": "manual"}

# 免人工逐条评审、可直接进入 Profile 的来源（仍受采用规则约束）
AUTO_CONFIRM_SOURCES = {"manual", "user_structured"}

# ---------------------------------------------------------------- 文档类型
DOC_TYPES: dict[str, str] = {
    "annual_report": "年报",
    "speech": "演讲稿/讲话",
    "shareholder_meeting": "股东大会记录",
    "announcement": "公告",
    "prospectus": "招股说明书",
    "research_report": "研究报告",
    "other": "其他文档",
}

# ---------------------------------------------------------------- 事实类型升级
# 领域事实类型（DOMAIN_FACT_TYPES）默认 unmapped；登记后可映射到正式字段参与计算。
FACT_TYPE_MAP_PATH = ROOT / "config" / "fact_type_map.json"


def _load_fact_type_map() -> dict[str, str]:
    try:
        with open(FACT_TYPE_MAP_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {str(k): str(v) for k, v in (data or {}).items()}
    except (OSError, ValueError):
        return {}


FACT_TYPE_MAP: dict[str, str] = _load_fact_type_map()

_KNOWN_FIELDS_CACHE: set[str] | None = None


def known_fields() -> set[str]:
    """正式字段全集：字段注册表 ∪ 衍生字段 ∪ 画像实际列（缓存）。"""
    global _KNOWN_FIELDS_CACHE
    if _KNOWN_FIELDS_CACHE is None:
        cols: set[str] = set(FIELD_SOURCES) | set(DERIVED_FIELDS)
        try:
            import pyarrow.parquet as pq
            from src.common import CONFIG
            p = Path(CONFIG["paths"]["features_dir"]) / "profile_features.parquet"
            cols |= set(pq.ParquetFile(p).schema.names)
        except Exception:  # noqa: BLE001
            pass
        _KNOWN_FIELDS_CACHE = cols
    return _KNOWN_FIELDS_CACHE


def normalize_source_type(source_type: str) -> str:
    st = str(source_type or "").strip().lower()
    return LEGACY_SOURCE_MAP.get(st, st or "manual")


def source_precedence(source_type: str, extraction_method: str = "") -> int:
    """采用优先级分值（越高越优先被采用）。"""
    base = SOURCE_PRECEDENCE.get(normalize_source_type(source_type), 30)
    return base + METHOD_BONUS.get(str(extraction_method or "").lower(), 0)


# 兼容旧名
source_priority = source_precedence


# 领域补充事实类型（不属于现有 CSMAR 字段，但筹划常需要）
DOMAIN_FACT_TYPES = {
    "RD_PROJECT": "研发项目（名称/预算/立项）",
    "RD_LEDGER": "研发支出辅助账（按项目/费用类型归集）",
    "RD_EXPENSE_BREAKDOWN": "研发费用构成明细（人员人工/直接投入/折旧/摊销/其他）",
    "HIGH_TECH_CERT": "高新技术企业证书（编号/有效期/税率）",
    "GOV_DOC": "政府补助拨付文件与管理办法",
    "RELATED_LOAN": "关联方借款明细（本金/利率/期限）",
    "BENCHMARK_RATE": "金融企业同期同类贷款利率",
    "TAX_FILING": "纳税申报表（可弥补亏损/调整明细）",
    "ASSET_LIST": "设备器具购置清单（单价/类别）",
    "LEASE_CONTRACT": "租赁合同（租赁期/租金）",
    "BAD_DEBT_WRITE_OFF": "坏账核销与资产损失资料",
    "EQUITY_TRANSFER": "股权转让合同与作价依据",
}

# 值类型 -> 冲突容差（金额/比率/数量/文本）
VALUE_KINDS = {"amount", "ratio", "count", "text"}

RATIO_HINTS = ("ratio", "rate", "占比", "比率", "率")
COUNT_HINTS = ("person", "count", "人数", "数量", "家数", "笔数")


def build_fact_type_vocab() -> dict[str, str]:
    """受控事实类型词表：复用现有字段注册表 + 领域补充。"""
    vocab: dict[str, str] = {}
    for k in FIELD_SOURCES:
        vocab[k] = f"字段：{k}"
    for k in DERIVED_FIELDS:
        vocab.setdefault(k, f"衍生字段：{k}")
    vocab.update(DOMAIN_FACT_TYPES)
    return vocab


def resolve_linked_field(fact_type: str) -> str | None:
    """把事实类型映射到正式字段（进得了 Skill 计算）；无法映射返回 None。"""
    ft = str(fact_type or "").strip()
    if ft in FIELD_SOURCES or ft in DERIVED_FIELDS:
        return ft
    if ft in FACT_TYPE_MAP:
        return FACT_TYPE_MAP[ft]
    if ft in known_fields():
        return ft
    return None


def upgrade_fact_type(fact_type: str, linked_field: str, persist: bool = True) -> dict:
    """把领域事实类型升级映射到正式字段（unmapped -> mapped）。

    升级后，历史证据在回填时会**动态重新解析** linked_field，因此对旧数据同样生效。
    """
    ft, lf = str(fact_type).strip(), str(linked_field).strip()
    if not ft or not lf:
        raise ValueError("fact_type 与 linked_field 均不能为空")
    in_registry = lf in FIELD_SOURCES or lf in DERIVED_FIELDS
    if not in_registry and lf not in known_fields():
        raise ValueError(f"目标字段 `{lf}` 不是已知画像字段，无法升级")
    FACT_TYPE_MAP[ft] = lf
    if persist:
        FACT_TYPE_MAP_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(FACT_TYPE_MAP_PATH, "w", encoding="utf-8") as f:
            json.dump(FACT_TYPE_MAP, f, ensure_ascii=False, indent=2)
    return {"fact_type": ft, "linked_field": lf, "in_registry": in_registry,
            "total_mapped": len(FACT_TYPE_MAP)}


def infer_value_kind(fact_type: str, value=None) -> str:
    ft = str(fact_type or "").lower()
    if any(h in ft for h in RATIO_HINTS):
        return "ratio"
    if any(h in ft for h in COUNT_HINTS):
        return "count"
    if isinstance(value, str) and not re.fullmatch(r"-?\d+(\.\d+)?", value.strip()):
        return "text"
    return "amount"


@dataclass
class Evidence:
    evidence_id: str
    stock_code: str
    company_name: str = ""
    year: int | None = None                 # 兼容保留（分析年份）
    report_year: int | None = None          # 报告年度（可空）
    period: str = "年度"
    fact_type: str = ""
    value_num: float | None = None          # 数值型事实
    value_text: str = ""                    # 文本型事实
    unit: str = ""
    currency: str = "CNY"
    caliber: str = ""
    value_kind: str = "amount"
    source_type: str = "manual"             # manual/user_structured/csmar/user_document
    doc_type: str = ""                      # annual_report/speech/... （仅 user_document）
    document_id: str = ""
    document_date: str = ""                 # YYYY-MM-DD，可空
    extraction_method: str = "manual"       # manual/csv/excel/pdf_text/pdf_table/llm_assisted
    extraction_confidence: float = 1.0
    verification_status: str = "candidate"  # candidate/confirmed/rejected
    resolution_status: str = "pending"      # pending/accepted/superseded/conflict
    supersedes_evidence_id: str = ""
    import_mode: str = "supplement"         # supplement/authoritative（仅 user_structured）
    linked_field: str | None = None
    reviewer_action: str = ""
    reviewer: str = ""
    reviewed_at: str = ""
    source_ref: str = ""                    # 简短展示（如 "2024年报 P87"）
    quote: str = ""                         # 原文引用片段
    location: str = ""                      # 页码/表格定位
    raw_context: str = ""
    created_at: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @classmethod
    def from_row(cls, row: dict) -> "Evidence":
        d = dict(row)
        d.pop("id", None)
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})


def make_evidence(seq: int, stock_code: str, year: int | None, fact_type: str,
                  value=None, **kwargs) -> Evidence:
    kind = kwargs.pop("value_kind", None) or infer_value_kind(fact_type, value)
    source_type = normalize_source_type(kwargs.pop("source_type", "manual"))
    if "verification_status" not in kwargs:
        kwargs["verification_status"] = ("confirmed" if source_type in AUTO_CONFIRM_SOURCES
                                         else "candidate")
    kwargs.setdefault("report_year", year)
    ev = Evidence(
        evidence_id=f"EV{datetime.now().strftime('%Y%m%d')}{seq:04d}",
        stock_code=str(stock_code).zfill(6) if stock_code else "",
        year=year,
        fact_type=fact_type,
        value_kind=kind,
        source_type=source_type,
        linked_field=resolve_linked_field(fact_type),
        **kwargs,
    )
    if kind == "text":
        ev.value_text = str(value)
    else:
        try:
            ev.value_num = float(value)
        except (TypeError, ValueError):
            ev.value_text = str(value)
            ev.value_kind = "text"
    return ev
