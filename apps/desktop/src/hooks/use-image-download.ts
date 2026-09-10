import { useCallback, useState } from 'react'

import { useI18n } from '@/i18n'
import { isBrowserClient } from '@/lib/browser-capabilities'
import { imageFilename, startBrowserImageDownload } from '@/lib/browser-image-download'
import { notify, notifyError } from '@/store/notifications'

function isMissingIpcHandler(error: unknown): boolean {
  const message = error instanceof Error ? error.message : typeof error === 'string' ? error : ''

  return message.includes("No handler registered for 'hermes:saveImageFromUrl'")
}

/** Save an image to disk via the desktop IPC bridge, falling back to a browser
 * download when the handler is unavailable (older shell / web preview). */
export function useImageDownload(src?: string) {
  const { t } = useI18n()
  const copy = t.desktop
  const [saving, setSaving] = useState(false)

  const download = useCallback(async () => {
    if (!src || saving) {
      return
    }

    setSaving(true)

    try {
      if (window.hermesDesktop?.saveImageFromUrl) {
        if (await window.hermesDesktop.saveImageFromUrl(src)) {
          notify({
            kind: isBrowserClient() ? 'info' : 'success',
            title: isBrowserClient() ? copy.downloadStarted : copy.imageSaved,
            message: imageFilename(src)
          })
        }

        return
      }

      await startBrowserImageDownload(src)
    } catch (error) {
      if (isMissingIpcHandler(error)) {
        try {
          await startBrowserImageDownload(src)
          notify({ kind: 'info', title: copy.downloadStarted, message: copy.restartToUseSaveImage })
        } catch (fallbackError) {
          notifyError(fallbackError, copy.restartToSaveImages)
        }

        return
      }

      notifyError(error, copy.imageDownloadFailed)
    } finally {
      setSaving(false)
    }
  }, [copy, saving, src])

  return { download, saving }
}
