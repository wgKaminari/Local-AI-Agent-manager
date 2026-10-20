"""A complete local index of NAV's public vacancy feed.

The feed lists every Norwegian vacancy NAV publishes (FINN-origin ads are
excluded by NAV), but each entry carries only a title, employer and
municipality. The previous collector read five feed pages per refresh and
kept only titles matching the profile at that moment, so most of the market
was skipped for good. Instead, every refresh now reads the feed to its tail
into this index. Any search, including a new one typed later, is matched
against all indexed titles, and only matches are downloaded in full.

The index is a rebuildable cache in its own SQLite file; it never holds
personal data. Its feed position advances only together with the entries it
read, so an interrupted run resumes where it stopped.
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path

from . import nav
from .profile import _role_terms, _role_text, contains_phrase
from .sources import SourceError, _TransientSourceError

INDEX_FILE = "market-index.db"
LOOKBACK_DAYS = 90
MAX_PAGES_PER_RUN = 400
SYNC_SECONDS = 420
DETAIL_WORKERS = 6
MAX_DETAILS_PER_RUN = 20000
# Seconds a refresh spends downloading full adverts; the rest continue next time.
HYDRATE_SECONDS = 480


class NavIndex:
    def __init__(self, data_dir: Path):
        self._connection = sqlite3.connect(Path(data_dir) / INDEX_FILE, timeout=30)
        self._connection.execute("PRAGMA journal_mode = WAL")
        self._connection.executescript("""
            CREATE TABLE IF NOT EXISTS nav_ads (
                uuid TEXT PRIMARY KEY,
                title TEXT NOT NULL DEFAULT '',
                employer TEXT NOT NULL DEFAULT '',
                municipal TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL,
                modified TEXT NOT NULL DEFAULT '',
                detail_url TEXT NOT NULL,
                hydrated TEXT NOT NULL DEFAULT '',
                checked_queries TEXT NOT NULL DEFAULT '[]',
                applied_status TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS nav_ads_status ON nav_ads(status);
            CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        """)
        # "IT jobs only" stores IT adverts alone; outside = 1 remembers that an
        # unchanged advert was read and is not IT, so it is not read again.
        columns = {row[1] for row in self._connection.execute("PRAGMA table_info(nav_ads)")}
        if "outside" not in columns:
            self._connection.execute("ALTER TABLE nav_ads ADD COLUMN outside INTEGER NOT NULL DEFAULT 0")
        # Adverts loaded before 0.7.7 lack NAV's occupation category, which
        # "IT jobs only" needs: load each active advert once more.
        if self._connection.execute("SELECT 1 FROM meta WHERE key = 'occupations_v1'").fetchone() is None:
            self._connection.execute("UPDATE nav_ads SET hydrated = ''")
            self._connection.execute("INSERT INTO meta (key, value) VALUES ('occupations_v1', '1')")
        self._connection.commit()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._connection.close()

    def cursor(self) -> dict:
        row = self._connection.execute("SELECT value FROM meta WHERE key = 'nav_cursor'").fetchone()
        return json.loads(row[0]) if row else {}

    def save_page(self, entries: list[tuple], cursor: dict) -> None:
        with self._connection:
            self._connection.executemany("""
                INSERT INTO nav_ads (uuid, title, employer, municipal, status, modified, detail_url)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(uuid) DO UPDATE SET
                    title = CASE WHEN excluded.title IN ('', '...') THEN nav_ads.title ELSE excluded.title END,
                    employer = CASE WHEN excluded.employer = '' THEN nav_ads.employer ELSE excluded.employer END,
                    municipal = CASE WHEN excluded.municipal = '' THEN nav_ads.municipal ELSE excluded.municipal END,
                    status = excluded.status, modified = excluded.modified, detail_url = excluded.detail_url
            """, entries)
            self._connection.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('nav_cursor', ?)", (json.dumps(cursor),))

    def counts(self) -> dict:
        rows = dict(self._connection.execute("SELECT status, COUNT(*) FROM nav_ads GROUP BY status").fetchall())
        return {"active": rows.get("ACTIVE", 0), "inactive": rows.get("INACTIVE", 0)}

    def active(self) -> list[tuple]:
        return self._connection.execute(
            "SELECT uuid, title, employer, municipal, modified, detail_url, hydrated, checked_queries, outside"
            " FROM nav_ads WHERE status = 'ACTIVE'").fetchall()

    def mark_hydrated(self, values: list[tuple[str, str, str]]) -> None:
        """(modified, checked queries JSON, uuid): this version was checked for these queries."""
        with self._connection:
            self._connection.executemany(
                "UPDATE nav_ads SET hydrated = ?, checked_queries = ?, outside = 0 WHERE uuid = ?", values)

    def mark_outside(self, uuids: list[str]) -> None:
        with self._connection:
            self._connection.executemany("UPDATE nav_ads SET outside = 1 WHERE uuid = ?", [(u,) for u in uuids])

    def pending_withdrawals(self) -> list[str]:
        return [row[0] for row in self._connection.execute(
            "SELECT uuid FROM nav_ads WHERE status = 'INACTIVE' AND applied_status <> 'INACTIVE'")]

    def mark_applied(self, uuids: list[str]) -> None:
        with self._connection:
            self._connection.executemany("UPDATE nav_ads SET applied_status = 'INACTIVE' WHERE uuid = ?", [(u,) for u in uuids])


def sync(data_dir: Path, *, token: str | None = None, progress=None,
         max_pages: int = MAX_PAGES_PER_RUN, seconds: float = SYNC_SECONDS) -> dict:
    """Read NAV's feed from the saved position to its tail (or the time budget)."""
    started = time.monotonic()
    access_token, experimental = nav._get_token(token)
    with NavIndex(data_dir) as index:
        state = index.cursor()
        initial = state.get("initial_since") or format_datetime(
            datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS), usegmt=True)
        cursor = {"url": nav._nav_url(state.get("url") or nav.FEED_URL),
                  "etag": nav._header(state.get("etag", "")),
                  "last_modified": nav._header(state.get("last_modified", "") or ("" if state.get("url") else initial)),
                  "initial_since": nav._header(initial)}
        pages = events = 0
        has_more, visited, interrupted = False, set(), ""
        for _ in range(max_pages):
            current = cursor["url"]
            if current in visited:
                raise SourceError("NAV repeated a feed page; the saved position was kept.")
            visited.add(current)
            conditions = {key: cursor[field] for key, field in (("If-None-Match", "etag"), ("If-Modified-Since", "last_modified")) if cursor[field]}
            try:
                status, headers, body, final_url = nav._fetch_nav(current, access_token, conditions)
            except _TransientSourceError as exc:
                if not pages:
                    raise
                # Pages read so far are saved; the next refresh resumes here
                # and this one still loads adverts from the index.
                has_more, interrupted = True, str(exc)
                break
            pages += 1
            if status == 304:
                for field, header in (("etag", "etag"), ("last_modified", "last-modified")):
                    if header in headers:
                        cursor[field] = nav._header(headers[header])
                index.save_page([], cursor)
                has_more = False
                break
            page = nav._decode(body)
            if not isinstance(page, dict) or not isinstance(page.get("items"), list):
                raise SourceError("NAV returned an invalid feed page; the saved position was kept.")
            page_url = nav._nav_url(page.get("feed_url") or final_url, final_url)
            entries = []
            for item in page["items"]:
                if not isinstance(item, dict) or not isinstance(item.get("_feed_entry"), dict):
                    raise SourceError("NAV returned an invalid feed entry; the saved position was kept.")
                entry = item["_feed_entry"]
                if entry.get("status") not in {"ACTIVE", "INACTIVE"}:
                    raise SourceError("NAV returned an invalid feed status; the saved position was kept.")
                entries.append((nav._uuid(entry.get("uuid")), str(entry.get("title") or item.get("title") or "")[:500],
                                str(entry.get("businessName") or "")[:300], str(entry.get("municipal") or "")[:200],
                                entry["status"], str(entry.get("sistEndret") or item.get("date_modified") or "")[:40],
                                nav._nav_url(item.get("url", ""), page_url)))
            events += len(entries)
            next_url = page.get("next_url")
            if next_url:
                cursor.update(url=nav._nav_url(next_url, page_url), etag="", last_modified="")
                has_more = True
            else:
                cursor.update(url=page_url, etag=nav._header(headers.get("etag", "")),
                              last_modified=nav._header(headers.get("last-modified", "")))
                has_more = False
            index.save_page(entries, cursor)
            if progress is not None and pages % 10 == 0:
                progress(f"Reading NAV's vacancy feed… {pages} pages, {events:,} updates.")
            if not has_more or time.monotonic() - started > seconds:
                break
        counts = index.counts()
    return {"pages": pages, "events": events, "has_more": has_more, "interrupted": interrupted,
            "experimental_token": experimental, **counts}


