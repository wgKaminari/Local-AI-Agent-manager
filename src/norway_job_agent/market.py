"""Market-wide job-board searches: every employer, not one company at a time.

Employer boards (Greenhouse, Workday, ...) only ever show the companies a user
has configured. These providers search a whole market for each role query:

* arbeitsagentur - Germany's federal employment agency (official public API)
* arbeitnow       - German tech job board (free public API)
* djinni, dou     - Ukrainian IT job boards (public RSS feeds)
* remotive, remoteok, himalayas - remote-job boards (free public APIs)
* hackernews      - Hacker News "Who is hiring?" threads, see hackernews.py
* us_employers    - a few hundred US company boards, see employer_pack.py
* linkedin        - LinkedIn's public guest job search; opt-in, see linkedin_public.py

Downloads never touch the database, so providers may run concurrently. Every
result is checked against the query that found it: boards return loosely
related adverts, and only relevant ones are kept.
"""

from __future__ import annotations

import base64
import json
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
from urllib.parse import quote, urlencode

from .countries import COUNTRIES
from .profile import _role_terms, _role_text, contains_phrase
from .sources import (
    MAX_ATS_RESPONSE_BYTES, SourceError, _fetch, _job, _link, _plain, _set_location_countries,
)

REQUEST_PAUSE = 0.3
# Words that do not identify a role. German adverts add gender markers.
_STOPWORDS = frozenset({
    "and", "or", "the", "of", "in", "for", "with", "a", "an", "to", "at", "on", "og", "i", "und", "der",
    "die", "das", "für", "mit", "m", "w", "d", "f", "x", "div", "all", "genders", "senior", "junior", "lead",
})
REMOTE = ("NO", "US", "DE", "UA")
# Title words that say little about a role on their own. For a multi-word
# search, a title needs one of the more specific words ("DevOps" in "Linux
# DevOps") before an advert without text counts as a candidate.
GENERIC_WORDS = frozenset({
    "engineer", "ingeniør", "ingenieur", "developer", "utvikler", "entwickler", "manager", "leder", "specialist",
    "spesialist", "spezialist", "consultant", "konsulent", "berater", "analyst", "analytiker", "senior", "junior",
    "data", "it", "software", "system", "systems", "technician", "tekniker", "assistant", "assistent", "research",
    "researcher", "scientist", "forsker", "officer", "advisor", "rådgiver", "architect", "arkitekt", "administrator",
    "lead", "head", "intern", "trainee", "medarbeider", "fagarbeider", "engineering", "mitarbeiter", "remote",
})


def specific_tokens(query: str) -> list[str]:
    """The query words that identify a role on their own (none for "Data Engineer")."""
    return [token for token in query_tokens(query) if token not in GENERIC_WORDS]


@dataclass(frozen=True)
class Provider:
    name: str
    countries: tuple[str, ...]
    homepage: str
    opt_in: bool = False
    # Providers that answer one keyword search per request. The others publish
    # one recent feed, which is downloaded once and filtered for every query.
    per_query: bool = True
    max_queries: int = 12
    note: str = ""


