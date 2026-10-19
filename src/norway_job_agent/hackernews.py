"""Hacker News "Ask HN: Who is hiring?" threads as a job site.

Each month the whoishiring account opens a thread where companies post one
top-level comment per opening, usually starting with a line such as
"Company | Role | Location | REMOTE". The threads are read through Hacker
News' official search API (hn.algolia.com), two requests per thread. Only
the first line is interpreted; the whole comment becomes the advert.
"""

from __future__ import annotations

import html
import json
import re
from datetime import datetime, timedelta, timezone

from .countries import _OTHER_COUNTRY_NAMES, job_countries
from .sources import MAX_ATS_RESPONSE_BYTES, SourceError, _fetch, _job, _link, _plain

API = "https://hn.algolia.com/api/v1"
THREADS = 2
MAX_AGE_DAYS = 45
_SEPARATOR = re.compile(r"\s*\|\s*|\s+[•·–—]\s+|\s+-\s+")
_ROLE = re.compile(
    r"\b(?:engineer\w*|developer|programmer|scientist|designer|manager|analyst|researcher|architect|lead|head of|"
    r"director|sre|devops|intern\w*|founding|cto|vp|specialist|consultant|recruiter|writer|administrator|"
    r"technician|full[- ]?stack|front[- ]?end|back[- ]?end|swe|ml|ai|data|product|designer|roles?|positions?)\b", re.I)
_LOCATION_HINT = re.compile(r"\b(?:remote|on-?site|hybrid|in[- ]office|worldwide|anywhere|global|europe|emea|eu|"
                            r"americas|north america|time ?zones?)\b", re.I)
_ON_SITE = re.compile(r"\b(?:on-?site|hybrid|in[- ]office|in[- ]person)\b", re.I)
# "US, UK, EU" names the USA even though the UK is outside the supported
# countries; country names count wherever they appear in a place.
_NAMED = (("US", re.compile(r"(?<![\w.])(?:US|USA|U\.S\.A?\.?|United States|NYC|SF|Bay Area)(?!\w)")),
          ("DE", re.compile(r"\b(?:Germany|Deutschland)\b", re.I)), ("NO", re.compile(r"\b(?:Norway|Norge)\b", re.I)),
          ("UA", re.compile(r"\b(?:Ukraine)\b", re.I)))
_REGIONS = (
    (re.compile(r"\b(?:worldwide|anywhere|global)\b", re.I), ("NO", "US", "DE", "UA")),
    (re.compile(r"\b(?:europe|emea|eu|eea)\b", re.I), ("NO", "DE", "UA")),
    (re.compile(r"\b(?:americas|north america)\b", re.I), ("US",)),
)
_TYPES = (("Internship", r"\bintern(?:ship)?s?\b"), ("Contract", r"\bcontract(?:or)?\b"),
          ("Part-time", r"\bpart[- ]time\b"), ("Full-time", r"\bfull[- ]time\b"))


def _json(url: str):
    _, _, body, _ = _fetch(url, respect_robots=True, max_response_bytes=MAX_ATS_RESPONSE_BYTES)
    try:
        return json.loads(body)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise SourceError("Hacker News returned invalid data; its API may have changed.") from exc


def fetch(now: datetime | None = None) -> list[dict]:
    """Every post in the latest hiring threads that names a supported country."""
    now = now or datetime.now(timezone.utc)
    found = _json(f"{API}/search_by_date?tags=story,author_whoishiring&hitsPerPage=12")
    hits = found.get("hits") if isinstance(found, dict) else None
    if not isinstance(hits, list):
        raise SourceError("Hacker News returned an unexpected thread list.")
    threads = [hit for hit in hits if isinstance(hit, dict)
               and str(hit.get("title", "")).startswith("Ask HN: Who is hiring?")
               and str(hit.get("objectID", "")).isdecimal()][:THREADS]
    if not threads:
        raise SourceError("No Hacker News hiring thread was found; try again later.")
    jobs, seen = [], set()
    for thread in threads:
        item = _json(f"{API}/items/{thread['objectID']}")
        children = item.get("children") if isinstance(item, dict) else None
        if not isinstance(children, list):
            raise SourceError("Hacker News returned an unexpected thread.")
        for child in children:
            job = parse_post(child, now)
            if job is not None and job["source_id"] not in seen:
                seen.add(job["source_id"])
                jobs.append(job)
    return jobs


