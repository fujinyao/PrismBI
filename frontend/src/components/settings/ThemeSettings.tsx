'use client'

import { useState } from 'react'
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/Card'
import { Button } from '@/components/ui/Button'
import { cn } from '@/lib/utils'
import { useI18nStore } from '@/stores/i18nStore'
import { useThemeStore } from '@/stores/themeStore'

interface ThemeSettingsProps {
  settings: any
  onSave: (s: any) => void
  saving?: boolean
  canSave?: boolean
}

const presetColors = [
  { label: 'Indigo', value: '#6366f1' },
  { label: 'Blue', value: '#3b82f6' },
  { label: 'Green', value: '#22c55e' },
  { label: 'Red', value: '#ef4444' },
  { label: 'Purple', value: '#a855f7' },
  { label: 'Orange', value: '#f97316' },
]

const DEFAULT_THEME = {
  theme_mode: 'light',
  theme_primary_color: '#1677ff',
  theme_border_radius: 'md',
  theme_font: 'Inter',
}

const radiusOptions = [
  { label: 'Sm', value: 'sm' },
  { label: 'Md', value: 'md' },
  { label: 'Lg', value: 'lg' },
]

const fontOptions = [
  { label: 'Inter', value: 'Inter' },
  { label: 'Roboto', value: 'Roboto' },
]

const themeModes = (t: (key: string, fallback: string) => string) => [
  { label: t('settings.theme.mode.light', 'Light'), value: 'light', icon: '\u2600\uFE0F' },
  { label: t('settings.theme.mode.dark', 'Dark'), value: 'dark', icon: '\u{1F319}' },
  { label: t('settings.theme.mode.system', 'System'), value: 'system', icon: '\u{1F5A5}\uFE0F' },
]