PROVIDERS: dict[str, Provider] = {
    "arbeitsagentur": Provider("Bundesagentur für Arbeit", ("DE",), "https://www.arbeitsagentur.de/jobsuche/",
                               max_queries=16, note="Germany's federal job agency: most German vacancies, official free API."),
    "arbeitnow": Provider("Arbeitnow", ("DE",), "https://www.arbeitnow.com/", per_query=False,
                          note="German tech and English-speaking jobs; free public API."),
    "djinni": Provider("Djinni", ("UA",), "https://djinni.co/jobs/", note="Ukrainian IT jobs; public RSS feed."),
    "dou": Provider("DOU", ("UA",), "https://jobs.dou.ua/", note="Ukrainian IT jobs; public RSS feed."),
    "remotive": Provider("Remotive", REMOTE, "https://remotive.com/", note="Remote jobs; country follows each job's stated eligibility."),
    "remoteok": Provider("Remote OK", REMOTE, "https://remoteok.com/", per_query=False,
                         note="Remote jobs; country follows each job's stated eligibility."),
    "himalayas": Provider("Himalayas", REMOTE, "https://himalayas.app/jobs", max_queries=8,
                          note="Remote jobs; country follows each job's stated eligibility."),
    "hackernews": Provider("Hacker News: Who is hiring?", REMOTE, "https://news.ycombinator.com/submitted?id=whoishiring",
                           per_query=False, note="Monthly hiring threads of mostly US and remote tech startups; "
                                                 "read through Hacker News' official search API."),
    "us_employers": Provider("US tech employers", ("US",), "https://github.com/wgKaminari/Local-AI-Agent-manager/blob/main/docs/market-search.md#us-tech-employers",
                             per_query=False, note="A few hundred verified US company career boards (Greenhouse, Lever, "
                                                   "Ashby, Workday), each read at most once a day."),
    "linkedin": Provider("LinkedIn public search", ("NO", "US", "DE", "UA"), "https://www.linkedin.com/jobs/",
                         opt_in=True, max_queries=6,
                         note="No login. Slow and polite, but LinkedIn's terms forbid automated access; off until you enable it."),
}
DEFAULT_ENABLED = {name: not provider.opt_in for name, provider in PROVIDERS.items()}
# Too large for a live "Search job sites" query; refreshes read it.
REFRESH_ONLY = frozenset({"us_employers"})
# Lists from these providers carry no advert text; descriptions are loaded
# when a vacancy is opened, and during collection for role matches.
LAZY_DETAILS = frozenset({"arbeitsagentur", "linkedin", "djinni"})
DJINNI_COMPANY = "Company on Djinni"


def needs_details(job: dict) -> bool:
    """Lists from some boards omit the advert text (or, on Djinni, the employer)."""
    source = job.get("source")
    if source == "djinni":
        return job.get("company") == DJINNI_COMPANY
    return source in LAZY_DETAILS and not str(job.get("description") or "").strip()
MAX_SEARCHES = 30
# "Collect everything in my fields": broad searches each refresh adds to your
# roles and saved searches. Everything they return is kept (in a supported
# country); matching then happens in the database. LinkedIn is never browsed
# broadly: it refuses more than a few dozen requests.
BROAD_QUERIES = {
    "arbeitsagentur": ["Informatik", "IT", "Software", "Entwickler", "Developer", "Data", "Daten", "Analyst",
                       "Ingenieur", "Engineer", "Mathematik", "Statistik", "Wissenschaftler", "Forschung",
                       "Machine Learning", "Künstliche Intelligenz", "Cloud", "DevOps", "Consultant", "Werkstudent"],
    "djinni": ["python", "data", "machine learning", "devops", "java", "javascript", "golang", "c++", ".net", "php",
               "qa", "analyst", "product", "project manager", "designer", "support", "engineer", "developer",
               "manager", "marketing", "sales", "hr", "security", "embedded"],
    "dou": ["category:" + name for name in (
        "Python", "Data Science", "AI/ML", "Data Engineer", "Big Data", "Analyst", "Java", ".NET", "Front End",
        "Node.js", "PHP", "Golang", "C++", "Ruby", "Scala", "Rust", "iOS/macOS", "Android", "Flutter", "DevOps",
        "SysAdmin", "Security", "QA", "Embedded", "Architect", "Product Manager", "Project Manager",
        "Engineering Manager", "Design", "Technical Writer", "Support", "DBA", "Blockchain", "Unity", "Other")],
    "remotive": ["category:" + slug for slug in (
        "software-dev", "data", "devops", "qa", "product", "project-management", "design", "customer-support",
        "marketing", "sales-business", "finance-legal", "hr", "writing", "all-others")],
    "himalayas": ["software engineer", "data", "machine learning", "devops", "product manager", "designer",
                  "analyst", "python", "frontend", "backend", "mobile", "security", "marketing", "sales",
                  "customer support", "project manager"],
}
# Seconds one provider may spend per refresh; the next refresh continues.
REFRESH_SECONDS = 600
LIVE_SECONDS = 90


