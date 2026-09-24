import { useEffect, useState } from 'react'
import { api, opinionExportUrl, opinionExportPdfUrl, planExportUrl, planExportPdfUrl, type Opinion, type PlanDetail } from '../api'
import { money, RESOLUTION_LABEL } from '../format'
import { fieldLabel } from '../labels'
import { BackButton } from '../components/Nav'
import { Skeleton } from '../components/Skeleton'
import { Term } from '../components/Term'
import { PoolRefs } from '../components/PoolRefs'
import { ErrorState } from '../components/ErrorState'
import { friendlyError } from '../errors'
import { useToast } from '../components/Toast'

function statusIcon(r: string) { return r === 'DIRECT' ? '✓' : r === 'PROXY' ? '△' : '✕' }

const POLICY_VALIDITY_LABEL: Record<string, string> = {
  VALID: '有效', EXPIRED: '已失效', NOT_YET_EFFECTIVE: '尚未生效', UNKNOWN: '未知（需人工核验）',
}

function opBlock(title: string, items?: string[], tone?: string) {
  if (!items || items.length === 0) return null
  return (
    <div className="op-block">
      <div className={`op-title${tone === 'danger' ? ' danger' : ''}`}>{title}</div>
      {items.map((x, i) => <div key={i} className="fieldmeta">- {x}</div>)}
    </div>
  )
}

function opSteps(steps?: { step: string; detail?: string }[], refMap?: Record<string, string>) {
  if (!steps || steps.length === 0) return null
  return (
    <div className="op-block">
      <div className="op-title">实施路径</div>
      {steps.map((s, i) => (
        <div key={i} className="fieldmeta">
          <b>{i + 1}. {s.step}</b>{s.detail ? <>：<PoolRefs text={s.detail} map={refMap || {}} /></> : null}
        </div>
      ))}
    </div>
  )
}

