"""Read-only Gmail job-alert discovery, with an offline .eml alternative.

This module never sends, changes or deletes mail and never opens a link found in
mail. It returns review candidates; the caller decides which to store. OAuth
uses Google's desktop loopback flow, PKCE and the system browser. No API keys,
mail bodies or OAuth responses are logged.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import html
import ipaddress
import json
import math
import os
import re
import secrets
import tempfile
import time
import webbrowser
from datetime import datetime, timezone
from email import policy
from email.parser import BytesParser
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, parse_qsl, quote, unquote, urlencode, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from .profile import contains_phrase
from .storage import canonical_url


DEFAULT_QUERY = 'newer_than:90d {from:linkedin.com subject:"job alert" subject:"jobs for you" subject:vacancy subject:vacancies subject:stillingsvarsel}'
SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
API_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages"
MAX_MESSAGE_BYTES = 5 * 1024 * 1024
MAX_TEXT_BYTES = 2 * 1024 * 1024
TOKEN_NAME = "gmail-token.json"


class GmailError(ValueError):
    """A user-facing error that does not include credentials or mail content."""


class _HTTPFailure(GmailError):
    def __init__(self, status: int):
        self.status = status
        descriptions = {
            400: "Google could not accept this request. Check the search query or reconnect Gmail.",
            401: "Gmail authorization has expired. Connect Gmail again.",
            403: "Gmail access was refused. Enable the Gmail API and grant read-only access to your test account.",
            404: "A message is no longer available in Gmail.",
            429: "Gmail is temporarily limiting requests. Try again later.",
        }
        super().__init__(descriptions.get(status, "Google is temporarily unavailable. Try again later."))


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise GmailError("Google returned an unexpected redirect; the request was stopped.")


def _request_json(url: str, *, form: dict | None = None, access_token: str = "") -> dict:
    """Only fixed Google origins; never pass tokens through redirects or proxies."""
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.netloc not in {"gmail.googleapis.com", "oauth2.googleapis.com"}:
        raise GmailError("Unsupported Gmail API destination.")
    is_token_request = parts.netloc == "oauth2.googleapis.com" and parts.path == "/token" and not parts.query
    is_message_read = parts.netloc == "gmail.googleapis.com" and re.fullmatch(r"/gmail/v1/users/me/messages(?:/[A-Za-z0-9_-]{1,200})?", parts.path)
    if not ((is_token_request and form is not None and not access_token) or (is_message_read and form is None)):
        raise GmailError("Only read-only Gmail message requests and OAuth token exchange are supported.")
    headers = {"Accept": "application/json", "User-Agent": "NorwayJobAgent/0.1"}
    data = None
    if form is not None:
        data = urlencode(form).encode("ascii")
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    if access_token:
        headers["Authorization"] = "Bearer " + access_token
    request = Request(url, data=data, headers=headers, method="POST" if form is not None else "GET")
    try:
        with build_opener(ProxyHandler({}), _NoRedirect()).open(request, timeout=25) as response:
            content = response.read(MAX_MESSAGE_BYTES + 1)
        if len(content) > MAX_MESSAGE_BYTES:
            raise GmailError("A Gmail response was too large to read safely.")
        result = json.loads(content)
        if not isinstance(result, dict):
            raise GmailError("Google returned an unexpected response.")
        return result
    except HTTPError as exc:
        # OAuth error bodies may contain sensitive data; do not echo them.
        raise _HTTPFailure(exc.code) from None
    except (URLError, TimeoutError, OSError):
        raise GmailError("Cannot reach Google. Check your internet connection and try again.") from None
    except (UnicodeError, json.JSONDecodeError):
        raise GmailError("Google returned an unreadable response.") from None


def _windows_protect(data: bytes, *, decrypt: bool = False) -> bytes:
    """DPAPI binds the token to the current Windows account, with no UI prompt."""
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]

    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    target = Blob()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    if decrypt:
        function = crypt32.CryptUnprotectData
        function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    else:
        function = crypt32.CryptProtectData
        function.argtypes = [ctypes.POINTER(Blob), wintypes.LPCWSTR, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    function.restype = wintypes.BOOL
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise GmailError("Windows could not unlock Gmail credentials for this user. Connect Gmail again.")
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        kernel32.LocalFree(target.data)


def _token_path(data_dir: Path) -> Path:
    return Path(data_dir).expanduser() / TOKEN_NAME


def _save_token(data_dir: Path, token: dict) -> None:
    path = _token_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(token, ensure_ascii=False).encode("utf-8")
    if os.name == "nt":
        content = json.dumps({"protection": "windows-dpapi", "data": base64.b64encode(_windows_protect(content)).decode("ascii")}).encode("utf-8")
    descriptor, temporary = tempfile.mkstemp(prefix=".gmail-", dir=path.parent)
    try:
        os.chmod(temporary, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _load_token(data_dir: Path) -> dict:
    try:
        path = _token_path(data_dir)
        if path.stat().st_size > 65536:
            raise GmailError("Gmail credentials are invalid. Connect Gmail again.")
        token = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(token, dict) and token.get("protection") == "windows-dpapi":
            if os.name != "nt":
                raise GmailError("These Gmail credentials belong to Windows. Connect Gmail on this computer.")
            token = json.loads(_windows_protect(base64.b64decode(token["data"], validate=True), decrypt=True))
        if not isinstance(token, dict) or token.get("scope") != SCOPE:
            raise GmailError("Gmail credentials are invalid. Connect Gmail again.")
        return token
    except FileNotFoundError:
        raise GmailError("Connect Gmail first, or import a downloaded .eml job alert.") from None
    except (OSError, KeyError, ValueError, TypeError) as exc:
        if isinstance(exc, GmailError):
            raise
        raise GmailError("Gmail credentials cannot be read. Connect Gmail again.") from None


def connection_status(data_dir: Path) -> dict:
    """Local credential presence, not a live assertion that Google accepts them."""
    if not _token_path(data_dir).exists():
        return {"connected": False, "has_credentials": False, "detail": "Not connected. Import .eml files or connect your Gmail account."}
    try:
        token = _load_token(data_dir)
        valid = bool(token.get("refresh_token") or token.get("access_token"))
        return {"connected": valid, "has_credentials": True, "detail": "Read-only credentials saved. Connection is checked when you fetch alerts." if valid else "Connect Gmail again to renew access."}
    except GmailError as exc:
        return {"connected": False, "has_credentials": True, "detail": str(exc)}


def _client_credentials(path: Path) -> dict:
    try:
        path = Path(path)
        if path.stat().st_size > 65536:
            raise GmailError("Choose the Desktop app OAuth JSON downloaded from Google Cloud.")
        client = json.loads(path.read_text(encoding="utf-8-sig")).get("installed", {})
        client_id = client.get("client_id", "")
        if not isinstance(client_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]+\.apps\.googleusercontent\.com", client_id):
            raise GmailError("Choose a Google OAuth Desktop app JSON, not a web client or service account.")
        secret = client.get("client_secret", "")
        if not isinstance(secret, str):
            raise GmailError("The Google Desktop app credential file is invalid.")
        return {"client_id": client_id, "client_secret": secret}
    except (OSError, json.JSONDecodeError, AttributeError):
        raise GmailError("Cannot read that credential file. Choose the Desktop app JSON downloaded from Google Cloud.") from None


def _callback_result(path: str, expected_state: str) -> tuple[str, str]:
    parsed = urlsplit(path)
    if parsed.path != "/oauth2callback" or parsed.scheme or parsed.netloc:
        raise GmailError("Unexpected OAuth callback path.")
    values = parse_qs(parsed.query, keep_blank_values=True)
    supplied = values.get("state", [])
    if len(supplied) != 1 or not hmac.compare_digest(supplied[0], expected_state):
        raise GmailError("OAuth state did not match. This sign-in response was ignored.")
    if values.get("error"):
        return "", "Gmail access was not granted. You can connect again or import .eml files."
    codes = values.get("code", [])
    if len(codes) != 1 or not codes[0] or len(codes[0]) > 4096:
        raise GmailError("Google did not return a valid authorization code.")
    return codes[0], ""


def connect(data_dir: Path, credentials_path: Path, *, timeout_seconds: float = 180, cancel_event=None) -> dict:
    """Open Google sign-in in the user's browser. Call off the UI thread.

    The user performs login and consent. A short-lived listener binds only to
    127.0.0.1 and checks a random state plus PKCE before accepting a callback.
    """
    client = _client_credentials(credentials_path)
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).decode("ascii").rstrip("=")
    result = {}

    class Callback(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            try:
                if self.headers.get("Host") != f"127.0.0.1:{self.server.server_port}":
                    raise GmailError("Unexpected OAuth callback host.")
                code, error = _callback_result(self.path, state)
                result.update(code=code, error=error)
                status, body = 200, b"Sign-in response received. Return to Norway Job Agent to finish connecting. You can close this tab."
            except GmailError:
                status, body = 400, b"This sign-in response was not accepted. Return to the app."
            self.send_response(status)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    class LoopbackServer(HTTPServer):
        def get_request(self):
            connection, address = super().get_request()
            connection.settimeout(2)
            return connection, address

    with LoopbackServer(("127.0.0.1", 0), Callback) as server:
        server.timeout = 0.5
        redirect = f"http://127.0.0.1:{server.server_port}/oauth2callback"
        url = AUTH_URL + "?" + urlencode({"client_id": client["client_id"], "redirect_uri": redirect, "response_type": "code", "scope": SCOPE, "state": state, "code_challenge": challenge, "code_challenge_method": "S256", "access_type": "offline", "prompt": "consent select_account"})
        if not webbrowser.open(url, new=1):
            raise GmailError("Could not open your browser for Google sign-in. Set a default browser and try again.")
        deadline = time.monotonic() + max(1, min(timeout_seconds, 300))
        while not result and time.monotonic() < deadline:
            if cancel_event is not None and cancel_event.is_set():
                raise GmailError("Gmail connection cancelled. Your existing connection was kept.")
            server.handle_request()
    if not result:
        raise GmailError("Google sign-in timed out. Connect Gmail again when you are ready.")
    if result.get("error"):
        raise GmailError(result["error"])
    response = _request_json(TOKEN_URL, form={**client, "code": result["code"], "code_verifier": verifier, "redirect_uri": redirect, "grant_type": "authorization_code"})
    token = _normalize_token(response, client)
    _save_token(data_dir, token)
    return connection_status(data_dir)


def _normalize_token(response: dict, previous: dict) -> dict:
    if not isinstance(response.get("access_token"), str) or not response["access_token"]:
        raise GmailError("Google did not return Gmail access. Connect again and grant read-only access.")
    scope = response.get("scope", SCOPE)
    scopes = scope.split() if isinstance(scope, str) else []
    if scopes != [SCOPE]:
        raise GmailError("Google returned unexpected permissions. Use a dedicated read-only Gmail OAuth client.")
    try:
        lifetime = max(0, int(response.get("expires_in", 3600)))
    except (ValueError, TypeError):
        raise GmailError("Google returned an invalid token lifetime.") from None
    if "refresh_token" in response and not isinstance(response["refresh_token"], str):
        raise GmailError("Google returned invalid credentials. Connect Gmail again.")
    refresh_token = response.get("refresh_token") or previous.get("refresh_token", "")
    if not isinstance(refresh_token, str):
        raise GmailError("Google returned invalid credentials. Connect Gmail again.")
    return {"client_id": previous["client_id"], "client_secret": previous.get("client_secret", ""), "access_token": response["access_token"], "refresh_token": refresh_token, "expires_at": time.time() + lifetime, "scope": SCOPE}


def _access_token(data_dir: Path, *, force_refresh: bool = False) -> str:
    token = _load_token(data_dir)
    try:
        expires_at = float(token.get("expires_at", 0))
        if not math.isfinite(expires_at) or not isinstance(token.get("access_token", ""), str) or not isinstance(token.get("refresh_token", ""), str):
            raise ValueError
    except (ValueError, TypeError):
        raise GmailError("Gmail credentials are invalid. Connect Gmail again.") from None
    if not force_refresh and token.get("access_token") and expires_at > time.time() + 60:
        return token["access_token"]
    if not token.get("refresh_token"):
        raise GmailError("Gmail access has expired. Connect Gmail again.")
    if not isinstance(token.get("client_id"), str) or not token["client_id"]:
        raise GmailError("Gmail credentials are invalid. Connect Gmail again.")
    try:
        response = _request_json(TOKEN_URL, form={"client_id": token["client_id"], "client_secret": token.get("client_secret", ""), "refresh_token": token["refresh_token"], "grant_type": "refresh_token"})
    except _HTTPFailure as exc:
        if exc.status in {400, 401}:
            raise GmailError("Gmail access has expired or was revoked. Connect Gmail again; Google test-app access can expire after seven days.") from None
        raise
    token = _normalize_token(response, token)
    _save_token(data_dir, token)
    return token["access_token"]


def disconnect(data_dir: Path) -> dict:
    """Forget this app's local credentials, retaining already imported jobs.

    This does not revoke other clients. Google Account -> Third-party access
    can revoke the grant separately; no remote account mutation occurs here.
    """
    _token_path(data_dir).unlink(missing_ok=True)
    return connection_status(data_dir)


def _gmail_get(data_dir: Path, url: str) -> dict:
    try:
        return _request_json(url, access_token=_access_token(data_dir))
    except _HTTPFailure as exc:
        if exc.status != 401:
            raise
    return _request_json(url, access_token=_access_token(data_dir, force_refresh=True))


class _EmailHTML(HTMLParser):
    """Text and anchor offsets only. Images, CSS, scripts and URLs are inert."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.chunks = []
        self.length = 0
        self.links = []
        self.anchor = None
        self.ignored = 0

    def append(self, text):
        if self.length < MAX_TEXT_BYTES:
            text = text[:MAX_TEXT_BYTES - self.length]
            self.chunks.append(text)
            self.length += len(text)

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "head"}:
            self.ignored += 1
        if self.ignored:
            return
        if tag in {"br", "p", "div", "tr", "li", "h1", "h2", "h3"}:
            self.append("\n")
        if tag == "a":
            self.anchor = (dict(attrs).get("href", ""), self.length)

    def handle_endtag(self, tag):
        if tag in {"script", "style", "head"} and self.ignored:
            self.ignored -= 1
            return
        if self.ignored:
            return
        if tag == "a" and self.anchor:
            url, start = self.anchor
            self.links.append({"url": url, "start": start, "end": self.length})
            self.anchor = None
        if tag in {"p", "div", "tr", "li", "h1", "h2", "h3"}:
            self.append("\n")

    def handle_data(self, data):
        if not self.ignored:
            self.append(data)


