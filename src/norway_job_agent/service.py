"""Application use cases shared by the desktop interface and command line."""

from __future__ import annotations

import json
import hashlib
import os
import re
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


class SettingsFileError(ValueError):
    """profile.json or sources.json cannot be read; the app offers a restore."""

    def __init__(self, name: str, path: Path, detail: str):
        self.file = name
        label = "profile" if name == "profile.json" else "source settings"
        super().__init__(f"Your {label} file is damaged and cannot be read ({detail}). "
                         f"Restore the last automatic backup, or fix the file at {path}.")


def read_profile(data_dir: Path) -> dict:
    path = Path(data_dir) / "profile.json"
    try:
        return load_profile(path)
    except UnicodeError as exc:
        raise SettingsFileError("profile.json", path, "not UTF-8 text") from exc
    except ValueError as exc:
        raise SettingsFileError("profile.json", path, str(exc)) from exc


def save_profile(data_dir: Path, profile: dict) -> None:
    # Copy because validation supplies defaults; never mutate a UI form object.
    validated = validate_profile(json.loads(json.dumps(profile)))
    _write_json(Path(data_dir) / "profile.json", validated)


def read_settings(data_dir: Path) -> dict:
    path = Path(data_dir) / "sources.json"
    try:
        result = _read_json(path)
    except (UnicodeError, ValueError) as exc:
        raise SettingsFileError("sources.json", path, "not valid JSON") from exc
    if not isinstance(result, dict) or not isinstance(result.get("sources"), list):
        raise SettingsFileError("sources.json", path, "it must contain a sources list")
    result.setdefault("model", "qwen3:4b")
    result.setdefault("active_country", "")
    result.setdefault("country_presets_added", [])
    from .market import validate_market_settings
    try:
        # Older settings predate market-wide job sites; they start enabled.
        result["market"] = validate_market_settings(result.get("market"))
    except ValueError as exc:
        raise SettingsFileError("sources.json", path, str(exc)) from exc
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
    from .market import validate_market_settings
    validate_model_name(settings.get("model", "qwen3:4b"))
    settings = {**settings, "market": validate_market_settings(settings.get("market"))}
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


def _collection_scope(settings: dict, countries: list[str]) -> list[dict]:
    """Employer sources a refresh checks: every one serving your countries.

    A refresh covers all your target countries; the country switch only
    filters what you see. (It used to refresh the selected country alone, so
    countries never selected were never collected.)
    """
    selected, seen = [], {}
    for source in settings["sources"]:
        if source.get("type") == "nav":
            if "NO" in countries and not any(item.get("type") == "nav" for item in selected):
                selected.append(source)
            continue
        if not source.get("countries"):
            candidate = dict(source)  # An unscoped board keeps every posting, as before.
        else:
            scope = [code for code in source["countries"] if code in countries]
            if not scope:
                continue
            candidate = {**source, "countries": scope}
        # The same board configured twice ("bekk" and "Bekk") downloads once.
        key = json.dumps({name: value.casefold().rstrip("/") if isinstance(value, str) else value
                          for name, value in candidate.items() if name not in {"name", "countries", "locations"}},
                         sort_keys=True, ensure_ascii=False)
        if key in seen:
            first = seen[key]
            if "countries" in first:
                if "countries" in candidate:
                    first["countries"] = list(dict.fromkeys([*first["countries"], *candidate["countries"]]))
                else:
                    first.pop("countries")
            continue
        seen[key] = candidate
        selected.append(candidate)
    return selected


def _target_countries(profile: dict) -> list[str]:
    from .countries import COUNTRIES
    return [code for code in (profile.get("target_countries") or list(COUNTRIES)) if code in COUNTRIES] or list(COUNTRIES)


MARKET_STATE_FILE = "market-state.json"
# Rebuildable matching results (experience requirements per advert text).
ANALYSIS_CACHE_FILE = "analysis-cache.db"
# Per provider and run: full adverts loaded during collection for role matches.
# The rest load when opened, so a refresh stays polite and quick.
_DETAIL_BUDGET = {"arbeitsagentur": 60, "linkedin": 6, "djinni": 0}


def _market_state(data_dir: Path) -> dict:
    try:
        state = _read_json(data_dir / MARKET_STATE_FILE)
    except (OSError, ValueError):
        return {}
    return state if isinstance(state, dict) else {}


def _save_market_state(data_dir: Path, state: dict) -> None:
    _write_json(data_dir / MARKET_STATE_FILE, {key: value for key, value in state.items() if not key.startswith("_")})


def _role_terms_for(profile: dict, country: str = "") -> list[str]:
    preferences = profile.get("country_preferences", {}).get(country, {}) if country else {}
    return [*(preferences.get("target_roles") or profile.get("target_roles", [])),
            *(preferences.get("related_roles") or profile.get("related_roles", []))]


def _fetch_nav(data_dir: Path, queries: list[str], known_ids: list[str], progress=None, *,
               everything: bool = False, seconds: float | None = None, it_only: bool = False) -> dict:
    """Bring the NAV index up to date and download adverts.

    everything downloads every active advert not stored yet (search matches
    first); otherwise only those answering the queries. Network and the index
    cache only; _store_nav writes the jobs and records which were checked.
    """
    from . import nav_index
    try:
        if not queries and not everything:
            raise ValueError("Add target roles in your profile, or a saved search, before collecting NAV vacancies.")
        synced = nav_index.sync(data_dir, progress=progress)
        candidates = nav_index.find(data_dir, queries, known=set(known_ids), everything=everything, skip_outside=it_only)
        if progress is not None and candidates:
            progress(f"NAV: {len(candidates):,} adverts to load…")
        options = {"seconds": seconds} if seconds is not None else {}
        loaded = nav_index.hydrate(data_dir, candidates, progress=progress, **options)
        return {"sync": synced, "candidates": len(candidates), "everything": everything,
                "matching": sum(bool(item["queries"]) for item in candidates), **loaded,
                "pending_withdrawals": nav_index.pending_withdrawals(data_dir)}
    except (ValueError, RuntimeError, OSError) as exc:
        return {"error": str(exc)}


