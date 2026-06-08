import type { Metadata } from 'next'
import { Inter } from 'next/font/google'
import { QueryClientProvider } from '@/components/providers/QueryClientProvider'
import { ThemeProvider } from '@/components/providers/ThemeProvider'
import { AuthGuard } from '@/components/providers/AuthGuard'
import { I18nHydrate } from '@/components/providers/I18nHydrate'
import { BrandingProvider } from '@/components/providers/BrandingProvider'
import { ToastProvider } from '@/components/ui/Toast'
import { OfflineBanner } from '@/components/ui/OfflineBanner'
import '@/styles/globals.css'

const inter = Inter({ subsets: ['latin'], variable: '--font-sans' })

export const metadata: Metadata = {
  title: 'PrismBI',
  description: 'Next Generation AI BI Tool',
  icons: {
    icon: '/prismbi-icon.svg',
    shortcut: '/prismbi-icon.svg',
    apple: '/prismbi-icon.svg',
  },
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" data-scroll-behavior="smooth" suppressHydrationWarning>
      <body className={`${inter.variable} font-sans antialiased`}>
        <a href="#main-content" className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-[200] focus:rounded-md focus:bg-primary focus:px-4 focus:py-2 focus:text-white focus:shadow-lg">
          Skip to content
        </a>
        <QueryClientProvider>
          <BrandingProvider />
          <ThemeProvider attribute="class" defaultTheme="light" enableSystem>
            <I18nHydrate />
            <ToastProvider>
              <AuthGuard>{children}</AuthGuard>
              <OfflineBanner />
            </ToastProvider>
          </ThemeProvider>
        </QueryClientProvider>
      </body>
    </html>
  )
}