def _parts(value: str) -> list[str]:
    # "Remote US or Ontario, Canada" names two places.
    return [part.strip(" ,;:") for part in re.split(r"\s+or\s+|\s*/\s*|\s*&\s*|\s+and\s+|;", value) if part.strip(" ,;:")]


def parse_post(item, now: datetime) -> dict | None:
    if not isinstance(item, dict) or not isinstance(item.get("text"), str) or not str(item.get("id", "")).isdecimal():
        return None
    if not item.get("author") or item.get("author") == "whoishiring":
        return None
    try:
        created = datetime.fromisoformat(str(item.get("created_at", "")).replace("Z", "+00:00"))
    except ValueError:
        return None
    if now - created > timedelta(days=MAX_AGE_DAYS):
        return None
    text = item["text"]
    header = (_plain(re.split(r"<p>", text, maxsplit=1, flags=re.I)[0]).splitlines() or [""])[0]
    header = re.sub(r"https?://[^\s)]+", " ", header)
    segments = [segment.strip(" -–—·•") for segment in _SEPARATOR.split(header) if segment.strip(" -–—·•")]
    if len(segments) < 2:
        return None
    first = segments[0]
    company = re.sub(r"\s*\([^)]*\)", "", first).strip(" ,:()")
    if not 2 <= len(company) <= 80:
        return None
    rest = [*re.findall(r"\(([^)]*)\)", first), *segments[1:]]
    title = next((segment for segment in segments[1:] if _ROLE.search(segment) and not _LOCATION_HINT.fullmatch(segment.strip())), "")
    places = [segment for segment in rest if segment != title
              and (_LOCATION_HINT.search(segment) or any(job_countries({"location": part}) for part in _parts(segment))
                   or any(pattern.search(segment) for _, pattern in _NAMED))]
    codes = [code for place in places for part in _parts(place) for code in job_countries({"location": part})]
    codes += [code for code, pattern in _NAMED if any(pattern.search(place) for place in places)]
    for pattern, regions in _REGIONS:
        if any(pattern.search(place) for place in places):
            codes.extend(regions)
    remote = any(re.search(r"\bremote\b", place, re.I) for place in places)
    named_elsewhere = any(re.search(r"(?<!\w)" + re.escape(name) + r"(?!\w)", segment)
                          for segment in rest for name in _OTHER_COUNTRY_NAMES)
    # Like the remote boards: a fully remote role naming no region is open
    # everywhere. "Onsite + part remote" without a place stays unplaced.
    unrestricted = not codes and remote and not named_elsewhere and not any(_ON_SITE.search(place) for place in places)
    if unrestricted:
        codes = ["NO", "US", "DE", "UA"]
    codes = list(dict.fromkeys(codes))
    if not codes:
        return None
    link = f"https://news.ycombinator.com/item?id={item['id']}"
    apply = next((url for href in re.findall(r'href="([^"]+)"', text)
                  if (url := _link(html.unescape(href), link)) and "ycombinator.com" not in url), "")
    job = _job(title=(title or "Multiple roles")[:200], company=company, location=" | ".join(places)[:300] or "Remote",
               description=_plain(text), source="hackernews", source_id=str(item["id"]), source_url=link,
               apply_url=link, published_at=created.isoformat(),
               employment_type=next((label for label, pattern in _TYPES if re.search(pattern, header, re.I)), ""))
    job["countries"] = codes
    job["raw_json"] = {"resolved_location_countries": codes, **({"external_apply_url": apply} if apply else {}),
                       **({"remote_eligibility": "Not stated"} if unrestricted else {})}
    return job