def _in_scope(jobs, it_only: bool) -> tuple[list, list]:
    """(kept, outside): with "IT jobs only", adverts that are not IT jobs stay out."""
    if not it_only:
        return list(jobs), []
    from .it_jobs import is_it
    kept, outside = [], []
    for job in jobs:
        (kept if is_it(job) else outside).append(job)
    return kept, outside


def _store_nav(data_dir: Path, source: dict, fetched: dict, store: JobStore, report: dict, it_only: bool = False) -> None:
    from . import job_index, nav_index
    result = {"source": source, "created": 0, "updated": 0, "withdrawn": 0}
    try:
        if "error" in fetched:
            raise ValueError(fetched["error"])
        counts = {"created": 0, "updated": 0, "withdrawn": 0}
        skipped = 0
        withdrawals = list(dict.fromkeys([*fetched["withdrawn"], *fetched["pending_withdrawals"]]))
        jobs, outside = _in_scope(fetched["jobs"], it_only)
        with store.transaction():
            if outside:
                job_index.discard(store, "nav", [job["source_id"] for job in outside])
            for job in jobs:
                try:
                    _, created = store.upsert_job(job)
                    counts["created" if created else "updated"] += 1
                except ValueError:
                    skipped += 1
            for source_id in withdrawals:
                if store.withdraw_source_job("nav", source_id):
                    counts["withdrawn"] += 1
        # Only now are these adverts safely stored; record them as checked.
        nav_index.mark_hydrated(data_dir, fetched["marks"])
        if outside:
            nav_index.mark_outside(data_dir, [job["source_id"] for job in outside])
        nav_index.mark_inactive(data_dir, fetched["withdrawn"])
        nav_index.mark_withdrawals_applied(data_dir, fetched["pending_withdrawals"])
        for key, count in counts.items():
            result[key] = count
            report[key] += count
        synced = fetched["sync"]
        more = synced["has_more"] or fetched["remaining"] > 0
        result.update(ok=True, has_more=more, pages=synced["pages"], seen=synced["events"],
                      experimental_token=synced["experimental_token"], fetched=synced["active"],
                      scanned=synced["active"], available=synced["active"], matched=len(jobs), outside_it=len(outside))
        coverage = f"NAV index: {synced['active']:,} active Norwegian vacancies (read {synced['pages']} feed pages this run). "
        if fetched.get("everything"):
            coverage += (f"Loaded {len(fetched['jobs']):,} full adverts this run "
                         f"({fetched.get('matching', 0):,} of the {fetched['candidates']:,} to load match your searches).")
        else:
            coverage += f"{fetched['candidates']} titles matched your searches; {len(fetched['jobs'])} saved."
        if synced.get("interrupted"):
            coverage += " NAV's feed stopped answering partway; the next refresh continues from there."
        elif synced["has_more"]:
            coverage += " The feed has more updates; the next refresh continues reading."
        if fetched.get("failed"):
            coverage += f" {fetched['failed']:,} adverts could not be downloaded ({fetched['failure']})."
        if fetched["remaining"]:
            coverage += f" {fetched['remaining']:,} more adverts load on the next refresh."
        if skipped:
            coverage += f" {skipped} adverts conflicted with saved records and were left unchanged."
        result["coverage"] = coverage
    except (ValueError, RuntimeError, OSError) as exc:
        result.update(ok=False, error=str(exc))
    report["sources"].append(result)


def _store_market(run, store: JobStore, report: dict, it_only: bool = False) -> dict:
    """Write one provider's results; a conflicting advert skips only itself."""
    result = run.report()
    counts = {"created": 0, "updated": 0, "withdrawn": 0}
    skipped = 0
    jobs, outside = _in_scope(run.jobs, it_only)
    if outside:
        result["outside_it"] = len(outside)
        result["matched"] = len(jobs)
    try:
        with store.transaction():
            for job in jobs:
                try:
                    _, created = store.upsert_job(job)
                except ValueError:
                    skipped += 1
                    continue
                counts["created" if created else "updated"] += 1
            # A complete employer board retires the postings it no longer lists.
            for source, prefix, ids in run.snapshots:
                counts["withdrawn"] += store.reconcile_source_snapshot(source, prefix, ids)
    except (ValueError, OSError) as exc:
        result.update(ok=False, error=str(exc))
        counts = {"created": 0, "updated": 0, "withdrawn": 0}
    for key, count in counts.items():
        result[key] = count
        report[key] = report.get(key, 0) + count
    if skipped:
        result["coverage"] += f" {skipped} conflicted with saved records and were left unchanged."
    report["sources"].append(result)
    return result


def _submit_market(data_dir: Path, pool, settings: dict, profile: dict, country: str, queries_for,
                   *, collection_detail: bool, progress=None) -> tuple[list, dict]:
    """Start market-wide searches; returns (kind, label, future) rows and their shared state.

    A refresh (collection_detail) reads broadly when "collect everything" is
    on; a live search reads only its one query, within a shorter budget.
    """
    from . import adzuna, employer_pack, market
    rows = []
    state = _market_state(data_dir)
    broad = collection_detail and settings["market"].get("collect_everything", True)
    seconds = market.REFRESH_SECONDS if collection_detail else market.LIVE_SECONDS
    targets = [country] if country else _target_countries(profile)
    for name in market.providers_for(country, settings["market"]["enabled"]):
        served = [code for code in market.PROVIDERS[name].countries if code in targets]
        if not served or (not collection_detail and name in market.REFRESH_ONLY):
            continue
        options = {}
        if name == "adzuna":
            key = adzuna.read_key(data_dir)
            if key is None:
                continue  # Nothing to search until you add your free key.
            options = {"credentials": key, "request_budget": adzuna.REQUESTS_PER_REFRESH if collection_detail
                       else adzuna.REQUESTS_PER_LIVE_SEARCH}
        # A one-country site searches with that country's role preferences;
        # remote boards and LinkedIn cover all your countries in one run.
        code = served[0] if len(market.PROVIDERS[name].countries) == 1 else country
        budget = _DETAIL_BUDGET.get(name, 0) if collection_detail else 0
        if name == "us_employers":
            options = {"skip_boards": employer_pack.configured_keys(settings["sources"])}
        rows.append(("market", market.PROVIDERS[name].name, pool.submit(
            market.search_provider, name, queries_for(code), code, role_terms=_role_terms_for(profile, code),
            detail_budget=budget, state=state, progress=progress, broad=broad, seconds=seconds,
            it_only=settings["market"].get("it_only", True), **options)))
    return rows, state


