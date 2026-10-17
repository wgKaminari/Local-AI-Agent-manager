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


MARKET_STATE_FILE = "market-state.json"
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


def _fetch_nav(data_dir: Path, queries: list[str], known_ids: list[str], progress=None) -> dict:
    """Bring the NAV index up to date and download adverts answering the queries.

    Network and the index cache only; the job database is written afterwards
    by _store_nav, which also records which adverts were checked.
    """
    from . import nav_index
    try:
        if not queries:
            raise ValueError("Add target roles in your profile, or a saved search, before collecting NAV vacancies.")
        synced = nav_index.sync(data_dir, progress=progress)
        candidates = nav_index.find(data_dir, queries, known=set(known_ids))
        if progress is not None and candidates:
            progress(f"NAV: {len(candidates)} matching adverts to load…")
        loaded = nav_index.hydrate(data_dir, candidates, progress=progress)
        return {"sync": synced, "candidates": len(candidates), **loaded,
                "pending_withdrawals": nav_index.pending_withdrawals(data_dir)}
    except (ValueError, RuntimeError, OSError) as exc:
        return {"error": str(exc)}


def _store_nav(data_dir: Path, source: dict, fetched: dict, store: JobStore, report: dict) -> None:
    from . import nav_index
    result = {"source": source, "created": 0, "updated": 0, "withdrawn": 0}
    try:
        if "error" in fetched:
            raise ValueError(fetched["error"])
        counts = {"created": 0, "updated": 0, "withdrawn": 0}
        skipped = 0
        withdrawals = list(dict.fromkeys([*fetched["withdrawn"], *fetched["pending_withdrawals"]]))
        with store.transaction():
            for job in fetched["jobs"]:
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
        nav_index.mark_inactive(data_dir, fetched["withdrawn"])
        nav_index.mark_withdrawals_applied(data_dir, fetched["pending_withdrawals"])
        for key, count in counts.items():
            result[key] = count
            report[key] += count
        synced = fetched["sync"]
        more = synced["has_more"] or fetched["remaining"] > 0
        result.update(ok=True, has_more=more, pages=synced["pages"], seen=synced["events"],
                      experimental_token=synced["experimental_token"], fetched=synced["active"],
                      scanned=synced["active"], available=synced["active"], matched=len(fetched["jobs"]))
        coverage = (f"NAV index: {synced['active']:,} active Norwegian vacancies (read {synced['pages']} feed pages this run). "
                    f"{fetched['candidates']} titles matched your searches; {len(fetched['jobs'])} confirmed and saved.")
        if synced["has_more"]:
            coverage += " The feed has more updates; the next refresh continues reading."
        if fetched["remaining"]:
            coverage += f" {fetched['remaining']} more matching adverts load on the next refresh."
        if skipped:
            coverage += f" {skipped} adverts conflicted with saved records and were left unchanged."
        result["coverage"] = coverage
    except (ValueError, RuntimeError, OSError) as exc:
        result.update(ok=False, error=str(exc))
    report["sources"].append(result)


def _store_market(run, store: JobStore, report: dict) -> dict:
    """Write one provider's results; a conflicting advert skips only itself."""
    result = run.report()
    counts = {"created": 0, "updated": 0}
    skipped = 0
    try:
        with store.transaction():
            for job in run.jobs:
                try:
                    _, created = store.upsert_job(job)
                except ValueError:
                    skipped += 1
                    continue
                counts["created" if created else "updated"] += 1
    except (ValueError, OSError) as exc:
        result.update(ok=False, error=str(exc))
        counts = {"created": 0, "updated": 0}
    for key, count in counts.items():
        result[key] = count
        report[key] = report.get(key, 0) + count
    result["withdrawn"] = 0
    if skipped:
        result["coverage"] += f" {skipped} conflicted with saved records and were left unchanged."
    report["sources"].append(result)
    return result


