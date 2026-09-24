import { useEffect, useState } from 'react'
import { api } from '../api'
import { nav } from '../router'
import { BackButton } from '../components/Nav'
import { friendlyError } from '../errors'

const CONC_LABEL: Record<string, string> = {
  READY: '就绪', CONDITIONAL: '有条件', DATA_GAP: '数据缺口',
  CALCULATION_GAP: '计算缺口', POLICY_GAP: '政策缺口', REVIEW_REQUIRED: '需人工审查',
}
const CONC_KEY: Record<string, string> = {
  READY: 'CONFIRMED', CONDITIONAL: 'SCENARIO', DATA_GAP: 'DATA_GAP',
  CALCULATION_GAP: 'DATA_GAP', POLICY_GAP: 'DATA_GAP', REVIEW_REQUIRED: 'NOT_RECOMMEND',
}

export default function ReviewSkills() {
  const [rows, setRows] = useState<Record<string, any>[]>([])
  const [q, setQ] = useState('')
  const [err, setErr] = useState('')
  useEffect(() => { api.reviewSkills().then((r) => setRows(r.skills)).catch((e) => setErr(friendlyError(e))) }, [])

  const filtered = rows.filter((r) => !q || String(r.skill_id).includes(q) || String(r.name).includes(q) || String(r.direction).includes(q))

  return (
    <div className="container">
      <BackButton label="返回审查" />
      <div className="topbar">
        <h2>Skill 审查（{rows.length}）</h2>
        <div className="searchbar"><input type="search" placeholder="搜索 Skill / 方向" value={q} onChange={(e) => setQ(e.target.value)} /></div>
      </div>
      {err && <div className="card" style={{ color: 'var(--bad)' }}>{err}</div>}
      <div className="card">
        <table>
          <thead><tr><th>Skill</th><th>方向</th><th>计算器</th><th>代理输入</th><th>初判结论</th><th></th></tr></thead>
          <tbody>
            {filtered.map((r) => {
              const c = String(r['初判_结论'] || '')
              return (
                <tr key={r.skill_id}>
                  <td><b>{r.name}</b><div className="note">{r.skill_id}</div></td>
                  <td>{r.direction}</td>
                  <td className="muted">{r.calc || '（无）'}</td>
                  <td>{r.has_proxy_input ? '△ 有' : '—'}</td>
                  <td><span className={`badge ${CONC_KEY[c] || 'DATA_GAP'}`}>{CONC_LABEL[c] || c}</span></td>
                  <td><a onClick={() => nav(`/review/skills/${r.skill_id}`)}>详情 →</a></td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}
