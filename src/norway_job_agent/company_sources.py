"""Bounded public company collectors; no credentials or application submission.

Every request uses the shared DNS-pinned downloader and observes robots rules.
Coverage is returned explicitly: a successful bounded scan is not a full crawl.
"""
from __future__ import annotations

import json
import re
import time
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from urllib.parse import quote, urlencode, urlsplit, urlunsplit

from .sources import SourceError, _board, _fetch, _job, _link, _plain, _url_parts, _set_location_countries, fetch_job_url, geographic_rejection, source_location_terms

COMPANY_SOURCE_TYPES = frozenset({"workday", "smartrecruiters", "career_page"})
MAX_JOBS = 100
MAX_LIST_PAGES = 10
MAX_SCAN_JOBS = 500
REQUEST_PAUSE = 0.15


def _limit(source: dict) -> int:
    value = source.get("max_jobs", 60)
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_JOBS:
        raise SourceError("Company collection limit must be a whole number from 1 to 100.")
    return value


def _scan_limit(source: dict) -> int:
    value = source.get("max_scan_jobs", min(MAX_SCAN_JOBS, max(100, _limit(source) * 3)))
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_SCAN_JOBS:
        raise SourceError("Company scan limit must be a whole number from 1 to 500.")
    return value


def _workday_parts(url: str):
    parts, host, _ = _url_parts(url)
    match = re.fullmatch(r"([a-zA-Z0-9_-]+)\.wd[0-9]+\.myworkdayjobs\.com", host)
    segments = [value for value in parts.path.split("/") if value]
    if segments and re.fullmatch(r"[a-z]{2}-[A-Z]{2}", segments[0]):
        segments.pop(0)
    if parts.scheme != "https" or parts.port is not None or parts.query or parts.fragment or not match or len(segments) != 1:
        raise SourceError("Use a Workday career board URL, for example https://company.wd3.myworkdayjobs.com/en-US/External.")
    board = _board(segments[0])
    origin = f"https://{host}"
    return origin, match.group(1), board


def validate_company_source(source: dict) -> None:
    if not isinstance(source, dict) or source.get("type") not in COMPANY_SOURCE_TYPES:
        raise SourceError("Unsupported company source type.")
    _limit(source)
    _scan_limit(source)
    source_location_terms(source)
    if "countries" in source:
        from .countries import validate_countries
        validate_countries(source["countries"])
    if not isinstance(source.get("name", ""), str) or len(source.get("name", "")) > 200:
        raise SourceError("Company name must be at most 200 characters.")
    if source["type"] == "workday":
        _workday_parts(source.get("url", ""))
        if not isinstance(source.get("search_text", ""), str) or len(source.get("search_text", "")) > 200:
            raise SourceError("Workday search text must be at most 200 characters.")
    elif source["type"] == "smartrecruiters":
        _board(source.get("board", ""))
        if not isinstance(source.get("country", "no"), str) or not re.fullmatch(r"[a-zA-Z]{2}", source.get("country", "no")):
            raise SourceError("Use a two-letter country code, such as no.")
    else:
        parts, _, _ = _url_parts(source.get("url", ""))
        sitemap = source.get("sitemap_url")
        if sitemap:
            sitemap_parts, _, _ = _url_parts(sitemap)
            if (parts.scheme, parts.netloc) != (sitemap_parts.scheme, sitemap_parts.netloc):
                raise SourceError("The sitemap must belong to the same career website.")
        path = source.get("job_path", "/jobs/")
        if not isinstance(path, str) or not path.startswith("/") or len(path) < 3 or "?" in path:
            raise SourceError("Provide a vacancy path prefix, such as /jobs/ or /job-search/.")
        keywords = source.get("url_keywords", [])
        if not isinstance(keywords, list) or len(keywords) > 20 or any(not isinstance(k, str) or not k.strip() for k in keywords):
            raise SourceError("URL keywords must be a list of nonempty words.")