def validate_market_settings(value) -> dict:
    """Normalize the market section of sources.json."""
    if value is None:
        value = {}
    if not isinstance(value, dict) or set(value) - {"enabled", "searches", "include_related_roles", "linkedin_terms_accepted",
                                                    "collect_everything"}:
        raise ValueError("Market search settings must contain enabled providers, saved searches and options.")
    enabled = value.get("enabled", {})
    if not isinstance(enabled, dict) or any(name not in PROVIDERS or not isinstance(flag, bool) for name, flag in enabled.items()):
        raise ValueError("Choose job sites from the supported list, each switched on or off.")
    searches = value.get("searches", [])
    if not isinstance(searches, list) or len(searches) > MAX_SEARCHES or any(
            not isinstance(item, str) or not item.strip() or len(item) > 120 for item in searches):
        raise ValueError(f"Saved searches must be at most {MAX_SEARCHES} phrases of up to 120 characters.")
    related = value.get("include_related_roles", True)
    accepted = value.get("linkedin_terms_accepted", False)
    everything = value.get("collect_everything", True)
    if not all(isinstance(flag, bool) for flag in (related, accepted, everything)):
        raise ValueError("Market search options must be true or false.")
    result = {"enabled": {**DEFAULT_ENABLED, **enabled},
              "searches": list(dict.fromkeys(" ".join(item.split()) for item in searches)),
              "include_related_roles": related, "linkedin_terms_accepted": accepted, "collect_everything": everything}
    if result["enabled"]["linkedin"] and not accepted:
        raise ValueError("Confirm LinkedIn's terms notice before enabling LinkedIn public search.")
    return result


def query_tokens(query: str) -> list[str]:
    words = re.findall(r"[\w+#./-]+", _role_text(query))
    return [word.strip("./-") for word in words if word.strip("./-") and word.strip("./-") not in _STOPWORDS]


def matches_query(job: dict, query: str) -> bool:
    """Whether an advert plausibly answers this query.

    A title that names the role (or an equivalent spelling) always counts.
    Otherwise every query word must appear, with at least one in the title:
    "Linux DevOps" keeps "DevOps Engineer" whose advert asks for Linux. A
    provider without advert text in its list gets the benefit of the doubt for
    words that are not in the title, because it searched the full advert.
    """
    title = _role_text(str(job.get("title", "")))
    if any(contains_phrase(title, term) for term in _role_terms(query)):
        return True
    tokens = query_tokens(query)
    if not tokens:
        return False
    in_title = [token for token in tokens if contains_phrase(title, token)]
    if not in_title:
        return False
    description = str(job.get("description") or "")
    if not description:
        return job.get("source") in LAZY_DETAILS
    body = title + "\n" + _role_text(description)
    return all(contains_phrase(body, token) for token in tokens)


def search_queries(profile: dict, market: dict, country: str = "") -> list[str]:
    """Saved searches first, then target and related roles; no duplicate spellings."""
    preferences = profile.get("country_preferences", {}).get(country, {}) if country else {}
    roles = list(preferences.get("target_roles") or profile.get("target_roles", []))
    if market.get("include_related_roles", True):
        roles += list(preferences.get("related_roles") or profile.get("related_roles", []))
    selected, covered = [], set()
    for query in [*market.get("searches", []), *roles]:
        normalized = _role_text(query)
        if not normalized or normalized in covered:
            continue
        selected.append(" ".join(query.split()))
        # One spelling of a role finds the others; skip its aliases.
        covered.update(_role_terms(query))
    return selected


def _iso(value) -> str:
    if not isinstance(value, str) or not value.strip():
        return ""
    try:
        return parsedate_to_datetime(value).isoformat()
    except (TypeError, ValueError, IndexError):
        return value.strip()


def _json(url: str, **kwargs):
    _, _, body, _ = _fetch(url, max_response_bytes=MAX_ATS_RESPONSE_BYTES, **kwargs)
    try:
        return json.loads(body)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise SourceError("The job site returned invalid data; its API may have changed.") from exc


def _rss(url: str) -> list[dict]:
    _, _, body, _ = _fetch(url, respect_robots=True, max_response_bytes=MAX_ATS_RESPONSE_BYTES)
    if b"<!DOCTYPE" in body[:2000].upper() or b"<!ENTITY" in body.upper():
        raise SourceError("The job feed is not a plain RSS document.")
    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        raise SourceError("The job feed is invalid; the site may have changed.") from exc
    return [{child.tag: child.text or "" for child in item} for item in root.iter("item")]


# Remote boards state who may apply. Only explicit regions are interpreted.
_REGIONS = (
    (re.compile(r"\b(?:worldwide|anywhere|global|world)\b", re.I), REMOTE),
    (re.compile(r"\b(?:europe|emea|eu|eea|european union)\b", re.I), ("NO", "DE", "UA")),
    (re.compile(r"\b(?:americas|north america)\b", re.I), ("US",)),
)


