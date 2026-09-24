// 自动生成，请勿手改：python scripts/gen_api_types.py
// 来源：FastAPI OpenAPI schema（src/api/app.py）
/* eslint-disable */


export interface AnalysisBrief {
  stock_code: string;
  year: number;
  name?: string | null;
  industry?: string | null;
  generated_at?: string | null;
  analyzed?: number;
  conditional?: number;
  insufficient?: number;
  recommend?: number;
  result_ref?: string | null;
}

export interface AnalysisList {
  analyses?: AnalysisBrief[];
}

export interface Body_ingest_file_api_ingest_file_post {
  file: string;
  stock_code: string;
  year: number;
  importance?: string;
}

export interface CompanyBrief {
  stock_code: string;
  short_name?: string | null;
  industry_name?: string | null;
  years?: number[];
}

export interface CompanyDetail {
  stock_code: string;
  short_name?: string | null;
  industry_name?: string | null;
  years?: number[];
  latest?: Record<string, unknown>;
}

export interface CompanyList {
  companies?: CompanyBrief[];
}

export interface EvidenceList {
  items?: Record<string, unknown>[];
}

export interface FactStat {
  total?: number;
  present?: number;
  direct?: number;
  proxy?: number;
  missing?: number;
  data_completeness?: number | null;
  evidence_completeness?: number | null;
}

export interface HTTPValidationError {
  detail?: ValidationError[];
}

export interface IngestCommit {
  items?: Record<string, unknown>[];
}

export interface JobStatus {
  job_id: string;
  status: string;
  pct?: number;
  stock_code?: string | null;
  year?: number | null;
  use_llm?: boolean;
  stages?: Record<string, unknown>[];
  error?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  updated_at?: string | null;
  warnings?: string[];
}

export interface KBHit {
  title?: string | null;
  doc_no?: string | null;
  channel?: string | null;
  date?: string | null;
  score?: number | null;
  excerpt?: string | null;
}

export interface KBResult {
  corpus: string;
  hits?: KBHit[];
}

export interface MetaStats {
  data?: Record<string, unknown>;
  knowledge?: Record<string, unknown>;
  evidence?: Record<string, unknown>;
}

export interface PlanIndexEntry {
  direction: string;
  skill_id?: string | null;
  decision?: string | null;
  decision_code?: string | null;
  status_key?: string | null;
  impact_type?: string | null;
  calculation_type?: string | null;
  policy_validity?: string | null;
  evidence_gap?: boolean;
  confirmed_tax_impact?: number | null;
  scenario_tax_impact?: number | null;
  discovery_source?: string | null;
  fact_stats?: FactStat | null;
  detail_ref?: string | null;
}

export interface ResultView {
  analysis_meta: Record<string, unknown>;
  enterprise_summary: Record<string, unknown>;
  diagnostics_summary: Record<string, unknown>;
  direction_summary: Record<string, unknown>;
  plan_index?: PlanIndexEntry[];
  evidence_summary: Record<string, unknown>;
  completeness: Record<string, unknown>;
  artifact_refs: Record<string, unknown>;
}

export interface RunRequest {
  stock_code: string;
  year: number;
  use_llm?: boolean;
  top_k?: number | null;
  rounds?: number | null;
  force?: boolean;
}

export interface SettingsUpdate {
  chat?: Record<string, unknown> | null;
  embedding?: Record<string, unknown> | null;
  llm?: Record<string, unknown> | null;
}

export interface SkillUpdate {
  goal?: string | null;
  policies?: string[] | null;
  risks?: string[] | null;
  eligibility_conditions?: Record<string, unknown>[] | null;
  evidence_required?: string[] | null;
}

export interface TextIngest {
  stock_code: string;
  year: number;
  text: string;
  importance?: string;
  fact_type?: string;
  unit?: string;
  caliber?: string;
}

export interface ThresholdCore {
  gate?: Record<string, unknown> | null;
  signals?: Record<string, unknown> | null;
  category_thresholds?: Record<string, unknown> | null;
}

export interface ValidationError {
  loc: string | number[];
  msg: string;
  type: string;
}

