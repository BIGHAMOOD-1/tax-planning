import { useEffect, useState } from 'react'
import { api } from '../api'
import { BackButton } from '../components/Nav'
import { Skeleton } from '../components/Skeleton'
import { friendlyError } from '../errors'

export default function ReviewSkillDetail({ skillId }: { skillId: string }) {
  const [d, setD] = useState<Record<string, any> | null>(null)
  const [err, setErr] = useState('')
  const [editing, setEditing] = useState(false)
  const [goal, setGoal] = useState('')
  const [ev, setEv] = useState('')
  const [risks, setRisks] = useState('')
  const [conds, setConds] = useState<any[]>([])
  const [msg, setMsg] = useState('')

  const load = () => api.reviewSkill(skillId).then(setD).catch((e) => setErr(friendlyError(e)))
  useEffect(() => { load() }, [skillId])

  if (err) return <div className="container"><div className="card" style={{ color: 'var(--bad)' }}>{err}</div></div>
  if (!d) return <div className="container"><Skeleton lines={6} /></div>

  const a = d.audit || {}
  const dep = (d.dependency || []) as Record<string, any>[]
  const sk = d.skill || {}
  const eligConds = ((sk.conditions || []) as any[]).filter((c) => c.stage === 'eligibility')

  const openEdit = () => {
    setGoal(sk.goal || '')
    setEv((sk.evidence_required || []).join('\n'))
    setRisks((sk.risks || []).join('\n'))
    setConds(eligConds.map((c) => ({ ...c })))
    setMsg(''); setEditing(true)
  }
  const save = async () => {
    if (!window.confirm('确定保存修改？将写回该 Skill 的 YAML 文件。')) return
    try {
      await api.updateSkill(skillId, {
        goal,
        evidence_required: ev.split('\n').map((s) => s.trim()).filter(Boolean),
        risks: risks.split('\n').map((s) => s.trim()).filter(Boolean),
        eligibility_conditions: conds.map((c) => ({ field: c.field, op: c.op, value: c.value, desc: c.desc, stage: 'eligibility' })),
      })
      setEditing(false); setMsg('已保存'); load()
    } catch (e) { setMsg('保存失败：' + friendlyError(e)) }
  }

  return (
    <div className="container">
      <BackButton label="返回 Skill 审查" />
      <div className="topbar">
        <h2>{a.name} · 审查详情</h2>
        <button className="btn primary" onClick={openEdit}>编辑</button>
      </div>
      {msg && <div className="card">{msg}</div>}

      <div className="card">
        <h3>基本信息</h3>
        <div className="kv">
          <div className="k">目标</div><div>{sk.goal || '—'}</div>
          <div className="k">方向 / 版本</div><div>{a.direction} · v{a.version}（{a.effective_from || '—'} ~ {a.effective_to || '—'}）</div>
          <div className="k">初判结论</div><div>{a['初判_结论']}（{a['初判_适用场景']} · {a['初判_口径']}）</div>
        </div>
      </div>

      <div className="card">
        <h3>计算说明</h3>
        <div className="kv">
          <div className="k">计算器</div><div>{sk.calculation || '（无确定性计算器）'}</div>
          <div className="k">输出字段</div><div>{sk.output_schema ? Object.keys(sk.output_schema).join('、') : '—'}</div>
        </div>
        {(sk.calculation_adjustments || []).length > 0 && (
          <div style={{ marginTop: 10 }}>
            <div className="muted">口径调整</div>
            {sk.calculation_adjustments.map((x: string, i: number) => <div key={i} className="note">- {x}</div>)}
          </div>
        )}
        {(sk.evidence_required || []).length > 0 && (
          <div style={{ marginTop: 10 }}>
            <div className="muted">所需证据</div>
            {sk.evidence_required.map((x: string, i: number) => <div key={i} className="note">□ {x}</div>)}
          </div>
        )}
      </div>

      <div className="card">
        <h3>资格条件</h3>
        <table>
          <thead><tr><th>字段</th><th>操作</th><th>阈值</th><th>说明</th></tr></thead>
          <tbody>
            {eligConds.map((c: any, i: number) => (
              <tr key={i}><td>{c.field}</td><td>{c.op}</td><td>{String(c.value)}</td><td className="muted">{c.desc}</td></tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="card">
        <h3>数据依赖（Required Facts）</h3>
        <table>
          <thead><tr><th>事实</th><th>字段</th><th>来源</th><th>可用性</th><th>覆盖率</th><th>Resolution</th></tr></thead>
          <tbody>
            {dep.map((r, i) => (
              <tr key={i}><td>{r.fact}</td><td className="muted">{r.linked_field}</td><td className="muted">{r.source_kind}</td>
                <td>{r.availability}</td><td className="num">{r.coverage_2020_2024}</td>
                <td>{r.default_resolution}{r.proxy_of ? `（${r.proxy_of}）` : ''}</td></tr>
            ))}
          </tbody>
        </table>
      </div>

      {(sk.risks || []).length > 0 && (
        <div className="card">
          <h3>风险与限制</h3>
          {sk.risks.map((r: string, i: number) => <div key={i} className="note">- {r}</div>)}
        </div>
      )}

      {editing && (
        <div className="modal-mask" onClick={() => setEditing(false)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <h3>编辑 Skill：{a.name}</h3>
            <label className="field">目标</label>
            <textarea rows={3} value={goal} onChange={(e) => setGoal(e.target.value)} style={{ width: '100%' }} />

            <label className="field" style={{ marginTop: 12 }}>资格条件</label>
            {conds.map((c, i) => (
              <div className="row" key={i} style={{ marginBottom: 6 }}>
                <input type="text" value={c.field} onChange={(e) => setConds(conds.map((x, j) => j === i ? { ...x, field: e.target.value } : x))} placeholder="字段" style={{ width: 160 }} />
                <select value={c.op} onChange={(e) => setConds(conds.map((x, j) => j === i ? { ...x, op: e.target.value } : x))}>
                  {['gt', 'ge', 'lt', 'le', 'eq', 'ne', 'in', 'not_in', 'contains'].map((o) => <option key={o}>{o}</option>)}
                </select>
                <input type="text" value={String(c.value)} onChange={(e) => setConds(conds.map((x, j) => j === i ? { ...x, value: e.target.value } : x))} placeholder="阈值" style={{ width: 140 }} />
                <input type="text" value={c.desc || ''} onChange={(e) => setConds(conds.map((x, j) => j === i ? { ...x, desc: e.target.value } : x))} placeholder="说明" style={{ flex: 1 }} />
                <button className="btn" onClick={() => setConds(conds.filter((_, j) => j !== i))}>删</button>
              </div>
            ))}
            <button className="btn" onClick={() => setConds([...conds, { field: '', op: 'gt', value: '', desc: '' }])}>+ 添加条件</button>

            <label className="field" style={{ marginTop: 12 }}>所需证据（每行一条）</label>
            <textarea rows={4} value={ev} onChange={(e) => setEv(e.target.value)} style={{ width: '100%' }} />

            <label className="field" style={{ marginTop: 12 }}>风险与限制（每行一条）</label>
            <textarea rows={4} value={risks} onChange={(e) => setRisks(e.target.value)} style={{ width: '100%' }} />

            <div className="row" style={{ marginTop: 16, justifyContent: 'flex-end' }}>
              <button className="btn" onClick={() => setEditing(false)}>取消</button>
              <button className="btn primary" onClick={save}>保存</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