def _safe_public_url(value: str) -> str:
    """Syntactic public URL filtering only: no DNS or network calls from mail."""
    value = html.unescape(value).strip().rstrip(".,;!>)\"'")
    if not value or len(value) > 8192 or any(ord(char) < 32 for char in value):
        return ""
    try:
        parts = urlsplit(value)
        host = (parts.hostname or "").rstrip(".").lower()
        if parts.scheme not in {"http", "https"} or not host or parts.username is not None or parts.password is not None or parts.port not in {None, 80, 443}:
            return ""
        if any(char.isspace() for char in host) or "%" in host or "\\" in value or "." not in host:
            return ""
        if host.endswith((".localhost", ".local", ".internal", ".lan", ".home", ".test", ".invalid")) or host in {"localhost", "metadata.google.internal"}:
            return ""
        try:
            address = ipaddress.ip_address(host)
            if not address.is_global:
                return ""
        except ValueError:
            if re.fullmatch(r"[0-9.]+", host) or any(part.startswith("0x") for part in host.split(".")):
                return ""
        host = host.encode("idna").decode("ascii")
        return urlunsplit((parts.scheme, host, parts.path, parts.query, ""))
    except (ValueError, UnicodeError):
        return ""


def job_url(value: str) -> str:
    """Decode common tracking wrappers offline; accept individual job links."""
    current = html.unescape(value).strip()
    for _ in range(5):
        current = _safe_public_url(current)
        if not current:
            return ""
        parts = urlsplit(current)
        host = parts.hostname or ""
        query = parse_qs(parts.query)
        nested = next((values[0] for key, values in query.items() if key.lower() in {"url", "target", "redirect", "redirect_url", "redirecturl", "destination", "dest", "u"} and values and values[0].startswith(("https://", "http://", "https%3A", "http%3A"))), "")
        if nested:
            current = unquote(nested)
            continue
        if host == "linkedin.com" or host.endswith(".linkedin.com"):
            match = re.search(r"/jobs/view/(?:[^/?#]*-)?(\d+)(?:/|$)", parts.path)
            if match:
                return "https://www.linkedin.com/jobs/view/" + match.group(1)
            return ""
        # Paths/parameters must identify an individual posting, not a homepage,
        # unsubscribe link, generic search, notification or account action.
        if re.search(r"unsubscribe|email-preferences|manage-alert|sign-?in|log-?in|tracking|/pixel", parts.path, re.I):
            return ""
        specific_path = re.search(r"/(?:jobs?|careers?|positions?|vacanc(?:y|ies)|opportunities|requisitions?|recruitment)/(?:[^/?#]+/)*[^/?#]+", parts.path, re.I)
        specific_query = any(key.lower() in {"jobid", "job_id", "job", "gh_jid", "requisitionid", "reqid", "positionid", "vacancyid", "postingid", "jobindex"} and values[0] for key, values in query.items())
        ats_path = ((host == "lever.co" or host.endswith(".lever.co")) and len(parts.path.strip("/").split("/")) >= 2) or (host.endswith(".myworkdayjobs.com") and "/job/" in parts.path)
        tail = parts.path.rstrip("/").split("/")[-1].casefold()
        if tail in {"search", "all", "list", "results", "view", "new", "alerts", "recommended"} and not specific_query:
            return ""
        if not (specific_path or specific_query or ats_path):
            return ""
        # Remove mail identifiers and affiliate parameters; preserve only query
        # keys known to identify a vacancy or its language/site context.
        keep = {"jobid", "job_id", "job", "gh_jid", "requisitionid", "reqid", "positionid", "vacancyid", "postingid", "jobindex", "lang", "locale", "siteid", "company", "currentjobid"}
        clean = [(key, val) for key, val in parse_qsl(parts.query) if key.lower() in keep]
        return canonical_url(urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(clean), "")))
    return ""


