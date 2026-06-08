import { cn } from '@/lib/utils'

interface BrandMarkProps {
  className?: string
}

export function BrandMark({ className }: BrandMarkProps) {
  return (
    <svg
      className={cn('h-8 w-8', className)}
      viewBox="0 0 64 64"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      aria-hidden="true"
    >
      <rect width="64" height="64" rx="16" fill="url(#prismbi-bg)" />
      <path d="M16 46V18L32 10L48 18V46L32 54L16 46Z" fill="white" fillOpacity="0.16" />
      <path d="M20 43V21L32 15L44 21V43L32 49L20 43Z" stroke="white" strokeWidth="3" strokeLinejoin="round" />
      <path d="M25 39V29" stroke="white" strokeWidth="4" strokeLinecap="round" />
      <path d="M32 39V24" stroke="white" strokeWidth="4" strokeLinecap="round" />
      <path d="M39 39V32" stroke="white" strokeWidth="4" strokeLinecap="round" />
      <path d="M22 22L32 27L42 22" stroke="#A7F3D0" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" />
      <defs>
        <linearGradient id="prismbi-bg" x1="8" y1="6" x2="58" y2="58" gradientUnits="userSpaceOnUse">
          <stop stopColor="#2563EB" />
          <stop offset="0.48" stopColor="#7C3AED" />
          <stop offset="1" stopColor="#0F766E" />
        </linearGradient>
      </defs>
    </svg>
  )
}
