import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { writeClipboardText } from '@/components/ui/copy-button'
import { installClipboardShim } from '@/lib/clipboard'

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

  it('reads gateway files through the authenticated same-origin filesystem endpoints', async () => {
    vi.mocked(fetch).mockImplementation(async input => {
      const path = new URL(String(input)).pathname

      if (path === '/api/fs/list') {
        return jsonResponse({ entries: [{ isDirectory: true, name: 'project', path: '/gateway/project' }] })
      }

      if (path === '/api/fs/read-text') {
        return jsonResponse({ byteSize: 5, path: '/gateway/project/note.txt', text: 'hello' })
      }

      return jsonResponse({ dataUrl: 'data:text/plain;base64,aGVsbG8=' })
    })

    await expect(window.hermesDesktop.readDir('/gateway')).resolves.toMatchObject({
      entries: [{ path: '/gateway/project' }]
    })
    await expect(window.hermesDesktop.readFileText('/gateway/project/note.txt')).resolves.toMatchObject({ text: 'hello' })
    await expect(window.hermesDesktop.readFileDataUrl('/gateway/project/note.txt')).resolves.toBe('data:text/plain;base64,aGVsbG8=')
    expect(vi.mocked(fetch).mock.calls.map(([input]) => new URL(String(input)).pathname)).toEqual([
      '/api/fs/list', '/api/fs/read-text', '/api/fs/read-data-url'
    ])
  })

  it('attaches a configured device picker, uploads selected files under its captured profile, and removes it', async () => {
    vi.mocked(fetch).mockImplementation(async input => {
      const path = new URL(String(input)).pathname

      if (path === '/api/files') {
        return jsonResponse({ can_change_path: true, root: '/managed' })
      }

      return jsonResponse({ path: '/managed/uploads/browser/research/batch/notes.txt' })
    })
    const result = window.hermesDesktop.selectPaths({
      filters: [{ extensions: ['txt', 'md'], name: 'Notes' }],
      multiple: true,
      profile: 'research'
    })
    const input = document.querySelector<HTMLInputElement>('input[type="file"]')

    expect(input).not.toBeNull()
    expect(input?.accept).toBe('.txt,.md')
    expect(input?.multiple).toBe(true)
    Object.defineProperty(input!, 'files', {
      configurable: true,
      value: [new File(['contents'], 'notes.txt', { type: 'text/plain' })]
    })
    input!.dispatchEvent(new Event('change'))

    await expect(result).resolves.toEqual(['/managed/uploads/browser/research/batch/notes.txt'])
    expect(document.querySelector('input[type="file"]')).toBeNull()
    expect(vi.mocked(fetch).mock.calls.map(([input]) => new URL(String(input)).pathname)).toEqual([
      '/api/files', '/api/files/upload-stream'
    ])
  })

  it('uploads picker files beneath the server-provided default when managed files are not root-locked', async () => {
    vi.mocked(fetch).mockImplementation(async input => {
      if (new URL(String(input)).pathname === '/api/files') {
        return jsonResponse({ can_change_path: true, path: '/managed-user-home', root: null })
      }

      return jsonResponse({ path: '/managed-user-home/uploads/browser/research/batch/notes.txt' })
    })
    const result = window.hermesDesktop.selectPaths({ profile: 'research' })
    const input = document.querySelector<HTMLInputElement>('input[type="file"]')!

    Object.defineProperty(input, 'files', {
      configurable: true,
      value: [new File(['contents'], 'notes.txt', { type: 'text/plain' })]
    })
    input.dispatchEvent(new Event('change'))

    await expect(result).resolves.toEqual(['/managed-user-home/uploads/browser/research/batch/notes.txt'])
  })

  it('settles a browser picker as cancellation when its live owner aborts before a selected upload finishes', async () => {
    let resolveUpload!: (value: Response) => void
    vi.mocked(fetch).mockImplementation(async input => {
      if (new URL(String(input)).pathname === '/api/files') {
        return jsonResponse({ root: '/managed' })
      }

      return new Promise<Response>(resolve => { resolveUpload = resolve })
    })
    const controller = new AbortController()
    const selectPaths = window.hermesDesktop.selectPaths as (
      options?: Parameters<Window['hermesDesktop']['selectPaths']>[0],
      signal?: AbortSignal
    ) => Promise<string[]>
    const result = selectPaths({ profile: 'research' }, controller.signal)
    const input = document.querySelector<HTMLInputElement>('input[type="file"]')!

    Object.defineProperty(input, 'files', {
      configurable: true,
      value: [new File(['contents'], 'notes.txt', { type: 'text/plain' })]
    })
    input.dispatchEvent(new Event('change'))
    controller.abort()

    await expect(result).resolves.toEqual([])
    expect(document.querySelector('input[type="file"]')).toBeNull()
    resolveUpload(jsonResponse({ path: '/managed/uploads/browser/research/late/notes.txt' }))
    await Promise.resolve()
  })

  it('reports denied clipboard and microphone access without pretending either operation succeeded', async () => {
    Object.assign(navigator, {
      clipboard: {
        readText: vi.fn().mockRejectedValue(new DOMException('denied', 'NotAllowedError')),
        writeText: vi.fn().mockRejectedValue(new DOMException('denied', 'NotAllowedError'))
      },
      mediaDevices: { getUserMedia: vi.fn().mockRejectedValue(new DOMException('denied', 'NotAllowedError')) }
    })

    await expect(window.hermesDesktop.writeClipboard('secret')).resolves.toBe(false)
    await expect(window.hermesDesktop.readClipboard()).rejects.toMatchObject({ name: 'NotAllowedError' })
    await expect(window.hermesDesktop.requestMicrophoneAccess()).resolves.toBe(false)
  })

  it('composes the browser bridge, clipboard shim, and copy helper without recursive writes', async () => {
    const nativeWrite = vi.fn().mockResolvedValue(undefined)
    Object.assign(navigator, { clipboard: { writeText: nativeWrite } })

    installClipboardShim()
    await expect(writeClipboardText('copied from browser')).resolves.toBeUndefined()

    expect(nativeWrite).toHaveBeenCalledOnce()
    expect(nativeWrite).toHaveBeenCalledWith('copied from browser')
  })

  it('requests notification permission only from the explicit test action and preserves routing metadata', async () => {
    let permission: NotificationPermission = 'default'
    const requestPermission = vi.fn().mockImplementation(async () => {
      permission = 'granted'
      return permission
    })
    const notify = vi.fn()
    vi.stubGlobal('Notification', Object.assign(notify, { requestPermission }))
    Object.defineProperty(Notification, 'permission', { configurable: true, get: () => permission })

    await expect(window.hermesDesktop.requestNotificationPermission?.()).resolves.toBe(true)
    await expect(window.hermesDesktop.notify({ body: 'done', kind: 'turnDone', tag: 'session-a', title: 'Hermes' })).resolves.toBe(true)
    expect(requestPermission).toHaveBeenCalledOnce()
    expect(notify).toHaveBeenCalledWith('Hermes', expect.objectContaining({ body: 'done', tag: 'session-a' }))
  })

  it('releases a late wake lock if the preference is disabled before acquisition resolves', async () => {
    let resolveLock!: (lock: { release: ReturnType<typeof vi.fn> }) => void
    const release = vi.fn().mockResolvedValue(undefined)
    Object.assign(navigator, {
      wakeLock: { request: vi.fn(() => new Promise(resolve => { resolveLock = resolve })) }
    })

    window.hermesDesktop.setKeepAwake?.(true)
    window.hermesDesktop.setKeepAwake?.(false)
    resolveLock({ release })
    await Promise.resolve()
    expect(release).toHaveBeenCalledOnce()
  })

  it('releases while hidden, reacquires only after visibility returns, and contains rejected requests', async () => {
    const firstRelease = vi.fn().mockResolvedValue(undefined)
    const secondRelease = vi.fn().mockResolvedValue(undefined)
    const request = vi.fn()
      .mockResolvedValueOnce({ addEventListener: vi.fn(), release: firstRelease })
      .mockRejectedValueOnce(new DOMException('denied', 'NotAllowedError'))
      .mockResolvedValueOnce({ addEventListener: vi.fn(), release: secondRelease })
    Object.assign(navigator, { wakeLock: { request } })
    const visibility = vi.spyOn(document, 'visibilityState', 'get')

    visibility.mockReturnValue('visible')
    window.hermesDesktop.setKeepAwake?.(true)
    await Promise.resolve()
    await Promise.resolve()
    expect(request).toHaveBeenCalledTimes(1)

    visibility.mockReturnValue('hidden')
    document.dispatchEvent(new Event('visibilitychange'))
    await Promise.resolve()
    expect(firstRelease).toHaveBeenCalledOnce()

    visibility.mockReturnValue('visible')
    document.dispatchEvent(new Event('visibilitychange'))
    await Promise.resolve()
    await Promise.resolve()
    expect(request).toHaveBeenCalledTimes(2)

    await Promise.resolve()
    document.dispatchEvent(new Event('visibilitychange'))
    await Promise.resolve()
    await Promise.resolve()
    expect(request).toHaveBeenCalledTimes(3)

    window.dispatchEvent(new Event('pagehide'))
    await Promise.resolve()
    expect(secondRelease).toHaveBeenCalledOnce()

    document.dispatchEvent(new Event('visibilitychange'))
    await Promise.resolve()
    expect(request).toHaveBeenCalledTimes(3)
  })

  it('routes a browser notification body click to its captured session and plugin activation', async () => {
    const focus = vi.fn()
    const activate = vi.fn()
    const notification = vi.fn()
    const focusWindow = vi.spyOn(window, 'focus').mockImplementation(() => {})
    vi.stubGlobal('Notification', Object.assign(notification, { permission: 'granted' }))

    const offFocus = window.hermesDesktop.onFocusSession?.(focus)
    const offActivate = window.hermesDesktop.onNotificationActivate?.(activate)
    await window.hermesDesktop.notify({
      activate: '/index-network/intent/1', body: 'done', icon: 'https://example.com/icon.png', kind: 'plugin', notifyId: 'notice-1', sessionId: 'origin-session', silent: true, tag: 'index-network', title: 'Hermes'
    })
    const instance = notification.mock.instances[0] as Notification
    instance.onclick?.(new Event('click'))

    expect(notification).toHaveBeenCalledWith('Hermes', {
      body: 'done', icon: 'https://example.com/icon.png', silent: true, tag: 'index-network'
    })
    expect(focus).toHaveBeenCalledWith('origin-session')
    expect(activate).toHaveBeenCalledWith({ activate: '/index-network/intent/1', notifyId: 'notice-1', tag: 'index-network' })
    expect(focusWindow).toHaveBeenCalledOnce()
    offFocus?.()
    offActivate?.()
  })

  it('reports browser battery data and removes its listener on disposal', async () => {
    const listeners = new Map<string, EventListener>()
    const battery = Object.assign(new EventTarget(), { charging: false, level: 0.4 })
    const addEventListener = vi.spyOn(battery, 'addEventListener').mockImplementation((type, listener) => {
      listeners.set(type, listener as EventListener)
    })
    const removeEventListener = vi.spyOn(battery, 'removeEventListener')
    const getBattery = vi.fn(function (this: Navigator) {
      if (this !== navigator) {
        throw new Error('getBattery lost its navigator receiver')
      }

      return Promise.resolve(battery)
    })
    Object.assign(navigator, { getBattery })
    const states: boolean[] = []

    expect(await window.hermesDesktop.getOnBattery?.()).toBe(true)
    const dispose = window.hermesDesktop.onBatteryChanged?.(onBattery => states.push(onBattery))
    await Promise.resolve()
    expect(addEventListener).toHaveBeenCalledWith('chargingchange', expect.any(Function))
    battery.charging = true
    listeners.get('chargingchange')?.(new Event('chargingchange'))
    expect(states).toEqual([false])
    dispose?.()
    expect(removeEventListener).toHaveBeenCalledWith('chargingchange', expect.any(Function))
  })

  it('keeps the renderer battery fallback when the browser battery API is unavailable', async () => {
    delete (navigator as { getBattery?: unknown }).getBattery

    expect(await window.hermesDesktop.getOnBattery?.()).toBe(false)
    const dispose = window.hermesDesktop.onBatteryChanged?.(vi.fn())

    expect(() => dispose?.()).not.toThrow()
  })

  it('saves an image with a browser download without calling a gateway upload endpoint', async () => {
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
    const createObjectURL = vi.fn(() => 'blob:fixture')
    const revokeObjectURL = vi.fn()
    vi.stubGlobal('URL', Object.assign(URL, { createObjectURL, revokeObjectURL }))
    vi.mocked(fetch).mockResolvedValue(new Response(new Blob(['image-bytes'], { type: 'image/png' })))

    await expect(window.hermesDesktop.saveImageFromUrl('https://images.example/picture')).resolves.toBe(true)
    expect(click).toHaveBeenCalledOnce()
    expect(vi.mocked(fetch)).toHaveBeenCalledWith('https://images.example/picture', { credentials: 'omit' })
    expect(vi.mocked(fetch).mock.calls.some(([input]) => String(input).includes('/api/fs/upload-image'))).toBe(false)
  })

  it('does not advertise native installers, windows or connection mutation', () => {
    expect(window.hermesDesktop).toBeDefined()
    expect(window.hermesDesktop.repairBootstrap).toBeUndefined()
    expect(window.hermesDesktop.openWindow).toBeUndefined()
    expect(window.hermesDesktop.applyConnectionConfig).toBeUndefined()
    expect(window.hermesDesktop.connections).toBeUndefined()
  })
})