def _collect(data_dir: Path, settings_override: dict | None = None, *, progress=None) -> dict:
    """Download sources concurrently; write each one in submission order.

    Employer boards, NAV's index and market-wide job sites download in
    parallel. Every source commits as its own transaction on this thread, so
    cross-source duplicate handling stays deterministic.
    """
    from concurrent.futures import ThreadPoolExecutor
    from datetime import datetime, timezone
    from . import collection
    from .market import search_queries, validate_market_settings

    settings = settings_override if settings_override is not None else read_settings(data_dir)
    # Command-line overrides may omit job sites; they then use saved defaults.
    settings = {**settings, "market": validate_market_settings(settings.get("market"))}
    profile = read_profile(data_dir)
    countries = _target_countries(profile)
    country = ""  # Every target country; the country switch only filters views.
    report = {"created": 0, "updated": 0, "withdrawn": 0, "sources": [], "country": country,
              "countries": countries, "started_at": datetime.now(timezone.utc).isoformat()}
    selected = _collection_scope(settings, countries)
    queries_for = lambda code: search_queries(profile, settings["market"], code)
    with JobStore(data_dir / "vacancies.db") as store:
        pool = ThreadPoolExecutor(max_workers=COLLECTION_WORKERS + 4, thread_name_prefix="collect")
        try:
            # Many-request sources start first so they overlap the employer boards.
            rows, state = _submit_market(data_dir, pool, settings, profile, country, queries_for,
                                         collection_detail=True, progress=progress)
            for source in selected:
                if source.get("type") == "nav":
                    rows.insert(0, ("nav", "NAV", pool.submit(
                        _fetch_nav, data_dir, queries_for("NO"), store.source_ids("nav"), progress,
                        everything=settings["market"].get("collect_everything", True),
                        it_only=settings["market"].get("it_only", True)), source))
                else:
                    label = source.get("name") or source.get("board") or source.get("url") or source.get("type", "source").upper()
                    rows.append(("employer", label, pool.submit(collection.fetch_source, source)))
            if progress is not None and rows:
                sites = sum(row[0] != "employer" for row in rows)
                progress(f"Checking {len(rows)} source{'' if len(rows) == 1 else 's'}"
                         + (f", including {sites} whole-market job site{'' if sites == 1 else 's'}" if sites else "")
                         + "… You can keep browsing.")
            it_only = settings["market"].get("it_only", True)
            for index, row in enumerate(rows, 1):
                kind, label, future = row[:3]
                fetched = future.result()
                if kind == "nav":
                    _store_nav(data_dir, row[3], fetched, store, report, it_only)
                elif kind == "market":
                    _store_market(fetched, store, report, it_only)
                else:
                    if fetched["jobs"] is not None:
                        fetched["jobs"], outside = _in_scope(fetched["jobs"], it_only)
                        if outside:
                            fetched["result"]["outside_it"] = len(outside)
                            fetched["result"]["matched"] = len(fetched["jobs"])
                    collection.store_fetched_source(fetched, store, report)
                if progress is not None:
                    progress(f"Checked {label} ({index} of {len(rows)})… You can keep browsing.")
            _save_market_state(data_dir, state)
        finally:
            # After an unexpected failure, stop queued downloads; running ones
            # never touch the store and finish on their own.
            pool.shutdown(wait=False, cancel_futures=True)
        report["removed"] = _cleanup_and_match(data_dir, store, profile, progress, it_only=settings["market"].get("it_only", True))
        report["details_loaded"] = _backfill_details(data_dir, store, settings, progress)
        if report["details_loaded"]:
            _cleanup_and_match(data_dir, store, profile, None, it_only=False)
    report["completed_at"] = datetime.now(timezone.utc).isoformat()
    return report


def save_adzuna_key(data_dir: Path, app_id: str, app_key: str) -> dict:
    """Check an Adzuna key with one search and save it; returns what the UI may show."""
    from . import adzuna
    state = _market_state(Path(data_dir))
    try:
        return adzuna.save_key(Path(data_dir), app_id, app_key, state=state)
    finally:
        _save_market_state(Path(data_dir), state)


def delete_adzuna_key(data_dir: Path) -> dict:
    from . import adzuna
    return adzuna.delete_key(Path(data_dir))


# Stored vacancies in your roles whose site listed them without the advert
# text (or, on Djinni, without the employer and its experience and language
# fields): each refresh loads this many, newest first. The Bundesagentur's
# search lists no text at all, and later refreshes read only new adverts, so
# without this its older matches never got requirements to read.
_BACKFILL = {"arbeitsagentur": 150, "djinni": 60}
_BACKFILL_SECONDS = 150


