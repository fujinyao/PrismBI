'use client'

/* eslint-disable @next/next/no-img-element */

import { DEFAULT_BRANDING, useBrandingStore } from '@/stores/brandingStore'
import { cn } from '@/lib/utils'

interface BrandLogoProps {
  src?: string | null
  className?: string
  alt?: string
}

export function BrandLogo({ src, className, alt }: BrandLogoProps) {
  const appName = useBrandingStore((s) => s.appName)
  const storeLogo = useBrandingStore((s) => s.appLogo)
  const logo = src || storeLogo || DEFAULT_BRANDING.appLogo

  return (
    <img
      src={logo}
      alt={alt ?? appName}
      className={cn('h-8 w-8 rounded object-contain', className)}
    />
  )
}
