"""SQLite persistence for vacancies and user-reviewed application drafts.

The storage boundary deliberately accepts dictionaries, keeping connectors and
the interface independent from any particular validation or AI library.
"""

from __future__ import annotations

import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


# The single definition of workflow stages, in display order.
STATUS_ORDER = ("new", "saved", "preparing", "ready", "applied", "interview", "rejected", "archived")
STATUSES = frozenset(STATUS_ORDER)
_TRACKING_PARAMETERS = frozenset(
    {"fbclid", "gclid", "dclid", "msclkid", "mc_cid", "mc_eid", "_ga", "_gl", "trk", "trackingid", "referrer"}
)
_JOB_QUERY_KEYS = frozenset(
    {"job", "jobid", "job_id", "job-id", "requisitionid", "requisition_id", "reqid", "postingid", "posting_id", "gh_jid", "positionid", "position_id", "vacancyid", "vacancy_id"}
)
_GENERIC_PATH_PARTS = frozenset(
    {"job", "jobs", "career", "careers", "vacancy", "vacancies", "positions", "open-positions", "opportunities", "search", "apply", "application", "applications", "openings", "recruitment", "recruiting", "index", "index.html", "default.aspx"}
)
_SCHEMA_OBJECTS = frozenset({
    "jobs", "jobs_source_identity", "jobs_source_url", "jobs_status", "job_identities", "drafts",
    "drafts_job", "job_provenance", "application_preparations", "application_preparations_job",
    "application_deliveries", "job_matches", "job_matches_fingerprint", "job_matches_profile", "jobs_fts",
    "jobs_fts_insert", "jobs_fts_delete", "jobs_fts_update", "job_matches_stale_v2", "jobs_last_seen",
    "job_identities_job", "job_provenance_job", "application_deliveries_job",
    "job_insights", "job_insights_job", "ai_chats", "ai_messages", "ai_messages_chat",
    "email_applications", "email_applications_job",
})
SCHEMA_VERSION = 7
# Version 5 keeps what the AI assistant wrote: vacancy analyses and interview
# preparation per vacancy, and chat conversations. Version 6 keeps the
# applications found in your mailbox (and which messages were not one).
# Version 4 adds derived data, so lists stay fast with tens of thousands of
# collected vacancies: a full-text index kept in sync by triggers, and stored
# match results that a trigger discards whenever a vacancy's content, stage
# or activity really changes (a refresh that merely sees a job again does
# not). Both are rebuildable from the jobs table.
_DERIVED_SCHEMA = """
    CREATE INDEX IF NOT EXISTS jobs_last_seen ON jobs(last_seen_at);
    -- Deleting a vacancy checks every table that refers to it. Without these
    -- indexes each deletion scanned all job identities (275,000 rows on real
    -- data), so removing tens of thousands of adverts took hours.
    CREATE INDEX IF NOT EXISTS job_identities_job ON job_identities(job_id);
    CREATE INDEX IF NOT EXISTS job_provenance_job ON job_provenance(job_id);
    CREATE INDEX IF NOT EXISTS application_deliveries_job ON application_deliveries(job_id);
    CREATE TABLE IF NOT EXISTS job_matches (
        job_id INTEGER PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,
        profile_hash TEXT NOT NULL,
        track TEXT NOT NULL,
        track_rank INTEGER NOT NULL,
        score INTEGER,
        mismatch INTEGER NOT NULL,
        excluded INTEGER NOT NULL,
        countries TEXT NOT NULL,
        fingerprint TEXT,
        primary_id INTEGER,
        also_on TEXT NOT NULL DEFAULT '[]',
        match_json TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS job_matches_fingerprint ON job_matches(fingerprint);
    CREATE INDEX IF NOT EXISTS job_matches_profile ON job_matches(profile_hash);
    CREATE VIRTUAL TABLE IF NOT EXISTS jobs_fts USING fts5(
        title, company, location, description,
        content='jobs', content_rowid='id', tokenize='unicode61 remove_diacritics 2'
    );
    CREATE TRIGGER IF NOT EXISTS jobs_fts_insert AFTER INSERT ON jobs BEGIN
        INSERT INTO jobs_fts(rowid, title, company, location, description)
        VALUES (new.id, new.title, new.company, new.location, new.description);
    END;
    CREATE TRIGGER IF NOT EXISTS jobs_fts_delete AFTER DELETE ON jobs BEGIN
        INSERT INTO jobs_fts(jobs_fts, rowid, title, company, location, description)
        VALUES ('delete', old.id, old.title, old.company, old.location, old.description);
    END;
    CREATE TRIGGER IF NOT EXISTS jobs_fts_update AFTER UPDATE OF title, company, location, description ON jobs
    WHEN old.title IS NOT new.title OR old.company IS NOT new.company
      OR old.location IS NOT new.location OR old.description IS NOT new.description BEGIN
        INSERT INTO jobs_fts(jobs_fts, rowid, title, company, location, description)
        VALUES ('delete', old.id, old.title, old.company, old.location, old.description);
        INSERT INTO jobs_fts(rowid, title, company, location, description)
        VALUES (new.id, new.title, new.company, new.location, new.description);
    END;
    DROP TRIGGER IF EXISTS job_matches_stale;
    CREATE TRIGGER IF NOT EXISTS job_matches_stale_v2 AFTER UPDATE ON jobs
    WHEN old.title IS NOT new.title OR old.company IS NOT new.company OR old.location IS NOT new.location
      OR old.description IS NOT new.description OR old.deadline IS NOT new.deadline
      OR old.countries IS NOT new.countries OR old.status IS NOT new.status
      OR old.is_active IS NOT new.is_active OR old.source IS NOT new.source
      OR old.employment_type IS NOT new.employment_type
      OR (CASE WHEN json_valid(old.raw_json) THEN json_extract(old.raw_json, '$.site_requirements') END)
         IS NOT (CASE WHEN json_valid(new.raw_json) THEN json_extract(new.raw_json, '$.site_requirements') END) BEGIN
        DELETE FROM job_matches WHERE job_id = new.id;
    END;
"""
_METADATA_FIELDS = (
    "source", "source_id", "source_url", "apply_url", "title", "company", "location",
    "description", "employment_type", "published_at", "deadline", "raw_json", "countries",
)


