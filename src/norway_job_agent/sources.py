"""Read-only public job sources. No browser automation or application submission.

NAV's incremental feed lives in nav.py and nav_index.py; market-wide job-board
searches live in market.py. FINN forbids automated collection, and LinkedIn is
reachable only through the opt-in public search in linkedin_public.py; pasted
links from either still require manual import.
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
from itertools import combinations

from .countries import country_code

MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_ATS_RESPONSE_BYTES = 20 * 1024 * 1024
TIMEOUT_SECONDS = 15
MAX_REDIRECTS = 5
RETRY_PAUSE_SECONDS = 1.0
USER_AGENT = "PersonalJobAgent/0.5"
_BOARD = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,99}\Z")
_BLOCKED_HOSTS = ("linkedin.com", "finn.no")


class SourceError(ValueError):
    """A source could not be read; safe to show this message to the user."""


class _TransientSourceError(SourceError):
    """A connection-level failure (reset, timeout); an idempotent GET may retry."""


def _url_parts(url: str, *, allow_restricted: bool = False):
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
    if not allow_restricted and any(host == name or host.endswith("." + name) for name in _BLOCKED_HOSTS):
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


def _request_once(url: str, *, method: str = "GET", body: bytes | None = None,
                  max_response_bytes: int = MAX_RESPONSE_BYTES, headers: dict[str, str] | None = None,
                  allow_restricted: bool = False) -> tuple[int, dict[str, str], bytes]:
    """Pin connections to checked IPs, including HTTPS hostname verification.

    No environment proxies are used. DNS is never resolved a second time when
    connecting, avoiding a DNS-check/connection gap. Extra headers may add an
    API key or Accept type, never replace the agent's honest User-Agent.
    """
    if method not in {"GET", "POST"} or (body is not None and (not isinstance(body, bytes) or len(body) > 65536)):
        raise SourceError("Unsupported source request or oversized request body.")
    if isinstance(max_response_bytes, bool) or not isinstance(max_response_bytes, int) or not 1 <= max_response_bytes <= MAX_ATS_RESPONSE_BYTES:
        raise SourceError("Invalid source download budget.")
    extra = dict(headers or {})
    if any(not isinstance(key, str) or not isinstance(value, str) or key.casefold() in {"user-agent", "host", "content-length"}
           or any(ord(char) < 32 for char in key + value) for key, value in extra.items()):
        raise SourceError("Invalid source request header.")
    budget_label = f"{max_response_bytes / (1024 * 1024):g} MB"
    parts, host, port = _url_parts(url, allow_restricted=allow_restricted)
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
            **extra,
        })
        response = connection.getresponse()
        headers = {key.lower(): value for key, value in response.getheaders()}
        length = headers.get("content-length")
        if length and (not length.isdecimal() or int(length) > max_response_bytes):
            raise SourceError(f"The source response exceeds {budget_label} or has an invalid Content-Length.")
        if headers.get("content-encoding", "identity").lower() not in {"", "identity"}:
            raise SourceError("The source returned unsupported compressed content.")
        chunks = []
        total = 0
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise _TransientSourceError("The source request timed out; try again later.")
            if connection.sock is not None:
                connection.sock.settimeout(remaining)
            chunk = response.read1(min(65536, max_response_bytes + 1 - total))
            if not chunk:
                break
            total += len(chunk)
            if total > max_response_bytes:
                raise SourceError(f"The source response exceeds the {budget_label} download limit.")
            chunks.append(chunk)
        return response.status, headers, b"".join(chunks)
    except SourceError:
        raise
    except (OSError, http.client.HTTPException) as exc:
        raise _TransientSourceError(f"Could not download from {host}; check connectivity or try again later.") from exc
    except ValueError as exc:
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


def _request_retrying(url: str, method: str, body: bytes | None, max_response_bytes: int,
                      headers: dict[str, str] | None = None, allow_restricted: bool = False):
    """One request; a GET that failed at the connection level is retried once.

    A single dropped connection previously failed a whole multi-page career
    scan. HTTP status responses, including 429 and 5xx, are never retried, and
    neither are POST listing searches.
    """
    for attempt in range(2):
        try:
            if headers or allow_restricted:
                return _request_once(url, method=method, body=body, max_response_bytes=max_response_bytes,
                                     headers=headers, allow_restricted=allow_restricted)
            if max_response_bytes != MAX_RESPONSE_BYTES:
                return _request_once(url, method=method, body=body, max_response_bytes=max_response_bytes)
            if method == "GET" and body is None:
                return _request_once(url)
            return _request_once(url, method=method, body=body)
        except _TransientSourceError:
            if attempt or method != "GET":
                raise
            time.sleep(RETRY_PAUSE_SECONDS)


def _fetch(url: str, *, respect_robots: bool = False, allow_missing: bool = False,
           method: str = "GET", body: bytes | None = None, max_response_bytes: int = MAX_RESPONSE_BYTES,
           headers: dict[str, str] | None = None, allow_restricted: bool = False):
    current = url
    robots_cache: dict[str, RobotFileParser | None] = {}
    request_headers = headers
    for _ in range(MAX_REDIRECTS + 1):
        _url_parts(current, allow_restricted=allow_restricted)
        if respect_robots:
            _check_robots(current, robots_cache)
        status, headers, response_body = _request_retrying(current, method, body, max_response_bytes,
                                                           request_headers, allow_restricted)
        if status in {301, 302, 303, 307, 308}:
            location = headers.get("location")
            if not location:
                raise SourceError("The source returned a redirect without a destination.")
            destination = urljoin(current, location)
            if method != "GET" and urlsplit(destination).netloc != urlsplit(current).netloc:
                raise SourceError("The listing request redirected to a different host; update the source URL.")
            if urlsplit(destination).netloc != urlsplit(current).netloc:
                request_headers = None  # API keys belong to the original host.
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


def _json(url: str, *, max_response_bytes: int = MAX_RESPONSE_BYTES):
    kwargs = {"max_response_bytes": max_response_bytes} if max_response_bytes != MAX_RESPONSE_BYTES else {}
    _, _, body, _ = _fetch(url, **kwargs)
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


def _set_location_countries(job, locations):
    """Retain structured countries; infer only locations without that metadata."""
    from .countries import job_countries
    codes, evidence = [], []
    for location, country in locations:
        country = _plain(country.get("name") or country.get("code")) if isinstance(country, dict) else _plain(country)
        evidence.append(country)
        if country:
            if code := country_code(country):
                codes.append(code)
        else:
            codes.extend(job_countries({"location": location}))
    if codes:
        job["countries"] = list(dict.fromkeys(codes))
    # Also persist incomplete evidence so a refreshed posting does not retain
    # a previous snapshot's structured country after its locations change.
    job["raw_json"] = {"location_country_evidence": evidence,
                       "resolved_location_countries": list(dict.fromkeys(codes))}
    return job


def source_location_terms(source: dict) -> list[str]:
    """Keep user location filters, but retire redundant legacy country city lists."""
    terms = source.get("locations", [])
    if not isinstance(terms, list) or any(not isinstance(term, str) or not term.strip() for term in terms):
        raise ValueError("A source's locations filter must be a list of nonempty location names.")
    if source.get("countries") and terms:
        from .countries import COUNTRY_TERMS
        from .company_catalog import NORWAY_LOCATIONS
        terms_set = {term.casefold() for term in terms}
        if terms_set == {term.casefold() for term in NORWAY_LOCATIONS}:
            return []
        groups = list(COUNTRY_TERMS.values())
        for size in range(1, len(groups) + 1):
            for selected in combinations(groups, size):
                if terms_set == {term.casefold() for group in selected for term in group}:
                    return []
    return terms


def geographic_rejection(job: dict, source: dict, *, location_terms=None) -> str | None:
    from .countries import job_countries, validate_countries
    terms = source_location_terms(source) if location_terms is None else location_terms
    if terms and not any(term.casefold() in str(job.get("location", "")).casefold() for term in terms):
        return "filtered_location"
    if source.get("countries"):
        requested = validate_countries(source["countries"])
        countries = job_countries(job)
        if not countries:
            return "unknown_country"
        if not set(countries).intersection(requested):
            return "filtered_country"
    return None


def fetch_greenhouse(board: str) -> list[dict]:
    """Fetch one employer's published jobs; no API key is required."""
    board = _board(board)
    base = f"https://boards-api.greenhouse.io/v1/boards/{board}"
    metadata = _json(base)
    payload = _json(base + "/jobs?content=true", max_response_bytes=MAX_ATS_RESPONSE_BYTES)
    if not isinstance(metadata, dict) or not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
        raise SourceError("Unexpected Greenhouse response; check the board ID.")
    company = _plain(metadata.get("name")) or board
    total = (payload.get("meta") or {}).get("total") if isinstance(payload.get("meta") or {}, dict) else None
    if total is not None and (isinstance(total, bool) or not isinstance(total, int) or total != len(payload["jobs"])):
        raise SourceError("Greenhouse returned an incomplete board snapshot; retry later.")
    jobs = []
    seen = set()
    for item in payload["jobs"]:
        if not isinstance(item, dict) or isinstance(item.get("id"), bool) or not isinstance(item.get("id"), (str, int)) or not str(item["id"]).strip() or not _plain(item.get("title")):
            raise SourceError("Greenhouse returned an incomplete job record.")
        identifier = str(item["id"])
        if identifier in seen:
            raise SourceError("Greenhouse repeated a job in its board snapshot; retry later.")
        seen.add(identifier)
        jobs.append(_greenhouse_job(item, board, company, base))
    return jobs


