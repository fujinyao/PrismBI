'use client'

import { useEffect } from 'react'
import { useRouter } from 'next/navigation'
import { useAuthStore } from '@/stores/authStore'

export default function RootPage() {
  const router = useRouter()
  const user = useAuthStore((s) => s.user)

  useEffect(() => {
    if (user) {
      router.replace('/home')
    } else {
      router.replace('/login')
    }
  }, [user, router])

  return null
}
