"""Explicit, user-triggered repair actions for optional local capabilities."""

from __future__ import annotations

import importlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
from http.client import HTTPConnection, HTTPException
from pathlib import Path

from .local_ai import LocalAIError, list_local_models, validate_model_name


def pdf_available() -> bool:
    importlib.invalidate_caches()
    return importlib.util.find_spec("pypdf") is not None


def install_pdf_support() -> str:
    """Install in the exact Python environment running this app, without a shell."""
    process = subprocess.run(
        [sys.executable, "-m", "pip", "install", "pypdf>=6.18,<7", "--disable-pip-version-check"],
        capture_output=True, timeout=300,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    if process.returncode or not pdf_available():
        raise RuntimeError("PDF setup could not finish. Check your internet connection and Python installation, then retry. "
                           "You can also run: python -m pip install pypdf")
    return "PDF import is ready. You can import your CV now."


def ollama_executable() -> str | None:
    found = shutil.which("ollama")
    if found:
        return found
    if os.name == "nt":
        candidate = Path.home() / "AppData/Local/Programs/Ollama/ollama.exe"
        if candidate.is_file():
            return str(candidate)
    return None


def _running() -> bool:
    connection = HTTPConnection("127.0.0.1", 11434, timeout=2)
    try:
        connection.request("GET", "/api/tags")
        response = connection.getresponse()
        return response.status == 200
    except (OSError, HTTPException):
        return False
    finally:
        connection.close()


def start_ollama() -> str:
    if _running():
        return "Ollama is already running."
    executable = ollama_executable()
    if not executable:
        raise RuntimeError("Install Ollama using Get Ollama, then choose Start Ollama here.")
    subprocess.Popen([executable, "serve"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL,
                     creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    for _ in range(20):
        time.sleep(0.5)
        if _running():
            return "Ollama is running. Download or select a local model next."
    raise RuntimeError("Ollama did not become ready. Open the Ollama app and retry Check setup.")


def setup_status(model: str) -> dict:
    result = {"pdf": pdf_available(), "python": sys.executable, "models": [], "ready": False}
    try:
        result["models"] = list_local_models()
        result["ready"] = _model_installed(model, result["models"])
        result["message"] = (f"Ready — {model} is installed locally." if result["ready"] else
                              f"Ollama is running. Download {model}, or choose an installed model.")
    except LocalAIError as exc:
        result["message"] = str(exc)
    return result


def _model_installed(model: str, models: list[str]) -> bool:
    # Ollama resolves an omitted tag to :latest and returns the expanded name
    # from /api/tags, even when the user requested a tagless model name.
    return model in models or (":" not in model and model + ":latest" in models)


def download_model(model: str, progress=None) -> list[str]:
    """Download through loopback Ollama; report bounded NDJSON progress messages."""
    validate_model_name(model)
    connection = HTTPConnection("127.0.0.1", 11434, timeout=120)
    started = time.monotonic()
    last_progress = 0.0
    finished = False
    try:
        body = json.dumps({"model": model, "stream": True}).encode()
        connection.request("POST", "/api/pull", body=body, headers={"Content-Type": "application/json"})
        response = connection.getresponse()
        if response.status != 200:
            raise RuntimeError("Model download could not start. Check the model name and choose Start Ollama first.")
        while time.monotonic() - started < 7200:
            line = response.readline(65537)
            if not line:
                break
            if len(line) > 65536:
                raise RuntimeError("Ollama returned invalid download progress. Restart Ollama and retry.")
            try:
                item = json.loads(line)
            except (ValueError, UnicodeError):
                raise RuntimeError("Ollama returned unreadable download progress. Retry the download.") from None
            if not isinstance(item, dict) or item.get("error"):
                raise RuntimeError("Model download failed. Check internet access and free disk space, then retry to resume.")
            status = str(item.get("status", "Downloading model"))[:200]
            if status == "success":
                finished = True
                break
            if progress and time.monotonic() - last_progress >= 0.5:
                total, complete = item.get("total"), item.get("completed")
                if isinstance(total, (int, float)) and total > 0 and isinstance(complete, (int, float)):
                    status = f"Downloading {model}: {min(100, max(0, int(100 * complete / total)))}% ({complete / 1e9:.2f} / {total / 1e9:.2f} GB)"
                progress(status)
                last_progress = time.monotonic()
        if not finished:
            raise RuntimeError("The download did not finish. Retry to resume the partial download.")
    except (OSError, HTTPException):
        raise RuntimeError("The model download was interrupted. Check Ollama and your connection, then retry to resume.") from None
    finally:
        connection.close()
    models = list_local_models()
    if not _model_installed(model, models):
        raise RuntimeError("The download finished, but Ollama could not verify this as a local chat model. Choose another model.")
    return models
