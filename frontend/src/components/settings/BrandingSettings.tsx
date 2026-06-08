'use client'

/* eslint-disable @next/next/no-img-element */

import { useEffect, useRef, useState } from 'react'
import { BrandLogo } from '@/components/brand/BrandLogo'
import { Button } from '@/components/ui/Button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/Card'
import { Input } from '@/components/ui/Input'
import { cn } from '@/lib/utils'
import { useI18nStore } from '@/stores/i18nStore'

interface BrandingSettingsProps {
  settings: any
  onSave: (s: any) => void
  saving?: boolean
  canSave?: boolean
}

export function BrandingSettings({ settings, onSave, saving, canSave = true }: BrandingSettingsProps) {
  const t = useI18nStore((s) => s.t)
  const [logo, setLogo] = useState<string | null>(settings.app_logo ?? settings.logo ?? '/prismbi-icon.svg')
  const [favicon, setFavicon] = useState<string | null>(settings.app_icon ?? settings.favicon ?? '/prismbi-icon.svg')
  const [appName, setAppName] = useState(settings.app_name ?? settings.appName ?? 'PrismBI')
  const [appDescription, setAppDescription] = useState(
    settings.app_description ?? settings.appDescription ?? t('settings.branding.platformDesc', 'Your business intelligence platform'),
  )
  const [brandColor, setBrandColor] = useState(settings.brandColor ?? '#6366f1')
  const [dragOver, setDragOver] = useState(false)
  const logoRef = useRef<HTMLInputElement>(null)
  const faviconRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    setLogo(settings.app_logo ?? settings.logo ?? '/prismbi-icon.svg')
    setFavicon(settings.app_icon ?? settings.favicon ?? '/prismbi-icon.svg')
    setAppName(settings.app_name ?? settings.appName ?? 'PrismBI')
    setAppDescription(
      settings.app_description ?? settings.appDescription ?? t('settings.branding.platformDesc', 'Your business intelligence platform'),
    )
    setBrandColor(settings.brandColor ?? '#6366f1')
  }, [settings, t])

  const handleFile = (file: File, cb: (data: string) => void) => {
    const reader = new FileReader()
    reader.onload = () => cb(reader.result as string)
    reader.readAsDataURL(file)
  }

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    onSave({ app_name: appName, app_description: appDescription, logo, icon: favicon, brandColor })
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-6">
      <div className="grid gap-4 lg:grid-cols-3">
        <Card>
          <CardHeader>
            <CardTitle>{t('settings.branding.appLogo', 'App Logo')}</CardTitle>
          </CardHeader>
          <CardContent>
            <div
              className={cn(
                'relative flex h-40 cursor-pointer items-center justify-center rounded-lg border-2 border-dashed p-4 transition-colors',
                dragOver
                  ? 'border-primary bg-primary-50 dark:bg-primary-900/20'
                  : 'border-gray-300 hover:border-gray-400 dark:border-gray-600',
              )}
              onDragOver={(e) => {
                e.preventDefault()
                setDragOver(true)
              }}
              onDragLeave={() => setDragOver(false)}
              onDrop={(e) => {
                e.preventDefault()
                setDragOver(false)
                const file = e.dataTransfer.files[0]
                if (file) handleFile(file, setLogo)
              }}
              onClick={() => logoRef.current?.click()}
            >
              {logo ? (
                <img src={logo} alt="Logo preview" className="max-h-24 max-w-full object-contain" />
              ) : (
                <svg className="h-10 w-10 text-gray-300 dark:text-gray-600" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z" />
                </svg>
              )}
              <input
                ref={logoRef}
                type="file"
                accept="image/*"
                className="hidden"
                onChange={(e) => {
                  const file = e.target.files?.[0]
                  if (file) handleFile(file, setLogo)
                }}
              />
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>{t('settings.branding.favicon', 'Favicon')}</CardTitle>
          </CardHeader>
          <CardContent>
            <div
              className="flex h-40 cursor-pointer items-center justify-center rounded-lg border border-gray-300 p-4 hover:border-gray-400 dark:border-gray-600"
              onClick={() => faviconRef.current?.click()}
            >
              {favicon ? (
                <img src={favicon} alt="Favicon preview" className="h-16 w-16 rounded object-contain" />
              ) : (
                <BrandLogo className="h-16 w-16 opacity-40" />
              )}
              <input
                ref={faviconRef}
                type="file"
                accept="image/x-icon,image/png,image/svg+xml"
                className="hidden"
                onChange={(e) => {
                  const file = e.target.files?.[0]
                  if (file) handleFile(file, setFavicon)
                }}
              />
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>{t('settings.branding.brandColor', 'Brand Color')}</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="flex h-40 flex-col justify-center gap-4 rounded-lg border border-gray-300 p-4 dark:border-gray-600">
              <div className="h-16 rounded-lg border border-gray-200 dark:border-gray-700" style={{ backgroundColor: brandColor }} />
              <div className="flex items-center gap-3">
                <input
                  type="color"
                  value={brandColor}
                  onChange={(e) => setBrandColor(e.target.value)}
                  className="h-10 w-16 cursor-pointer rounded border border-gray-300 bg-transparent p-1 dark:border-gray-600"
                />
                <span className="font-mono text-sm text-gray-600 dark:text-gray-400">{brandColor}</span>
              </div>
            </div>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardContent className="grid gap-4 md:grid-cols-2">
          <Input
            label={t('settings.branding.appName', 'App Name')}
            value={appName}
            onChange={(e) => setAppName(e.target.value)}
            placeholder={t('settings.branding.appNamePlaceholder', 'PrismBI')}
          />
          <Input
            label={t('settings.branding.appDescription', 'App Description')}
            value={appDescription}
            onChange={(e) => setAppDescription(e.target.value)}
            placeholder={t('settings.branding.appDescriptionPlaceholder', 'Your business intelligence platform')}
          />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>{t('settings.branding.preview', 'Preview')}</CardTitle>
        </CardHeader>
        <CardContent>
          <div
            className="flex items-center gap-4 rounded-lg border border-gray-200 p-4 dark:border-gray-700"
            style={{ borderLeftColor: brandColor, borderLeftWidth: 4 }}
          >
            {logo ? (
              <BrandLogo src={logo} className="h-14 w-14 shrink-0" alt="" />
            ) : (
              <BrandLogo className="h-14 w-14 shrink-0" />
            )}
            <div className="min-w-0">
              <p className="truncate text-2xl font-semibold leading-8 text-gray-900 dark:text-gray-100" style={{ color: brandColor }}>
                {appName || 'PrismBI'}
              </p>
              <p className="truncate text-sm leading-5 text-gray-500 dark:text-gray-400">
                {appDescription || t('settings.branding.platformDesc', 'Your business intelligence platform')}
              </p>
            </div>
          </div>
        </CardContent>
      </Card>

      <div className="flex justify-end gap-2">
        <Button type="button" variant="secondary" onClick={() => { setLogo('/prismbi-icon.svg'); setFavicon('/prismbi-icon.svg') }}>
          {t('settings.branding.useDefaults', 'Use defaults')}
        </Button>
        <Button type="submit" loading={saving} disabled={!canSave}>{t('settings.branding.save', 'Save Branding')}</Button>
      </div>
    </form>
  )
}
