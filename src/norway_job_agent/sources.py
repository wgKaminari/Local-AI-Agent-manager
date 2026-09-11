"""Read-only public job sources. No browser automation or application submission.

NAV is deliberately excluded until incremental updates and withdrawals have a
durable checkpoint implementation. FINN and LinkedIn require manual text import.
"""

from __future__ import annotations

import html
import http.client
import ipaddress
import json
import re
import socket
import ssl
import time
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import quote, urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

MAX_RESPONSE_BYTES = 2 * 1024 * 1024
TIMEOUT_SECONDS = 15
MAX_REDIRECTS = 5
USER_AGENT = "NorwayJobAgent/0.1"
_BOARD = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,99}\Z")
_BLOCKED_HOSTS = ("linkedin.com", "finn.no")


class SourceError(ValueError):
    """A source could not be read; safe to show this message to the user."""


def _url_parts(url: str):
    if not isinstance(url, str) or not url or any(ord(c) < 32 for c in url):
        raise SourceError("Enter a valid public http:// or https:// URL.")
    try:
        parts = urlsplit(url)
        host = parts.hostname
        port = parts.port
    except ValueError as exc:
        raise SourceError("The source URL has an invalid host or port.") from exc
    if parts.scheme not in {"http", "https"} or not host:
        raise SourceError("Only public http:// and https:// URLs are supported.")
    if parts.username is not None or parts.password is not None:
        raise SourceError("URLs containing usernames or passwords are not supported.")
    host = host.rstrip(".").lower()
    if not host or "%" in host or any(c.isspace() for c in host):
        raise SourceError("The source URL has an invalid host.")
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise SourceError("The source URL has an invalid host.") from exc
    if any(host == name or host.endswith("." + name) for name in _BLOCKED_HOSTS):
        raise SourceError("FINN and LinkedIn are manual-import sources. Paste the vacancy text instead.")
    return parts, host, port or (443 if parts.scheme == "https" else 80)


def _public_addresses(host: str, port: int) -> list[str]:
    try:
        addresses = list(dict.fromkeys(
            result[4][0] for result in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        ))
    except OSError as exc:
        raise SourceError(f"Cannot resolve {host}; check the URL and internet connection.") from exc
    if not addresses:
        raise SourceError(f"No network address was found for {host}.")
    for address in addresses:
        try:
            ip = ipaddress.ip_address(address)
        except ValueError as exc:
            raise SourceError("The source resolved to an invalid network address.") from exc
        mapped = getattr(ip, "ipv4_mapped", None)
        if not ip.is_global or (mapped is not None and not mapped.is_global):
            raise SourceError("Local, private, and reserved network destinations are not allowed.")
    return addresses


def _request_once(url: str, *, method: str = "GET", body: bytes | None = None) -> tuple[int, dict[str, str], bytes]:
    """Pin connections to checked IPs, including HTTPS hostname verification.

    No environment proxies are used. DNS is never resolved a second time when
    connecting, avoiding a DNS-check/connection gap.
    """
    if method not in {"GET", "POST"} or (body is not None and (not isinstance(body, bytes) or len(body) > 65536)):
        raise SourceError("Unsupported source request or oversized request body.")
    parts, host, port = _url_parts(url)
    addresses = _public_addresses(host, port)
    deadline = time.monotonic() + TIMEOUT_SECONDS

    def connect(_address, timeout=TIMEOUT_SECONDS, source_address=None):
        last_error = None
        for address in addresses:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Source request timed out")
            try:
                return socket.create_connection((address, port), remaining, source_address)
            except OSError as exc:
                last_error = exc
        raise last_error or OSError("Unable to connect")

    if parts.scheme == "https":
        connection = http.client.HTTPSConnection(
            host, port, timeout=TIMEOUT_SECONDS, context=ssl.create_default_context()
        )
    else:
        connection = http.client.HTTPConnection(host, port, timeout=TIMEOUT_SECONDS)
    connection._create_connection = connect
    path = quote(parts.path or "/", safe="/%:@!$&'()*+,;=-._~")
    if parts.query:
        path += "?" + quote(parts.query, safe="/%?:@!$&'()*+,;=-._~")
    try:
        connection.request(method, path, body=body, headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json, text/html, text/plain;q=0.9",
            "Accept-Encoding": "identity",
            **({"Content-Type": "application/json"} if body is not None else {}),
        })
        response = connection.getresponse()
        headers = {key.lower(): value for key, value in response.getheaders()}
        length = headers.get("content-length")
        if length and (not length.isdecimal() or int(length) > MAX_RESPONSE_BYTES):
            raise SourceError("The source response exceeds 2 MB or has an invalid Content-Length.")
        if headers.get("content-encoding", "identity").lower() not in {"", "identity"}:
            raise SourceError("The source returned unsupported compressed content.")
        chunks = []
        total = 0
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise SourceError("The source request timed out; try again later.")
            if connection.sock is not None:
                connection.sock.settimeout(remaining)
            chunk = response.read1(min(65536, MAX_RESPONSE_BYTES + 1 - total))
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_RESPONSE_BYTES:
                raise SourceError("The source response exceeds the 2 MB download limit.")
            chunks.append(chunk)
        return response.status, headers, b"".join(chunks)
    except SourceError:
        raise
    except (OSError, http.client.HTTPException, ValueError) as exc:
        raise SourceError(f"Could not download from {host}; check connectivity or try again later.") from exc
    finally:
        connection.close()


