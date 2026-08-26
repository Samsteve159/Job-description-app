"""Job URL to clean job description text.

Uses the standard library HTML parser rather than adding BeautifulSoup, because the job
here is small and blunt: drop script and style, keep the text, find the densest run of it.
A job posting is mostly one long block of prose, which is an easy thing to find and a hard
thing to get subtly wrong.

The important behaviour is not parsing, it is knowing when it has failed. A login wall, a
cookie interstitial and a bot check all return HTTP 200 with a body full of words. Handing
that to `extract` produces a confident, entirely fictional set of requirements. So anything
that does not look like a job description raises, with an instruction to paste the text.

LinkedIn is a special case and always will be. It answers non-browser clients with HTTP
999, which is not a real status code, and no header set changes that. There is no version
of this module that scrapes LinkedIn. The app asks you to paste instead, which takes five
seconds and does not get anybody's account restricted.
"""
from __future__ import annotations

import logging
import re
from html import unescape
from html.parser import HTMLParser
from typing import List, Optional, Tuple

import httpx

log = logging.getLogger(__name__)

TIMEOUT = 20.0
MAX_BYTES = 4_000_000
MIN_JD_CHARS = 400

# Sent because many sites serve a stub to anything that looks automated. This is not an
# attempt to defeat a bot check: when a site says no, this module gives up and says so.
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-GB,en;q=0.9",
}

_DROP_TAGS = {"script", "style", "noscript", "svg", "head", "nav", "footer", "form"}
_BREAK_TAGS = {"p", "br", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section"}

_BLOCKED_HOSTS = {
    "linkedin.com": (
        "LinkedIn answers automated clients with HTTP 999 and no header set changes that. "
        "Open the posting in your browser, copy the description, and paste it in."
    ),
    "naukri.com": (
        "Naukri blocks automated fetches. Open the posting and paste the description in."
    ),
    "glassdoor.com": (
        "Glassdoor blocks automated fetches. Open the posting and paste the description in."
    ),
}

# Phrases that mean the page is a wall, not a posting. Checked against the extracted text.
_WALL_MARKERS = (
    "sign in to continue", "please enable javascript", "verify you are human",
    "access denied", "are you a robot", "checking your browser",
    "log in to view", "create an account to continue", "enable cookies",
    "this page isn't available", "page not found", "403 forbidden",
)

# What a real job description almost always contains at least a couple of.
_JD_MARKERS = (
    "responsibilit", "qualificat", "requirement", "experience", "you will",
    "we are looking", "about the role", "about the job", "skills", "the role",
    "what you", "who you are", "job description", "apply", "candidate",
)


class FetchError(RuntimeError):
    """The page could not be turned into a job description. The message says what to do."""


class _Text(HTMLParser):
    """Collect visible text, keeping block boundaries so paragraphs survive."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.chunks: List[str] = []
        self._skip = 0
        self.title: Optional[str] = None
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag in _DROP_TAGS:
            self._skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag in _BREAK_TAGS:
            self.chunks.append("\n")

    def handle_endtag(self, tag):
        if tag in _DROP_TAGS and self._skip:
            self._skip -= 1
        elif tag == "title":
            self._in_title = False
        elif tag in _BREAK_TAGS:
            self.chunks.append("\n")

    def handle_data(self, data):
        if self._in_title and self.title is None:
            self.title = data.strip()
        if not self._skip and data.strip():
            self.chunks.append(data)

    def text(self) -> str:
        raw = "".join(self.chunks)
        lines = [re.sub(r"[ \t\xa0]+", " ", ln).strip() for ln in raw.split("\n")]
        return "\n".join(ln for ln in lines if ln)


def _densest_block(text: str) -> str:
    """Keep the longest run of substantial lines, which is the posting itself.

    Job pages bury the description in navigation, related jobs and legal boilerplate,
    all of which arrive as short lines. The description arrives as long ones.
    """
    lines = text.split("\n")
    best: Tuple[int, int, int] = (0, 0, 0)     # score, start, end
    start, score = 0, 0
    for i, line in enumerate(lines + [""]):
        if len(line) >= 40 or (line and score and len(line) >= 12):
            if score == 0:
                start = i
            score += len(line)
        else:
            if score > best[0]:
                best = (score, start, i)
            score = 0
    if best[0] == 0:
        return text
    return "\n".join(lines[best[1]:best[2]])


def looks_like_a_job(text: str) -> Tuple[bool, str]:
    """Is this a posting, or a wall wearing one's clothes? Returns (verdict, reason)."""
    lowered = text.lower()
    if len(text) < MIN_JD_CHARS:
        return False, f"only {len(text)} characters of text, too short to be a posting"
    for marker in _WALL_MARKERS:
        if marker in lowered:
            return False, f"the page says {marker!r}, which reads as a login or bot wall"
    hits = sum(1 for m in _JD_MARKERS if m in lowered)
    if hits < 3:
        return False, f"only {hits} job-description markers found, expected at least 3"
    return True, f"{len(text)} characters, {hits} markers"


def normalise_url(url: str) -> str:
    url = (url or "").strip()
    # Without a scheme httpx reads the whole thing as a path and the host comes back
    # empty, which silently defeated the block list for a bare "naukri.com/job/1".
    return url if re.match(r"^https?://", url, re.I) or not url else "https://" + url


def blocked_reason(url: str) -> Optional[str]:
    host = re.sub(r"^www\.", "", (httpx.URL(normalise_url(url)).host or "").lower())
    for blocked, reason in _BLOCKED_HOSTS.items():
        if host == blocked or host.endswith("." + blocked):
            return reason
    return None


def clean(text: str) -> str:
    """Normalise pasted or fetched text. Safe to call on either."""
    text = unescape(text or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = [re.sub(r"[ \t\xa0]+", " ", ln).strip() for ln in text.split("\n")]
    out: List[str] = []
    for line in lines:
        if not line and out and not out[-1]:
            continue                      # collapse runs of blank lines
        out.append(line)
    return "\n".join(out).strip()


def fetch(url: str) -> str:
    """Fetch a job posting and return its description text.

    Raises FetchError with an actionable message rather than returning something that
    only looks like a job description.
    """
    url = (url or "").strip()
    if not url:
        raise FetchError("no URL given")
    url = normalise_url(url)

    reason = blocked_reason(url)
    if reason:
        raise FetchError(reason)

    try:
        with httpx.Client(follow_redirects=True, timeout=TIMEOUT, headers=HEADERS) as client:
            response = client.get(url)
    except httpx.HTTPError as exc:
        raise FetchError(f"could not reach {url}: {type(exc).__name__}") from exc

    if response.status_code == 999:
        raise FetchError(
            "the site returned HTTP 999, which means it refuses automated clients. "
            "Open the posting in your browser and paste the description in."
        )
    if response.status_code >= 400:
        raise FetchError(
            f"the site returned HTTP {response.status_code}. "
            f"Open the posting in your browser and paste the description in."
        )
    if len(response.content) > MAX_BYTES:
        raise FetchError("that page is unusually large. Paste the description instead.")

    parser = _Text()
    parser.feed(response.text)
    body = _densest_block(parser.text())

    ok, detail = looks_like_a_job(body)
    if not ok:
        raise FetchError(
            f"that URL did not give a job description ({detail}). "
            f"Open it in your browser and paste the description in."
        )

    title = (parser.title or "").strip()
    log.info("fetched %s: %s", url, detail)
    return clean(f"{title}\n\n{body}" if title and title not in body else body)