def _body_parts(payload: dict, *, depth: int = 0) -> tuple[list[str], list[str]]:
    if depth > 20 or not isinstance(payload, dict) or payload.get("filename"):
        return [], []
    headers = _headers(payload)
    if "attachment" in headers.get("content-disposition", "").casefold():
        return [], []
    plain, rich = [], []
    mime = str(payload.get("mimeType", "")).lower()
    body = payload.get("body", {})
    data = body.get("data", "") if isinstance(body, dict) else ""
    if mime in {"text/plain", "text/html"} and isinstance(data, str) and len(data) <= MAX_TEXT_BYTES * 2:
        try:
            decoded = base64.b64decode(data + "=" * (-len(data) % 4), altchars=b"-_", validate=True)[:MAX_TEXT_BYTES]
            charset_match = re.search(r"charset\s*=\s*[\"']?([^;\s\"']+)", headers.get("content-type", ""), re.I)
            charset = charset_match.group(1) if charset_match else "utf-8"
            try:
                content = decoded.decode(charset, errors="replace")
            except LookupError:
                content = decoded.decode("utf-8", errors="replace")
            (plain if mime == "text/plain" else rich).append(content)
        except (ValueError, TypeError):
            pass
    parts = payload.get("parts", [])
    for part in (parts[:100] if isinstance(parts, list) else []):
        p, h = _body_parts(part, depth=depth + 1)
        plain.extend(p)
        rich.extend(h)
    return plain, rich