def _check_robots(url: str, cache: dict[str, RobotFileParser | None]) -> None:
    parts, _, _ = _url_parts(url)
    origin = urlunsplit((parts.scheme, parts.netloc, "", "", ""))
    if origin not in cache:
        status, _, body, _ = _fetch(origin + "/robots.txt", allow_missing=True)
        if status in {404, 410}:
            cache[origin] = None
        else:
            parser = RobotFileParser()
            rules = body.decode("utf-8-sig", errors="replace")
            # Some official career platforms serve robots.txt as a JSON string.
            # Interpret its actual lines so restrictions still take effect.
            if rules.lstrip().startswith('"'):
                try:
                    decoded = json.loads(rules)
                    if isinstance(decoded, str):
                        rules = decoded
                except (ValueError, RecursionError):
                    pass
            parser.parse(rules.splitlines())
            cache[origin] = parser
    parser = cache[origin]
    if parser is not None and not parser.can_fetch(USER_AGENT, url):
        raise SourceError("This page is disallowed by robots.txt. Paste the vacancy text instead.")


def _fetch(url: str, *, respect_robots: bool = False, allow_missing: bool = False,
           method: str = "GET", body: bytes | None = None):
    current = url
    robots_cache: dict[str, RobotFileParser | None] = {}
    for _ in range(MAX_REDIRECTS + 1):
        _url_parts(current)
        if respect_robots:
            _check_robots(current, robots_cache)
        if method == "GET" and body is None:
            status, headers, response_body = _request_once(current)
        else:
            status, headers, response_body = _request_once(current, method=method, body=body)
        if status in {301, 302, 303, 307, 308}:
            location = headers.get("location")
            if not location:
                raise SourceError("The source returned a redirect without a destination.")
            destination = urljoin(current, location)
            if method != "GET" and urlsplit(destination).netloc != urlsplit(current).netloc:
                raise SourceError("The listing request redirected to a different host; update the source URL.")
            if status in {301, 302, 303}:
                method, body = "GET", None
            current = destination
            continue
        if allow_missing and status in {404, 410}:
            return status, headers, response_body, current
        if not 200 <= status < 300:
            if status in {401, 403}:
                raise SourceError("The source denies automated access. Paste the vacancy text instead.")
            if status == 429:
                raise SourceError("The source is rate limiting requests; try again later.")
            raise SourceError(f"The source returned HTTP {status}; check the URL or board ID.")
        return status, headers, response_body, current
    raise SourceError("The source redirected too many times; use the final career page URL.")


def _json(url: str):
    _, _, body, _ = _fetch(url)
    try:
        return json.loads(body)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise SourceError("The source did not return valid JSON; its API may have changed.") from exc


