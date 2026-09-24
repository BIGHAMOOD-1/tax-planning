import { createPortal } from 'react-dom'
import { usePopover } from './usePopover'

function Ref({ token, desc }: { token: string; desc: string }) {
  const { open, anchorRef, popRef, enter, leave, toggle } = usePopover()
  return (
    <span className="poolref" ref={anchorRef as React.Ref<HTMLSpanElement>}
          onMouseEnter={enter} onMouseLeave={leave} onClick={toggle}>
      {token}
      {open && createPortal(
        <span className="poolref-pop" ref={popRef as React.Ref<HTMLSpanElement>}
              onMouseEnter={enter} onMouseLeave={leave}>{desc}</span>,
        document.body,
      )}
    </span>
  )
}

/** 把文本中的池编号（[P1]/[F2]/[C3]/[R4]）渲染为可悬停查看名称的片段。 */
export function PoolRefs({ text, map }: { text: string; map: Record<string, string> }) {
  const parts = String(text ?? '').split(/(\[[PFCR]\d+\])/g)
  return (
    <>
      {parts.map((p, i) => {
        const m = p.match(/^\[([PFCR]\d+)\]$/)
        if (m && map[m[1]]) return <Ref key={i} token={p} desc={map[m[1]]} />
        return <span key={i}>{p}</span>
      })}
    </>
  )
}
