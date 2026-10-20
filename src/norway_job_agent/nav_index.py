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
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path

from . import nav
from .profile import _role_terms, _role_text, contains_phrase
from .sources import SourceError

INDEX_FILE = "market-index.db"
LOOKBACK_DAYS = 90
MAX_PAGES_PER_RUN = 400
SYNC_SECONDS = 420
DETAIL_WORKERS = 4
MAX_DETAILS_PER_RUN = 800


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
            "SELECT uuid, title, employer, municipal, modified, detail_url, hydrated, checked_queries"
            " FROM nav_ads WHERE status = 'ACTIVE'").fetchall()

    def mark_hydrated(self, values: list[tuple[str, str, str]]) -> None:
        """(modified, checked queries JSON, uuid): this version was checked for these queries."""
        with self._connection:
            self._connection.executemany("UPDATE nav_ads SET hydrated = ?, checked_queries = ? WHERE uuid = ?", values)

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
        has_more, visited = False, set()
        for _ in range(max_pages):
            current = cursor["url"]
            if current in visited:
                raise SourceError("NAV repeated a feed page; the saved position was kept.")
            visited.add(current)
            conditions = {key: cursor[field] for key, field in (("If-None-Match", "etag"), ("If-Modified-Since", "last_modified")) if cursor[field]}
            status, headers, body, final_url = nav._fetch_nav(current, access_token, conditions)
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
    return {"pages": pages, "events": events, "has_more": has_more, "experimental_token": experimental, **counts}


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


def find(data_dir: Path, queries: list[str], *, known: set[str] | frozenset[str] = frozenset()) -> list[dict]:
    """Active ads whose titles answer a query and still need their full advert.

    Saved vacancies are refreshed when NAV changes them; other adverts are
    downloaded once per version for each query that has not checked them yet.
    """
    from .market import query_tokens
    prepared = [(query, query_tokens(query)) for query in queries if query.strip()]
    if not prepared:
        return []
    # A cheap substring screen avoids word-boundary checks on 40,000 titles.
    hints = {hint for query, tokens in prepared for hint in [*(_role_terms(query)), *tokens] if hint}
    results = []
    with NavIndex(data_dir) as index:
        for uuid, title, employer, municipal, modified, detail_url, hydrated, checked in index.active():
            folded = _role_text(title)
            if not any(hint in folded for hint in hints):
                continue
            matched = [query for query, tokens in prepared if _title_candidate(folded, query, tokens)]
            if not matched:
                continue
            if hydrated == modified and (uuid in known or set(matched) <= set(json.loads(checked or "[]"))):
                continue
            previous = json.loads(checked or "[]") if hydrated == modified else []
            results.append({"uuid": uuid, "title": title, "employer": employer, "municipal": municipal,
                            "modified": modified, "detail_url": detail_url, "queries": matched,
                            "checked": list(dict.fromkeys([*previous, *matched]))})
    return results


def hydrate(data_dir: Path, candidates: list[dict], *, token: str | None = None, progress=None,
            limit: int = MAX_DETAILS_PER_RUN) -> dict:
    """Download full adverts for candidates. Network only; nothing is marked.

    Returns jobs, withdrawn ids, the remaining candidate count and the marks
    to record with mark_hydrated once the jobs are safely stored.
    """
    from .market import matches_query
    access_token, _ = nav._get_token(token)
    selected = candidates[:limit]

    def load(candidate):
        try:
            status, _, body, _ = nav._fetch_nav(candidate["detail_url"], access_token)
            if status != 200:
                return candidate, None, "missing"
            return candidate, nav._normalize(candidate["uuid"], nav._decode(body)), None
        except SourceError as exc:
            return candidate, None, str(exc)

    jobs, withdrawn, hydrated = [], [], []
    with ThreadPoolExecutor(max_workers=DETAIL_WORKERS, thread_name_prefix="nav-detail") as pool:
        for done, (candidate, job, error) in enumerate(pool.map(load, selected), 1):
            if error == "missing" or (error is None and job is None):
                withdrawn.append(candidate["uuid"])
            elif job is not None:
                # Multi-word searches are confirmed against the full advert.
                matched = [query for query in candidate["queries"] if matches_query(job, query)]
                if matched:
                    job["raw_json"] = {"matched_queries": matched[:5]}
                    jobs.append(job)
            if error is None:
                hydrated.append((candidate["modified"], json.dumps(candidate["checked"][-50:]), candidate["uuid"]))
            if progress is not None and done % 50 == 0:
                progress(f"Loading NAV adverts… {done} of {len(selected)}.")
    return {"jobs": jobs, "withdrawn": withdrawn, "remaining": max(0, len(candidates) - len(selected)), "marks": hydrated}


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
