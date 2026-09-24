export function Empty({ title = '暂无数据', desc, action, onAction }: {
  title?: string
  desc?: string
  action?: string
  onAction?: () => void
}) {
  return (
    <div className="empty">
      <div className="empty-title">{title}</div>
      {desc && <div className="empty-desc">{desc}</div>}
      {action && onAction && <button className="btn" style={{ marginTop: 12 }} onClick={onAction}>{action}</button>}
    </div>
  )
}