def _backfill_details(data_dir: Path, store: JobStore, settings: dict, progress=None) -> int:
    import time
    from . import market
    from .sources import SourceError
    enabled = settings.get("market", {}).get("enabled", {})
    state = _market_state(data_dir)
    deadline = time.monotonic() + _BACKFILL_SECONDS
    loaded = 0
    try:
        for source, budget in _BACKFILL.items():
            if not enabled.get(source):
                continue
            rows = store._connection.execute(
                "SELECT j.* FROM jobs j JOIN job_matches m ON m.job_id = j.id "
                "WHERE j.source = ? AND j.is_active = 1 AND m.track IN ('target', 'horizon') "
                "AND (j.description = '' OR j.company = ?) "
                "ORDER BY COALESCE(NULLIF(j.published_at, ''), j.discovered_at) DESC LIMIT ?",
                (source, market.DJINNI_COMPANY, budget * 2)).fetchall()
            done = failures = 0
            for row in rows:
                if done >= budget or failures >= 5 or time.monotonic() > deadline:
                    break
                job = store._job_dict(row)
                if not market.needs_details(job):
                    continue
                try:
                    raw = json.loads(job.get("raw_json") or "{}")
                except ValueError:
                    raw = {}
                raw = raw if isinstance(raw, dict) else {}
                try:
                    details = market.fetch_details({**job, "raw_json": raw}, state)
                except SourceError:
                    failures += 1
                    continue
                update = {key: job[key] for key in ("source", "source_id", "source_url", "title", "company", "employment_type")}
                update["raw_json"] = raw
                store.upsert_job(market.apply_details(update, details))
                done += 1
                failures = 0
                if progress is not None and done % 25 == 0:
                    progress(f"Loading full adverts for your roles from {market.PROVIDERS[source].name}… {done}")
                time.sleep(market.REQUEST_PAUSE)
            loaded += done
    finally:
        _save_market_state(data_dir, state)
    return loaded


def _cleanup_and_match(data_dir: Path, store: JobStore, profile: dict, progress=None, *, it_only: bool = False) -> int:
    """Remove long-closed vacancies, then store matches so lists open instantly.

    With "IT jobs only", stored adverts that are not IT jobs go too, except
    ones you saved, staged, noted, drafted, added or imported.
    """
    from . import experience_matching, job_index
    removed = job_index.cleanup(store)
    if it_only:
        if progress is not None:
            progress("Removing vacancies outside IT…")
        removed += job_index.prune_outside_it(store)
    if progress is not None:
        progress("Matching new vacancies to your profile…")
    experience_matching.attach_cache(Path(data_dir) / ANALYSIS_CACHE_FILE)
    try:
        job_index.ensure_matches(store, profile, progress=progress)
    finally:
        experience_matching.flush_cache()
    _relink_mail(store)  # Newly collected vacancies you already applied to.
    return removed


def market_search(data_dir: Path, query: str, country: str = "", *, progress=None) -> dict:
    """Search every enabled job site for one query now, as a web search would.

    Results are saved like a refresh's, so the local search shows them at
    once. Full adverts load when opened. It never overlaps a refresh.
    """
    from concurrent.futures import ThreadPoolExecutor
    from .countries import COUNTRIES
    query = " ".join(str(query or "").split())
    if not 2 <= len(query) <= 120:
        raise ValueError("Type a role or skill of 2 to 120 characters to search job sites.")
    if country and country not in COUNTRIES:
        raise ValueError("Choose a supported country or all countries.")
    if not _collection_lock.acquire(blocking=False):
        raise ValueError("A refresh is running. Search job sites again when it finishes.")
    try:
        data_dir = Path(data_dir)
        settings = read_settings(data_dir)
        profile = read_profile(data_dir)
        report = {"created": 0, "updated": 0, "withdrawn": 0, "sources": [], "query": query, "country": country}
        nav_enabled = any(source.get("type") == "nav" for source in settings["sources"])
        with JobStore(data_dir / "vacancies.db") as store:
            with ThreadPoolExecutor(max_workers=10, thread_name_prefix="market-search") as pool:
                rows, state = _submit_market(data_dir, pool, settings, profile, country, lambda code: [query],
                                             collection_detail=False, progress=progress)
                if nav_enabled and country in {"", "NO"}:
                    rows.insert(0, ("nav", "NAV", pool.submit(_fetch_nav, data_dir, [query], store.source_ids("nav"), progress,
                                                              it_only=settings["market"].get("it_only", True)),
                                    {"type": "nav"}))
                if progress is not None:
                    progress(f"Searching {len(rows)} job sites for “{query}”…")
                it_only = settings["market"].get("it_only", True)
                for row in rows:
                    fetched = row[2].result()
                    if row[0] == "nav":
                        _store_nav(data_dir, row[3], fetched, store, report, it_only)
                    else:
                        _store_market(fetched, store, report, it_only)
            _save_market_state(data_dir, state)
        report["found"] = sum(row.get("matched") or 0 for row in report["sources"])
        return report
    finally:
        _collection_lock.release()


def load_details(data_dir: Path, id: int) -> dict:
    """Download a board advert's full text once, when its list had none."""
    from . import market
    data_dir = Path(data_dir)
    job = get_vacancy(data_dir, id)
    if not market.needs_details(job):
        return job
    raw = job.get("raw_json")
    try:
        raw = json.loads(raw) if isinstance(raw, str) else (raw or {})
    except ValueError:
        raw = {}
    state = _market_state(data_dir)
    try:
        details = market.fetch_details({**job, "raw_json": raw}, state)
    finally:
        _save_market_state(data_dir, state)
    update = {key: job[key] for key in ("source", "source_id", "source_url", "title", "company", "employment_type")}
    update["raw_json"] = raw if isinstance(raw, dict) else {}
    with JobStore(data_dir / "vacancies.db") as store:
        store.upsert_job(market.apply_details(update, details))
    return get_vacancy(data_dir, id)


from .job_index import BOARD_SOURCES, RETENTION_DAYS as STALE_BOARD_DAYS  # noqa: E402,F401  (re-exported)


# Matches a list request may compute itself before answering (about a second).
LIST_MATCH_LIMIT = 1500


def _open_index(data_dir: Path, *, progress=None):
    """A store whose stored matches are current for the saved profile."""
    from . import experience_matching, job_index
    profile = read_profile(data_dir)
    experience_matching.attach_cache(Path(data_dir) / ANALYSIS_CACHE_FILE)
    store = JobStore(Path(data_dir) / "vacancies.db")
    try:
        # Bounded and non-blocking: a refresh or re-match stores the rest.
        computed = job_index.ensure_matches(store, profile, progress=progress, limit=LIST_MATCH_LIMIT, wait=False)
    except BaseException:
        store.close()
        raise
    finally:
        experience_matching.flush_cache()
    if computed >= LIST_MATCH_LIMIT:
        _rematch_in_background(Path(data_dir))
    return store


