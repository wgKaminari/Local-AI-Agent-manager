"""Bounded, resumable reads of NAV's public vacancy change feed.

The first batch starts 90 days back by default, not at the beginning of the
market. Callers must apply jobs and withdrawals before atomically persisting
the returned state. Input state is never mutated, including on failures.

NAV excludes FINN-origin ads from this feed. Its rotating public token is for
experiments; NAV_API_TOKEN can supply a separately registered private token.
Documentation: https://navikt.github.io/pam-stilling-feed/
"""

from __future__ import annotations

import http.client
import json
import os
import re
import socket
import ssl
import time
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from urllib.parse import quote, urljoin, urlsplit, urlunsplit
from uuid import UUID

from .sources import (
    MAX_RESPONSE_BYTES, TIMEOUT_SECONDS, USER_AGENT, SourceError,
    _job, _link, _plain, _public_addresses,
)

NAV_HOST = "pam-stilling-feed.nav.no"
NAV_ORIGIN = "https://" + NAV_HOST
FEED_URL = NAV_ORIGIN + "/api/v1/feed"
PUBLIC_TOKEN_URL = NAV_ORIGIN + "/api/publicToken"


def _nav_url(value: str, base: str = NAV_ORIGIN + "/") -> str:
    if not isinstance(value, str) or not value or any(ord(char) < 32 for char in value):
        raise SourceError("NAV returned an invalid feed URL; the checkpoint was not advanced.")
    try:
        parts = urlsplit(urljoin(base, value))
        allowed = (parts.scheme == "https" and parts.hostname == NAV_HOST
                   and parts.port in {None, 443} and parts.username is None and parts.password is None)
    except ValueError as exc:
        raise SourceError("NAV returned an invalid feed URL; the checkpoint was not advanced.") from exc
    if not allowed:
        raise SourceError("NAV requests must stay on https://pam-stilling-feed.nav.no; no credentials were sent to another host.")
    return urlunsplit(("https", NAV_HOST, parts.path or "/", parts.query, ""))


def _header(value) -> str:
    if not isinstance(value, str) or any(ord(char) < 32 or ord(char) > 126 for char in value):
        raise SourceError("NAV returned an invalid cache header; the checkpoint was not advanced.")
    return value


def _valid_token(value) -> str:
    if not isinstance(value, str):
        raise SourceError("NAV did not provide a valid access token.")
    value = value.strip()
    if len(value) > 16384 or not re.fullmatch(r"[A-Za-z0-9._~+/-]+=*", value):
        raise SourceError("NAV did not provide a valid access token; check NAV_API_TOKEN or retry later.")
    return value


def _request_nav_once(url: str, token: str | None, headers: dict[str, str]):
    """Authenticated transport isolated to one origin and validated public IPs."""
    url = _nav_url(url)  # Must occur before constructing any bearer header.
    parts = urlsplit(url)
    addresses = _public_addresses(NAV_HOST, 443)
    deadline = time.monotonic() + TIMEOUT_SECONDS

    def connect(_address, timeout=TIMEOUT_SECONDS, source_address=None):
        last_error = None
        for address in addresses:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("NAV request timed out")
            try:
                return socket.create_connection((address, 443), remaining, source_address)
            except OSError as exc:
                last_error = exc
        raise last_error or OSError("Cannot connect to NAV")

    request_headers = {"User-Agent": USER_AGENT, "Accept": "application/json, text/plain",
                       "Accept-Encoding": "identity"}
    for key in ("If-None-Match", "If-Modified-Since"):
        if headers.get(key):
            request_headers[key] = _header(headers[key])
    if token is not None:
        request_headers["Authorization"] = "Bearer " + _valid_token(token)
    connection = http.client.HTTPSConnection(
        NAV_HOST, 443, timeout=TIMEOUT_SECONDS, context=ssl.create_default_context()
    )
    connection._create_connection = connect
    path = quote(parts.path, safe="/%:@!$&'()*+,;=-._~")
    if parts.query:
        path += "?" + quote(parts.query, safe="/%?:@!$&'()*+,;=-._~")
    try:
        connection.request("GET", path, headers=request_headers)
        response = connection.getresponse()
        response_headers = {key.lower(): value for key, value in response.getheaders()}
        length = response_headers.get("content-length")
        if length and (not length.isdecimal() or int(length) > MAX_RESPONSE_BYTES):
            raise SourceError("NAV response exceeds 2 MB or has an invalid Content-Length.")
        if response_headers.get("content-encoding", "identity").lower() not in {"", "identity"}:
            raise SourceError("NAV returned unsupported compressed content; try again later.")
        chunks = []
        total = 0
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise SourceError("NAV request timed out; the checkpoint was not advanced.")
            if connection.sock is not None:
                connection.sock.settimeout(remaining)
            chunk = response.read1(min(65536, MAX_RESPONSE_BYTES + 1 - total))
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_RESPONSE_BYTES:
                raise SourceError("NAV response exceeds the 2 MB download limit.")
            chunks.append(chunk)
        return response.status, response_headers, b"".join(chunks)
    except SourceError:
        raise
    except (OSError, ValueError, http.client.HTTPException) as exc:
        raise SourceError("Could not download from NAV; check connectivity or try again later.") from exc
    finally:
        connection.close()


