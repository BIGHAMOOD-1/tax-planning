import { nav } from '../router'

export default function ReviewHome() {
  const cards = [
    { title: 'Skill 审查', desc: '24 个 Tax Skill 的适用场景、数据依赖、口径与结论', to: '/review/skills' },
    { title: '审查引擎', desc: '诊断规则、阈值（可编辑）、字段 Resolution', to: '/review/engine' },
    { title: '案例评价', desc: '对生成方案进行采纳 / 驳回 / 修改', to: '/review/cases' },
    { title: '证据冲突', desc: '证据与画像不一致时，决定「以证据为准」或「保留画像」', to: '/review/conflicts' },
  ]
  return (
    <div className="container">
      <div className="topbar"><h2>审查</h2><span className="muted">对 Skill、审查引擎与生成案例进行评估</span></div>
      <div className="grid">
        {cards.map((c) => (
          <div key={c.to} className="card hoverable" onClick={() => nav(c.to)}>
            <div style={{ fontSize: 16, fontWeight: 600 }}>{c.title}</div>
            <div className="note" style={{ marginTop: 8 }}>{c.desc}</div>
            <div style={{ marginTop: 14 }}><a>进入 →</a></div>
          </div>
        ))}
      </div>
    </div>
  )
}