def _remote_countries(restriction: str) -> list[str]:
    from .countries import job_countries
    codes = list(job_countries({"location": restriction}))
    for pattern, regions in _REGIONS:
        if pattern.search(restriction):
            codes.extend(regions)
    return list(dict.fromkeys(codes))


def _remote_job(job: dict, restriction: str) -> dict:
    restriction = " ".join(str(restriction or "").split())
    job["location"] = "Remote" + (f" ({restriction})" if restriction else "")
    codes = _remote_countries(restriction or "Worldwide")
    job["countries"] = codes
    job["raw_json"] = {"remote_eligibility": restriction or "Not stated", "resolved_location_countries": codes}
    return job


# --- Providers -----------------------------------------------------------------

_BA_BASE = "https://rest.arbeitsagentur.de/jobboerse/jobsuche-service"
# The public client key documented at https://jobsuche.api.bund.dev/.
_BA_HEADERS = {"X-API-Key": "jobboerse-jobsuche"}


def _ba_job(item: dict) -> dict | None:
    reference = item.get("referenznummer") or item.get("refnr")
    title = _plain(item.get("stellenangebotsTitel") or item.get("titel") or item.get("hauptberuf"))
    if not isinstance(reference, str) or not re.fullmatch(r"[A-Za-z0-9-]{4,80}", reference) or not title:
        return None
    places = item.get("stellenlokationen") or ([item["arbeitsort"]] if isinstance(item.get("arbeitsort"), dict) else [])
    rows = []
    for place in places if isinstance(places, list) else []:
        address = place.get("adresse", place) if isinstance(place, dict) else {}
        if isinstance(address, dict):
            town = _plain(address.get("ort"))
            region = _plain(address.get("region", "")).replace("_", " ").title()
            city = ", ".join(dict.fromkeys(filter(None, (town, region if region.casefold() not in town.casefold() else ""))))
            land = _plain(address.get("land"))
            rows.append((city, "Germany" if land.upper() in {"DEUTSCHLAND", ""} else land.title()))
    link = f"https://www.arbeitsagentur.de/jobsuche/jobdetail/{quote(reference)}"
    job = _job(title=title, company=_plain(item.get("firma") or item.get("arbeitgeber")) or "Employer not named",
               location="; ".join(dict.fromkeys(city for city, _ in rows if city)) or "Germany",
               source="arbeitsagentur", source_id=reference, source_url=link, apply_url=link,
               employment_type="Full-time" if item.get("arbeitszeitVollzeit") else "",
               published_at=(item.get("veroeffentlichungszeitraum") or {}).get("von") or item.get("aktuelleVeroeffentlichungsdatum"))
    _set_location_countries(job, rows or [("Germany", "Germany")])
    return job


def _arbeitsagentur(query: str, country: str, limit: int, *, since_days: int = 60, deadline: float = 0.0,
                    **_) -> list[dict]:
    """One search; at most 100 pages of 100, the API's own ceiling."""
    jobs = []
    for page in range(1, 101):
        if deadline and time.monotonic() > deadline:
            raise _Budget(jobs)
        data = _json(f"{_BA_BASE}/pc/v6/jobs?" + urlencode({
            "was": query, "angebotsart": 1, "veroeffentlichtseit": since_days, "size": 100, "page": page,
        }), headers=_BA_HEADERS)
        items = data.get("ergebnisliste") if isinstance(data, dict) else None
        if items is None and isinstance(data, dict) and "maxErgebnisse" in data:
            items = []
        if not isinstance(items, list):
            raise SourceError("Bundesagentur für Arbeit returned an unexpected search response.")
        jobs.extend(job for item in items if isinstance(item, dict) and (job := _ba_job(item)))
        total = data.get("maxErgebnisse") or 0
        if len(jobs) >= limit or len(items) < 100 or page * 100 >= int(total or 0):
            break
        time.sleep(0.1)
    return jobs[:limit]


class _Budget(Exception):
    """A provider's time budget ran out; carries the jobs read so far."""

    def __init__(self, jobs):
        super().__init__("time budget")
        self.jobs = jobs


