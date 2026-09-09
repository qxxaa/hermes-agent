import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// Discovery lets this same contract run on the baseline with no browser adapter.
// The observable failure is that the browser has no connection capability.
const adapters = import.meta.glob<{ installBrowserBridge: () => void }>('./browser-bridge.ts')

beforeEach(async () => {
  vi.stubGlobal('fetch', vi.fn())
  vi.stubGlobal('__HERMES_AUTH_REQUIRED__', true)
  vi.stubGlobal('__HERMES_BASE_PATH__', '')
  vi.stubGlobal('__HERMES_SESSION_TOKEN__', '')
  delete (window as Partial<Window>).hermesDesktop

  for (const load of Object.values(adapters)) {(await load()).installBrowserBridge()}
})
afterEach(() => vi.unstubAllGlobals())

const jsonResponse = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })

describe('integrated browser adapter', () => {
  it('uses the injected loopback token for REST and sockets without requesting cookies', async () => {
    vi.stubGlobal('__HERMES_AUTH_REQUIRED__', false)
    vi.stubGlobal('__HERMES_SESSION_TOKEN__', 'loopback-fixture-token')
    vi.mocked(fetch).mockResolvedValue(jsonResponse({ ok: true }))
    await window.hermesDesktop.api({ path: '/api/config' })
    const options = vi.mocked(fetch).mock.calls[0][1]!
    expect(new Headers(options.headers).get('X-Hermes-Session-Token')).toBe('loopback-fixture-token')
    const connection = await window.hermesDesktop.getConnection()
    expect(connection.authMode).toBe('token')
    const socket = await window.hermesDesktop.getGatewayWsUrl()
    expect(socket).toMatchObject({ ok: true })
    const url = new URL((socket as { wsUrl: string }).wsUrl)
    expect(url.searchParams.get('token')).toBe('loopback-fixture-token')
    expect(url.searchParams.has('ticket')).toBe(false)
    expect(fetch).toHaveBeenCalledTimes(1)
    expect(JSON.stringify(localStorage)).not.toContain('loopback-fixture-token')
  })

  it('never falls back to an injected legacy token in gated mode', async () => {
    vi.stubGlobal('__HERMES_SESSION_TOKEN__', 'ignored-fixture-token')
    vi.mocked(fetch).mockResolvedValue(jsonResponse({ ticket: 'single-use' }))
    const result = await window.hermesDesktop.getGatewayWsUrl()
    expect(result).toMatchObject({ ok: true })
    expect(new Headers(vi.mocked(fetch).mock.calls[0][1]?.headers).has('X-Hermes-Session-Token')).toBe(false)
    expect((result as { wsUrl: string }).wsUrl).not.toContain('ignored-fixture-token')
  })

  it('fails closed when ungated bootstrap omitted its required token', async () => {
    vi.stubGlobal('__HERMES_AUTH_REQUIRED__', false)
    await expect(window.hermesDesktop.api({ path: '/api/config' })).rejects.toThrow(/token/i)
    expect(fetch).not.toHaveBeenCalled()
    expect(await window.hermesDesktop.getGatewayWsUrl()).toMatchObject({ ok: false, needsOauthLogin: false })
  })

  it.each(['/hermes', '/hermes/'])('keeps REST, connection URLs and fresh tickets beneath %s', async prefix => {
    vi.stubGlobal('__HERMES_BASE_PATH__', prefix)
    vi.mocked(fetch).mockImplementation(async () => jsonResponse({ ticket: 'prefixed-fixture-ticket' }))
    const connection = await window.hermesDesktop.getConnection('research')
    expect(connection.baseUrl).toBe(`${window.location.origin}/hermes`)
    await window.hermesDesktop.api({ path: '/api/config', profile: 'research' })
    const apiUrl = new URL(String(vi.mocked(fetch).mock.calls[0][0]))
    expect(apiUrl.pathname).toBe('/hermes/api/config')
    expect(apiUrl.searchParams.get('profile')).toBe('research')
    const result = await window.hermesDesktop.getGatewayWsUrl()
    expect(new URL((result as { wsUrl: string }).wsUrl).pathname).toBe('/hermes/api/ws')
    expect(new URL(String(vi.mocked(fetch).mock.calls[1][0])).pathname).toBe('/hermes/api/auth/ws-ticket')
  })

  it('retains prefix confinement when rejecting API traversal', async () => {
    vi.stubGlobal('__HERMES_BASE_PATH__', '/hermes')
    await expect(window.hermesDesktop.api({ path: '/api/../../elsewhere' })).rejects.toThrow()
    expect(fetch).not.toHaveBeenCalled()
  })

  it('connects to its own origin without reading saved Mobile credentials', async () => {
    localStorage.setItem('hermes-mobile.connection.v1', 'unrelated legacy data')
    expect(window.hermesDesktop?.getConnection).toBeTypeOf('function')
    const connection = await window.hermesDesktop.getConnection('research')
    expect(connection).toMatchObject({
      baseUrl: window.location.origin, mode: 'remote', authMode: 'oauth', token: '',
      profile: 'research', sharedPrimary: true
    })
    expect(localStorage.getItem('hermes-mobile.connection.v1')).toBe('unrelated legacy data')
    expect(fetch).not.toHaveBeenCalled()
  })

  it('mints a fresh ticket for every WebSocket dial', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse({ ticket: 'one-use-a' }))
      .mockResolvedValueOnce(jsonResponse({ ticket: 'one-use-b' }))
    expect(window.hermesDesktop?.getGatewayWsUrl).toBeTypeOf('function')

    for (const ticket of ['one-use-a', 'one-use-b']) {
      const result = await window.hermesDesktop.getGatewayWsUrl('research')
      expect(result).toMatchObject({ ok: true })
      const url = new URL((result as { wsUrl: string }).wsUrl)
      expect(url.host).toBe(window.location.host)
      expect(url.pathname).toBe('/api/ws')
      expect(url.searchParams.get('ticket')).toBe(ticket)
      expect(url.searchParams.has('token')).toBe(false)
    }

    expect(fetch).toHaveBeenCalledTimes(2)
    expect(fetch).toHaveBeenCalledWith(expect.stringContaining('/api/auth/ws-ticket'),
      expect.objectContaining({ method: 'POST', credentials: 'same-origin' }))
  })

  it.each([401, 403, 500])('classifies HTTP %s without inventing a successful connection', async status => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse({ error: 'test rejection' }, status))
    expect(window.hermesDesktop?.getGatewayWsUrl).toBeTypeOf('function')
    expect(await window.hermesDesktop.getGatewayWsUrl()).toMatchObject({
      ok: false, needsOauthLogin: status === 401 || status === 403
    })
  })

  it('keeps network failure distinct from authentication rejection', async () => {
    vi.mocked(fetch).mockRejectedValue(new TypeError('connection unavailable'))
    expect(window.hermesDesktop?.getGatewayWsUrl).toBeTypeOf('function')
    expect(await window.hermesDesktop.getGatewayWsUrl()).toMatchObject({ ok: false, needsOauthLogin: false })
  })

  it('scopes REST to the requested profile but preserves explicit endpoint scope', async () => {
    vi.mocked(fetch).mockImplementation(async () => jsonResponse({ ok: true }))
    expect(window.hermesDesktop?.api).toBeTypeOf('function')
    await window.hermesDesktop.api({ path: '/api/config', profile: 'research' })
    expect(new URL(String(vi.mocked(fetch).mock.calls[0][0])).searchParams.get('profile')).toBe('research')
    await window.hermesDesktop.api({ path: '/api/config?profile=default', profile: 'research' })
    expect(new URL(String(vi.mocked(fetch).mock.calls[1][0])).searchParams.get('profile')).toBe('default')
  })

  it('rejects off-origin and non-API REST targets before sending anything', async () => {
    expect(window.hermesDesktop?.api).toBeTypeOf('function')

    for (const path of ['https://elsewhere.invalid/api/config', '//elsewhere.invalid/api/config', '/login']) {
      await expect(window.hermesDesktop.api({ path })).rejects.toThrow()
    }

    expect(fetch).not.toHaveBeenCalled()
  })

  it('preserves HTTP status errors and rejects successful HTML fallbacks', async () => {
    expect(window.hermesDesktop?.api).toBeTypeOf('function')
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse({ error: 'denied' }, 403))
    await expect(window.hermesDesktop.api({ path: '/api/config' })).rejects.toMatchObject({ statusCode: 403 })
    vi.mocked(fetch).mockResolvedValueOnce(new Response('<!doctype html><html></html>'))
    await expect(window.hermesDesktop.api({ path: '/api/config' })).rejects.toThrow('Expected JSON')
  })

  it('does not advertise native installers, windows or connection mutation', () => {
    expect(window.hermesDesktop).toBeDefined()
    expect(window.hermesDesktop.repairBootstrap).toBeUndefined()
    expect(window.hermesDesktop.openWindow).toBeUndefined()
    expect(window.hermesDesktop.applyConnectionConfig).toBeUndefined()
    expect(window.hermesDesktop.connections).toBeUndefined()
  })
})
