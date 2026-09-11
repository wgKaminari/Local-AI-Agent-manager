"""Set up the user's private runtime data without overwriting an existing profile."""

import argparse
import json
import sys
import os
import shutil
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from norway_job_agent.cli import default_data_dir
from norway_job_agent.profile import load_profile
from norway_job_agent import service


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", required=True, type=Path)
    parser.add_argument("--data-dir", default=default_data_dir(), type=Path)
    parser.add_argument("--collect", action="store_true")
    args = parser.parse_args()
    profile = load_profile(args.profile)
    legacy = Path(os.environ.get("LOCALAPPDATA", "")) / "NorwayJobAgent"
    if not (args.data_dir / "profile.json").exists() and (legacy / "profile.json").exists():
        # Copy only this app's known files from the earlier development default.
        # The source stays intact; never move or overwrite an existing profile.
        if load_profile(legacy / "profile.json").get("cv_text") == profile.get("cv_text"):
            args.data_dir.mkdir(parents=True, exist_ok=True)
            for filename in ("profile.json", "sources.json", "vacancies.db", "nav-state.json"):
                source = legacy / filename
                target = args.data_dir / filename
                if source.is_file() and not target.exists():
                    shutil.copy2(source, target)
    existing = args.data_dir / "profile.json"
    if existing.exists():
        current = load_profile(existing)
        if current.get("cv_text") and current != profile:
            raise SystemExit("An existing personal profile is present. Import or edit it through the app to avoid overwriting your changes.")
    service.initialize(args.data_dir)
    service.save_profile(args.data_dir, profile)
    print(f"Private data configured in {args.data_dir.resolve()}")
    print("Profile includes supplied CV facts; no application has been submitted.")
    if args.collect:
        # Prime a useful initial result list in bounded batches, with progress.
        # Subsequent app runs resume the same checkpoint.
        for batch_number in range(1, 41):
            report = service.collect(args.data_dir)
            print(json.dumps({"batch": batch_number, **report}, ensure_ascii=False), flush=True)
            if any(not entry["ok"] for entry in report["sources"]):
                raise SystemExit(1)
            if not any(entry.get("has_more") for entry in report["sources"]):
                break
        jobs = service.vacancies(args.data_dir)
        print(json.dumps({"stored_visible": len(jobs), "target_roles": sum(job["match"]["track"] == "target" for job in jobs), "related_suggestions": sum(job["match"]["track"] == "horizon" for job in jobs)}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
