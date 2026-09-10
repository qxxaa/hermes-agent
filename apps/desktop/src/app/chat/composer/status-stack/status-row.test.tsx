import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { I18nProvider } from '@/i18n'
import type { ComposerStatusItem } from '@/store/composer-status'

import { StatusItemRow } from './status-row'

const { openAgentTerminal } = vi.hoisted(() => ({ openAgentTerminal: vi.fn() }))
const initialDesktop = window.hermesDesktop

vi.mock('@/app/right-sidebar/terminal/terminals', () => ({ openAgentTerminal }))

const background: ComposerStatusItem = {
  id: 'background-1',
  state: 'running',
  title: 'Background task',
  type: 'background'
}

function renderRow() {
  return render(
    <I18nProvider configClient={null} initialLocale="en">
      <StatusItemRow item={background} />
    </I18nProvider>
  )
}

describe('background status-row terminal activation', () => {
  beforeEach(() => {
    openAgentTerminal.mockClear()
  })

  afterEach(() => {
    cleanup()
    window.hermesDesktop = initialDesktop
  })

  it('does not advertise terminal activation without the terminal capability', () => {
    window.hermesDesktop = { browserClient: true } as Window['hermesDesktop']

    renderRow()

    expect(screen.queryByRole('button', { name: /background task/i })).toBeNull()
  })

  it('opens the background terminal from a native-capable row', () => {
    window.hermesDesktop = { terminal: {} } as Window['hermesDesktop']

    renderRow()
    fireEvent.click(screen.getByRole('button', { name: /background task/i }))

    expect(openAgentTerminal).toHaveBeenCalledWith('background-1', 'Background task')
  })
})
