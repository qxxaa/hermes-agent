# Built-in browser client: Slice 1

## Approved contract

This record supersedes the earlier Phase 1 proposal's port and service precedence sections. Implementation was authorised in conversation; commits, publication, integration into main and deployment remain separate gates.

The existing dashboard service stays on its configured host and port (default 9119). With that service enabled, `HERMES_WEB_CLIENT_ENABLED=true` selects the compiled Desktop frontend instead of the dashboard frontend. Unset/false preserves the existing dashboard. It is a startup selector, not a second service or a live switch. There is no web-client port setting and no internal proxy.

Both modes use the existing login, session cookies, API and WebSocket endpoints. The browser adapter is same-origin, never stores credentials, never creates a second backend, and preserves explicit profile routing. Authentication failure must remain distinct from a network failure. `hermes serve` stays headless.

The browser branch owns common functionality. Mobile-only enhancements belong on a later branch; both are ultimately integrated into main. Slice 1 proves authenticated Desktop boot, session loading and refresh, not full desktop parity. No phone layout, UI Scale, PWA, sharing, input redesign or old optimisation patches are included.

## Provenance

- Agent baseline: `91adf584a4783d600e8f358a4f32ad95e7e0cdde`.
- Browser bridge reference: `sremes/hermes-mobile`, `d35d93dbbb010ba72fb8183ee3377de2e27c6f33`, `apps/desktop/src/bridge/browser-bridge.ts`.
- Desktop and shared sources remain in their upstream paths. Native sources are preserved; the browser build does not run Electron.

## Verification ownership

The implementation is tested locally with the built renderer, isolated backend and test-only credentials/data. No production credentials or configuration are read. Q will perform the final image build and container smoke test; Docker is not accessible from the implementation environment.

Required local checks: browser adapter routing and fresh tickets; auth versus connection failure; existing gateway/profile regressions; renderer build; actual login and persisted session list; refresh; private-route and ticket protection; frontend mode selection and missing-asset failure.

Required image checks: both selector states on the same configured port; disabled dashboard remains down regardless of selector; prebuilt assets without startup compilation; auth and persisted data survive restart; normal server failures restart under s6; invalid selector/missing assets fail clearly without restart storms. Device acceptance remains Q's test, not an inferred result.

## Container configuration

The new setting is `HERMES_WEB_CLIENT_ENABLED=true`. It selects the browser UI only when the existing dashboard service is enabled. Existing host, port and authentication settings remain authoritative. No second port or proxy is added. The dashboard enable switch still controls whether a listener starts at all.

Unset, `false`, `0`, `no` and `off` preserve the original dashboard and any existing `HERMES_WEB_DIST` override. `true`, `1`, `yes` and `on` select the packaged browser assets; spelling is case-insensitive. In client mode the packaged directory deliberately takes precedence over a manual `HERMES_WEB_DIST`. Invalid values or missing packaged assets exit with a configuration error and keep the service down rather than silently falling back or compiling at startup.

The toggle is wired through the container's dashboard launcher. Direct CLI development can use the existing `HERMES_WEB_DIST` mechanism; this patch does not add a new CLI command.

## Implementation boundaries

The browser adapter supplies same-origin REST, fresh WebSocket tickets, gateway/profile identity, login/logout navigation, browser links and the existing renderer's boot contract. It does not advertise local file watching, native installer repair, process/window control or remote-host editing as working browser capabilities. Disk-plugin watching is gated when the native watcher is absent. Browser boot recovery offers reload rather than native repair or filesystem log actions.

Native source files remain in the tree. Native build dependencies are removed from this fork's Desktop workspace; Electron packaging is not a verified output of this branch. The dependency guard still validates everything a manifest declares, including native dependencies if another manifest declares them. The root lockfile removes the native dependency closure without changing retained package versions.

This is the framework milestone, not a claim that every Desktop feature has its browser equivalent. Attachments, native filesystem features, pop-outs, voice/media integration and complete parity still require their own acceptance before the browser branch is considered feature-complete.

## Local verification

- `npm run build:browser -w apps/desktop` builds the renderer.
- `npm run typecheck:browser -w apps/desktop` checks renderer types.
- `scripts/browser-client-smoke.mjs` launches a real backend with an isolated home and generated test-only credentials. It packages the compiled assets into a temporary directory, sources the same UI selector as the container, uses the existing login page, and checks persisted-session loading, actual WebSocket responses, refresh and logout. It does not invoke a model or use production state.
- The smoke runner uses `.venv/bin/python` by default; `BROWSER_SMOKE_PYTHON` can select an existing test interpreter. `BROWSER_SMOKE_CHROMIUM`, `BROWSER_SMOKE_HOST` and `BROWSER_SMOKE_PORT` configure the isolated test harness, not the product.
- Reports and screenshots are written beneath `.hermes/browser-smoke/`. The source tree, build inputs and generated bundle are identified in the verification manifest.

The local browser run uses loopback with the backend bound to a non-loopback interface so its authentication gate is enabled. The environment's proxy policy denied the container's LAN address; actual LAN HTTP acceptance is therefore reserved for the final image test. The backend has no model credentials in this fixture, so inference is intentionally unavailable.

## Image acceptance still owned by Q

The final build must prove the Docker layers, packaged static assets and actual s6 lifecycle. Both UI modes need to work on the configured port, retain the existing login/authentication boundary, and preserve persistent state across recreation. The configured port must not change with the toggle. Disabled dashboard, missing assets, invalid selector, ordinary crash/restart and shutdown are explicit cases. No Docker image or deployment is claimed by the local tests.