def _fetch_nav(url: str, token: str | None, headers: dict[str, str] | None = None):
    current = _nav_url(url)
    conditions = dict(headers or {})
    for _ in range(6):
        status, response_headers, body = _request_nav_once(current, token, conditions)
        if status in {301, 302, 303, 307, 308}:
            current = _nav_url(response_headers.get("location", ""), current)
            conditions = {}  # Validators belong to the original resource.
            continue
        if status == 304:
            return status, response_headers, body, current
        if status != 200:
            if status in {401, 403}:
                raise SourceError("NAV rejected the access token. Retry if using the experimental token, or check NAV_API_TOKEN.")
            if status == 429:
                raise SourceError("NAV is rate limiting requests; retry later. The checkpoint was not advanced.")
            raise SourceError(f"NAV returned HTTP {status}; the checkpoint was not advanced.")
        return status, response_headers, body, current
    raise SourceError("NAV redirected too many times; the checkpoint was not advanced.")


def _decode(body: bytes):
    try:
        return json.loads(body)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise SourceError("NAV returned invalid JSON; the checkpoint was not advanced.") from exc


def _get_token(explicit: str | None) -> tuple[str, bool]:
    supplied = explicit if explicit is not None else os.environ.get("NAV_API_TOKEN")
    if supplied:
        return _valid_token(supplied), False
    status, _, body, _ = _fetch_nav(PUBLIC_TOKEN_URL, None)
    if status != 200:
        raise SourceError("NAV's experimental token is unavailable; try again later.")
    try:
        value = body.decode("utf-8-sig").strip()
    except UnicodeError as exc:
        raise SourceError("NAV's experimental token response was invalid.") from exc
    if value.startswith(('"', "{")):
        value = _decode(body)
        if isinstance(value, dict):
            value = value.get("token") or value.get("access_token")
    elif value.startswith("Current public token for Nav Job Vacancy Feed:"):
        # The documented endpoint returns a heading followed by the rotating
        # JWT, not just the JWT itself. Never log or persist the response.
        lines = value.splitlines()
        candidates = [line.strip() for line in lines[1:] if line.strip()]
        if len(candidates) != 1:
            raise SourceError("NAV's experimental token response was unexpected; retry later.")
        value = candidates[0]
    return _valid_token(value), True


def _uuid(value) -> str:
    try:
        return str(UUID(str(value)))
    except (ValueError, AttributeError) as exc:
        raise SourceError("NAV returned an invalid vacancy ID; the checkpoint was not advanced.") from exc


