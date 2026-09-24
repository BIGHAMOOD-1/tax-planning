import { useEffect, useMemo, useState } from 'react'
import { api, reportUrl, reportPdfUrl, type ResultView } from '../api'
import { money, pct, STATUS_LABEL } from '../format'
import { nav } from '../router'
import Chart from '../components/Chart'
import { Skeleton } from '../components/Skeleton'
import { friendlyError } from '../errors'
import { Term } from '../components/Term'
import { ErrorState } from '../components/ErrorState'
import { Num } from '../components/Num'
import { BackButton } from '../components/Nav'

const METRICS: [string, string, 'money' | 'pct' | 'raw'][] = [
  ['营业收入', 'is_revenue', 'money'],
  ['净利润', 'is_net_profit', 'money'],
  ['总资产', 'bs_total_assets', 'money'],
  ['研发投入', 'rd_spend_sum', 'money'],
  ['研发费用率', 'rd_expense_ratio', 'pct'],
  ['研发人员', 'rd_person', 'raw'],
  ['毛利率', 'gross_margin', 'pct'],
  ['净利率', 'net_margin', 'pct'],
  ['资产负债率', 'asset_liability_ratio', 'pct'],
  ['有息负债率', 'interest_debt_ratio', 'pct'],
  ['营收增长率', 'revenue_growth', 'pct'],
  ['政府补助', 'gov_subsidy_total', 'money'],
  ['所得税费用', 'is_income_tax', 'money'],
  ['支付的税费', 'cf_tax_paid', 'money'],
  ['职工薪酬', 'employee_compensation_base', 'money'],
  ['应收账款', 'bs_accounts_receivable', 'money'],
]

function fmt(v: unknown, kind: string) {
  if (v === null || v === undefined) return '—'
  if (kind === 'money') return money(v)
  if (kind === 'pct') return pct(v)
  return String(v)
}