def _clean_text(value: str) -> str:
    return re.sub(r"\n[ \t]*\n(?:[ \t]*\n)+", "\n\n", re.sub(r"[ \t]+", " ", value)).strip()


def _candidate_list(plain: str, rich: str, metadata: dict, profile: dict) -> list[dict]:
    parser = _EmailHTML()
    parser.feed(rich[:MAX_TEXT_BYTES])
    parser.close()
    text = "".join(parser.chunks)
    anchors = parser.links
    # Prefer HTML because links and visible titles retain their relationship.
    # Plain text is a fallback when HTML contains no individual vacancy URL.
    if not any(job_url(item["url"]) for item in anchors):
        text = plain[:MAX_TEXT_BYTES]
        anchors = [{"url": match.group(0), "start": match.start(), "end": match.end()} for match in re.finditer(r"https?://[^\s<>\"']+", text)]
    grouped = []
    for anchor in anchors:
        url = job_url(anchor["url"])
        if url:
            grouped.append({**anchor, "url": url})
        if len(grouped) >= 1000:
            break
    role_terms = [str(term).strip() for key in ("target_roles", "related_roles") for term in profile.get(key, []) if str(term).strip()]
    skill_terms = [str(term).strip() for term in profile.get("skills", []) if str(term).strip()]
    excluded = [str(term).strip() for term in profile.get("excluded_keywords", []) if str(term).strip()]
    jobs = {}
    for index, anchor in enumerate(grouped):
        url = anchor["url"]
        following = next((item["start"] for item in grouped[index + 1:] if item["url"] != url), len(text))
        previous = next((item["end"] for item in reversed(grouped[:index]) if item["url"] != url), 0)
        label = _clean_text(text[anchor["start"]:anchor["end"]])
        generic = not label or re.match(r"^(?:https?://|view\b|see\b|apply\b|learn more\b|read more\b|job details\b)", label, re.I) or len(label) > 200
        if generic:
            prefix = _clean_text(text[max(previous, anchor["start"] - 500):anchor["start"]])
            lines = [line.strip(" -*•\t") for line in prefix.splitlines() if line.strip()]
            title = next((line for line in reversed(lines) if any(contains_phrase(line, term) for term in role_terms) and not re.match(r"^(?:subject|company|location|employer):", line, re.I) and len(line) <= 180), "")
            # A generic CTA has no safely attributable title. Keep it only when
            # a role title is explicitly present immediately before the URL.
            if not title:
                continue
            # For a plain URL or generic CTA, text after the link can already
            # belong to the next digest card. Keep only its preceding title
            # and details, instead of borrowing the following role's company.
            context = prefix[prefix.rfind(title):]
        else:
            title = label
            context = text[anchor["start"]:min(following, anchor["end"] + 1800)]
        context = _clean_text(context)
        # Title evidence prevents a matching role elsewhere in a digest from
        # making unrelated links relevant. Skills are used only within a card.
        matched = [term for term in role_terms if contains_phrase(title, term)]
        matched += [term for term in skill_terms if contains_phrase(context, term)]
        if (role_terms or skill_terms) and not matched:
            continue
        if any(contains_phrase(title, term) for term in excluded):
            continue
        company_match = re.search(r"(?im)^\s*(?:company|employer|arbeidsgiver)\s*:\s*([^\n]{1,180})", context)
        location_match = re.search(r"(?im)^\s*(?:location|sted|arbeidssted)\s*:\s*([^\n]{1,180})", context)
        company = company_match.group(1).strip() if company_match else "Not stated in email"
        location = location_match.group(1).strip() if location_match else ""
        # Never store tracking URLs, full inbox contents or unrelated messages.
        excerpt = re.sub(r"https?://[^\s<>]+", "[link]", context)[:2200]
        job = {"source": "gmail", "source_id": hashlib.sha256(url.encode("utf-8")).hexdigest(), "source_url": url, "apply_url": url, "title": title[:200], "company": company, "location": location, "description": "Job-alert excerpt — review the original vacancy for complete details.\n\n" + excerpt, "published_at": "", "raw_json": {"email": dict(metadata), "email_import": {"review_required": True, "matched_terms": list(dict.fromkeys(matched)), "partial_description": True}}}
        existing = jobs.get(url)
        if existing is None or (existing["company"] == "Not stated in email" and company != "Not stated in email"):
            jobs[url] = job
    return list(jobs.values())