def _json(url, **kwargs):
    _, _, body, _ = _fetch(url, respect_robots=True, **kwargs)
    try:
        data = json.loads(body)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise SourceError("The company listing returned invalid JSON; its website may have changed.") from exc
    if not isinstance(data, dict):
        raise SourceError("The company listing returned an unexpected response.")
    return data


def _report(jobs, coverage, has_more=False):
    return {"jobs": jobs, "coverage": coverage, "has_more": bool(has_more)}


def _workday(source):
    origin, tenant, board = _workday_parts(source["url"])
    base = f"{origin}/wday/cxs/{tenant}/{board}"
    limit = _limit(source)
    scan_limit = _scan_limit(source)
    terms = source_location_terms(source)
    filtered = bool(terms or source.get("countries"))
    counters = {"fetched": 0, "filtered_location": 0, "filtered_country": 0, "unknown_country": 0}
    jobs, seen = [], set()
    offset, total = 0, None
    for _ in range(MAX_LIST_PAGES):
        batch = min(20, scan_limit - offset, 20 if filtered else limit - len(jobs))
        if batch <= 0:
            break
        payload = _json(base + "/jobs", method="POST", body=json.dumps({
            "appliedFacets": {}, "limit": batch, "offset": offset,
            "searchText": source.get("search_text", ""),
        }).encode("utf-8"))
        items, total = payload.get("jobPostings"), payload.get("total")
        if not isinstance(items, list) or isinstance(total, bool) or not isinstance(total, int) or total < 0:
            raise SourceError("Workday returned an unexpected listing response.")
        if len(items) > batch:
            raise SourceError("Workday ignored the requested page size; no partial import was returned.")
        if not items:
            if offset < total:
                raise SourceError("Workday stopped pagination before the advertised total; retry later.")
            break
        processed = 0
        for item in items[:batch]:
            processed += 1
            if not isinstance(item, dict):
                raise SourceError("Workday returned an incomplete posting.")
            path = item.get("externalPath", "")
            if not isinstance(path, str) or not path.startswith("/job/") or ".." in path or "?" in path or "#" in path:
                raise SourceError("Workday returned an invalid posting path.")
            if path in seen:
                raise SourceError("Workday repeated a page; no partial import was returned.")
            seen.add(path)
            time.sleep(REQUEST_PAUSE)
            detail = _json(base + path).get("jobPostingInfo")
            if not isinstance(detail, dict) or not detail.get("title"):
                raise SourceError("Workday returned incomplete vacancy details.")
            locations = [detail.get("location", "")]
            additional = detail.get("additionalLocations", [])
            if not isinstance(additional, list):
                raise SourceError("Workday returned invalid additional locations.")
            locations.extend(value if isinstance(value, str) else value.get("descriptor", "") for value in additional if isinstance(value, (str, dict)))
            country = detail.get("country")
            if isinstance(country, dict) and country.get("descriptor"):
                locations.append(_plain(country["descriptor"]))
            link = f"{origin}/en-US/{board}{path}"
            job = _job(title=_plain(detail["title"]), company=source.get("name") or tenant,
                location="; ".join(dict.fromkeys(filter(None, locations))) or _plain(item.get("locationsText")),
                description=_plain(detail.get("jobDescription")), source="workday",
                source_id=f"{tenant}:{board}:{detail.get('jobReqId') or path}", source_url=link, apply_url=link,
                employment_type=_plain(detail.get("timeType")), published_at=detail.get("startDate"), deadline=detail.get("endDate"))
            _set_location_countries(job, [(detail.get("location") or item.get("locationsText", ""),
                                          country.get("descriptor") if isinstance(country, dict) else None),
                                         *((value if isinstance(value, str) else value.get("descriptor", ""), None)
                                           for value in additional if isinstance(value, (str, dict)))])
            counters["fetched"] += 1
            reason = geographic_rejection(job, source, location_terms=terms)
            if reason:
                counters[reason] += 1
            else:
                jobs.append(job)
            if len(jobs) >= limit:
                break
        offset += processed
        if offset >= total or offset >= scan_limit or len(jobs) >= limit:
            break
        time.sleep(REQUEST_PAUSE)
    more = offset < (total or 0)
    return {**_report(jobs, f"Checked {offset} of {total or 0} public Workday search results; kept {len(jobs)} after geographic filtering." + (" Collection budget reached; this is partial coverage." if more else ""), more),
            **counters, "scanned": offset, "available": total or 0}


