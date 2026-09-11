"""Application use cases shared by the desktop interface and command line."""

from __future__ import annotations

import json
import hashlib
import os
import tempfile
import threading
import zipfile
from pathlib import Path
from xml.etree import ElementTree

from .profile import load_profile, match_job, profile_hash, validate_profile
from .storage import JobStore


DEFAULT_PROFILE = {
    "name": "", "summary": "", "skills": [], "evidence": [], "cv_text": "",
    "target_roles": ["Data Scientist", "Data Science", "AI Engineer", "Artificial Intelligence", "Machine Learning Engineer", "ML Engineer", "Mathematician", "maskinlæring", "kunstig intelligens", "matematiker"],
    "related_roles": ["Data Analyst", "Data Engineer", "Research Engineer", "Research Scientist", "Applied Scientist", "Statistician", "Quantitative Analyst", "Analytics Engineer", "Research Assistant", "PhD", "stipendiat", "dataanalytiker", "statistiker"],
    "preferred_locations": [], "excluded_keywords": [],
    "languages": {"English": "B2-C1", "Norwegian": "A1-A2"},
    "search_languages": {"Norwegian": "B1"},
    "work_authorization": "I do not currently have permission to work in Norway.",
    "cover_letter_language": "English",
}
DEFAULT_SETTINGS = {"sources": [{"type": "nav"}], "model": "qwen3:4b"}
_collection_lock = threading.Lock()


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as output:
            json.dump(value, output, indent=2, ensure_ascii=False)
            output.write("\n")
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def initialize(data_dir: Path) -> None:
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    with JobStore(data_dir / "vacancies.db"):
        pass
    if not (data_dir / "profile.json").exists():
        _write_json(data_dir / "profile.json", DEFAULT_PROFILE)
    if not (data_dir / "sources.json").exists():
        _write_json(data_dir / "sources.json", DEFAULT_SETTINGS)


def read_profile(data_dir: Path) -> dict:
    return load_profile(Path(data_dir) / "profile.json")


def save_profile(data_dir: Path, profile: dict) -> None:
    # Copy because validation supplies defaults; never mutate a UI form object.
    validated = validate_profile(json.loads(json.dumps(profile)))
    _write_json(Path(data_dir) / "profile.json", validated)


def read_settings(data_dir: Path) -> dict:
    result = _read_json(Path(data_dir) / "sources.json")
    if not isinstance(result, dict) or not isinstance(result.get("sources"), list):
        raise ValueError("sources.json must contain a sources list.")
    result.setdefault("model", "qwen3:4b")
    return result


def save_settings(data_dir: Path, settings: dict) -> None:
    if not isinstance(settings, dict) or not isinstance(settings.get("sources"), list):
        raise ValueError("Settings need a sources list.")
    if len(settings["sources"]) > 100:
        raise ValueError("Use at most 100 sources per run.")
    from .local_ai import validate_model_name
    from .company_sources import COMPANY_SOURCE_TYPES, validate_company_source
    validate_model_name(settings.get("model", "qwen3:4b"))
    for source in settings["sources"]:
        if not isinstance(source, dict) or source.get("type") not in {"nav", "greenhouse", "lever", "job_url", *COMPANY_SOURCE_TYPES}:
            raise ValueError("Choose NAV, a supported company source or a job URL for each source.")
        if source["type"] in COMPANY_SOURCE_TYPES:
            validate_company_source(source)
        if source["type"] in ("greenhouse", "lever") and not str(source.get("board", "")).strip():
            raise ValueError("A company source needs its board ID.")
        if source["type"] == "job_url" and not str(source.get("url", "")).startswith(("https://", "http://")):
            raise ValueError("A job page needs its full HTTP(S) URL.")
    _write_json(Path(data_dir) / "sources.json", settings)


def collect(data_dir: Path, settings_override: dict | None = None, *, progress=None) -> dict:
    if not _collection_lock.acquire(blocking=False):
        raise ValueError("Collection is already running. Wait for it to finish.")
    try:
        report = _collect(Path(data_dir), settings_override, progress=progress)
        _write_json(Path(data_dir) / "last-collection.json", report)
        return report
    finally:
        _collection_lock.release()


