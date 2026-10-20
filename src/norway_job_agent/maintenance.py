"""Local diagnostics and safety nets: a log file, automatic backups, recovery.

The desktop app runs without a console, so unexpected errors are written to
logs/job-agent.log in the private data folder. Log records contain error
types and stack traces, never profile, CV, vacancy or letter text.

A compact backup of the database and settings is kept once a day (the last
ten), so a damaged or mistakenly edited file never costs the job-search
history. Gmail credentials and attachment files are deliberately excluded.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import tempfile
import zipfile
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_FILE = Path("logs") / "job-agent.log"
BACKUP_FOLDER = Path("backups") / "automatic"
BACKUP_KEEP = 10
BACKUP_INTERVAL = timedelta(hours=20)
SETTINGS_FILES = ("profile.json", "sources.json", "appearance.json", "nav-state.json", "last-collection.json")
RECOVERABLE = {"profile.json": "profile", "sources.json": "source settings"}


def configure_logging(data_dir: Path) -> Path:
    """Send warnings and errors from every module to a rotating log file."""
    path = Path(data_dir) / LOG_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    if not any(getattr(handler, "_job_agent_log", None) == str(path) for handler in root.handlers):
        handler = RotatingFileHandler(path, maxBytes=1_000_000, backupCount=3, encoding="utf-8", delay=True)
        handler._job_agent_log = str(path)
        handler.setLevel(logging.WARNING)
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        root.addHandler(handler)
        if root.level > logging.WARNING or root.level == logging.NOTSET:
            root.setLevel(logging.WARNING)
    return path


def _backups(data_dir: Path) -> list[Path]:
    folder = Path(data_dir) / BACKUP_FOLDER
    return sorted(folder.glob("auto-*.zip")) if folder.is_dir() else []


def automatic_backup(data_dir: Path, *, now: datetime | None = None, force: bool = False) -> Path | None:
    """Back up the database and settings at most once per interval.

    SQLite's online backup API gives a consistent copy even while the app
    reads or writes (including WAL content). Returns the new file, or None.
    """
    data_dir = Path(data_dir)
    now = now or datetime.now()
    existing = _backups(data_dir)
    if existing and not force:
        try:
            newest = datetime.strptime(existing[-1].stem, "auto-%Y%m%d-%H%M%S")
            if now - newest < BACKUP_INTERVAL:
                return None
        except ValueError:
            pass
    folder = data_dir / BACKUP_FOLDER
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"auto-{now:%Y%m%d-%H%M%S}.zip"
    # Built beside its final name, then renamed: an interrupted backup never
    # looks like a complete one.
    partial = folder / (target.name + ".partial")
    with tempfile.TemporaryDirectory(prefix="job-agent-backup-") as scratch:
        with zipfile.ZipFile(partial, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            database = data_dir / "vacancies.db"
            if database.exists():
                copy = Path(scratch) / "vacancies.db"
                source = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
                destination = sqlite3.connect(copy)
                try:
                    source.backup(destination)
                finally:
                    destination.close()
                    source.close()
                archive.write(copy, "vacancies.db")
            for name in SETTINGS_FILES:
                if (data_dir / name).is_file():
                    archive.write(data_dir / name, name)
    partial.replace(target)
    for old in _backups(data_dir)[:-BACKUP_KEEP]:
        old.unlink(missing_ok=True)
    return target


def restore_settings_file(data_dir: Path, name: str) -> str:
    """Replace a damaged profile or settings file, keeping the damaged copy.

    Uses the newest automatic backup whose copy of the file is valid JSON;
    without one, the file starts again from the defaults.
    """
    if name not in RECOVERABLE:
        raise ValueError("Only the profile and source settings can be restored here.")
    data_dir = Path(data_dir)
    current = data_dir / name
    stamp = f"{datetime.now():%Y%m%d-%H%M%S}"
    if current.exists():
        current.replace(data_dir / f"{name}.damaged-{stamp}")
    for backup in reversed(_backups(data_dir)):
        try:
            with zipfile.ZipFile(backup) as archive:
                content = archive.read(name)
            json.loads(content.decode("utf-8-sig"))
        except (KeyError, OSError, ValueError, zipfile.BadZipFile):
            continue
        current.write_bytes(content)
        return f"Restored your {RECOVERABLE[name]} from the backup of {datetime.strptime(backup.stem, 'auto-%Y%m%d-%H%M%S'):%d %B %Y, %H:%M}. The damaged file was kept as {name}.damaged-{stamp}."
    from .service import DEFAULT_PROFILE, DEFAULT_SETTINGS, _write_json
    _write_json(current, DEFAULT_PROFILE if name == "profile.json" else DEFAULT_SETTINGS)
    return f"No backup of your {RECOVERABLE[name]} was available, so it starts again from the defaults. The damaged file was kept as {name}.damaged-{stamp}."
