import { useEffect, useState } from 'react'
import { api } from '../api'
import { money } from '../format'
import { BackButton } from '../components/Nav'
import { friendlyError } from '../errors'
import { useToast } from '../components/Toast'
import { Empty } from '../components/Empty'

function val(v: unknown) {
  return typeof v === 'number' ? money(v) : String(v ?? '—')
}

export default function ReviewConflicts() {
  const [code, setCode] = useState('600004')
  const [year, setYear] = useState(2024)
  const [items, setItems] = useState<Record<string, any>[]>([])
  const toast = useToast()

  const load = async () => {
    try {
      const r = await api.evidenceConflicts(code.trim(), year)
      setItems(r.items)
    } catch (e) { toast('error', friendlyError(e)) }
  }
  useEffect(() => { load() }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const resolve = async (id: string, choice: 'evidence' | 'profile') => {
    try {
      await api.evidenceResolve(id, choice)
      toast('success', choice === 'evidence' ? '已采纳证据（下次分析将覆盖画像）' : '已保留画像（证据已驳回）')
      load()
    } catch (e) { toast('error', friendlyError(e)) }
  }

  return (
    <div className="container">
      <BackButton label="返回审查" />
      <div className="topbar">
        <h2>证据冲突</h2>
        <span className="muted">证据与画像不一致：决定「以证据为准」或「保留画像」</span>
      </div>

      <div className="card">
        <div className="row" style={{ gap: 10, alignItems: 'flex-end' }}>
          <div><label className="field" htmlFor="cf-code">股票代码</label>
            <input id="cf-code" aria-label="股票代码" value={code} onChange={(e) => setCode(e.target.value)} /></div>
          <div><label className="field" htmlFor="cf-year">年度</label>
            <input id="cf-year" aria-label="年度" type="number" value={year} onChange={(e) => setYear(Number(e.target.value))} /></div>
          <button className="btn" onClick={load}>查询冲突</button>
        </div>
        <div className="note" style={{ marginTop: 8 }}>
          规则：manual / 权威结构化可覆盖画像；其余来源冲突默认不覆盖，需人工决定。
        </div>
      </div>

      {items.length === 0 && <div className="card"><Empty title="未发现冲突" desc="该企业没有与画像不一致的已确认证据。" /></div>}

      {items.length > 0 && (
        <div className="card">
          <h3>冲突 {items.length} 条</h3>
          <table>
            <thead><tr><th>字段</th><th className="num">画像值</th><th className="num">证据值</th>
              <th>来源</th><th>优先级</th><th>证据ID</th><th>操作</th></tr></thead>
            <tbody>
              {items.map((c, i) => (
                <tr key={i}>
                  <td>{c.field}</td>
                  <td className="num">{val(c.profile_value)}</td>
                  <td className="num">{val(c.value)}</td>
                  <td className="muted">{c.source_type}{c.extraction_method ? ` / ${c.extraction_method}` : ''}</td>
                  <td className="muted">{c.precedence}</td>
                  <td className="muted">{c.evidence_id}</td>
                  <td>
                    <button className="btn" onClick={() => resolve(c.evidence_id, 'evidence')}>以证据为准</button>
                    <button className="btn" style={{ marginLeft: 6 }} onClick={() => resolve(c.evidence_id, 'profile')}>保留画像</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
