"""Make the desktop window behave like an installed Linux application.

The Linux counterpart of windows_integration, which calls into this module
when it runs on Linux:

* an application menu entry and a desktop launcher (freedesktop .desktop
  files) with the app's own icon, so it can be added to favourites or the
  dock like any other program, and the window groups under that entry;
* one window per data folder: an flock() on a lock file, and a small Unix
  socket that lets a second start bring the open window to the front;
* API keys and the Gmail token kept in the login keyring (GNOME Keyring,
  KWallet or any Secret Service) through secret-tool;
* free memory from /proc/meminfo, for the local AI speed advice.

Nothing here needs root, and every file it writes lives under the user's
own XDG folders.
"""
from __future__ import annotations

import base64
import hashlib
import os
import shutil
import socket
import subprocess
import threading
from pathlib import Path

APP_NAME = "Personal Job Agent"
# The .desktop file name doubles as the Wayland app_id and X11 WM_CLASS, which
# is how the dock matches the open window to its launcher and icon.
DESKTOP_ID = "personal-job-agent"
DESKTOP_FILE = DESKTOP_ID + ".desktop"
KEYRING_APPLICATION = "personal-job-agent"

_lock_handle = None
_listener: socket.socket | None = None
_focus_callback = None


# ---------------------------------------------------------------- folders

def _home() -> Path:
    return Path.home()


def data_home() -> Path:
    value = os.environ.get("XDG_DATA_HOME", "")
    return Path(value) if os.path.isabs(value) else _home() / ".local" / "share"


def applications_dir() -> Path:
    return data_home() / "applications"


def icon_target() -> Path:
    return data_home() / "icons" / "hicolor" / "512x512" / "apps" / (DESKTOP_ID + ".png")


def desktop_dir() -> Path | None:
    """The user's desktop folder, or None when the session has none."""
    try:
        found = subprocess.run(["xdg-user-dir", "DESKTOP"], capture_output=True, text=True, timeout=5)
        candidate = Path(found.stdout.strip()) if found.returncode == 0 and found.stdout.strip() else None
    except (OSError, subprocess.SubprocessError):
        candidate = None
    # xdg-user-dir answers $HOME when no desktop folder is configured.
    if candidate is None or candidate == _home():
        candidate = _home() / "Desktop"
    return candidate if candidate.is_dir() else None


def runtime_dir() -> Path:
    value = os.environ.get("XDG_RUNTIME_DIR", "")
    if value and os.path.isdir(value) and os.access(value, os.W_OK):
        return Path(value)
    fallback = Path("/tmp") / f"personal-job-agent-{os.getuid()}"
    fallback.mkdir(mode=0o700, exist_ok=True)
    return fallback


def png_icon() -> Path:
    return Path(__file__).resolve().parent / "web" / "icon-512.png"


# ---------------------------------------------------------------- identity

def set_app_id() -> None:
    """Name the process after the .desktop entry so docks show the right icon."""
    try:
        import gi
        from gi.repository import GLib
    except Exception:
        return  # Qt or no GUI toolkit yet; StartupWMClass still matches by title.
    try:
        GLib.set_prgname(DESKTOP_ID)
        GLib.set_application_name(APP_NAME)
    except Exception:
        pass


def set_title_bar_theme(dark: bool) -> None:
    """Ask GTK for its dark variant so the title bar follows the app's theme."""
    import sys
    if "gi.repository.Gtk" not in sys.modules:
        return
    try:
        from gi.repository import GLib, Gtk

        def apply():
            settings = Gtk.Settings.get_default()
            if settings is not None:
                settings.set_property("gtk-application-prefer-dark-theme", bool(dark))
            return False

        GLib.idle_add(apply)
    except Exception:
        pass


# ---------------------------------------------------------------- single instance

def _instance_paths(data_dir: Path) -> tuple[Path, Path]:
    digest = hashlib.sha256(str(Path(data_dir).resolve()).encode()).hexdigest()[:16]
    folder = runtime_dir()
    return folder / f"personal-job-agent-{digest}.lock", folder / f"personal-job-agent-{digest}.sock"