def _board(value: str) -> str:
    if not isinstance(value, str) or not _BOARD.fullmatch(value):
        raise SourceError("Use the company board ID, containing only letters, numbers, underscores or hyphens.")
    return value


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.hidden += 1
        elif tag in {"p", "div", "br", "li", "h1", "h2", "h3"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.hidden = max(0, self.hidden - 1)
        elif tag in {"p", "div", "li", "h1", "h2", "h3"}:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def _plain(value) -> str:
    if not isinstance(value, str):
        return ""
    parser = _Text()
    # Greenhouse may encode its entire HTML fragment. Avoid decoding entities
    # inside an existing fragment first, which would turn literal <T> into tags.
    if "<" not in value and re.search(r"&lt;/?(?:p|div|ul|li|h[1-6]|br|span|strong|em)(?:&gt;|\s)", value, re.I):
        value = html.unescape(value)
    parser.feed(value)
    parser.close()
    return "\n".join(
        line for part in "".join(parser.parts).splitlines()
        if (line := " ".join(part.split()))
    ).strip()


def _link(value, base: str) -> str:
    if not isinstance(value, str) or not value.strip():
        return ""
    candidate = urljoin(base, value.strip())
    try:
        parts = urlsplit(candidate)
        if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username is not None:
            return ""
        return urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ""))
    except ValueError:
        return ""


def _job(**values) -> dict[str, str]:
    fields = ("title", "company", "location", "description", "source", "source_id",
              "source_url", "apply_url", "employment_type", "published_at", "deadline")
    return {key: str(values.get(key) or "").strip() for key in fields}


def fetch_greenhouse(board: str) -> list[dict]:
    """Fetch one employer's published jobs; no API key is required."""
    board = _board(board)
    base = f"https://boards-api.greenhouse.io/v1/boards/{board}"
    metadata = _json(base)
    payload = _json(base + "/jobs?content=true")
    if not isinstance(metadata, dict) or not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
        raise SourceError("Unexpected Greenhouse response; check the board ID.")
    company = _plain(metadata.get("name")) or board
    jobs = []
    for item in payload["jobs"]:
        if not isinstance(item, dict) or not item.get("id") or not _plain(item.get("title")):
            raise SourceError("Greenhouse returned an incomplete job record.")
        location = item.get("location") or {}
        link = _link(item.get("absolute_url"), base)
        jobs.append(_job(
            title=_plain(item.get("title")), company=company,
            location=_plain(location.get("name")) if isinstance(location, dict) else _plain(location),
            description=_plain(item.get("content")), source="greenhouse",
            source_id=f"{board}:{item['id']}", source_url=link, apply_url=link,
            published_at=item.get("first_published") or item.get("published_at"),
            deadline=_date(item.get("application_deadline")),
        ))
    return jobs


def _date(value) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return datetime.fromtimestamp(value / 1000 if value > 10**11 else value, timezone.utc).isoformat()
        except (ValueError, OverflowError, OSError):
            pass
    return ""


def fetch_lever(board: str, region: str = "global") -> list[dict]:
    """Fetch one employer's published jobs; company uses the Lever site ID."""
    board = _board(board)
    if region not in {"global", "eu"}:
        raise SourceError("Lever region must be 'global' or 'eu'.")
    domain = "api.lever.co" if region == "global" else "api.eu.lever.co"
    base = f"https://{domain}/v0/postings/{board}"
    jobs = []
    seen = set()
    for page in range(100):
        payload = _json(f"{base}?mode=json&limit=100&skip={page * 100}")
        if not isinstance(payload, list):
            raise SourceError("Unexpected Lever response; check the board ID and region.")
        for item in payload:
            if not isinstance(item, dict) or not item.get("id") or not _plain(item.get("text")):
                raise SourceError("Lever returned an incomplete job record.")
            if not isinstance(item["id"], (str, int)):
                raise SourceError("Lever returned an invalid job identifier.")
            if item["id"] in seen:
                raise SourceError("Lever repeated jobs across pages; retry the import later.")
            seen.add(item["id"])
            categories = item.get("categories") or {}
            if not isinstance(categories, dict):
                categories = {}
            sections = [item.get("descriptionPlain") or item.get("description") or ""]
            lists = item.get("lists") or []
            if not isinstance(lists, list):
                raise SourceError("Lever returned an invalid description section.")
            for section in lists:
                if isinstance(section, dict):
                    sections.extend([section.get("text") or "", section.get("content") or ""])
            sections.append(item.get("additionalPlain") or item.get("additional") or "")
            jobs.append(_job(
                title=_plain(item.get("text")), company=board,
                location=_plain(categories.get("location")),
                description="\n\n".join(filter(None, (_plain(section) for section in sections))),
                source="lever", source_id=f"{region}:{board}:{item['id']}",
                source_url=_link(item.get("hostedUrl"), base),
                apply_url=_link(item.get("applyUrl") or item.get("hostedUrl"), base),
                employment_type=_plain(categories.get("commitment")),
                published_at=_date(item.get("createdAt")),
            ))
        if len(payload) < 100:
            return jobs
    raise SourceError("Lever pagination exceeded the import limit; no partial import was returned.")


