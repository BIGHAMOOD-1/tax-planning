// API client（Phase 0/1）。类型与后端 Pydantic schema 对齐；后续可用 OpenAPI 自动生成。

export type StatusKey = 'CONFIRMED' | 'SCENARIO' | 'DATA_GAP' | 'NOT_RECOMMEND'
export type ImpactType =
  | 'CONFIRMED_IMPACT' | 'SCENARIO_IMPACT' | 'TAX_SHIELD'
  | 'NO_CALCULATION' | 'NOT_CALCULABLE'
export type CalculationType =
  | 'BASELINE_TAX' | 'SCENARIO_TAX' | 'INCREMENTAL_TAX_BENEFIT'
  | 'TAX_SHIELD' | 'NOT_CALCULABLE' | 'NO_CALCULATION'
export type PolicyValidity = 'VALID' | 'EXPIRED' | 'NOT_YET_EFFECTIVE' | 'UNKNOWN'

export interface CompanyBrief {
  stock_code: string
  short_name?: string | null
  industry_name?: string | null
  years: number[]
}

export interface AnalysisBrief {
  stock_code: string
  year: number
  name?: string | null
  industry?: string | null
  generated_at?: string | null
  analyzed: number
  conditional: number
  insufficient: number
  recommend: number
  brief?: string | null
  result_ref?: string | null
}

export interface FactStat {
  total: number
  present: number
  direct: number
  proxy: number
  missing: number
  data_completeness?: number | null
  evidence_completeness?: number | null
}

export interface RequiredFact {
  fact: string
  field: string
  present: boolean
  label?: string
  source?: string
  formula?: string
  resolution: 'DIRECT' | 'PROXY' | 'INSUFFICIENT'
  proxy_of?: string | null
  qualification_proxy?: boolean
}

export interface PlanIndexEntry {
  direction: string
  skill_id?: string | null
  decision?: string | null
  decision_code?: string | null
  status_key: StatusKey
  impact_type?: ImpactType | null
  calculation_type?: CalculationType | null
  policy_validity?: PolicyValidity | null
  evidence_gap: boolean
  confirmed_tax_impact?: number | null
  scenario_tax_impact?: number | null
  amount_is_estimate?: boolean | null
  discovery_source?: string | null
  fact_stats?: FactStat | null
  detail_ref?: string | null
}

export interface Diagnostic {
  severity: string
  name: string
  direction: string
  actual?: number | null
  expected?: number | null
  deviation?: number | null
  statement?: string
  evidence_needed?: string[]
}

export interface ResultView {
  analysis_meta: {
    stock_code: string
    year: number
    name?: string
    industry?: string
    generated_at?: string
    opportunity_source?: string
  }
  brief?: string
  enterprise_summary: Record<string, unknown>
  trend?: Record<string, Record<string, number | null>>
  diagnostics_summary: { 关注?: number; 观察?: number; 提示?: number; items: Diagnostic[] }
  direction_summary: Record<string, number>
  plan_index: PlanIndexEntry[]
  evidence_summary: { confirmed: number; proxy: number; missing: number; total: number }
  completeness: {
    data?: number | null
    evidence?: number | null
    by_direction: Record<string, FactStat>
  }
  artifact_refs: Record<string, string>
}

export interface Opinion {
  direction?: string
  decision?: string
  summary?: string
  approach?: string
  applicability?: string[]
  steps?: { step: string; detail?: string }[]
  materials?: string[]
  accounting_tax?: string[]
  red_lines?: string[]
  not_applicable?: string[]
  references?: string[]
  refs?: { id: string; doc_no?: string; title?: string; url?: string }[]
  disclaimer?: string
  _source?: string
}