export default function AnalysisResult({ code, year }: { code: string; year: number }) {
  const [v, setV] = useState<ResultView | null>(null)
  const [err, setErr] = useState('')
  const [open, setOpen] = useState<number | null>(null)
  const [metrics, setMetrics] = useState<Record<string, any> | null>(null)
  useEffect(() => { api.getResult(code, year).then(setV).catch((e) => setErr(friendlyError(e))) }, [code, year])
  useEffect(() => { api.metrics(code, year).then((m) => setMetrics(m as Record<string, any>)).catch(() => {}) }, [code, year])

  const charts = useMemo(() => {
    const t = v?.trend || {}
    const years = Object.keys(t).map(Number).sort()
    const series = (key: string, scale = 1) => years.map((y) => {
      const val = (t[String(y)] || {})[key]
      return val === null || val === undefined ? null : Number(val) * scale
    })
    const c1 = {
      color: ['#2f6bff', '#6d5efc', '#12b5a5'],
      tooltip: { trigger: 'axis' }, legend: { data: ['营业收入(亿)', '研发投入(亿)', '净利润(亿)'], bottom: 0 },
      grid: { left: 56, right: 24, top: 24, bottom: 56 },
      xAxis: { type: 'category', data: years, axisLabel: { hideOverlap: true } }, yAxis: { type: 'value' },
      series: [
        { name: '营业收入(亿)', type: 'line', smooth: true, data: series('is_revenue', 1e-8) },
        { name: '研发投入(亿)', type: 'line', smooth: true, data: series('rd_spend_sum', 1e-8) },
        { name: '净利润(亿)', type: 'line', smooth: true, data: series('is_net_profit', 1e-8) },
      ],
    }
    const c2 = {
      color: ['#2f6bff', '#2fae6a', '#c9a227'],
      tooltip: { trigger: 'axis' }, legend: { data: ['实际税率ETR(%)', '毛利率(%)', '资产负债率(%)'], bottom: 0 },
      grid: { left: 56, right: 24, top: 24, bottom: 56 },
      xAxis: { type: 'category', data: years, axisLabel: { hideOverlap: true } }, yAxis: { type: 'value' },
      series: [
        { name: '实际税率ETR(%)', type: 'line', smooth: true, data: series('etr', 100) },
        { name: '毛利率(%)', type: 'line', smooth: true, data: series('gross_margin', 100) },
        { name: '资产负债率(%)', type: 'line', smooth: true, data: series('asset_liability_ratio', 100) },
      ],
    }
    const dirs = (v?.plan_index || []).filter(
      (p) => p.confirmed_tax_impact != null || p.scenario_tax_impact != null)
    const c3 = {
      tooltip: { trigger: 'axis' }, legend: { data: ['确认影响(万元)', '情景测算(万元)'], bottom: 0 },
      grid: { left: 56, right: 24, top: 24, bottom: 56 },
      xAxis: { type: 'category', data: dirs.map((p) => p.direction), axisLabel: { hideOverlap: true } },
      yAxis: { type: 'value' },
      series: [
        { name: '确认影响(万元)', type: 'bar',
          data: dirs.map((p) => (p.confirmed_tax_impact != null ? p.confirmed_tax_impact / 1e4 : null)),
          itemStyle: { color: '#2fae6a' } },
        { name: '情景测算(万元)', type: 'bar',
          data: dirs.map((p) => (p.scenario_tax_impact != null ? p.scenario_tax_impact / 1e4 : null)),
          itemStyle: { color: '#c9a227' } },
      ],
    }
    return { years, c1, c2, c3, hasImpact: dirs.length > 0 }
  }, [v])
  if (err) return <div className="container"><ErrorState error={err} /></div>
  if (!v) return <div className="container"><Skeleton lines={5} /></div>

  const es = v.enterprise_summary as Record<string, unknown>
  const diags = v.diagnostics_summary.items || []
  const analyzed = new Set(v.plan_index.map((p) => p.direction))
  const ds = (v.direction_summary || {}) as Record<string, any>
  const skCount = v.plan_index.reduce((m: Record<string, number>, p) => {
    m[p.status_key] = (m[p.status_key] || 0) + 1; return m
  }, {})
  const topDir = [...v.plan_index].sort((a, b) =>
    (b.confirmed_tax_impact ?? b.scenario_tax_impact ?? -1) - (a.confirmed_tax_impact ?? a.scenario_tax_impact ?? -1))[0]

  return (
    <div className="container">
      <BackButton label="返回生成结果" />
      <div className="topbar">
        <h2>{String(es.short_name || v.analysis_meta.name)} · {year} 年度税务筹划分析</h2>
        <span className="muted">{v.analysis_meta.stock_code} · {String(es.industry_name || '')}</span>
        <div className="spacer" />
        <a className="btn" href={reportUrl(v.analysis_meta.stock_code, year)}>下载报告 (.md)</a>
        <a className="btn primary" href={reportPdfUrl(v.analysis_meta.stock_code, year)}>下载报告 (PDF)</a>
      </div>

      {/* 结论摘要（置顶） */}
      <div className="card summary-card">
        <h3>结论摘要</h3>
        {v.brief && <div className="summary-headline">{v.brief}</div>}
        <div className="state-chips">
          {(['CONFIRMED', 'SCENARIO', 'DATA_GAP', 'NOT_RECOMMEND'] as const).map((k) => skCount[k] ? (
            <span key={k} className={`badge ${k}`}>{STATUS_LABEL[k]} {skCount[k]}</span>
          ) : null)}
        </div>
        <div className="summary-grid">
          <div className="summary-stat"><div className="k"><Term k="confirm_impact" /></div><div className="v money-confirmed"><Num value={ds.confirmed_tax_impact_total} format={money} /></div></div>
          <div className="summary-stat"><div className="k"><Term k="scenario" /></div><div className="v money-scenario"><Num value={ds.scenario_tax_impact_total} format={money} /></div></div>
          <div className="summary-stat"><div className="k">分析方向</div><div className="v">{ds.analyzed ?? v.plan_index.length}</div></div>
          <div className="summary-stat"><div className="k">推荐 / 有条件</div><div className="v">{ds.recommend ?? 0} / {ds.conditional ?? 0}</div></div>
        </div>
        {topDir && <div className="note" style={{ marginTop: 12 }}>主要方向：{topDir.direction}（{STATUS_LABEL[topDir.status_key]}）</div>}
      </div>

      {/* ① 企业基本情况 */}
      <div className="card">
        <h3>企业基本情况</h3>
        <div className="grid" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(180px, 1fr))' }}>
          {METRICS.map(([label, k, kind]) => (
            <div key={k}>
              <div className="muted">{label}</div>
              <div style={{ fontSize: 17, fontWeight: 600 }}>{fmt(es[k], kind)}</div>
            </div>
          ))}
        </div>
        <div className="note" style={{ marginTop: 12 }}>
          数据完整度 {pct(v.completeness.data)}　|　证据：已确认 {v.evidence_summary.confirmed} / 代理 {v.evidence_summary.proxy} / 缺失 {v.evidence_summary.missing}
        </div>
      </div>

      {/* 多年趋势（ECharts） */}
      {charts.years.length > 0 && (
        <div className="card">
          <h3>多年趋势（{charts.years[0]}–{charts.years[charts.years.length - 1]}）</h3>
          <Chart option={charts.c1} label="多年趋势：营收/研发/净利润" />
          <div style={{ height: 16 }} />
          <Chart option={charts.c2} label="多年趋势：ETR/毛利率/资产负债率" />
        </div>
      )}

      {/* 方向影响对比（ECharts） */}
      {charts.hasImpact && (
        <div className="card">
          <h3>方向影响对比</h3>
          <div className="note" style={{ marginBottom: 8 }}>
            确认影响 = 可验证的增量税收收益；情景测算为非结论性估算。「二者不可相加」。
          </div>
          <Chart option={charts.c3} label="方向影响对比：确认影响 vs 情景测算" />
        </div>
      )}

      {/* ② 系统观察 */}
      <div className="card">
        <h3><Term k="diagnostics">系统观察</Term></h3>
        <div className="note" style={{ marginBottom: 12 }}>以下为事实性观察，仅指出值得关注之处，不代表企业存在问题。</div>
        {diags.length === 0 && <div className="muted">本次分析未发现显著信号。</div>}
        {diags.map((d, i) => (
          <div key={i} style={{ borderTop: i ? '1px solid var(--border)' : 'none', padding: '10px 0' }}>
            <div className="row">
              <span className={`sev-${d.severity}`}><span className={`sev-dot dot-${d.severity}`} />{d.severity}</span>
              <b>{d.name}</b>
              <div className="spacer" />
              {analyzed.has(d.direction)
                ? <a onClick={() => nav(`/results/${code}/${year}/${encodeURIComponent(d.direction)}`)}>查看依据 →</a>
                : <a onClick={() => setOpen(open === i ? null : i)}>查看依据 {open === i ? '▲' : '▼'}</a>}
            </div>
            <div className="note" style={{ marginTop: 4 }}>实际 {money(d.actual)} / 预期 {money(d.expected)}　·　关联方向：{d.direction}</div>
            {open === i && (
              <div className="note" style={{ marginTop: 6 }}>
                {d.statement}
                {(d.evidence_needed?.length ?? 0) > 0 && <div>需补充材料：{d.evidence_needed!.join('、')}</div>}
              </div>
            )}
          </div>
        ))}
      </div>

      {/* 运行指标 */}
      {metrics && (
        <div className="card">
          <h3>运行指标</h3>
          <div className="kv">
            <div className="muted">总耗时</div><div>{metrics.total_seconds != null ? `${metrics.total_seconds} 秒` : '—'}</div>
            <div className="muted">模式</div><div>{metrics.use_llm ? 'LLM' : '规则'}</div>
            <div className="muted">LLM 调用</div><div>{metrics.llm?.calls ?? 0} 次（缓存命中 {metrics.llm?.cached_calls ?? 0}）</div>
            <div className="muted">Token 合计</div><div>{(metrics.llm?.total_tokens ?? 0).toLocaleString()}（提示 {metrics.llm?.prompt_tokens ?? 0} / 生成 {metrics.llm?.completion_tokens ?? 0}）</div>
          </div>
          {Array.isArray(metrics.stages) && metrics.stages.length > 0 && (
            <div className="note" style={{ marginTop: 8 }}>
              阶段：{metrics.stages.map((s: any) => `${s.stage}(${s.seconds}s)`).join(' → ')}
            </div>
          )}
        </div>
      )}

      {/* ③ 筹划方向 */}
      <div className="card">
        <h3>筹划方向</h3>
        <table>
          <thead><tr><th>方向</th><th><Term k="status_key">结论</Term></th><th className="num"><Term k="confirm_impact" /></th><th className="num"><Term k="scenario" /></th><th></th></tr></thead>
          <tbody>
            {v.plan_index.map((p) => (
              <tr key={p.direction}>
                <td><b>{p.direction}</b></td>
                <td><span className={`badge ${p.status_key}`}>{STATUS_LABEL[p.status_key]}</span></td>
                <td className="num money-confirmed">{money(p.confirmed_tax_impact)}</td>
                <td className="num money-scenario">{money(p.scenario_tax_impact)}</td>
                <td><a onClick={() => nav(`/results/${code}/${year}/${encodeURIComponent(p.direction)}`)}>查看完整分析 →</a></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