def _smartrecruiters(source):
    board, limit = _board(source["board"]), _limit(source)
    scan_limit = _scan_limit(source)
    terms = source_location_terms(source)
    filtered = bool(terms or source.get("countries"))
    counters = {"fetched": 0, "filtered_location": 0, "filtered_country": 0, "unknown_country": 0}
    base = f"https://api.smartrecruiters.com/v1/companies/{board}/postings"
    jobs, seen = [], set()
    offset, total = 0, 0
    for _ in range(MAX_LIST_PAGES):
        batch = min(100, scan_limit - offset, 100 if filtered else limit - len(jobs))
        if batch <= 0:
            break
        query = urlencode({"country": source.get("country", "no"), "destination": "PUBLIC", "limit": batch, "offset": offset})
        data = _json(base + "?" + query)
        items, total = data.get("content"), data.get("totalFound")
        if not isinstance(items, list) or isinstance(total, bool) or not isinstance(total, int) or total < 0:
            raise SourceError("SmartRecruiters returned an unexpected listing response.")
        if len(items) > batch:
            raise SourceError("SmartRecruiters ignored the requested page size.")
        if not items and offset < total:
            raise SourceError("SmartRecruiters returned an incomplete page.")
        processed = 0
        for item in items:
            processed += 1
            identifier = item.get("id") if isinstance(item, dict) else None
            if not isinstance(identifier, (str, int)) or isinstance(identifier, bool) or not re.fullmatch(r"[a-zA-Z0-9-]+", str(identifier)):
                raise SourceError("SmartRecruiters returned an invalid job identifier.")
            if identifier in seen:
                raise SourceError("SmartRecruiters repeated a job across pages.")
            seen.add(identifier)
            time.sleep(REQUEST_PAUSE)
            detail = _json(base + "/" + quote(str(identifier), safe=""))
            if not detail.get("name"):
                raise SourceError("SmartRecruiters returned incomplete vacancy details.")
            if detail.get("active") is False:
                continue
            location = detail.get("location") or {}
            job_ad, company, employment = detail.get("jobAd") or {}, detail.get("company") or {}, detail.get("typeOfEmployment") or {}
            if not all(isinstance(value, dict) for value in (location, job_ad, company, employment)):
                raise SourceError("SmartRecruiters returned invalid vacancy metadata.")
            sections = job_ad.get("sections") or {}
            if not isinstance(sections, dict):
                raise SourceError("SmartRecruiters returned invalid vacancy sections.")
            description = "\n\n".join(_plain(section.get("text")) for section in sections.values() if isinstance(section, dict))
            link = _link(detail.get("postingUrl"), base) or f"https://jobs.smartrecruiters.com/{board}/{identifier}"
            job = _job(title=_plain(detail["name"]), company=_plain(company.get("name")) or source.get("name") or board,
                location=", ".join(_plain(location.get(k)) for k in ("city", "region", "country") if location.get(k)),
                description=description, source="smartrecruiters", source_id=f"{board}:{identifier}",
                source_url=link, apply_url=_link(detail.get("applyUrl"), link) or link,
                published_at=detail.get("releasedDate"), employment_type=_plain(employment.get("label")))
            _set_location_countries(job, [(job["location"], location.get("country"))])
            counters["fetched"] += 1
            reason = geographic_rejection(job, source, location_terms=terms)
            if reason:
                counters[reason] += 1
            else:
                jobs.append(job)
            if len(jobs) >= limit:
                break
        offset += processed
        if offset >= total or offset >= scan_limit or len(jobs) >= limit:
            break
    more = offset < total
    return {**_report(jobs, f"Checked {offset} of {total} public country-filtered SmartRecruiters listings; kept {len(jobs)} after geographic filtering." + (" Collection budget reached; this is partial coverage." if more else ""), more),
            **counters, "scanned": offset, "available": total}