export interface PlanDetail {
  stock_code: string
  year: number
  direction: string
  final_decision?: string
  decision_code?: string
  estimated_tax_impact?: number | null
  scenario_tax_impact?: number | null
  policy_validity?: { overall?: PolicyValidity | null; count?: number } | null
  opinion?: Opinion | null
  amount_is_estimate?: boolean
  gate?: Record<string, unknown>
  skill?: { id: string; name: string; version?: string }
  plan?: {
    measures?: string[]
    measures_ai?: string[]
    policies?: string[]
    evidence_required?: string[]
  }
  lineage: {
    calculation?: {
      calculator?: string
      impact_type?: ImpactType | null
      calculation_type?: CalculationType | null
      calculation_status?: string | null
      direction?: 'tax_reduce' | 'tax_increase' | 'none' | null
      impact_amount?: number | null
      impact_basis?: string | null
      baseline?: string | null
      action?: string | null
      caveat?: string | null
      risk_note?: string | null
      shield_amount?: number | null
      proxy_inputs?: { field: string; of?: string }[]
      not_calculable_inputs?: string[]
      results?: Record<string, unknown>
      layers?: { layer: string; value: unknown; kind?: string; note?: string; source?: string }[]
      assumptions?: string[]
    }
    required_facts?: RequiredFact[]
    data_pool?: Record<string, unknown>
    debate?: Record<string, unknown>[]
    conditions?: Record<string, unknown>
    evidence_required?: string[]
    ai_process?: Record<string, unknown>
    cross_references?: Record<string, unknown>[]
  }
}

async function get<T>(path: string, params?: Record<string, unknown>): Promise<T> {
  let qs = ''
  if (params) {
    const entries = Object.entries(params)
      .filter(([, v]) => v !== undefined && v !== null)
      .map(([k, v]) => [k, String(v)] as [string, string])
    qs = '?' + new URLSearchParams(entries).toString()
  }
  const res = await fetch(`/api${path}${qs}`)
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`)
  return (await res.json()) as T
}

export const api = {
  listCompanies: (q?: string) => get<{ companies: CompanyBrief[] }>('/companies', { q }),
  companyDetail: (code: string) => get<Record<string, unknown>>(`/companies/${code}`),
  listAnalyses: () => get<{ analyses: AnalysisBrief[] }>('/analyses'),
  getResult: (code: string, year: number) => get<ResultView>(`/analyses/${code}/${year}`),
  getPlan: (code: string, year: number, direction: string) =>
    get<PlanDetail>(`/analyses/${code}/${year}/plans/${encodeURIComponent(direction)}`),
  getOpinion: (code: string, year: number, direction: string, refresh = false) =>
    post<Opinion>(`/analyses/${code}/${year}/plans/${encodeURIComponent(direction)}/opinion?refresh=${refresh}`, {}),
  kbSearch: (q: string, corpus = 'policy', top_k = 5) =>
    get<{ corpus: string; hits: Record<string, unknown>[] }>('/kb/search', { q, corpus, top_k }),
  knowledgeIndex: () => get<Record<string, unknown>>('/knowledge/index'),
  rebuildKnowledge: (backend = 'auto') =>
    post<JobStatus>(`/knowledge/rebuild?backend=${backend}`, {}),
  settings: () => get<Record<string, unknown>>('/settings'),
  saveSettings: (body: Record<string, unknown>) =>
    post<{ ok: boolean; chat_model: string; embedding_model: string }>('/settings', body),
  testSettings: () => post<Record<string, unknown>>('/settings/test', {}),
  metaStats: () => get<Record<string, Record<string, unknown>>>('/meta/stats'),
  fieldLabels: () => get<{ labels: Record<string, string> }>('/field-labels'),
  evidencePending: () => get<{ items: Record<string, unknown>[] }>('/evidence/pending'),
  evidenceConflicts: (code: string, year: number) =>
    get<{ items: Record<string, unknown>[] }>('/evidence/conflicts', { code, year }),
  evidenceResolve: (id: string, choice: 'evidence' | 'profile') =>
    post<{ result: string }>(`/evidence/${id}/resolve?choice=${choice}`, {}),
  reviewEvidence: (id: string, action: string, value?: string) =>
    post<{ status: string }>(`/evidence/${id}/review?action=${action}${value ? `&value=${encodeURIComponent(value)}` : ''}`, {}),

  // 生成（异步 job）
  runAnalysis: (body: { stock_code: string; year: number; use_llm: boolean; top_k?: number; rounds?: number; force?: boolean }) =>
    post<JobStatus>('/analyses/run', body),
  metrics: (code: string, year: number) => get<Record<string, unknown>>(`/analyses/${code}/${year}/metrics`),
  getJob: (id: string) => get<JobStatus>(`/jobs/${id}`),

  // 审查
  reviewSkills: () => get<{ skills: Record<string, unknown>[] }>('/review/skills'),
  reviewSkill: (id: string) => get<Record<string, unknown>>(`/review/skills/${id}`),
  reviewEngine: () => get<Record<string, unknown>>('/review/engine'),
  updateThresholds: (content: string) => put<{ ok: boolean }>('/review/thresholds', { content }),
  updateThresholdsCore: (body: { gate?: Record<string, unknown>; signals?: Record<string, unknown>; category_thresholds?: Record<string, unknown> }) =>
    put<{ ok: boolean }>('/review/thresholds', body),
  updateSkill: (id: string, body: Record<string, unknown>) => put<{ ok: boolean }>(`/review/skills/${id}`, body),
  solutions: (status?: string) => get<{ solutions: Record<string, unknown>[] }>('/solutions', { status }),
  reviewSolution: (id: string, status: string, note = '') =>
    post<Record<string, unknown>>(`/solutions/${id}/review?status=${encodeURIComponent(status)}&note=${encodeURIComponent(note)}`, {}),
  deleteSolution: (id: string) => del<{ ok: boolean; deleted: number }>(`/solutions/${id}`),

  // 补充材料（预览 → 提交）
  factTypes: () => get<{ fact_types: { key: string; label: string; desc: string }[] }>('/fact-types'),
  ingestText: (body: { stock_code: string; year: number; text: string; importance: string; fact_type: string; unit?: string; caliber?: string }) =>
    post<IngestPreview>('/ingest/text', body),
  ingestCommit: (items: Record<string, unknown>[]) =>
    post<{ ids: string[]; count: number }>('/ingest/commit', { items }),
}

export interface IngestPreview {
  candidates: Record<string, unknown>[]
  issues: string[]
}

export async function ingestFile(file: File, stock_code: string, year: number, importance: string): Promise<IngestPreview> {
  const fd = new FormData()
  fd.append('file', file)
  fd.append('stock_code', stock_code)
  fd.append('year', String(year))
  fd.append('importance', importance)
  const res = await fetch('/api/ingest/file', { method: 'POST', body: fd })
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`)
  return (await res.json()) as IngestPreview
}

