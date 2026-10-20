"""Make the desktop window behave like an installed Windows application.

Without an explicit identity Windows treats the app as "Python": it borrows
pythonw.exe's icon, groups it with other Python programs and pins python
itself. Here the process gets its own AppUserModelID, the Start menu shortcut
carries the same ID (so the taskbar, pinning and jump lists resolve to it),
only one window per data folder opens, and the title bar follows the app's
light/dark theme. Every function is a harmless no-op off Windows.
"""
from __future__ import annotations

import ctypes
import hashlib
import os
import subprocess
import sys
import tempfile
from pathlib import Path

APP_ID = "Kaminari.PersonalJobAgent"
APP_NAME = "Personal Job Agent"
WINDOW_TITLE = APP_NAME
SHORTCUT_NAME = APP_NAME + ".lnk"
_IS_WINDOWS = sys.platform == "win32"
_mutex = None


def icon_path() -> Path:
    return Path(__file__).resolve().parent / "assets" / "job-agent.ico"


def set_app_id() -> None:
    """Give this process its own taskbar identity instead of Python's."""
    if _IS_WINDOWS:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)


def claim_single_instance(data_dir: Path) -> bool:
    """Return False, after focusing the open window, if the app already runs.

    One window per private data folder: two would edit the same profile and
    database. A different --data-dir may still open its own window.
    """
    global _mutex
    if not _IS_WINDOWS:
        return True
    digest = hashlib.sha256(str(Path(data_dir).resolve()).casefold().encode()).hexdigest()[:16]
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    handle = kernel32.CreateMutexW(None, False, f"Local\\PersonalJobAgent-{digest}")
    if handle and kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        kernel32.CloseHandle(ctypes.c_void_p(handle))
        focus_existing_window()
        return False
    _mutex = handle  # Held for the lifetime of the process.
    return True


def _windows_with_title(title: str, own_process: bool) -> list[int]:
    user32 = ctypes.windll.user32
    found = []
    pid = os.getpid()

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def visit(hwnd, _):
        length = user32.GetWindowTextLengthW(ctypes.c_void_p(hwnd))
        if length and user32.IsWindowVisible(ctypes.c_void_p(hwnd)):
            buffer = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(ctypes.c_void_p(hwnd), buffer, length + 1)
            owner = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(ctypes.c_void_p(hwnd), ctypes.byref(owner))
            if buffer.value.startswith(title) and (owner.value == pid) == own_process:
                found.append(hwnd)
        return True

    user32.EnumWindows(visit, None)
    return found


def focus_existing_window() -> None:
    if not _IS_WINDOWS:
        return
    user32 = ctypes.windll.user32
    for hwnd in _windows_with_title(WINDOW_TITLE, own_process=False):
        if user32.IsIconic(ctypes.c_void_p(hwnd)):
            user32.ShowWindow(ctypes.c_void_p(hwnd), 9)  # SW_RESTORE
        user32.SetForegroundWindow(ctypes.c_void_p(hwnd))
        return


def set_title_bar_theme(dark: bool) -> None:
    """Dark or light native title bar for this process's app window."""
    if not _IS_WINDOWS:
        return
    value = ctypes.c_int(1 if dark else 0)
    for hwnd in _windows_with_title(WINDOW_TITLE, own_process=True):
        # 20 = DWMWA_USE_IMMERSIVE_DARK_MODE (Windows 10 20H1 and later).
        ctypes.windll.dwmapi.DwmSetWindowAttribute(ctypes.c_void_p(hwnd), 20, ctypes.byref(value), 4)


def launch_command() -> tuple[str, str, str]:
    """Target, arguments and working folder that open the app without a console."""
    python = Path(sys.executable)
    windowed = python.with_name("pythonw.exe")
    target = str(windowed if windowed.is_file() else python)
    checkout = Path(__file__).resolve().parents[2]
    if (checkout / "run.py").is_file():
        return target, f'"{checkout / "run.py"}" gui', str(checkout)
    return target, "-m norway_job_agent gui", str(Path.home())


