import { useEffect, useRef, useState } from 'react'
import { api, type JobStatus } from '../api'
import { Modal } from '../components/Modal'
import { friendlyError } from '../errors'
import { useToast } from '../components/Toast'
import { Empty } from '../components/Empty'

export default function Knowledge() {
  const [q, setQ] = useState('研发费用加计扣除')
  const [corpus, setCorpus] = useState('policy')
  const [hits, setHits] = useState<Record<string, unknown>[]>([])
  const [loading, setLoading] = useState(false)
  const [idx, setIdx] = useState<Record<string, any> | null>(null)
  const [job, setJob] = useState<JobStatus | null>(null)
  const [confirmOpen, setConfirmOpen] = useState(false)
  const timer = useRef<number | null>(null)
  const toast = useToast()

  const loadIndex = () => api.knowledgeIndex().then((r) => setIdx(r as Record<string, any>)).catch(() => {})
  useEffect(() => { loadIndex() }, [])
  useEffect(() => () => { if (timer.current) window.clearInterval(timer.current) }, [])

  const run = async () => {
    setLoading(true)
    try { const r = await api.kbSearch(q, corpus, 8); setHits(r.hits) }
    catch (e) { toast('error', friendlyError(e)) }
    finally { setLoading(false) }
  }

  const doRebuild = async () => {
    setConfirmOpen(false)
    setJob(null)
    try {
      const j = await api.rebuildKnowledge('auto')
      setJob(j)
      timer.current = window.setInterval(async () => {
        try {
          const cur = await api.getJob(j.job_id)
          setJob(cur)
          if (cur.status === 'COMPLETED' || cur.status === 'FAILED') {
            if (timer.current) window.clearInterval(timer.current)
            loadIndex()
            toast(cur.status === 'COMPLETED' ? 'success' : 'error',
                  cur.status === 'COMPLETED' ? '索引重建完成' : `索引重建失败：${cur.error || ''}`)
          }
        } catch (e) { toast('error', friendlyError(e)) }
      }, 2000)
    } catch (e) { toast('error', friendlyError(e)) }
  }

  return (
    <div>
      <div className="topbar"><h2>知识库</h2><span className="muted">政策 / 案例检索 · 索引重建</span></div>

      {/* 索引概况 + 重建 */}
      <div className="card">
        <div className="row">
          <h3 style={{ margin: 0 }}>索引</h3>
          <div className="spacer" />
          <button className="btn" onClick={() => setConfirmOpen(true)} disabled={job?.status === 'RUNNING'}>重建索引</button>
        </div>
        {idx && (
          <div className="note" style={{ marginTop: 8 }}>
            政策：{String(idx.policy?.backend ?? '—')} · {String(idx.policy?.chunks ?? '—')} chunk　|　
            案例：{String(idx.case?.backend ?? '—')} · {String(idx.case?.chunks ?? '—')} chunk
            {idx.embedding && <>　|　向量：{String(idx.embedding.embedding_model)}（{String(idx.embedding.dim)} 维）</>}
            {idx.policy?.mismatch && <span style={{ color: 'var(--bad)' }}>　⚠ 指纹不一致：{String(idx.policy.mismatch)}</span>}
          </div>
        )}
        <div className="note" style={{ marginTop: 6 }}>
          ⏳ 重建会删除旧索引并重新向量化全部政策/案例，「耗时较长」，请确认后再执行。
        </div>
        {job && (
          <div style={{ marginTop: 10 }}>
            <div className="row"><span className="muted">{job.status === 'RUNNING' ? '重建中…' : job.status}</span>
              <div className="spacer" /><span className="muted">{job.pct}%</span></div>
            <div className="progress" style={{ marginTop: 6 }}><span style={{ width: `${job.pct}%` }} /></div>
            {job.stages?.length > 0 && <div className="note" style={{ marginTop: 6 }}>{job.stages[job.stages.length - 1]?.name}</div>}
            {job.error && <div className="note" style={{ color: 'var(--bad)' }}>{job.error}</div>}
          </div>
        )}
      </div>

      <div className="card">
        <div className="searchbar">
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="检索政策 / 案例" aria-label="检索关键词" />
          <select value={corpus} onChange={(e) => setCorpus(e.target.value)}>
            <option value="policy">政策</option>
            <option value="case">案例</option>
          </select>
          <button className="btn primary" onClick={run} disabled={loading}>{loading ? '检索中…' : '检索'}</button>
        </div>
      </div>

      <div className="card">
        {hits.length === 0 && <Empty title="无检索结果" desc="换个关键词，或先「重建索引」。" />}
        {hits.map((h, i) => (
          <div key={i} style={{ marginBottom: 10 }}>
            <div>
              {h.url
                ? <a href={String(h.url)} target="_blank" rel="noreferrer"><b>{String(h.doc_no || '')}</b> {String(h.title || '')}</a>
                : <><b>{String(h.doc_no || '')}</b> {String(h.title || '')}</>}
              <span className="muted">（{String(h.channel || '')} {String(h.date || '')}）</span>
            </div>
            <div className="note">{String(h.excerpt || '').slice(0, 200)}</div>
          </div>
        ))}
      </div>

      {confirmOpen && (
        <Modal title="重建知识库索引" danger confirmText="确认重建" onClose={() => setConfirmOpen(false)} onConfirm={doRebuild}>
          <div>将执行以下操作：</div>
          <ul style={{ margin: '8px 0 0 18px' }}>
            <li>删除现有索引（政策 / 案例）</li>
            <li>重新向量化全部 chunk（政策 {String(idx?.policy?.chunks ?? '—')} + 案例 {String(idx?.case?.chunks ?? '—')}）并重建 BM25</li>
          </ul>
          <div style={{ marginTop: 10, color: 'var(--bad)' }}>
            ⚠ 耗时较长（约数分钟～数十分钟），且消耗 Embedding 配额；期间检索可能不可用/回退。
          </div>
          <div className="note" style={{ marginTop: 6 }}>更换 Embedding 模型后必须重建。确定继续？</div>
        </Modal>
      )}
    </div>
  )
}
