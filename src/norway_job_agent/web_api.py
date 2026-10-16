"""Private loopback API for the local desktop web interface.

The session token is embedded only in our own HTML. All API calls require it,
including reads; Origin, Fetch Metadata and exact Host checks add independent
protection against other websites reaching the local application's private data.
"""
from __future__ import annotations

import copy
import json
import secrets
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlsplit

from flask import Flask, jsonify, request, send_from_directory
from werkzeug.exceptions import HTTPException
from werkzeug.utils import secure_filename

from . import gmail, runtime_setup, service
from .browser_delivery import BrowserApplicationSession
from .company_catalog import company_catalog, job_board_catalog
from .countries import COUNTRIES
from .storage import JobStore


def _object(value, label="Request"):
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object.")
    return value


def _text(value, label, maximum=50000):
    if not isinstance(value, str) or len(value) > maximum:
        raise ValueError(f"{label} must be text of at most {maximum:,} characters.")
    return value


def _job_id(value):
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError("Choose an existing vacancy.")
    return value


def _same_token(supplied, expected):
    # compare_digest's text form rejects non-ASCII headers with TypeError.
    return isinstance(supplied, str) and secrets.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8"))


def _diverse_recommendations(jobs):
    """Keep role/experience groups ordered, with two jobs per employer per pass."""
    from collections import deque
    groups = {}
    for job in jobs:
        match = job["match"]
        group = (match.get("experience", {}).get("status") == "mismatch", match["track"])
        employers = groups.setdefault(group, {})
        employer = " ".join(job["company"].casefold().split())
        employers.setdefault(employer, deque()).append(job)
    result = []
    for employers in groups.values():
        remaining = list(employers.values())
        while remaining:
            for queue in remaining:
                for _ in range(min(2, len(queue))):
                    result.append(queue.popleft())
            remaining = [queue for queue in remaining if queue]
    return result


def _list_match(match):
    """The match summary a vacancy card renders; details come from /api/jobs/<id>."""
    experience = match.get("experience") or {}
    return {"score": match.get("score"), "track": match.get("track"), "expired": match.get("expired", False),
            "excluded_terms": match.get("excluded_terms", []),
            "experience": {key: experience.get(key) for key in ("status", "candidate_years", "minimum_years")}}


def _appearance(data_dir):
    """Share the legacy desktop preference file without importing Tk widgets."""
    result = {"theme": "light", "sidebar_visible": True}
    try:
        saved = json.loads((data_dir / "appearance.json").read_text(encoding="utf-8"))
        if isinstance(saved, dict):
            if saved.get("theme") in ("light", "dark"):
                result["theme"] = saved["theme"]
            if isinstance(saved.get("sidebar_visible"), bool):
                result["sidebar_visible"] = saved["sidebar_visible"]
    except (OSError, ValueError):
        pass
    return result