def _collect(data_dir: Path, settings_override: dict | None = None, *, progress=None) -> dict:
    from .cli import collect_sources
    from .nav import fetch_nav_batch

    settings = settings_override if settings_override is not None else read_settings(data_dir)
    profile = read_profile(data_dir)
    report = {"created": 0, "updated": 0, "withdrawn": 0, "sources": []}
    with JobStore(data_dir / "vacancies.db") as store:
        for index, source in enumerate(settings["sources"], 1):
            if progress is not None:
                label = source.get("name") or source.get("board") or source.get("url") or source.get("type", "source").upper()
                progress(f"Checking {label} ({index} of {len(settings['sources'])})… You can keep browsing.")
            if source.get("type") != "nav":
                item = collect_sources({"sources": [source]}, store)
                report["created"] += item["created"]
                report["updated"] += item["updated"]
                report["sources"].extend(item["sources"])
                continue
            result = {"source": source, "created": 0, "updated": 0, "withdrawn": 0}
            try:
                state_path = data_dir / "nav-state.json"
                state = _read_json(state_path) if state_path.exists() else {}
                keywords = profile.get("target_roles", []) + profile.get("related_roles", []) + profile.get("skills", [])
                if not keywords:
                    raise ValueError("Add target roles or skills in your profile before collecting NAV vacancies.")
                fingerprint = hashlib.sha256(json.dumps(sorted(set(word.casefold() for word in keywords))).encode()).hexdigest()
                if state.get("keyword_hash") != fingerprint:
                    state = {}
                batch = fetch_nav_batch(state, keywords, known_ids=store.source_ids("nav"))
                for job in batch["jobs"]:
                    _, created = store.upsert_job(job)
                    key = "created" if created else "updated"
                    result[key] += 1
                    report[key] += 1
                for source_id in batch["withdrawn_ids"]:
                    if store.withdraw_source_job("nav", source_id):
                        result["withdrawn"] += 1
                        report["withdrawn"] += 1
                # Failed writes never commit a cursor past unprocessed events.
                _write_json(state_path, {**batch["state"], "keyword_hash": fingerprint})
                result.update(ok=True, has_more=batch["has_more"], pages=batch["pages"], seen=batch["seen"], experimental_token=batch["experimental_token"])
                result["coverage"] = "NAV begins with the last 90 days of updates; each run reads at most 5 pages. " + ("More pages remain; collect again to continue." if batch["has_more"] else "Reached the current end of this feed window.")
            except (ValueError, RuntimeError, OSError) as exc:
                result.update(ok=False, error=str(exc))
            report["sources"].append(result)
    return report


def vacancies(data_dir: Path, query: str = "", status: str = "") -> list[dict]:
    profile = read_profile(data_dir)
    with JobStore(Path(data_dir) / "vacancies.db") as store:
        jobs = store.list_jobs(status=status or None, query=query or None)
    visible = []
    for job in jobs:
        job["match"] = match_job(job, profile)
        # NAV requires inactive ads to leave results; an elapsed explicit
        # deadline also keeps the active search clear before the next refresh.
        if job.get("source") == "nav" and job["match"]["expired"]:
            continue
        visible.append(job)
    rank = {"target": 2, "horizon": 1, "other": 0}
    visible.sort(key=lambda job: (rank[job["match"]["track"]], job["match"]["score"] if job["match"]["score"] is not None else -1), reverse=True)
    return visible


def get_vacancy(data_dir: Path, id: int) -> dict:
    with JobStore(Path(data_dir) / "vacancies.db") as store:
        job = store.get_job(id)
        if job is not None:
            job["provenance"] = store.provenance(id)
    if job is None:
        raise ValueError(f"Vacancy {id} does not exist.")
    job["match"] = match_job(job, read_profile(data_dir))
    return job


def import_vacancy(data_dir: Path, job: dict) -> tuple[int, bool]:
    from .cli import validate_job
    with JobStore(Path(data_dir) / "vacancies.db") as store:
        return store.upsert_job(validate_job(job))


def update_workflow(data_dir: Path, id: int, status: str, notes: str) -> None:
    with JobStore(Path(data_dir) / "vacancies.db") as store:
        store.update_status(id, status)
        store.update_notes(id, notes)


