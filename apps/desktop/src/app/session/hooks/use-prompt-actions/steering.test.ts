import { describe, expect, it, vi } from 'vitest'

import { createClientSessionState } from '@/lib/chat-runtime'
import { clearQueuedPrompts, getQueuedPrompts } from '@/store/composer-queue'
import { $sessionStates } from '@/store/session-states'

import { dispatchSteering, routeBusyInput, submitBusyPrompt } from './steering'

// These are protocol fixtures, not simulated live-run evidence.
describe('shared steering action', () => {
  it('dispatches a structured operation without changing instruction text', async () => {
    const request = vi.fn().mockResolvedValue({ type: 'exec', output: 'Steer queued' })
    const render = vi.fn()
    const send = vi.fn()
    const text = 'focus on /steer parsing\n  keep this line'

    expect(await dispatchSteering({ request, sessionId: 'original', text, render, send })).toBe(true)
    expect(request).toHaveBeenCalledExactlyOnceWith('command.dispatch', {
      session_id: 'original',
      name: 'steer',
      arg: text
    })
    expect(render).toHaveBeenCalledExactlyOnceWith('Steer queued')
    expect(send).not.toHaveBeenCalled()
  })

  it('uses the existing normal-send fallback only when directed by the backend', async () => {
    const request = vi.fn().mockResolvedValue({ type: 'send', message: 'follow up' })
    const send = vi.fn().mockResolvedValue(true)
    expect(await dispatchSteering({ request, sessionId: 'target', text: 'follow up', render: vi.fn(), send })).toBe(
      true
    )
    expect(send).toHaveBeenCalledExactlyOnceWith('follow up')
  })

  it('does not blindly replay an ambiguous dispatch failure', async () => {
    const request = vi.fn().mockRejectedValue(new Error('connection lost'))
    const send = vi.fn()
    await expect(
      dispatchSteering({ request, sessionId: 'target', text: 'note', render: vi.fn(), send })
    ).rejects.toThrow('connection lost')
    expect(request).toHaveBeenCalledTimes(1)
    expect(send).not.toHaveBeenCalled()
  })

  it('rejects malformed acknowledgements rather than claiming delivery', async () => {
    expect(
      await dispatchSteering({
        request: vi.fn().mockResolvedValue({}),
        sessionId: 'target',
        text: 'note',
        render: vi.fn(),
        send: vi.fn()
      })
    ).toBe(false)
  })
})

describe('busy mode selection', () => {
  it('retains the existing redirect-rejection queue fallback', async () => {
    clearQueuedPrompts('target')
    const request = vi.fn()
    const redirect = vi.fn().mockResolvedValue(false)
    const options = {
      mode: 'interrupt' as const,
      text: 'correction',
      sessionId: 'target',
      storedSessionId: 'target',
      foregroundBusy: true,
      request,
      redirect,
      update: vi.fn(),
      submit: vi.fn()
    }
    expect(await submitBusyPrompt(options)).toBe(true)
    expect(redirect).toHaveBeenCalledExactlyOnceWith('correction')
    expect(getQueuedPrompts('target').map(entry => entry.text)).toEqual(['correction'])
    expect(request).not.toHaveBeenCalled()
    clearQueuedPrompts('target')
  })

  it('uses the existing queue without adding a new transcript notice', async () => {
    clearQueuedPrompts('queue-target')
    $sessionStates.set({ 'queue-target': { ...createClientSessionState(), busy: true } })
    const update = vi.fn()
    const request = vi.fn()
    expect(
      await submitBusyPrompt({
        mode: 'queue',
        text: 'later',
        sessionId: 'queue-target',
        storedSessionId: 'queue-target',
        foregroundBusy: true,
        request,
        update,
        submit: vi.fn(),
        redirect: vi.fn()
      })
    ).toBe(true)
    expect(getQueuedPrompts('queue-target').map(entry => entry.text)).toEqual(['later'])
    expect(update).not.toHaveBeenCalled()
    expect(request).not.toHaveBeenCalled()
    clearQueuedPrompts('queue-target')
    $sessionStates.set({})
  })

  it.each(['steer', 'interrupt', 'queue'] as const)('selects only %s', async mode => {
    const actions = {
      steer: vi.fn().mockResolvedValue(true),
      interrupt: vi.fn().mockResolvedValue(true),
      queue: vi.fn().mockResolvedValue(true)
    }

    expect(await routeBusyInput(mode, 'unchanged', actions)).toBe(true)

    for (const [name, action] of Object.entries(actions)) {
      expect(action).toHaveBeenCalledTimes(name === mode ? 1 : 0)
    }

    expect(actions[mode]).toHaveBeenCalledWith('unchanged')
  })

  it('does not guess interruption before settings load', async () => {
    const actions = { steer: vi.fn(), interrupt: vi.fn(), queue: vi.fn() }
    expect(await routeBusyInput(null, 'keep draft', actions)).toBe(false)
    Object.values(actions).forEach(action => expect(action).not.toHaveBeenCalled())
  })
})
