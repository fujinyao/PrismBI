'use client'

import { useState, useEffect, useRef } from 'react'
import { cn } from '@/lib/utils'

interface StreamContentProps {
  content: string
  isLoading: boolean
  isComplete: boolean
}

export function StreamContent({ content, isLoading, isComplete }: StreamContentProps) {
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
    if (!isLoading || isComplete) {
      setDisplayedLength(content.length)
      return
    }
    if (displayedLength >= content.length) return
    const timer = setTimeout(() => {
      setDisplayedLength((prev) => Math.min(prev + 1, content.length))
    }, 16)
    return () => clearTimeout(timer)
  }, [content, isLoading, isComplete, displayedLength])

  useEffect(() => {
    if (isComplete) {
      setCursorVisible(false)
      return
    }
    const interval = setInterval(() => setCursorVisible((v) => !v), 530)
    return () => clearInterval(interval)
  }, [isComplete])

  const text = isLoading ? content.slice(0, displayedLength) : content

  const renderInline = (part: string) => {
    if (part.startsWith('**') && part.endsWith('**')) {
      return <strong key={part}>{part.slice(2, -2)}</strong>
    }
    if (part.startsWith('*') && part.endsWith('*') && !part.startsWith('**')) {
      return <em key={part}>{part.slice(1, -1)}</em>
    }
    if (part.startsWith('`') && part.endsWith('`')) {
      return (
        <code
          key={part}
          className="rounded bg-gray-100 px-1 py-0.5 text-sm font-mono dark:bg-gray-800"
        >
          {part.slice(1, -1)}
        </code>
      )
    }
    return part
  }

  const renderContent = (t: string) => {
    return t.split(/(\*\*.*?\*\*|\*.*?\*|`.*?`)/).map((part, i) => (
      <span key={i}>{renderInline(part)}</span>
    ))
  }

  return (
    <div className={cn('leading-relaxed', isLoading && 'min-h-[1em]')} role="region" aria-live="polite" aria-label="AI response">
      {text.split('\n').map((line, i) => (
        <p key={i} className={cn('mb-2 last:mb-0', line === '' && 'h-4')}>
          {renderContent(line)}
        </p>
      ))}
      {!isComplete && cursorVisible && (
        <span className="ml-0.5 inline-block h-[1em] w-[2px] motion-safe:animate-pulse bg-primary align-text-bottom" />
      )}
    </div>
  )
}
