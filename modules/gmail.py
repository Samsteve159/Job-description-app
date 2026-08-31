"""Read-only Gmail access.

Two things this is for, and they are different jobs sharing one connection:

  applications   mail you have already sent or been acknowledged for, so the dashboard
                 counts what you actually applied to rather than what you remembered
                 to type in
  alerts         LinkedIn and Naukri job alert emails. Those boards refuse automated
                 access, but they will happily email you the same listings, and mail in
                 your own inbox is yours to read. This is the scout, without scraping.

Read-only, always. The scope below is the narrowest Google offers for reading mail, and
nothing here can send, label, archive or delete. That is enforced by Google, not by this
file being careful.

Credentials live in data/gmail_client.json, the refresh token in data/gmail_token.json.
Both are gitignored and chmod 600. Neither is ever read by anything else in the app.
"""
from __future__ import annotations

import base64
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

log = logging.getLogger("jobapp.gmail")

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

DATA = Path(__file__).resolve().parent.parent / "data"
CLIENT_FILE = DATA / "gmail_client.json"
TOKEN_FILE = DATA / "gmail_token.json"


class GmailNotConnected(RuntimeError):
    """Raised when there is no usable token, so callers can offer to connect."""


@dataclass
class Message:
    """The little we keep. Bodies are read and discarded, never stored."""
    id: str
    thread_id: str
    sender: str = ""
    subject: str = ""
    received: Optional[datetime] = None
    snippet: str = ""
    labels: List[str] = field(default_factory=list)

    @property
    def sender_domain(self) -> str:
        m = re.search(r"@([\w.-]+)", self.sender or "")
        return m.group(1).lower() if m else ""


# --------------------------------------------------------------------------- auth

def connected() -> bool:
    return TOKEN_FILE.exists()


def _credentials(interactive: bool = False):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    creds = None
    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            _save(creds)
            return creds
        except Exception as exc:  # noqa: BLE001 - a dead token must not be fatal
            log.warning("gmail token refresh failed, re-authorisation needed: %s", exc)
            creds = None

    if not interactive:
        raise GmailNotConnected(
            "Gmail is not connected. Run: python3 scripts/gmail_auth.py"
        )

    if not CLIENT_FILE.exists():
        raise GmailNotConnected(
            f"No OAuth client at {CLIENT_FILE}. Download it from Google Cloud first."
        )

    from google_auth_oauthlib.flow import InstalledAppFlow
    flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_FILE), SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent",
                                  authorization_prompt_message="")
    _save(creds)
    return creds


def _save(creds) -> None:
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(creds.to_json())
    TOKEN_FILE.chmod(0o600)


def service(interactive: bool = False):
    from googleapiclient.discovery import build
    return build("gmail", "v1", credentials=_credentials(interactive),
                 cache_discovery=False)


def account(interactive: bool = False) -> str:
    return service(interactive).users().getProfile(userId="me").execute().get(
        "emailAddress", "")


# ------------------------------------------------------------------------ reading

def _header(payload: Dict[str, Any], name: str) -> str:
    for h in payload.get("headers", []) or []:
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "") or ""
    return ""


def search(query: str, days: Optional[int] = None, limit: int = 100,
           svc: Any = None) -> List[Message]:
    """Gmail's own search, run on Gmail's servers.

    `query` takes the syntax you type into the Gmail search box. `days` is a convenience
    that appends a newer_than clause rather than filtering afterwards, because pulling a
    year of mail to throw most of it away is the slow way round.
    """
    svc = svc or service()
    q = f"{query} newer_than:{days}d".strip() if days else query

    out: List[Message] = []
    page: Optional[str] = None
    while len(out) < limit:
        resp = (svc.users().messages()
                .list(userId="me", q=q, maxResults=min(100, limit - len(out)),
                      pageToken=page)
                .execute())
        ids = [m["id"] for m in resp.get("messages", []) or []]
        for mid in ids:
            full = (svc.users().messages()
                    .get(userId="me", id=mid, format="metadata",
                         metadataHeaders=["From", "Subject", "Date"])
                    .execute())
            payload = full.get("payload", {}) or {}
            received = None
            ms = full.get("internalDate")
            if ms:
                received = datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc)
            out.append(Message(
                id=full["id"], thread_id=full.get("threadId", ""),
                sender=_header(payload, "From"),
                subject=_header(payload, "Subject"),
                received=received,
                snippet=full.get("snippet", "") or "",
                labels=list(full.get("labelIds", []) or []),
            ))
        page = resp.get("nextPageToken")
        if not page or not ids:
            break

    log.info("gmail: %d message(s) for %r", len(out), q)
    return out


def body_text(message_id: str, svc: Any = None) -> str:
    """Full text of one message. Fetched on demand and never written to the database."""
    svc = svc or service()
    full = svc.users().messages().get(userId="me", id=message_id,
                                      format="full").execute()

    chunks: List[str] = []

    def walk(part: Dict[str, Any]) -> None:
        if part.get("mimeType", "").startswith("text/"):
            data = (part.get("body", {}) or {}).get("data")
            if data:
                chunks.append(base64.urlsafe_b64decode(data).decode("utf-8", "replace"))
        for sub in part.get("parts", []) or []:
            walk(sub)

    walk(full.get("payload", {}) or {})
    return "\n".join(chunks)
