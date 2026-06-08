import { create } from 'zustand'

export const DEFAULT_BRANDING = {
  appName: 'PrismBI',
  appDescription: 'Your Business Intelligence Platform',
  appLogo: '/prismbi-icon.svg',
  appIcon: '/prismbi-icon.svg',
}

type BrandingPayload = Record<string, unknown> | null | undefined

interface BrandingState {
  appName: string
  appDescription: string
  appLogo: string
  appIcon: string
  setBranding: (branding: BrandingPayload) => void
}

function stringValue(value: unknown, fallback: string) {
  return typeof value === 'string' && value.trim() ? value : fallback
}

export const useBrandingStore = create<BrandingState>()((set) => ({
  ...DEFAULT_BRANDING,

  setBranding: (branding) => {
    set({
      appName: stringValue(branding?.app_name ?? branding?.appName, DEFAULT_BRANDING.appName),
      appDescription: stringValue(
        branding?.app_description ?? branding?.appDescription,
        DEFAULT_BRANDING.appDescription,
      ),
      appLogo: stringValue(
        branding?.app_logo ?? branding?.logo ?? branding?.appLogo,
        DEFAULT_BRANDING.appLogo,
      ),
      appIcon: stringValue(
        branding?.app_icon ?? branding?.icon ?? branding?.favicon ?? branding?.appIcon,
        DEFAULT_BRANDING.appIcon,
      ),
    })
  },
}))
