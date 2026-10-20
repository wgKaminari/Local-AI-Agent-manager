"""LinkedIn's public guest job search: opt-in, polite, never logged in.

LinkedIn publishes job listings to visitors without an account. Its user
agreement nevertheless forbids automated access, so this collector is off
until the user enables it after reading that notice. Safeguards:

* no account, cookies or login, so no LinkedIn account can be affected;
* the agent's honest User-Agent, with no browser disguise or proxy rotation;
* a pause between requests and a small per-run request budget;
* any refusal (429, 999, a login wall) stops the run and starts a cooldown,
  instead of retrying around it.
"""

from __future__ import annotations

import re
import time
from datetime import datetime, timedelta, timezone
from html import unescape
from html.parser import HTMLParser
from urllib.parse import parse_qs, quote, urlencode, urlsplit

from .sources import MAX_RESPONSE_BYTES, SourceError, _job, _link, _plain, _request_retrying, _set_location_countries

BASE = "https://www.linkedin.com/jobs-guest/jobs/api"
PAUSE_SECONDS = 5.0
PAGES_PER_QUERY = 2
MAX_REQUESTS_PER_RUN = 24
COOLDOWN = timedelta(hours=1)
_COUNTRY_LOCATIONS = {"NO": "Norway", "US": "United States", "DE": "Germany", "UA": "Ukraine"}


def _cooldown_message(state: dict) -> str | None:
    until = state.get("linkedin_cooldown_until")
    if not until:
        return None
    try:
        end = datetime.fromisoformat(until)
    except ValueError:
        return None
    if end <= datetime.now(timezone.utc):
        return None
    return f"LinkedIn asked to slow down; paused until {end.astimezone().strftime('%H:%M')}."


def _get(url: str, state: dict) -> str:
    message = _cooldown_message(state)
    if message:
        raise SourceError(message)
    used = state.setdefault("_linkedin_requests", 0)
    if used >= MAX_REQUESTS_PER_RUN:
        raise SourceError("LinkedIn request budget for this run is used up; the next refresh continues.")
    last = state.get("_linkedin_last_request", 0.0)
    wait = PAUSE_SECONDS - (time.monotonic() - last)
    if wait > 0:
        time.sleep(wait)
    state["_linkedin_requests"] = used + 1
    try:
        status, _, body = _request_retrying(url, "GET", None, MAX_RESPONSE_BYTES, {"Accept": "text/html"}, True)
    finally:
        state["_linkedin_last_request"] = time.monotonic()
    if status == 200:
        return body.decode("utf-8", errors="replace")
    if status == 404:
        raise SourceError("LinkedIn no longer shows this job publicly; it may have closed.")
    # 429/999 are throttling; 3xx usually leads to a login wall. Respect both.
    state["linkedin_cooldown_until"] = (datetime.now(timezone.utc) + COOLDOWN).isoformat()
    raise SourceError(f"LinkedIn asked to slow down (HTTP {status}); paused for an hour. Nothing was retried.")


def _card_text(chunk: str, css_class: str) -> str:
    match = re.search(r'class="[^"]*\b' + re.escape(css_class) + r'\b[^"]*"[^>]*>(.*?)</(?:h3|h4|span|a|div)>', chunk, re.S)
    return _plain(unescape(match.group(1))) if match else ""


