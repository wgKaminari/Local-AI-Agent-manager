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
    "years_of_experience": None,
    "target_roles": ["Data Scientist", "Data Science", "AI Engineer", "Artificial Intelligence", "Machine Learning Engineer", "ML Engineer", "Mathematician", "maskinlæring", "kunstig intelligens", "matematiker"],
    "related_roles": ["Data Analyst", "Data Engineer", "Research Engineer", "Research Scientist", "Applied Scientist", "Statistician", "Quantitative Analyst", "Analytics Engineer", "Research Assistant", "PhD", "stipendiat", "dataanalytiker", "statistiker"],
    "preferred_locations": [], "excluded_keywords": [],
    "languages": {"English": "B2-C1", "Norwegian": "A1-A2"},
    "search_languages": {"Norwegian": "B1"},
    "work_authorization": "I do not currently have permission to work in Norway.",
    "cover_letter_language": "English",
}
DEFAULT_SETTINGS = {"sources": [{"type": "nav"}], "model": "qwen3:4b"}
# Parallel source downloads. Six keeps a full run quick without hammering the
# few shared ATS APIs (Greenhouse, Lever, Ashby) that most employers use.
COLLECTION_WORKERS = 6
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


def read_collection_report(data_dir: Path) -> dict | None:
    """The last collection summary, or None when missing or unreadable.

    It is a convenience record; a damaged file must not stop the app opening.
    """
    try:
        report = _read_json(Path(data_dir) / "last-collection.json")
    except (OSError, ValueError):
        return None
    return report if isinstance(report, dict) else None


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
    result.setdefault("active_country", "")
    result.setdefault("country_presets_added", [])
    return result


def save_settings(data_dir: Path, settings: dict) -> None:
    if not isinstance(settings, dict) or not isinstance(settings.get("sources"), list):
        raise ValueError("Settings need a sources list.")
    if len(settings["sources"]) > 250:
        raise ValueError("Use at most 250 configured sources.")
    from .local_ai import validate_model_name
    from .countries import COUNTRIES, validate_countries
    if settings.get("active_country", "") not in {"", *COUNTRIES}:
        raise ValueError("Choose NO, US, DE, UA or all countries.")
    validate_countries(settings.get("country_presets_added", []))
    from .company_sources import COMPANY_SOURCE_TYPES, validate_company_source
    validate_model_name(settings.get("model", "qwen3:4b"))
    for source in settings["sources"]:
        if not isinstance(source, dict) or source.get("type") not in {"nav", "greenhouse", "lever", "ashby", "job_url", *COMPANY_SOURCE_TYPES}:
            raise ValueError("Choose NAV, a supported company source or a job URL for each source.")
        if "countries" in source:
            validate_countries(source["countries"])
        if source["type"] in COMPANY_SOURCE_TYPES:
            validate_company_source(source)
        if source["type"] in ("greenhouse", "lever", "ashby") and not str(source.get("board", "")).strip():
            raise ValueError("A company source needs its board ID.")
        if source["type"] == "job_url" and not str(source.get("url", "")).startswith(("https://", "http://")):
            raise ValueError("A job page needs its full HTTP(S) URL.")
    _write_json(Path(data_dir) / "sources.json", settings)


def _merge_catalog_sources(settings: dict, entries: list[dict], countries: list[str]) -> None:
    from .company_catalog import source_for_countries
    configured = settings["sources"]

    def identity(source):
        # Different Workday searches or crawl limits are separate sources.
        # Only country/location scopes and display names may be combined.
        value = {key: item for key, item in source.items() if key not in {"name", "countries", "locations"}}
        if value.get("type") == "lever":
            value.setdefault("region", "global")
        return json.dumps(value, sort_keys=True, ensure_ascii=False)

    for entry in entries:
        source = source_for_countries(entry, countries)
        if source is None:
            continue
        existing = next((item for item in configured if identity(item) == identity(source)
                         and (item.get("countries") or not item.get("locations")
                              or item.get("locations") == source.get("locations"))), None)
        if existing is None:
            configured.append(source)
            continue
        # An unscoped existing source already covers the selected country;
        # do not accidentally narrow a user's global board configuration.
        if existing.get("countries"):
            existing["countries"] = list(dict.fromkeys([*existing["countries"], *source["countries"]]))
            if existing.get("locations") and source.get("locations"):
                existing["locations"] = list(dict.fromkeys([*existing["locations"], *source["locations"]]))
            else:
                # A residual location filter could silently exclude every
                # job in the newly selected country. Country filtering is
                # still applied independently by the collector.
                existing.pop("locations", None)
    if len(configured) > 250:
        raise ValueError(f"Adding these presets would use {len(configured)} sources; the limit is 250. Remove unused sources first. Your saved country and sources were not changed.")


