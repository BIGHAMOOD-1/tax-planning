import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'

/** 锚点浮层：fixed 定位 + 视口内钳制（四边都不超出）+ 滚动/缩放自动关闭。
 *  用「延迟关闭」让鼠标能从锚点移到浮层。 */
export function usePopover() {
  const [open, setOpen] = useState(false)
  const anchorRef = useRef<HTMLElement | null>(null)
  const popRef = useRef<HTMLElement | null>(null)
  const timer = useRef<number | null>(null)

  const place = useCallback(() => {
    const a = anchorRef.current
    const p = popRef.current
    if (!a || !p) return
    const ar = a.getBoundingClientRect()
    const pr = p.getBoundingClientRect()
    const vw = window.innerWidth
    const vh = window.innerHeight
    const pad = 8
    let left = ar.left
    if (left + pr.width > vw - pad) left = vw - pad - pr.width
    if (left < pad) left = pad
    let top = ar.bottom + 8
    if (top + pr.height > vh - pad) top = ar.top - pr.height - 8   // 上方放不下则翻到上面
    if (top < pad) top = pad
    p.style.left = `${Math.round(left)}px`
    p.style.top = `${Math.round(top)}px`
  }, [])

  useLayoutEffect(() => {
    if (open) place()
  }, [open, place])

  const enter = useCallback(() => {
    if (timer.current) { window.clearTimeout(timer.current); timer.current = null }
    setOpen(true)
  }, [])
  const leave = useCallback(() => {
    if (timer.current) window.clearTimeout(timer.current)
    timer.current = window.setTimeout(() => setOpen(false), 220)
  }, [])
  const toggle = useCallback(() => setOpen((o) => !o), [])

  useEffect(() => {
    if (!open) return
    const close = () => setOpen(false)
    window.addEventListener('scroll', close, true)
    window.addEventListener('resize', close)
    return () => {
      window.removeEventListener('scroll', close, true)
      window.removeEventListener('resize', close)
    }
  }, [open])

  return { open, setOpen, anchorRef, popRef, enter, leave, toggle, place }
}
