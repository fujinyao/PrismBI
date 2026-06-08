const isTauri = (): boolean => {
  if (typeof window === 'undefined') return false
  return !!(window as unknown as Record<string, unknown>).__TAURI_INTERNALS__
}

let invoke: ((cmd: string, args?: Record<string, unknown>) => Promise<unknown>) | null = null

async function getInvoke() {
  if (invoke) return invoke
  if (!isTauri()) return null
  try {
    const tauri = await import('@tauri-apps/api/core')
    invoke = tauri.invoke
    return invoke
  } catch {
    return null
  }
}

export async function getAppInfo(): Promise<{ name: string; version: string; platform: string; arch: string } | null> {
  const fn = await getInvoke()
  if (!fn) return null
  try {
    return (await fn('get_app_info', {})) as { name: string; version: string; platform: string; arch: string }
  } catch {
    return null
  }
}

export async function getBackendStatus(): Promise<{ running: boolean; pid: number | null; port: number } | null> {
  const fn = await getInvoke()
  if (!fn) return null
  try {
    return (await fn('get_backend_status', {})) as { running: boolean; pid: number | null; port: number }
  } catch {
    return null
  }
}

export async function openExternal(url: string): Promise<void> {
  const fn = await getInvoke()
  if (!fn) {
    window.open(url, '_blank', 'noopener,noreferrer')
    return
  }
  try {
    await fn('open_external', { url })
  } catch {
    window.open(url, '_blank', 'noopener,noreferrer')
  }
}

export async function onBackendStatus(callback: (status: { running: boolean; pid: number | null; port: number }) => void): Promise<(() => void) | null> {
  if (!isTauri()) return null
  try {
    const { listen } = await import('@tauri-apps/api/event')
    const unlisten = await listen<{ running: boolean; pid: number | null; port: number }>('backend-status', (event) => {
      callback(event.payload)
    })
    return unlisten
  } catch {
    return null
  }
}

export async function openFileDialog(options?: { multiple?: boolean; filters?: { name: string; extensions: string[] }[] }): Promise<string | string[] | null> {
  if (!isTauri()) return null
  try {
    const { open } = await import('@tauri-apps/plugin-dialog')
    return await open(options as Parameters<typeof open>[0]) as string | string[] | null
  } catch {
    return null
  }
}

export async function saveFileDialog(options?: { defaultPath?: string; filters?: { name: string; extensions: string[] }[] }): Promise<string | null> {
  if (!isTauri()) return null
  try {
    const { save } = await import('@tauri-apps/plugin-dialog')
    return await save(options as Parameters<typeof save>[0]) as string | null
  } catch {
    return null
  }
}

export { isTauri }