def select_country(data_dir: Path, country: str) -> dict:
    """Save a country view and add its employer presets once, as one update.

    Shared candidate facts are never rewritten. A source-cap failure leaves
    both the prior selection and its presets intact, so the UI can explain it
    without silently enabling only part of a country's directory.
    """
    from .countries import COUNTRIES
    from .company_catalog import company_catalog

    if not isinstance(country, str) or country not in {"", *COUNTRIES}:
        raise ValueError("Choose NO, US, DE, UA or all countries.")
    settings = read_settings(data_dir)
    if country and country not in settings["country_presets_added"]:
        _merge_catalog_sources(settings, company_catalog(country), [country])
        settings["country_presets_added"].append(country)
    settings["active_country"] = country
    save_settings(data_dir, settings)
    return settings


def add_catalog_sources(data_dir: Path, ids: list[str], country: str = "") -> dict:
    """Add explicitly selected employer collectors without partial saves."""
    from .countries import COUNTRIES
    from .company_catalog import company_catalog

    if not isinstance(country, str) or country not in {"", *COUNTRIES}:
        raise ValueError("Choose NO, US, DE, UA or all countries.")
    if not isinstance(ids, list) or len(ids) > 500 or any(not isinstance(item, str) for item in ids):
        raise ValueError("Choose a list of employer directory IDs.")
    catalog = {entry["id"]: entry for entry in company_catalog(country)}
    if any(item not in catalog or not catalog[item].get("source") for item in ids):
        raise ValueError("Choose employers with an automatic collector in the selected country.")
    settings = read_settings(data_dir)
    _merge_catalog_sources(settings, [catalog[item] for item in dict.fromkeys(ids)], [country] if country else list(COUNTRIES))
    save_settings(data_dir, settings)
    return settings


def collect(data_dir: Path, settings_override: dict | None = None, *, progress=None) -> dict:
    if not _collection_lock.acquire(blocking=False):
        raise ValueError("Collection is already running. Wait for it to finish.")
    try:
        report = _collect(Path(data_dir), settings_override, progress=progress)
        _write_json(Path(data_dir) / "last-collection.json", report)
        return report
    finally:
        _collection_lock.release()


def _collection_scope(settings: dict) -> list[dict]:
    """Sources this run will check, already scoped to the active country."""
    country = settings.get("active_country", "")
    selected = []
    for source in settings["sources"]:
        if country and ((source.get("type") == "nav" and country != "NO") or
                        (source.get("countries") and country not in source["countries"])):
            continue
        if country and source.get("type") != "nav":
            source = {**source, "countries": [country]}
        selected.append(source)
    return selected


def _fetch_nav(data_dir: Path, profile: dict, known_ids: list[str]) -> dict:
    """Read the next NAV feed batch; the checkpoint is saved only after its writes."""
    from .nav import fetch_nav_batch
    try:
        state_path = data_dir / "nav-state.json"
        state = _read_json(state_path) if state_path.exists() else {}
        keywords = profile.get("target_roles", []) + profile.get("related_roles", []) + profile.get("skills", [])
        if not keywords:
            raise ValueError("Add target roles or skills in your profile before collecting NAV vacancies.")
        fingerprint = hashlib.sha256(json.dumps(sorted(set(word.casefold() for word in keywords))).encode()).hexdigest()
        if state.get("keyword_hash") != fingerprint:
            state = {}
        return {"batch": fetch_nav_batch(state, keywords, known_ids=known_ids), "fingerprint": fingerprint}
    except (ValueError, RuntimeError, OSError) as exc:
        return {"error": str(exc)}