export function ThemeSettings({ settings, onSave, saving, canSave = true }: ThemeSettingsProps) {
  const effectiveSettings = canSave ? settings : DEFAULT_THEME
  const t = useI18nStore((s) => s.t)
  const setTheme = useThemeStore((s) => s.setTheme)
  const storedMode = useThemeStore((s) => s.mode)
  const storedPrimaryColor = useThemeStore((s) => s.primaryColor)
  const storedBorderRadius = useThemeStore((s) => s.borderRadius)
  const storedFont = useThemeStore((s) => s.font)
  const [themeMode, setThemeMode] = useState(effectiveSettings.theme_mode ?? effectiveSettings.themeMode ?? storedMode ?? 'system')
  const [primaryColor, setPrimaryColor] = useState(effectiveSettings.theme_primary_color ?? effectiveSettings.primaryColor ?? storedPrimaryColor ?? '#1677ff')
  const [customColor, setCustomColor] = useState(effectiveSettings.theme_primary_color ?? effectiveSettings.primaryColor ?? storedPrimaryColor ?? '#1677ff')
  const [borderRadius, setBorderRadius] = useState<string>(effectiveSettings.theme_border_radius ?? effectiveSettings.borderRadius ?? storedBorderRadius ?? 'md')
  const [font, setFont] = useState(effectiveSettings.theme_font ?? effectiveSettings.font ?? storedFont ?? 'Inter')
  const [showCustom, setShowCustom] = useState(false)

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    setTheme({ mode: themeMode as 'light' | 'dark' | 'system', primaryColor, borderRadius, font })
    onSave({ mode: themeMode, primary_color: primaryColor, border_radius: borderRadius, font })
  }

  const handleModeChange = (mode: string) => {
    setThemeMode(mode)
    setTheme({ mode: mode as 'light' | 'dark' | 'system', primaryColor, borderRadius, font })
  }

  const handleColorChange = (color: string) => {
    setPrimaryColor(color)
    setCustomColor(color)
    setTheme({ mode: themeMode as 'light' | 'dark' | 'system', primaryColor: color, borderRadius, font })
  }

  const handleRadiusChange = (radius: string) => {
    setBorderRadius(radius)
    setTheme({ mode: themeMode as 'light' | 'dark' | 'system', primaryColor, borderRadius: radius, font })
  }

  const handleFontChange = (nextFont: string) => {
    setFont(nextFont)
    setTheme({ mode: themeMode as 'light' | 'dark' | 'system', primaryColor, borderRadius, font: nextFont })
  }

  const radiusClass = ({
    sm: 'rounded-sm',
    md: 'rounded-md',
    lg: 'rounded-lg',
  } as Record<string, string>)[borderRadius] ?? 'rounded-md'

  return (
    <form onSubmit={handleSubmit} className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>{t('settings.theme.themeMode', 'Theme Mode')}</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-3 gap-3">
            {themeModes(t).map((mode) => (
              <button
                key={mode.value}
                type="button"
                onClick={() => handleModeChange(mode.value)}
                className={cn(
                  'flex flex-col items-center gap-2 rounded-lg border-2 p-4 transition-colors',
                  themeMode === mode.value
                    ? 'border-primary bg-primary-50 dark:bg-primary-900/20'
                    : 'border-gray-200 dark:border-gray-700 hover:border-gray-300',
                )}
              >
                <span className="text-2xl">{mode.icon}</span>
                <span className="text-sm font-medium text-gray-700 dark:text-gray-300">{mode.label}</span>
              </button>
            ))}
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardContent>
          <div className="grid gap-4 lg:grid-cols-3">
            <Card>
              <CardHeader>
                <CardTitle>{t('settings.theme.primaryColor', 'Primary Color')}</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="flex min-h-24 flex-wrap content-start gap-3">
                  {presetColors.map((c) => (
                    <button
                      key={c.value}
                      type="button"
                      onClick={() => { handleColorChange(c.value); setShowCustom(false) }}
                      className={cn(
                        'h-8 w-8 rounded-full border-2 transition-transform hover:scale-110',
                        primaryColor === c.value && !showCustom
                          ? 'scale-110 border-gray-900 dark:border-white'
                          : 'border-transparent',
                      )}
                      style={{ backgroundColor: c.value }}
                      title={t(`settings.theme.color.${c.value}`, c.label)}
                    />
                  ))}
                  <button
                    type="button"
                    onClick={() => setShowCustom(!showCustom)}
                    className={cn(
                      'flex h-8 w-8 items-center justify-center rounded-full border-2 text-xs font-medium',
                      showCustom
                        ? 'border-gray-900 dark:border-white'
                        : 'border-gray-300 dark:border-gray-600',
                    )}
                  >
                    +
                  </button>
                  {showCustom && (
                    <div className="flex w-full items-center gap-3">
                      <input
                        type="color"
                        value={customColor}
                        onChange={(e) => handleColorChange(e.target.value)}
                        className="h-8 w-16 cursor-pointer rounded border border-gray-300 bg-transparent p-0.5 dark:border-gray-600"
                      />
                      <span className="font-mono text-sm text-gray-600 dark:text-gray-400">{customColor}</span>
                    </div>
                  )}
                </div>
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>{t('settings.theme.borderRadius', 'Border Radius')}</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="flex min-h-24 items-center gap-3">
                  {radiusOptions.map((r) => (
                    <button
                      key={r.value}
                      type="button"
                      onClick={() => handleRadiusChange(r.value)}
                      className={cn(
                        'flex-1 rounded-lg border-2 px-4 py-3 text-sm font-medium transition-colors',
                        borderRadius === r.value
                          ? 'border-primary bg-primary-50 text-primary dark:bg-primary-900/20'
                          : 'border-gray-200 text-gray-600 hover:border-gray-300 dark:border-gray-700 dark:text-gray-400',
                      )}
                    >
                      {r.label}
                    </button>
                  ))}
                </div>
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>{t('settings.theme.font', 'Font')}</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="flex min-h-24 items-center">
                  <select
                    value={font}
                    onChange={(e) => handleFontChange(e.target.value)}
                    className="block w-full rounded-md border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 focus:outline-none focus:ring-2 focus:ring-primary-300 dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100"
                  >
                    {fontOptions.map((f) => (
                      <option key={f.value} value={f.value}>{f.label}</option>
                    ))}
                  </select>
                </div>
              </CardContent>
            </Card>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>{t('settings.theme.livePreview', 'Live Preview')}</CardTitle>
        </CardHeader>
        <CardContent>
          <div
            className={cn('border border-gray-200 dark:border-gray-700 p-4', radiusClass)}
            style={{ fontFamily: font }}
          >
            <div className="flex items-center gap-3">
              <div
                className={cn('flex h-10 w-10 items-center justify-center text-white text-sm font-bold', radiusClass)}
                style={{ backgroundColor: primaryColor }}
              >
                P
              </div>
              <div>
                <p className="font-semibold text-gray-900 dark:text-gray-100">{t('settings.theme.previewCard', 'Preview Card')}</p>
                <p className="text-sm text-gray-500 dark:text-gray-400">
                  {t('settings.theme.previewDescription', 'This is how themed elements will appear')}
                </p>
              </div>
            </div>
            <div className="mt-4 flex gap-2">
              <button
                className={cn('px-4 py-2 text-sm font-medium text-white', radiusClass)}
                style={{ backgroundColor: primaryColor }}
              >
                {t('settings.theme.primaryButton', 'Primary Button')}
              </button>
              <button
                className={cn('border border-gray-300 dark:border-gray-600 px-4 py-2 text-sm font-medium text-gray-700 dark:text-gray-300', radiusClass)}
              >
                {t('settings.theme.secondary', 'Secondary')}
              </button>
            </div>
          </div>
        </CardContent>
      </Card>

      <div className="flex justify-end">
        <Button type="submit" loading={saving} disabled={!canSave}>{t('settings.theme.save', 'Save Theme')}</Button>
      </div>
    </form>
  )
}