# IShellLink cannot set a shortcut's AppUserModelID; IPropertyStore can. The
# script receives every path through environment variables, never by
# interpolating text into code.
_SHORTCUT_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
using System.Runtime.InteropServices.ComTypes;
using System.Text;
[ComImport, Guid("000214F9-0000-0000-C000-000000000046"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IShellLinkW {
  void GetPath([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder f, int c, IntPtr d, uint fl);
  void GetIDList(out IntPtr p); void SetIDList(IntPtr p);
  void GetDescription([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder n, int c);
  void SetDescription([MarshalAs(UnmanagedType.LPWStr)] string n);
  void GetWorkingDirectory([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder d, int c);
  void SetWorkingDirectory([MarshalAs(UnmanagedType.LPWStr)] string d);
  void GetArguments([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder a, int c);
  void SetArguments([MarshalAs(UnmanagedType.LPWStr)] string a);
  void GetHotkey(out short h); void SetHotkey(short h);
  void GetShowCmd(out int s); void SetShowCmd(int s);
  void GetIconLocation([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder p, int c, out int i);
  void SetIconLocation([MarshalAs(UnmanagedType.LPWStr)] string p, int i);
  void SetRelativePath([MarshalAs(UnmanagedType.LPWStr)] string p, uint r);
  void Resolve(IntPtr h, uint f);
  void SetPath([MarshalAs(UnmanagedType.LPWStr)] string f);
}
[ComImport, Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IPropertyStore {
  void GetCount(out uint c); void GetAt(uint i, out PropertyKey k);
  void GetValue(ref PropertyKey k, out PropVariant v);
  void SetValue(ref PropertyKey k, ref PropVariant v); void Commit();
}
[StructLayout(LayoutKind.Sequential, Pack = 4)] public struct PropertyKey { public Guid fmtid; public uint pid; }
[StructLayout(LayoutKind.Explicit)] public struct PropVariant { [FieldOffset(0)] public ushort vt; [FieldOffset(8)] public IntPtr p; }
[ComImport, Guid("00021401-0000-0000-C000-000000000046")] class CShellLink {}
public static class JobAgentShortcut {
  public static void Create(string lnk, string target, string args, string dir, string icon, string appId, string about) {
    var link = (IShellLinkW)new CShellLink();
    link.SetPath(target); link.SetArguments(args); link.SetWorkingDirectory(dir);
    link.SetIconLocation(icon, 0); link.SetDescription(about);
    var key = new PropertyKey { fmtid = new Guid("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3"), pid = 5 };
    var value = new PropVariant { vt = 31, p = Marshal.StringToCoTaskMemUni(appId) };
    try { var store = (IPropertyStore)link; store.SetValue(ref key, ref value); store.Commit(); }
    finally { Marshal.FreeCoTaskMem(value.p); }
    ((IPersistFile)link).Save(lnk, true);
  }
}
'@
$folders = @()
if ($env:JA_START -eq '1') { $folders += [Environment]::GetFolderPath('Programs') }
if ($env:JA_DESKTOP -eq '1') { $folders += [Environment]::GetFolderPath('Desktop') }
if ($env:JA_FOLDER) { $folders = @($env:JA_FOLDER) }
foreach ($folder in $folders) {
  $lnk = Join-Path $folder $env:JA_NAME
  if ($env:JA_REMOVE -eq '1') { Remove-Item -LiteralPath $lnk -ErrorAction SilentlyContinue; continue }
  [JobAgentShortcut]::Create($lnk, $env:JA_TARGET, $env:JA_ARGS, $env:JA_DIR, $env:JA_ICON, $env:JA_APPID, $env:JA_ABOUT)
  # Read the identity back through the Shell, as the taskbar will.
  $item = (New-Object -ComObject Shell.Application).Namespace($folder).ParseName($env:JA_NAME)
  if ($item.ExtendedProperty('System.AppUserModel.ID') -ne $env:JA_APPID) { throw "The shortcut identity could not be verified: $lnk" }
  Write-Output $lnk
}
"""


def _run_shortcut_script(*, start_menu: bool, desktop: bool, remove: bool, folder: Path | None = None) -> list[str]:
    if not _IS_WINDOWS:
        raise RuntimeError("Shortcuts are available on Windows only.")
    target, arguments, working_dir = launch_command()
    environment = {**os.environ, "JA_START": "1" if start_menu else "0", "JA_DESKTOP": "1" if desktop else "0",
                   "JA_REMOVE": "1" if remove else "0", "JA_NAME": SHORTCUT_NAME, "JA_TARGET": target,
                   "JA_ARGS": arguments, "JA_DIR": working_dir, "JA_ICON": str(icon_path()), "JA_APPID": APP_ID,
                   "JA_ABOUT": "Find vacancies and prepare applications on this computer.",
                   "JA_FOLDER": str(folder) if folder else ""}
    # A script file, not stdin: piped input is read line by line and would
    # split the multi-line C# definition.
    with tempfile.TemporaryDirectory(prefix="job-agent-shortcut-") as directory:
        script = Path(directory) / "shortcuts.ps1"
        script.write_text(_SHORTCUT_SCRIPT, encoding="utf-8-sig")
        process = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(script)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", env=environment, timeout=120,
            creationflags=subprocess.CREATE_NO_WINDOW)
    if process.returncode:
        raise RuntimeError("Windows could not update the shortcuts. " + (process.stderr.strip().splitlines() or [""])[-1][:300])
    return [line.strip() for line in process.stdout.splitlines() if line.strip()]


def create_shortcuts(*, start_menu: bool = True, desktop: bool = True, folder: Path | None = None) -> list[str]:
    """Create Start menu and desktop shortcuts that open the app without a console.

    folder replaces both locations (used by tests). Existing shortcuts are
    replaced, so running this again after moving the project repairs them.
    """
    return _run_shortcut_script(start_menu=start_menu, desktop=desktop, remove=False, folder=folder)


def remove_shortcuts() -> None:
    _run_shortcut_script(start_menu=True, desktop=True, remove=True)