def _submit_market(data_dir: Path, pool, settings: dict, profile: dict, country: str, queries_for,
                   *, collection_detail: bool, progress=None) -> tuple[list, dict]:
    """Start market-wide searches; returns (kind, label, future) rows and their shared state."""
    from . import market
    rows = []
    state = _market_state(data_dir)
    for name in market.providers_for(country, settings["market"]["enabled"]):
        budget = _DETAIL_BUDGET.get(name, 0) if collection_detail else 0
        rows.append(("market", market.PROVIDERS[name].name, pool.submit(
            market.search_provider, name, queries_for(country), country, role_terms=_role_terms_for(profile, country),
            detail_budget=budget, state=state, progress=progress)))
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
    country = settings.get("active_country", "")
    report = {"created": 0, "updated": 0, "withdrawn": 0, "sources": [],
              "country": country, "started_at": datetime.now(timezone.utc).isoformat()}
    selected = _collection_scope(settings)
    queries_for = lambda code: search_queries(profile, settings["market"], code)
    with JobStore(data_dir / "vacancies.db") as store:
        pool = ThreadPoolExecutor(max_workers=COLLECTION_WORKERS + 4, thread_name_prefix="collect")
        try:
            # Many-request sources start first so they overlap the employer boards.
            rows, state = _submit_market(data_dir, pool, settings, profile, country, queries_for,
                                         collection_detail=True, progress=progress)
            for source in selected:
                if source.get("type") == "nav":
                    rows.insert(0, ("nav", "NAV", pool.submit(_fetch_nav, data_dir, queries_for("NO"),
                                                              store.source_ids("nav"), progress), source))
                else:
                    label = source.get("name") or source.get("board") or source.get("url") or source.get("type", "source").upper()
                    rows.append(("employer", label, pool.submit(collection.fetch_source, source)))
            if progress is not None and rows:
                sites = sum(row[0] != "employer" for row in rows)
                progress(f"Checking {len(rows)} source{'' if len(rows) == 1 else 's'}"
                         + (f", including {sites} whole-market job site{'' if sites == 1 else 's'}" if sites else "")
                         + "… You can keep browsing.")
            for index, row in enumerate(rows, 1):
                kind, label, future = row[:3]
                fetched = future.result()
                if kind == "nav":
                    _store_nav(data_dir, row[3], fetched, store, report)
                elif kind == "market":
                    _store_market(fetched, store, report)
                else:
                    collection.store_fetched_source(fetched, store, report)
                if progress is not None:
                    progress(f"Checked {label} ({index} of {len(rows)})… You can keep browsing.")
            _save_market_state(data_dir, state)
        finally:
            # After an unexpected failure, stop queued downloads; running ones
            # never touch the store and finish on their own.
            pool.shutdown(wait=False, cancel_futures=True)
    report["completed_at"] = datetime.now(timezone.utc).isoformat()
    return report


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
                    rows.insert(0, ("nav", "NAV", pool.submit(_fetch_nav, data_dir, [query], store.source_ids("nav"), progress),
                                    {"type": "nav"}))
                if progress is not None:
                    progress(f"Searching {len(rows)} job sites for “{query}”…")
                for row in rows:
                    fetched = row[2].result()
                    if row[0] == "nav":
                        _store_nav(data_dir, row[3], fetched, store, report)
                    else:
                        _store_market(fetched, store, report)
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


BOARD_SOURCES = frozenset({"arbeitsagentur", "arbeitnow", "djinni", "dou", "remotive", "remoteok", "himalayas", "linkedin"})
# Boards publish no complete snapshot, so a closed advert just stops appearing.
STALE_BOARD_DAYS = 30
# Which copy of a job seen on several sites to show: the employer's own first.
_SOURCE_RANK = {"greenhouse": 0, "lever": 0, "ashby": 0, "workday": 0, "smartrecruiters": 0, "career_page": 0,
                "manual": 0, "nav": 1, "arbeitsagentur": 1, "djinni": 2, "dou": 2, "arbeitnow": 2, "remotive": 2,
                "remoteok": 2, "himalayas": 2, "gmail": 3, "linkedin": 3}
_GENDER_MARKERS = re.compile(r"\((?:[^()]*\b(?:m|w|d|f|x|gn|all genders?|div)\b[^()]*)\)|\*in\b|\b[mwfd]\s*/\s*[mwfd](?:\s*/\s*[mwfdx])?\b", re.I)
_LEGAL_SUFFIXES = re.compile(r"\b(?:gmbh|ag|as|asa|inc|llc|ltd|limited|corp|corporation|se|plc|co|kg|ab|oy|sa|bv|nv|ug|sp z o o|tov|тов)\b\.?", re.I)


def _stale_board_advert(job: dict) -> bool:
    if job.get("source") not in BOARD_SOURCES or job.get("status") != "new":
        return False
    from datetime import datetime, timedelta, timezone
    try:
        seen = datetime.fromisoformat(str(job.get("last_seen_at") or ""))
    except ValueError:
        return False
    return seen < datetime.now(timezone.utc) - timedelta(days=STALE_BOARD_DAYS)


def job_fingerprint(job: dict) -> tuple | None:
    """Title, employer and first city with formatting differences removed."""
    title = " ".join(re.findall(r"\w+", _GENDER_MARKERS.sub(" ", str(job.get("title") or "")).casefold()))
    company = " ".join(re.findall(r"\w+", _LEGAL_SUFFIXES.sub(" ", str(job.get("company") or "")).casefold()))
    location = re.split(r"[;,/|(\n]", str(job.get("location") or "").casefold())[0]
    city = " ".join(re.findall(r"\w+", location)[:2])
    if not title or not company or company.startswith("company on"):
        return None
    return title, company, city


