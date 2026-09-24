import { createPortal } from 'react-dom'
import { nav } from '../router'
import { GLOSSARY } from '../help/glossary'
import { usePopover } from './usePopover'

/** 行内术语：显示名称 + ⓘ，悬停/点击弹出释义，可跳「帮助」。
 *  浮层 Portal 到 body + 视口内钳制（四边都不超出），滚动时自动关闭。 */
export function Term({ k, label, children }: { k: string; label?: string; children?: React.ReactNode }) {
  const g = GLOSSARY[k]
  const { open, anchorRef, popRef, enter, leave, toggle } = usePopover()

  return (
    <span className="term" ref={anchorRef as React.Ref<HTMLSpanElement>} onMouseEnter={enter} onMouseLeave={leave}>
      {children ?? label ?? g?.label ?? k}
      <span className="term-ico" role="button" aria-label="术语说明" onClick={toggle}>ⓘ</span>
      {open && g && createPortal(
        <span className="term-pop" ref={popRef as React.Ref<HTMLSpanElement>} onMouseEnter={enter} onMouseLeave={leave}>
          <b>{g.label}</b>
          <span className="term-desc">{g.desc}</span>
          <a onClick={(e) => { e.stopPropagation(); toggle(); nav('/help') }}>查看帮助 →</a>
        </span>,
        document.body,
      )}
    </span>
  )
}
