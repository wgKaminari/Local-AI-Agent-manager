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

from .local_ai import list_local_models, validate_model_name


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
    # A cold start (first run, antivirus scan, low memory) can take well over
    # ten seconds; the earlier 10 s limit reported failure for working setups.
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        time.sleep(0.5)
        if _running():
            return "Ollama is running. Download or select a local model next."
    raise RuntimeError("Ollama did not become ready within a minute. Open the Ollama app from the Start menu, then retry.")


def ensure_ollama(progress=None) -> None:
    """Start an installed Ollama before local AI work instead of failing.

    Ollama only answers while its background server runs, and it is often
    not started with Windows. A missing installation still raises the
    actionable error from local_ai when the request is made.
    """
    if _running() or not ollama_executable():
        return
    if progress is not None:
        progress("Starting Ollama on this computer…")
    start_ollama()


def memory_available_gb() -> float | None:
    """Free physical memory, or None where it cannot be measured."""
    if os.name != "nt":
        return None
    import ctypes

    class Status(ctypes.Structure):
        _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong), ("total", ctypes.c_ulonglong),
                    ("available", ctypes.c_ulonglong), ("page_total", ctypes.c_ulonglong),
                    ("page_available", ctypes.c_ulonglong), ("virtual_total", ctypes.c_ulonglong),
                    ("virtual_available", ctypes.c_ulonglong), ("extended", ctypes.c_ulonglong)]

    status = Status(length=ctypes.sizeof(Status))
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        return None
    return status.available / 1024 ** 3


def speed_advice(tokens_per_second, available_gb=None) -> str | None:
    """Plain advice when local generation was slow, else None."""
    if tokens_per_second is None or tokens_per_second >= 4:
        return None
    advice = f"Your local model wrote only {tokens_per_second:.1f} tokens per second."
    if available_gb is not None and available_gb < 2:
        advice += f" Only {available_gb:.1f} GB of memory was free, so Windows was swapping the model to disk; closing browsers, games or other large programs while drafting helps most."
    else:
        advice += " Closing other large programs helps; a smaller model such as qwen3:1.7b in Settings is faster but writes less carefully."
    return advice


def setup_status(model: str) -> dict:
    result = {"pdf": pdf_available(), "python": sys.executable, "models": [], "ready": False}
    free = memory_available_gb()
    if free is not None:
        result["free_memory"] = f"{free:.1f} GB" + (" — low; local drafting will be slow until you close other programs" if free < 2 else "")
    try:
        ensure_ollama()
        result["models"] = list_local_models()
        result["ready"] = _model_installed(model, result["models"])
        result["message"] = (f"Ready — {model} is installed locally." if result["ready"] else
                              f"Ollama is running. Download {model}, or choose an installed model.")
    except RuntimeError as exc:  # LocalAIError, or Ollama failing to start.
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