def _store_nav(data_dir: Path, source: dict, fetched: dict, store: JobStore, report: dict) -> None:
    result = {"source": source, "created": 0, "updated": 0, "withdrawn": 0}
    try:
        if "error" in fetched:
            raise ValueError(fetched["error"])
        batch = fetched["batch"]
        counts = {"created": 0, "updated": 0, "withdrawn": 0}
        with store.transaction():
            for job in batch["jobs"]:
                _, created = store.upsert_job(job)
                counts["created" if created else "updated"] += 1
            for source_id in batch["withdrawn_ids"]:
                if store.withdraw_source_job("nav", source_id):
                    counts["withdrawn"] += 1
        for key, count in counts.items():
            result[key] = count
            report[key] += count
        # Failed writes never commit a cursor past unprocessed events.
        _write_json(data_dir / "nav-state.json", {**batch["state"], "keyword_hash": fetched["fingerprint"]})
        result.update(ok=True, has_more=batch["has_more"], pages=batch["pages"], seen=batch["seen"], experimental_token=batch["experimental_token"])
        result["coverage"] = "NAV begins with the last 90 days of updates; each run reads at most 5 pages. " + ("More pages remain; collect again to continue." if batch["has_more"] else "Reached the current end of this feed window.")
    except (ValueError, RuntimeError, OSError) as exc:
        result.update(ok=False, error=str(exc))
    report["sources"].append(result)


def _collect(data_dir: Path, settings_override: dict | None = None, *, progress=None) -> dict:
    """Download sources concurrently; write each one in configured order.

    Every source still commits as its own transaction on this thread, so
    cross-source duplicate handling stays deterministic. Only network reads,
    which dominated a run, overlap.
    """
    from concurrent.futures import ThreadPoolExecutor
    from datetime import datetime, timezone
    from . import cli

    settings = settings_override if settings_override is not None else read_settings(data_dir)
    profile = read_profile(data_dir)
    report = {"created": 0, "updated": 0, "withdrawn": 0, "sources": [],
              "country": settings.get("active_country", ""),
              "started_at": datetime.now(timezone.utc).isoformat()}
    selected = _collection_scope(settings)
    if progress is not None and selected:
        progress(f"Checking {len(selected)} source{'' if len(selected) == 1 else 's'}… You can keep browsing.")
    with JobStore(data_dir / "vacancies.db") as store:
        pool = ThreadPoolExecutor(max_workers=COLLECTION_WORKERS, thread_name_prefix="collect")
        try:
            futures = [pool.submit(_fetch_nav, data_dir, profile, store.source_ids("nav"))
                       if source.get("type") == "nav" else pool.submit(cli.fetch_source, source)
                       for source in selected]
            for index, (source, future) in enumerate(zip(selected, futures), 1):
                fetched = future.result()
                if source.get("type") == "nav":
                    _store_nav(data_dir, source, fetched, store, report)
                else:
                    cli.store_fetched_source(fetched, store, report)
                if progress is not None:
                    label = source.get("name") or source.get("board") or source.get("url") or source.get("type", "source").upper()
                    progress(f"Checked {label} ({index} of {len(selected)})… You can keep browsing.")
        finally:
            # After an unexpected failure, stop queued downloads; running ones
            # never touch the store and finish on their own.
            pool.shutdown(wait=False, cancel_futures=True)
    report["completed_at"] = datetime.now(timezone.utc).isoformat()
    return report


def _deadline_passed(job: dict) -> bool:
    from datetime import date
    try:
        return date.fromisoformat(str(job.get("deadline") or "")[:10]) < date.today()
    except ValueError:
        return False