def _matches(item: dict, keywords: list[str]) -> bool:
    if not keywords:
        return True
    entry = item["_feed_entry"]
    values = [entry.get("title"), item.get("title"), item.get("content_text"), entry.get("jobtitle")]
    for container in (item, entry):
        for key in ("occupationCategories", "categoryList"):
            for category in container.get(key) or []:
                if isinstance(category, dict):
                    values.extend(category.get(field) for field in ("level1", "level2", "name", "description"))
    text = " ".join(value for value in values if isinstance(value, str)).casefold()
    for keyword in keywords:
        # Short acronyms such as AI/ML must not match inside unrelated words.
        if len(keyword) <= 3:
            if re.search(r"(?<!\w)" + re.escape(keyword) + r"(?!\w)", text):
                return True
        elif keyword in text:
            return True
    return False


def _normalize(uuid: str, payload: dict) -> dict | None:
    if not isinstance(payload, dict) or payload.get("status") not in {"ACTIVE", "INACTIVE"}:
        raise SourceError("NAV returned an invalid vacancy status; the checkpoint was not advanced.")
    if payload.get("uuid") is not None and _uuid(payload["uuid"]) != uuid:
        raise SourceError("NAV returned a mismatched vacancy ID; the checkpoint was not advanced.")
    if payload["status"] == "INACTIVE":
        return None
    # The reference page calls this field json; its pseudocode uses ad_content.
    content = payload.get("ad_content")
    if content is None:
        content = payload.get("json")
    if not isinstance(content, dict):
        raise SourceError("NAV returned missing vacancy details; the checkpoint was not advanced.")
    if content.get("uuid") is not None and _uuid(content["uuid"]) != uuid:
        raise SourceError("NAV returned a mismatched vacancy ID; the checkpoint was not advanced.")
    employer = content.get("employer") or {}
    title = _plain(content.get("title") or content.get("jobtitle"))
    company = _plain(employer.get("name")) if isinstance(employer, dict) else ""
    if not title or not company:
        raise SourceError("NAV returned an active vacancy without a title or employer; the checkpoint was not advanced.")
    locations = []
    for location in content.get("workLocations") or []:
        if isinstance(location, dict):
            fields = [location.get("city") or location.get("municipal"), location.get("county"), location.get("country")]
            formatted = ", ".join(dict.fromkeys(_plain(value) for value in fields if _plain(value)))
            if formatted and formatted not in locations:
                locations.append(formatted)
    source_url = f"https://arbeidsplassen.nav.no/stillinger/stilling/{uuid}"
    apply_url = (_link(content.get("applicationUrl"), source_url)
                 or _link(content.get("sourceurl"), source_url) or source_url)
    return _job(
        title=title, company=company, location="; ".join(locations),
        description=_plain(content.get("description")), source="nav", source_id=uuid,
        source_url=source_url, apply_url=apply_url,
        employment_type=_plain(content.get("engagementtype")),
        published_at=content.get("published"), deadline=content.get("applicationDue"),
    )


