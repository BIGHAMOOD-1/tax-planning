/** 链接规范化：政策相对路径补全官网域名；空值返回空串（不渲染为链接）。
 *
 * 后端多数接口已返回绝对链接，此处做前端兜底，避免出现 404 或空链接。
 */
const POLICY_BASE = 'https://fgk.chinatax.gov.cn'

export function absUrl(u?: unknown): string {
  const s = String(u ?? '').trim()
  if (!s) return ''
  if (s.startsWith('http://') || s.startsWith('https://')) return s
  if (s.startsWith('/')) return POLICY_BASE + s
  return s
}