def vacancies(data_dir: Path, query: str = "", status: str = "", country: str = "", *, include_inactive: bool = False,
              recommendations_only: bool = False, coverage: dict | None = None,
              stages: set[str] | frozenset[str] | None = None) -> list[dict]:
    """Return matched vacancies, best first.

    stages limits which workflow stages are matched and returned. Jobs in
    other stages still count toward coverage, so Saved and Applications report
    the same search scope as before without matching every stored advert.
    """
    from .countries import COUNTRIES, job_countries
    if country and country not in {*COUNTRIES, "unknown"}:
        raise ValueError("Choose a supported country or unknown.")
    profile = read_profile(data_dir)
    with JobStore(Path(data_dir) / "vacancies.db") as store:
        jobs = store.list_jobs(status=status or None, query=query or None, include_inactive=include_inactive)
    visible = []
    in_scope, employers = 0, set()
    for job in jobs:
        job["countries"] = job_countries(job)
        if country == "unknown" and job["countries"]:
            continue
        if country and country != "unknown" and country not in job["countries"]:
            continue
        if stages is not None and job["status"] not in stages:
            # Count scope exactly as matching would: only active views drop
            # NAV adverts whose deadline has passed.
            if include_inactive or job.get("source") != "nav" or not _deadline_passed(job):
                in_scope += 1
                employers.add(job["company"])
            continue
        if recommendations_only and not include_inactive and job.get("source") == "nav":
            # Count precisely the same active NAV scope before the early role
            # gate; a non-recommended advert need not parse its full description.
            if _deadline_passed(job):
                continue
        job["match"] = match_job(job, profile, recommendations_only=recommendations_only)
        # NAV requires inactive ads to leave results; an elapsed explicit
        # deadline also keeps the active search clear before the next refresh.
        if not include_inactive and job.get("source") == "nav" and job["match"] and job["match"]["expired"]:
            continue
        in_scope += 1
        employers.add(job["company"])
        if job["match"] is None:
            continue
        visible.append(job)
    if coverage is not None:
        coverage.update(in_scope=in_scope, employers=len(employers))
    rank = {"target": 2, "horizon": 1, "other": 0}
    visible.sort(key=lambda job: (
        job["match"].get("experience", {}).get("status") != "mismatch",
        not bool(job["match"].get("excluded_terms") or job["match"].get("expired")),
        rank[job["match"]["track"]],
        job["match"]["score"] if job["match"]["score"] is not None else -1), reverse=True)
    return visible


def discovery_coverage(data_dir: Path, country: str = "") -> dict:
    """Report configured collection separately from a directory's research links."""
    from .company_catalog import company_catalog, job_board_catalog
    from .countries import COUNTRIES
    if country and country not in COUNTRIES:
        raise ValueError("Choose a supported country or all countries.")
    settings = read_settings(data_dir)
    sources = [source for source in settings["sources"] if not country or (
        (source.get("type") != "nav" or country == "NO") and
        (not source.get("countries") or country in source["countries"]))]
    catalog = company_catalog(country)
    report = read_collection_report(data_dir)
    # Old reports lack country metadata: don't present them as this market's run.
    current_report = report if report and report.get("country") == country else None
    checked = current_report.get("sources", []) if current_report else []
    return {"country": country, "directory_employers": len(catalog),
            "available_collectors": sum(bool(row.get("source")) for row in catalog),
            "job_board_links": len(job_board_catalog(country)),
            "configured_collectors": len(sources),
            "last_run": current_report.get("started_at") if current_report else None,
            "checked": len(checked), "failed": sum(not row.get("ok") for row in checked),
            "limited": sum(bool(row.get("has_more")) for row in checked),
            "legacy_report": bool(report and "country" not in report)}


def get_vacancy(data_dir: Path, id: int) -> dict:
    with JobStore(Path(data_dir) / "vacancies.db") as store:
        job = store.get_job(id)
        if job is not None:
            job["provenance"] = store.provenance(id)
    if job is None:
        raise ValueError(f"Vacancy {id} does not exist.")
    from .countries import job_countries
    job["countries"] = job_countries(job)
    job["match"] = match_job(job, read_profile(data_dir))
    return job


def import_vacancy(data_dir: Path, job: dict) -> tuple[int, bool]:
    from .cli import validate_job
    with JobStore(Path(data_dir) / "vacancies.db") as store:
        return store.upsert_job(validate_job(job))


def update_workflow(data_dir: Path, id: int, status: str, notes: str) -> None:
    with JobStore(Path(data_dir) / "vacancies.db") as store:
        with store.transaction():
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
        with store.transaction():
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


def write_letter(data_dir: Path, id: int, *, persist: bool = True) -> dict:
    from .local_ai import generate_cover_letter
    job = get_vacancy(data_dir, id)
    if not job.get("is_active", True):
        raise ValueError("This vacancy has been withdrawn. Select an active opportunity.")
    from .countries import profile_for_job
    profile = profile_for_job(read_profile(data_dir), job)
    model = read_settings(data_dir)["model"]
    result = generate_cover_letter(job, profile, model=model)
    if persist:
        with JobStore(Path(data_dir) / "vacancies.db") as store:
            # A generation request never changes the application status.
            result["id"] = store.save_draft(id, result["cover_letter"], model=model, profile_hash=profile_hash(profile))
    else:
        result["draft_metadata"] = {"model": model, "profile_hash": profile_hash(profile)}
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