class _State:
    def __init__(self, data_dir, app):
        self.data_dir, self.app = data_dir, app
        self.lock = threading.RLock()
        self.worker = ThreadPoolExecutor(max_workers=4, thread_name_prefix="local-job")
        self.tasks, self.uploads, self.previews, self.revisions = {}, {}, {}, {}
        self.active_actions, self.saved_edits = set(), {}
        self.gmail_connect_cancel = None
        self.credentials = tempfile.TemporaryDirectory(prefix="job-agent-credentials-")
        self.closing = False

    def task(self, callback, *, executor=None, key=None):
        with self.lock:
            if self.closing:
                raise ValueError("The application is closing.")
            if key is not None and key in self.active_actions:
                raise ValueError("This action is already running. Let it finish first.")
            if sum(t["status"] == "running" for t in self.tasks.values()) >= 12:
                raise ValueError("Several actions are already running. Let one finish first.")
            # Keep polling results long enough for a busy UI, with a bounded cache.
            for task_key in list(self.tasks):
                if len(self.tasks) < 200:
                    break
                if self.tasks[task_key]["status"] != "running":
                    del self.tasks[task_key]
            identifier = secrets.token_urlsafe(24)
            self.tasks[identifier] = {"status": "running", "message": "Working…", "result": None, "error": None}
            if key is not None:
                self.active_actions.add(key)

        def progress(message):
            with self.lock:
                self.tasks[identifier]["message"] = str(message)[:1000]

        def run():
            try:
                result = callback(progress)
                with self.lock:
                    self.tasks[identifier].update(status="done", message="Finished", result=result)
            except (ValueError, RuntimeError, OSError) as exc:
                with self.lock:
                    self.tasks[identifier].update(status="error", message="Action needs attention", error=str(exc))
            except Exception:
                self.app.logger.exception("Local action failed")
                with self.lock:
                    self.tasks[identifier].update(status="error", message="Action needs attention", error="The action could not finish. Review its status and the application log before retrying.")
            finally:
                with self.lock:
                    self.active_actions.discard(key)

        with self.lock:
            try:
                if self.closing:
                    raise ValueError("The application is closing.")
                (executor or self.worker).submit(run)
            except Exception:
                self.tasks.pop(identifier, None)
                self.active_actions.discard(key)
                raise
        return {"task_id": identifier}

    def expose(self, payload, job_id):
        """Expose opaque file handles, never arbitrary local path authority."""
        result = copy.deepcopy(payload)
        files = result.get("attachments", {})
        result["attachments"], result["attachment_files"] = {}, {}
        if not isinstance(files, dict):
            return result
        with self.lock:
            for field, raw_path in files.items():
                if not isinstance(raw_path, str) or not raw_path:
                    continue
                key = next((key for key, upload in self.uploads.items()
                            if upload["kind"] == "attachment" and str(upload["path"]) == raw_path
                            and (upload.get("job_id") is None or (upload.get("job_id"), upload.get("field")) == (job_id, field))), None)
                if key is None:
                    key = secrets.token_urlsafe(24)
                    self.uploads[key] = {"kind": "attachment", "path": Path(raw_path), "job_id": job_id, "field": field}
                path = self.uploads[key]["path"]
                try:
                    size = path.stat().st_size
                except OSError:
                    size = 0
                result["attachments"][field] = key
                result["attachment_files"][field] = {"upload_id": key, "name": path.name, "size": size}
        return result

    def resolve(self, payload, job_id):
        result = copy.deepcopy(_object(payload, "Application"))
        if result.get("job_id", job_id) != job_id:
            raise ValueError("These answers belong to a different vacancy.")
        from .application_forms import validate_form
        validate_form(result.get("form"))
        result.pop("attachment_files", None)
        files = result.get("attachments", {})
        if not isinstance(files, dict):
            raise ValueError("Choose attachments using the file picker.")
        fields = {item.get("id") for item in result.get("form", {}).get("fields", [])
                  if isinstance(item, dict) and item.get("type") == "file"}
        if set(files) - fields:
            raise ValueError("An attachment does not belong to an upload question.")
        resolved = {}
        with self.lock:
            for field, identifier in files.items():
                upload = self.uploads.get(identifier) if isinstance(identifier, str) else None
                if not upload or upload["kind"] != "attachment" or (upload.get("job_id") is not None and
                        (upload["job_id"], upload.get("field")) != (job_id, field)):
                    raise ValueError("Choose this attachment again using the file picker.")
                resolved[field] = str(upload["path"])
        result["attachments"] = resolved
        result["job_id"] = job_id
        return result

    def changed(self, job_id):
        with self.lock:
            self.revisions[job_id] = self.revisions.get(job_id, 0) + 1

    def remember_edit(self, kind, job_id, value):
        """Keep newer explicit saves current when a slower AI task completes."""
        key = (kind, job_id)
        with self.lock:
            revision = self.saved_edits.get(key, (0, None))[0]
            self.saved_edits[key] = (revision + 1, copy.deepcopy(value))

    def edit_revision(self, kind, job_id):
        with self.lock:
            return self.saved_edits.get((kind, job_id), (0, None))[0]

    def preserve_newer_edit(self, kind, job_id, revision):
        # Caller holds the lock through saving the generated version and this
        # restore, so an explicit save cannot land between these two writes.
        current, value = self.saved_edits.get((kind, job_id), (0, None))
        if current != revision:
            if kind == "application":
                service.save_application(self.data_dir, job_id, value)
            else:
                service.save_edited_letter(self.data_dir, job_id, value)


