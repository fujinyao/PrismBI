'use client'

import { useEffect } from 'react'
import { useQuery } from '@tanstack/react-query'
import { settingsApi } from '@/lib/api'
import { setRequestTimeout } from '@/lib/api'
import { DEFAULT_BRANDING, useBrandingStore } from '@/stores/brandingStore'
import { useAuthStore } from '@/stores/authStore'

function ensureIconLink(rel: string) {
  let link = document.querySelector<HTMLLinkElement>(`link[rel="${rel}"]`)
  if (!link) {
    link = document.createElement('link')
    link.rel = rel
    document.head.appendChild(link)
  }
  return link
}

export function BrandingProvider() {
  const setBranding = useBrandingStore((s) => s.setBranding)
  const appName = useBrandingStore((s) => s.appName)
  const appDescription = useBrandingStore((s) => s.appDescription)
  const appIcon = useBrandingStore((s) => s.appIcon)
  const token = useAuthStore((s) => s.token)
  const hasPermission = useAuthStore((s) => s.hasPermission)
  const canReadSettings = hasPermission('settings', 'read')

  const { data } = useQuery({
    queryKey: token && canReadSettings ? ['settings', 'private'] : ['settings', 'public'],
    queryFn: () => token && canReadSettings ? settingsApi.getAll() : settingsApi.getPublic(),
    refetchOnMount: true,
  })

  useEffect(() => {
    setBranding((data as any)?.settings ?? data ?? DEFAULT_BRANDING)
    const settings = (data as any)?.settings ?? data
    if (settings?.request_timeout_ms) {
      setRequestTimeout(Number(settings.request_timeout_ms))
    }
  }, [data, setBranding])

  useEffect(() => {
    document.title = appName || DEFAULT_BRANDING.appName

    const description = document.querySelector<HTMLMetaElement>('meta[name="description"]')
      ?? document.head.appendChild(document.createElement('meta'))
    description.name = 'description'
    description.content = appDescription || DEFAULT_BRANDING.appDescription

    const href = appIcon || DEFAULT_BRANDING.appIcon
    ensureIconLink('icon').href = href
    ensureIconLink('shortcut icon').href = href
    ensureIconLink('apple-touch-icon').href = href
  }, [appName, appDescription, appIcon])

  return null
}
