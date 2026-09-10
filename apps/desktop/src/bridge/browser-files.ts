import type { BrowserOperationScope, HermesSelectPathsOptions } from '@/global'

export type BrowserFileUploader = (file: Blob, filename: string, scope: BrowserOperationScope) => Promise<string>

function acceptAttribute(filters: HermesSelectPathsOptions['filters']): string {
  return filters?.flatMap(filter => filter.extensions.map(extension => `.${extension.replace(/^\./, '')}`)).join(',') || ''
}

/** Opens a browser-owned device picker. Selected files remain File objects until
 * the supplied uploader returns the backend-confirmed path expected by Desktop callers. */
export function pickBrowserFiles(
  options: HermesSelectPathsOptions | undefined,
  scope: BrowserOperationScope,
  uploadFile: BrowserFileUploader,
  signal?: AbortSignal
): Promise<string[]> {
  return new Promise((resolve, reject) => {
    const input = document.createElement('input')
    let settled = false

    input.type = 'file'
    input.accept = acceptAttribute(options?.filters)
    input.multiple = Boolean(options?.multiple)
    input.style.display = 'none'

    const cleanup = () => {
      input.removeEventListener('cancel', onCancel)
      input.removeEventListener('change', onChange)
      signal?.removeEventListener('abort', onAbort)
      input.remove()
    }
    const finish = (paths: string[]) => {
      if (settled) {
        return
      }

      settled = true
      cleanup()
      resolve(paths)
    }
    const fail = (error: unknown) => {
      if (settled) {
        return
      }

      settled = true
      cleanup()
      reject(error)
    }
    const onCancel = () => finish([])
    const onAbort = () => finish([])
    const onChange = () => {
      const files = [...(input.files || [])]

      if (files.length === 0) {
        finish([])

        return
      }

      void Promise.all(files.map(file => uploadFile(file, file.name, scope))).then(finish, fail)
    }

    input.addEventListener('cancel', onCancel, { once: true })
    input.addEventListener('change', onChange, { once: true })
    signal?.addEventListener('abort', onAbort, { once: true })
    document.body.appendChild(input)

    if (signal?.aborted) {
      finish([])

      return
    }

    try {
      input.click()
    } catch (error) {
      fail(error)
    }
  })
}
