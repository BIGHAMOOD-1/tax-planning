// 全局字段中文名映射（Update 6.3）：UI 统一展示，避免露出字段名。
import { api } from './api'

let MAP: Record<string, string> = {}
let loading: Promise<void> | null = null

export function loadFieldLabels(): Promise<void> {
  if (loading) return loading
  loading = api.fieldLabels()
    .then((r) => { MAP = r.labels || {} })
    .catch(() => { MAP = {} })
  return loading
}

/** 字段 -> 人类名；未知字段回退为原值（尽量不露，但保底可读）。 */
export function fieldLabel(f?: string | null): string {
  if (!f) return '—'
  return MAP[f] || f
}

/** 批量：把字段数组转成中文名数组。 */
export function fieldLabels(fields?: string[] | null): string[] {
  return (fields || []).map((f) => fieldLabel(f))
}