def _date(value: str) -> str:
    try:
        parsed = parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat()
    except (ValueError, TypeError, OverflowError):
        return ""


def _headers(payload: dict) -> dict:
    headers = payload.get("headers", [])
    if not isinstance(headers, list):
        return {}
    return {str(item.get("name", "")).casefold(): str(item.get("value", "")) for item in headers if isinstance(item, dict)}


def parse_message(message: dict, profile: dict) -> list[dict]:
    """Parse a Gmail `format=full` message without fetching attachments or links."""
    if not isinstance(message, dict) or not isinstance(message.get("payload", {}), dict):
        raise GmailError("A job-alert message was malformed and could not be read.")
    payload = message.get("payload", {})
    headers = _headers(payload)
    message_id = str(message.get("id", ""))
    thread_id = str(message.get("threadId", ""))
    safe_id = thread_id if re.fullmatch(r"[A-Za-z0-9_-]{1,200}", thread_id) else ""
    received = _date(headers.get("date", ""))
    if not received:
        try:
            received = datetime.fromtimestamp(int(message.get("internalDate", "")) / 1000, timezone.utc).isoformat()
        except (ValueError, TypeError, OSError, OverflowError):
            received = ""
    # Gmail's browser route uses a thread ID. Account 0 is the browser default,
    # which can differ from the account selected in OAuth; documented in setup.
    metadata = {"message_id": message_id[:200], "sender": headers.get("from", "")[:400], "subject": headers.get("subject", "")[:400], "received_at": received, "message_url": "https://mail.google.com/mail/u/0/#all/" + safe_id if safe_id else ""}
    plain, rich = _body_parts(payload)
    return _candidate_list("\n".join(plain)[:MAX_TEXT_BYTES], "\n".join(rich)[:MAX_TEXT_BYTES], metadata, profile)


