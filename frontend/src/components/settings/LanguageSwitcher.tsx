'use client'

import { LOCALES, type LocaleCode } from '@/lib/i18n/locales'
import { Card, CardContent } from '@/components/ui/Card'
import { Button } from '@/components/ui/Button'

interface LanguageSwitcherProps {
  value: LocaleCode
  onChange: (locale: LocaleCode) => void
  saving?: boolean
}

export function LanguageSwitcher({ value, onChange, saving }: LanguageSwitcherProps) {
  return (
    <Card>
      <CardContent>
        <div className="grid gap-3 sm:grid-cols-2 md:grid-cols-3 xl:grid-cols-4">
          {LOCALES.map((l) => (
            <Button
              key={l.code}
              type="button"
              variant={value === l.code ? 'primary' : 'secondary'}
              loading={saving && value === l.code}
              disabled={saving}
              onClick={() => onChange(l.code)}
              className={`min-h-11 justify-start rounded-lg px-4 text-left text-sm ${
                value === l.code
                  ? 'border-primary bg-primary-50 text-primary dark:bg-primary-900 dark:text-primary-200'
                  : 'border-gray-200 text-gray-600 hover:border-gray-300 dark:border-gray-600 dark:text-gray-400 dark:hover:border-gray-500'
              }`}
            >
              {l.nativeLabel}
            </Button>
          ))}
        </div>
      </CardContent>
    </Card>
  )
}
