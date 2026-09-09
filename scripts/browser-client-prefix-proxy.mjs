// Test-only path-prefix proxy. This is not installed or run by the product.
import { createServer, request } from 'node:http'
import { connect } from 'node:net'

export async function startPrefixProxy(backendOrigin, prefix) {
  const backend = new URL(backendOrigin)
  const sockets = new Set()
  const escapedPaths = []
  const pathFor = req => req.url === prefix ? '/' : req.url.startsWith(`${prefix}/`) ? req.url.slice(prefix.length) : null
  const headersFor = req => ({ ...req.headers, 'x-forwarded-prefix': prefix })
  const server = createServer((req, res) => {
    const path = pathFor(req)
    if (path === null) {
      escapedPaths.push(new URL(req.url, backendOrigin).pathname)
      res.writeHead(404).end('Outside the test prefix')
      return
    }
    const upstream = request(new URL(path, backendOrigin), { method: req.method, headers: headersFor(req) }, response => {
      res.writeHead(response.statusCode, response.headers)
      response.pipe(res)
    })
    upstream.on('error', () => { if (!res.headersSent) res.writeHead(502); res.end() })
    req.pipe(upstream)
  })
  server.on('connection', socket => {
    sockets.add(socket)
    socket.on('close', () => sockets.delete(socket))
  })
  server.on('upgrade', (req, socket, head) => {
    const path = pathFor(req)
    if (path === null) { socket.destroy(); return }
    const upstream = connect(Number(backend.port), backend.hostname, () => {
      const headers = Object.entries(headersFor(req)).map(([name, value]) => `${name}: ${value}`).join('\r\n')
      upstream.write(`${req.method} ${path} HTTP/1.1\r\n${headers}\r\n\r\n`)
      if (head.length) upstream.write(head)
      socket.pipe(upstream)
      upstream.pipe(socket)
    })
    sockets.add(upstream)
    upstream.on('close', () => { sockets.delete(upstream); socket.destroy() })
    upstream.on('error', () => socket.destroy())
    socket.on('close', () => upstream.destroy())
    socket.on('error', () => upstream.destroy())
  })
  await new Promise((resolve, reject) => {
    server.once('error', reject)
    server.listen(0, '127.0.0.1', resolve)
  })
  return {
    origin: `http://127.0.0.1:${server.address().port}`,
    escapedPaths,
    close: async () => {
      for (const socket of sockets) socket.destroy()
      await new Promise(resolve => server.close(resolve))
    }
  }
}
