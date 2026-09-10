// Built renderer against a real, isolated backend. No production state or model calls.
import assert from 'node:assert/strict'
import { spawn, spawnSync } from 'node:child_process'
import { randomBytes } from 'node:crypto'
import { cp, mkdtemp, mkdir, readFile, stat, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { resolve } from 'node:path'
import { setTimeout as delay } from 'node:timers/promises'
import { chromium } from 'playwright'
import { startPrefixProxy } from './browser-client-prefix-proxy.mjs'

const root = resolve(import.meta.dirname, '..')
const home = await mkdtemp(`${tmpdir()}/hermes-browser-smoke-`)
const gated = process.env.BROWSER_SMOKE_AUTH !== 'token'
const prefix = process.env.BROWSER_SMOKE_PREFIX || ''
assert(!prefix || /^\/[a-zA-Z0-9_-]+$/.test(prefix), 'Use a simple test prefix')
const variant = `${gated ? 'cookie' : 'token'}${prefix ? '-prefix' : ''}`
const output = process.env.BROWSER_SMOKE_OUTPUT || resolve(root, '.hermes/browser-smoke', variant)
await mkdir(output, { recursive: true })
const port = process.env.BROWSER_SMOKE_PORT || '19119'
const address = process.env.BROWSER_SMOKE_HOST || '127.0.0.1'
const backendOrigin = `http://${address}:${port}`
const password = randomBytes(24).toString('hex')
await mkdir(resolve(home, 'package/hermes_cli'), { recursive: true })
await cp(resolve(root, 'apps/desktop/dist'), resolve(home, 'package/hermes_cli/browser_dist'), { recursive: true })
const env = {
  PATH: process.env.PATH, HOME: home, HERMES_HOME: `${home}/.hermes`,
  PYTHONDONTWRITEBYTECODE: '1', PYTHONUNBUFFERED: '1', HERMES_WEB_CLIENT_ENABLED: 'true',
  ...(gated ? {
    HERMES_DASHBOARD_BASIC_AUTH_USERNAME: 'browser-smoke',
    HERMES_DASHBOARD_BASIC_AUTH_PASSWORD: password,
    HERMES_DASHBOARD_BASIC_AUTH_SECRET: randomBytes(32).toString('hex')
  } : {})
}
const python = process.env.BROWSER_SMOKE_PYTHON || resolve(root, '.venv/bin/python')
const seeded = spawnSync(python, ['-c', `
from hermes_constants import get_hermes_home
from hermes_state import SessionDB
get_hermes_home().mkdir(parents=True, exist_ok=True)
db = SessionDB()
db.create_session('browser-smoke-fixture', 'desktop')
db.set_session_title('browser-smoke-fixture', 'Browser smoke fixture')
db.append_message('browser-smoke-fixture', 'user', 'Seeded fixture for browser verification; no model call.')
image = get_hermes_home() / 'browser-smoke-image.png'
image.write_bytes(bytes.fromhex('89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d49444154789c6360f8cfc0000004010100c6a9f0f00000000049454e44ae426082'))
db.close()
`], { cwd: root, env, encoding: 'utf8' })
assert.equal(seeded.status, 0, seeded.stderr)
const server = spawn('sh', [
  '-c', '. "$1"; hermes_select_dashboard_ui "$2" || exit $?; shift 2; exec "$@"',
  'browser-smoke', resolve(root, 'docker/dashboard-ui.sh'), resolve(home, 'package'),
  python, '-c', 'from hermes_cli.main import main; main()',
  'dashboard', '--host', gated ? '0.0.0.0' : '127.0.0.1', '--port', port, '--no-open'
], { cwd: root, env })
let logs = ''
for (const stream of [server.stdout, server.stderr]) stream.on('data', data => { logs += data })
let browser
let proxy
let browserEvidence
const errors = []
const checks = []
const workingSockets = new Set()
const rpcTrace = []
try {
  let ready = false
  for (let i = 0; i < 120; i++) {
    if (server.exitCode !== null) throw new Error(`Backend exited ${server.exitCode}: ${logs.slice(-6000)}`)
    try {
      const response = await fetch(`${backendOrigin}/`)
      if (response.status === 200) { ready = true; break }
    } catch {}
    await delay(500)
  }
  assert(ready, 'Backend did not become ready')
  if (prefix) proxy = await startPrefixProxy(backendOrigin, prefix)
  const origin = proxy?.origin || backendOrigin
  const appUrl = `${origin}${prefix}`
  browser = await chromium.launch({
    executablePath: process.env.BROWSER_SMOKE_CHROMIUM,
    args: [`--proxy-bypass-list=${address};127.0.0.1`]
  })
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } })
  const page = await context.newPage()
  page.on('pageerror', error => errors.push(String(error)))
  page.on('websocket', socket => {
    const requests = new Map()
    socket.on('framesent', ({ payload }) => {
      const message = JSON.parse(String(payload))
      if (message.id != null && message.method) requests.set(message.id, message.method)
    })
    socket.on('framereceived', ({ payload }) => {
      const message = JSON.parse(String(payload))
      rpcTrace.push({ method: requests.get(message.id), success: Boolean(message.result), errorCode: message.error?.code })
      if (requests.get(message.id) === 'profiles.list' && message.result) workingSockets.add(socket)
    })
  })
  await page.goto(`${appUrl}/`)
  const denied = await context.request.get(`${appUrl}/api/config`)
  assert.equal(denied.status(), 401)
  checks.push('private REST rejected without cookie or session-token header')
  if (gated) {
    assert.equal(new URL(page.url()).pathname, `${prefix}/login`)
    checks.push('unauthenticated HTML reached the existing login page')
    const username = page.locator('input[name="username"], input[autocomplete="username"]')
    await username.fill('browser-smoke')
    await page.locator('input[type="password"]').fill('wrong-test-password')
    const rejected = page.waitForResponse(response => new URL(response.url()).pathname === `${prefix}/auth/password-login`)
    await page.locator('button[type="submit"]').click()
    assert.equal((await rejected).status(), 401)
    assert.equal(new URL(page.url()).pathname, `${prefix}/login`)
    checks.push('wrong password rejected by the real backend')
    await page.locator('input[type="password"]').fill(password)
    await page.locator('button[type="submit"]').click()
    await page.waitForURL(url => url.pathname === `${prefix}/` || url.pathname === prefix, { timeout: 20000 })
    assert((await context.cookies()).some(c => c.httpOnly), 'Login must establish an HttpOnly cookie')
    checks.push('real login returned to the selected frontend with an HttpOnly cookie')
  }
  const deferProvider = page.getByText("I'll choose a provider later", { exact: true })
  await deferProvider.waitFor({ timeout: 15000 })
  await deferProvider.click()
  assert.equal(await page.evaluate(() => window.__HERMES_AUTH_REQUIRED__), gated)
  assert.equal(await page.evaluate(() => window.__HERMES_BASE_PATH__), prefix)
  await page.getByText('Browser smoke fixture', { exact: true }).first().waitFor({ timeout: 45000 })
  for (let i = 0; i < 100 && workingSockets.size < 1; i++) await delay(100)
  assert(workingSockets.size >= 1, 'The real WebSocket must return a profile response')
  checks.push('persisted session displayed and authenticated JSON-RPC returned profiles')
  await page.getByRole('button', { name: 'Add context', exact: true }).click()
  const imagesMenuItem = page.getByRole('menuitem', { name: /Images/ })
  await imagesMenuItem.waitFor({ timeout: 15000 })
  const pickerOpened = page.waitForEvent('filechooser')
  await imagesMenuItem.click()
  const picker = await pickerOpened
  assert.equal(picker.isMultiple(), false, 'Image picker must keep its requested single-file policy')
  assert.match(await (await picker.element()).getAttribute('accept') || '', /\.png.*\.jpg.*\.jpeg/, 'Image picker must expose image filters')
  const uploadedImage = page.waitForResponse(response => new URL(response.url()).pathname === `${prefix}/api/files/upload-stream`)
  await picker.setFiles({
    buffer: Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    mimeType: 'image/png', name: 'browser-picker.png'
  })
  const imageUploadResponse = await uploadedImage
  assert.equal(imageUploadResponse.status(), 200, 'Browser picker upload must reach the authenticated gateway')
  const confirmedUpload = await imageUploadResponse.json()
  assert.equal(typeof confirmedUpload.path, 'string', 'Gateway must confirm an uploaded path')
  assert(confirmedUpload.path.startsWith('/'), 'Gateway-confirmed upload path must be absolute')
  await page.getByText('browser-picker.png', { exact: true }).waitFor({ timeout: 15000 })
  checks.push('rendered image picker enforced filters, uploaded through the authenticated gateway and attached its confirmed path')
  await page.keyboard.press('Control+K')
  await page.getByRole('dialog').waitFor({ timeout: 15000 })
  assert.equal(await page.getByText('Toggle terminal', { exact: true }).count(), 0, 'Browser palette must not offer a terminal without a terminal bridge')
  await page.keyboard.press('Escape')
  await page.keyboard.press('Control+Shift+`')
  await page.keyboard.press('Control+F')
  await page.getByRole('searchbox', { name: 'Find in page' }).waitFor({ timeout: 15000 })
  await page.keyboard.press('Escape')
  checks.push('browser terminal palette/keybind paths were unavailable while the renderer FindBar shortcut remained available')
  const clipboardWrites = await page.evaluate(async () => {
    let writes = 0
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText: async () => { writes += 1 } }
    })
    await window.hermesDesktop.writeClipboard('browser-smoke clipboard')

    return writes
  })
  assert.equal(clipboardWrites, 1, 'Browser bridge clipboard writes must not recurse through the Electron shim')
  checks.push('browser bridge clipboard wrote once through the emitted browser client')
  const protectedImage = `${home}/.hermes/browser-smoke-image.png`
  const [download] = await Promise.all([
    page.waitForEvent('download'),
    page.evaluate(path => window.hermesDesktop.saveImageFromUrl(`/api/files/download?path=${encodeURIComponent(path)}`), protectedImage)
  ])
  const downloadPath = `${output}/browser-download.png`
  await download.saveAs(downloadPath)
  const downloaded = await readFile(downloadPath)
  assert((await stat(downloadPath)).size > 8, 'Browser image download must emit bytes')
  assert.deepEqual(downloaded.subarray(0, 8), Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]), 'Browser image download must not save an HTML fallback')
  checks.push('browser image-save bridge downloaded the authenticated PNG fixture with a verified signature')
  assert.equal(errors.length, 0, errors.join('\n'))
  const browserTerminalLayout = {
    type: 'split', id: 'spl-root', orientation: 'row', weights: [1, 3.4, 1.25], children: [
      { type: 'group', id: 'grp-sessions', panes: ['sessions'], active: 'sessions' },
      { type: 'group', id: 'grp-main', panes: ['workspace'], active: 'workspace' },
      {
        type: 'split', id: 'spl-right', orientation: 'column', weights: [1.6, 1], children: [
          {
            type: 'split', id: 'spl-rail', orientation: 'row', weights: [1, 1.2], children: [
              { type: 'group', id: 'grp-review', panes: ['review'], active: 'review' },
              { type: 'group', id: 'grp-files', panes: ['files'], active: 'files' }
            ]
          },
          { type: 'group', id: 'grp-terminal', panes: ['terminal'], active: 'terminal' }
        ]
      }
    ]
  }
  const savedBrowserTerminalLayout = JSON.stringify(browserTerminalLayout)
  await page.evaluate(savedLayout => {
    localStorage.setItem('hermes.desktop.layoutTree.v2', savedLayout)
    localStorage.setItem('hermes.desktop.statusbarHidden.v2', '[]')
  }, savedBrowserTerminalLayout)
  await page.reload()
  await page.getByText('Browser smoke fixture', { exact: true }).first().waitFor({ timeout: 45000 })
  const persistedBrowserLayout = await page.evaluate(() => JSON.parse(localStorage.getItem('hermes.desktop.layoutTree.v2') || 'null'))
  assert.deepEqual(
    persistedBrowserLayout?.weights,
    browserTerminalLayout.weights,
    'Browser boot must preserve saved split weights'
  )
  assert.deepEqual(
    persistedBrowserLayout?.children?.[2],
    browserTerminalLayout.children[2],
    'Browser boot must preserve the saved terminal zone and placement rather than cleaning it up'
  )
  assert((await page.locator('body').innerText()).trim(), 'Browser boot with a saved terminal layout must retain usable content')
  assert.equal(
    await page.locator('[data-slot="statusbar"] svg.lucide-terminal').count(),
    0,
    'Browser statusbar must not advertise a terminal without a terminal bridge'
  )
  checks.push('browser boot preserved saved terminal layout while rendering usable content and no terminal statusbar control')
  const previousSocketCount = workingSockets.size
  await page.reload()
  await page.getByText('Browser smoke fixture', { exact: true }).first().waitFor({ timeout: 45000 })
  for (let i = 0; i < 100 && workingSockets.size <= previousSocketCount; i++) await delay(100)
  assert(workingSockets.size > previousSocketCount, 'Reload must reconnect the real WebSocket')
  checks.push('reload reconnected and redisplayed the persisted session')
  assert.equal(errors.length, 0, errors.join('\n'))
  assert(await page.evaluate(() => {
    const token = window.__HERMES_SESSION_TOKEN__
    return !token || !JSON.stringify({ ...localStorage, ...sessionStorage }).includes(token)
  }), 'The injected token must not be persisted')
  if (proxy) {
    assert.deepEqual(proxy.escapedPaths.filter(path => path !== '/favicon.ico'), [], 'Requests escaped the prefix')
    checks.push('renderer assets, API and WebSocket requests stayed beneath the prefix')
  }
  await page.evaluate(async () => {
    await document.fonts.ready
    await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))
  })
  const capabilities = page.getByRole('button', { name: 'Capabilities', exact: true })
  await capabilities.waitFor({ timeout: 15000 })
  const beforeHover = await capabilities.evaluate(control => {
    const rect = control.getBoundingClientRect()
    const style = getComputedStyle(control)
    return {
      backgroundColor: style.backgroundColor,
      cursor: style.cursor,
      pointerEvents: style.pointerEvents,
      rect: { x: rect.x, y: rect.y, width: rect.width, height: rect.height }
    }
  })
  assert(beforeHover.rect.width > 0 && beforeHover.rect.height > 0, 'Capabilities control must have rendered geometry')
  assert.notEqual(beforeHover.pointerEvents, 'none', 'Capabilities control must accept pointer input')
  await capabilities.hover()
  const afterHover = await capabilities.evaluate(control => ({
    backgroundColor: getComputedStyle(control).backgroundColor,
    hovered: control.matches(':hover')
  }))
  const stylesheetUrls = await page.evaluate(() =>
    [...document.styleSheets].map(sheet => sheet.href).filter(href => /\/assets\/.*\.css(?:$|\?)/.test(href))
  )
  assert(stylesheetUrls.length > 0, 'The emitted renderer stylesheet must be loaded')
  assert(afterHover.hovered, 'Capabilities control must receive an effective hover state')
  assert.notEqual(afterHover.backgroundColor, beforeHover.backgroundColor, 'The emitted hover stylesheet must affect the control')
  const skillsRoute = '/skills'
  await Promise.all([
    page.waitForURL(url => url.hash === `#${skillsRoute}`, { timeout: 15000 }),
    capabilities.click()
  ])
  await page.getByText('Skills', { exact: true }).first().waitFor({ timeout: 15000 })
  browserEvidence = {
    control: 'Capabilities sidebar navigation',
    emittedStylesheetUrls: stylesheetUrls,
    pointer: await page.evaluate(() => ({
      anyFine: matchMedia('(any-pointer: fine)').matches,
      hover: matchMedia('(hover: hover)').matches
    })),
    beforeHover,
    afterHover,
    transition: { expectedHashRoute: `#${skillsRoute}`, observedHashRoute: new URL(page.url()).hash }
  }
  assert(browserEvidence.pointer.anyFine && browserEvidence.pointer.hover, 'Browser context must expose effective fine-pointer hover input')
  assert.equal(browserEvidence.transition.observedHashRoute, `#${skillsRoute}`, 'Capabilities control must navigate to its production route')
  checks.push('emitted stylesheet applied to rendered pointer-hover control with geometry recorded')
  checks.push('Capabilities production control received pointer hover and navigated to /skills')
  await writeFile(`${output}/page.txt`, await page.locator('body').innerText())
  await page.screenshot({ path: `${output}/desktop.png`, fullPage: true })
  if (gated) {
    await page.evaluate(() => window.hermesDesktop.oauthLogoutConnectionConfig())
    await page.waitForURL(url => url.pathname === `${prefix}/login`, { timeout: 15000 })
    assert.equal((await page.request.get(`${appUrl}/api/config`)).status(), 401)
    checks.push('logout returned to login and revoked private API access')
  }
  await writeFile(`${output}/browser-observations.json`, JSON.stringify(browserEvidence, null, 2))
  await writeFile(`${output}/result.json`, JSON.stringify({ passed: true, variant, checks, errors, browserEvidence }, null, 2))
  console.log(JSON.stringify({ passed: true, variant, checks, output }))
} catch (error) {
  if (browser) {
    const page = browser.contexts()[0]?.pages()[0]
    if (page) {
      await writeFile(`${output}/page.txt`, await page.locator('body').innerText())
      await page.screenshot({ path: `${output}/failure.png` })
    }
  }
  const failure = { passed: false, variant, checks, errors, browserEvidence, error: String(error), escapedPaths: proxy?.escapedPaths }
  await writeFile(`${output}/result.json`, JSON.stringify(failure, null, 2))
  console.error(JSON.stringify(failure))
  process.exitCode = 1
} finally {
  await browser?.close()
  await proxy?.close()
  server.kill('SIGTERM')
  await Promise.race([new Promise(resolve => server.on('exit', resolve)), delay(10000)])
  if (server.exitCode === null) server.kill('SIGKILL')
  const safe = logs.replace(/([?&](?:ticket|token)=)[^\s"&]+/g, '$1[REDACTED]').replaceAll(password, '[REDACTED]')
  await writeFile(`${output}/backend.log`, safe)
  await writeFile(`${output}/rpc.json`, JSON.stringify(rpcTrace, null, 2))
}