export default function DirectionAnalysis({ code, year, direction }: { code: string; year: number; direction: string }) {
  const [p, setP] = useState<PlanDetail | null>(null)
  const [err, setErr] = useState('')
  const [showAi, setShowAi] = useState(false)
  const [showReview, setShowReview] = useState(false)
  const [opinion, setOpinion] = useState<Opinion | null>(null)
  const [opLoading, setOpLoading] = useState(false)
  const [expandPoints, setExpandPoints] = useState<Record<number, boolean>>({})
  const toast = useToast()
  useEffect(() => { api.getPlan(code, year, direction).then(setP).catch((e) => setErr(friendlyError(e))) }, [code, year, direction])
  useEffect(() => { setOpinion(p?.opinion ?? null) }, [p])

  const genOpinion = async (refresh: boolean) => {
    setOpLoading(true)
    try {
      setOpinion(await api.getOpinion(code, year, direction, refresh))
      toast('success', '合规税务意见已生成')
    }
    catch (e) { toast('error', friendlyError(e)) }
    finally { setOpLoading(false) }
  }

  if (err) return <div className="container"><ErrorState error={err} /></div>
  if (!p) return <div className="container"><Skeleton lines={5} /></div>

  const lin = p.lineage
  const calc = lin.calculation || {}
  const facts = lin.required_facts || []
  const debate = lin.debate || []
  const dp = (lin.data_pool || {}) as Record<string, any>
  const green = ((lin.ai_process || {}) as Record<string, any>).green || {}
  const aiNote: string[] = green.ai_note || []
  const pv = (dp.policy_validity || {}) as Record<string, any>
  const pvMap: Record<string, string> = {}
  for (const k of ['valid', 'expired', 'not_yet_effective', 'unknown']) {
    for (const it of (pv[k] || [])) pvMap[it.doc_no || it.title] = k
  }
  const pvTag: Record<string, string> = { valid: '有效', expired: '已失效', not_yet_effective: '尚未生效', unknown: '时效未知' }
  const pvOverall: string = p.policy_validity?.overall || (dp.policy_validity || {}).overall || 'UNKNOWN'
  const conds = (dp.conditions || {}) as Record<string, any[]>
  const diags = (dp.diagnostics || []) as any[]

  // 池编号 → 名称/值（用于 [P1]/[F2]/[C3]/[R4] 悬停可读化）
  const refMap: Record<string, string> = {}
  for (const f of (dp.company_facts || []) as any[]) {
    refMap[f.id] = `${f.label || f.field} = ${typeof f.value === 'number' ? money(f.value) : String(f.value ?? '—')}`
  }
  for (const p of (dp.policies || []) as any[]) refMap[p.id] = `${p.doc_no || ''} ${p.title || ''}`.trim()
  for (const c of (dp.cases || []) as any[]) refMap[c.id] = c.title || ''
  for (const r of (dp.related_fields || []) as any[]) {
    refMap[r.id] = `${fieldLabel(r.field)} = ${typeof r.value === 'number' ? money(r.value) : String(r.value ?? '—')}`
  }

  return (
    <div className="container">
      <BackButton label="返回分析结果" />
      <div className="topbar">
        <h2>{direction} · 分析结果</h2>
        <span className="muted">{code} · {year} · {p.skill?.name || '（无 Skill）'}</span>
        <div className="spacer" />
        <a className="btn" href={planExportUrl(code, year, direction)}>导出证据链 (.md)</a>
        <a className="btn" href={planExportPdfUrl(code, year, direction)}>证据链 (PDF)</a>
      </div>

      {/* 结论条（置顶常驻） */}
      <div className="dir-sticky">
        <b>{p.final_decision}</b>
        <span className="muted">确认 {money(p.estimated_tax_impact)} · 情景 {money(p.scenario_tax_impact)}</span>
        {calc.direction && calc.direction !== 'none' && (p.estimated_tax_impact != null || p.scenario_tax_impact != null) && (
          <span className={`dir-badge ${calc.direction === 'tax_increase' ? 'increase' : 'reduce'}`}>
            {calc.direction === 'tax_increase' ? '税负增加' : '税收收益'}
          </span>
        )}
      </div>

      {/* ① 结论（Green，置顶） */}
      <div className="card">
        <h3>{direction} · 结论</h3>
        <div className="row">
          <div style={{ fontSize: 20, fontWeight: 600 }}>{p.final_decision}</div>
          <div className="spacer" />
          <div style={{ textAlign: 'right' }}>
            <div className="muted"><Term k="confirm_impact" /></div>
            <div className="money-confirmed" style={{ fontSize: 18 }}>{money(p.estimated_tax_impact)}</div>
            <div className="muted" style={{ marginTop: 6 }}><Term k="scenario" /></div>
            <div className="money-scenario" style={{ fontSize: 18 }}>{money(p.scenario_tax_impact)}</div>
            {calc.direction && calc.direction !== 'none' && (p.estimated_tax_impact != null || p.scenario_tax_impact != null) && (
              <div style={{ marginTop: 4 }}>
                <span className={`dir-badge ${calc.direction === 'tax_increase' ? 'increase' : 'reduce'}`}>
                  {calc.direction === 'tax_increase' ? '税负增加' : '税收收益'}
                </span>
              </div>
            )}
            {(calc.calculation_type || pvOverall) && (
              <div className="note" style={{ marginTop: 6 }}>
                {calc.calculation_type && <><Term k="calculation_type" />：{calc.calculation_type}</>}
                {calc.calculation_type && pvOverall && <>　</>}
                {pvOverall && <><Term k="policy_validity" />：{POLICY_VALIDITY_LABEL[pvOverall] || pvOverall}</>}
              </div>
            )}
            {calc.direction === 'tax_reduce' && calc.impact_basis && (
              <div className="note" style={{ marginTop: 4 }}>
                口径：{calc.impact_basis}
                {calc.baseline ? `（基准：${calc.baseline}${calc.action ? `；动作：${calc.action}` : ''}）` : ''}
              </div>
            )}
            {calc.caveat && <div className="note" style={{ marginTop: 2, color: 'var(--proxy)' }}>提示：{calc.caveat}</div>}
            {calc.risk_note && <div className="note" style={{ marginTop: 2, color: 'var(--bad)' }}>风险：{calc.risk_note}</div>}
          </div>
        </div>
        <div style={{ marginTop: 14 }}>
          <div className="muted" style={{ marginBottom: 6 }}>为什么</div>
          {(green.reasons || []).filter((r: string) => !r.startsWith('需补充：')).map((r: string, i: number) => <div key={i} className="note">- {r}</div>)}
        </div>
        {((lin.evidence_required?.length ?? 0) > 0 || (p.plan?.measures_ai?.length ?? 0) > 0) && (
          <div style={{ marginTop: 14 }}>
            <div className="muted" style={{ marginBottom: 6 }}>待补证据与建议措施</div>
            {(lin.evidence_required || []).map((r: string, i: number) => <div key={`e${i}`} className="note">□ 待补：{r}</div>)}
            {(p.plan?.measures_ai || []).map((m, i) => <div key={`ai${i}`} className="note">✦ <PoolRefs text={m} map={refMap} />（AI 建议）</div>)}
          </div>
        )}
        {aiNote.length > 0 && (
          <div style={{ marginTop: 14 }}>
            <a onClick={() => setShowAi(!showAi)}>AI 分析说明 {showAi ? '▲' : '▼'}</a>
            {showAi && <div style={{ marginTop: 8 }}>{aiNote.map((r, i) => <div key={i} className="note">- {r}</div>)}</div>}
          </div>
        )}
      </div>

      {/* 合规税务意见（Advisor · 面向行动）—— 独立模块，最醒目 */}
      <div className="card opinion-card">
        <div className="row">
          <h3 style={{ margin: 0 }}>合规税务意见</h3>
          <div className="spacer" />
          {opinion && <a className="btn" href={opinionExportUrl(code, year, direction)}>下载意见书 (.md)</a>}
          {opinion && <a className="btn" href={opinionExportPdfUrl(code, year, direction)}>意见书 (PDF)</a>}
          <button className="btn primary" onClick={() => genOpinion(true)} disabled={opLoading}>
            {opLoading ? '生成中…' : (opinion ? '重新生成' : '生成意见')}
          </button>
        </div>
        {!opinion && !opLoading && (
          <div className="note" style={{ marginTop: 10 }}>
            面向行动的意见：「若要真正落地该方向，应当怎么做」（实施路径 / 需备资料 / 合规红线）。
            基于同一公用数据池、红蓝辩论与政策检索生成。
          </div>
        )}
        {opinion && (
          <div style={{ marginTop: 12 }}>
            {opinion.summary && <div className="opinion-summary">{opinion.summary}</div>}
            {opinion.approach && (
              <div className="opinion-approach">
                <span className="op-title" style={{ marginRight: 6 }}>关键第一步</span>{opinion.approach}
              </div>
            )}
            {opBlock('适用条件与前提', opinion.applicability)}
            {opSteps(opinion.steps, refMap)}
            {opBlock('需准备的资料/证据', opinion.materials)}
            {opBlock('会计与税务处理要点', opinion.accounting_tax)}
            {opBlock('合规红线与风险', opinion.red_lines, 'danger')}
            {opBlock('不适用/需谨慎情形', opinion.not_applicable)}
            {opinion.refs && opinion.refs.length > 0 && (
              <div className="op-block">
                <div className="op-title">参考依据</div>
                {opinion.refs.map((r, i) => (
                  <div key={i} className="fieldmeta">
                    [{r.id}] {r.url
                      ? <a href={r.url} target="_blank" rel="noreferrer">{r.doc_no || '（无文号）'} {r.title}</a>
                      : <>{r.doc_no || '（无文号）'} {r.title}</>}
                  </div>
                ))}
              </div>
            )}
            {opinion.disclaimer && <div className="note" style={{ marginTop: 10, fontStyle: 'italic' }}>{opinion.disclaimer}</div>}
          </div>
        )}
      </div>

      {/* 证据链 · 7 节点（P0-C） */}
      <div className="card">
        <h3>证据链</h3>
        <div className="note" style={{ marginBottom: 10 }}>
          企业事实 → 诊断信号 → 税务方向 → 政策依据 → 条件核验 → 税额计算 → 最终结论
        </div>
        <div className="echain">
          <div className="enode" data-idx="1">
            <div className="enode-head"><b>企业事实</b><span className="enode-sub">{(dp.company_facts || []).length} 项</span></div>
            <div className="enode-body">
              {(dp.company_facts || []).slice(0, 6).map((f: any) => (
                <div key={f.id} className="fieldmeta">[{f.id}] {f.label || fieldLabel(f.field)} = {typeof f.value === 'number' ? money(f.value) : String(f.value ?? '—')}</div>
              ))}
              {(dp.company_facts || []).length > 6 && <div className="fieldmeta">… 其余 {(dp.company_facts || []).length - 6} 项见下方明细</div>}
            </div>
          </div>
          <div className="enode" data-idx="2">
            <div className="enode-head"><b><Term k="diagnostics">诊断信号</Term></b><span className="enode-sub">{diags.length} 项</span></div>
            <div className="enode-body">
              {diags.length === 0 && <span className="muted">该方向无命中诊断</span>}
              {diags.map((d: any, i: number) => (
                <div key={i} className="fieldmeta">[{d.severity}] {d.name}{d.note ? `：${d.note}` : ''}</div>
              ))}
            </div>
          </div>
          <div className="enode" data-idx="3">
            <div className="enode-head"><b>税务方向</b></div>
            <div className="enode-body">
              <div className="fieldlabel">{direction}</div>
              <div className="fieldmeta">Skill：{p.skill?.name || '（无）'}{p.skill?.id ? `（${p.skill.id}）` : ''}</div>
            </div>
          </div>
          <div className="enode" data-idx="4">
            <div className="enode-head">
              <b>政策依据</b>
              <span className="enode-sub">{(dp.policies || []).length} 条　时效：{POLICY_VALIDITY_LABEL[pvOverall] || '未知'}</span>
            </div>
            <div className="enode-body">
              {(dp.policies || []).map((pol: any) => (
                <div key={pol.id} className="fieldmeta">
                  [{pol.id}]{" "}
                  {pol.url
                    ? <a href={String(pol.url)} target="_blank" rel="noreferrer">{pol.doc_no || '（无文号）'} {pol.title}</a>
                    : <>{pol.doc_no || '（无文号）'} {pol.title}</>}
                  （{pol.date}）
                  {pvMap[pol.doc_no || pol.title] && ` [${pvTag[pvMap[pol.doc_no || pol.title]]}]`}
                  {pol.meta && <>　元数据：{pol.meta.source}/{pol.meta.metadata_confidence}/{pol.meta.verification}
                    {pol.meta.level ? `　层级：${pol.meta.level}` : ''}
                    {(pol.meta.subject && pol.meta.subject.length > 0) ? `　主体：${pol.meta.subject.join('、')}` : ''}
                  </>}
                </div>
              ))}
              {(dp.policies || []).length === 0 && <span className="muted">未检索到政策依据</span>}
            </div>
          </div>
          <div className="enode" data-idx="5">
            <div className="enode-head">
              <b>条件核验</b>
              <span className="enode-sub">通过 {(conds.passed || []).length} · 不满足 {(conds.failed || []).length} · 未知 {(conds.unknown || []).length}</span>
            </div>
            <div className="enode-body">
              {(conds.failed || []).map((c: any, i: number) => <div key={`f${i}`} className="fieldmeta">✕ {c.desc || fieldLabel(c.field)}</div>)}
              {(conds.unknown || []).map((c: any, i: number) => <div key={`u${i}`} className="fieldmeta">? {c.desc || fieldLabel(c.field)}</div>)}
              {(conds.passed || []).map((c: any, i: number) => <div key={`p${i}`} className="fieldmeta">✓ {c.desc || fieldLabel(c.field)}</div>)}
            </div>
          </div>
          <div className="enode" data-idx="6">
            <div className="enode-head"><b>税额计算</b><span className="enode-sub">{calc.calculator || '（无计算器）'}</span></div>
            <div className="enode-body">
              <div className="fieldmeta">金额语义：{calc.calculation_type || '—'}　影响类型：{calc.impact_type || '—'}</div>
              {calc.results?.confirmed_tax_impact != null && <div className="fieldlabel">确认影响：{money(calc.results.confirmed_tax_impact as number)}</div>}
              {calc.results?.scenario_tax_impact != null && <div className="fieldmeta">情景测算：{money(calc.results.scenario_tax_impact as number)}</div>}
              {calc.not_calculable_inputs && calc.not_calculable_inputs.length > 0 && <div className="fieldmeta">缺失输入：{calc.not_calculable_inputs.map((f: string) => fieldLabel(f)).join('、')}</div>}
            </div>
          </div>
          <div className="enode" data-idx="7">
            <div className="enode-head"><b>最终结论</b><span className="enode-sub">{p.final_decision}</span></div>
            <div className="enode-body">
              {(green.reasons || []).map((r: string, i: number) => <div key={i} className="fieldmeta">- {r}</div>)}
            </div>
          </div>
        </div>

        <div className="ebranch">
          <div className="ebranch-title">旁支 · 审查说明（机会分析 / 合规审查）</div>
          <a className="echain-toggle" onClick={() => setShowReview(!showReview)}>展开审查过程 {showReview ? '▲' : '▼'}</a>
          {showReview && (
            <div style={{ marginTop: 8 }}>
              {debate.map((h: any, i: number) => (
                <div key={i} style={{ marginBottom: 8 }}>
                  <div className="fieldmeta">第 {h.round} 轮</div>
                  <div className="fieldmeta">机会分析：{h.red?.position || '—'}</div>
                  <div className="fieldmeta">合规审查：{h.blue?.position || '—'}</div>
                </div>
              ))}
            </div>
          )}
        </div>
        <div className="ebranch">
          <div className="ebranch-title">旁支 · Evidence / 来源</div>
          <div className="fieldmeta">{facts.length} 项必需事实（{facts.filter((f) => f.present).length} 项已有）　待补证据 {(lin.evidence_required || []).length} 项</div>
        </div>
      </div>

      {/* ② Evidence */}
      <div className="card">
        <h3>Evidence · Required Facts</h3>
        <table>
          <thead><tr><th>事实</th><th>状态</th><th><Term k="resolution">Resolution</Term></th><th>来源</th></tr></thead>
          <tbody>
            {facts.map((f) => (
              <tr key={f.fact}>
                <td>
                  <div className="fieldlabel">{f.label || fieldLabel(f.field)}</div>
                  {f.formula
                    ? <div className="fieldmeta">计算：{f.formula}</div>
                    : <div className="fieldmeta">字段：{fieldLabel(f.field)}</div>}
                </td>
                <td>{f.present ? '✓ 已有' : '✕ 缺失'}</td>
                <td>{statusIcon(f.resolution)} {RESOLUTION_LABEL[f.resolution] || f.resolution}{f.proxy_of ? `（代理：${f.proxy_of}）` : ''}</td>
                <td className="muted">{f.source || '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* ③ Calculator */}
      {calc.layers && calc.layers.length > 0 && (
        <div className="card">
          <h3>Calculator · 计算分层</h3>
          <div className="note" style={{ marginBottom: 10 }}>
            <Term k="impact_type" />：{calc.impact_type || '—'}
            {calc.calculation_type && <>　<Term k="calculation_type" />：{calc.calculation_type}</>}
            {calc.proxy_inputs && calc.proxy_inputs.length > 0 && <>　代理输入：{calc.proxy_inputs.map((x: any) => x.label || fieldLabel(x.field)).join('、')}</>}
            {calc.not_calculable_inputs && calc.not_calculable_inputs.length > 0 && <>　缺失：{calc.not_calculable_inputs.map((f: string) => fieldLabel(f)).join('、')}</>}
          </div>
          <table>
            <thead><tr><th>层</th><th className="num">值</th><th>说明</th></tr></thead>
            <tbody>
              {calc.layers.map((l, i) => (
                <tr key={i}><td>{l.layer}</td><td className="num">{typeof l.value === 'number' ? money(l.value) : String(l.value ?? '—')}</td><td className="muted">{l.note || (l.source ? fieldLabel(l.source) : '')}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* ④ Red / Blue */}
      <div className="card">
        <h3>审查过程 · 机会分析 → 合规审查</h3>
        {debate.map((h: any, i: number) => (
          <div key={i} style={{ marginTop: i ? 14 : 0 }}>
            <div className="note">第 {h.round} 轮</div>
            <div className="card" style={{ borderLeft: '3px solid var(--bad)', marginBottom: 10 }}>
              <b>机会分析</b>
              <div>{h.red?.position || '—'}</div>
              {(h.red?.supporting_points?.length ?? 0) > 0 && (
                <>
                  <a className="echain-toggle" style={{ marginTop: 4, display: 'inline-block' }}
                     onClick={() => setExpandPoints((s) => ({ ...s, [i]: !s[i] }))}>
                    {h.red.supporting_points.length} 条依据 {expandPoints[i] ? '▲' : '▼'}
                  </a>
                  {expandPoints[i] && h.red.supporting_points.map((p: string, k: number) => (
                    <div key={k} className="fieldmeta">- {p}</div>
                  ))}
                </>
              )}
              {expandPoints[i] && (h.red?.alternatives?.length ?? 0) > 0 && (
                <div style={{ marginTop: 6 }}>
                  <div className="op-title">替代方案</div>
                  {h.red.alternatives.map((x: string, k: number) => <div key={k} className="fieldmeta">- {x}</div>)}
                </div>
              )}
              {expandPoints[i] && (h.red?.measures?.length ?? 0) > 0 && (
                <div style={{ marginTop: 6 }}>
                  <div className="op-title">建议措施</div>
                  {h.red.measures.map((x: string, k: number) => <div key={k} className="fieldmeta">- {x}</div>)}
                </div>
              )}
            </div>
            <div className="card" style={{ borderLeft: '3px solid var(--accent)', marginBottom: 10 }}>
              <b>合规审查</b>
              <div>{h.blue?.position || '—'}</div>
              {(h.blue?.issues?.length ?? 0) > 0 && (
                <>
                  <a className="echain-toggle" style={{ marginTop: 4, display: 'inline-block' }}
                     onClick={() => setExpandPoints((s) => ({ ...s, [1000 + i]: !s[1000 + i] }))}>
                    {h.blue.issues.length} 条质疑 {expandPoints[1000 + i] ? '▲' : '▼'}
                  </a>
                  {(expandPoints[1000 + i] ? h.blue.issues : h.blue.issues.slice(0, 3)).map((x: string, k: number) => (
                    <div key={k} className="fieldmeta">⚠ {x}</div>
                  ))}
                </>
              )}
              {(h.blue?.evidence_found || []).slice(0, 3).map((e: any, k: number) => (
                <div key={k} className="fieldmeta">🔎 {e.title}（{e.channel} {e.date}）</div>
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
