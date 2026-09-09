"""Execute the dashboard selector and s6 finish contract without a Docker daemon."""
import os
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]


def select(tmp_path, flag):
    dist = tmp_path / 'hermes_cli' / 'browser_dist'
    env = {
        'PATH': os.defpath,
        'HERMES_WEB_CLIENT_ENABLED': flag,
        'HERMES_WEB_DIST': '/caller-managed/dashboard',
        'HERMES_DASHBOARD_PORT': '9119',
    }
    # On the original revision the selector is absent, leaving the legacy dist.
    # This exercises the missing selection rather than failing on a missing file.
    result = subprocess.run([
        'sh', '-c',
        'if [ -f "$1" ]; then . "$1"; hermes_select_dashboard_ui "$2" || exit $?; fi; '
        'printf "%s\\n%s\\n" "$HERMES_WEB_DIST" "$HERMES_DASHBOARD_PORT"',
        'test', str(ROOT / 'docker/dashboard-ui.sh'), str(tmp_path),
    ], env=env, text=True, capture_output=True)
    return result, dist


@pytest.mark.parametrize('flag', ['true', 'TRUE', '1', 'yes', 'On'])
def test_client_selects_packaged_assets_without_changing_port(tmp_path, flag):
    dist = tmp_path / 'hermes_cli' / 'browser_dist'
    dist.mkdir(parents=True)
    (dist / 'index.html').write_text('<html>packaged browser fixture</html>')
    result, _ = select(tmp_path, flag)
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [str(dist), '9119']


@pytest.mark.parametrize('flag', ['', 'false', 'FALSE', '0', 'no', 'off'])
def test_disabled_selector_preserves_existing_dist_and_port(tmp_path, flag):
    result, _ = select(tmp_path, flag)
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ['/caller-managed/dashboard', '9119']


@pytest.mark.parametrize('flag', ['true', 'unrecognised'])
def test_missing_assets_or_invalid_selector_fail_without_fallback(tmp_path, flag):
    result, _ = select(tmp_path, flag)
    assert result.returncode == 78
    assert not result.stdout
    assert 'browser' in result.stderr.lower() or 'HERMES_WEB_CLIENT_ENABLED' in result.stderr


@pytest.mark.parametrize('enabled,exit_code,signal,expected', [
    ('', '0', '0', 125), ('false', '1', '0', 125),
    ('true', '1', '0', 0), ('true', '0', '15', 0),
    ('true', '78', '0', 125), ('true', '78', '15', 0),
])
def test_finish_keeps_disabled_or_invalid_configuration_down(enabled, exit_code, signal, expected):
    result = subprocess.run([
        'sh', str(ROOT / 'docker/s6-rc.d/dashboard/finish'), exit_code, signal,
    ], env={'PATH': os.defpath, 'HERMES_DASHBOARD': enabled, 'HERMES_WEB_CLIENT_ENABLED': 'true'}, capture_output=True)
    assert result.returncode == expected