def _title_candidate(folded_title: str, query: str, tokens: list[str]) -> bool:
    if any(contains_phrase(folded_title, term) for term in _role_terms(query)):
        return True
    if not tokens:
        return False
    present = [token for token in tokens if contains_phrase(folded_title, token)]
    if len(present) == len(tokens):
        return True
    from .market import GENERIC_WORDS
    return any(token not in GENERIC_WORDS for token in present)


def mark_outside(data_dir: Path, uuids: list[str]) -> None:
    with NavIndex(data_dir) as index:
        index.mark_outside(uuids)


def find(data_dir: Path, queries: list[str], *, known: set[str] | frozenset[str] = frozenset(), skip_outside: bool = False,
         everything: bool = False) -> list[dict]:
    """Active ads that still need their full advert, search matches first.

    Stored vacancies are refreshed when NAV changes them. With everything,
    every other active advert not stored yet follows, so the database holds
    the whole Norwegian market; otherwise an advert is downloaded once per
    version for each search that has not checked it yet.
    """
    from .market import query_tokens
    prepared = [(query, query_tokens(query)) for query in queries if query.strip()]
    if not prepared and not everything:
        return []
    # A cheap substring screen avoids word-boundary checks on 40,000 titles.
    hints = {hint for query, tokens in prepared for hint in [*(_role_terms(query)), *tokens] if hint}
    results, rest = [], []
    with NavIndex(data_dir) as index:
        for uuid, title, employer, municipal, modified, detail_url, hydrated, checked, outside in index.active():
            folded = _role_text(title)
            matched = [query for query, tokens in prepared if _title_candidate(folded, query, tokens)] \
                if any(hint in folded for hint in hints) else []
            current = hydrated == modified
            if skip_outside and current and outside:
                continue  # Read already and not an IT job; read again once NAV changes it.
            if everything:
                if current and uuid in known:
                    continue
            elif not matched or (current and (uuid in known or set(matched) <= set(json.loads(checked or "[]")))):
                continue
            previous = json.loads(checked or "[]") if current else []
            (results if matched else rest).append({
                "uuid": uuid, "title": title, "employer": employer, "municipal": municipal,
                "modified": modified, "detail_url": detail_url, "queries": matched,
                "checked": list(dict.fromkeys([*previous, *matched]))})
    return results + rest


