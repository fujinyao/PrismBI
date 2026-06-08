'use client'

import { useEffect } from 'react'
import { useRouter } from 'next/navigation'

export default function SetupModelsRedirectPage() {
  const router = useRouter()

  useEffect(() => {
    router.replace('/setup/connection')
  }, [router])

  return null
}
