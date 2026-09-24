import { useMemo, useState } from 'react'
import { SECTIONS, type Block } from '../help/content'
import { Empty } from '../components/Empty'

function renderBlock(b: Block, i: number) {
  if (b.kind === 'p') return <p key={i} className="help-p">{b.text}</p>
  if (b.kind === 'note') return <div key={i} className="help-note">{b.text}</div>
  if (b.kind === 'list') return <ul key={i} className="help-list">{b.items.map((x, k) => <li key={k}>{x}</li>)}</ul>
  if (b.kind === 'steps') return <ol key={i} className="help-steps">{b.items.map((x, k) => <li key={k}>{x}</li>)}</ol>
  if (b.kind === 'table') return (
    <table key={i} className="help-table">
      <thead><tr>{b.head.map((h, k) => <th key={k}>{h}</th>)}</tr></thead>
      <tbody>{b.rows.map((r, k) => <tr key={k}>{r.map((c, j) => <td key={j}>{c}</td>)}</tr>)}</tbody>
    </table>
  )
  return (
    <dl key={i} className="help-defs">
      {b.items.map((d, k) => (
        <div key={k} className="help-def">
          <dt>{d.term}{d.code && <code className="help-code">{d.code}</code>}</dt>
          <dd>{d.desc}</dd>
        </div>
      ))}
    </dl>
  )
}

function sectionText(s: (typeof SECTIONS)[number]): string {
  const parts: string[] = [s.title, s.summary]
  for (const b of s.blocks) {
    if (b.kind === 'defs') for (const d of b.items) parts.push(d.term, d.code || '', d.desc)
    else if (b.kind === 'table') parts.push(...b.head, ...b.rows.flat())
    else if (b.kind === 'list' || b.kind === 'steps') parts.push(...b.items)
    else parts.push(b.text)
  }
  return parts.join(' ').toLowerCase()
}

export default function Help() {
  const [q, setQ] = useState('')
  const [open, setOpen] = useState<Record<string, boolean>>({ [SECTIONS[0].id]: true })

  const visible = useMemo(() => {
    const kw = q.trim().toLowerCase()
    if (!kw) return SECTIONS
    return SECTIONS.filter((s) => sectionText(s).includes(kw))
  }, [q])

  const searching = q.trim().length > 0

  return (
    <div className="container-narrow">
      <div className="topbar">
        <h2>帮助 · 使用说明</h2>
        <span className="muted">术语解释与操作指引（点击标题展开）</span>
      </div>

      <div className="card">
        <div className="searchbar">
          <input aria-label="搜索帮助" value={q} onChange={(e) => setQ(e.target.value)}
                 placeholder="搜索术语或问题（如：税盾 / calculation_type / 证据链）" />
          {q && <button className="btn" onClick={() => setQ('')}>清空</button>}
        </div>
      </div>

      {!searching && (
        <>
          <div className="card">
            <h3>证据链（7 节点）</h3>
            <div className="chainflow">
              {['企业事实', '诊断信号', '税务方向', '政策依据', '条件核验', '税额计算', '最终结论'].map((n, i) => (
                <span key={n} className="cf-node"><b>{i + 1}</b>{n}</span>
              ))}
            </div>
            <div className="note" style={{ marginTop: 10 }}>旁支：审查说明（机会分析 / 合规审查）、Evidence / 来源。</div>
          </div>
          <div className="card">
            <h3>目录</h3>
            <div className="toc">
              {SECTIONS.map((s) => (
                <a key={s.id} onClick={() => {
                  setOpen((o) => ({ ...o, [s.id]: true }))
                  setTimeout(() => document.getElementById('help-' + s.id)?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 30)
                }}>{s.title}</a>
              ))}
            </div>
          </div>
        </>
      )}

      {visible.length === 0 && <div className="card"><Empty title="未找到匹配内容" desc="试试其它关键词，如「税盾」「证据链」。" /></div>}

      {visible.map((s) => {
        const isOpen = searching || open[s.id]
        return (
          <div className="card help-section" key={s.id} id={`help-${s.id}`}>
            <div className="help-head" role="button" tabIndex={0} aria-expanded={isOpen}
                 onClick={() => setOpen((o) => ({ ...o, [s.id]: !isOpen }))}
                 onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setOpen((o) => ({ ...o, [s.id]: !isOpen })) } }}>
              <div>
                <div className="help-title">{s.title}</div>
                <div className="help-sub">{s.summary}</div>
              </div>
              <span className="help-arrow">{isOpen ? '▲' : '▼'}</span>
            </div>
            {isOpen && <div className="help-body">{s.blocks.map(renderBlock)}</div>}
          </div>
        )
      })}
    </div>
  )
}