class _Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            value = dict(attrs).get("href")
            if value:
                self.links.append(value)


def _career_page(source):
    base = source["url"]
    listing = source.get("sitemap_url") or base
    _, _, body, final = _fetch(listing, respect_robots=True)
    candidates = []
    if source.get("sitemap_url"):
        if b"<!DOCTYPE" in body.upper() or b"<!ENTITY" in body.upper():
            raise SourceError("Sitemaps with document entities are not supported.")
        try:
            tree = ET.fromstring(body)
        except ET.ParseError as exc:
            raise SourceError("The company sitemap is invalid.") from exc
        if tree.tag.rsplit("}", 1)[-1] != "urlset":
            raise SourceError("Use a vacancy sitemap containing URLs, not a sitemap index.")
        rows = []
        for node in tree:
            values = {child.tag.rsplit("}", 1)[-1]: child.text or "" for child in node}
            rows.append((values.get("lastmod", ""), values.get("loc", "")))
        candidates = [url for _, url in sorted(rows, reverse=True)]
    else:
        parser = _Links()
        parser.feed(body.decode("utf-8-sig", errors="replace"))
        parser.close()
        candidates = parser.links
    origin = urlsplit(base)
    seen, selected = set(), []
    keywords = [word.casefold() for word in source.get("url_keywords", [])]
    prefix = source.get("job_path", "/jobs/")
    for raw in candidates:
        link = _link(raw, final)
        if not link:
            continue
        parts = urlsplit(link)
        if (parts.scheme, parts.netloc) != (origin.scheme, origin.netloc) or not parts.path.startswith(prefix) or parts.path == prefix:
            continue
        if keywords and not any(word in parts.path.casefold() for word in keywords):
            continue
        link = urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
        if link not in seen:
            seen.add(link)
            selected.append(link)
    if not selected:
        raise SourceError("No matching public vacancy links were found. The site may be empty or have changed; open the career page to check.")
    limit = _limit(source)
    scan_limit = _scan_limit(source)
    terms = source_location_terms(source)
    counters = {"fetched": 0, "filtered_location": 0, "filtered_country": 0, "unknown_country": 0}
    jobs, failures, job_seen = [], [], set()
    capped, checked = False, 0
    for link in selected[:scan_limit]:
        time.sleep(REQUEST_PAUSE)
        checked += 1
        try:
            found = fetch_job_url(link)
        except SourceError as exc:
            failures.append(str(exc))
            continue
        for job in found:
            key = (job["source_id"], job["source_url"])
            if key not in job_seen:
                job_seen.add(key)
                counters["fetched"] += 1
                reason = geographic_rejection(job, source, location_terms=terms)
                if reason:
                    counters[reason] += 1
                    continue
                if len(jobs) >= limit:
                    capped = True
                    break
                # The catalog identifies the employer, avoiding ATS branding.
                if source.get("name"):
                    job["company"] = source["name"]
                jobs.append(job)
        if len(jobs) >= limit:
            capped = capped or checked < len(selected)
            break
    if not jobs and failures:
        raise SourceError(f"Could not read any matching jobs from {checked} vacancy pages. {failures[0]}")
    more = capped or len(selected) > checked or bool(failures) or not source.get("sitemap_url")
    coverage = f"Checked {checked} of {len(selected)} matching links in the public " + ("sitemap." if source.get("sitemap_url") else "listing page; other pages may contain more jobs.")
    if keywords:
        coverage += " URL filtering: " + ", ".join(source["url_keywords"]) + "; jobs without these words in the URL are outside this scan."
    if capped or len(selected) > checked:
        coverage += " Collection limit reached; this is partial coverage."
    if failures:
        coverage += f" {len(failures)} pages could not be read: {failures[0]}"
    return {**_report(jobs, coverage, more), **counters, "scanned": checked, "available": len(selected)}


def fetch_company_source(source: dict) -> dict:
    """Return {jobs, coverage, has_more}; failure never claims an empty feed."""
    validate_company_source(source)
    return {"workday": _workday, "smartrecruiters": _smartrecruiters, "career_page": _career_page}[source["type"]](source)