def _arbeitsagentur_details(job: dict) -> dict:
    reference = job["source_id"]
    encoded = base64.b64encode(reference.encode("utf-8")).decode("ascii")
    data = _json(f"{_BA_BASE}/pc/v4/jobdetails/{encoded}", headers=_BA_HEADERS)
    if not isinstance(data, dict):
        raise SourceError("Bundesagentur für Arbeit returned no vacancy details.")
    description = _plain(data.get("stellenangebotsBeschreibung") or data.get("stellenbeschreibung"))
    external = _link(data.get("externeUrl") or data.get("allianzpartnerUrl"), job["source_url"])
    return {"description": description, "external_apply_url": external,
            "company": _plain(data.get("arbeitgeber") or data.get("firma"))}


def _arbeitnow(queries: list[str], country: str, limit: int, *, newest: int = 0, max_pages: int = 3, **_) -> list[dict]:
    """The newest pages, stopping at a page older than the last refresh's newest job."""
    jobs = []
    for page in range(1, max_pages + 1):
        data = _json(f"https://www.arbeitnow.com/api/job-board-api?page={page}", respect_robots=True)
        items = data.get("data") if isinstance(data, dict) else None
        if not isinstance(items, list):
            raise SourceError("Arbeitnow returned an unexpected response.")
        for item in items:
            if not isinstance(item, dict) or not item.get("slug") or not _plain(item.get("title")):
                continue
            link = _link(item.get("url"), "https://www.arbeitnow.com/") or f"https://www.arbeitnow.com/jobs/{quote(str(item['slug']))}"
            created = item.get("created_at")
            job = _job(title=_plain(item.get("title")), company=_plain(item.get("company_name")),
                       location=_plain(item.get("location")), description=_plain(item.get("description")),
                       source="arbeitnow", source_id=str(item["slug"]), source_url=link, apply_url=link,
                       employment_type=", ".join(x for x in item.get("job_types") or [] if isinstance(x, str)),
                       published_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(created)) if isinstance(created, int) else "")
            if item.get("remote"):
                job["location"] = "; ".join(filter(None, ["Remote", job["location"]]))
            _set_location_countries(job, [(job["location"], None)])
            job["_created"] = created if isinstance(created, int) else 0
            jobs.append(job)
        if len(items) < 100 or (newest and all(not isinstance(item, dict) or (item.get("created_at") or 0) <= newest
                                               for item in items)):
            break
        time.sleep(REQUEST_PAUSE)
    return jobs


def _djinni(query: str, country: str, limit: int, **_) -> list[dict]:
    jobs = []
    for item in _rss("https://djinni.co/jobs/rss/?" + urlencode({"all_keywords": query}))[:limit]:
        link = _link(item.get("link"), "https://djinni.co/")
        title = _plain(item.get("title"))
        match = re.search(r"/jobs/(\d+)", link)
        if not link or not title or not match:
            continue
        # The feed omits the employer; opening the vacancy loads it.
        job = _job(title=title, company=DJINNI_COMPANY, location="Ukraine / remote",
                   description=_plain(item.get("description")), source="djinni", source_id=match.group(1),
                   source_url=link, apply_url=link, published_at=_iso(item.get("pubDate")))
        job["countries"] = ["UA"]
        jobs.append(job)
    return jobs


def _dou(query: str, country: str, limit: int, **_) -> list[dict]:
    jobs = []
    params = {"category": query[9:]} if query.startswith("category:") else {"search": query}
    for item in _rss("https://jobs.dou.ua/vacancies/feeds/?" + urlencode(params))[:limit]:
        link = _link(item.get("link"), "https://jobs.dou.ua/").split("?", 1)[0]
        match = re.search(r"/companies/([^/]+)/vacancies/(\d+)", link)
        raw_title = _plain(item.get("title"))
        if not match or not raw_title:
            continue
        # Titles read "Role в Company, salary, city or віддалено (remote)".
        title, _, rest = raw_title.rpartition(" в ")
        if not title:
            title, rest = raw_title, ""
        company, _, extra = rest.partition(",")
        company = company.strip() or match.group(1).replace("-", " ").title()
        # Salary parts hold digits or start with від/до ("from"/"up to").
        places = [part.strip() for part in extra.split(",")
                  if part.strip() and not re.search(r"\d|\$|€", part) and not re.match(r"(?:від|до)\s", part.strip())]
        location = "; ".join("Remote" if place.casefold() in {"віддалено", "remote"} else place for place in places)
        job = _job(title=title.strip(), company=company, location=", ".join(filter(None, [location, "Ukraine"])),
                   description=_plain(item.get("description")), source="dou", source_id=match.group(2),
                   source_url=link, apply_url=link, published_at=_iso(item.get("pubDate")))
        job["countries"] = ["UA"]
        jobs.append(job)
    return jobs


