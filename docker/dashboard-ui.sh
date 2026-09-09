#!/bin/sh
# Sourced by the existing dashboard service; never starts another server.
hermes_select_dashboard_ui() {
    case "$(printf '%s' "${HERMES_WEB_CLIENT_ENABLED:-}" | tr '[:upper:]' '[:lower:]')" in
        ''|0|false|no|off) return 0 ;;
        1|true|yes|on)
            if [ ! -r "$1/hermes_cli/browser_dist/index.html" ]; then
                printf '%s\n' '[dashboard] Packaged browser client is missing; rebuild the image.' >&2
                return 78
            fi
            HERMES_WEB_DIST="$1/hermes_cli/browser_dist"
            export HERMES_WEB_DIST
            printf '%s\n' '[dashboard] Serving the Desktop browser client on the existing dashboard port.' >&2
            ;;
        *)
            printf '%s\n' '[dashboard] Invalid HERMES_WEB_CLIENT_ENABLED; expected true or false.' >&2
            return 78
            ;;
    esac
}