def claim_single_instance(data_dir: Path) -> bool:
    """Return False, after asking the open window to come forward, if one runs.

    The lock is released by the kernel when the process exits, even after a
    crash, so a stale lock file never blocks the next start.
    """
    import fcntl
    global _lock_handle
    lock_path, socket_path = _instance_paths(data_dir)
    handle = open(lock_path, "a+")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        focus_existing_window(data_dir)
        return False
    _lock_handle = handle  # Held for the lifetime of the process.
    _listen_for_focus(socket_path)
    return True


def release_single_instance() -> None:
    """Drop the lock and the focus socket (tests; the OS does it at exit)."""
    global _lock_handle, _listener
    if _listener is not None:
        path = _listener.getsockname()
        try:
            _listener.close()
        finally:
            _listener = None
            if isinstance(path, str) and path:
                Path(path).unlink(missing_ok=True)
    if _lock_handle is not None:
        _lock_handle.close()
        _lock_handle = None


def on_focus_request(callback) -> None:
    """Run callback when a second start asks this window to come forward."""
    global _focus_callback
    _focus_callback = callback


def _listen_for_focus(path: Path) -> None:
    global _listener
    path.unlink(missing_ok=True)  # We hold the lock, so any old socket is stale.
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        server.bind(str(path))
        os.chmod(path, 0o600)
        server.listen(2)
    except OSError:
        server.close()
        return  # Focusing is a convenience; the lock alone keeps one window.
    _listener = server

    def serve():
        while True:
            try:
                connection, _ = server.accept()
            except OSError:
                return
            with connection:
                connection.settimeout(2)
                try:
                    request = connection.recv(16)
                except OSError:
                    continue
            if request.startswith(b"focus") and _focus_callback is not None:
                try:
                    _focus_callback()
                except Exception:
                    pass

    threading.Thread(target=serve, name="single-instance", daemon=True).start()


def focus_existing_window(data_dir: Path | None = None) -> bool:
    if data_dir is None:
        return False
    _, socket_path = _instance_paths(data_dir)
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(2)
            client.connect(str(socket_path))
            client.sendall(b"focus")
        return True
    except OSError:
        return False


# ---------------------------------------------------------------- launchers

def _exec_quote(argument: str) -> str:
    """Quote one Exec= argument per the Desktop Entry Specification."""
    if argument and not any(character in argument for character in ' \t\n"\'\\><~|&;$*?#()`%'):
        return argument
    escaped = "".join("\\" + character if character in '"`$\\' else character for character in argument)
    return '"' + escaped.replace("%", "%%") + '"'


def launch_command() -> tuple[str, list[str], str]:
    """Python, arguments and working folder that open the app."""
    import sys
    python = sys.executable
    checkout = Path(__file__).resolve().parents[2]
    if (checkout / "run.py").is_file():
        return python, [str(checkout / "run.py"), "gui"], str(checkout)
    return python, ["-m", "norway_job_agent", "gui"], str(_home())


def desktop_entry() -> str:
    target, arguments, working_dir = launch_command()
    command = " ".join(_exec_quote(part) for part in [target, *arguments])
    return "\n".join([
        "[Desktop Entry]",
        "Type=Application",
        "Version=1.5",
        f"Name={APP_NAME}",
        "GenericName=Job search assistant",
        "Comment=Find vacancies and prepare applications on this computer.",
        f"Exec={command}",
        f"Path={working_dir}",
        f"Icon={DESKTOP_ID}",
        "Terminal=false",
        "Categories=Office;Utility;",
        "Keywords=jobs;vacancies;applications;cover letter;career;",
        f"StartupWMClass={DESKTOP_ID}",
        "StartupNotify=true",
        "",
    ])


