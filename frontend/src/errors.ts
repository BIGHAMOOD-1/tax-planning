// 友好错误：把后端/网络异常翻译成「人话 + 建议动作」（Update 6.3）。
export function friendlyError(e: unknown): string {
  const raw = e instanceof Error ? e.message : String(e ?? '')
  const s = raw.toLowerCase()
  if (s.includes('econnrefused') || s.includes('failed to fetch') || s.includes('networkerror') || s.includes('load failed'))
    return '无法连接后端服务：请确认服务已启动（双击「智能税务筹划」或运行 python run_server.py）。'
  if (s.includes('405') || s.includes('method not allowed'))
    return '接口不可用：后端可能未重启（代码已更新）。请重启后端服务后重试。'
  if (s.includes('404'))
    return '未找到对应数据：请确认企业代码/年度/方向是否正确，或先完成一次分析。'
  if (s.includes('429') || s.includes('rate limit') || s.includes('tpm'))
    return '调用过于频繁（触发限流）：请稍等片刻再试。'
  if (s.includes('timeout') || s.includes('timed out'))
    return '请求超时：模型或网络较慢，请稍后重试。'
  if (s.includes('401') || s.includes('403') || s.includes('unauthorized') || s.includes('invalid api key'))
    return 'API Key 无效或未配置：请到「设置」检查密钥后重试。'
  if (s.includes('500') || s.includes('502') || s.includes('503'))
    return '服务端错误：请稍后重试；若持续出现，请查看后端日志。'
  if (s.includes('尚未构建') || s.includes('npm'))
    return '前端资源异常：请确认已构建前端（npm run build）。'
  return raw ? (raw.length > 200 ? raw.slice(0, 200) + '…' : raw) : '操作失败，请重试。'
}
