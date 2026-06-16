import { describe, expect, it } from 'vitest'
import { buildGeneralSettingsPayload } from './GeneralSettings'

describe('buildGeneralSettingsPayload', () => {
  it('builds a payload with UI/locale fields', () => {
    const payload = buildGeneralSettingsPayload({
      language: 'en',
      timezone: 'UTC',
      dateFormat: 'YYYY-MM-DD',
      sessionTimeout: 60,
    })

    expect(payload).toEqual({
      language: 'en',
      timezone: 'UTC',
      date_format: 'YYYY-MM-DD',
      session_timeout: 60,
    })
  })
})