export interface JobStatus {
  job_id: string
  status: 'PENDING' | 'RUNNING' | 'COMPLETED' | 'COMPLETED_WITH_WARNINGS' | 'FAILED'
  pct: number
  stock_code?: string
  year?: number
  use_llm?: boolean
  stages: { name: string; pct: number; at: string }[]
  error?: string | null
  warnings?: string[]
  started_at?: string | null
  finished_at?: string | null
  updated_at?: string | null
}

async function post<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`/api${path}`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  })
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`)
  return (await res.json()) as T
}

export const reportUrl = (code: string, year: number) => `/api/analyses/${code}/${year}/report`
export const reportPdfUrl = (code: string, year: number) => `/api/analyses/${code}/${year}/report.pdf`
export const planExportUrl = (code: string, year: number, direction: string) =>
  `/api/analyses/${code}/${year}/plans/${encodeURIComponent(direction)}/export`
export const planExportPdfUrl = (code: string, year: number, direction: string) =>
  `/api/analyses/${code}/${year}/plans/${encodeURIComponent(direction)}/export.pdf`
export const opinionExportUrl = (code: string, year: number, direction: string) =>
  `/api/analyses/${code}/${year}/plans/${encodeURIComponent(direction)}/opinion.md`
export const opinionExportPdfUrl = (code: string, year: number, direction: string) =>
  `/api/analyses/${code}/${year}/plans/${encodeURIComponent(direction)}/opinion.pdf`

async function put<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`/api${path}`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  })
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`)
  return (await res.json()) as T
}

async function del<T>(path: string): Promise<T> {
  const res = await fetch(`/api${path}`, { method: 'DELETE' })
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`)
  return (await res.json()) as T
}