def import_eml(path: Path, profile: dict) -> list[dict]:
    """Read a user-selected downloaded email. Never fetch remote content."""
    path = Path(path)
    if path.stat().st_size > MAX_MESSAGE_BYTES:
        raise GmailError("This email is larger than 5 MB. Save a job alert without large attachments.")
    raw = path.read_bytes()
    try:
        message = BytesParser(policy=policy.default).parsebytes(raw)
    except (RecursionError, ValueError):
        raise GmailError("This email is malformed or too deeply nested to read. Try another job alert.") from None
    plain, rich = [], []
    def parts_without_attachments(part, depth=0):
        if depth > 20 or part.get_content_disposition() == "attachment" or part.get_filename() or part.get_content_type() == "message/rfc822":
            return
        if part.is_multipart():
            for index, child in enumerate(part.iter_parts()):
                if index >= 100:
                    break
                yield from parts_without_attachments(child, depth + 1)
        else:
            yield part

    for part in parts_without_attachments(message):
        if part.get_content_type() not in {"text/plain", "text/html"}:
            continue
        try:
            content = part.get_content()
        except (LookupError, ValueError):
            content = (part.get_payload(decode=True) or b"").decode("utf-8", errors="replace")
        if isinstance(content, str):
            (plain if part.get_content_type() == "text/plain" else rich).append(content[:MAX_TEXT_BYTES])
    metadata = {"message_id": str(message.get("Message-ID", ""))[:200] or "eml-" + hashlib.sha256(raw).hexdigest(), "sender": str(message.get("From", ""))[:400], "subject": str(message.get("Subject", ""))[:400], "received_at": _date(str(message.get("Date", ""))), "message_url": "", "import_method": "eml"}
    return _candidate_list("\n".join(plain)[:MAX_TEXT_BYTES], "\n".join(rich)[:MAX_TEXT_BYTES], metadata, profile)


