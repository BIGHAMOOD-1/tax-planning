import { useEffect, useState } from 'react'
import { api } from '../api'

export default function DataCenter() {
  const [s, setS] = useState<Record<string, Record<string, unknown>> | null>(null)
  useEffect(() => { api.metaStats().then(setS).catch(() => {}) }, [])
  if (!s) return <div className="card">加载中…</div>
  const d = s.data || {}, k = s.knowledge || {}, e = s.evidence || {}
  return (
    <div>
      <div className="topbar"><h2>数据中心</h2><span className="muted">数据与知识库规模</span></div>
      <div className="grid">
        <div className="card">
          <b>企业数据</b>
          <div className="kv" style={{ marginTop: 8 }}>
            <div className="k">公司</div><div>{String(d.companies ?? '—')}</div>
            <div className="k">公司·年</div><div>{String(d.company_years ?? '—')}</div>
            <div className="k">年度</div><div>{Array.isArray(d.years) ? (d.years as number[]).join('–') : '—'}</div>
            <div className="k">画像字段</div><div>{String(d.profile_fields ?? '—')}</div>
            <div className="k">衍生字段</div><div>{String(d.derived_fields ?? '—')}</div>
          </div>
        </div>
        <div className="card">
          <b>知识库</b>
          <div className="kv" style={{ marginTop: 8 }}>
            <div className="k">政策</div><div>{String(k.policy_articles ?? '—')} 篇 · {String(k.policy_chunks ?? '—')} chunk</div>
            <div className="k">案例</div><div>{String(k.case_chunks ?? '—')} chunk</div>
          </div>
        </div>
        <div className="card">
          <b>外部证据</b>
          <div className="kv" style={{ marginTop: 8 }}>
            <div className="k">合计</div><div>{String(e.total ?? '—')}</div>
            <div className="k">已确认</div><div>{String(e.confirmed ?? '—')}</div>
            <div className="k">待评审</div><div>{String(e.pending ?? '—')}</div>
          </div>
        </div>
      </div>
    </div>
  )
}
