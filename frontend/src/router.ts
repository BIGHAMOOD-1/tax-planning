import { useEffect, useState } from 'react'

export function useRoute(): string {
  const [hash, setHash] = useState(() => window.location.hash.replace(/^#/, '') || '/generate')
  useEffect(() => {
    const on = () => setHash(window.location.hash.replace(/^#/, '') || '/generate')
    window.addEventListener('hashchange', on)
    return () => window.removeEventListener('hashchange', on)
  }, [])
  return hash
}

export function nav(to: string) {
  window.location.hash = to
}

export function segments(route: string): string[] {
  return route.split('/').filter(Boolean)
}
