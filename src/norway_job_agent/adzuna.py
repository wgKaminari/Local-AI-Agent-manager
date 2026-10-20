"""Adzuna's job search API: a whole-market US source with your own free key.

The USA has no free national job feed like NAV or the Bundesagentur, and the
big US job sites forbid automated collection. Adzuna collects adverts from
many US job sites and employers and offers a free developer key
(https://developer.adzuna.com). Its terms allow 25 requests a minute, 250 a
day, 1,000 a week and 2,500 a month by default; this module stays below 90%
of each, spreads the month evenly over its days and keeps the count in
market-state.json. Lists carry an excerpt of
each advert, not the full text, and every result links to Adzuna, which the
terms ask users to credit as the source.

The key is saved in adzuna-key.json in the data folder, never in
sources.json or backups, and the interface only ever shows its last
characters.
"""

from __future__ import annotations

import json
import re
import time
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

from .sources import MAX_ATS_RESPONSE_BYTES, SourceError, _job, _link, _plain, _request_retrying

KEY_FILE = "adzuna-key.json"
API = "https://api.adzuna.com/v1/api/jobs"
COUNTRY_CODES = {"US": "us"}
PER_PAGE = 50
PAGES_PER_SEARCH = 2
# A refresh may use up to the day's share: 2,250 a month is about 72 a day.
REQUESTS_PER_REFRESH = 80
REQUESTS_PER_LIVE_SEARCH = 2
LIMITS = {"day": 250, "week": 1000, "month": 2500}
SHARE = 0.9
PAUSE_SECONDS = 2.6  # 25 requests a minute
STATE_KEY = "adzuna_usage"
_ID = re.compile(r"[A-Za-z0-9]{4,64}\Z")
_KEY = re.compile(r"[A-Za-z0-9]{8,128}\Z")


def _path(data_dir: Path) -> Path:
    return Path(data_dir) / KEY_FILE


