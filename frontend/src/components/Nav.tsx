import { nav } from '../router'

export function BackButton({ label = '返回' }: { label?: string }) {
  return <div className="back" onClick={() => window.history.back()}>← {label}</div>
}

export function Crumbs({ items }: { items: { label: string; to?: string }[] }) {
  return (
    <div className="crumb">
      {items.map((it, i) => (
        <span key={i}>
          {it.to ? <a onClick={() => nav(it.to!)}>{it.label}</a> : <span>{it.label}</span>}
          {i < items.length - 1 && <span> / </span>}
        </span>
      ))}
    </div>
  )
}
