import { cleanup, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { StatusbarControls } from '@/app/shell/statusbar-controls'
import { I18nProvider } from '@/i18n'
import { $statusbarHiddenIds, STATUSBAR_HIDDEN_BY_DEFAULT } from '@/store/statusbar-prefs'

import { useStatusbarItems } from './use-statusbar-items'

vi.mock('@/app/shell/hooks/use-context-breakdown', () => ({
  useContextBreakdown: () => ({ breakdown: null, loading: false })
}))

const initialDesktop = window.hermesDesktop
const requestGateway = <T = unknown,>(_method: string, _params?: Record<string, unknown>): Promise<T> => Promise.resolve(null as T)

function StatusbarUnderTest() {
  const { statusbarItems } = useStatusbarItems({
    agentsOpen: false,
    chatOpen: true,
    commandCenterOpen: false,
    extraLeftItems: [],
    extraRightItems: [],
    gatewayState: 'closed',
    inferenceStatus: null,
    openAgents: () => undefined,
    openCommandCenterSection: () => undefined,
    freshDraftReady: false,
    requestGateway,
    statusSnapshot: null,
    toggleCommandCenter: () => undefined
  })

  return (
    <>
      <output data-testid="terminal-statusbar-item">
        {statusbarItems.some(item => item.id === 'terminal' && !item.hidden) ? 'present' : 'absent'}
      </output>
      <StatusbarControls items={statusbarItems} />
    </>
  )
}

function renderStatusbar() {
  return render(
    <MemoryRouter>
      <I18nProvider configClient={null} initialLocale="en">
        <StatusbarUnderTest />
      </I18nProvider>
    </MemoryRouter>
  )
}

describe('terminal statusbar capability', () => {
  beforeEach(() => {
    $statusbarHiddenIds.set(STATUSBAR_HIDDEN_BY_DEFAULT.filter(id => id !== 'terminal'))
  })

  afterEach(() => {
    cleanup()
    window.hermesDesktop = initialDesktop
  })

  it('does not render the terminal control for a browser bridge without a terminal capability', () => {
    window.hermesDesktop = { browserClient: true } as Window['hermesDesktop']

    renderStatusbar()

    expect(screen.getByTestId('terminal-statusbar-item').textContent).toBe('absent')
  })

  it('renders the terminal control for a native bridge with a terminal capability', () => {
    window.hermesDesktop = { terminal: {} } as Window['hermesDesktop']

    renderStatusbar()

    expect(screen.getByTestId('terminal-statusbar-item').textContent).toBe('present')
  })
})
