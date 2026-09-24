"""API response schemas（Pydantic v2）。

约定：DTO 只给**展示字段 + 状态键**，不暴露领域概念供前端做逻辑。
status_key ∈ {CONFIRMED, SCENARIO, DATA_GAP, NOT_RECOMMEND}
  （结论状态；由「结论 + 金额」共同推导，CONFIRMED 不要求存在金额）
impact_type ∈ {CONFIRMED_IMPACT, SCENARIO_IMPACT, TAX_SHIELD, NO_CALCULATION, NOT_CALCULABLE}
calculation_type ∈ {BASELINE_TAX, SCENARIO_TAX, INCREMENTAL_TAX_BENEFIT, TAX_SHIELD, NOT_CALCULABLE, NO_CALCULATION}
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class Loose(BaseModel):
    """宽松基类：允许携带未声明字段（extra=allow）。

    产物 JSON 常带演进中的附加字段，宽松模式可避免因新增字段导致响应校验失败。
    """
    model_config = ConfigDict(extra="allow")


class CompanyBrief(Loose):
    """企业列表项：代码、简称、行业与覆盖年度。"""
    stock_code: str
    short_name: str | None = None
    industry_name: str | None = None
    years: list[int] = []


class CompanyList(Loose):
    """企业列表响应包装。"""
    companies: list[CompanyBrief] = []


class CompanyDetail(Loose):
    """企业详情：基础信息 + 最近年度画像行 latest。"""
    stock_code: str
    short_name: str | None = None
    industry_name: str | None = None
    years: list[int] = []
    latest: dict = {}


class AnalysisBrief(Loose):
    """分析列表项：结论计数摘要（analyzed/conditional/insufficient/recommend）。"""
    stock_code: str
    year: int
    name: str | None = None
    industry: str | None = None
    generated_at: str | None = None
    analyzed: int = 0
    conditional: int = 0
    insufficient: int = 0
    recommend: int = 0
    result_ref: str | None = None


class AnalysisList(Loose):
    """分析列表响应包装。"""
    analyses: list[AnalysisBrief] = []


class FactStat(Loose):
    """事实统计：总数/已具备/直接/代理/缺失及两类完整度（0~1）。"""
    total: int = 0
    present: int = 0
    direct: int = 0
    proxy: int = 0
    missing: int = 0
    data_completeness: float | None = None
    evidence_completeness: float | None = None


class PlanIndexEntry(Loose):
    """方案索引条目：单方向的结论状态、金额与证据缺口。

    status_key/impact_type/calculation_type 为前端展示状态机的核心键；
    confirmed_tax_impact 与 scenario_tax_impact 分别对应确定性与情景金额。
    """
    direction: str
    skill_id: str | None = None
    decision: str | None = None
    decision_code: str | None = None
    status_key: str | None = None
    impact_type: str | None = None
    calculation_type: str | None = None
    policy_validity: str | None = None
    evidence_gap: bool = False
    confirmed_tax_impact: float | None = None
    scenario_tax_impact: float | None = None
    discovery_source: str | None = None
    fact_stats: FactStat | None = None
    detail_ref: str | None = None


class ResultView(Loose):
    """完整分析结果视图：元信息、诊断、方向汇总、方案索引与产物引用。"""
    analysis_meta: dict
    enterprise_summary: dict
    diagnostics_summary: dict
    direction_summary: dict
    plan_index: list[PlanIndexEntry] = []
    evidence_summary: dict
    completeness: dict
    artifact_refs: dict


class KBHit(Loose):
    """知识库单条命中（含评分与摘要片段）。"""
    title: str | None = None
    doc_no: str | None = None
    channel: str | None = None
    date: str | None = None
    score: float | None = None
    excerpt: str | None = None


class KBResult(Loose):
    """知识库检索响应：语料库名 + 命中列表。"""
    corpus: str
    hits: list[KBHit] = []


class MetaStats(Loose):
    """元统计响应：数据/知识库/证据三块独立汇总。"""
    data: dict = {}
    knowledge: dict = {}
    evidence: dict = {}


class EvidenceList(Loose):
    """证据列表响应包装。"""
    items: list[dict] = []


class RunRequest(Loose):
    """分析任务请求：企业/年度，及是否用 LLM、检索 top_k、辩论轮数、强制重跑。"""
    stock_code: str
    year: int
    use_llm: bool = True
    top_k: int | None = None
    rounds: int | None = None
    force: bool = False


class JobStatus(Loose):
    """任务状态：进度百分比、阶段明细、起止时间与告警。"""
    job_id: str
    status: str
    pct: int = 0
    stock_code: str | None = None
    year: int | None = None
    use_llm: bool = False
    stages: list[dict] = []
    error: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    updated_at: str | None = None
    warnings: list[str] = []


class SettingsUpdate(Loose):
    """设置更新请求：仅提交需变更的分区，None 表示不改动。"""
    chat: dict | None = None
    embedding: dict | None = None
    llm: dict | None = None


class ThresholdCore(Loose):
    """核心阈值更新请求（gate / signals / category_thresholds）。"""
    gate: dict | None = None
    signals: dict | None = None
    category_thresholds: dict | None = None


class SkillUpdate(Loose):
    """Skill 更新请求：仅覆盖显式提供的字段。"""
    goal: str | None = None
    policies: list[str] | None = None
    risks: list[str] | None = None
    eligibility_conditions: list[dict] | None = None
    evidence_required: list[str] | None = None


class TextIngest(Loose):
    """文本录入请求：企业/年度/正文，及重要性、事实类型、单位、口径。"""
    stock_code: str
    year: int
    text: str
    importance: str = "direct"      # direct | reference
    fact_type: str = ""
    unit: str = ""
    caliber: str = ""


class IngestCommit(Loose):
    """候选证据提交请求。"""
    items: list[dict] = []