def _remotive(query: str, country: str, limit: int, **_) -> list[dict]:
    params = {"category": query[9:]} if query.startswith("category:") else {"search": query}
    data = _json("https://remotive.com/api/remote-jobs?" + urlencode(params), respect_robots=True)
    items = data.get("jobs") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise SourceError("Remotive returned an unexpected response.")
    jobs = []
    for item in items[:limit]:
        if not isinstance(item, dict) or not item.get("id") or not _plain(item.get("title")):
            continue
        link = _link(item.get("url"), "https://remotive.com/")
        job = _job(title=_plain(item.get("title")), company=_plain(item.get("company_name")),
                   description=_plain(item.get("description")), source="remotive", source_id=str(item["id"]),
                   source_url=link, apply_url=link, employment_type=_plain(item.get("job_type")).replace("_", " "),
                   published_at=_plain(item.get("publication_date")))
        jobs.append(_remote_job(job, _plain(item.get("candidate_required_location"))))
    return jobs


def _remoteok(queries: list[str], country: str, limit: int, **_) -> list[dict]:
    data = _json("https://remoteok.com/api", respect_robots=True)
    if not isinstance(data, list):
        raise SourceError("Remote OK returned an unexpected response.")
    jobs = []
    for item in data:
        if not isinstance(item, dict) or not item.get("id") or not _plain(item.get("position")):
            continue  # The first element is the API's legal notice.
        link = _link(item.get("url"), "https://remoteok.com/") or f"https://remoteok.com/remote-jobs/{quote(str(item['id']))}"
        job = _job(title=_plain(item.get("position")), company=_plain(item.get("company")),
                   description=_plain(item.get("description")), source="remoteok", source_id=str(item["id"]),
                   source_url=link, apply_url=link, published_at=_plain(item.get("date")))
        jobs.append(_remote_job(job, _plain(item.get("location"))))
    return jobs


def _himalayas(query: str, country: str, limit: int, **_) -> list[dict]:
    jobs = []
    for page in (1, 2):
        data = _json("https://himalayas.app/jobs/api/search?" + urlencode({"q": query, "page": page}), respect_robots=True)
        items = data.get("jobs") if isinstance(data, dict) else None
        if not isinstance(items, list):
            raise SourceError("Himalayas returned an unexpected response.")
        for item in items:
            if not isinstance(item, dict) or not _plain(item.get("title")):
                continue
            link = _link(item.get("applicationLink") or item.get("guid"), "https://himalayas.app/")
            if not link:
                continue
            restrictions = item.get("locationRestrictions") or []
            restriction = ", ".join(x for x in restrictions if isinstance(x, str)) if isinstance(restrictions, list) else ""
            published = item.get("pubDate")
            job = _job(title=_plain(item.get("title")), company=_plain(item.get("companyName")),
                       description=_plain(item.get("description") or item.get("excerpt")), source="himalayas",
                       source_id=str(item.get("guid") or link), source_url=link, apply_url=link,
                       employment_type=_plain(item.get("employmentType")),
                       published_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(published)) if isinstance(published, (int, float)) else _plain(published))
            jobs.append(_remote_job(job, restriction or "Worldwide"))
        if len(jobs) >= limit or len(items) < 20:
            break
        time.sleep(REQUEST_PAUSE)
    return jobs[:limit]


def _hackernews(queries: list[str], country: str, limit: int) -> list[dict]:
    from .hackernews import fetch
    return fetch()


_SEARCHERS = {"arbeitsagentur": _arbeitsagentur, "djinni": _djinni, "dou": _dou,
              "remotive": _remotive, "himalayas": _himalayas}
_FEEDS = {"arbeitnow": _arbeitnow, "remoteok": _remoteok, "hackernews": _hackernews}


