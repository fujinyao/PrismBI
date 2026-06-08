import Link from 'next/link'
import { useI18nStore } from '@/stores/i18nStore'

export default function NotFound() {
  const t = useI18nStore.getState().t

  return (
    <div className="flex min-h-[60vh] flex-col items-center justify-center px-6 py-12 text-center">
      <h1 className="text-6xl font-bold text-gray-200 dark:text-gray-700">404</h1>
      <h2 className="mt-4 text-lg font-semibold text-gray-900 dark:text-gray-100">
        {t('error.notFound', 'Page not found')}
      </h2>
      <p className="mt-2 max-w-md text-sm text-gray-500 dark:text-gray-400">
        {t('error.notFoundDesc', 'The page you are looking for does not exist or has been moved.')}
      </p>
      <Link
        href="/"
        className="mt-6 rounded-md bg-primary px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-primary-700"
      >
        {t('error.goHome', 'Go to Home')}
      </Link>
    </div>
  )
}