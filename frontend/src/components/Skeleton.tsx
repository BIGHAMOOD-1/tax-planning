export function Skeleton({ lines = 3 }: { lines?: number }) {
  return (
    <div className="card">
      <div className="skeleton line" style={{ width: '42%', height: 16 }} />
      {Array.from({ length: lines }).map((_, i) => (
        <div key={i} className="skeleton line" style={{ width: `${92 - i * 11}%` }} />
      ))}
    </div>
  )
}
