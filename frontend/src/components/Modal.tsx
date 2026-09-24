import { useEffect, useRef, type ReactNode } from 'react'
import { createPortal } from 'react-dom'

export function Modal({ title, children, onClose, onConfirm,
  confirmText = '确认', cancelText = '取消', danger = false }: {
  title: string
  children: ReactNode
  onClose: () => void
  onConfirm?: () => void
  confirmText?: string
  cancelText?: string
  danger?: boolean
}) {
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', onKey)
    const prev = document.activeElement as HTMLElement | null
    ref.current?.querySelector<HTMLElement>('button, a[href], input, select, textarea, [tabindex]')?.focus()
    return () => { document.removeEventListener('keydown', onKey); prev?.focus?.() }
  }, [onClose])

  const trap = (e: React.KeyboardEvent) => {
    if (e.key !== 'Tab') return
    const nodes = ref.current?.querySelectorAll<HTMLElement>(
      'button, a[href], input, select, textarea, [tabindex]:not([tabindex="-1"])')
    if (!nodes || nodes.length === 0) return
    const list = Array.from(nodes)
    const first = list[0]; const last = list[list.length - 1]
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus() }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus() }
  }

  return createPortal(
    <div className="modal-mask" onClick={onClose}>
      <div className="modal" role="dialog" aria-modal="true" aria-label={title} ref={ref}
           onClick={(e) => e.stopPropagation()} onKeyDown={trap}>
        <h3>{title}</h3>
        <div className="modal-body">{children}</div>
        <div className="modal-actions">
          <button className="btn" onClick={onClose}>{cancelText}</button>
          {onConfirm && (
            <button className={`btn ${danger ? 'danger' : 'primary'}`} onClick={onConfirm}>{confirmText}</button>
          )}
        </div>
      </div>
    </div>,
    document.body,
  )
}
