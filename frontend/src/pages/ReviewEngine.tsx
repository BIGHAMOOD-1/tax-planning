import { useEffect, useState } from 'react'
import { api } from '../api'
import { BackButton } from '../components/Nav'
import { Skeleton } from '../components/Skeleton'

const RES_MEAN: Record<string, string> = {
  DIRECT: '直接事实：可进入确定性计算。',
  PROXY: '代理变量：仅能作为假设测算（情景测算），不产出确认影响。',
  INSUFFICIENT: '证据不足：禁止计算。',
}

export default function ReviewEngine() {
  const [e, setE] = useState<Record<string, any> | null>(null)
  const [gate, setGate] = useState<Record<string, any>>({})
  const [signals, setSignals] = useState<Record<string, number>>({})
  const [cats, setCats] = useState<Record<string, any>>({})
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')
  const [diagModal, setDiagModal] = useState<string | null>(null)
  const [resModal, setResModal] = useState<string | null>(null)

  const load = () => api.reviewEngine().then((d) => {
    setE(d)
    setGate({ ...((d.gate as object) || {}) })
    setSignals({ ...((d.signals as object) || {}) })
    setCats({ ...(((d.diagnostics as any)?.category_thresholds) || {}) })
  }).catch((x) => setErr(String(x)))
  useEffect(() => { load() }, [])

  if (err) return <div className="container"><div className="card" style={{ color: 'var(--bad)' }}>{err}</div></div>
  if (!e) return <div className="container"><Skeleton lines={6} /></div>

  const items = (e.diagnostics?.items || {}) as Record<string, any>
  const fr = (e.fact_resolution || {}) as Record<string, any>
  const sl = (e.signal_labels || {}) as Record<string, any>
  const dm = (e.diagnostic_meta || {}) as Record<string, any>
  const fl = (e.field_labels || {}) as Record<string, any>
  const num = (v: any) => (typeof v === 'number' ? v : Number(v))

  const save = async () => {
    if (!window.confirm('确定修改阈值？该改动会影响后续所有分析。')) return
    setMsg('')
    try { await api.updateThresholdsCore({ gate, signals, category_thresholds: cats }); setMsg('已保存。') }
    catch (x) { setMsg('保存失败：' + String(x)) }
  }

  return (
    <div className="container">
      <BackButton label="返回审查" />
      <div className="topbar"><h2>审查引擎</h2><button className="btn primary" onClick={save}>保存阈值</button></div>
      {msg && <div className="card">{msg}</div>}

      <div className="card">
        <h3>Gate</h3>
        <div className="grid">
          <div>
            <label className="field">L2 最少核心异常数（min_core_anomalies）</label>
            <input type="text" value={gate.min_core_anomalies ?? ''} onChange={(ev) => setGate({ ...gate, min_core_anomalies: num(ev.target.value) })} style={{ width: '100%' }} />
          </div>
          <div>
            <label className="field">缺数据 → WEAK（weak_on_data_gap）</label>
            <select value={String(gate.weak_on_data_gap)} onChange={(ev) => setGate({ ...gate, weak_on_data_gap: ev.target.value === 'true' })} style={{ width: '100%' }}>
              <option value="true">true</option><option value="false">false</option>
            </select>
          </div>
        </div>
      </div>

      <div className="card">
        <h3>信号阈值</h3>
        <div className="grid" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(320px, 1fr))' }}>
          {Object.entries(signals).map(([k, v]) => (
            <div key={k}>
              <label className="field">{sl[k]?.name || k}</label>
              <input type="text" value={String(v)} onChange={(ev) => setSignals({ ...signals, [k]: num(ev.target.value) })} style={{ width: '100%' }} />
              <div className="fieldmeta">{sl[k]?.desc || ''}　<span className="pill">{k}</span></div>
            </div>
          ))}
        </div>
      </div>

      <div className="card">
        <h3>诊断严重度阈值</h3>
        <table>
          <thead><tr><th>类别</th><th>提示</th><th>观察</th><th>关注</th></tr></thead>
          <tbody>
            {Object.entries(cats).map(([cat, lv]: any) => (
              <tr key={cat}>
                <td>{cat}</td>
                {['提示', '观察', '关注'].map((level) => (
                  <td key={level}>
                    <input type="text" value={lv[level] ?? ''} style={{ width: 90 }}
                           onChange={(ev) => setCats({ ...cats, [cat]: { ...lv, [level]: num(ev.target.value) } })} />
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="card">
        <h3>诊断规则（{Object.keys(items).length}）</h3>
        <table>
          <thead><tr><th>诊断</th><th>类别</th><th>gate</th><th>严重度封顶</th><th></th></tr></thead>
          <tbody>
            {Object.entries(items).map(([k, v]: any) => (
              <tr key={k}>
                <td>{dm[k]?.name || k}<div className="fieldmeta">{k}</div></td>
                <td>{v.category}</td>
                <td>{v.gate === false ? '✕ 不触发' : '✓ 触发'}</td>
                <td>{v.max_severity || '—'}</td>
                <td><a onClick={() => setDiagModal(k)}>查看 →</a></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="card">
        <h3>字段 Resolution（{Object.keys(fr).length}）</h3>
        <table>
          <thead><tr><th>字段</th><th>默认 Resolution</th><th>代理对象</th><th>资格类</th><th></th></tr></thead>
          <tbody>
            {Object.entries(fr).map(([k, v]: any) => (
              <tr key={k}>
                <td>{fl[k]?.label || k}<div className="fieldmeta">{k}</div></td>
                <td>{v.default_resolution || '—'}</td>
                <td className="muted">{v.of || '—'}</td>
                <td>{v.qualification_proxy ? '✓' : '—'}</td>
                <td><a onClick={() => setResModal(k)}>说明 →</a></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {diagModal && (
        <div className="modal-mask" onClick={() => setDiagModal(null)}>
          <div className="modal" onClick={(ev) => ev.stopPropagation()}>
            <h3>{dm[diagModal]?.name || diagModal}</h3>
            <div className="kv">
              <div className="k">ID / 类别</div><div>{diagModal} · {dm[diagModal]?.category || items[diagModal]?.category}</div>
              <div className="k">判据</div><div>{dm[diagModal]?.judge || '—'}</div>
              <div className="k">公式</div><div>{dm[diagModal]?.formula || '—'}</div>
              <div className="k">涉及字段</div><div>{(dm[diagModal]?.fields || []).join('、') || '—'}</div>
              <div className="k">关联方向</div><div>{dm[diagModal]?.direction || '—'}</div>
              <div className="k">需补证据</div><div>{(dm[diagModal]?.evidence || []).join('、') || '—'}</div>
              <div className="k">gate 开关</div><div>{items[diagModal]?.gate === false ? '不触发方向升级' : '触发方向升级'}</div>
              <div className="k">严重度封顶</div><div>{items[diagModal]?.max_severity || '无'}</div>
            </div>
            <div className="row" style={{ marginTop: 16, justifyContent: 'flex-end' }}>
              <button className="btn" onClick={() => setDiagModal(null)}>关闭</button>
            </div>
          </div>
        </div>
      )}

      {resModal && (
        <div className="modal-mask" onClick={() => setResModal(null)}>
          <div className="modal" onClick={(ev) => ev.stopPropagation()}>
            <h3>{fl[resModal]?.label || resModal}</h3>
            <div className="kv">
              <div className="k">字段</div><div>{resModal}</div>
              <div className="k">来源</div><div>{fl[resModal]?.source || '—'}</div>
              {fl[resModal]?.formula && <><div className="k">计算</div><div>{fl[resModal].formula}</div></>}
              <div className="k">默认 Resolution</div><div>{fr[resModal]?.default_resolution || '—'}</div>
              <div className="k">含义</div><div>{RES_MEAN[fr[resModal]?.default_resolution] || '—'}</div>
              <div className="k">代理对象</div><div>{fr[resModal]?.of || '—'}</div>
              <div className="k">资格类</div><div>{fr[resModal]?.qualification_proxy ? '是（不可由财务指标替代资格事实）' : '否'}</div>
            </div>
            <div className="row" style={{ marginTop: 16, justifyContent: 'flex-end' }}>
              <button className="btn" onClick={() => setResModal(null)}>关闭</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
