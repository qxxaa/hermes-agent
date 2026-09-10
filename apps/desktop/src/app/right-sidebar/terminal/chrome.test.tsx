import { cleanup, render } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import { TerminalPaneChrome } from './chrome'

const desktopWindow = window as unknown as { hermesDesktop?: Window['hermesDesktop'] }

afterEach(() => {
  cleanup()
  delete desktopWindow.hermesDesktop
})

describe('TerminalPaneChrome', () => {
  it('retains terminal chrome when the native bridge exposes a terminal capability', () => {
    desktopWindow.hermesDesktop = { terminal: {} } as Window['hermesDesktop']

    const { container } = render(<TerminalPaneChrome />)

    expect(container.childElementCount).toBe(1)
  })

  it('does not render terminal chrome for the browser bridge without a terminal capability', () => {
    desktopWindow.hermesDesktop = { browserClient: true } as Window['hermesDesktop']

    const { container } = render(<TerminalPaneChrome />)

    expect(container.childElementCount).toBe(0)
  })
})