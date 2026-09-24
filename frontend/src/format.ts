import type { StatusKey } from './api'

export function money(v: unknown): string {
  if (v === null || v === undefined || v === '') return '—'
  const f = Number(v)
  if (!isFinite(f)) return '—'
  const a = Math.abs(f)
  if (a >= 1e8) return `${(f / 1e8).toFixed(2)} 亿元`
  if (a >= 1e4) return `${(f / 1e4).toFixed(2)} 万元`
  return `${f.toFixed(2)} 元`
}

export function pct(v: unknown, digits = 1): string {
  if (v === null || v === undefined || v === '') return '—'
  const f = Number(v)
  return isFinite(f) ? `${(f * 100).toFixed(digits)}%` : '—'
}

export const STATUS_LABEL: Record<StatusKey, string> = {
  CONFIRMED: '已确认',
  SCENARIO: '情景/待确认',
  DATA_GAP: '证据不足',
  NOT_RECOMMEND: '不推荐',
}

export const RESOLUTION_LABEL: Record<string, string> = {
  DIRECT: 'DIRECT（直接）',
  PROXY: 'PROXY（代理）',
  INSUFFICIENT: 'INSUFFICIENT（不足）',
}