def parse_search(html: str, country: str = "") -> list[dict]:
    jobs = []
    for chunk in re.split(r"<li[\s>]", html)[1:]:
        identifier = re.search(r"urn:li:jobPosting:(\d+)", chunk)
        title = _card_text(chunk, "base-search-card__title")
        if not identifier or not title:
            continue
        company = _card_text(chunk, "base-search-card__subtitle")
        location = _card_text(chunk, "job-search-card__location")
        posted = re.search(r'<time[^>]*datetime="([0-9-]{10})"', chunk)
        link = f"https://www.linkedin.com/jobs/view/{identifier.group(1)}"
        job = _job(title=title, company=company or "Company on LinkedIn", location=location or _COUNTRY_LOCATIONS.get(country, ""),
                   source="linkedin", source_id=identifier.group(1), source_url=link, apply_url=link,
                   published_at=posted.group(1) if posted else "")
        _set_location_countries(job, [(job["location"], None)])
        if not job.get("countries") and country:
            # LinkedIn listed it for this country (often a remote role).
            job["countries"] = [country]
            job["raw_json"]["resolved_location_countries"] = [country]
        jobs.append(job)
    return jobs


def search(run, queries: list[str], country: str, state: dict, *, progress=None) -> None:
    """Append public LinkedIn results for each query to run.jobs."""
    countries = [country] if country else list(_COUNTRY_LOCATIONS)
    for code in countries:
        for query in queries:
            for page in range(PAGES_PER_QUERY):
                params = {"keywords": query, "location": _COUNTRY_LOCATIONS[code], "f_TPR": "r2592000", "start": page * 10}
                try:
                    found = parse_search(_get(f"{BASE}/seeMoreJobPostings/search?{urlencode(params)}", state), code)
                except SourceError as exc:
                    if "budget" in str(exc):
                        run.has_more = True
                        run.stopped = str(exc)
                    elif "slow down" in str(exc):
                        run.stopped = str(exc)
                    else:
                        run.errors.append(f"{query}: {exc}")
                        continue
                    return
                run.jobs.extend(found)
                if len(found) < 10:
                    break
            if progress is not None:
                progress(f"LinkedIn: searched “{query}”…")


class _Markup(HTMLParser):
    """Text inside the first element carrying the description class."""

    def __init__(self, css_class: str):
        super().__init__(convert_charrefs=True)
        self.css_class, self.depth, self.done = css_class, 0, False
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if self.done:
            return
        if self.depth:
            self.depth += tag not in {"br", "img", "hr", "input", "meta", "link"}
            if tag in {"p", "li", "br", "div", "h1", "h2", "h3", "ul"}:
                self.parts.append("\n")
        elif self.css_class in (dict(attrs).get("class") or "").split():
            self.depth = 1

    def handle_endtag(self, tag):
        if self.depth and not self.done:
            self.depth -= 1
            if tag in {"p", "li", "div", "ul"}:
                self.parts.append("\n")
            if not self.depth:
                self.done = True

    def handle_data(self, data):
        if self.depth and not self.done:
            self.parts.append(data)


def parse_details(html: str) -> dict:
    parser = _Markup("show-more-less-html__markup")
    parser.feed(html)
    description = "\n".join(line for line in (" ".join(part.split()) for part in "".join(parser.parts).splitlines()) if line)
    criteria = {}
    for name, value in re.findall(r'description__job-criteria-subheader">\s*(.*?)\s*</h3>\s*<span[^>]*>\s*(.*?)\s*</span>', html, re.S):
        criteria[_plain(unescape(name)).casefold()] = _plain(unescape(value))
    external = ""
    code = re.search(r'<code id="applyUrl"[^>]*><!--"(.*?)"--></code>', html, re.S)
    if code:
        target = parse_qs(urlsplit(unescape(code.group(1))).query).get("url", [""])[0]
        external = _link(target, "https://www.linkedin.com/")
    return {"description": description, "employment_type": criteria.get("employment type", ""),
            "seniority": criteria.get("seniority level", ""), "external_apply_url": external}


def details(job: dict, state: dict) -> dict:
    identifier = str(job.get("source_id", ""))
    if not identifier.isdecimal():
        raise SourceError("This LinkedIn job has an invalid identifier.")
    result = parse_details(_get(f"{BASE}/jobPosting/{quote(identifier)}", state))
    if not result["description"]:
        raise SourceError("LinkedIn showed this job without its description; open it on LinkedIn.")
    return result
