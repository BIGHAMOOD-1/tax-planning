import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { api, ingestFile, type CompanyBrief, type IngestPreview, type JobStatus } from '../api'
import { nav } from '../router'
import { friendlyError } from '../errors'
import { useToast } from '../components/Toast'

function PreviewPanel({ p, onCommit, onClear }: { p: IngestPreview; onCommit: () => void; onClear: () => void }) {
  const cands = p.candidates as Record<string, any>[]
  return (
    <div className="card" style={{ background: '#fbfcfe' }}>
      <div className="row">
        <b>预览：{cands.length} 条候选</b>
        <div className="spacer" />
        <button className="btn" onClick={onClear}>清除</button>
        <button className="btn primary" onClick={onCommit} disabled={cands.length === 0}>确认入库</button>
      </div>
      {p.issues.length > 0 && <div className="note" style={{ color: 'var(--proxy)', marginTop: 6 }}>校验提示：{p.issues.slice(0, 5).join('；')}</div>}
      <table style={{ marginTop: 8 }}>
        <thead><tr><th>事实类型</th><th>值</th><th>来源</th><th>状态</th></tr></thead>
        <tbody>
          {cands.slice(0, 20).map((c, i) => (
            <tr key={i}>
              <td>{String(c.fact_type)}</td>
              <td>{String(c.value_num ?? c.value_text ?? '')}</td>
              <td className="muted">{String(c.source_type)}{c.caliber ? ` / ${c.caliber}` : ''}</td>
              <td>{String(c.verification_status)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export default function Generate() {
  const [code, setCode] = useState('600004')
  const [name, setName] = useState('白云机场')
  const [year, setYear] = useState(2024)
  const [useLlm, setUseLlm] = useState(true)
  const [force, setForce] = useState(false)
  const [sug, setSug] = useState<CompanyBrief[]>([])
  const [showSug, setShowSug] = useState(false)
  const [job, setJob] = useState<JobStatus | null>(null)
  const timer = useRef<number | null>(null)
  const toast = useToast()

  // 补充材料
  const [factTypes, setFactTypes] = useState<{ key: string; label: string; desc: string }[]>([])
  const [text, setText] = useState('')
  const [importance, setImportance] = useState('direct')
  const [factType, setFactType] = useState('')
  const [unit, setUnit] = useState('')
  const [caliber, setCaliber] = useState('')
  const [textPreview, setTextPreview] = useState<IngestPreview | null>(null)
  const [filePreview, setFilePreview] = useState<IngestPreview | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)
  const [committed, setCommitted] = useState(0)

  useEffect(() => () => { if (timer.current) window.clearInterval(timer.current) }, [])
  useEffect(() => { api.factTypes().then((r) => setFactTypes(r.fact_types)).catch(() => {}) }, [])
  useEffect(() => {
    if (!showSug) return
    const t = setTimeout(() => { api.listCompanies(code.trim()).then((r) => setSug(r.companies.slice(0, 12))).catch(() => {}) }, 220)
    return () => clearTimeout(t)
  }, [code, showSug])

  const poll = (id: string) => {
    timer.current = window.setInterval(async () => {
      try {
        const j = await api.getJob(id); setJob(j)
        const done = j.status === 'COMPLETED' || j.status === 'COMPLETED_WITH_WARNINGS'
        if (done) { if (timer.current) window.clearInterval(timer.current); setTimeout(() => nav(`/results/${j.stock_code}/${j.year}`), 900) }
        else if (j.status === 'FAILED') { if (timer.current) window.clearInterval(timer.current) }
      } catch (e) { toast('error', friendlyError(e)) }
    }, 1500)
  }
  const start = async () => {
    if (!code.trim()) { toast('error', '请先输入股票代码或企业名称'); return }
    try { const j = await api.runAnalysis({ stock_code: code.trim(), year, use_llm: useLlm, force }); setJob(j); poll(j.job_id) }
    catch (e) { toast('error', friendlyError(e)) }
  }
  const doPreviewText = async () => {
    try { setTextPreview(await api.ingestText({ stock_code: code.trim(), year, text, importance, fact_type: factType, unit, caliber })) }
    catch (e) { toast('error', friendlyError(e)) }
  }
  const doUpload = async (f: File) => {
    try { setFilePreview(await ingestFile(f, code.trim(), year, 'reference')) }
    catch (e) { toast('error', friendlyError(e)) }
  }
  const commit = async (p: IngestPreview | null, clear: () => void) => {
    if (!p) return
    try {
      const r = await api.ingestCommit(p.candidates)
      setCommitted((c) => c + 1)
      toast('success', `已入库 ${r.count} 条。可点「重新分析（应用补充材料）」让新事实参与计算。`)
      clear()
    } catch (e) { toast('error', friendlyError(e)) }
  }

  if (job) {
    return (
      <div className="container-narrow">
        {createPortal(<div className="debate-bg"><span className="blob red" /><span className="blob blue" /></div>, document.body)}
        <div className="topbar"><h2>正在分析</h2><span className="muted">{job.stock_code} · {job.year} · {job.use_llm ? 'LLM' : '规则'}模式</span></div>
        <div className="warn" style={{ marginBottom: 16 }}>⚠ 生成期间请勿关闭或刷新本页面（LLM 模式约数分钟）。</div>
        <div className={`card${job.status === 'RUNNING' ? ' breathing' : ''}`}>
          <div className="row"><b>{job.status === 'FAILED' ? '生成失败'
            : job.status === 'COMPLETED' ? '生成完成'
              : job.status === 'COMPLETED_WITH_WARNINGS' ? '生成完成（部分产物缺失）' : '运行中'}</b><div className="spacer" /><span className="muted">{job.pct}%</span></div>
          <div className="progress" style={{ marginTop: 10 }}><span style={{ width: `${job.pct}%` }} /></div>
          <ul className="stages">
            {job.stages.map((s, i) => {
              const running = i === job.stages.length - 1 && job.status === 'RUNNING'
              return <li key={i} className={running ? 'active' : 'done'}><span className="ico">{running ? '●' : '✓'}</span>{s.name}</li>
            })}
          </ul>
          {job.error && <div className="note" style={{ color: 'var(--bad)', marginTop: 10 }}>错误：{job.error}</div>}
          {(job.warnings?.length ?? 0) > 0 && (
            <div className="warn" style={{ marginTop: 10 }}>
              ⚠ 部分产物缺失（任务仍完成）：{(job.warnings || []).map((w, i) => <div key={i}>- {w}</div>)}
            </div>
          )}
          {(job.status === 'COMPLETED' || job.status === 'COMPLETED_WITH_WARNINGS') && <div className="row" style={{ marginTop: 14 }}><button className="btn primary" onClick={() => nav(`/results/${job.stock_code}/${job.year}`)}>查看结果 →</button></div>}
          {job.status === 'FAILED' && <div className="row" style={{ marginTop: 14 }}><button className="btn" onClick={() => { setJob(null); if (timer.current) window.clearInterval(timer.current) }}>返回重试</button></div>}
        </div>
      </div>
    )
  }

  return (
    <div className="container-narrow">
      <div className="topbar"><h2><span className="grad-text">生成分析</span></h2><span className="muted">输入企业代码与材料，启动一次税务筹划分析</span></div>

      <div className="card">
        <h3>① 企业</h3>
        <div className="grid" style={{ gridTemplateColumns: '2fr 1fr 1.4fr' }}>
          <div>
            <label className="field" htmlFor="gen-code">股票代码 / 企业名称
              <a style={{ marginLeft: 8, fontSize: 12.5 }} onClick={() => { setCode('600004'); setYear(2024); setName('白云机场'); setShowSug(false) }}>填入示例</a>
            </label>
            <div className="autocomplete">
              <input id="gen-code" type="text" value={code} style={{ width: '100%' }} aria-label="股票代码或企业名称"
                     onChange={(e) => { setCode(e.target.value); setShowSug(true) }} onFocus={() => setShowSug(true)}
                     onBlur={() => setTimeout(() => setShowSug(false), 180)} placeholder="如 600004 / 白云机场" />
              {showSug && sug.length > 0 && (
                <div className="suggest">
                  {sug.map((c) => (
                    <div key={c.stock_code} className="item" onMouseDown={() => { setCode(c.stock_code); setName(c.short_name || ''); setShowSug(false) }}>
                      <span className="code">{c.stock_code}</span>{c.short_name || '—'}<span className="muted">　{c.industry_name || ''}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
            {name && <div className="note" style={{ marginTop: 4 }}>{name}</div>}
          </div>
          <div>
            <label className="field">年度</label>
            <select aria-label="年度" value={year} onChange={(e) => setYear(Number(e.target.value))} style={{ width: '100%' }}>
              {[2024, 2023, 2022, 2021, 2020].map((y) => <option key={y} value={y}>{y}</option>)}
            </select>
          </div>
          <div>
            <label className="field">运行模式</label>
            <select aria-label="运行模式" value={useLlm ? 'llm' : 'rule'} onChange={(e) => setUseLlm(e.target.value === 'llm')} style={{ width: '100%' }}>
              <option value="llm">LLM（红蓝辩论，分钟级）</option>
              <option value="rule">规则（快速）</option>
            </select>
          </div>
        </div>
        <label style={{ display: 'flex', gap: 6, alignItems: 'center', marginTop: 10 }} className="note">
          <input type="checkbox" checked={force} onChange={(e) => setForce(e.target.checked)} />
          强制重跑（忽略缓存；默认若输入未变则复用已有结果）
        </label>
      </div>

      <div className="card">
        <h3>② 补充材料（可选）</h3>

        {/* 直接信息输入 */}
        <label className="field">直接信息输入（文本）</label>
        <textarea aria-label="补充材料文本" rows={3} value={text} onChange={(e) => setText(e.target.value)} style={{ width: '100%', fontFamily: 'inherit', fontSize: 14 }}
                  placeholder="例如：公司有一家境外子公司，主要从事东南亚销售；或：2024 年研发人员 320 人。" />
        <div className="grid" style={{ gridTemplateColumns: '1fr 1.6fr 1fr 1fr', marginTop: 10 }}>
          <div>
            <label className="field">重要性</label>
            <select aria-label="重要性" value={importance} onChange={(e) => setImportance(e.target.value)} style={{ width: '100%' }}>
              <option value="direct">直接输入（最可信）</option>
              <option value="reference">参考（需人工确认）</option>
            </select>
          </div>
          <div>
            <label className="field">事实类型（映射到画像字段）</label>
            <input list="facttypes" aria-label="事实类型" value={factType} onChange={(e) => setFactType(e.target.value)} style={{ width: '100%' }} placeholder="如 rd_person / subsidiary_overseas_count" />
            <datalist id="facttypes">
              {factTypes.map((f) => <option key={f.key} value={f.key}>{f.label} — {f.desc}</option>)}
            </datalist>
            {(() => { const sel = factTypes.find((f) => f.key === factType); return sel
              ? <div className="fieldmeta" style={{ marginTop: 4 }}>{sel.label}　{sel.desc}</div>
              : <div className="fieldmeta" style={{ marginTop: 4 }}>输入或选择字段名（自动显示含义）</div> })()}
          </div>
          <div>
            <label className="field">单位</label>
            <select aria-label="单位" value={unit} onChange={(e) => setUnit(e.target.value)} style={{ width: '100%' }}>
              <option value="">（无）</option>
              {['元', '万元', '亿元', '千元', '人', '次', '项', '%'].map((u) => <option key={u} value={u}>{u}</option>)}
            </select>
          </div>
          <div>
            <label className="field">口径</label>
            <select aria-label="口径" value={caliber} onChange={(e) => setCaliber(e.target.value)} style={{ width: '100%' }}>
              <option value="">（未指定）</option>
              {['合并', '母公司', '其他'].map((c) => <option key={c} value={c}>{c}</option>)}
            </select>
          </div>
        </div>
        <div className="row" style={{ marginTop: 10 }}>
          <button className="btn" onClick={doPreviewText} disabled={!text.trim()}>预览</button>
        </div>
        {textPreview && <PreviewPanel p={textPreview} onCommit={() => commit(textPreview, () => setTextPreview(null))} onClear={() => setTextPreview(null)} />}

        {/* 文件上传 */}
        <div style={{ marginTop: 18 }}>
          <label className="field">上传文件（CSV / Excel / PDF 数字版）</label>
          <div className="dropzone" onClick={() => fileRef.current?.click()}>
            点击选择文件（或拖拽）
            <input ref={fileRef} type="file" accept=".csv,.xlsx,.xls,.pdf" style={{ display: 'none' }}
                   onChange={(e) => { const f = e.target.files?.[0]; if (f) doUpload(f); e.target.value = '' }} />
          </div>
        </div>
        {filePreview && <PreviewPanel p={filePreview} onCommit={() => commit(filePreview, () => setFilePreview(null))} onClear={() => setFilePreview(null)} />}
        {committed > 0 && (
          <div className="row" style={{ marginTop: 10 }}>
            <button className="btn primary" onClick={start} disabled={!code.trim()}>重新分析（应用补充材料）</button>
          </div>
        )}
      </div>

      <div className="row" style={{ justifyContent: 'flex-end' }}>
        <button className="btn primary" onClick={start} disabled={!code.trim()}>开始生成</button>
      </div>
    </div>
  )
}