def _greenhouse_job(item: dict, board: str, company: str, base: str) -> dict:
    location = item.get("location") or {}
    locations = [_plain(location.get("name")) if isinstance(location, dict) else _plain(location)]
    offices = item.get("offices") or []
    if isinstance(offices, list):
        locations.extend(_plain(office.get("location")) for office in offices if isinstance(office, dict))
    link = _link(item.get("absolute_url"), base)
    return _job(
        title=_plain(item.get("title")), company=company,
        location="; ".join(dict.fromkeys(filter(None, locations))),
        description=_plain(item.get("content")), source="greenhouse",
        source_id=f"{board}:{item['id']}", source_url=link, apply_url=link,
        published_at=item.get("first_published") or item.get("published_at"),
        deadline=_date(item.get("application_deadline")),
    )


_GREENHOUSE_HOSTS = frozenset({"boards.greenhouse.io", "job-boards.greenhouse.io", "boards.eu.greenhouse.io", "job-boards.eu.greenhouse.io"})


def fetch_greenhouse_job(board: str, job_id: str) -> dict:
    """Read one posting through Greenhouse's public API.

    Greenhouse job pages carry no JobPosting data, so pasted Greenhouse links
    previously failed to import. The record matches board collection exactly,
    so a later board scan updates it instead of creating a duplicate.
    """
    board = _board(board)
    if not isinstance(job_id, str) or not job_id.isdecimal():
        raise SourceError("Use a Greenhouse job link ending in /jobs/<number>.")
    base = f"https://boards-api.greenhouse.io/v1/boards/{board}"
    metadata = _json(base)
    item = _json(f"{base}/jobs/{job_id}")
    if not isinstance(metadata, dict) or not isinstance(item, dict) or str(item.get("id")) != job_id or not _plain(item.get("title")):
        raise SourceError("Greenhouse did not return this job; it may have closed.")
    return _greenhouse_job(item, board, _plain(metadata.get("name")) or board, base)