_background_rematches: set[str] = set()
_background_guard = threading.Lock()


def _rematch_in_background(data_dir: Path) -> None:
    """Finish a large re-match (an upgrade, or a profile changed outside the app) once."""
    key = str(Path(data_dir).resolve())
    with _background_guard:
        if key in _background_rematches:
            return
        _background_rematches.add(key)

    def run():
        try:
            refresh_matches(data_dir)
        except Exception:
            import logging
            logging.getLogger(__name__).exception("Background re-match failed")
        finally:
            with _background_guard:
                _background_rematches.discard(key)
    threading.Thread(target=run, name="rematch", daemon=True).start()


def refresh_matches(data_dir: Path, *, progress=None) -> dict:
    """Bring every stored match up to date, for example after a profile change."""
    from . import experience_matching, job_index
    profile = read_profile(data_dir)
    experience_matching.attach_cache(Path(data_dir) / ANALYSIS_CACHE_FILE)
    try:
        with JobStore(Path(data_dir) / "vacancies.db") as store:
            return {"matched": job_index.ensure_matches(store, profile, progress=progress)}
    finally:
        experience_matching.flush_cache()


def _scoped_rows(store: JobStore, *, query: str, status: str, country: str, include_inactive: bool) -> list[dict]:
    from . import job_index
    from .countries import COUNTRIES
    if country and country not in {*COUNTRIES, "unknown"}:
        raise ValueError("Choose a supported country or unknown.")
    return job_index.select_rows(store, query=query, status=status, country=country, include_inactive=include_inactive)


def vacancies(data_dir: Path, query: str = "", status: str = "", country: str = "", *, include_inactive: bool = False,
              recommendations_only: bool = False, coverage: dict | None = None,
              stages: set[str] | frozenset[str] | None = None) -> list[dict]:
    """Return matched vacancies with full match evidence, best first.

    stages limits which workflow stages are returned; jobs in other stages
    still count toward coverage. recommendations_only returns target and
    related roles only. The interface pages through list_page instead; this
    complete list serves the classic window and the command line.
    """
    from . import job_index
    with _open_index(data_dir) as store:
        rows = _scoped_rows(store, query=query, status=status, country=country, include_inactive=include_inactive)
        if coverage is not None:
            coverage.update(in_scope=len(rows), employers=len({row["company"] for row in rows}))
        if stages is not None:
            rows = [row for row in rows if row["status"] in stages]
        if recommendations_only:
            rows = [row for row in rows if row["track"] in {"target", "horizon"}]
        if query:
            job_index.relevance_sort(rows, query)
        rows = job_index.sort_rows(rows)
        return job_index.load_full(store, [row["id"] for row in rows])


LIST_VIEWS = {"discover", "saved", "applications", "for-you"}


EXPERIENCE_GROUPS = ("fits", "stretch", "above")
_LEGACY_EXPERIENCE = {"all": set(), "relevant": {"fits", "stretch"}, "mismatch": {"above"}}
_REMOTE = re.compile(r"remote|віддален|удален|fjern|home\s*office|homeoffice", re.IGNORECASE)


def _experience_groups(experience: str | None, view: str) -> set[str]:
    """Experience filter: "fits", "stretch" and/or "above"; empty means every level."""
    value = experience or ("relevant" if view == "for-you" else "all")
    if value in _LEGACY_EXPERIENCE:
        return set(_LEGACY_EXPERIENCE[value])
    groups = {part for part in value.split(",") if part}
    if not groups or not groups <= set(EXPERIENCE_GROUPS):
        raise ValueError("Choose experience levels: fits, stretch or above.")
    return set() if groups == set(EXPERIENCE_GROUPS) else groups


def _experience_group(row: dict) -> str:
    return "above" if row["mismatch"] else "stretch" if row.get("experience_status") == "stretch" else "fits"


