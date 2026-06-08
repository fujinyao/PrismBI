'use client'

import { useEffect } from 'react'
import { usePathname } from 'next/navigation'

export function useRouteFocus() {
  const pathname = usePathname()

  useEffect(() => {
    const mainContent = document.getElementById('main-content')
    if (mainContent) {
      mainContent.focus({ preventScroll: true })
      mainContent.scrollIntoView({ behavior: 'instant', block: 'start' })
    }
  }, [pathname])
}