def _ats_job_link(url: str):
    """(provider, board, identifier) for a direct ATS job link, else None."""
    parts, host, _ = _url_parts(url)
    if host in _GREENHOUSE_HOSTS:
        match = re.fullmatch(r"/([A-Za-z0-9_-]+)/jobs/(\d+)/?", parts.path)
        if match:
            return "greenhouse", match[1], match[2]
    if host == "jobs.ashbyhq.com":
        match = re.fullmatch(r"/([A-Za-z0-9_.-]+)/([0-9a-f-]{36})(?:/application)?/?", parts.path)
        if match:
            return "ashby", match[1], match[2]
    return None


def _date(value) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return datetime.fromtimestamp(value / 1000 if value > 10**11 else value, timezone.utc).isoformat()
        except (ValueError, OverflowError, OSError):
            pass
    return ""


def fetch_ashby(board: str, name: str = "") -> list[dict]:
    """Read Ashby's public job board feed, including its structured locations."""
    board = _board(board)
    base = f"https://api.ashbyhq.com/posting-api/job-board/{board}"
    payload = _json(base, max_response_bytes=MAX_ATS_RESPONSE_BYTES)
    if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
        raise SourceError("Unexpected Ashby response; check the board ID.")
    jobs, seen = [], set()
    for item in payload["jobs"]:
        if not isinstance(item, dict):
            raise SourceError("Ashby returned an incomplete job record.")
        if item.get("isListed") is False:
            continue
        link = _link(item.get("jobUrl"), base)
        title = _plain(item.get("title"))
        if not title or not link:
            raise SourceError("Ashby returned an incomplete public job record.")
        if link in seen:
            continue
        seen.add(link)
        address = item.get("address")
        if address is None:
            address = {}
        if not isinstance(address, dict):
            raise SourceError("Ashby returned invalid address metadata.")
        locations = [_plain(item.get("location"))]
        locations.append(_location(address.get("postalAddress") or address))
        additional = item.get("secondaryLocations")
        if additional is None:
            additional = []
        if not isinstance(additional, list):
            raise SourceError("Ashby returned invalid secondary locations.")
        for other in additional:
            if not isinstance(other, dict):
                raise SourceError("Ashby returned invalid secondary location metadata.")
            other_address = other.get("address") or {}
            if not isinstance(other_address, dict):
                raise SourceError("Ashby returned invalid secondary address metadata.")
            locations.extend([_plain(other.get("location")), _location(other_address.get("postalAddress") or other_address)])
        if item.get("isRemote"):
            locations.insert(0, "Remote")
        job = _job(title=title, company=_plain(name) or board,
            location="; ".join(dict.fromkeys(filter(None, locations))),
            description=_plain(item.get("descriptionHtml") or item.get("descriptionPlain")),
            source="ashby", source_id=f"{board}:{urlsplit(link).path.rstrip('/').rsplit('/', 1)[-1]}",
            source_url=link, apply_url=_link(item.get("applyUrl"), link) or link,
            employment_type=_plain(item.get("employmentType")), published_at=item.get("publishedAt"))
        addresses = [address.get("postalAddress") or address]
        addresses.extend((other.get("address") or {}).get("postalAddress") or other.get("address") for other in additional)
        location_rows = [item, *additional]
        _set_location_countries(job, [
            (_plain(row.get("location")), value.get("addressCountry") if isinstance(value, dict) else None)
            for row, value in zip(location_rows, addresses)
        ])
        jobs.append(job)
    return jobs