def fetch_nav_batch(
    state: dict, keywords: list[str], known_ids: list[str] | set[str] = (),
    token: str | None = None, max_pages: int = 5, lookback_days: int = 90,
) -> dict:
    """Return jobs, withdrawals and a candidate checkpoint without writing files.

    ``has_more`` means the initial/change backlog still has another page. False
    means the current tail was reached, not that the Norwegian market is fully
    covered. Keywords filter only new active vacancies, never known IDs or
    withdrawal events. A new keyword set needs an explicitly reset checkpoint
    to revisit previously skipped historical events.
    """
    if not isinstance(state, dict):
        raise SourceError("NAV checkpoint must be a JSON object.")
    if isinstance(max_pages, bool) or not isinstance(max_pages, int) or not 1 <= max_pages <= 100:
        raise SourceError("NAV max_pages must be between 1 and 100.")
    if isinstance(lookback_days, bool) or not isinstance(lookback_days, int) or not 1 <= lookback_days <= 180:
        raise SourceError("NAV lookback_days must be between 1 and 180.")
    if not isinstance(keywords, list) or any(not isinstance(word, str) for word in keywords):
        raise SourceError("NAV keywords must be a list of words or phrases.")
    keywords = list(dict.fromkeys(word.casefold().strip() for word in keywords if word.strip()))
    if not isinstance(known_ids, (list, set, tuple)):
        raise SourceError("Known NAV vacancy IDs must be a list or set.")
    known = {_uuid(value) for value in known_ids}
    if state.get("version", 1) != 1:
        raise SourceError("Unsupported NAV checkpoint version; reset the NAV checkpoint explicitly.")
    initial_since = state.get("initial_since") or format_datetime(
        datetime.now(timezone.utc) - timedelta(days=lookback_days), usegmt=True
    )
    candidate = {
        "version": 1, "url": _nav_url(state.get("url") or FEED_URL),
        "etag": _header(state.get("etag", "")),
        "last_modified": _header(state.get("last_modified", "")),
        "initial_since": _header(initial_since),
    }
    if not state.get("url"):
        candidate["last_modified"] = candidate["initial_since"]
    access_token, experimental = _get_token(token)
    events: dict[str, dict] = {}
    pages = seen = 0
    has_more = False
    visited = set()
    for _ in range(max_pages):
        current = candidate["url"]
        if current in visited:
            raise SourceError("NAV repeated a feed page; the checkpoint was not advanced.")
        visited.add(current)
        conditions = {}
        if candidate["etag"]:
            conditions["If-None-Match"] = candidate["etag"]
        if candidate["last_modified"]:
            conditions["If-Modified-Since"] = candidate["last_modified"]
        status, response_headers, body, final_url = _fetch_nav(current, access_token, conditions)
        pages += 1
        if status == 304:
            candidate["url"] = _nav_url(final_url)
            for field, header in (("etag", "etag"), ("last_modified", "last-modified")):
                if header in response_headers:
                    candidate[field] = _header(response_headers[header])
            has_more = False
            break
        page = _decode(body)
        if not isinstance(page, dict) or not isinstance(page.get("items"), list):
            raise SourceError("NAV returned an invalid feed page; the checkpoint was not advanced.")
        page_url = _nav_url(page.get("feed_url") or final_url, final_url)
        for item in page["items"]:
            seen += 1
            if not isinstance(item, dict) or not isinstance(item.get("_feed_entry"), dict):
                raise SourceError("NAV returned an invalid feed entry; the checkpoint was not advanced.")
            entry = item["_feed_entry"]
            uuid = _uuid(entry.get("uuid"))
            if entry.get("status") not in {"ACTIVE", "INACTIVE"}:
                raise SourceError("NAV returned an invalid feed status; the checkpoint was not advanced.")
            detail_url = _nav_url(item.get("url", ""), page_url)
            events[uuid] = {"item": item, "url": detail_url}
        next_url = page.get("next_url")
        if next_url:
            candidate.update(url=_nav_url(next_url, page_url), etag="", last_modified="")
            has_more = True
        else:
            candidate.update(url=page_url, etag=_header(response_headers.get("etag", "")),
                             last_modified=_header(response_headers.get("last-modified", "")))
            has_more = False
            break
    jobs = []
    withdrawn = []
    for uuid, event in events.items():
        item = event["item"]
        status = item["_feed_entry"]["status"]
        if status == "INACTIVE" and uuid not in known:
            withdrawn.append(uuid)
            continue
        if uuid not in known and not _matches(item, keywords):
            continue
        detail_status, _, body, _ = _fetch_nav(event["url"], access_token)
        if detail_status != 200:
            raise SourceError("NAV returned no current vacancy details; the checkpoint was not advanced.")
        job = _normalize(uuid, _decode(body))
        if job is None:
            withdrawn.append(uuid)
        else:
            jobs.append(job)
    return {
        "jobs": jobs, "withdrawn_ids": withdrawn, "state": candidate,
        "has_more": has_more, "pages": pages, "seen": seen,
        "experimental_token": experimental,
    }