def create_app(data_dir: Path, token: str | None = None) -> Flask:
    data_dir = Path(data_dir)
    service.initialize(data_dir)
    app = Flask(__name__, static_folder=None)
    app.config.update(LOCAL_TOKEN=token or secrets.token_urlsafe(32), LOCAL_ORIGIN=None,
                      MAX_CONTENT_LENGTH=16 * 1024 * 1024, MAX_FORM_MEMORY_SIZE=16 * 1024 * 1024)
    assets = Path(__file__).parent / "web"
    state = _State(data_dir, app)
    app.extensions["local_state"] = state

    @app.before_request
    def guard():
        origin = app.config["LOCAL_ORIGIN"]
        if origin:
            if request.host != urlsplit(origin).netloc:
                return jsonify(error="This local address is not allowed."), 403
        elif request.host.split(":")[0] not in {"localhost", "127.0.0.1"}:
            return jsonify(error="Use the local application address."), 403
        expected = origin or request.host_url.rstrip("/")
        if request.headers.get("Origin") not in (None, expected) or request.headers.get("Sec-Fetch-Site") == "cross-site":
            return jsonify(error="Requests from another website are not allowed."), 403
        if request.path.startswith("/api/") and not _same_token(request.headers.get("X-Session-Token", ""), app.config["LOCAL_TOKEN"]):
            return jsonify(error="This session has expired. Reopen the application."), 403

    @app.after_request
    def headers(response):
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.errorhandler(ValueError)
    def invalid(exc):
        return jsonify(error=str(exc)), 400

    @app.errorhandler(HTTPException)
    def http_error(exc):
        return jsonify(error=exc.description), exc.code

    @app.errorhandler(Exception)
    def unexpected(exc):
        app.logger.exception("Local API failed")
        return jsonify(error="This action could not finish. Your private data has not been exposed."), 500

    def body():
        return _object(request.get_json())

    def histories(identifier):
        history = service.application_history(data_dir, identifier)
        for item in history:
            item["payload"] = state.expose(item["payload"], identifier)
        return history

    @app.get("/")
    def index():
        html = (assets / "index.html").read_text(encoding="utf-8")
        # The saved theme is applied before scripts run, avoiding a light flash.
        html = html.replace("__THEME__", _appearance(data_dir)["theme"])
        return html.replace("__SESSION_TOKEN__", app.config["LOCAL_TOKEN"]), 200, {"Content-Type": "text/html; charset=utf-8"}

    @app.get("/assets/<path:filename>")
    def asset(filename):
        # Only shipped UI assets are reachable; private data is outside this tree.
        if Path(filename).suffix.lower() not in {".js", ".css", ".svg", ".woff2", ".png", ".ico"}:
            return jsonify(error="Asset not found."), 404
        return send_from_directory(assets, filename)

    @app.get("/api/bootstrap")
    def bootstrap():
        from .cli import STATUSES
        return jsonify(profile=service.read_profile(data_dir), settings=service.read_settings(data_dir),
                       appearance=_appearance(data_dir),
                       countries=COUNTRIES, statuses=STATUSES, catalog=company_catalog(), boards=job_board_catalog(),
                       gmail=gmail.connection_status(data_dir), collection=service.read_collection_report(data_dir))

    @app.get("/api/jobs")
    def jobs():
        view = request.args.get("view", "discover")
        if view not in {"discover", "saved", "applications", "for-you"}:
            raise ValueError("Choose a vacancy view.")
        summary = {}
        stages = None
        if view == "saved":
            stages = {"saved"}
        elif view == "applications":
            stages = {"preparing", "ready", "applied", "interview", "rejected"}
            if request.args.get("status") == "archived":
                stages.add("archived")
        jobs = service.vacancies(data_dir, query=request.args.get("query", ""), status=request.args.get("status", ""),
                                 country=request.args.get("country", ""), include_inactive=view in {"saved", "applications"},
                                 recommendations_only=view == "for-you", coverage=summary, stages=stages)
        if view == "for-you":
            jobs = [job for job in jobs if job["match"]["track"] in {"target", "horizon"} and not job["match"].get("excluded_terms") and not job["match"].get("expired")]
        summary["role_matches"] = len(jobs) if view == "for-you" else sum(job["match"]["track"] in {"target", "horizon"} for job in jobs)
        summary["experience_mismatches"] = sum(job["match"].get("experience", {}).get("status") == "mismatch" for job in jobs)
        experience = request.args.get("experience", "relevant" if view == "for-you" else "all")
        if experience not in {"all", "relevant", "mismatch"}:
            raise ValueError("Choose relevant, all or mismatch experience levels.")
        if experience != "all":
            jobs = [job for job in jobs if (job["match"].get("experience", {}).get("status") == "mismatch") == (experience == "mismatch")]
        summary["shown"] = len(jobs)
        summary["shown_employers"] = len({job["company"] for job in jobs})
        sort = request.args.get("sort", "match")
        if sort == "newest":
            jobs.sort(key=lambda item: str(item.get("published_at") or item.get("discovered_at") or ""), reverse=True)
        elif sort == "company":
            jobs.sort(key=lambda item: (str(item.get("company", "")).casefold(), str(item.get("title", "")).casefold()))
        elif sort != "match":
            raise ValueError("Choose match, newest or company sorting.")
        elif view == "for-you":
            jobs = _diverse_recommendations(jobs)
        # Descriptions, raw provider JSON and full match evidence are loaded
        # only for the selected vacancy. Cards need just these summary fields;
        # sending everything made a large discovery list several megabytes.
        for job in jobs:
            job.pop("description", None)
            job.pop("raw_json", None)
            job["match"] = _list_match(job["match"])
        return jsonify(jobs=jobs, summary=summary)

    @app.get("/api/discovery/coverage")
    def coverage():
        with state.lock:
            return jsonify(service.discovery_coverage(data_dir, request.args.get("country", "")))

    @app.get("/api/jobs/<int:identifier>")
    def job(identifier):
        with state.lock:
            value = service.get_vacancy(data_dir, identifier)
            with JobStore(data_dir / "vacancies.db") as store:
                deliveries = store.deliveries(identifier)
            return jsonify(job=value, letters=service.draft_history(data_dir, identifier), preparations=histories(identifier), deliveries=deliveries)

    @app.post("/api/jobs")
    def import_job():
        identifier, created = service.import_vacancy(data_dir, body())
        return jsonify(id=identifier, created=created)

    @app.post("/api/jobs/<int:identifier>/workflow")
    def workflow(identifier):
        service.get_vacancy(data_dir, identifier)
        value = body()
        service.update_workflow(data_dir, identifier, _text(value.get("status"), "Status", 50), _text(value.get("notes", ""), "Notes"))
        return jsonify(ok=True)

    @app.route("/api/profile", methods=["GET", "PUT"])
    def profile():
        if request.method == "PUT":
            service.save_profile(data_dir, body())
        return jsonify(service.read_profile(data_dir))

    @app.route("/api/settings", methods=["GET", "PUT"])
    def settings():
        with state.lock:
            if request.method == "PUT":
                value = body()
                _text(value.get("active_country", ""), "Country", 2)
                if not isinstance(value.get("sources"), list):
                    raise ValueError("Settings need a sources list.")
                for source in value["sources"]:
                    _text(_object(source, "Source").get("type"), "Source type", 50)
                service.save_settings(data_dir, value)
            return jsonify(service.read_settings(data_dir))

    @app.route("/api/appearance", methods=["GET", "PUT"])
    def appearance():
        with state.lock:
            values = _appearance(data_dir)
            if request.method == "PUT":
                theme = _text(body().get("theme"), "Theme", 10)
                if theme not in {"light", "dark"}:
                    raise ValueError("Choose a light or dark theme.")
                values["theme"] = theme
                service._write_json(data_dir / "appearance.json", values)
                hook = app.config.get("THEME_CHANGED")
                if hook is not None:
                    try:
                        hook(theme)  # Native title bar colour; cosmetic only.
                    except Exception:
                        app.logger.exception("Could not update the window theme")
            return jsonify(values)

    @app.post("/api/country")
    def country():
        with state.lock:
            return jsonify(service.select_country(data_dir, _text(body().get("country", ""), "Country", 2)))

    @app.post("/api/sources/add")
    def add_sources():
        value = body()
        with state.lock:
            return jsonify(service.add_catalog_sources(data_dir, value.get("ids"), value.get("country", "")))

    @app.post("/api/jobs/<int:identifier>/letter")
    def save_letter(identifier):
        service.get_vacancy(data_dir, identifier)
        content = _text(body().get("content"), "Cover letter")
        with state.lock:
            result = service.save_edited_letter(data_dir, identifier, content)
            state.remember_edit("letter", identifier, content)
        return jsonify(id=result)

    @app.post("/api/jobs/<int:identifier>/application")
    def save_application(identifier):
        service.get_vacancy(data_dir, identifier)
        value = state.resolve(body(), identifier)
        with state.lock:
            result = service.save_application(data_dir, identifier, value)
            state.remember_edit("application", identifier, value)
            state.changed(identifier)
        return jsonify(id=result, payload=state.expose(value, identifier))

    @app.post("/api/tasks")
    def start_task():
        value = body()
        action = _text(value.get("action"), "Action", 50)
        if action in {"letter", "questions", "answers"}:
            identifier = _job_id(value.get("job_id"))
            service.get_vacancy(data_dir, identifier)
        if action == "collect":
            callback = lambda progress: service.collect(data_dir, progress=progress)
        elif action == "letter":
            revision = state.edit_revision("letter", identifier)
            def callback(progress):
                result = service.write_letter(data_dir, identifier, persist=False)
                with state.lock:
                    metadata = result.pop("draft_metadata")
                    with JobStore(data_dir / "vacancies.db") as store:
                        result["id"] = store.save_draft(identifier, result["cover_letter"], **metadata)
                    state.preserve_newer_edit("letter", identifier, revision)
                return result
        elif action == "questions":
            manual = _text(value.get("manual_text", ""), "Questions")
            revision = state.edit_revision("application", identifier)
            def callback(progress):
                result = service.discover_application(data_dir, identifier, manual)
                with state.lock:
                    service.save_application(data_dir, identifier, result)
                    state.preserve_newer_edit("application", identifier, revision)
                    state.changed(identifier)
                return state.expose(result, identifier)
        elif action == "answers":
            form = copy.deepcopy(_object(value.get("form"), "Form"))
            from .application_forms import validate_form
            validate_form(form)
            revision = state.edit_revision("application", identifier)
            def callback(progress):
                result = service.prepare_application(data_dir, identifier, form, persist=False)
                with state.lock:
                    service.save_application(data_dir, identifier, result)
                    state.preserve_newer_edit("application", identifier, revision)
                    state.changed(identifier)
                return state.expose(result, identifier)
        elif action == "suggest-profile":
            cv_text = _text(value.get("cv_text"), "CV")
            callback = lambda progress: service.suggest_profile(data_dir, cv_text)
        elif action == "models":
            callback = lambda progress: {"models": service.available_models()}
        elif action == "setup-status":
            callback = lambda progress: runtime_setup.setup_status(service.read_settings(data_dir)["model"])
        elif action == "start-ollama":
            callback = lambda progress: {"message": runtime_setup.start_ollama()}
        elif action == "install-pdf":
            callback = lambda progress: {"message": runtime_setup.install_pdf_support()}
        elif action == "download-model":
            model = service.read_settings(data_dir)["model"]
            callback = lambda progress: {"models": runtime_setup.download_model(model, progress=progress)}
        elif action == "import-url":
            url = _text(value.get("url"), "Job URL", 5000)
            def callback(progress):
                from .sources import fetch_job_url
                jobs = fetch_job_url(url)
                results = [service.import_vacancy(data_dir, job) for job in jobs]
                return {"ids": [item[0] for item in results], "created": sum(item[1] for item in results)}
        elif action == "gmail-fetch":
            query = _text(value.get("query", gmail.DEFAULT_QUERY), "Gmail query", 5000)
            page_token = _text(value.get("page_token", ""), "Page token", 5000)
            callback = lambda progress: gmail.fetch_candidates(data_dir, service.read_profile(data_dir), query=query, page_token=page_token)
        elif action == "gmail-connect":
            with state.lock:
                upload = state.uploads.get(value.get("upload_id")) if isinstance(value.get("upload_id"), str) else None
            if not upload or upload["kind"] != "gmail":
                raise ValueError("Choose the downloaded Gmail desktop credentials JSON file.")
            path = upload["path"]
            cancel = threading.Event()
            def callback(progress):
                progress("Complete the read-only Gmail connection in your browser.")
                try:
                    if cancel.is_set():
                        raise ValueError("Gmail connection cancelled. Your existing connection was kept.")
                    return gmail.connect(data_dir, path, cancel_event=cancel)
                finally:
                    try:
                        path.unlink(missing_ok=True)
                    finally:
                        with state.lock:
                            if state.gmail_connect_cancel is cancel:
                                state.gmail_connect_cancel = None
        elif action == "gmail-disconnect":
            callback = lambda progress: gmail.disconnect(data_dir)
        elif action == "shortcuts":
            def callback(progress):
                from .windows_integration import create_shortcuts
                created = create_shortcuts()
                return {"message": f"Added {len(created)} shortcuts: Start menu and desktop. Pin the app from the Start menu if you like."}
        else:
            raise ValueError("Choose a supported background action.")
        key = ("application", identifier) if action in {"questions", "answers"} else (action, identifier if action == "letter" else None)
        if action in {"gmail-connect", "gmail-disconnect", "gmail-fetch"}:
            key = ("gmail-account", None)
        if action == "gmail-connect":
            with state.lock:
                if state.gmail_connect_cancel is not None:
                    raise ValueError("A Gmail connection is already running. Finish or cancel it first.")
                state.gmail_connect_cancel = cancel
                try:
                    return jsonify(state.task(callback, key=key)), 202
                except Exception:
                    state.gmail_connect_cancel = None
                    raise
        return jsonify(state.task(callback, key=key)), 202

    @app.get("/api/tasks/<identifier>")
    def task_status(identifier):
        with state.lock:
            result = copy.deepcopy(state.tasks.get(identifier))
        if result is None:
            return jsonify(error="This action is no longer available."), 404
        return jsonify(result)

    @app.post("/api/upload")
    def upload():
        kind, file = request.form.get("kind"), request.files.get("file")
        extensions = {"cv": {".txt", ".md", ".docx", ".pdf"}, "eml": {".eml"}, "gmail": {".json"},
                      "attachment": {".pdf", ".doc", ".docx", ".txt", ".rtf", ".odt", ".png", ".jpg", ".jpeg"}}
        if kind not in extensions or file is None or not file.filename:
            raise ValueError("Choose a file and its import type.")
        original_name = file.filename.replace("\\", "/").rsplit("/", 1)[-1]
        suffix = Path(original_name).suffix.lower()
        if suffix not in extensions[kind]:
            raise ValueError("This file type is not supported for this import.")
        # Cyrillic-only and other Unicode stems can disappear during Werkzeug's
        # ASCII sanitization. Keep their recognized extension and a safe fallback.
        stem = secure_filename(original_name[:-len(suffix)]).strip(".")[:80] or "document"
        name = stem + suffix
        limit = {"cv": 15, "eml": 10, "gmail": 1, "attachment": 10}[kind] * 1024 * 1024
        content = file.stream.read(limit + 1)
        if not content or len(content) > limit:
            raise ValueError(f"Choose a nonempty file smaller than {limit // (1024 * 1024)} MB.")
        if kind in {"cv", "eml"}:
            with tempfile.TemporaryDirectory(prefix="job-agent-import-") as directory:
                path = Path(directory) / name
                path.write_bytes(content)
                return jsonify(text=service.import_cv_text(path)) if kind == "cv" else jsonify(candidates=gmail.import_eml(path, service.read_profile(data_dir)))
        identifier = secrets.token_urlsafe(24)
        directory = (data_dir / "uploads" / identifier) if kind == "attachment" else Path(state.credentials.name) / identifier
        directory.mkdir(parents=True)
        path = directory / name
        path.write_bytes(content)
        if kind == "gmail":
            try:
                gmail._client_credentials(path)
            except Exception:
                path.unlink(missing_ok=True)
                raise
        with state.lock:
            state.uploads[identifier] = {"path": path, "kind": kind}
        return jsonify(upload_id=identifier, name=name, size=len(content))

    @app.post("/api/gmail/import")
    def import_emails():
        return jsonify(service.import_email_candidates(data_dir, body().get("candidates")))

    @app.post("/api/gmail/cancel")
    def cancel_gmail():
        with state.lock:
            cancel = state.gmail_connect_cancel
            if cancel is not None:
                cancel.set()
        return jsonify(ok=True, cancel_requested=cancel is not None)

    @app.post("/api/delivery/preview")
    def preview():
        value = body()
        identifier = _job_id(value.get("job_id"))
        job = service.get_vacancy(data_dir, identifier)
        if not job.get("is_active", True):
            raise ValueError("This vacancy has been withdrawn.")
        payload = state.resolve(value.get("payload"), identifier)
        from .application_forms import validate_form
        validate_form(payload.get("form"))
        with state.lock:
            if len(state.previews) >= 4:
                raise ValueError("Close an existing browser preview before opening another.")
            preview_id = secrets.token_urlsafe(24)
            executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="reviewed-delivery")
            entry = {"executor": executor, "session": None, "preview": None, "job_id": identifier,
                     "revision": state.revisions.get(identifier, 0), "attempted": False, "closed": False}
            state.previews[preview_id] = entry
        def prepare(progress):
            progress("Opening and filling the supported employer form for your review…")
            session = None
            try:
                session = BrowserApplicationSession()
                entry["session"] = session
                result = session.prepare(payload["form"], payload.get("answers", []), payload.get("attachments", {}))
                with state.lock:
                    entry["preview"] = copy.deepcopy(result)
                return {"preview_id": preview_id, "preview": result}
            except Exception:
                try:
                    if session is not None:
                        session.close()
                finally:
                    with state.lock:
                        state.previews.pop(preview_id, None)
                    executor.shutdown(wait=False)
                raise
        try:
            return jsonify(state.task(prepare, executor=executor)), 202
        except Exception:
            state.previews.pop(preview_id, None)
            executor.shutdown(wait=False)
            raise

    @app.post("/api/delivery/<preview_id>/send")
    def send(preview_id):
        value = body()
        with state.lock:
            entry = state.previews.get(preview_id)
            if not entry or entry["closed"] or entry["attempted"] or not entry["preview"]:
                raise ValueError("Prepare and review a new application before sending.")
            supplied = value.get("token")
            if not _same_token(supplied, entry["preview"]["token"]):
                raise ValueError("This review token does not match the application preview.")
            if state.revisions.get(entry["job_id"], 0) != entry["revision"]:
                raise ValueError("The application was edited after this preview. Prepare and review it again.")
            def deliver(progress):
                with state.lock:
                    if entry["closed"] or state.closing:
                        raise ValueError("This browser preview was closed before sending. No application was sent.")
                    if state.revisions.get(entry["job_id"], 0) != entry["revision"]:
                        raise ValueError("The application was edited before sending. Prepare and review it again.")
                progress("Sending the exact application you reviewed, once…")
                return service.send_reviewed_application(data_dir, entry["job_id"], entry["session"], entry["preview"])
            result = state.task(deliver, executor=entry["executor"])
            entry["attempted"] = True
        return jsonify(result), 202

    @app.post("/api/delivery/<preview_id>/close")
    def close_preview(preview_id):
        with state.lock:
            entry = state.previews.pop(preview_id, None)
            if entry:
                entry["closed"] = True
                entry["executor"].submit(lambda: entry["session"].close() if entry["session"] else None)
                entry["executor"].shutdown(wait=False)
        return jsonify(ok=True)

    return app


def close_resources(app: Flask) -> None:
    """Stop accepting work and close each browser on its owning worker."""
    state = app.extensions["local_state"]
    with state.lock:
        if state.closing:
            return
        state.closing = True
        if state.gmail_connect_cancel is not None:
            state.gmail_connect_cancel.set()
        for entry in state.previews.values():
            entry["closed"] = True
            entry["executor"].submit(lambda entry=entry: entry["session"].close() if entry["session"] else None)
            entry["executor"].shutdown(wait=False)
        state.previews.clear()
    state.worker.shutdown(wait=False, cancel_futures=True)
    state.credentials.cleanup()
