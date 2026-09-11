"""Verify local setup and CV extraction without changing the saved profile."""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from norway_job_agent import local_ai, service, runtime_setup
from norway_job_agent.cli import default_data_dir

started = time.monotonic()
runtime_setup.start_ollama()
report = {"models": local_ai.list_local_models()}
if "--cv" in sys.argv:
    profile = service.read_profile(default_data_dir())
    try:
        result = local_ai.extract_profile(profile["cv_text"], model=service.read_settings(default_data_dir())["model"])
        report.update(cv_ok=True, suggested_fields=[key for key, value in result.items() if value and key not in {"cv_text", "_review_notes"}],
                      omissions_reported=bool(result.get("_review_notes")))
    except (ValueError, RuntimeError) as exc:
        report.update(cv_ok=False, error=str(exc))
report["seconds"] = round(time.monotonic() - started, 1)
destination = Path(__file__).resolve().parents[1] / "private/local-ai-check.json"
destination.parent.mkdir(exist_ok=True)
destination.write_text(json.dumps(report, indent=2), encoding="utf-8")
print(json.dumps(report), flush=True)
if report.get("cv_ok") is False:
    raise SystemExit(1)
