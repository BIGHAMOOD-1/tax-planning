import { useEffect, useRef } from 'react'
import * as echarts from 'echarts'

export default function Chart({ option, height = 320, label = '图表' }: {
  option: Record<string, unknown>; height?: number; label?: string
}) {
  const ref = useRef<HTMLDivElement>(null)
  const inst = useRef<echarts.ECharts | null>(null)

  useEffect(() => {
    if (!ref.current) return
    inst.current = echarts.init(ref.current)
    const onResize = () => inst.current?.resize()
    window.addEventListener('resize', onResize)
    return () => { window.removeEventListener('resize', onResize); inst.current?.dispose() }
  }, [])

  useEffect(() => { inst.current?.setOption(option as echarts.EChartsOption, true) }, [option])

  return <div ref={ref} role="img" aria-label={label} style={{ width: '100%', height }} />
}
