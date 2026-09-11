"""Add supported Norway employers without replacing the existing watchlist."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from norway_job_agent import service
from norway_job_agent.cli import default_data_dir
from norway_job_agent.company_catalog import company_catalog

if "--diagnose-greenhouse" in sys.argv:
    from norway_job_agent.sources import _json, _plain
    payload = _json("https://boards-api.greenhouse.io/v1/boards/cognite/jobs?content=true")
    print(json.dumps({"jobs_count": len(payload.get("jobs", []))}))
    for item in payload.get("jobs", []):
        if not isinstance(item, dict) or not item.get("id") or not _plain(item.get("title")):
            print(json.dumps({"invalid_job_type": type(item).__name__, "id": item.get("id") if isinstance(item, dict) else None, "title": item.get("title") if isinstance(item, dict) else None, "keys": list(item) if isinstance(item, dict) else []}))
    raise SystemExit(0)

data_dir = default_data_dir()
settings = service.read_settings(data_dir)
watchlist = [entry["source"] for entry in company_catalog() if entry.get("source")]
identity_keys = ("type", "board", "url", "tenant", "site", "host", "company")
added = 0
for source in watchlist:
    if not any(all(item.get(key, "") == source.get(key, "") for key in identity_keys) for item in settings["sources"]):
        settings["sources"].append(source)
        added += 1
service.save_settings(data_dir, settings)
print(json.dumps({"added_sources": added, "configured_sources": len(settings["sources"]), "status": "Collecting public vacancies..."}), flush=True)
report = service.collect(data_dir, progress=lambda message: print(message, flush=True))
print(json.dumps(report, ensure_ascii=True, indent=2), flush=True)
print(json.dumps({"visible_vacancies": len(service.vacancies(data_dir))}), flush=True)
if any(not entry["ok"] for entry in report["sources"]):
    raise SystemExit(1)
