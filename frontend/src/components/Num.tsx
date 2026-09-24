import { useEffect, useRef, useState } from 'react'

/** 大数字 count-up（prefers-reduced-motion 时直接显示）。 */
export function Num({ value, format }: { value: number | null | undefined; format: (v: number) => string }) {
  const [display, setDisplay] = useState<number | null>(value ?? null)
  const raf = useRef<number | null>(null)

  useEffect(() => {
    if (value == null || !isFinite(Number(value))) { setDisplay(value ?? null); return }
    const reduce = typeof window !== 'undefined'
      && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
    if (reduce) { setDisplay(value); return }
    const target = Number(value)
    const start = performance.now()
    const dur = 650
    const tick = (t: number) => {
      const p = Math.min(1, (t - start) / dur)
      const eased = 1 - Math.pow(1 - p, 3)
      setDisplay(target * eased)
      if (p < 1) raf.current = requestAnimationFrame(tick)
    }
    raf.current = requestAnimationFrame(tick)
    return () => { if (raf.current) cancelAnimationFrame(raf.current) }
  }, [value])

  return <>{display == null ? '—' : format(display)}</>
}
