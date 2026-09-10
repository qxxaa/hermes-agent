import { afterEach, describe, expect, it, vi } from 'vitest'

import type { BrowserOperationScope } from '@/global'

import { pickBrowserFiles } from './browser-files'

const scope: BrowserOperationScope = { connectionId: 'browser', profile: 'research' }

afterEach(() => {
  document.querySelectorAll('input[type="file"]').forEach(input => input.remove())
})

describe('pickBrowserFiles', () => {
  it('resolves cancellation once and removes the attached input', async () => {
    const uploadFile = vi.fn()
    const result = pickBrowserFiles({ multiple: true }, scope, uploadFile)
    const input = document.querySelector<HTMLInputElement>('input[type="file"]')!

    input.dispatchEvent(new Event('cancel'))

    await expect(result).resolves.toEqual([])
    expect(document.querySelector('input[type="file"]')).toBeNull()
    expect(uploadFile).not.toHaveBeenCalled()
  })

  it('rejects an upload failure instead of misreporting it as picker cancellation', async () => {
    const uploadFile = vi.fn().mockRejectedValue(new Error('gateway upload failed'))
    const result = pickBrowserFiles({ filters: [{ extensions: ['txt'], name: 'Text' }] }, scope, uploadFile)
    const input = document.querySelector<HTMLInputElement>('input[type="file"]')!

    Object.defineProperty(input, 'files', {
      configurable: true,
      value: [new File(['contents'], 'notes.txt', { type: 'text/plain' })]
    })
    input.dispatchEvent(new Event('change'))

    await expect(result).rejects.toThrow('gateway upload failed')
    expect(document.querySelector('input[type="file"]')).toBeNull()
  })
})