def hydrate(data_dir: Path, candidates: list[dict], *, token: str | None = None, progress=None,
            limit: int = MAX_DETAILS_PER_RUN, seconds: float = HYDRATE_SECONDS) -> dict:
    """Download full adverts for candidates. Network only; nothing is marked.

    Every downloaded active advert is kept; the searches it answers become
    its tags. Returns jobs, withdrawn ids, the remaining candidate count, the
    marks to record with mark_hydrated once the jobs are safely stored, and
    how many downloads failed with the most common reason. Failed adverts
    stay unmarked and load on the next refresh. If NAV rate-limits or rejects
    the token, the run stops instead of repeating the request thousands of
    times.
    """
    from .market import matches_query
    access_token, _ = nav._get_token(token)
    selected = candidates[:limit]
    started = time.monotonic()

    def load(candidate):
        try:
            status, _, body, _ = nav._fetch_nav(candidate["detail_url"], access_token)
            if status != 200:
                return candidate, None, "missing"
            return candidate, nav._normalize(candidate["uuid"], nav._decode(body)), None
        except SourceError as exc:
            return candidate, None, str(exc)

    jobs, withdrawn, hydrated = [], [], []
    failures: Counter = Counter()
    done = 0
    with ThreadPoolExecutor(max_workers=DETAIL_WORKERS, thread_name_prefix="nav-detail") as pool:
        for start in range(0, len(selected), 120):
            if time.monotonic() - started > seconds or _stops_run(failures):
                break
            for candidate, job, error in pool.map(load, selected[start:start + 120]):
                done += 1
                if error not in {None, "missing"}:
                    failures[error] += 1
                    continue
                if error == "missing" or job is None:
                    withdrawn.append(candidate["uuid"])
                elif job is not None:
                    # Multi-word searches are confirmed against the full advert.
                    job["raw_json"] = {**(job.get("raw_json") or {}),
                                       "matched_queries": [query for query in candidate["queries"] if matches_query(job, query)][:5]}
                    jobs.append(job)
                if error is None:
                    hydrated.append((candidate["modified"], json.dumps(candidate["checked"][-50:]), candidate["uuid"]))
            if progress is not None:
                progress(f"Loading NAV adverts… {done:,} of {len(selected):,}.")
    failed = sum(failures.values())
    return {"jobs": jobs, "withdrawn": withdrawn, "remaining": max(0, len(candidates) - done) + failed,
            "marks": hydrated, "failed": failed, "failure": failures.most_common(1)[0][0] if failures else ""}


def _stops_run(failures: Counter) -> bool:
    return any("rate limiting" in reason or "access token" in reason for reason in failures)


def mark_hydrated(data_dir: Path, marks: list[tuple[str, str, str]]) -> None:
    with NavIndex(data_dir) as index:
        index.mark_hydrated(marks)


def mark_inactive(data_dir: Path, uuids: list[str]) -> None:
    """Adverts found closed while loading; their withdrawal is already applied."""
    with NavIndex(data_dir) as index:
        with index._connection:
            index._connection.executemany(
                "UPDATE nav_ads SET status = 'INACTIVE', applied_status = 'INACTIVE' WHERE uuid = ?", [(u,) for u in uuids])


def pending_withdrawals(data_dir: Path) -> list[str]:
    with NavIndex(data_dir) as index:
        return index.pending_withdrawals()


def mark_withdrawals_applied(data_dir: Path, uuids: list[str]) -> None:
    with NavIndex(data_dir) as index:
        index.mark_applied(uuids)


def counts(data_dir: Path) -> dict:
    if not (Path(data_dir) / INDEX_FILE).exists():
        return {"active": 0, "inactive": 0}
    with NavIndex(data_dir) as index:
        return index.counts()
