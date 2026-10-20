"""Local command-line workflow shared by future dashboard or bot adapters."""

import argparse
import json
import sys
from pathlib import Path

from .profile import load_profile, match_job, preparation_brief
from .storage import JobStore


# Re-exported for scripts and tests that import them from the command line.
from .collection import collect_sources, fetch_source, store_fetched_source, validate_job  # noqa: F401
from .storage import STATUS_ORDER as STATUSES


def default_data_dir() -> Path:
    # A stable home path avoids packaged Windows applications virtualizing
    # LOCALAPPDATA differently from a normal double-clicked Python process.
    return Path.home() / ".norway-job-agent"


def read_json(path: str | Path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def print_json(value) -> None:
    print(json.dumps(value, indent=2, ensure_ascii=False))


def require_job(store: JobStore, job_id: int) -> dict:
    job = store.get_job(job_id)
    if job is None:
        raise ValueError(f"Vacancy {job_id} does not exist.")
    return job


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Collect and organize vacancies; prepare materials for your own review and submission.")
    root.add_argument("--data-dir", type=Path, default=default_data_dir(), help="Personal data folder (defaults outside this OneDrive checkout).")
    commands = root.add_subparsers(dest="command", required=True)
    commands.add_parser("init", help="Create empty database, profile and source configuration.")
    gui = commands.add_parser("gui", help="Open the modern private desktop app.")
    gui.add_argument("--classic", action="store_true", help="Use the previous Tkinter interface.")
    shortcuts = commands.add_parser("shortcuts", help="Add Start menu and desktop shortcuts that open the app without a console (Windows).")
    shortcuts.add_argument("--remove", action="store_true", help="Remove the shortcuts instead.")
    commands.add_parser("models", help="List verified local Ollama models.")
    generate = commands.add_parser("letter", help="Generate a draft cover letter with your local Ollama model.")
    generate.add_argument("id", type=int)
    cv = commands.add_parser("read-cv", help="Extract CV file text for review; does not change your profile.")
    cv.add_argument("file", type=Path)
    imp = commands.add_parser("import", help="Import manually saved vacancy JSON (one object or a list).")
    imp.add_argument("file", type=Path)
    url = commands.add_parser("import-url", help="Read a public job page with JobPosting structured data.")
    url.add_argument("url")
    collect = commands.add_parser("collect", help="Collect vacancies from configured public sources.")
    collect.add_argument("--sources", type=Path)
    listing = commands.add_parser("list", help="List stored vacancies, optionally with keyword match evidence.")
    listing.add_argument("--status", choices=STATUSES)
    listing.add_argument("--query")
    listing.add_argument("--match", action="store_true")
    listing.add_argument("--country", choices=("NO", "US", "DE", "UA", "unknown"))
    catalog = commands.add_parser("catalog", help="List researched employers or job boards by country.")
    catalog.add_argument("--country", choices=("NO", "US", "DE", "UA"))
    catalog.add_argument("--boards", action="store_true")
    application = commands.add_parser("application", help="Extract application questions and optionally draft answers; never submits.")
    application.add_argument("id", type=int)
    application.add_argument("--questions", type=Path, help="Manual questions file, one per line; options separated by |.")
    application.add_argument("--draft", action="store_true")
    for name, help_text in (("show", "Show a stored vacancy."), ("brief", "Export an offline factual preparation brief.")):
        cmd = commands.add_parser(name, help=help_text)
        cmd.add_argument("id", type=int)
        if name == "brief":
            cmd.add_argument("--output", type=Path)
    status = commands.add_parser("status", help="Record your application workflow status manually.")
    status.add_argument("id", type=int)
    status.add_argument("status", choices=STATUSES)
    notes = commands.add_parser("notes", help="Save your private notes for a vacancy.")
    notes.add_argument("id", type=int)
    notes.add_argument("text")
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    data_dir = args.data_dir.expanduser().resolve()
    try:
        if args.command == "catalog":
            from .company_catalog import company_catalog, job_board_catalog
            print_json((job_board_catalog if args.boards else company_catalog)(args.country or ""))
            return 0
        if args.command == "application":
            from .service import discover_application, prepare_application
            result = discover_application(data_dir, args.id, args.questions.read_text(encoding="utf-8-sig") if args.questions else "")
            if args.draft:
                result = prepare_application(data_dir, args.id, result["form"])
            print_json(result)
            return 0
        if args.command == "gui":
            if args.classic:
                from .desktop import launch
            else:
                from .modern_desktop import launch
            launch(data_dir)
            return 0
        if args.command == "shortcuts":
            from .windows_integration import create_shortcuts, remove_shortcuts
            if args.remove:
                remove_shortcuts()
                print("Shortcuts removed.")
            else:
                for path in create_shortcuts():
                    print(f"Shortcut: {path}")
            return 0
        if args.command == "models":
            from .service import available_models
            print_json(available_models())
            return 0
        if args.command == "read-cv":
            from .service import import_cv_text
            print(import_cv_text(args.file))
            return 0
        if args.command == "letter":
            from .service import write_letter
            print_json(write_letter(data_dir, args.id))
            return 0
        if args.command == "collect":
            from .service import collect
            report = collect(data_dir, read_json(args.sources) if args.sources else None)
            print_json(report)
            if not report["sources"]:
                print("No sources configured. Add NAV, company boards or job URLs to sources.json.", file=sys.stderr)
            return 1 if any(not source["ok"] for source in report["sources"]) else 0
        with JobStore(data_dir / "vacancies.db") as store:
            if args.command == "init":
                from .service import initialize
                initialize(data_dir)
                print(f"Data folder: {data_dir}\nFill profile.json and sources.json to configure your search.")
            elif args.command in ("import", "import-url"):
                if args.command == "import":
                    payload = read_json(args.file)
                    jobs = payload if isinstance(payload, list) else [payload]
                else:
                    from .sources import fetch_job_url
                    jobs = fetch_job_url(args.url)
                jobs = [validate_job(job) for job in jobs]
                created = updated = 0
                for job in jobs:
                    _, is_new = store.upsert_job(job)
                    created += int(is_new)
                    updated += int(not is_new)
                print_json({"created": created, "updated": updated})
            elif args.command == "list":
                jobs = store.list_jobs(status=args.status, query=args.query)
                from .countries import job_countries
                if args.country:
                    jobs = [job for job in jobs if (not job_countries(job) if args.country == "unknown" else args.country in job_countries(job))]
                profile = load_profile(data_dir / "profile.json") if args.match else None
                rows = []
                for job in jobs:
                    row = {key: job.get(key) for key in ("id", "title", "company", "location", "status", "deadline", "last_seen_at", "source_url")}
                    row["countries"] = job_countries(job)
                    if profile is not None:
                        row["match"] = match_job(job, profile)
                    rows.append(row)
                if profile is not None:
                    rows.sort(key=lambda row: row["match"]["score"] if row["match"]["score"] is not None else -1, reverse=True)
                print_json(rows)
            elif args.command == "show":
                print_json(require_job(store, args.id))
            elif args.command == "status":
                require_job(store, args.id)
                store.update_status(args.id, args.status)
                print(f"Vacancy {args.id}: {args.status}")
            elif args.command == "notes":
                require_job(store, args.id)
                store.update_notes(args.id, args.text)
                print(f"Notes saved for vacancy {args.id}.")
            elif args.command == "brief":
                job = require_job(store, args.id)
                content = preparation_brief(job, load_profile(data_dir / "profile.json"))
                if args.output:
                    # Never silently overwrite a previously edited application document.
                    args.output.parent.mkdir(parents=True, exist_ok=True)
                    with args.output.open("x", encoding="utf-8") as target:
                        target.write(content)
                    print(f"Preparation brief: {args.output.resolve()}")
                else:
                    print(content)
        return 0
    except (ValueError, KeyError, RuntimeError, OSError) as exc:
        if args.command == "gui":
            from .modern_desktop import report_startup_error
            report_startup_error(str(exc))
        elif sys.stderr is not None:
            print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