def read_key(data_dir: Path) -> dict | None:
    try:
        value = json.loads(_path(data_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if isinstance(value, dict) and _ID.match(str(value.get("app_id", ""))) and _KEY.match(str(value.get("app_key", ""))):
        return {"app_id": value["app_id"], "app_key": value["app_key"], "saved_at": str(value.get("saved_at", ""))}
    return None


def key_status(data_dir: Path) -> dict:
    """What the interface may show: never the key itself."""
    key = read_key(data_dir)
    if key is None:
        return {"configured": False}
    return {"configured": True, "app_id_hint": "…" + key["app_id"][-4:], "saved_at": key["saved_at"]}


def _status(key: dict, url: str) -> tuple[int, bytes]:
    status, _, body = _request_retrying(f"{url}&" + urlencode({"app_id": key["app_id"], "app_key": key["app_key"]}),
                                        "GET", None, MAX_ATS_RESPONSE_BYTES)
    return status, body


def _check(status: int) -> None:
    if status in {401, 403}:
        raise SourceError("Adzuna did not accept this Application ID and key. Copy both from "
                          "developer.adzuna.com (Dashboard → API access details) and try again.")
    if status == 429:
        raise SourceError("Adzuna's request limit for this key is reached; try again later.")
    if not 200 <= status < 300:
        raise SourceError(f"Adzuna returned HTTP {status}; try again later.")


def save_key(data_dir: Path, app_id: str, app_key: str, *, state: dict | None = None) -> dict:
    """Check the key with one small search, then save it."""
    app_id, app_key = app_id.strip(), app_key.strip()
    if not _ID.match(app_id) or not _KEY.match(app_key):
        raise ValueError("Paste Adzuna's Application ID and Application key: letters and digits only.")
    key = {"app_id": app_id, "app_key": app_key}
    status, _ = _status(key, f"{API}/us/search/1?" + urlencode({"results_per_page": 1, "what": "engineer"}))
    if state is not None:
        _count(state)
    _check(status)
    path = _path(data_dir)
    partial = path.with_suffix(".tmp")
    partial.write_text(json.dumps({**key, "saved_at": datetime.now(timezone.utc).isoformat()}), encoding="utf-8")
    partial.replace(path)
    return key_status(data_dir)


def delete_key(data_dir: Path) -> dict:
    _path(data_dir).unlink(missing_ok=True)
    return key_status(data_dir)


def _periods(today: date) -> dict:
    year, week, _ = today.isocalendar()
    return {"day": today.isoformat(), "week": f"{year}-W{week:02d}", "month": today.strftime("%Y-%m")}


def remaining(state: dict, today: date | None = None) -> int:
    """Requests left today within 90% of every Adzuna limit."""
    usage = state.get(STATE_KEY) if isinstance(state.get(STATE_KEY), dict) else {}
    left = []
    for period, value in _periods(today or date.today()).items():
        used = usage.get(f"{period}_count", 0) if usage.get(period) == value else 0
        left.append(int(LIMITS[period] * SHARE) - int(used))
    return max(0, min(left))


def _count(state: dict, today: date | None = None) -> None:
    usage = state.get(STATE_KEY) if isinstance(state.get(STATE_KEY), dict) else {}
    for period, value in _periods(today or date.today()).items():
        if usage.get(period) != value:
            usage[period], usage[f"{period}_count"] = value, 0
        usage[f"{period}_count"] = int(usage.get(f"{period}_count", 0)) + 1
    state[STATE_KEY] = usage


def _parse(item: dict) -> dict | None:
    if not isinstance(item, dict) or not str(item.get("id", "")).strip():
        return None
    title = _plain(item.get("title"))
    link = _link(item.get("redirect_url"), "https://www.adzuna.com/")
    if not title or not link:
        return None
    company = _plain((item.get("company") or {}).get("display_name")) if isinstance(item.get("company"), dict) else ""
    location = _plain((item.get("location") or {}).get("display_name")) if isinstance(item.get("location"), dict) else ""
    kind = {"full_time": "Full-time", "part_time": "Part-time"}.get(item.get("contract_time"), "")
    if item.get("contract_type") == "contract":
        kind = ", ".join(filter(None, [kind, "Contract"]))
    job = _job(title=title, company=company or "Employer not named",
               location=", ".join(filter(None, [location, "United States"])) if "United States" not in location else location,
               description=_plain(item.get("description")), source="adzuna", source_id=str(item["id"]),
               source_url=link, apply_url=link, employment_type=kind, published_at=_plain(item.get("created")))
    job["countries"] = ["US"]
    raw = {"resolved_location_countries": ["US"], "excerpt_only": True}
    salary = [item.get(key) for key in ("salary_min", "salary_max") if isinstance(item.get(key), (int, float))]
    if salary:
        raw["salary"] = {"min": min(salary), "max": max(salary), "estimated": bool(item.get("salary_is_predicted") in {1, "1", True})}
    job["raw_json"] = raw
    return job


def _daily_pace(state: dict, today: date | None = None) -> int:
    """Requests left today so the monthly allowance lasts the whole month."""
    import calendar
    today = today or date.today()
    usage = state.get(STATE_KEY) if isinstance(state.get(STATE_KEY), dict) else {}
    used_today = usage.get("day_count", 0) if usage.get("day") == today.isoformat() else 0
    days = calendar.monthrange(today.year, today.month)[1]
    return max(0, int(LIMITS["month"] * SHARE / days) - int(used_today))


def _it_category(key: dict, state: dict) -> str:
    """Adzuna's tag for IT jobs in the USA, read once from its category list."""
    cached = state.get("adzuna_it_category")
    if isinstance(cached, str) and cached:
        return cached
    status, body = _status(key, f"{API}/{COUNTRY_CODES['US']}/categories?content-type=application/json")
    _count(state)
    _check(status)
    try:
        items = json.loads(body).get("results")
    except (ValueError, AttributeError) as exc:
        raise SourceError("Adzuna returned an unexpected category list.") from exc
    tags = {str(item.get("tag")): str(item.get("label", "")) for item in items or [] if isinstance(item, dict) and item.get("tag")}
    tag = "it-jobs" if "it-jobs" in tags else next(
        (tag for tag, label in tags.items() if re.search(r"\bIT\b|information technology", label)), "")
    if not tag:
        raise SourceError("Adzuna has no IT category for the USA; your role searches still ran.")
    state["adzuna_it_category"] = tag
    return tag


def search(run, queries: list[str], key: dict, state: dict, *, keep, budget: int, deadline: float, progress=None,
           it_only: bool = False) -> None:
    """Search Adzuna USA within the quota, newest adverts of the last 30 days first.

    Your roles come first. With "IT jobs only" the rest of the day's share
    browses Adzuna's whole IT category; otherwise it reads further pages of
    your role searches.
    """
    allowed = min(budget, remaining(state), _daily_pace(state))
    if allowed <= 0:
        run.stopped = ("Today's share of your Adzuna key's free allowance is used; the next day continues. "
                       "(Free keys allow 2,500 requests a month.)")
        return
    category = ""
    before = remaining(state)
    if it_only:
        try:
            category = _it_category(key, state)
        except SourceError as exc:
            run.errors.append(str(exc))
            if "did not accept" in str(exc):
                return
        if category:
            # The category lookup (once per key) counts against this run too.
            allowed = min(budget - (before - remaining(state)), remaining(state), _daily_pace(state))
    roles = [(query, 1) for query in queries[:5]] if category else [
        (query, page) for page in range(1, PAGES_PER_SEARCH + 1) for query in queries]
    browse = [("", page) for page in range(1, allowed + 1)] if category else []
    exhausted: set[str] = set()
    used = 0
    for query, page in [*roles, *browse]:
        if used >= allowed:
            run.has_more = True
            break
        if query in exhausted:
            continue
        if time.monotonic() > deadline:
            run.has_more = True
            run.stopped = "Time budget reached; the next refresh continues."
            break
        if used:
            time.sleep(PAUSE_SECONDS)
        params = {"results_per_page": PER_PAGE, "max_days_old": 30, "sort_by": "date", "content-type": "application/json"}
        if query:
            params["what"] = query
        if category:
            params["category"] = category
        status, body = _status(key, f"{API}/{COUNTRY_CODES['US']}/search/{page}?" + urlencode(params))
        used += 1
        _count(state)
        label = query or "IT category"
        try:
            _check(status)
            data = json.loads(body)
        except ValueError as exc:
            run.errors.append(f"{label}: {exc if isinstance(exc, SourceError) else 'Adzuna returned invalid data.'}")
            if status in {401, 403, 429}:
                break
            continue
        results = data.get("results") if isinstance(data, dict) else None
        if not isinstance(results, list):
            run.errors.append(f"{label}: Adzuna returned an unexpected response.")
            continue
        jobs = [job for job in map(_parse, results) if job is not None]
        for job in jobs if category else ():
            job["raw_json"]["it_category"] = True
        keep(run, jobs, queries)
        if len(results) < PER_PAGE or page * PER_PAGE >= int(data.get("count") or 0):
            exhausted.add(query)
        if progress is not None and used % 10 == 0:
            progress(f"Adzuna: {used} of {allowed} requests, {len(run.jobs):,} vacancies…")
    run.summary_note = (f"Used {used} Adzuna requests" + (" (your roles, then the newest IT jobs)" if category else "")
                        + f"; {remaining(state)} left today within your key's free allowance.")