def discover_application(data_dir: Path, id: int, manual_text: str = "") -> dict:
    from .application_forms import discover_application_form, parse_manual_questions
    job = get_vacancy(data_dir, id)
    if not job.get("is_active", True):
        raise ValueError("This vacancy has been withdrawn.")
    form = parse_manual_questions(manual_text) if manual_text.strip() else discover_application_form(job)
    return {"form": form, "answers": [], "review_notes": [], "job_id": id}


def prepare_application(data_dir: Path, id: int, form: dict, *, persist: bool = True) -> dict:
    from .application_answers import generate_application_answers
    from .countries import profile_for_job
    job = get_vacancy(data_dir, id)
    if not job.get("is_active", True):
        raise ValueError("This vacancy has been withdrawn.")
    profile = profile_for_job(read_profile(data_dir), job)
    result = generate_application_answers(job, profile, form, writing_style=profile.get("writing_style"), model=read_settings(data_dir)["model"])
    from .application_forms import form_fingerprint
    history = application_history(data_dir, id)
    if history and form_fingerprint(history[0]["payload"]["form"]) == form_fingerprint(form):
        attachments = history[0]["payload"].get("attachments")
        if attachments:
            result["attachments"] = attachments
    result["job_id"] = id
    result["profile_hash"] = profile_hash(profile)
    if persist:
        save_application(data_dir, id, result)
    return result


def save_application(data_dir: Path, id: int, payload: dict) -> int:
    if not isinstance(payload, dict) or not isinstance(payload.get("form"), dict) or not isinstance(payload.get("answers", []), list):
        raise ValueError("Application preparation needs a form and an answers list.")
    if payload.get("job_id", id) != id:
        raise ValueError("These answers belong to a different vacancy.")
    from .application_forms import validate_form
    validate_form(payload["form"])
    fields = {field["id"]: field for field in payload["form"]["fields"]}
    seen = set()
    for answer in payload.get("answers", []):
        if not isinstance(answer, dict) or not isinstance(answer.get("field_id"), str) or answer["field_id"] not in fields or answer["field_id"] in seen:
            raise ValueError("Each prepared answer must match one unique form question.")
        seen.add(answer["field_id"])
        if not isinstance(answer.get("answer", ""), str) or not isinstance(answer.get("selected_options", []), list) or any(not isinstance(option, str) for option in answer.get("selected_options", [])):
            raise ValueError("Application answers must contain text and a list of option values.")
    with JobStore(Path(data_dir) / "vacancies.db") as store:
        return store.save_preparation(id, payload)


def application_history(data_dir: Path, id: int) -> list[dict]:
    with JobStore(Path(data_dir) / "vacancies.db") as store:
        return store.preparations(id)


def send_reviewed_application(data_dir: Path, id: int, session, preview: dict) -> dict:
    """Called only by the explicit Send button; records intent before network work."""
    job = get_vacancy(data_dir, id)
    if not job.get("is_active", True):
        raise ValueError("This vacancy has been withdrawn.")
    snapshot = {key: preview.get(key) for key in ("destination", "fields", "files")}
    fingerprint = hashlib.sha256(json.dumps(snapshot, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    with JobStore(Path(data_dir) / "vacancies.db") as store:
        attempt = store.begin_delivery(id, fingerprint, preview["destination"])
    from .browser_delivery import BrowserDeliveryError
    try:
        result = session.send(preview["token"])
    except BrowserDeliveryError:
        result = {"status": "blocked", "message": "The reviewed browser form was no longer valid. No send request was made; prepare and review a new preview."}
    except Exception:
        result = {"status": "uncertain", "message": "The delivery attempt did not finish cleanly. Check the employer page for confirmation before retrying."}
    with JobStore(Path(data_dir) / "vacancies.db") as store:
        store.finish_delivery(attempt, result)
    # Only an employer confirmation can establish success. Never mark applied
    # based merely on a browser click or an HTTP response.
    return {**result, "attempt_id": attempt}