@dataclass
class ProviderRun:
    provider: str
    jobs: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    queries: list[str] = field(default_factory=list)
    fetched: int = 0
    details_loaded: int = 0
    has_more: bool = False
    stopped: str = ""
    # Complete employer boards read: (source, id prefix, ids) for retirement.
    snapshots: list = field(default_factory=list)
    summary: str = ""

    def report(self) -> dict:
        provider = PROVIDERS[self.provider]
        coverage = self.summary or (
            f"Searched {provider.name} with {len(self.queries)} search{'' if len(self.queries) == 1 else 'es'}: "
            f"{self.fetched} results, {len(self.jobs)} new or updated vacancies kept.")
        if self.details_loaded:
            coverage += f" Loaded {self.details_loaded} full adverts for role matches."
        if self.stopped:
            coverage += " " + self.stopped
        if self.errors:
            coverage += f" {len(self.errors)} searches failed: {self.errors[0]}"
        return {"source": {"type": "market", "provider": self.provider, "name": provider.name},
                "ok": bool(self.jobs) or not self.errors, "fetched": self.fetched, "scanned": self.fetched,
                "available": self.fetched, "matched": len(self.jobs), "has_more": self.has_more,
                "coverage": coverage, **({"error": self.errors[0]} if self.errors and not self.jobs else {})}


def _keep(run: ProviderRun, found: list[dict], tags: list[str], country: str, seen: set) -> None:
    """Keep every vacancy a site returned in a supported country.

    Nothing is discarded for relevance: matching happens in the database, so
    a later search or profile change can use any collected advert. The
    role and saved searches an advert answers are recorded as tags, which let
    the local search find job-site adverts whose text is not loaded yet.
    """
    run.fetched += len(found)
    for job in found:
        job.pop("_created", None)
        key = (job["source"], job["source_id"])
        if key in seen:
            continue
        # A job restricted to other countries (often remote "Spain only") or
        # with no recognisable country cannot be placed in a supported market.
        if not job.get("countries") or (country and country not in job["countries"]):
            continue
        seen.add(key)
        job.setdefault("raw_json", {})
        if isinstance(job["raw_json"], dict):
            job["raw_json"]["matched_queries"] = [query for query in tags if matches_query(job, query)][:5]
        run.jobs.append(job)


def search_provider(name: str, queries: list[str], country: str = "", *, limit_per_query: int = 100,
                    role_terms: list[str] = (), detail_budget: int = 0, state: dict | None = None,
                    progress=None, broad: bool = False, seconds: float = REFRESH_SECONDS,
                    skip_boards: frozenset[str] = frozenset()) -> ProviderRun:
    """Search one provider for several queries. Network only; never raises.

    broad adds the provider's field-wide searches and reads deeper, for a
    refresh that collects everything; incremental positions live in state.
    skip_boards names employer boards already configured as sources.
    """
    run = ProviderRun(name)
    provider = PROVIDERS[name]
    state = state if state is not None else {}
    queries = list(queries[:provider.max_queries])
    tags = list(queries)
    if broad and name in BROAD_QUERIES:
        covered = {query.casefold() for query in queries}
        queries += [query for query in BROAD_QUERIES[name] if query.casefold() not in covered]
    run.queries = list(queries)
    seen: set = set()
    if not queries:
        return run
    deadline = time.monotonic() + seconds
    try:
        if name == "linkedin":
            from .linkedin_public import search as linkedin_search
            linkedin_search(run, queries, country, state, progress=progress)
            _filter_linkedin(run, tags, country)
        elif name == "us_employers":
            from . import employer_pack
            employer_pack.collect(run, tags, state, skip=skip_boards, deadline=deadline, progress=progress,
                                  keep=lambda run, jobs, tags: _keep(run, jobs, tags, "", seen))
        elif name == "arbeitnow":
            newest = int(state.get("arbeitnow_newest") or 0)
            found = _arbeitnow(queries, country, limit_per_query, newest=newest, max_pages=(10 if not newest else 5) if broad else 3)
            if found:
                state["arbeitnow_newest"] = max([newest, *(job.get("_created", 0) for job in found)])
            _keep(run, found, tags, country, seen)
        elif name in _FEEDS:
            _keep(run, _FEEDS[name](queries, country, limit_per_query), tags, country, seen)
        else:
            synced = state.setdefault("arbeitsagentur_synced", {}) if name == "arbeitsagentur" else {}
            today = time.strftime("%Y-%m-%d")
            for index, query in enumerate(queries):
                if time.monotonic() > deadline:
                    run.has_more = True
                    run.stopped = "Time budget reached; the next refresh continues with the remaining searches."
                    break
                options = {}
                if name == "arbeitsagentur" and broad:
                    last = synced.get(query)
                    days = (time.mktime(time.strptime(today, "%Y-%m-%d")) - time.mktime(time.strptime(last, "%Y-%m-%d"))) // 86400 if last else None
                    options = {"since_days": int(min(60, days + 1)) if days is not None else 60, "deadline": deadline}
                limit = 10000 if (broad and name == "arbeitsagentur") else limit_per_query
                try:
                    _keep(run, _SEARCHERS[name](query, country, limit, **options), [query] if query in tags else [], country, seen)
                    if name == "arbeitsagentur" and broad:
                        synced[query] = today
                except _Budget as stop:
                    _keep(run, stop.jobs, [query] if query in tags else [], country, seen)
                    run.has_more = True
                    run.stopped = "Time budget reached; the next refresh continues with the remaining searches."
                    break
                except SourceError as exc:
                    run.errors.append(f"{query}: {exc}")
                if progress is not None and broad and len(queries) > 10 and index % 5 == 4:
                    progress(f"{provider.name}: {index + 1} of {len(queries)} searches, {len(run.jobs):,} vacancies…")
                if index + 1 < len(queries):
                    time.sleep(REQUEST_PAUSE)
    except (SourceError, OSError) as exc:
        run.errors.append(str(exc))
    if detail_budget and name in LAZY_DETAILS and role_terms:
        _load_role_details(run, role_terms, detail_budget, state)
    return run