def merge_duplicates(jobs: list[dict]) -> list[dict]:
    """Show one card per job that several sites list; keep the others as links.

    Display only: records stay separate, so a site's refresh never overwrites
    another's advert. Only copies from different sites merge; two postings
    on one site are genuinely separate openings. The shown copy prefers your
    workflow stage, then the employer's own posting, then the fuller advert.
    """
    groups: dict[tuple, list[dict]] = {}
    order = []
    for job in jobs:
        key = job_fingerprint(job)
        if key is None:
            order.append([job])
            continue
        if key not in groups:
            groups[key] = []
            order.append(groups[key])
        groups[key].append(job)
    result = []
    for group in order:
        if len(group) == 1:
            result.append(group[0])
            continue
        group.sort(key=lambda job: (job.get("status") == "new", _SOURCE_RANK.get(job.get("source"), 2),
                                    not str(job.get("description") or "").strip()))
        cards: list[tuple[dict, set]] = []
        for job in group:
            card = next((card for card in cards if job.get("source") not in card[1]), None)
            if card is None:
                cards.append((job, {job.get("source")}))
                continue
            card[1].add(job.get("source"))
            card[0].setdefault("also_on", []).append(
                {"source": job.get("source"), "id": job.get("id"), "url": job.get("source_url")})
        result.extend(card for card, _ in cards)
    return result


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
    from .profile import _role_terms
    # "ML Engineer" also finds "Machine Learning Engineer" and other spellings.
    from .market import LAZY_DETAILS, query_tokens, specific_tokens
    alternatives = [term for term in _role_terms(query)][1:] if query and len(query) <= 120 else []
    with JobStore(Path(data_dir) / "vacancies.db") as store:
        jobs = store.list_jobs(status=status or None, query=query or None, include_inactive=include_inactive,
                               alternatives=alternatives, title_only_sources=tuple(sorted(LAZY_DETAILS - {"djinni"})),
                               title_terms=specific_tokens(query) if query and len(query) <= 120 else [])
    if not include_inactive:
        jobs = [job for job in jobs if not _stale_board_advert(job)]
    jobs = merge_duplicates(jobs)
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
    if query:
        # A search ranks titles naming what was searched above adverts that
        # merely mention it, as a web search would.
        from .profile import _role_text, contains_phrase
        tokens, phrases = query_tokens(query), _role_terms(query)

        def relevance(job):
            title = _role_text(str(job.get("title", "")))
            if any(contains_phrase(title, phrase) for phrase in phrases):
                return 3
            hits = sum(contains_phrase(title, token) for token in tokens)
            return 2 if tokens and hits == len(tokens) else 1 if hits else 0
        for job in visible:
            job["_relevance"] = relevance(job)
    visible.sort(key=lambda job: (
        job.get("_relevance", 0),
        job["match"].get("experience", {}).get("status") != "mismatch",
        not bool(job["match"].get("excluded_terms") or job["match"].get("expired")),
        rank[job["match"]["track"]],
        job["match"]["score"] if job["match"]["score"] is not None else -1), reverse=True)
    for job in visible:
        job.pop("_relevance", None)
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
    from . import market, nav_index
    enabled = settings["market"]["enabled"]
    sites = [{"id": name, "name": provider.name, "enabled": bool(enabled.get(name))}
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


def _with_speed_advice(result: dict) -> dict:
    from .local_ai import last_tokens_per_second
    from .runtime_setup import memory_available_gb, speed_advice
    advice = speed_advice(last_tokens_per_second(), memory_available_gb())
    if advice:
        result["performance_note"] = advice
    return result


def suggest_profile(data_dir: Path, cv_text: str, *, progress=None) -> dict:
    from .local_ai import extract_profile
    _prepare_local_ai(progress)
    return _with_speed_advice(extract_profile(cv_text, model=read_settings(data_dir)["model"], progress=progress))


def write_letter(data_dir: Path, id: int, *, persist: bool = True, progress=None) -> dict:
    from .local_ai import generate_cover_letter
    job = get_vacancy(data_dir, id)
    if not job.get("is_active", True):
        raise ValueError("This vacancy has been withdrawn. Select an active opportunity.")
    from .countries import profile_for_job
    profile = profile_for_job(read_profile(data_dir), job)
    model = read_settings(data_dir)["model"]
    _prepare_local_ai(progress)
    result = _with_speed_advice(generate_cover_letter(job, profile, model=model, progress=progress))
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
    _prepare_local_ai()
    return list_local_models()


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
    _prepare_local_ai(progress)
    result = _with_speed_advice(generate_application_answers(job, profile, form, writing_style=profile.get("writing_style"),
                                                             model=read_settings(data_dir)["model"], progress=progress))
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
