"""Stored matches, search and paging over every collected vacancy.

Collection keeps everything sources return, so the database can hold tens of
thousands of vacancies. Matching each one against the profile on every list
request (the previous design) grows linearly with that; instead each job's
match is computed once and stored in job_matches, together with its country
set and duplicate group. A trigger discards a stored match when the job's
content, stage or activity changes, and a different profile makes every match
stale. Lists then filter and sort in SQL over the stored results, search uses
the jobs_fts full-text index, and only one page of full records is loaded.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
from collections import deque
from datetime import date, datetime, timedelta, timezone

from .storage import JobStore, search_terms

# Bump when match_job, fingerprints or stored fields change meaning.
MATCH_VERSION = "1"
RETENTION_DAYS = 30
BOARD_SOURCES = ("arbeitsagentur", "arbeitnow", "djinni", "dou", "remotive", "remoteok", "himalayas", "linkedin")
# User-added or email-imported vacancies are never removed automatically.
_KEEP_SOURCES = ("manual", "gmail")
_SOURCE_RANK = {"greenhouse": 0, "lever": 0, "ashby": 0, "workday": 0, "smartrecruiters": 0, "career_page": 0,
                "manual": 0, "nav": 1, "arbeitsagentur": 1, "djinni": 2, "dou": 2, "arbeitnow": 2, "remotive": 2,
                "remoteok": 2, "himalayas": 2, "gmail": 3, "linkedin": 3}
_TRACK_RANK = {"target": 2, "horizon": 1, "other": 0}
_GENDER_MARKERS = re.compile(r"\((?:[^()]*\b(?:m|w|d|f|x|gn|all genders?|div)\b[^()]*)\)|\*in\b|\b[mwfd]\s*/\s*[mwfd](?:\s*/\s*[mwfdx])?\b", re.I)
_LEGAL_SUFFIXES = re.compile(r"\b(?:gmbh|ag|as|asa|inc|llc|ltd|limited|corp|corporation|se|plc|co|kg|ab|oy|sa|bv|nv|ug|sp z o o|tov|тов)\b\.?", re.I)
_EXPIRED_WARNING = "The stated application deadline has passed."
_DEADLINE_SQL = ("(substr(j.deadline, 1, 10) GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]' "
                 "AND substr(j.deadline, 1, 10) < :today)")
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock(store: JobStore) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(store.db_path, threading.Lock())


def profile_key(profile: dict) -> str:
    text = json.dumps(profile, sort_keys=True, ensure_ascii=False) + "|" + MATCH_VERSION
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def job_fingerprint(job: dict) -> str | None:
    """Title, employer and first city with formatting differences removed."""
    title = " ".join(re.findall(r"\w+", _GENDER_MARKERS.sub(" ", str(job.get("title") or "")).casefold()))
    company = " ".join(re.findall(r"\w+", _LEGAL_SUFFIXES.sub(" ", str(job.get("company") or "")).casefold()))
    location = re.split(r"[;,/|(\n]", str(job.get("location") or "").casefold())[0]
    city = " ".join(re.findall(r"\w+", location)[:2])
    if not title or not company or company.startswith("company on"):
        return None
    return f"{title}|{company}|{city}"


def _expired(deadline, today: date | None = None) -> bool:
    try:
        return date.fromisoformat(str(deadline or "")[:10]) < (today or date.today())
    except ValueError:
        return False


def current_match(match: dict, deadline, today: date | None = None) -> dict:
    """A stored match with its deadline judged against today, not its compute date."""
    expired = _expired(deadline, today)
    warnings = [warning for warning in match.get("warnings", []) if warning != _EXPIRED_WARNING]
    if expired:
        warnings.insert(0, _EXPIRED_WARNING)
    return {**match, "expired": expired, "warnings": warnings}


def ensure_matches(store: JobStore, profile: dict, *, progress=None, batch: int = 1000,
                   limit: int | None = None, wait: bool = True) -> int:
    """Compute missing or stale matches; returns how many were computed.

    A list request passes a limit and wait=False: while a refresh or a
    re-match is running it serves the stored results instead of blocking,
    and new vacancies appear once their matches are stored.
    """
    from .countries import job_countries
    from .profile import match_job
    key = profile_key(profile)
    lock = _lock(store)
    if not lock.acquire(blocking=wait):
        return 0
    try:
        connection = store._connection
        # Vacancies without any stored match come first: they are invisible.
        stale = [row[0] for row in connection.execute(
            "SELECT j.id FROM jobs j LEFT JOIN job_matches m ON m.job_id = j.id "
            "WHERE m.job_id IS NULL OR m.profile_hash <> ? ORDER BY m.job_id IS NOT NULL, j.id DESC", (key,))]
        if limit is not None:
            stale = stale[:limit]
        if not stale:
            return 0
        for start in range(0, len(stale), batch):
            ids = stale[start:start + batch]
            marks = ",".join("?" for _ in ids)
            values = []
            for row in connection.execute(f"SELECT * FROM jobs WHERE id IN ({marks})", ids):
                job = store._job_dict(row)
                codes = job_countries(job)
                job["countries"] = codes
                match = match_job(job, profile)
                values.append((job["id"], key, match["track"], _TRACK_RANK[match["track"]], match["score"],
                               int((match.get("experience") or {}).get("status") == "mismatch"),
                               int(bool(match.get("excluded_terms"))), ("," + ",".join(codes) + ",") if codes else ",",
                               job_fingerprint(job), json.dumps(match, ensure_ascii=False)))
            with store.transaction():
                connection.executemany(
                    "INSERT OR REPLACE INTO job_matches (job_id, profile_hash, track, track_rank, score, mismatch, "
                    "excluded, countries, fingerprint, match_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", values)
            if progress is not None and len(stale) > batch:
                progress(f"Matching vacancies to your profile… {min(start + batch, len(stale)):,} of {len(stale):,}.")
        _refresh_duplicates(store)
        return len(stale)
    finally:
        lock.release()


def _refresh_duplicates(store: JobStore) -> None:
    """Group one job listed on several sites; the shown copy carries the others.

    Only copies from different sites merge: two postings on one site are
    separate openings. The shown copy prefers an active posting, then your
    workflow stage, then the employer's own posting, then the fuller advert.
    """
    connection = store._connection
    rows = connection.execute(
        "SELECT m.job_id, m.fingerprint, j.source, j.status, j.is_active, j.description <> '', j.source_url, "
        "m.primary_id, m.also_on FROM job_matches m JOIN jobs j ON j.id = m.job_id "
        "WHERE m.fingerprint IS NOT NULL ORDER BY j.discovered_at DESC, j.id DESC").fetchall()
    groups: dict[str, list] = {}
    for row in rows:
        groups.setdefault(row[1], []).append(row)
    updates = []
    for members in groups.values():
        if len(members) == 1:
            desired = {members[0][0]: (None, "[]")}
        else:
            members.sort(key=lambda row: (not row[4], row[3] == "new", _SOURCE_RANK.get(row[2], 2), not row[5]))
            cards: list[tuple] = []
            for row in members:
                card = next((card for card in cards if row[2] not in card[1]), None)
                if card is None:
                    cards.append((row, {row[2]}, []))
                else:
                    card[1].add(row[2])
                    card[2].append(row)
            desired = {}
            for primary, _, others in cards:
                also = [{"source": other[2], "id": other[0], "url": other[6]} for other in others]
                desired[primary[0]] = (primary[0] if others else None, json.dumps(also))
                for other in others:
                    desired[other[0]] = (primary[0], "[]")
        for row in members:
            primary_id, also_on = desired[row[0]]
            if (row[7], row[8]) != (primary_id, also_on):
                updates.append((primary_id, also_on, row[0]))
    if updates:
        with store.transaction():
            connection.executemany("UPDATE job_matches SET primary_id = ?, also_on = ? WHERE job_id = ?", updates)


def _fts_term(term: str) -> str:
    """One search word or "quoted phrase" as an FTS5 expression.

    Words of four or more characters also match longer words (prefix), as
    typing does; shorter words such as AI or Go must be whole words.
    """
    quoted = '"' + term.replace('"', '""') + '"'
    return quoted + "*" if " " not in term and len(term) >= 4 else quoted


def _like(term: str) -> str:
    return "%" + term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _query_sql(query: str, params: dict) -> str:
    """Rows answering a search, like a web search: every word, in any order.

    Each word must occur in the full-text index, or inside the title (German
    compounds such as Softwareentwickler contain Entwickler). Other spellings
    of a role count as the same search. Job-site adverts whose text is not
    loaded yet also match on a specific search word in the title, or when the
    site returned them for exactly this search.
    """
    from .market import LAZY_DETAILS, specific_tokens
    from .profile import _role_terms
    groups = [terms for value in [query, *list(_role_terms(query))[1:]] if (terms := search_terms(value))]
    clauses = []
    for group_index, terms in enumerate(groups):
        parts = []
        for term_index, term in enumerate(terms):
            name = f"q{group_index}_{term_index}"
            params[name] = _fts_term(term)
            part = f"j.id IN (SELECT rowid FROM jobs_fts WHERE jobs_fts MATCH :{name})"
            if len(term) >= 4:
                params[name + "_like"] = _like(term)
                part = f"({part} OR j.title LIKE :{name}_like ESCAPE '\\')"
            parts.append(part)
        clauses.append("(" + " AND ".join(parts) + ")")
    lazy_sources = sorted(LAZY_DETAILS - {"djinni"})
    words = specific_tokens(query)
    if words and lazy_sources:
        for index, word in enumerate(words):
            params[f"lazy{index}"] = _like(word)
        sources = ", ".join(f"'{source}'" for source in lazy_sources)
        clauses.append(f"(j.description = '' AND j.source IN ({sources}) AND ("
                       + " OR ".join(f"j.title LIKE :lazy{index} ESCAPE '\\'" for index in range(len(words))) + "))")
    params["tag"] = '%"' + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + '"%'
    clauses.append("j.raw_json LIKE :tag ESCAPE '\\'")
    return "(" + " OR ".join(clauses) + ")"


def select_rows(store: JobStore, *, query: str = "", status: str = "", country: str = "",
                include_inactive: bool = False) -> list[dict]:
    """Lightweight rows in scope, one per duplicate group, newest first."""
    from .storage import _validate_status
    params: dict = {"today": date.today().isoformat(),
                    "cutoff": (datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)).isoformat()}
    where = ["(m.primary_id IS NULL OR m.primary_id = j.id)"]
    if not include_inactive:
        sources = ", ".join(f"'{source}'" for source in BOARD_SOURCES)
        # NAV requires inactive ads to leave results, and an elapsed deadline
        # keeps them out before the next refresh. Job-site adverts no search
        # has seen for a month have usually closed.
        where += ["j.is_active = 1", f"NOT (j.source = 'nav' AND {_DEADLINE_SQL})",
                  f"NOT (j.source IN ({sources}) AND j.status = 'new' AND j.last_seen_at < :cutoff)"]
    if country == "unknown":
        where.append("m.countries = ','")
    elif country:
        params["country"] = f"%,{country},%"
        where.append("m.countries LIKE :country")
    if status:
        _validate_status(status)
        params["status"] = status
        where.append("j.status = :status")
    if query and query.strip():
        where.append(_query_sql(query.strip()[:500], params))
    sql = (f"SELECT j.id, j.title, j.company, j.status, j.source, j.deadline, j.published_at, j.discovered_at, "
           f"m.track, m.track_rank, m.score, m.mismatch, m.excluded, {_DEADLINE_SQL} AS expired "
           f"FROM jobs j JOIN job_matches m ON m.job_id = j.id WHERE {' AND '.join(where)} "
           f"ORDER BY j.discovered_at DESC, j.id DESC")
    return [dict(row) for row in store._connection.execute(sql, params)]


def relevance_sort(rows: list[dict], query: str) -> None:
    """A search ranks titles naming what was searched first, as a web search would."""
    from .market import query_tokens
    from .profile import _role_terms, _role_text, contains_phrase
    tokens, phrases = query_tokens(query), _role_terms(query)

    def relevance(row):
        title = _role_text(str(row.get("title", "")))
        if any(contains_phrase(title, phrase) for phrase in phrases):
            return 3
        hits = sum(contains_phrase(title, token) for token in tokens)
        return 2 if tokens and hits == len(tokens) else 1 if hits else 0
    for row in rows:
        row["_relevance"] = relevance(row)


def sort_rows(rows: list[dict], sort: str = "match") -> list[dict]:
    if sort == "newest":
        return sorted(rows, key=lambda row: str(row.get("published_at") or row.get("discovered_at") or ""), reverse=True)
    if sort == "company":
        return sorted(rows, key=lambda row: (str(row.get("company", "")).casefold(), str(row.get("title", "")).casefold()))
    if sort != "match":
        raise ValueError("Choose match, newest or company sorting.")
    return sorted(rows, key=lambda row: (row.get("_relevance", 0), not row["mismatch"], not (row["excluded"] or row["expired"]),
                                         row["track_rank"], row["score"] if row["score"] is not None else -1), reverse=True)


def diversify(rows: list[dict]) -> list[dict]:
    """Keep role/experience groups ordered, with two jobs per employer per pass."""
    groups: dict = {}
    for row in rows:
        group = (bool(row["mismatch"]), row["track"])
        employer = " ".join(str(row["company"]).casefold().split())
        groups.setdefault(group, {}).setdefault(employer, deque()).append(row)
    result = []
    for employers in groups.values():
        remaining = list(employers.values())
        while remaining:
            for queue in remaining:
                for _ in range(min(2, len(queue))):
                    result.append(queue.popleft())
            remaining = [queue for queue in remaining if queue]
    return result


def load_full(store: JobStore, ids: list[int]) -> list[dict]:
    """Full records with their stored match, in the order of ids."""
    if not ids:
        return []
    marks = ",".join("?" for _ in ids)
    found = {}
    for row in store._connection.execute(
            f"SELECT j.*, m.match_json AS _match, m.also_on AS _also_on, m.countries AS _countries "
            f"FROM jobs j JOIN job_matches m ON m.job_id = j.id WHERE j.id IN ({marks})", ids):
        record = dict(row)
        match, also_on, codes = json.loads(record.pop("_match")), json.loads(record.pop("_also_on")), record.pop("_countries")
        job = store._job_dict(record)
        job["countries"] = [code for code in codes.strip(",").split(",") if code]
        job["match"] = current_match(match, job.get("deadline"))
        if also_on:
            job["also_on"] = also_on
        found[job["id"]] = job
    return [found[identifier] for identifier in ids if identifier in found]


def cleanup(store: JobStore, *, now: datetime | None = None) -> int:
    """Remove closed or stale vacancies after RETENTION_DAYS, keeping your work.

    A vacancy is kept whatever its age when it has a stage other than new,
    notes, drafts, application answers or deliveries, or when you added it
    yourself or imported it from email. Removed are: postings withdrawn or
    retired by their source, job-site adverts no search has seen, and
    adverts whose deadline passed, each more than RETENTION_DAYS ago.
    """
    now = now or datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=RETENTION_DAYS)).isoformat()
    deadline = (now - timedelta(days=RETENTION_DAYS)).date().isoformat()
    sources = ", ".join(f"'{source}'" for source in BOARD_SOURCES)
    keep = ", ".join(f"'{source}'" for source in _KEEP_SOURCES)
    with store.transaction():
        cursor = store._connection.execute(f"""
            DELETE FROM jobs WHERE status = 'new' AND notes = '' AND source NOT IN ({keep})
              AND id NOT IN (SELECT job_id FROM drafts)
              AND id NOT IN (SELECT job_id FROM application_preparations)
              AND id NOT IN (SELECT job_id FROM application_deliveries)
              AND id NOT IN (SELECT job_id FROM job_provenance)
              AND ((is_active = 0 AND last_seen_at < :cutoff)
                OR (source IN ({sources}) AND last_seen_at < :cutoff)
                OR (substr(deadline, 1, 10) GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'
                    AND substr(deadline, 1, 10) < :deadline))""", {"cutoff": cutoff, "deadline": deadline})
        removed = cursor.rowcount
    if removed:
        _refresh_duplicates(store)
    return removed
