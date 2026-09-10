import { cleanup, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, describe, expect, it } from 'vitest'

import { I18nProvider } from '@/i18n'

import { TitlebarControls } from './titlebar-controls'

afterEach(() => {
  cleanup()
  delete (window as Partial<Window>).hermesDesktop
})

function renderControls() {
  return render(
    <MemoryRouter>
      <I18nProvider configClient={null} initialLocale="en">
        <TitlebarControls onOpenSettings={() => undefined} />
      </I18nProvider>
    </MemoryRouter>
  )
}

describe('TitlebarControls browser capability boundary', () => {
  it('does not render the native HUD control when the browser bridge has no HUD capability', () => {
    ;(window as Partial<Window>).hermesDesktop = { browserClient: true } as Window['hermesDesktop']

    renderControls()

    expect(screen.queryByRole('button', { name: 'HUD mode' })).toBeNull()
    expect(screen.getByRole('button', { name: 'Open settings' })).toBeTruthy()
  })
})