def fetch_lever(board: str, region: str = "global", name: str = "") -> list[dict]:
    """Fetch one employer's published jobs.

    Lever postings do not include the employer name; use the configured name
    and fall back to the Lever site ID.
    """
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
                title=_plain(item.get("text")), company=_plain(name) or board,
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
    Direct Greenhouse and Ashby job links are read through their public APIs.
    """
    ats = _ats_job_link(url)
    if ats and ats[0] == "greenhouse":
        return [fetch_greenhouse_job(ats[1], ats[2])]
    if ats and ats[0] == "ashby":
        wanted = ats[2].lower()
        matches = [job for job in fetch_ashby(ats[1]) if job["source_url"].rstrip("/").lower().endswith(wanted)]
        if not matches:
            raise SourceError("Ashby did not list this job; it may have closed.")
        return matches
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
            job = _job(
                title=title, company=_name(item.get("hiringOrganization")), location=location,
                description=_plain(item.get("description")), source="career_page",
                source_id=f"{canonical}#{identifier}" if identifier else canonical,
                source_url=canonical, apply_url=canonical,
                employment_type=employment, published_at=item.get("datePosted"),
                deadline=item.get("validThrough"),
            )
            locations = item.get("jobLocation") or []
            if not isinstance(locations, list):
                locations = [locations]
            rows = []
            for value in locations:
                address = value.get("address", value) if isinstance(value, dict) else {}
                country = address.get("addressCountry") if isinstance(address, dict) else None
                rows.append((_location(value), country))
            if item.get("jobLocationType") == "TELECOMMUTE":
                requirements = item.get("applicantLocationRequirements") or []
                if not isinstance(requirements, list):
                    requirements = [requirements]
                for value in requirements:
                    if isinstance(value, dict) and value.get("@type") == "Country":
                        rows.append((_name(value), value))
            _set_location_countries(job, rows)
            jobs.append(job)
    if not jobs:
        raise SourceError("No JobPosting structured data was found. Paste the vacancy text for manual import.")
    return jobs
