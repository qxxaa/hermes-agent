const MIME_EXTENSIONS: Record<string, string> = {
  'image/bmp': '.bmp',
  'image/gif': '.gif',
  'image/jpeg': '.jpg',
  'image/png': '.png',
  'image/svg+xml': '.svg',
  'image/webp': '.webp'
}

const KNOWN_IMAGE_EXTENSION_RE = /\.(?:apng|avif|bmp|gif|ico|jpe?g|png|svg|tiff?|webp)$/i

export function imageFilename(src?: string): string {
  if (!src) {return 'image'}

  try {
    return new URL(src, window.location.href).pathname.split('/').filter(Boolean).pop() || 'image'
  } catch {
    return src.split(/[\\/]/).filter(Boolean).pop() || 'image'
  }
}

export function downloadFilename(src: string, mimeType?: string): string {
  const base = imageFilename(src)

  if (KNOWN_IMAGE_EXTENSION_RE.test(base)) {return base}

  const type = String(mimeType || '').split(';')[0].trim().toLowerCase()

  return `${base}${MIME_EXTENSIONS[type] || '.png'}`
}

/** Starts a browser-managed image download. The browser, not Hermes, owns the
 * eventual save location and completion. */
export async function startBrowserImageDownload(src: string, options: { headers?: HeadersInit } = {}): Promise<void> {
  const target = new URL(src, window.location.href)
  const sameOrigin = target.origin === window.location.origin
  const response = await fetch(target.href, {
    ...(sameOrigin && options.headers ? { headers: options.headers } : {}),
    credentials: sameOrigin ? 'same-origin' : 'omit'
  })

  if (!response.ok) {
    throw new Error(`Could not fetch image: ${response.status}`)
  }

  const contentType = response.headers.get('Content-Type') || ''

  if (/^text\/html(?:;|$)/i.test(contentType)) {
    throw new Error('Could not download image: Hermes returned HTML instead of image bytes.')
  }

  const blob = await response.blob()
  const blobUrl = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = blobUrl
  link.download = downloadFilename(src, blob.type)
  link.rel = 'noopener noreferrer'
  document.body.appendChild(link)
  link.click()
  link.remove()
  window.setTimeout(() => URL.revokeObjectURL(blobUrl), 30_000)
}
