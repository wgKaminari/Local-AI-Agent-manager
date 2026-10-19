"""Built-in US employer boards, collected as one job site.

A few hundred company boards would pass the 250 configured sources and,
saved in sources.json, would only reach new installs. Shipped as the "US
tech employers" job site, they need no setup and updates can add boards.

Greenhouse, Lever and Ashby boards are read whole, like configured sources,
and a complete board retires its vanished postings. Workday has no whole-board
feed, so each Workday employer is searched for your first roles. A board is
read at most once every REREAD_HOURS; refreshes in between skip it, which
keeps a refresh to a few hundred megabytes a day instead of each time.
"""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path

PACK_FILE = Path(__file__).parent / "data" / "us_employer_boards.json"
STATE_KEY = "us_employers_read"
REREAD_HOURS = 20
WORKERS = 4
WORKDAY_SEARCHES = 2
WORKDAY_JOBS = 25


@lru_cache(maxsize=1)
def _pack() -> dict:
    return json.loads(PACK_FILE.read_text(encoding="utf-8"))


def boards() -> list[dict]:
    return [dict(entry) for entry in _pack()["boards"]]


def source_key(source: dict) -> str:
    """One identity per employer board, matching configured sources too."""
    kind = source.get("type")
    if kind == "workday":
        return "workday:" + str(source.get("url", "")).casefold().rstrip("/")
    if kind == "lever":
        return f"lever:{source.get('region', 'global')}:{str(source.get('board', '')).casefold()}"
    return f"{kind}:{str(source.get('board', '')).casefold()}"


def configured_keys(sources: list[dict]) -> frozenset[str]:
    return frozenset(source_key(source) for source in sources
                     if isinstance(source, dict) and source.get("type") in {"greenhouse", "lever", "ashby", "workday"})


def _source(entry: dict, search: str | None = None) -> dict:
    from .countries import COUNTRIES
    # Every supported country: a US company's Berlin or Oslo office counts too.
    source = {key: entry[key] for key in ("type", "board", "url", "name") if key in entry}
    source["countries"] = list(COUNTRIES)
    if entry["type"] == "workday":
        source.update(search_text=search or "", max_jobs=WORKDAY_JOBS)
    return source


def _read_at(value) -> datetime | None:
    try:
        moment = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def collect(run, tags: list[str], state: dict, *, keep, skip: frozenset[str] = frozenset(),
            deadline: float, progress=None, now: datetime | None = None) -> None:
    """Read the boards that are due into run; network only.

    keep(run, jobs, tags) stores accepted jobs on run. Never-read boards go
    first, then the longest unread, so a refresh that runs out of time
    continues where it stopped.
    """
    from .collection import fetch_source
    from .countries import job_countries

    now = now or datetime.now(timezone.utc)
    read = state.setdefault(STATE_KEY, {})
    due, recent, configured = [], 0, 0
    for entry in boards():
        key = source_key(entry)
        if key in skip:
            configured += 1
            continue
        last = _read_at(read.get(key))
        if last is not None and now - last < timedelta(hours=REREAD_HOURS):
            recent += 1
            continue
        due.append((key, entry))
    due.sort(key=lambda item: read.get(item[0], ""))
    searches = list(dict.fromkeys(tags[:WORKDAY_SEARCHES])) or [""]
    tasks = [(key, entry, _source(entry, search))
             for key, entry in due for search in (searches if entry["type"] == "workday" else [None])]

    def fetch(task):
        if time.monotonic() > deadline:
            return task, None
        return task, fetch_source(task[2])

    failed: set[str] = set()
    finished: set[str] = set()
    postings = boards_read = 0
    with ThreadPoolExecutor(max_workers=WORKERS, thread_name_prefix="us-employers") as pool:
        for done, ((key, entry, _), batch) in enumerate(pool.map(fetch, tasks), 1):
            if batch is None:
                run.has_more = True
                continue
            result = batch["result"]
            if batch["jobs"] is None:
                failed.add(key)
                run.errors.append(f"{entry['name']}: {result.get('error', 'not read')}")
                continue
            postings += result.get("fetched", 0)
            for job in batch["jobs"]:
                job["countries"] = job_countries(job)
            keep(run, batch["jobs"], tags)
            if batch["snapshot"] is not None:
                run.snapshots.append((batch["kind"], *batch["snapshot"]))
            finished.add(key)
            if progress is not None and done % 40 == 0:
                progress(f"US tech employers: {done} of {len(tasks)} board reads, {len(run.jobs):,} vacancies…")
    stamp = now.isoformat()
    for key in finished - failed:
        read[key] = stamp
        boards_read += 1
    run.summary = (f"Read {boards_read} of {len(due) + recent + configured} US employer boards: "
                   f"{postings:,} postings, {len(run.jobs):,} in your countries kept.")
    if recent:
        run.summary += f" {recent} boards were read in the last {REREAD_HOURS} hours and wait for a later refresh."
    if configured:
        run.summary += f" {configured} are already among your sources."
    if run.has_more:
        run.stopped = "Time budget reached; the next refresh continues with the remaining boards."