def import_email_candidates(data_dir: Path, candidates: list[dict]) -> dict:
    """Persist only the email opportunities explicitly selected in the review UI."""
    from .cli import validate_job
    if not isinstance(candidates, list) or len(candidates) > 500:
        raise ValueError("Review and import at most 500 opportunities at once.")
    normalized = [validate_job(item) for item in candidates]
    prepared = []
    # Validate provenance for the entire review before writing any vacancies.
    # Otherwise one malformed email object can leave an apparently failed
    # import partly saved, with its original message attribution missing.
    for item in normalized:
        raw = item.get("raw_json") or {}
        try:
            if isinstance(raw, str):
                raw = json.loads(raw)
            if not isinstance(raw, dict):
                raise ValueError
            provenance = raw.get("email", raw)
            if not isinstance(provenance, dict):
                raise ValueError
            json.dumps(raw)
        except (ValueError, TypeError, RecursionError):
            raise ValueError("Email opportunity metadata must be a valid JSON object. Review or reload the email before importing.") from None
        external_id = str(provenance.get("message_id") or item.get("source_id") or item["source_url"])
        prepared.append((item, provenance, external_id))
    report = {"created": 0, "existing": 0, "ids": []}
    with JobStore(Path(data_dir) / "vacancies.db") as store:
        for item, provenance, external_id in prepared:
            job_id, created = store.upsert_job(item, fill_only=True)
            store.record_provenance(job_id, "email", external_id, provenance)
            report["created" if created else "existing"] += 1
            report["ids"].append(job_id)
    return report


def import_cv_text(path: str | Path) -> str:
    path = Path(path)
    if path.stat().st_size > 15 * 1024 * 1024:
        raise ValueError("Use a CV smaller than 15 MB.")
    extension = path.suffix.lower()
    if extension in (".txt", ".md"):
        result = path.read_text(encoding="utf-8-sig")
    elif extension == ".docx":
        try:
            with zipfile.ZipFile(path) as archive:
                info = archive.getinfo("word/document.xml")
                if info.file_size > 8 * 1024 * 1024:
                    raise ValueError("The CV document is too large to extract.")
                tree = ElementTree.fromstring(archive.read(info))
            namespace = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
            result = "\n".join("".join(paragraph.itertext()) for paragraph in tree.findall(".//w:p", namespace))
        except (zipfile.BadZipFile, KeyError, ElementTree.ParseError) as exc:
            raise ValueError("This DOCX could not be read. Save it again or paste your CV text.") from exc
    elif extension == ".pdf":
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise ValueError("PDF support needs the free pypdf package. Run: python -m pip install pypdf. You can also paste CV text or use DOCX/TXT.") from exc
        try:
            reader = PdfReader(path)
            if len(reader.pages) > 30:
                raise ValueError("Use a CV of at most 30 pages.")
            result = "\n".join(page.extract_text() or "" for page in reader.pages)
        except Exception as exc:
            raise ValueError("This PDF could not be read. Try an unencrypted text PDF, DOCX, or paste your CV text.") from exc
    else:
        raise ValueError("Choose a TXT, Markdown, DOCX or text PDF CV.")
    if not result.strip():
        raise ValueError("No readable text was found. Scanned images need OCR; paste the CV text instead.")
    if len(result) > 50000:
        raise ValueError("The extracted CV exceeds 50,000 characters. Please shorten it before importing.")
    return result.strip()


def suggest_profile(data_dir: Path, cv_text: str) -> dict:
    from .local_ai import extract_profile
    return extract_profile(cv_text, model=read_settings(data_dir)["model"])


def write_letter(data_dir: Path, id: int) -> dict:
    from .local_ai import generate_cover_letter
    job = get_vacancy(data_dir, id)
    if not job.get("is_active", True):
        raise ValueError("This vacancy has been withdrawn. Select an active opportunity.")
    profile = read_profile(data_dir)
    model = read_settings(data_dir)["model"]
    result = generate_cover_letter(job, profile, model=model)
    with JobStore(Path(data_dir) / "vacancies.db") as store:
        # A generation request never changes the application status.
        result["id"] = store.save_draft(id, result["cover_letter"], model=model, profile_hash=profile_hash(profile))
    return result


def save_edited_letter(data_dir: Path, id: int, content: str) -> int:
    with JobStore(Path(data_dir) / "vacancies.db") as store:
        return store.save_draft(id, content, model="user-edited", profile_hash=profile_hash(read_profile(data_dir)))


def draft_history(data_dir: Path, id: int) -> list[dict]:
    with JobStore(Path(data_dir) / "vacancies.db") as store:
        return store.list_drafts(id)


def available_models() -> list[str]:
    from .local_ai import list_local_models
    return list_local_models()
