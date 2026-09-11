"""SQLite persistence for vacancies and user-reviewed application drafts.

The storage boundary deliberately accepts dictionaries, keeping connectors and
the interface independent from any particular validation or AI library.
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


STATUSES = frozenset(
    {"new", "saved", "preparing", "ready", "applied", "interview", "rejected", "archived"}
)
_TRACKING_PARAMETERS = frozenset(
    {"fbclid", "gclid", "dclid", "msclkid", "mc_cid", "mc_eid", "_ga", "_gl", "trk", "trackingid", "referrer"}
)
_JOB_QUERY_KEYS = frozenset(
    {"job", "jobid", "job_id", "job-id", "requisitionid", "requisition_id", "reqid", "postingid", "posting_id", "gh_jid", "positionid", "position_id", "vacancyid", "vacancy_id"}
)
_GENERIC_PATH_PARTS = frozenset(
    {"job", "jobs", "career", "careers", "vacancy", "vacancies", "positions", "open-positions", "opportunities", "search", "apply", "application", "applications", "openings", "recruitment", "recruiting", "index", "index.html", "default.aspx"}
)
_METADATA_FIELDS = (
    "source", "source_id", "source_url", "apply_url", "title", "company", "location",
    "description", "employment_type", "published_at", "deadline", "raw_json",
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
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        version = self._connection.execute("PRAGMA user_version").fetchone()[0]
        if version > 2:
            self.close()
            raise ValueError(f"Database schema version {version} is newer than supported version 2")
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
        self._connection.execute("PRAGMA user_version = 2")
        self._connection.commit()

    def __enter__(self) -> JobStore:
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()

    def close(self) -> None:
        self._connection.close()

    @staticmethod
    def _job_dict(row: sqlite3.Row | None) -> dict | None:
        if row is None:
            return None
        result = dict(row)
        result.pop("canonical_source_url", None)
        return result

    def upsert_job(self, job: dict, *, fill_only: bool = False) -> tuple[int, bool]:
        """Insert or refresh a vacancy, preserving user workflow and draft history.

        Empty incoming fields do not erase existing metadata. Source and source
        ID change together only when both are supplied. Different source
        identities and URLs remain aliases after a cross-source update.
        Conflicting identities already attached to two rows require review and
        raise ValueError instead of silently combining user data.
        """
        incoming = {key: str(job[key]).strip() for key in _METADATA_FIELDS if job.get(key) is not None and key != "raw_json"}
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
        with self._connection:
            self._connection.execute("BEGIN IMMEDIATE")
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

    def record_provenance(self, job_id: int, kind: str, external_id: str, payload: dict) -> None:
        if not external_id:
            raise ValueError("Provenance requires an external message identifier")
        with self._connection:
            self._connection.execute(
                "INSERT OR IGNORE INTO job_provenance (job_id, kind, external_id, payload_json, created_at) VALUES (?, ?, ?, ?, ?)",
                (job_id, kind, external_id, json.dumps(payload, ensure_ascii=False), _now()),
            )

    def provenance(self, job_id: int) -> list[dict]:
        rows = self._connection.execute("SELECT kind, external_id, payload_json FROM job_provenance WHERE job_id = ? ORDER BY id", (job_id,)).fetchall()
        return [{"kind": row[0], "external_id": row[1], **json.loads(row[2])} for row in rows]

    def list_jobs(self, status: str | None = None, query: str | None = None, include_inactive: bool = False) -> list[dict]:
        filters = [] if include_inactive else ["is_active = 1"]
        parameters = []
        if status is not None:
            _validate_status(status)
            filters.append("status = ?")
            parameters.append(status)
        if query:
            escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            filters.append("(title LIKE ? ESCAPE '\\' OR company LIKE ? ESCAPE '\\' OR location LIKE ? ESCAPE '\\' OR description LIKE ? ESCAPE '\\')")
            parameters.extend([f"%{escaped}%"] * 4)
        where = " WHERE " + " AND ".join(filters) if filters else ""
        rows = self._connection.execute(
            "SELECT * FROM jobs" + where + " ORDER BY discovered_at DESC, id DESC", parameters
        ).fetchall()
        return [self._job_dict(row) for row in rows]

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
        with self._connection:
            row = self._connection.execute("SELECT job_id FROM job_identities WHERE kind = 'source_id' AND value = ?", (identity,)).fetchone()
            if row is None:
                return False
            self._connection.execute("UPDATE jobs SET is_active = 0, description = '', raw_json = '{}', last_seen_at = ? WHERE id = ?", (_now(), row[0]))
        return True

    def get_job(self, id: int) -> dict | None:
        return self._job_dict(self._connection.execute("SELECT * FROM jobs WHERE id = ?", (id,)).fetchone())

    def update_status(self, id: int, status: str) -> dict:
        _validate_status(status)
        with self._connection:
            cursor = self._connection.execute("UPDATE jobs SET status = ? WHERE id = ?", (status, id))
            if not cursor.rowcount:
                raise KeyError(id)
        return self.get_job(id)

    def update_notes(self, id: int, notes: str) -> dict:
        with self._connection:
            cursor = self._connection.execute("UPDATE jobs SET notes = ? WHERE id = ?", (notes, id))
            if not cursor.rowcount:
                raise KeyError(id)
        return self.get_job(id)

    def save_draft(self, job_id: int, content: str, model: str = "", profile_hash: str = "") -> int:
        if not isinstance(content, str) or not content.strip():
            raise ValueError("Draft content must not be empty")
        with self._connection:
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