def _filter_linkedin(run: ProviderRun, tags: list[str], country: str) -> None:
    found, run.jobs = run.jobs, []
    run.fetched = 0
    _keep(run, found, tags, "", set())  # LinkedIn already searched this country.


def _load_role_details(run: ProviderRun, role_terms: list[str], budget: int, state: dict | None) -> None:
    from .profile import role_matches
    for job in run.jobs:
        if run.details_loaded >= budget or run.stopped:
            break
        if not needs_details(job) or not any(role_matches(job["title"], role) for role in role_terms):
            continue
        try:
            apply_details(job, fetch_details(job, state))
            run.details_loaded += 1
        except SourceError as exc:
            if job["source"] == "linkedin" and ("slow down" in str(exc) or "budget" in str(exc)):
                run.stopped = run.stopped or str(exc)
                break
        time.sleep(REQUEST_PAUSE if job["source"] != "linkedin" else 0)


def fetch_details(job: dict, state: dict | None = None) -> dict:
    """Load one advert's full text from a provider whose list omits it."""
    if job.get("source") == "arbeitsagentur":
        return _arbeitsagentur_details(job)
    if job.get("source") == "linkedin":
        from .linkedin_public import details
        return details(job, state if state is not None else {})
    if job.get("source") == "djinni":
        from .sources import fetch_job_url
        found = fetch_job_url(job["source_url"])
        return {"description": found[0].get("description", ""), "company": found[0].get("company", ""),
                "employment_type": found[0].get("employment_type", "")}
    raise SourceError("This vacancy's source already includes its full description.")


def apply_details(job: dict, details: dict) -> dict:
    if details.get("description"):
        job["description"] = details["description"]
    if details.get("company") and job.get("company") in {"", DJINNI_COMPANY, "Employer not named", "Company on LinkedIn"}:
        job["company"] = details["company"]
    if details.get("employment_type") and not job.get("employment_type"):
        job["employment_type"] = details["employment_type"]
    raw = job.get("raw_json") if isinstance(job.get("raw_json"), dict) else {}
    if details.get("external_apply_url"):
        # Kept separate so the board's record never merges into the employer's.
        raw["external_apply_url"] = details["external_apply_url"]
    if details.get("seniority"):
        raw["seniority"] = details["seniority"]
    job["raw_json"] = raw
    return job


def providers_for(country: str, enabled: dict) -> list[str]:
    return [name for name, provider in PROVIDERS.items()
            if enabled.get(name) and (not country or country in provider.countries)]


def provider_catalog() -> list[dict]:
    return [{"id": name, "name": provider.name, "countries": list(provider.countries), "homepage": provider.homepage,
             "opt_in": provider.opt_in, "note": provider.note} for name, provider in PROVIDERS.items()]


def country_name(code: str) -> str:
    return {"US": "United States"}.get(code, COUNTRIES.get(code, ""))
