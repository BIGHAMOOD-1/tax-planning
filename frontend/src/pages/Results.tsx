import { useEffect, useState } from 'react'
import { api, type AnalysisBrief } from '../api'
import { nav } from '../router'
import { friendlyError } from '../errors'
import { Empty } from '../components/Empty'

export default function Results() {
  const [items, setItems] = useState<AnalysisBrief[]>([])
  const [q, setQ] = useState('')
  const [err, setErr] = useState('')

  useEffect(() => { api.listAnalyses().then((r) => setItems(r.analyses)).catch((e) => setErr(friendlyError(e))) }, [])

  const filtered = items.filter((a) => !q || a.stock_code.includes(q) || (a.name || '').includes(q))

  return (
    <div className="container">
      <div className="topbar">
        <h2>生成结果</h2>
        <div className="searchbar">
          <input type="search" placeholder="搜索代码 / 名称" value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
      </div>
      {err && <div className="card" style={{ color: 'var(--bad)' }}>{err}</div>}
      {filtered.length === 0 && <div className="card"><Empty title="暂无分析结果" desc="请在「生成」页运行一次分析。" action="去生成" onAction={() => nav('/generate')} /></div>}
      {filtered.map((a) => (
        <div key={`${a.stock_code}-${a.year}`} className="card hoverable" onClick={() => nav(`/results/${a.stock_code}/${a.year}`)}>
          <div className="row">
            <div style={{ fontSize: 16, fontWeight: 600 }}>{a.stock_code}　{a.name}</div>
            <span className="muted">{a.year} 年度 · {a.generated_at}</span>
            <div className="spacer" />
            <span className="muted">方向 {a.analyzed} · 有条件 {a.conditional} / 证据不足 {a.insufficient}</span>
          </div>
          {a.brief && <div style={{ marginTop: 10 }}>简要建议：{a.brief}</div>}
          <div className="row" style={{ marginTop: 12 }}>
            <span className="muted">{a.industry}</span>
            <div className="spacer" />
            <a>查看分析结果 →</a>
          </div>
        </div>
      ))}
    </div>
  )
}
