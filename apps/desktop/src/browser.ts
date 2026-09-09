import { installBrowserBridge } from './bridge/browser-bridge'

// The renderer's module-level stores must see the adapter on first evaluation.
installBrowserBridge()
void import('./main')
