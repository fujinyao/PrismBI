'use client'

import { Component, type ReactNode, type ErrorInfo } from 'react'
import { Button } from './Button'
import { useI18nStore } from '@/stores/i18nStore'

interface ErrorBoundaryProps {
  children: ReactNode
  fallback?: ReactNode
  onError?: (error: Error, errorInfo: ErrorInfo) => void
}

interface ErrorBoundaryState {
  hasError: boolean
  error: Error | null
}

export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  constructor(props: ErrorBoundaryProps) {
    super(props)
    this.state = { hasError: false, error: null }
  }

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { hasError: true, error }
  }

  override componentDidCatch(error: Error, errorInfo: ErrorInfo) {
    console.error('ErrorBoundary caught:', error, errorInfo)
    this.props.onError?.(error, errorInfo)
  }

  handleRetry = () => {
    this.setState({ hasError: false, error: null })
  }

  override render() {
    if (this.state.hasError) {
      if (this.props.fallback) return this.props.fallback

      const t = useI18nStore.getState().t

      return (
        <div className="flex flex-col items-center justify-center px-6 py-12 text-center">
          <svg
            className="mb-4 h-12 w-12 text-error"
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={1.5}
              d="M12 9v3.75m9-.75a9 9 0 11-18 0 9 9 0 0118 0zm-9 3.75h.008v.008H12v-.008z"
            />
          </svg>
          <h3 className="text-base font-medium text-gray-900 dark:text-gray-100">{t('common.somethingWentWrong', 'Something went wrong')}</h3>
          <p className="mt-1 max-w-sm text-sm text-gray-500 dark:text-gray-400">
            {this.state.error?.message || t('common.unexpectedError', 'An unexpected error occurred')}
          </p>
          <Button variant="primary" size="md" className="mt-4" onClick={this.handleRetry}>
            {t('common.tryAgain', 'Try again')}
          </Button>
        </div>
      )
    }

    return this.props.children
  }
}
