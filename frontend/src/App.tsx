import { useEffect, type ReactElement } from 'react'
import { useRoute, segments, nav } from './router'
import { loadFieldLabels } from './labels'
import Generate from './pages/Generate'
import Results from './pages/Results'
import AnalysisResult from './pages/AnalysisResult'
import DirectionAnalysis from './pages/DirectionAnalysis'
import ReviewHome from './pages/ReviewHome'
import ReviewSkills from './pages/ReviewSkills'
import ReviewSkillDetail from './pages/ReviewSkillDetail'
import ReviewEngine from './pages/ReviewEngine'
import ReviewCases from './pages/ReviewCases'
import ReviewConflicts from './pages/ReviewConflicts'
import DataCenter from './pages/DataCenter'
import Knowledge from './pages/Knowledge'
import Settings from './pages/Settings'
import Help from './pages/Help'

const ICONS: Record<string, ReactElement> = {
  gen: <svg className="nav-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M12 5v14M5 12h14" /></svg>,
  result: <svg className="nav-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M4 6h16M4 12h16M4 18h10" /></svg>,
  settings: <svg className="nav-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="3" /><path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M19.1 4.9L17 7M7 17l-2.1 2.1" /></svg>,
  help: <svg className="nav-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="9" /><path d="M9.5 9a2.5 2.5 0 1 1 3.4 2.3c-.7.3-1 .9-1 1.7v.4" /><path d="M12 17h.01" /></svg>,
  review: <svg className="nav-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M12 3l7 3v5c0 4.5-3 8-7 10-4-2-7-5.5-7-10V6z" /><path d="M9 12l2 2 4-4" /></svg>,
  data: <svg className="nav-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><ellipse cx="12" cy="6" rx="7" ry="3" /><path d="M5 6v12c0 1.7 3.1 3 7 3s7-1.3 7-3V6" /><path d="M5 12c0 1.7 3.1 3 7 3s7-1.3 7-3" /></svg>,
  kb: <svg className="nav-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M4 5a2 2 0 0 1 2-2h12v18H6a2 2 0 0 1-2-2z" /><path d="M8 3v18" /></svg>,
}

const SECTION: Record<string, string> = {
  generate: '生成', results: '生成结果', review: '审查', data: '数据中心',
  kb: '知识库', settings: '设置', help: '帮助',
}

export default function App() {
  const route = useRoute()
  const seg = segments(route)
  const KNOWN = ['generate', 'results', 'review', 'data', 'kb', 'settings', 'help']
  const top = KNOWN.includes(seg[0]) ? seg[0] : 'generate'
  useEffect(() => { loadFieldLabels() }, [])

  let content: ReactElement
  if (top === 'data') content = <DataCenter />
  else if (top === 'kb') content = <Knowledge />
  else if (top === 'settings') content = <Settings />
  else if (top === 'help') content = <Help />
  else if (top === 'review') {
    const sub = seg[1]
    if (sub === 'skills' && seg[2]) content = <ReviewSkillDetail skillId={seg[2]} />
    else if (sub === 'skills') content = <ReviewSkills />
    else if (sub === 'engine') content = <ReviewEngine />
    else if (sub === 'cases') content = <ReviewCases />
    else if (sub === 'conflicts') content = <ReviewConflicts />
    else content = <ReviewHome />
  } else if (top === 'results') {
    const code = seg[1]
    const year = seg[2] ? Number(seg[2]) : undefined
    const dir = seg[3] ? decodeURIComponent(seg[3]) : ''
    if (code && year && dir) content = <DirectionAnalysis code={code} year={year} direction={dir} />
    else if (code && year) content = <AnalysisResult code={code} year={year} />
    else content = <Results />
  } else content = <Generate />

  const item = (label: string, to: string, active: boolean, icon: string) => (
    <a className={`nav ${active ? 'active' : ''}`} role="link" tabIndex={0}
       aria-current={active ? 'page' : undefined}
       onClick={() => nav(to)}
       onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); nav(to) } }}>
      {ICONS[icon]}<span>{label}</span>
    </a>
  )

  return (
    <div>
      <div className="bg-layer" aria-hidden="true" />
      <div className="layout">
        <aside className="sidebar">
          <div className="brand">智能税务筹划</div>
          <div className="brand-sub">税务分析工作台</div>

          {item('生成', '/generate', top === 'generate', 'gen')}
          {item('生成结果', '/results', top === 'results', 'result')}
          {item('审查', '/review', top === 'review', 'review')}

          <div className="divider" />
          {item('数据中心', '/data', top === 'data', 'data')}
          {item('知识库', '/kb', top === 'kb', 'kb')}
          {item('设置', '/settings', top === 'settings', 'settings')}
          {item('帮助', '/help', top === 'help', 'help')}
          <div className="ver">v8.1 · 智能税务筹划</div>
        </aside>
        <main className="main">
          {seg.length > 1 && (
            <div className="ctxbar" aria-label="当前位置">
              <a onClick={() => nav(`/${top}`)}>{SECTION[top] || top}</a>
              {top === 'results' && seg[1] && (
                <> <span className="sep">/</span>
                  <a onClick={() => nav(`/results/${seg[1]}/${seg[2]}`)}>{seg[1]} {seg[2]}</a></>
              )}
              {top === 'results' && seg[3] && (
                <> <span className="sep">/</span>
                  <span className="cur">{decodeURIComponent(seg[3])}</span></>
              )}
            </div>
          )}
          <div className="page" key={route}>{content}</div>
          <footer className="ai-foot">
            <span className="ai-badge">AI 生成</span>
            <span>本系统全部输出由人工智能自动生成，仅供参考，不构成专业意见或纳税申报依据。</span>
          </footer>
        </main>
      </div>
    </div>
  )
}
