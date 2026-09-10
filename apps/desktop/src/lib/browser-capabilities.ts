/** True only for the integrated browser adapter, never for a partial native bridge. */
export function isBrowserClient(): boolean {
  return window.hermesDesktop?.browserClient === true
}

/** A native control is available only when its actual bridge method is present. */
export function hasDesktopMethod(method: keyof Window['hermesDesktop']): boolean {
  return typeof window.hermesDesktop?.[method] === 'function'
}

/** Native object capabilities (such as HUD windowing) need an actual bridge object. */
export function hasDesktopCapability(capability: keyof Window['hermesDesktop']): boolean {
  return Boolean(window.hermesDesktop?.[capability])
}