class _LinkedData(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.active = False
        self.parts: list[str] = []
        self.scripts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "script":
            self.active = (dict(attrs).get("type") or "").lower().split(";", 1)[0].strip() == "application/ld+json"
            self.parts = []

    def handle_data(self, data):
        if self.active:
            self.parts.append(data)

    def handle_endtag(self, tag):
        if tag == "script" and self.active:
            self.scripts.append("".join(self.parts))
            self.active = False


def _postings(value, depth=0):
    if depth > 50:
        return
    if isinstance(value, dict):
        types = value.get("@type", [])
        if isinstance(types, str):
            types = [types]
        if isinstance(types, list) and any(
            kind in {"JobPosting", "https://schema.org/JobPosting", "http://schema.org/JobPosting"}
            for kind in types if isinstance(kind, str)
        ):
            yield value
        for nested in value.values():
            if isinstance(nested, (dict, list)):
                yield from _postings(nested, depth + 1)
    elif isinstance(value, list):
        for nested in value:
            yield from _postings(nested, depth + 1)


def _name(value) -> str:
    return _plain(value.get("name")) if isinstance(value, dict) else _plain(value)


def _location(value) -> str:
    if isinstance(value, list):
        return "; ".join(filter(None, (_location(part) for part in value)))
    if not isinstance(value, dict):
        return _plain(value)
    address = value.get("address", value)
    if not isinstance(address, dict):
        return _plain(address)
    parts = [_name(address.get(key)) for key in ("addressLocality", "addressRegion", "addressCountry")]
    return ", ".join(dict.fromkeys(filter(None, parts))) or _name(value)


def fetch_job_url(url: str) -> list[dict]:
    """Read JobPosting JSON-LD on one public page, observing robots rules.

    No JavaScript is executed, links are not crawled, and access restrictions
    are not bypassed. If structured data is absent, use manual text import.
    """
    _, _, body, final_url = _fetch(url, respect_robots=True)
    parser = _LinkedData()
    parser.feed(body.decode("utf-8-sig", errors="replace"))
    jobs = []
    seen = set()
    for script in parser.scripts:
        try:
            data = json.loads(script)
        except (ValueError, RecursionError):
            continue
        for item in _postings(data):
            title = _plain(item.get("title"))
            if not title:
                continue
            canonical = _link(item.get("url"), final_url) or final_url
            identifier = item.get("identifier")
            if isinstance(identifier, dict):
                identifier = identifier.get("value") or identifier.get("@id")
            identifier = str(identifier) if isinstance(identifier, (str, int)) else ""
            key = (identifier, canonical, title)
            if key in seen:
                continue
            seen.add(key)
            employment = item.get("employmentType") or ""
            if isinstance(employment, list):
                employment = ", ".join(value for value in employment if isinstance(value, str))
            location = _location(item.get("jobLocation"))
            if item.get("jobLocationType") == "TELECOMMUTE":
                location = "Remote" + (f" ({location})" if location else "")
            jobs.append(_job(
                title=title, company=_name(item.get("hiringOrganization")), location=location,
                description=_plain(item.get("description")), source="career_page",
                source_id=f"{canonical}#{identifier}" if identifier else canonical,
                source_url=canonical, apply_url=canonical,
                employment_type=employment, published_at=item.get("datePosted"),
                deadline=item.get("validThrough"),
            ))
    if not jobs:
        raise SourceError("No JobPosting structured data was found. Paste the vacancy text for manual import.")
    return jobs
