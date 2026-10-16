"""Collect vacancies from configured sources into the job database.

Downloads (fetch_source) never touch the database, so callers may run them
concurrently; each source is then written as one transaction by
store_fetched_source on the thread that owns the store.
"""

from __future__ import annotations

from .storage import JobStore


def validate_job(job: dict) -> dict:
    from urllib.parse import urlsplit

    if not isinstance(job, dict):
        raise ValueError("Each vacancy must be a JSON object.")
    normalized = dict(job)
    for key in ("title", "company", "source_url"):
        if not isinstance(job.get(key), str) or not job[key].strip():
            raise ValueError(f"Each vacancy needs a nonempty '{key}'.")
    for key in ("source_url", "apply_url"):
        if normalized.get(key):
            parsed = urlsplit(str(normalized[key]))
            if parsed.scheme not in ("https", "http") or not parsed.hostname or parsed.username or parsed.password:
                raise ValueError(f"'{key}' must be an HTTP(S) URL without credentials.")
    normalized.setdefault("source", "manual")
    normalized.setdefault("apply_url", normalized["source_url"])
    return normalized


def collect_sources(config: dict, store: JobStore) -> dict:
    if not isinstance(config, dict) or not isinstance(config.get("sources"), list):
        raise ValueError("Source config must contain a 'sources' list.")
    if len(config["sources"]) > 250:
        raise ValueError("Use at most 250 sources per collection run.")
    report = {"created": 0, "updated": 0, "withdrawn": 0, "sources": []}
    for entry in config["sources"]:
        store_fetched_source(fetch_source(entry), store, report)
    return report


def fetch_source(entry) -> dict:
    """Download and validate one source without touching the database.

    Network work is independent per source, so callers may run this on worker
    threads. The returned batch is written by store_fetched_source, on the
    thread that owns the store. Errors are captured in the batch's result.
    """
    from .sources import fetch_greenhouse, fetch_job_url, fetch_lever, fetch_ashby, geographic_rejection, source_location_terms
    from .company_sources import COMPANY_SOURCE_TYPES, fetch_company_source

    result = {"source": entry, "created": 0, "updated": 0}
    batch = {"result": result, "jobs": None, "snapshot": None, "kind": None}
    try:
        if not isinstance(entry, dict):
            raise ValueError("Each source entry must be an object.")
        source_location_terms(entry)
        if "countries" in entry:
            from .countries import validate_countries
            validate_countries(entry["countries"])
        kind = entry.get("type")
        if kind == "greenhouse":
            jobs = fetch_greenhouse(entry.get("board", ""))
        elif kind == "lever":
            jobs = fetch_lever(entry.get("board", ""), region=entry.get("region", "global"), name=entry.get("name", ""))
        elif kind == "ashby":
            jobs = fetch_ashby(entry.get("board", ""), name=entry.get("name", ""))
        elif kind == "job_url":
            jobs = fetch_job_url(entry.get("url", ""))
        elif kind in COMPANY_SOURCE_TYPES:
            company = fetch_company_source(entry)
            jobs = company["jobs"]
            result["coverage"] = company.get("coverage", "")
            result["has_more"] = company.get("has_more", False)
            result.update({key: company[key] for key in ("fetched", "scanned", "available", "filtered_location", "filtered_country", "unknown_country") if key in company})
        else:
            raise ValueError(f"Unsupported source type: {kind!r}. Choose a supported company source or job URL.")
        # These three adapters return complete boards or raise. Preserve
        # the full feed identity set before geographic filters so a job
        # outside this run's country scope is not mistaken for withdrawn.
        snapshot = None
        if kind in {"greenhouse", "lever", "ashby"} and all(job.get("source") == kind for job in jobs):
            prefix = f"{entry.get('board', '')}:"
            if kind == "lever":
                prefix = f"{entry.get('region', 'global')}:{prefix}"
            snapshot = (prefix, [job.get("source_id") for job in jobs])
        location_terms = source_location_terms(entry)
        result.setdefault("fetched", len(jobs))
        result.setdefault("scanned", result["fetched"])
        result.setdefault("available", result["fetched"])
        selected = []
        for job in jobs:
            reason = geographic_rejection(job, entry, location_terms=location_terms)
            if reason:
                result[reason] = result.get(reason, 0) + 1
            else:
                selected.append(job)
        jobs = selected
        result["matched"] = len(jobs)
        for key in ("filtered_location", "filtered_country", "unknown_country"):
            result.setdefault(key, 0)
        result.setdefault("has_more", False)
        result.setdefault("coverage", f"Read {result['fetched']} public postings; {len(jobs)} match this source's geographic scope.")
        # Validate a complete source batch before writing any of its jobs.
        batch.update(jobs=[validate_job(job) for job in jobs], snapshot=snapshot, kind=kind)
    except (ValueError, OSError) as exc:
        result["ok"] = False
        result["error"] = str(exc)
    return batch


def store_fetched_source(batch: dict, store: JobStore, report: dict) -> dict:
    """Write one fetched source as a single transaction and update the report."""
    result = batch["result"]
    if batch["jobs"] is not None:
        try:
            counts = {"created": 0, "updated": 0, "withdrawn": 0}
            with store.transaction():
                for job in batch["jobs"]:
                    _, created = store.upsert_job(job)
                    counts["created" if created else "updated"] += 1
                if batch["snapshot"] is not None:
                    counts["withdrawn"] = store.reconcile_source_snapshot(batch["kind"], *batch["snapshot"])
            # Identity conflicts and failed writes roll back the whole source;
            # only committed vacancies belong in a successful collection count.
            for key, count in counts.items():
                result[key] = count
                report[key] += count
            result["ok"] = True
        except (ValueError, OSError) as exc:
            result["ok"] = False
            result["error"] = str(exc)
    report["sources"].append(result)
    return result