def fetch_candidates(data_dir: Path, profile: dict, query: str = DEFAULT_QUERY, page_token: str = "", max_messages: int = 50) -> dict:
    """Fetch one bounded page for review; pass the returned cursor to continue.

    No sync checkpoint is advanced here. The UI owns its query/cursor pair and
    should clear its cursor when the query changes. Duplicate URLs within this
    batch collapse, and their stable identity supports later database dedup.
    """
    if not isinstance(query, str) or not query.strip() or len(query) > 2000:
        raise GmailError("Enter a Gmail search query, up to 2,000 characters.")
    if not isinstance(max_messages, int) or isinstance(max_messages, bool) or not 1 <= max_messages <= 100:
        raise GmailError("Read between 1 and 100 messages per batch.")
    if not isinstance(page_token, str) or len(page_token) > 4096:
        raise GmailError("The Gmail page cursor is invalid. Start the search again.")
    parameters = {"q": query.strip(), "maxResults": max_messages, "includeSpamTrash": "false"}
    if page_token:
        parameters["pageToken"] = page_token
    listing = _gmail_get(data_dir, API_URL + "?" + urlencode(parameters))
    jobs, warnings, failures = {}, [], []
    scanned = 0
    messages = listing.get("messages", [])
    if not isinstance(messages, list):
        raise GmailError("Google returned an invalid message list. Try the search again.")
    for item in messages[:max_messages]:
        if not isinstance(item, dict):
            continue
        message_id = str(item.get("id", ""))
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", message_id):
            continue
        try:
            message = _gmail_get(data_dir, API_URL + "/" + quote(message_id, safe="") + "?format=full")
            scanned += 1
            for job in parse_message(message, profile):
                jobs.setdefault(job["source_url"], job)
        except GmailError as exc:
            failures.append(message_id)
            if str(exc) not in warnings:
                warnings.append(str(exc))
    if failures:
        warnings.append(f"{len(failures)} message(s) could not be read. Start this search again to retry them; saved opportunities are deduplicated.")
    return {"candidates": list(jobs.values()), "next_page_token": str(listing.get("nextPageToken", "")), "messages_scanned": scanned, "warnings": warnings, "failed_message_ids": failures}
