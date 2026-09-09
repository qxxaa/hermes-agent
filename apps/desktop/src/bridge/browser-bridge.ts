// Browser transport adapted from sremes/hermes-mobile at
// d35d93dbbb010ba72fb8183ee3377de2e27c6f33 (MIT), browser-bridge.ts.
// The integrated client honours the existing server's origin, prefix and auth mode.
import { buildHermesWebSocketUrl } from '@hermes/shared'
import type { GatewayWsUrlResult } from '@hermes/shared'

import type { DesktopConnectionConfig, HermesApiRequest, HermesConnection } from '@/global'

import { version } from '../../package.json'

const PROFILE_KEY = 'hermes.browser.active-profile'
const BROWSER_CONNECTION = 'browser'

const unsubscribe = () => () => {}

function authRequired(): boolean {
  // A missing bootstrap flag must not downgrade authentication.
  return window.__HERMES_AUTH_REQUIRED__ !== false
}

function basePath(): string {
  return (window.__HERMES_BASE_PATH__ || '').replace(/\/+$/, '')
}

export function browserServerUrl(path: string): string {
  return new URL(`${basePath()}${path}`, window.location.origin).href
}

function sessionToken(): string {
  if (authRequired()) {return ''}
  const token = window.__HERMES_SESSION_TOKEN__

  if (!token) {throw new Error('Hermes did not supply the local session token. Reload this page.')}

  return token
}

function websocketUrl(authParam?: readonly [string, string]): string {
  return buildHermesWebSocketUrl({ path: '/api/ws', basePath: basePath(), authParam })
}

function checkConnectionId(id?: string | null) {
  if (id && id !== BROWSER_CONNECTION) {throw new Error('This browser client uses its own Hermes server only.')}
}

async function api<T>(request: HermesApiRequest): Promise<T> {
  checkConnectionId(request.connectionId)
  const url = new URL(browserServerUrl(request.path))

  if (!request.path.startsWith('/api/') || url.origin !== window.location.origin || !url.pathname.startsWith(`${basePath()}/api/`)) {
    throw new Error('The browser bridge accepts same-origin API paths only.')
  }

  if (request.profile && !url.searchParams.has('profile')) {url.searchParams.set('profile', request.profile)}
  const headers = new Headers()

  if (!authRequired()) {headers.set('X-Hermes-Session-Token', sessionToken())}

  const init: RequestInit = {
    credentials: 'same-origin', method: request.method || 'GET', headers,
    signal: AbortSignal.timeout(request.timeoutMs ?? 30_000), redirect: 'error'
  }

  if (request.upload) {
    const form = new FormData()
    form.append('file', new Blob([request.upload.bytes], {
      type: request.upload.contentType || 'application/octet-stream'
    }), request.upload.filename)
    init.body = form
  } else if (request.body !== undefined) {
    headers.set('Content-Type', 'application/json')
    init.body = JSON.stringify(request.body)
  }

  const response = await fetch(url.href, init)
  const text = await response.text()

  if (!response.ok) {
    throw Object.assign(new Error(`${response.status}: ${text || response.statusText}`), { statusCode: response.status })
  }

  if (/^\s*<(?:!doctype|html)/i.test(text)) {throw new Error(`Expected JSON from ${url.pathname}, received HTML.`)}

  return text ? JSON.parse(text) as T : null as T
}

async function getConnection(profile?: string | null): Promise<HermesConnection> {
  return {
    baseUrl: browserServerUrl('').replace(/\/$/, ''), authMode: authRequired() ? 'oauth' : 'token', mode: 'remote',
    remoteHost: window.location.host, remoteKind: 'url', source: 'settings',
    profile: profile || undefined, connectionId: BROWSER_CONNECTION,
    sharedPrimary: Boolean(profile), sharedRemote: Boolean(profile), registryScoped: true,
    token: sessionToken(), wsUrl: websocketUrl(authRequired() ? undefined : ['token', sessionToken()]),
    logs: [], isFullscreen: Boolean(document.fullscreenElement), nativeOverlayWidth: 0, windowButtonPosition: null
  }
}

async function getGatewayWsUrl(): Promise<GatewayWsUrlResult> {
  try {
    if (!authRequired()) {return { ok: true, wsUrl: websocketUrl(['token', sessionToken()]) }}
    const body = await api<{ ticket?: unknown }>({ path: '/api/auth/ws-ticket', method: 'POST', timeoutMs: 8_000 })

    if (typeof body?.ticket !== 'string' || !body.ticket) {throw new Error('Hermes did not return a WebSocket ticket.')}

    return { ok: true, wsUrl: websocketUrl(['ticket', body.ticket]) }
  } catch (error) {
    const status = (error as { statusCode?: number })?.statusCode

    return { ok: false, error: error instanceof Error ? error.message : String(error), needsOauthLogin: status === 401 || status === 403 }
  }
}

