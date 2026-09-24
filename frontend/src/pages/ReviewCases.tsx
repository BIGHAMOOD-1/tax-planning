import { useEffect, useState } from 'react'
import { api } from '../api'
import { nav } from '../router'
import { BackButton } from '../components/Nav'
import { friendlyError } from '../errors'
import { useToast } from '../components/Toast'
import { Modal } from '../components/Modal'
import { Empty } from '../components/Empty'

export default function ReviewCases() {
  const [rows, setRows] = useState<Record<string, any>[]>([])
  const [notes, setNotes] = useState<Record<string, string>>({})
  const [open, setOpen] = useState<Record<string, boolean>>({})
  const [delId, setDelId] = useState<string | null>(null)
  const toast = useToast()
  const load = () => api.solutions().then((r) => setRows(r.solutions)).catch((e) => toast('error', friendlyError(e)))
  useEffect(() => { load() }, [])  // eslint-disable-line react-hooks/exhaustive-deps

  const act = async (id: string, status: string) => {
    try { await api.reviewSolution(id, status, notes[id] || ''); load() }
    catch (e) { toast('error', friendlyError(e)) }
  }

  const remove = (id: string) => setDelId(id)
  const doDelete = async () => {
    if (!delId) return
    try { await api.deleteSolution(delId); toast('success', '已删除'); setDelId(null); load() }
    catch (e) { toast('error', friendlyError(e)) }
  }

  // 按 (公司, 年度) 聚合
  const groups: Record<string, Record<string, any>[]> = {}
  for (const r of rows) {
    const key = `${r.stock_code}_${r.year}`
    ;(groups[key] = groups[key] || []).push(r)
  }
  const keys = Object.keys(groups).sort()

  return (
    <div className="container">
      <BackButton label="返回审查" />
      <div className="topbar">
        <h2>案例评价</h2>
        <span className="muted">共 {keys.length} 家公司 · {rows.length} 个方向方案</span>
      </div>
      {keys.length === 0 && <div className="card"><Empty title="暂无方案" desc="请先在「生成」页运行一次分析。" /></div>}

      {keys.map((k) => {
        const items = groups[k]
        const r0 = items[0]
        const name = r0.company_name || ''
        const isOpen = !!open[k]
        const decisions = items.reduce((m: Record<string, number>, x) => { const d = x.final_decision || '-'; m[d] = (m[d] || 0) + 1; return m }, {})
        return (
          <div key={k} className="card">
            <div className="acc-head" onClick={() => setOpen({ ...open, [k]: !isOpen })}>
              <b style={{ fontSize: 16 }}>{r0.stock_code}　{name}</b>
              <span className="muted">{r0.year} 年度 · {items.length} 个方向</span>
              <div className="spacer" />
              <span className="muted">{Object.entries(decisions).map(([d, n]) => `${d} ${n}`).join(' / ')}</span>
              <span>{isOpen ? '▲' : '▼'}</span>
            </div>
            {isOpen && (
              <div className="acc-body">
                <table>
                  <thead><tr><th>方向</th><th>结论</th><th>状态</th><th>评审</th><th></th></tr></thead>
                  <tbody>
                    {items.map((r) => (
                      <tr key={r.solution_id}>
                        <td>{r.direction}<div className="fieldmeta">{r.skill_id}</div></td>
                        <td>{r.final_decision}</td>
                        <td><span className="badge DATA_GAP">{r.review_status || 'candidate'}</span></td>
                        <td>
                          <div className="row" style={{ gap: 6 }}>
                            <input type="text" placeholder="备注" value={notes[r.solution_id] || ''}
                                   onChange={(e) => setNotes({ ...notes, [r.solution_id]: e.target.value })} style={{ width: 140, height: 34 }} />
                            <button className="btn" style={{ height: 34 }} onClick={() => act(r.solution_id, 'accepted')}>采纳</button>
                            <button className="btn" style={{ height: 34 }} onClick={() => act(r.solution_id, 'rejected')}>驳回</button>
                            <button className="btn" style={{ height: 34 }} onClick={() => act(r.solution_id, 'modified')}>修改</button>
                          </div>
                        </td>
                        <td>
                          <a onClick={() => nav(`/results/${r.stock_code}/${r.year}/${encodeURIComponent(r.direction)}`)}>查看案例 →</a>
                          <a style={{ marginLeft: 10, color: 'var(--bad)' }} onClick={() => remove(r.solution_id)}>删除</a>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )
      })}

      {delId && (
        <Modal title="删除方案" danger confirmText="确认删除" onClose={() => setDelId(null)} onConfirm={doDelete}>
          确定删除该方案（案例）？此操作不可恢复。
        </Modal>
      )}
    </div>
  )
}
