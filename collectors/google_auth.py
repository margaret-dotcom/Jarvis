"""Google sign-in for every account in accounts.yaml.

One OAuth client (Desktop app type, path in GOOGLE_CLIENT_SECRET_FILE) is
shared by all accounts. Each account gets its own token file at
collectors/tokens/<email>.token.json. Scopes are read-only for Gmail and
Calendar.

    python -m collectors.google_auth          authorize every account
    python -m collectors.google_auth --check  report which tokens exist

Google libraries are imported inside functions so the rest of the package
imports cleanly on a machine without them.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import config

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar.readonly",
]

AUTH_COMMAND = "python -m collectors.google_auth"


def client_secret_path() -> Path:
    """Absolute path to the OAuth client file. Raises NotConfigured if missing."""
    raw = config.env("GOOGLE_CLIENT_SECRET_FILE")
    if not raw:
        raise config.NotConfigured(
            "GOOGLE_CLIENT_SECRET_FILE is not set in .env. Create a Desktop-type "
            "OAuth client in Google Cloud Console, download the JSON, and point "
            "the variable at it.")
    path = config.resolve_path(raw)
    if not path.exists():
        raise config.NotConfigured(
            f"OAuth client file not found at {path}. Download it from Google "
            "Cloud Console (APIs and Services > Credentials).")
    return path


def has_token(email: str) -> bool:
    return config.token_path(email).exists()


def _save_token(creds, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(creds.to_json(), encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, path)


def get_credentials(email: str, interactive: bool = False):
    """Return valid google.oauth2 Credentials for one account.

    Refreshes silently when the stored token has expired. When no token exists
    (or it cannot be refreshed) and interactive is False, raises NotConfigured
    with the command to run. When interactive is True, opens the browser flow.
    """
    from google.auth.exceptions import RefreshError
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    email = email.strip().lower()
    path = config.token_path(email)
    creds = None
    if path.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(path), SCOPES)
        except (ValueError, json.JSONDecodeError) as exc:
            config.warn(f"token for {email} is unreadable ({exc.__class__.__name__}), "
                        "it will be re-created")
            creds = None

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            _save_token(creds, path)
            return creds
        except RefreshError as exc:
            config.warn(f"refresh failed for {email}: {exc.__class__.__name__}")
            creds = None

    if not interactive:
        raise config.NotConfigured(
            f"No Google token for {email}. Run `{AUTH_COMMAND}` on a machine "
            "with a browser to authorize it.")

    creds = _run_browser_flow(email)
    _save_token(creds, path)
    return creds


def _run_browser_flow(email: str):
    """Walk one account through consent and return its credentials.

    Tries a local redirect server first (the normal path). If that method is
    missing or cannot bind a port, falls back to printing the URL and asking
    for the redirect URL to be pasted back. run_console was removed from
    google-auth-oauthlib, so the paste flow is the portable fallback.
    """
    from google_auth_oauthlib.flow import InstalledAppFlow

    secret = client_secret_path()
    print(f"\nAuthorizing {email}. Sign in with exactly that account in the "
          "browser window.")
    flow = InstalledAppFlow.from_client_secrets_file(str(secret), SCOPES)
    login_hint = {"login_hint": email}

    if hasattr(flow, "run_local_server"):
        try:
            return flow.run_local_server(port=0, prompt="consent", **login_hint)
        except OSError as exc:
            config.warn(f"local server flow failed ({exc.__class__.__name__}), "
                        "falling back to copy and paste")
        except Exception as exc:  # noqa: BLE001, we want a clean fallback
            config.warn(f"local server flow failed ({exc.__class__.__name__}), "
                        "falling back to copy and paste")

    if hasattr(flow, "run_console"):
        return flow.run_console(**login_hint)

    return _run_paste_flow(flow, login_hint)


def _run_paste_flow(flow, extra_params: dict):
    """Manual flow: open the URL, sign in, paste the final localhost URL back.

    The browser will land on a localhost address that does not load. That is
    expected. Copy the whole address from the address bar and paste it here.
    """
    flow.redirect_uri = "http://localhost:1/"
    auth_url, _state = flow.authorization_url(
        access_type="offline", prompt="consent", **extra_params)
    print("\nOpen this URL in a browser:\n")
    print(auth_url)
    print("\nAfter you approve, the browser shows a localhost page that fails "
          "to load. Copy the full address from the address bar and paste it here.")
    pasted = input("Redirect URL: ").strip()
    # The redirect is plain http on localhost. oauthlib refuses that unless told.
    os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")
    flow.fetch_token(authorization_response=pasted)
    return flow.credentials


def build_service(api: str, version: str, creds, timeout: int = config.HTTP_TIMEOUT):
    """A googleapiclient service with a real socket timeout on every call."""
    import httplib2
    from google_auth_httplib2 import AuthorizedHttp
    from googleapiclient.discovery import build

    http = AuthorizedHttp(creds, http=httplib2.Http(timeout=timeout))
    return build(api, version, http=http, cache_discovery=False)


def authorize_all(accounts: list[dict] | None = None) -> dict[str, str]:
    """Run the flow for every account without a valid token. Returns email -> state."""
    accounts = accounts if accounts is not None else config.load_accounts()
    results: dict[str, str] = {}
    if not accounts:
        print("No accounts found. Copy collectors/accounts.yaml.example to "
              "collectors/accounts.yaml and list your Google accounts.")
        return results
    for account in accounts:
        email = account["email"]
        try:
            get_credentials(email, interactive=True)
            results[email] = "authorized"
        except config.NotConfigured as exc:
            results[email] = f"not configured: {exc}"
        except KeyboardInterrupt:
            results[email] = "skipped"
            print("\nSkipped.")
        except Exception as exc:  # noqa: BLE001, report and continue with the next account
            results[email] = f"failed: {exc.__class__.__name__}"
    return results


def check_all(accounts: list[dict] | None = None) -> dict[str, str]:
    """Report token state for each account without opening a browser."""
    accounts = accounts if accounts is not None else config.load_accounts()
    results: dict[str, str] = {}
    for account in accounts:
        email = account["email"]
        if not has_token(email):
            results[email] = "no token"
            continue
        try:
            get_credentials(email, interactive=False)
            results[email] = "authorized"
        except config.NotConfigured:
            results[email] = "token present but not usable, re-run authorization"
        except Exception as exc:  # noqa: BLE001
            results[email] = f"check failed: {exc.__class__.__name__}"
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog=AUTH_COMMAND,
        description="Authorize every Google account in collectors/accounts.yaml.")
    parser.add_argument("--check", action="store_true",
                        help="only report which accounts have a working token")
    args = parser.parse_args(argv)

    try:
        import google.oauth2.credentials  # noqa: F401
    except ImportError:
        print("Google libraries are not installed. Run: pip install -r requirements.txt",
              file=sys.stderr)
        return 1

    if not args.check:
        try:
            client_secret_path()
        except config.NotConfigured as exc:
            print(str(exc), file=sys.stderr)
            return 1

    results = check_all() if args.check else authorize_all()
    print("\nAccounts:")
    for email, state in results.items():
        print(f"  {email}: {state}")
    ok = sum(1 for s in results.values() if s == "authorized")
    print(f"\n{ok} of {len(results)} account(s) authorized.")
    return 0 if ok == len(results) and results else 1


if __name__ == "__main__":
    sys.exit(main())
