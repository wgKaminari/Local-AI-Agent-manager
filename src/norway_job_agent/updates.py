"""Check GitHub for a newer release and hand over to the installer to update.

Three kinds of installation exist:

* installed - by install.ps1, under %LOCALAPPDATA%\\PersonalJobAgent\\app with
  an install.json next to it. Updating downloads the release and swaps the
  app folder; the private Python environment and your data stay.
* git - a clone of the repository (a developer checkout). Updating runs
  git pull --ff-only, and only when the checkout has no local changes.
* source - an unzipped copy without either; it is updated by reinstalling.

The update itself runs in install.ps1, started as a separate process: it
waits for this app to close, replaces files the app cannot replace while it
runs, and reopens the app (the previous version if anything fails). Your
data folder is never touched. Checking sends one anonymous request to
GitHub's public API; nothing about you or your data is sent.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from . import __version__

REPOSITORY = "wgKaminari/Local-AI-Agent-manager"
LATEST_RELEASE_URL = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPOSITORY}/releases"
INSTALL_COMMAND = (f"irm https://raw.githubusercontent.com/{REPOSITORY}/main/install.ps1 | iex")


def checkout() -> Path:
    """The folder holding run.py and src/ (the app folder of an installation)."""
    return Path(__file__).resolve().parents[2]


def installation() -> dict:
    folder = checkout()
    if folder.name == "app" and (folder.parent / "install.json").is_file():
        return {"kind": "installed", "folder": str(folder), "root": str(folder.parent)}
    if (folder / ".git").exists():
        return {"kind": "git", "folder": str(folder)}
    return {"kind": "source", "folder": str(folder)}


def parse_version(text: str) -> tuple[int, ...] | None:
    match = re.fullmatch(r"v?(\d+)\.(\d+)(?:\.(\d+))?", str(text or "").strip())
    return tuple(int(part or 0) for part in match.groups()) if match else None


def _git(folder: str, *arguments: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", folder, *arguments], capture_output=True, text=True, timeout=30,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def git_blocker(folder: str) -> str:
    """Why a checkout cannot be updated automatically, or '' when it can."""
    if shutil.which("git") is None:
        return "git is not installed, so this project folder cannot be updated automatically."
    try:
        changes = _git(folder, "status", "--porcelain", "--untracked-files=no")
        upstream = _git(folder, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    except (OSError, subprocess.SubprocessError):
        return "git could not inspect this project folder."
    if changes.returncode or upstream.returncode:
        return "This project folder has no remote branch to update from."
    if changes.stdout.strip():
        return "This project folder has local changes. Commit or discard them, then update with git pull."
    return ""


def check() -> dict:
    """The current and latest versions, and whether this copy can update itself."""
    from .sources import SourceError, _fetch
    info = installation()
    result = {"current": __version__, "latest": None, "newer": False, "notes": "", "url": RELEASES_PAGE,
              "kind": info["kind"], "can_update": False, "reason": "", "install_command": INSTALL_COMMAND}
    try:
        _, _, body, _ = _fetch(LATEST_RELEASE_URL, allow_missing=True, headers={"Accept": "application/vnd.github+json"})
        release = json.loads(body) if body else {}
    except (SourceError, ValueError) as exc:
        raise ValueError(f"Could not reach GitHub to check for updates: {exc}") from exc
    tag = release.get("tag_name") if isinstance(release, dict) else None
    if not tag or parse_version(tag) is None:
        result["reason"] = "No release has been published yet."
        return result
    result.update(latest=tag.lstrip("v"), tag=tag, notes=str(release.get("body") or "")[:4000],
                  url=str(release.get("html_url") or RELEASES_PAGE))
    result["newer"] = parse_version(tag) > (parse_version(__version__) or (0, 0, 0))
    if not result["newer"]:
        result["reason"] = "You have the latest version."
    elif sys.platform != "win32":
        result["reason"] = "Automatic updates are available on Windows; download the release from GitHub."
    elif info["kind"] == "source":
        result["reason"] = "This copy was not installed with the installer. Install it once with the command shown, then it updates itself."
    elif info["kind"] == "git" and (blocker := git_blocker(info["folder"])):
        result["reason"] = blocker
    else:
        result["can_update"] = True
    return result


def start_update(tag: str) -> dict:
    """Start install.ps1 in its own window; the caller then closes the app."""
    if sys.platform != "win32":
        raise ValueError("Automatic updates are available on Windows only.")
    if parse_version(tag) is None:
        raise ValueError("Choose a release version to install.")
    info = installation()
    script = Path(info["folder"]) / "install.ps1"
    if not script.is_file():
        raise ValueError("The installer is missing from this copy; reinstall with the command from GitHub.")
    # The app folder is replaced during the update, so the installer runs
    # from a temporary copy.
    folder = Path(tempfile.mkdtemp(prefix="job-agent-update-"))
    runner = folder / "install.ps1"
    shutil.copyfile(script, runner)
    command = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(runner),
               "-WaitPid", str(os.getpid())]
    if info["kind"] == "installed":
        command += ["-InstallRoot", info["root"], "-Tag", tag]
    elif info["kind"] == "git":
        if blocker := git_blocker(info["folder"]):
            raise ValueError(blocker)
        python = Path(sys.executable)
        console_python = python.with_name("python.exe") if python.name.lower() == "pythonw.exe" else python
        command += ["-Checkout", info["folder"], "-Python", str(console_python)]
    else:
        raise ValueError("This copy was not installed with the installer; install it once with the command from GitHub.")
    subprocess.Popen(command, cwd=str(folder), creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0), close_fds=True)
    return {"started": True, "kind": info["kind"], "tag": tag}
