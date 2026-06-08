'use client'

import { useState, useEffect, useRef } from 'react'
import { cn } from '@/lib/utils'

interface StreamContentProps {
  content: string
  isLoading: boolean
  type: 'text' | 'sql'
}

export function StreamContent({ content, isLoading, type }: StreamContentProps) {
  const [displayedLength, setDisplayedLength] = useState(0)
  const [cursorVisible, setCursorVisible] = useState(true)
  const prevContentRef = useRef(content)

  useEffect(() => {
    if (content !== prevContentRef.current) {
      if (isLoading) {
        setDisplayedLength(prevContentRef.current.length)
      }
      prevContentRef.current = content
    }
  }, [content, isLoading])

  useEffect(() => {
    if (!isLoading) {
      setDisplayedLength(content.length)
      return
    }
    if (displayedLength >= content.length) return
    const timer = setTimeout(() => {
      setDisplayedLength((prev) => Math.min(prev + 1, content.length))
    }, 12)
    return () => clearTimeout(timer)
  }, [content, isLoading, displayedLength])

  useEffect(() => {
    if (!isLoading) {
      setCursorVisible(false)
      return
    }
    const interval = setInterval(() => setCursorVisible((v) => !v), 530)
    return () => clearInterval(interval)
  }, [isLoading])

  const text = isLoading ? content.slice(0, displayedLength) : content

  if (type === 'sql') {
    return (
      <div className="relative">
        <pre
          className={cn(
            'overflow-x-auto rounded-md bg-gray-50 p-4 font-mono text-sm leading-relaxed text-gray-800 dark:bg-gray-900 dark:text-gray-200',
            isLoading && 'min-h-[2em]',
          )}
        >
          <code>{text}</code>
          {isLoading && cursorVisible && (
            <span className="ml-0.5 inline-block h-[1em] w-[2px] animate-pulse bg-primary align-text-bottom" />
          )}
        </pre>
      </div>
    )
  }

  const renderContent = (t: string) => {
    return t.split(/(\*\*.*?\*\*|\*.*?\*|`.*?`)/).map((part, i) => {
      if (part.startsWith('**') && part.endsWith('**')) {
        return <strong key={i}>{part.slice(2, -2)}</strong>
      }
      if (part.startsWith('*') && part.endsWith('*') && !part.startsWith('**')) {
        return <em key={i}>{part.slice(1, -1)}</em>
      }
      if (part.startsWith('`') && part.endsWith('`')) {
        return (
          <code key={i} className="rounded bg-gray-100 px-1 py-0.5 font-mono text-sm dark:bg-gray-800">
            {part.slice(1, -1)}
          </code>
        )
      }
      return <span key={i}>{part}</span>
    })
  }

  return (
    <div className={cn('leading-relaxed text-gray-800 dark:text-gray-200', isLoading && 'min-h-[1em]')}>
      {text.split('\n').map((line, i) => (
        <p key={i} className={cn('mb-2 last:mb-0', line === '' && 'h-4')}>
          {renderContent(line)}
        </p>
      ))}
      {isLoading && cursorVisible && (
        <span className="ml-0.5 inline-block h-[1em] w-[2px] animate-pulse bg-primary align-text-bottom" />
      )}
    </div>
  )
}