def canonical_url(url: str | None) -> str:
    """Normalize a web URL while retaining parameters that may identify a job.

    Hosts and schemes are case-insensitive; paths and parameter values are not.
    Fragments and recognized advertising parameters do not identify a vacancy.
    """
    if not url or not str(url).strip():
        return ""
    parts = urlsplit(str(url).strip())
    if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
        raise ValueError("Vacancy URLs must be absolute HTTP or HTTPS URLs")
    if parts.username is not None or parts.password is not None:
        raise ValueError("Vacancy URLs must not contain credentials")
    scheme = parts.scheme.lower()
    host = parts.hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    port = parts.port
    if port and (scheme, port) not in {("http", 80), ("https", 443)}:
        host = f"{host}:{port}"
    query = sorted(
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in _TRACKING_PARAMETERS
    )
    return urlunsplit((scheme, host, parts.path.rstrip("/") or "/", urlencode(query), ""))


def _job_specific_apply_url(url: str) -> str:
    """Only use unmistakably job-specific application URLs for cross-source dedup."""
    if not url:
        return ""
    normalized = canonical_url(url)
    parts = urlsplit(normalized)
    if any(key.lower() in _JOB_QUERY_KEYS and value for key, value in parse_qsl(parts.query)):
        return normalized
    segments = [part.lower() for part in parts.path.split("/") if part]
    if not segments:
        return ""
    tail = segments[-1]
    if tail in _GENERIC_PATH_PARTS or re.fullmatch(r"[a-z]{2}(?:[-_][a-z]{2})?", tail):
        return ""
    # /jobs/123, /careers/software-engineer, /company/jobs/123 and similar.
    if len(segments) >= 2 and any(part in _GENERIC_PATH_PARTS for part in segments[:-1]):
        return normalized
    # Lever-style UUIDs and common ATS numeric posting IDs.
    if re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", tail):
        return normalized
    if len(segments) >= 2 and re.match(r"\d{5,}(?:[-_]|$)", tail):
        return normalized
    return ""


def search_terms(query: str | None) -> list[str]:
    """Split a search into words, keeping "quoted phrases" together."""
    if not query:
        return []
    terms = []
    for phrase, word in re.findall(r'"([^"]*)"|(\S+)', str(query)[:500]):
        value = " ".join((phrase or word).split()).strip(",;")
        if value and value.casefold() not in {term.casefold() for term in terms}:
            terms.append(value)
    return terms[:12]


def _short_term(term: str) -> bool:
    return len(term) <= 3 and term.isalnum()


def _has_word(job: dict, term: str) -> bool:
    text = "\n".join(str(job.get(key) or "") for key in ("title", "company", "location", "description"))
    return re.search(r"(?<![\w])" + re.escape(term) + r"(?![\w])", text, re.IGNORECASE) is not None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _validate_status(status: str) -> None:
    if status not in STATUSES:
        raise ValueError(f"Invalid vacancy status: {status!r}")