def _write_launcher(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name("." + path.name + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    os.chmod(temporary, 0o755)  # Desktop launchers must be executable to run.
    os.replace(temporary, path)


def _refresh_menus() -> None:
    for command in (["update-desktop-database", str(applications_dir())],
                    ["gtk-update-icon-cache", "-q", "-t", str(data_home() / "icons" / "hicolor")]):
        if shutil.which(command[0]):
            try:
                subprocess.run(command, capture_output=True, timeout=30)
            except (OSError, subprocess.SubprocessError):
                pass


def _trust(path: Path) -> None:
    # GNOME asks before running desktop launchers it was not told to trust.
    if shutil.which("gio"):
        try:
            subprocess.run(["gio", "set", str(path), "metadata::trusted", "true"], capture_output=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            pass


def create_shortcuts(*, start_menu: bool = True, desktop: bool = True, folder: Path | None = None) -> list[str]:
    """Add the app to the applications menu and the desktop.

    folder replaces both locations (used by tests). Existing launchers are
    replaced, so running this again after moving the project repairs them.
    """
    content = desktop_entry()
    icon = icon_target()
    icon.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(png_icon(), icon)
    targets = []
    if folder is not None:
        targets.append(Path(folder))
    else:
        if start_menu:
            targets.append(applications_dir())
        if desktop and (found := desktop_dir()) is not None:
            targets.append(found)
    created = []
    for target in targets:
        path = target / DESKTOP_FILE
        _write_launcher(path, content)
        if folder is None and target != applications_dir():
            _trust(path)
        created.append(str(path))
    if folder is None:
        _refresh_menus()
    return created


def remove_shortcuts() -> None:
    for target in (applications_dir(), desktop_dir()):
        if target is not None:
            (target / DESKTOP_FILE).unlink(missing_ok=True)
    icon_target().unlink(missing_ok=True)
    _refresh_menus()


# ---------------------------------------------------------------- keyring

class KeyringUnavailable(RuntimeError):
    pass


def keyring_available() -> bool:
    # JOB_AGENT_KEYRING=off keeps secrets in 0600 files (tests, headless use).
    if os.environ.get("JOB_AGENT_KEYRING", "").lower() in {"off", "0", "no", "false"}:
        return False
    return shutil.which("secret-tool") is not None and bool(
        os.environ.get("DBUS_SESSION_BUS_ADDRESS") or os.environ.get("XDG_RUNTIME_DIR"))


def _attributes(data_dir: Path, item: str) -> list[str]:
    account = hashlib.sha256(str(Path(data_dir).expanduser().resolve()).encode()).hexdigest()[:16]
    return ["application", KEYRING_APPLICATION, "account", account, "item", item]


def keyring_store(data_dir: Path, item: str, data: bytes, label: str) -> dict:
    """Save data in the login keyring; return the reference kept on disk."""
    if not keyring_available():
        raise KeyringUnavailable("No login keyring (secret-tool) is available.")
    try:
        process = subprocess.run(["secret-tool", "store", "--label", label, *_attributes(data_dir, item)],
                                 input=base64.b64encode(data), capture_output=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:
        raise KeyringUnavailable(str(exc)) from None
    if process.returncode:
        raise KeyringUnavailable(process.stderr.decode("utf-8", "replace").strip()[:200] or "secret-tool failed")
    return {"protection": "linux-keyring", "item": item}


def keyring_lookup(data_dir: Path, item: str) -> bytes:
    if shutil.which("secret-tool") is None:
        raise KeyringUnavailable("secret-tool is not installed.")
    try:
        process = subprocess.run(["secret-tool", "lookup", *_attributes(data_dir, item)],
                                 capture_output=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:
        raise KeyringUnavailable(str(exc)) from None
    if process.returncode or not process.stdout.strip():
        raise KeyringUnavailable("The secret is not in the login keyring.")
    return base64.b64decode(process.stdout.strip(), validate=True)


def keyring_clear(data_dir: Path, item: str) -> None:
    if shutil.which("secret-tool") is None:
        return
    try:
        subprocess.run(["secret-tool", "clear", *_attributes(data_dir, item)], capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        pass


# ---------------------------------------------------------------- memory

def memory_available_gb() -> float | None:
    try:
        with open("/proc/meminfo", encoding="ascii") as stream:
            for line in stream:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) / 1024 ** 2
    except (OSError, ValueError, IndexError):
        return None
    return None
