# ruff: noqa: E402
from __future__ import annotations

import sys
from pathlib import Path

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config import get_settings


def _status_line(ok: bool, label: str, detail: str = "") -> None:
    prefix = "OK " if ok else "FAIL"
    if detail:
        print(f"[{prefix}] {label}: {detail}")
    else:
        print(f"[{prefix}] {label}")


def _check_prefix(value: str | None, expected_prefix: str, label: str) -> bool:
    if not value:
        _status_line(False, label, "missing")
        return False
    if not value.startswith(expected_prefix):
        _status_line(False, label, f"unexpected prefix (expected {expected_prefix})")
        return False
    _status_line(True, label)
    return True


def main() -> int:
    settings = get_settings()

    print("Slack setup diagnostics\n")

    bot_token_ok = _check_prefix(settings.slack_bot_token, "xoxb-", "SLACK_BOT_TOKEN format")
    app_token_ok = _check_prefix(settings.slack_app_token, "xapp-", "SLACK_APP_TOKEN format")
    signing_ok = bool(settings.slack_signing_secret)
    _status_line(signing_ok, "SLACK_SIGNING_SECRET presence")

    if not (bot_token_ok and app_token_ok and signing_ok):
        print("\nOne or more required Slack env vars are invalid.")
        return 1

    bot_client = WebClient(token=settings.slack_bot_token)
    app_client = WebClient(token=settings.slack_app_token)

    try:
        bot_auth = bot_client.auth_test()
        _status_line(
            True,
            "Bot token auth.test",
            f"team={bot_auth.get('team')} user={bot_auth.get('user')} bot_id={bot_auth.get('bot_id')}",
        )
    except SlackApiError as error:
        _status_line(False, "Bot token auth.test", str(error))
        return 1

    try:
        app_auth = app_client.auth_test()
        _status_line(
            True,
            "App token auth.test",
            f"app_name={app_auth.get('app_name')} app_id={app_auth.get('app_id')}",
        )
    except SlackApiError as error:
        _status_line(False, "App token auth.test", str(error))
        return 1

    try:
        conn = WebClient().apps_connections_open(app_token=settings.slack_app_token or "")
        has_url = bool(conn.get("url"))
        _status_line(has_url, "Socket Mode apps.connections.open", "websocket URL issued" if has_url else "no URL")
    except SlackApiError as error:
        _status_line(False, "Socket Mode apps.connections.open", str(error))
        return 1

    print("\nDiagnostics complete.")
    print("If all checks are OK but events are not received, verify Event Subscriptions and reinstall the app.")
    print("Required bot scopes: app_mentions:read, chat:write, im:history")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
