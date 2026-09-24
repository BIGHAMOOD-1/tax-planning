import { friendlyError } from '../errors'

export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  return (
    <div className="card error-state" role="alert">
      <div className="error-title">出错了</div>
      <div className="note">{friendlyError(error)}</div>
      {onRetry && <button className="btn" style={{ marginTop: 10 }} onClick={onRetry}>重试</button>}
    </div>
  )
}