async function getConnectionConfig(profile?: string | null): Promise<DesktopConnectionConfig> {
  let connected = false

  try {
    if (authRequired()) {
      await api({ path: '/api/auth/me', timeoutMs: 6_000 })
      connected = true
    }
  } catch (error) {
    const status = (error as { statusCode?: number })?.statusCode

    if (status !== 401 && status !== 403) {throw error}
  }

  return {
    envOverride: true, mode: 'remote', profile: profile ?? null,
    remoteAuthMode: authRequired() ? 'oauth' : 'token', remoteOauthConnected: connected, remoteTokenPreview: null,
    remoteTokenSet: !authRequired() && Boolean(sessionToken()), remoteTokenPlainText: false, secureTokenStorage: false,
    remoteUrl: browserServerUrl('').replace(/\/$/, ''), cloudOrg: '', sshHost: '', sshUser: '', sshPort: null,
    sshKeyPath: '', sshRemoteHermesPath: '', sshRemoteProfile: ''
  }
}

const rememberProfile = async (profile: string | null) => {
  if (profile) {localStorage.setItem(PROFILE_KEY, profile)}
  else {localStorage.removeItem(PROFILE_KEY)}

  return { profile }
}

const bridge = {
  browserClient: true,
  api,
  getConnection,
  getConnectionFor: async ({ connectionId, profile }) => {
    checkConnectionId(connectionId)

    return getConnection(profile)
  },
  getGatewayWsUrl,
  getGatewayWsUrlFor: async ({ connectionId }) => {
    checkConnectionId(connectionId)

    return getGatewayWsUrl()
  },
  getProfileRoutes: async profiles => profiles.map(profile => ({
    connectionId: BROWSER_CONNECTION, mode: 'remote' as const, profile, targetProfile: profile
  })),
  getConnectionConfig,
  getBootProgress: async () => ({
    error: null, fakeMode: false, message: 'Connecting to Hermes', phase: 'connecting',
    progress: 20, running: true, timestamp: Date.now()
  }),
  getBootstrapState: async () => ({
    active: false, manifest: null, stages: {}, error: null, log: [], startedAt: null,
    completedAt: null, setupChoice: null, unsupportedPlatform: null
  }),
  getVersion: async () => ({ appVersion: version, electronVersion: '', nodeVersion: '', platform: 'browser', hermesRoot: '' }),
  revalidateConnection: async () => {
    await api({ path: '/api/status', timeoutMs: 8_000 })

    return { ok: true, rebuilt: false }
  },
  // Remote lifecycles are owned by the server; there is no local child to reap.
  touchBackend: async () => ({ ok: true }),
  onBackendExit: unsubscribe,
  onBootProgress: unsubscribe,
  onConnectionApplied: unsubscribe,
  onWindowStateChanged: unsubscribe,
  oauthLoginConnectionConfig: async () => {
    window.location.assign(browserServerUrl('/login'))

    return { ok: true, connected: false, baseUrl: browserServerUrl('').replace(/\/$/, '') }
  },
  oauthLogoutConnectionConfig: async () => {
    // Logout is a server auth route returning a redirect, not a JSON API.
    const response = await fetch(browserServerUrl('/auth/logout'), {
      method: 'POST', credentials: 'same-origin', signal: AbortSignal.timeout(8_000)
    })

    if (!response.ok) {
      throw new Error(`Logout failed (${response.status}).`)
    }

    window.location.assign(browserServerUrl('/login'))

    return { ok: true, connected: false }
  },
  profile: {
    get: async () => ({ profile: localStorage.getItem(PROFILE_KEY) }),
    remember: rememberProfile,
    set: rememberProfile
  },
  sanitizeWorkspaceCwd: async cwd => {
    if (cwd) {return { cwd, sanitized: false }}
    const result = await api<{ cwd: string }>({ path: '/api/fs/default-cwd' })

    return { cwd: result.cwd, sanitized: true }
  },
  openExternal: async url => {
    const target = new URL(url, window.location.origin)

    if (!['http:', 'https:', 'mailto:'].includes(target.protocol)) {throw new Error('Unsupported external URL scheme.')}
    window.open(target.href, '_blank', 'noopener,noreferrer')
  }
} satisfies Partial<Window['hermesDesktop']>

// Omitted capabilities stay absent. Native callers must test the member, not
// merely the bridge object; a successful no-op would conceal an unavailable action.
export function installBrowserBridge(): void {
  if (window.hermesDesktop) {return}
  window.hermesDesktop = bridge as unknown as Window['hermesDesktop']
}