def list_page(data_dir: Path, *, view: str = "discover", query: str = "", status: str = "", country: str = "",
              experience: str | None = None, sort: str = "match", offset: int = 0, limit: int = 80,
              posted: int = 0, min_score: int = 0, remote: bool = False, progress=None) -> dict:
    """One page of a vacancy view plus the counts the interface shows.

    Filtering and counting use stored matches only; full records and match
    evidence are loaded for the returned page alone. status may name several
    stages ("applied,rejected"); experience several levels ("fits,stretch");
    posted keeps adverts from the last N days; min_score a lowest match %.
    """
    from datetime import date, timedelta
    from . import job_index
    if view not in LIST_VIEWS:
        raise ValueError("Choose a vacancy view.")
    groups = _experience_groups(experience, view)
    if sort not in job_index.SORTS:
        raise ValueError("Choose a supported sort order.")
    for value, top in ((posted, 3650), (min_score, 100)):
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= top:
            raise ValueError("Choose a posting age of 0 to 3650 days and a match score from 0 to 100.")
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0 or isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
        raise ValueError("Choose a page offset of 0 or more and a page size from 1 to 500.")
    stages = None
    if view == "saved":
        stages = {"saved"}
    elif view == "applications":
        stages = {"preparing", "ready", "applied", "interview", "rejected"} | (
            {"archived"} if "archived" in status.split(",") else set())
    with _open_index(data_dir, progress=progress) as store:
        rows = _scoped_rows(store, query=query, status=status, country=country,
                            include_inactive=view in {"saved", "applications"})
        summary = {"in_scope": len(rows), "employers": len({row["company"] for row in rows})}
        if stages is not None:
            rows = [row for row in rows if row["status"] in stages]
        if view == "for-you":
            rows = [row for row in rows if row["track"] in {"target", "horizon"} and not row["excluded"] and not row["expired"]]
        summary["role_matches"] = sum(row["track"] in {"target", "horizon"} for row in rows)
        summary["experience_mismatches"] = sum(bool(row["mismatch"]) for row in rows)
        if groups:
            rows = [row for row in rows if _experience_group(row) in groups]
        if posted:
            cutoff = (date.today() - timedelta(days=posted)).isoformat()
            rows = [row for row in rows if str(row.get("published_at") or row.get("discovered_at") or "")[:10] >= cutoff]
        if min_score:
            rows = [row for row in rows if row["score"] is not None and row["score"] >= min_score]
        if remote:
            rows = [row for row in rows if _REMOTE.search(str(row.get("location") or ""))]
        summary["shown"] = len(rows)
        summary["shown_employers"] = len({row["company"] for row in rows})
        if query and sort == "match":
            job_index.relevance_sort(rows, query)
        rows = job_index.sort_rows(rows, sort, local_first=bool(country) and country != "unknown")
        if view == "for-you" and sort == "match" and not query:
            rows = job_index.diversify(rows)
        page = job_index.load_full(store, [row["id"] for row in rows[offset:offset + limit]])
        mail = store.email_for_jobs([job["id"] for job in page])
        for job in page:
            if job["id"] in mail:
                job["email"] = _email_outcome(mail[job["id"]])
    summary.update(offset=offset, limit=limit, has_more=offset + limit < len(rows))
    return {"jobs": page, "summary": summary}


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
    # Old reports lack country metadata: don't present them as this market's
    # run. A refresh of all countries ("") covers every country view.
    current_report = report if report and report.get("country") in {"", country} and (
        not country or country in report.get("countries", [country])) else None
    checked = current_report.get("sources", []) if current_report else []
    from . import market, nav_index
    enabled = settings["market"]["enabled"]
    from .adzuna import read_key
    keyed = read_key(data_dir) is not None
    sites = [{"id": name, "name": provider.name, "enabled": bool(enabled.get(name)) and (name != "adzuna" or keyed)}
             for name, provider in market.PROVIDERS.items() if not country or country in provider.countries]
    nav_on = any(source.get("type") == "nav" for source in settings["sources"]) and country in {"", "NO"}
    return {"country": country, "job_sites": sites, "job_sites_enabled": sum(site["enabled"] for site in sites) + nav_on,
            "nav_index": nav_index.counts(data_dir) if nav_on else None,
            "searches": len(market.search_queries(read_profile(data_dir), settings["market"], country)),
            "directory_employers": len(catalog),
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
    from .collection import validate_job
    with JobStore(Path(data_dir) / "vacancies.db") as store:
        return store.upsert_job(validate_job(job))


def update_stage(data_dir: Path, id: int, status: str) -> None:
    """Change only the stage (a quick mark in the list); notes stay as they are."""
    with JobStore(Path(data_dir) / "vacancies.db") as store:
        store.update_status(id, status)


def update_workflow(data_dir: Path, id: int, status: str, notes: str) -> None:
    with JobStore(Path(data_dir) / "vacancies.db") as store:
        with store.transaction():
            store.update_status(id, status)
            store.update_notes(id, notes)


def import_email_candidates(data_dir: Path, candidates: list[dict]) -> dict:
    """Persist only the email opportunities explicitly selected in the review UI."""
    from .collection import validate_job
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


def _prepare_local_ai(progress=None) -> None:
    from .runtime_setup import ensure_ollama, memory_available_gb
    ensure_ollama(progress)
    free = memory_available_gb()
    if progress is not None and free is not None and free < 2:
        progress(f"Only {free:.1f} GB of memory is free, so this may take several minutes. Closing other programs speeds it up.")


def _writer(data_dir: Path, progress=None):
    """The AI you chose in Settings: your local model's name, or a cloud engine."""
    from . import cloud_ai
    engine = cloud_ai.engine(data_dir)
    if engine is not None:
        return engine
    _prepare_local_ai(progress)
    return read_settings(data_dir)["model"]


def _label(model) -> str:
    return model.label if getattr(model, "is_cloud", False) else model


def _with_speed_advice(result: dict) -> dict:
    from .local_ai import last_tokens_per_second
    from .runtime_setup import memory_available_gb, speed_advice
    advice = speed_advice(last_tokens_per_second(), memory_available_gb())
    if advice:
        result["performance_note"] = advice
    return result


def suggest_profile(data_dir: Path, cv_text: str, *, progress=None) -> dict:
    from .local_ai import extract_profile
    return _with_speed_advice(extract_profile(cv_text, model=_writer(data_dir, progress), progress=progress))


def write_letter(data_dir: Path, id: int, *, persist: bool = True, progress=None) -> dict:
    from .local_ai import generate_cover_letter
    job = get_vacancy(data_dir, id)
    if not job.get("is_active", True):
        raise ValueError("This vacancy has been withdrawn. Select an active opportunity.")
    from .countries import profile_for_job
    profile = profile_for_job(read_profile(data_dir), job)
    model = _writer(data_dir, progress)
    result = _with_speed_advice(generate_cover_letter(job, profile, model=model, progress=progress))
    if persist:
        with JobStore(Path(data_dir) / "vacancies.db") as store:
            # A generation request never changes the application status.
            result["id"] = store.save_draft(id, result["cover_letter"], model=_label(model), profile_hash=profile_hash(profile))
    else:
        result["draft_metadata"] = {"model": _label(model), "profile_hash": profile_hash(profile)}
    return result


def save_edited_letter(data_dir: Path, id: int, content: str) -> int:
    with JobStore(Path(data_dir) / "vacancies.db") as store:
        return store.save_draft(id, content, model="user-edited", profile_hash=profile_hash(read_profile(data_dir)))


def draft_history(data_dir: Path, id: int) -> list[dict]:
    with JobStore(Path(data_dir) / "vacancies.db") as store:
        return store.list_drafts(id)


def available_models() -> list[str]:
    from .local_ai import list_local_models
    _prepare_local_ai()
    return list_local_models()


def _job_and_profile(data_dir: Path, id: int) -> tuple[dict, dict]:
    from .countries import profile_for_job
    job = get_vacancy(data_dir, id)
    return job, profile_for_job(read_profile(data_dir), job)


def analyze_job(data_dir: Path, id: int, *, progress=None) -> dict:
    """Fit, gaps, red flags and key facts for one vacancy; the latest is kept."""
    from .assistant import analyze_vacancy
    job, profile = _job_and_profile(data_dir, id)
    model = _writer(data_dir, progress)
    result = analyze_vacancy(job, profile, model, progress=progress)
    with JobStore(Path(data_dir) / "vacancies.db") as store:
        store.save_insight(id, "analysis", result, model=_label(model))
        return store.insights(id)


def prepare_interview(data_dir: Path, id: int, *, progress=None) -> dict:
    from .assistant import interview_prep
    job, profile = _job_and_profile(data_dir, id)
    model = _writer(data_dir, progress)
    result = interview_prep(job, profile, model, progress=progress)
    with JobStore(Path(data_dir) / "vacancies.db") as store:
        store.save_insight(id, "interview", result, model=_label(model))
        return store.insights(id)


def job_insights(data_dir: Path, id: int) -> dict:
    with JobStore(Path(data_dir) / "vacancies.db") as store:
        return store.insights(id)


def draft_reply(data_dir: Path, email_text: str, *, goal: str = "", job_id: int | None = None,
                language: str = "", progress=None) -> dict:
    """A reply to an employer's email for you to edit and send; nothing is sent."""
    from .assistant import reply_draft
    if job_id is not None:
        job, profile = _job_and_profile(data_dir, job_id)
    else:
        job, profile = None, read_profile(data_dir)
    model = _writer(data_dir, progress)
    return {**reply_draft(email_text, profile, model, goal=goal, job=job, language=language, progress=progress),
            "model": _label(model)}


def list_chats(data_dir: Path) -> list[dict]:
    with JobStore(Path(data_dir) / "vacancies.db") as store:
        return store.chats()


def get_chat(data_dir: Path, chat_id: int) -> dict:
    with JobStore(Path(data_dir) / "vacancies.db") as store:
        chat = store.chat(chat_id)
    if chat is None:
        raise ValueError("This chat no longer exists.")
    return chat


def update_chat(data_dir: Path, chat_id: int, changes: dict) -> dict:
    get_chat(data_dir, chat_id)
    with JobStore(Path(data_dir) / "vacancies.db") as store:
        try:
            store.update_chat(chat_id, **changes)
        except KeyError:
            raise ValueError("That vacancy no longer exists.") from None
        return store.chat(chat_id)


def delete_chat(data_dir: Path, chat_id: int) -> bool:
    with JobStore(Path(data_dir) / "vacancies.db") as store:
        return store.delete_chat(chat_id)


def send_chat(data_dir: Path, message: str, *, chat_id: int | None = None, job_id: int | None = None,
              use_profile: bool = True, on_text=None, progress=None) -> dict:
    """Ask the chosen AI; the exchange is saved only once the answer is complete."""
    from .assistant import chat_context, chat_reply, chat_title
    chat = get_chat(data_dir, chat_id) if chat_id is not None else None
    if chat is not None:
        job_id, use_profile = chat["job_id"], chat["use_profile"]
    if job_id is not None:
        job, profile = _job_and_profile(data_dir, job_id)
    else:
        job, profile = None, read_profile(data_dir)
    model = _writer(data_dir, progress)
    answer = chat_reply(model, chat_context(profile if use_profile else None, job), chat["messages"] if chat else [],
                        message, on_text=on_text, progress=progress)
    with JobStore(Path(data_dir) / "vacancies.db") as store:
        if chat is None:
            chat_id = store.create_chat(chat_title(message), job_id, use_profile)
        store.add_chat_messages(chat_id, [("user", message.strip(), ""), ("assistant", answer, _label(model))])
        return store.chat(chat_id)


def _relink_mail(store: JobStore) -> None:
    """Match application mail to your vacancies (company and position)."""
    from . import mail_applications
    records = store.email_records()
    if not records:
        return
    jobs = [dict(row) for row in store._connection.execute("SELECT id, title, company, status FROM jobs")]
    store.set_email_links(mail_applications.link(records, jobs))


def _email_outcome(messages: list[dict]) -> dict:
    """The latest news among a vacancy's application mail."""
    outcome = messages[0]
    for message in messages[1:]:
        if message["kind"] != "applied" or outcome["kind"] == "applied":
            outcome = message
    return {"kind": outcome["kind"], "received_at": outcome["received_at"], "applied_at": messages[0]["received_at"]}


def mail_overview(data_dir: Path) -> dict:
    """Applications found in your mail: counts, months, stage suggestions."""
    from . import mail_applications
    with JobStore(Path(data_dir) / "vacancies.db") as store:
        records = store.email_records()
        overview = mail_applications.summary(records)
        ids = sorted({group["job_id"] for group in overview["groups"] if group["job_id"]})
        jobs = {job["id"]: job for job in (store.get_job(identifier) for identifier in ids) if job}
    suggestions = []
    for group in overview["groups"]:
        job = jobs.get(group["job_id"])
        if job is None:
            group["job_id"] = None
            continue
        group["job_title"], group["job_status"] = job["title"], job["status"]
        stage = mail_applications.stage_suggestion(job["status"], group["outcome"])
        if stage and not any(item["job_id"] == job["id"] for item in suggestions):
            suggestions.append({"job_id": job["id"], "title": job["title"], "company": job["company"],
                                "current": job["status"], "suggested": stage, "date": group["last_at"]})
    unclear = sum(1 for record in records if not record["company"] or not record["position"])
    return {**overview, "suggestions": suggestions, "unclear": unclear, "messages": len(records)}


def scan_mail(data_dir: Path, *, days: int = 365, progress=None) -> dict:
    """Read new application mail from Gmail (read-only) and match it to your vacancies."""
    from . import mail_applications
    with JobStore(Path(data_dir) / "vacancies.db") as store:
        known = store.email_known_ids()
    result = mail_applications.scan(data_dir, days=days, known=known, progress=progress)
    with JobStore(Path(data_dir) / "vacancies.db") as store:
        store.save_email_records(result["records"], result["ignored"])
        _relink_mail(store)
    return {**mail_overview(data_dir), "scan": {key: result[key] for key in ("listed", "read", "more", "warnings")} | {"found": len(result["records"])}}


def ai_read_mail(data_dir: Path, *, progress=None) -> dict:
    """Let the chosen AI read application mail whose company or position the rules missed."""
    from . import mail_applications
    with JobStore(Path(data_dir) / "vacancies.db") as store:
        unclear = [record for record in store.email_records() if not record["company"] or not record["position"]][:60]
    if not unclear:
        return mail_overview(data_dir)
    changes = mail_applications.ai_read(unclear, _writer(data_dir, progress), progress=progress)
    with JobStore(Path(data_dir) / "vacancies.db") as store:
        for message_id, change in changes.items():
            store.update_email_record(message_id, **change, source="ai")
        _relink_mail(store)
    return mail_overview(data_dir)


def apply_mail_stages(data_dir: Path, items: list[dict]) -> dict:
    """Update the stages you ticked; each must still be what your mail suggests."""
    if not isinstance(items, list) or len(items) > 1000:
        raise ValueError("Choose the stages to update.")
    suggested = {item["job_id"]: item["suggested"] for item in mail_overview(data_dir)["suggestions"]}
    updated = 0
    for item in items:
        if not isinstance(item, dict) or suggested.get(item.get("job_id")) != item.get("status"):
            raise ValueError("Your mail no longer suggests one of these stages. Reload the page and try again.")
    for item in items:
        update_stage(data_dir, item["job_id"], item["status"])
        updated += 1
    return {**mail_overview(data_dir), "updated": updated}


def add_mail_application(data_dir: Path, message_id: str) -> dict:
    """Add an application found only in your mail to Applications, at its stage."""
    from . import mail_applications
    with JobStore(Path(data_dir) / "vacancies.db") as store:
        records = store.email_records()
    group = next((group for group in mail_applications.applications(records) if message_id in group["message_ids"]), None)
    if group is None:
        raise ValueError("This application is no longer in your mail results.")
    if group["job_id"]:
        return {"id": group["job_id"], **mail_overview(data_dir)}
    first = next(record for record in records if record["message_id"] == group["message_ids"][0])
    thread = first["thread_id"] or first["message_id"]
    identifier, _ = import_vacancy(data_dir, {
        "source": "gmail", "source_id": "application:" + thread,
        "source_url": "https://mail.google.com/mail/u/0/#all/" + thread,
        "title": group["position"] or "Application", "company": group["company"] or "Employer not named",
        "description": f"Added from your email: {first['subject']}\n\n{first['excerpt'][:1200]}",
        "published_at": group["applied_at"],
        "raw_json": {"email_application": {"message_ids": group["message_ids"], "outcome": group["outcome"]}}})
    update_stage(data_dir, identifier, mail_applications.STAGE_FOR[group["outcome"]])
    with JobStore(Path(data_dir) / "vacancies.db") as store:
        store.set_email_links({message: (identifier, "position") for message in group["message_ids"]})
    return {"id": identifier, **mail_overview(data_dir)}


def ai_read_alerts(data_dir: Path, message_ids: list[str], *, progress=None) -> dict:
    """Vacancies in job-alert emails the rules could not read, found by the chosen AI."""
    from . import gmail
    candidates, warnings = [], []
    model = _writer(data_dir, progress)
    for number, message_id in enumerate(message_ids, 1):
        if progress is not None:
            progress(f"Reading email {number} of {len(message_ids)} with AI…")
        try:
            candidates += gmail.ai_candidates(data_dir, message_id, model, progress=progress)
        except gmail.GmailError as exc:
            if str(exc) not in warnings:
                warnings.append(str(exc))
    unique = {}
    for job in candidates:
        unique.setdefault(job["source_url"], job)
    return {"candidates": list(unique.values()), "warnings": warnings}


def forget_mail(data_dir: Path) -> dict:
    with JobStore(Path(data_dir) / "vacancies.db") as store:
        store.clear_email_records()
    return mail_overview(data_dir)


def mail_history(data_dir: Path, job: dict) -> list[dict]:
    """Application mail about this vacancy, or earlier applications to its employer."""
    from .mail_applications import company_key, same_company
    with JobStore(Path(data_dir) / "vacancies.db") as store:
        records = store.email_records()
    key = company_key(job.get("company"))
    found = [{**{name: record[name] for name in ("kind", "received_at", "company", "position", "subject")},
              "this_job": record["job_id"] == job["id"]}
             for record in records if record["job_id"] == job["id"] or (key and same_company(key, company_key(record["company"])))]
    return sorted(found, key=lambda item: item["received_at"], reverse=True)[:12]


def discover_application(data_dir: Path, id: int, manual_text: str = "") -> dict:
    from .application_forms import discover_application_form, parse_manual_questions
    job = get_vacancy(data_dir, id)
    if not job.get("is_active", True):
        raise ValueError("This vacancy has been withdrawn.")
    form = parse_manual_questions(manual_text) if manual_text.strip() else discover_application_form(job)
    return {"form": form, "answers": [], "review_notes": [], "job_id": id}


def prepare_application(data_dir: Path, id: int, form: dict, *, persist: bool = True, progress=None) -> dict:
    from .application_answers import generate_application_answers
    from .countries import profile_for_job
    job = get_vacancy(data_dir, id)
    if not job.get("is_active", True):
        raise ValueError("This vacancy has been withdrawn.")
    profile = profile_for_job(read_profile(data_dir), job)
    result = _with_speed_advice(generate_application_answers(job, profile, form, writing_style=profile.get("writing_style"),
                                                             model=_writer(data_dir, progress), progress=progress))
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
