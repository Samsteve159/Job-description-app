"""One-time Gmail sign-in. Opens a browser, asks you, saves a refresh token.

    python3 scripts/gmail_auth.py            # connect, or confirm an existing connection
    python3 scripts/gmail_auth.py --status   # say whether it is connected, ask nothing
    python3 scripts/gmail_auth.py --forget   # delete the token. Revokes nothing at Google

Read-only scope. Google will say so on the consent screen: "Read all resources and their
metadata". There is no narrower scope for reading mail, and nothing here can send or
delete.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from modules import gmail  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--status", action="store_true", help="report and exit")
    ap.add_argument("--forget", action="store_true", help="delete the stored token")
    args = ap.parse_args()

    if args.forget:
        if gmail.TOKEN_FILE.exists():
            gmail.TOKEN_FILE.unlink()
            print("Token deleted. Revoke the app itself at myaccount.google.com/permissions")
        else:
            print("Nothing stored.")
        return 0

    if args.status:
        if not gmail.connected():
            print("Not connected.")
            return 1
        try:
            print(f"Connected as {gmail.account()}")
            return 0
        except Exception as exc:  # noqa: BLE001
            print(f"A token exists but does not work: {exc}")
            return 1

    if not gmail.CLIENT_FILE.exists():
        print(f"No OAuth client at {gmail.CLIENT_FILE}.")
        return 1

    if gmail.connected():
        try:
            print(f"Already connected as {gmail.account()}")
            return 0
        except Exception:  # noqa: BLE001 - fall through and re-authorise
            print("The stored token no longer works. Signing in again.")

    print("A browser window will open. Sign in as yourself and allow read-only access.")
    print("Google will warn that the app is not verified. That is expected: it is your")
    print("own app, unpublished, with you as its only test user. Click Advanced, then")
    print("Go to Job App.\n")

    try:
        address = gmail.account(interactive=True)
    except Exception as exc:  # noqa: BLE001
        print(f"\nSign-in failed: {exc}")
        return 1

    print(f"\nConnected as {address}")
    print(f"Token saved to {gmail.TOKEN_FILE} (gitignored, chmod 600)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
