import { afterEach, describe, expect, it } from 'vitest'

import { generateId } from '@/lib/utils'

const originalCryptoDescriptor = Object.getOwnPropertyDescriptor(globalThis, 'crypto')
const originalMsCryptoDescriptor = Object.getOwnPropertyDescriptor(globalThis, 'msCrypto')

function restoreCryptoDescriptors() {
  if (originalCryptoDescriptor) {
    Object.defineProperty(globalThis, 'crypto', originalCryptoDescriptor)
  } else {
    Reflect.deleteProperty(globalThis, 'crypto')
  }
  if (originalMsCryptoDescriptor) {
    Object.defineProperty(globalThis, 'msCrypto', originalMsCryptoDescriptor)
  } else {
    Reflect.deleteProperty(globalThis, 'msCrypto')
  }
}

afterEach(() => {
  restoreCryptoDescriptors()
})

describe('generateId', () => {
  it('uses crypto.randomUUID when available', () => {
    Object.defineProperty(globalThis, 'crypto', {
      value: {
        randomUUID: () => 'uuid-fixed',
      },
      configurable: true,
    })

    expect(generateId()).toBe('uuid-fixed')
  })

  it('falls back to getRandomValues UUID when randomUUID is absent', () => {
    Object.defineProperty(globalThis, 'crypto', {
      value: {
        getRandomValues: (array: Uint8Array) => {
          for (let i = 0; i < array.length; i++) {
            array[i] = i
          }
          return array
        },
      },
      configurable: true,
    })

    const id = generateId()
    expect(id).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/)
  })

  it('falls back to time/random id when crypto API is missing', () => {
    Object.defineProperty(globalThis, 'crypto', {
      value: undefined,
      configurable: true,
    })

    const id1 = generateId()
    const id2 = generateId()
    expect(id1.startsWith('id-')).toBe(true)
    expect(id2.startsWith('id-')).toBe(true)
    expect(id1).not.toBe(id2)
  })
})
