import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { group, removePane, split } from '@/components/pane-shell/tree/model'

const TREE_KEY = 'hermes.desktop.layoutTree.v2'
const PANE_SHARE_KEY = 'hermes.desktop.paneShare.v1'

const terminalTree = split(
  'row',
  [
    group(['sessions'], { id: 'grp-sessions' }),
    group(['workspace'], { id: 'grp-main' }),
    split(
      'column',
      [
        split('row', [group(['review'], { id: 'grp-review' }), group(['files'], { id: 'grp-files' })], [1, 1.2], 'spl-rail'),
        group(['terminal'], { id: 'grp-terminal' })
      ],
      [1.6, 1],
      'spl-right'
    )
  ],
  [1, 3.4, 1.25],
  'spl-root'
)

const terminalFreeTree = removePane(terminalTree, 'terminal')!

async function loadBrowserLayout(tree: object) {
  window.localStorage.setItem(TREE_KEY, JSON.stringify(tree))
  window.hermesDesktop = { browserClient: true } as Window['hermesDesktop']

  const storage = await import('@/lib/storage')
  const events: { key: string; op: string }[] = []
  const unsubscribe = storage.onPersistenceEvent(event => events.push(event))
  const layout = await import('@/components/pane-shell/tree/store')
  const { registry } = await import('@/contrib/registry')

  registry.register({
    id: 'terminal',
    area: 'panes',
    title: 'terminal',
    when: () => Boolean(window.hermesDesktop?.terminal),
    render: () => null
  })
  unsubscribe()

  return { events, layout, registry }
}

describe('browser saved terminal layouts', () => {
  const initialDesktop = window.hermesDesktop

  beforeEach(() => {
    window.localStorage.clear()
    vi.resetModules()
  })

  afterEach(() => {
    window.hermesDesktop = initialDesktop
    vi.resetModules()
  })

  it('loads a terminal-free saved layout without writing layout or pane-share storage', async () => {
    const saved = JSON.stringify(terminalFreeTree)
    const { events, layout } = await loadBrowserLayout(terminalFreeTree)

    expect(window.localStorage.getItem(TREE_KEY)).toBe(saved)
    expect(window.localStorage.getItem(PANE_SHARE_KEY)).toBeNull()
    expect(layout.$layoutTree.get()).toEqual(terminalFreeTree)
    expect(events.filter(event => (event.key === TREE_KEY || event.key === PANE_SHARE_KEY) && event.op !== 'read')).toEqual([])
  })

  it('keeps terminal nodes and weights in storage while registry rendering omits the unsupported terminal', async () => {
    const saved = JSON.stringify(terminalTree)
    const { events, layout, registry } = await loadBrowserLayout(terminalTree)

    expect(window.localStorage.getItem(TREE_KEY)).toBe(saved)
    expect(layout.$layoutTree.get()).toEqual(terminalTree)
    expect(registry.getArea('panes')).not.toContainEqual(expect.objectContaining({ id: 'terminal' }))
    expect(events.filter(event => (event.key === TREE_KEY || event.key === PANE_SHARE_KEY) && event.op !== 'read')).toEqual([])
  })
})
