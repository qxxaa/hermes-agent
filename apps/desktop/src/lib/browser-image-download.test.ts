import { afterEach, describe, expect, it, vi } from 'vitest'

import { startBrowserImageDownload } from './browser-image-download'

afterEach(() => vi.restoreAllMocks())

describe('startBrowserImageDownload', () => {
  it('rejects a successful HTML fallback rather than downloading it as an image', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('<!doctype html><html>login</html>', {
      headers: { 'Content-Type': 'text/html' }, status: 200
    })))

    await expect(startBrowserImageDownload('/protected-image.png')).rejects.toThrow(/HTML/i)
  })

  it('forwards an injected-token header only to same-origin protected image requests', async () => {
    vi.stubGlobal('fetch', vi.fn()
      .mockResolvedValueOnce(new Response(new Blob(['png'], { type: 'image/png' })))
      .mockResolvedValueOnce(new Response(new Blob(['png'], { type: 'image/png' }))))
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
    vi.stubGlobal('URL', Object.assign(URL, { createObjectURL: vi.fn(() => 'blob:image'), revokeObjectURL: vi.fn() }))

    await startBrowserImageDownload('/protected-image.png', { headers: { 'X-Hermes-Session-Token': 'test-token' } })
    await startBrowserImageDownload('https://images.example/image.png', { headers: { 'X-Hermes-Session-Token': 'test-token' } })

    expect(vi.mocked(fetch)).toHaveBeenNthCalledWith(1, expect.stringContaining('/protected-image.png'), {
      credentials: 'same-origin', headers: { 'X-Hermes-Session-Token': 'test-token' }
    })
    expect(vi.mocked(fetch)).toHaveBeenNthCalledWith(2, 'https://images.example/image.png', { credentials: 'omit' })
  })
})