class JobStore:
    """A short-lived SQLite connection; use one instance per request or worker."""

    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        if self.db_path != ":memory:":
            Path(self.db_path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
            self.db_path = str(Path(self.db_path).expanduser())
        self._connection = sqlite3.connect(self.db_path, timeout=30)
        self._savepoint_sequence = 0
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        version = self._connection.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            self.close()
            raise ValueError(f"Database schema version {version} is newer than supported version {SCHEMA_VERSION}")
        if version == SCHEMA_VERSION and self._schema_complete():
            # Every request opens a store. Skipping redundant DDL keeps reads
            # from taking a write lock and syncing the file each time.
            return
        if self.db_path != ":memory:":
            # WAL lets the interface keep reading while a collection writes.
            # The mode persists in the file, so this runs once per database.
            self._connection.execute("PRAGMA journal_mode = WAL")
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY,
                source TEXT NOT NULL DEFAULT '',
                source_id TEXT NOT NULL DEFAULT '',
                source_url TEXT NOT NULL DEFAULT '',
                apply_url TEXT NOT NULL DEFAULT '',
                title TEXT NOT NULL DEFAULT '',
                company TEXT NOT NULL DEFAULT '',
                location TEXT NOT NULL DEFAULT '',
                description TEXT NOT NULL DEFAULT '',
                employment_type TEXT NOT NULL DEFAULT '',
                published_at TEXT NOT NULL DEFAULT '',
                deadline TEXT NOT NULL DEFAULT '',
                discovered_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'new'
                    CHECK (status IN ('new','saved','preparing','ready','applied','interview','rejected','archived')),
                notes TEXT NOT NULL DEFAULT '',
                raw_json TEXT NOT NULL DEFAULT '{}',
                canonical_source_url TEXT NOT NULL DEFAULT ''
            );
            CREATE UNIQUE INDEX IF NOT EXISTS jobs_source_identity
                ON jobs(source, source_id) WHERE source_id <> '';
            CREATE UNIQUE INDEX IF NOT EXISTS jobs_source_url
                ON jobs(canonical_source_url) WHERE canonical_source_url <> '';
            CREATE INDEX IF NOT EXISTS jobs_status ON jobs(status);
            CREATE TABLE IF NOT EXISTS job_identities (
                kind TEXT NOT NULL,
                value TEXT NOT NULL,
                job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                PRIMARY KEY (kind, value)
            );
            CREATE TABLE IF NOT EXISTS drafts (
                id INTEGER PRIMARY KEY,
                job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                content TEXT NOT NULL,
                model TEXT NOT NULL DEFAULT '',
                profile_hash TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS drafts_job ON drafts(job_id);
            CREATE TABLE IF NOT EXISTS job_provenance (
                id INTEGER PRIMARY KEY,
                job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                kind TEXT NOT NULL,
                external_id TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(kind, external_id, job_id)
            );
            """
        )
        columns = {row[1] for row in self._connection.execute("PRAGMA table_info(jobs)")}
        if "is_active" not in columns:
            self._connection.execute("ALTER TABLE jobs ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1")
        if "countries" not in columns:
            self._connection.execute("ALTER TABLE jobs ADD COLUMN countries TEXT NOT NULL DEFAULT '[]'")
        self._connection.executescript("""
            CREATE TABLE IF NOT EXISTS application_preparations (
                id INTEGER PRIMARY KEY,
                job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS application_preparations_job ON application_preparations(job_id);
            CREATE TABLE IF NOT EXISTS application_deliveries (
                id INTEGER PRIMARY KEY,
                job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                fingerprint TEXT NOT NULL,
                destination TEXT NOT NULL,
                status TEXT NOT NULL,
                result_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS job_insights (
                id INTEGER PRIMARY KEY,
                job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                kind TEXT NOT NULL CHECK (kind IN ('analysis', 'interview')),
                payload_json TEXT NOT NULL,
                model TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS job_insights_job ON job_insights(job_id, kind);
            CREATE TABLE IF NOT EXISTS ai_chats (
                id INTEGER PRIMARY KEY,
                title TEXT NOT NULL DEFAULT '',
                job_id INTEGER REFERENCES jobs(id) ON DELETE SET NULL,
                use_profile INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS ai_messages (
                id INTEGER PRIMARY KEY,
                chat_id INTEGER NOT NULL REFERENCES ai_chats(id) ON DELETE CASCADE,
                role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
                content TEXT NOT NULL,
                model TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS ai_messages_chat ON ai_messages(chat_id);
            CREATE TABLE IF NOT EXISTS email_applications (
                message_id TEXT PRIMARY KEY,
                thread_id TEXT NOT NULL DEFAULT '',
                received_at TEXT NOT NULL DEFAULT '',
                kind TEXT NOT NULL CHECK (kind IN ('applied', 'interview', 'rejected', 'other', 'ignored')),
                company TEXT NOT NULL DEFAULT '',
                position TEXT NOT NULL DEFAULT '',
                sender TEXT NOT NULL DEFAULT '',
                subject TEXT NOT NULL DEFAULT '',
                excerpt TEXT NOT NULL DEFAULT '',
                sent INTEGER NOT NULL DEFAULT 0,
                source TEXT NOT NULL DEFAULT 'rules',
                job_id INTEGER REFERENCES jobs(id) ON DELETE SET NULL,
                match TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS email_applications_job ON email_applications(job_id);
        """)
        fts_existed = self._connection.execute("SELECT 1 FROM sqlite_master WHERE name = 'jobs_fts'").fetchone()
        self._connection.executescript(_DERIVED_SCHEMA)
        if not fts_existed:
            # Index vacancies stored before the search index existed.
            self._connection.execute("INSERT INTO jobs_fts(jobs_fts) VALUES ('rebuild')")
        self._connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        self._connection.commit()

    def _schema_complete(self) -> bool:
        names = {row[0] for row in self._connection.execute("SELECT name FROM sqlite_master")}
        if not _SCHEMA_OBJECTS <= names:
            return False
        columns = {row[1] for row in self._connection.execute("PRAGMA table_info(jobs)")}
        mode = self._connection.execute("PRAGMA journal_mode").fetchone()[0]
        return {"is_active", "countries"} <= columns and (mode == "wal" or self.db_path == ":memory:")

    def __enter__(self) -> JobStore:
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()

    def close(self) -> None:
        self._connection.close()

    @contextmanager
    def transaction(self):
        """Commit a complete use case, including writes through nested methods.

        SQLite connection context managers commit even when nested, so they
        cannot protect a batch that calls several independently useful writes.
        Savepoints let those writes join a batch without committing its prefix.
        """
        nested = self._connection.in_transaction
        self._savepoint_sequence += 1
        savepoint = f"job_store_{self._savepoint_sequence}"
        self._connection.execute(f"SAVEPOINT {savepoint}" if nested else "BEGIN IMMEDIATE")
        try:
            yield self
            if nested:
                self._connection.execute(f"RELEASE SAVEPOINT {savepoint}")
            else:
                self._connection.commit()
        except BaseException:
            if nested:
                self._connection.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                self._connection.execute(f"RELEASE SAVEPOINT {savepoint}")
            else:
                self._connection.rollback()
            raise

    @staticmethod
    def _job_dict(row: sqlite3.Row | None) -> dict | None:
        if row is None:
            return None
        result = dict(row)
        result.pop("canonical_source_url", None)
        result["countries"] = json.loads(result.get("countries") or "[]")
        return result

    def upsert_job(self, job: dict, *, fill_only: bool = False) -> tuple[int, bool]:
        """Insert or refresh a vacancy, preserving user workflow and draft history.

        Empty incoming fields do not erase existing metadata. Source and source
        ID change together only when both are supplied. Different source
        identities and URLs remain aliases after a cross-source update.
        Conflicting identities already attached to two rows require review and
        raise ValueError instead of silently combining user data.
        """
        incoming = {key: str(job[key]).strip() for key in _METADATA_FIELDS if job.get(key) is not None and key not in {"raw_json", "countries"}}
        from .countries import job_countries
        countries = job_countries(job)
        if countries or job.get("location"):
            incoming["countries"] = json.dumps(countries)
        if job.get("raw_json") not in (None, "", {}):
            raw = job["raw_json"]
            incoming["raw_json"] = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False, sort_keys=True)
        normalized_source = canonical_url(incoming.get("source_url", ""))
        application_identity = _job_specific_apply_url(incoming.get("apply_url", ""))
        identities = []
        if incoming.get("source_id"):
            identities.append(("source_id", json.dumps([incoming.get("source", ""), incoming["source_id"]])))
        if normalized_source:
            identities.append(("source_url", normalized_source))
            # An employer's detail URL may have appeared as an application URL
            # on a job board. Match that same specific posting across roles.
            specific_source = _job_specific_apply_url(normalized_source)
            if specific_source:
                identities.append(("apply_url", specific_source))
        if application_identity:
            identities.append(("apply_url", application_identity))
        if not identities:
            raise ValueError("A vacancy needs a source ID or a source/job-specific application URL")

        # An immediate transaction also prevents two local processes from both
        # deciding that the same external posting is new.
        with self.transaction():
            matches = set()
            for kind, value in identities:
                row = self._connection.execute(
                    "SELECT job_id FROM job_identities WHERE kind = ? AND value = ?", (kind, value)
                ).fetchone()
                if row:
                    matches.add(row[0])
            if len(matches) > 1:
                raise ValueError("Vacancy identifiers match multiple saved jobs; review the records before merging")
            timestamp = _now()
            created = not matches
            if created:
                status = job.get("status") or "new"
                _validate_status(status)
                fields = {**incoming, "discovered_at": timestamp, "last_seen_at": timestamp,
                          "status": status, "notes": str(job.get("notes") or ""),
                          "canonical_source_url": normalized_source}
                keys = list(fields)
                cursor = self._connection.execute(
                    f"INSERT INTO jobs ({', '.join(keys)}) VALUES ({', '.join('?' for _ in keys)})",
                    [fields[key] for key in keys],
                )
                job_id = cursor.lastrowid
            else:
                job_id = matches.pop()
                fields = {key: value for key, value in incoming.items() if value}
                if "raw_json" in fields:
                    fields["raw_json"] = self._keep_site_facts(job_id, fields["raw_json"])
                if fill_only:
                    current = self.get_job(job_id)
                    fields = {key: value for key, value in fields.items() if not current.get(key)}
                    # Email excerpts must not replace an employer's full posting,
                    # change its identity, or reopen an already withdrawn role.
                    fields.pop("source", None)
                    fields.pop("source_id", None)
                # A source ID belongs to its provider. A URL-only manual import
                # or an unscoped ID must not manufacture a new provider/ID pair
                # by combining one incoming half with one existing half.
                if not (incoming.get("source") and incoming.get("source_id")):
                    fields.pop("source", None)
                    fields.pop("source_id", None)
                fields["last_seen_at"] = timestamp
                if not fill_only:
                    fields["is_active"] = 1
                if normalized_source and not fill_only:
                    fields["canonical_source_url"] = normalized_source
                self._connection.execute(
                    f"UPDATE jobs SET {', '.join(key + ' = ?' for key in fields)} WHERE id = ?",
                    [*fields.values(), job_id],
                )
            for kind, value in identities:
                self._connection.execute(
                    "INSERT OR IGNORE INTO job_identities (kind, value, job_id) VALUES (?, ?, ?)",
                    (kind, value, job_id),
                )
        return int(job_id), created

    # Requirements a site states as data (Djinni's experience and languages, a
    # level-filtered feed) arrive with some refreshes only; a later feed
    # without them must not erase them.
    _STICKY_RAW = ("site_requirements",)

    def _keep_site_facts(self, job_id: int, incoming: str) -> str:
        try:
            new = json.loads(incoming)
            row = self._connection.execute("SELECT raw_json FROM jobs WHERE id = ?", (job_id,)).fetchone()
            old = json.loads(row[0] or "{}") if row else {}
        except (ValueError, TypeError):
            return incoming
        if not isinstance(new, dict) or not isinstance(old, dict):
            return incoming
        kept = {}
        for key in self._STICKY_RAW:
            if isinstance(old.get(key), dict):
                # Facts from the advert page (languages) survive a feed that
                # only knows the experience level, and vice versa.
                kept[key] = {**old[key], **new[key]} if isinstance(new.get(key), dict) else old[key]
        if not kept:
            return incoming
        return json.dumps({**new, **kept}, ensure_ascii=False, sort_keys=True)

    def record_provenance(self, job_id: int, kind: str, external_id: str, payload: dict) -> None:
        if not external_id:
            raise ValueError("Provenance requires an external message identifier")
        with self.transaction():
            self._connection.execute(
                "INSERT OR IGNORE INTO job_provenance (job_id, kind, external_id, payload_json, created_at) VALUES (?, ?, ?, ?, ?)",
                (job_id, kind, external_id, json.dumps(payload, ensure_ascii=False), _now()),
            )

    def provenance(self, job_id: int) -> list[dict]:
        rows = self._connection.execute("SELECT kind, external_id, payload_json FROM job_provenance WHERE job_id = ? ORDER BY id", (job_id,)).fetchall()
        return [{"kind": row[0], "external_id": row[1], **json.loads(row[2])} for row in rows]

    def list_jobs(self, status: str | None = None, query: str | None = None, include_inactive: bool = False,
                  alternatives: list[str] | tuple[str, ...] = (), title_only_sources: tuple[str, ...] = (),
                  title_terms: list[str] | tuple[str, ...] = ()) -> list[dict]:
        """List vacancies; a query matches word by word, like a web search.

        Every word must appear somewhere in the title, employer, location or
        advert, in any order; "quoted phrases" stay together. Words of up to
        three characters (AI, ML, Go) must be whole words. Alternatives are
        other spellings of the same search, such as role aliases.

        Some job sites list adverts without their text until opened. Those
        (title_only_sources, still without a description) also match when a
        title contains one of title_terms, or when the site returned the
        advert for exactly this search.
        """
        filters = [] if include_inactive else ["is_active = 1"]
        parameters = []
        if status is not None:
            _validate_status(status)
            filters.append("status = ?")
            parameters.append(status)
        groups = [terms for value in ([query] if query else []) + [item for item in alternatives if item]
                  if (terms := search_terms(value))]
        if groups:
            clauses = []
            for terms in groups:
                parts = []
                for term in terms:
                    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                    parts.append("(title LIKE ? ESCAPE '\\' OR company LIKE ? ESCAPE '\\' OR location LIKE ? ESCAPE '\\' OR description LIKE ? ESCAPE '\\')")
                    parameters.extend([f"%{escaped}%"] * 4)
                clauses.append("(" + " AND ".join(parts) + ")")
            lazy = [term for term in title_terms if term] if title_only_sources else []
            if lazy:
                marks = ", ".join("?" for _ in title_only_sources)
                escaped = [term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") for term in [*lazy, query or ""]]
                clauses.append(f"(description = '' AND source IN ({marks}) AND ("
                               + " OR ".join("title LIKE ? ESCAPE '\\'" for _ in lazy) + " OR raw_json LIKE ? ESCAPE '\\'))")
                parameters.extend([*title_only_sources, *(f"%{term}%" for term in escaped[:-1]), f'%"{escaped[-1]}"%'])
            filters.append("(" + " OR ".join(clauses) + ")")
        where = " WHERE " + " AND ".join(filters) if filters else ""
        rows = self._connection.execute(
            "SELECT * FROM jobs" + where + " ORDER BY discovered_at DESC, id DESC", parameters
        ).fetchall()
        jobs = [self._job_dict(row) for row in rows]
        if groups:
            def matches(job):
                if title_terms and job.get("source") in title_only_sources and not job.get("description"):
                    if any(_has_word({"title": job.get("title")}, term) if _short_term(term) else term.casefold() in str(job.get("title", "")).casefold()
                           for term in title_terms) or f'"{(query or "").casefold()}"' in str(job.get("raw_json", "")).casefold():
                        return True
                return any(all(_has_word(job, term) for term in terms if _short_term(term))
                           and all(term.casefold() in "\n".join(str(job.get(key) or "") for key in ("title", "company", "location", "description")).casefold()
                                   for term in terms) for terms in groups)
            jobs = [job for job in jobs if matches(job)]
        return jobs

    def source_ids(self, source: str) -> list[str]:
        """Include aliases retained after an employer-board cross-source merge."""
        result = []
        for row in self._connection.execute("SELECT value FROM job_identities WHERE kind = 'source_id'"):
            pair = json.loads(row[0])
            if pair[0] == source:
                result.append(pair[1])
        return result

    def withdraw_source_job(self, source: str, source_id: str) -> bool:
        """Hide an inactive upstream vacancy and remove its cached advert text.

        Personal workflow and reviewed letters remain available by ID. Keeping
        the identity lets a later genuine reopening refresh the same record.
        """
        identity = json.dumps([source, source_id])
        with self.transaction():
            row = self._connection.execute("SELECT job_id FROM job_identities WHERE kind = 'source_id' AND value = ?", (identity,)).fetchone()
            if row is None:
                return False
            self._connection.execute("UPDATE jobs SET is_active = 0, description = '', raw_json = '{}', last_seen_at = ? WHERE id = ?", (_now(), row[0]))
        return True

    def reconcile_source_snapshot(self, source: str, prefix: str, current_ids) -> int:
        """Retire vanished jobs after one complete public ATS board snapshot.

        Match the current primary source, never a historical cross-provider
        alias. Preserve advert text and all personal notes and drafts. A
        bounded or failed collector must never call this method.
        """
        if source not in {"greenhouse", "lever", "ashby"} or not isinstance(prefix, str) or not prefix.endswith(":") or len(prefix) < 2:
            raise ValueError("A complete ATS snapshot needs a specific board prefix.")
        ids = set(current_ids)
        if any(not isinstance(identifier, str) or not identifier.startswith(prefix) for identifier in ids):
            raise ValueError("Snapshot identifiers must belong to the requested board.")
        with self.transaction():
            rows = self._connection.execute(
                "SELECT id, source_id FROM jobs WHERE source = ? AND substr(source_id, 1, ?) = ? AND is_active = 1",
                (source, len(prefix), prefix),
            ).fetchall()
            missing = [(row[0],) for row in rows if row[1] not in ids]
            self._connection.executemany("UPDATE jobs SET is_active = 0 WHERE id = ?", missing)
        return len(missing)

    def get_job(self, id: int) -> dict | None:
        return self._job_dict(self._connection.execute("SELECT * FROM jobs WHERE id = ?", (id,)).fetchone())

    def update_status(self, id: int, status: str) -> dict:
        _validate_status(status)
        with self.transaction():
            cursor = self._connection.execute("UPDATE jobs SET status = ? WHERE id = ?", (status, id))
            if not cursor.rowcount:
                raise KeyError(id)
        return self.get_job(id)

    def update_notes(self, id: int, notes: str) -> dict:
        if not isinstance(notes, str):
            raise ValueError("Vacancy notes must be text.")
        with self.transaction():
            cursor = self._connection.execute("UPDATE jobs SET notes = ? WHERE id = ?", (notes, id))
            if not cursor.rowcount:
                raise KeyError(id)
        return self.get_job(id)

    def save_draft(self, job_id: int, content: str, model: str = "", profile_hash: str = "") -> int:
        if not isinstance(content, str) or not content.strip():
            raise ValueError("Draft content must not be empty")
        with self.transaction():
            if self.get_job(job_id) is None:
                raise KeyError(job_id)
            cursor = self._connection.execute(
                "INSERT INTO drafts (job_id, content, model, profile_hash, created_at) VALUES (?, ?, ?, ?, ?)",
                (job_id, content, model, profile_hash, _now()),
            )
        return int(cursor.lastrowid)

    def list_drafts(self, job_id: int) -> list[dict]:
        rows = self._connection.execute(
            "SELECT * FROM drafts WHERE job_id = ? ORDER BY created_at DESC, id DESC", (job_id,)
        ).fetchall()
        return [dict(row) for row in rows]

    def save_preparation(self, job_id: int, payload: dict) -> int:
        encoded = json.dumps(payload, ensure_ascii=False)
        if len(encoded) > 500000:
            raise ValueError("Application preparation is too large.")
        with self.transaction():
            if self.get_job(job_id) is None:
                raise KeyError(job_id)
            cursor = self._connection.execute(
                "INSERT INTO application_preparations(job_id, payload_json, created_at) VALUES (?, ?, ?)",
                (job_id, encoded, _now()),
            )
        return int(cursor.lastrowid)

    def preparations(self, job_id: int) -> list[dict]:
        rows = self._connection.execute(
            "SELECT id, payload_json, created_at FROM application_preparations WHERE job_id = ? ORDER BY id DESC", (job_id,)
        ).fetchall()
        return [{"id": row[0], "payload": json.loads(row[1]), "created_at": row[2]} for row in rows]

    def save_insight(self, job_id: int, kind: str, payload: dict, model: str = "") -> int:
        encoded = json.dumps(payload, ensure_ascii=False)
        if len(encoded) > 200000:
            raise ValueError("The AI result is too large to save.")
        with self.transaction():
            if self.get_job(job_id) is None:
                raise KeyError(job_id)
            cursor = self._connection.execute(
                "INSERT INTO job_insights(job_id, kind, payload_json, model, created_at) VALUES (?, ?, ?, ?, ?)",
                (job_id, kind, encoded, model, _now()))
            # Earlier versions of the same kind are replaced, not kept.
            self._connection.execute("DELETE FROM job_insights WHERE job_id = ? AND kind = ? AND id <> ?",
                                     (job_id, kind, cursor.lastrowid))
        return int(cursor.lastrowid)

    def insights(self, job_id: int) -> dict:
        rows = self._connection.execute(
            "SELECT kind, payload_json, model, created_at FROM job_insights WHERE job_id = ? ORDER BY id", (job_id,)).fetchall()
        return {row[0]: {"payload": json.loads(row[1]), "model": row[2], "created_at": row[3]} for row in rows}

    def create_chat(self, title: str, job_id: int | None = None, use_profile: bool = True) -> int:
        now = _now()
        with self.transaction():
            if job_id is not None and self.get_job(job_id) is None:
                raise KeyError(job_id)
            cursor = self._connection.execute(
                "INSERT INTO ai_chats(title, job_id, use_profile, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (title[:120], job_id, int(bool(use_profile)), now, now))
        return int(cursor.lastrowid)

    def chats(self, limit: int = 200) -> list[dict]:
        rows = self._connection.execute(
            """SELECT c.id, c.title, c.job_id, c.use_profile, c.updated_at, j.title, j.company,
                      (SELECT COUNT(*) FROM ai_messages m WHERE m.chat_id = c.id)
               FROM ai_chats c LEFT JOIN jobs j ON j.id = c.job_id
               ORDER BY c.updated_at DESC, c.id DESC LIMIT ?""", (limit,)).fetchall()
        return [{"id": row[0], "title": row[1], "job_id": row[2], "use_profile": bool(row[3]), "updated_at": row[4],
                 "job_title": row[5] or "", "job_company": row[6] or "", "messages": row[7]} for row in rows]

    def chat(self, chat_id: int) -> dict | None:
        found = next((chat for chat in self.chats(1_000_000) if chat["id"] == chat_id), None)
        if found is None:
            return None
        rows = self._connection.execute(
            "SELECT id, role, content, model, created_at FROM ai_messages WHERE chat_id = ? ORDER BY id", (chat_id,)).fetchall()
        return {**found, "messages": [{"id": row[0], "role": row[1], "content": row[2], "model": row[3], "created_at": row[4]}
                                      for row in rows]}

    def add_chat_messages(self, chat_id: int, messages: list[tuple[str, str, str]]) -> None:
        now = _now()
        with self.transaction():
            if not self._connection.execute("SELECT 1 FROM ai_chats WHERE id = ?", (chat_id,)).fetchone():
                raise KeyError(chat_id)
            self._connection.executemany(
                "INSERT INTO ai_messages(chat_id, role, content, model, created_at) VALUES (?, ?, ?, ?, ?)",
                [(chat_id, role, content, model, now) for role, content, model in messages])
            self._connection.execute("UPDATE ai_chats SET updated_at = ? WHERE id = ?", (now, chat_id))

    def update_chat(self, chat_id: int, **changes) -> None:
        allowed = {key: value for key, value in changes.items() if key in {"title", "job_id", "use_profile"}}
        if not allowed:
            return
        if allowed.get("job_id") is not None and self.get_job(allowed["job_id"]) is None:
            raise KeyError(allowed["job_id"])
        if "use_profile" in allowed:
            allowed["use_profile"] = int(bool(allowed["use_profile"]))
        if "title" in allowed:
            allowed["title"] = str(allowed["title"])[:120]
        with self.transaction():
            self._connection.execute(
                f"UPDATE ai_chats SET {', '.join(key + ' = ?' for key in allowed)} WHERE id = ?", [*allowed.values(), chat_id])

    def delete_chat(self, chat_id: int) -> bool:
        with self.transaction():
            return self._connection.execute("DELETE FROM ai_chats WHERE id = ?", (chat_id,)).rowcount > 0

    _EMAIL_FIELDS = ("message_id", "thread_id", "received_at", "kind", "company", "position", "sender", "subject",
                     "excerpt", "sent", "source", "job_id", "match")

    def email_known_ids(self) -> set[str]:
        return {row[0] for row in self._connection.execute("SELECT message_id FROM email_applications")}

    def save_email_records(self, records: list[dict], ignored: list[str] = ()) -> None:
        now = _now()
        with self.transaction():
            self._connection.executemany(
                f"INSERT OR IGNORE INTO email_applications({', '.join(self._EMAIL_FIELDS[:11])}, created_at) "
                f"VALUES ({', '.join('?' * 12)})",
                [(*(int(bool(record.get(key))) if key == "sent" else str(record.get(key) or "") for key in self._EMAIL_FIELDS[:11]), now)
                 for record in records])
            self._connection.executemany(
                "INSERT OR IGNORE INTO email_applications(message_id, kind, created_at) VALUES (?, 'ignored', ?)",
                [(message_id, now) for message_id in ignored])

    def email_records(self) -> list[dict]:
        rows = self._connection.execute(
            f"SELECT {', '.join(self._EMAIL_FIELDS)} FROM email_applications WHERE kind IN ('applied', 'interview', 'rejected') "
            "ORDER BY received_at, message_id").fetchall()
        return [{**dict(row), "sent": bool(row["sent"])} for row in rows]

    def update_email_record(self, message_id: str, **changes) -> None:
        allowed = {key: value for key, value in changes.items() if key in {"kind", "company", "position", "source", "job_id", "match"}}
        if allowed:
            with self.transaction():
                self._connection.execute(f"UPDATE email_applications SET {', '.join(key + ' = ?' for key in allowed)} WHERE message_id = ?",
                                         [*allowed.values(), message_id])

    def set_email_links(self, links: dict[str, tuple[int | None, str]]) -> None:
        with self.transaction():
            self._connection.executemany("UPDATE email_applications SET job_id = ?, match = ? WHERE message_id = ?",
                                         [(job_id, match, message_id) for message_id, (job_id, match) in links.items()])

    def email_for_jobs(self, job_ids: list[int]) -> dict[int, list[dict]]:
        """Each vacancy's application mail, oldest first."""
        result = {}
        for start in range(0, len(job_ids), 500):
            chunk = job_ids[start:start + 500]
            rows = self._connection.execute(
                f"SELECT job_id, kind, received_at FROM email_applications WHERE job_id IN ({', '.join('?' * len(chunk))}) "
                "AND kind IN ('applied', 'interview', 'rejected') ORDER BY received_at", chunk).fetchall()
            for row in rows:
                result.setdefault(row[0], []).append({"kind": row[1], "received_at": row[2]})
        return result

    def clear_email_records(self) -> int:
        with self.transaction():
            return self._connection.execute("DELETE FROM email_applications").rowcount

    def begin_delivery(self, job_id: int, fingerprint: str, destination: str) -> int:
        with self.transaction():
            previous = self._connection.execute(
                "SELECT id FROM application_deliveries WHERE job_id=? AND fingerprint=? AND status IN ('pending','uncertain','submitted_unconfirmed')",
                (job_id, fingerprint),
            ).fetchone()
            if previous:
                raise ValueError("This exact application already has a delivery attempt. Check the employer's confirmation before sending anything else.")
            cursor = self._connection.execute(
                "INSERT INTO application_deliveries(job_id,fingerprint,destination,status,created_at) VALUES (?,?,?,'pending',?)",
                (job_id, fingerprint, destination, _now()),
            )
        return int(cursor.lastrowid)

    def finish_delivery(self, attempt_id: int, result: dict) -> None:
        status = result.get("status", "uncertain")
        if status not in {"uncertain", "blocked", "submitted_unconfirmed"}:
            status = "uncertain"
        with self.transaction():
            self._connection.execute("UPDATE application_deliveries SET status=?,result_json=? WHERE id=?", (status, json.dumps(result, ensure_ascii=False), attempt_id))

    def deliveries(self, job_id: int) -> list[dict]:
        return [dict(row) for row in self._connection.execute("SELECT * FROM application_deliveries WHERE job_id=? ORDER BY id DESC", (job_id,))]
