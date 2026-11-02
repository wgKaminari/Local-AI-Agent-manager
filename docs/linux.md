# Personal Job Agent on Linux

From version 0.10 the app runs on Linux as a desktop application, with the same features and the same data layout as on Windows. Python does the work (collection, matching, the database, AI, Gmail); the interface is the same bundled HTML, CSS and JavaScript, shown in a native window by pywebview.

Tested on Fedora 44 (KDE Plasma, Wayland). It should work on any current distribution with Python 3.12 or newer: Fedora, Ubuntu 24.04 and later, Debian 13, Arch, openSUSE Tumbleweed.

## Install

Open a terminal and run:

```bash
curl -fsSL https://raw.githubusercontent.com/wgKaminari/Local-AI-Agent-manager/main/install.sh | bash
```

The installer:

1. downloads the latest release into `~/.local/share/PersonalJobAgent/app`;
2. creates a private Python environment in `~/.local/share/PersonalJobAgent/venv` (it needs Python 3.12+ and offers to install it with your package manager when missing);
3. chooses a window toolkit (see below);
4. adds **Personal Job Agent** to the applications menu and, if you have one, the desktop;
5. offers to install Ollama, the free local AI, with its official installer.

It needs no root, except for the package installs it asks about. Your data in `~/.norway-job-agent` is never changed by installing or updating, so a profile copied from Windows is picked up as it is (see [Moving from Windows](#moving-from-windows)).

Prefer to read the script first? Download [install.sh](../install.sh), review it, and run `bash install.sh`. `bash install.sh --help` lists the options (a specific `--tag`, another `--install-root`, `--gui gtk|qt`, `--no-shortcuts`, `--no-launch`, `--quiet`).

### Window toolkit: GTK or Qt

pywebview needs one of two web engines:

- **WebKitGTK** (small, from your distribution). The installer uses it when it is installed and otherwise offers to install it:

  | Distribution | Packages |
  |---|---|
  | Fedora | `sudo dnf install python3-gobject gtk3 webkit2gtk4.1` |
  | Ubuntu / Debian | `sudo apt install python3-gi gir1.2-gtk-3.0 gir1.2-webkit2-4.1` |
  | Arch | `sudo pacman -S python-gobject gtk3 webkit2gtk-4.1` |
  | openSUSE | `sudo zypper install python3-gobject-Gdk typelib-1_0-Gtk-3_0 typelib-1_0-WebKit2-4_1` |

  The private environment is then created with access to the system's PyGObject, so it uses your distribution's `python3`.

- **Qt WebEngine** (about 200 MB, from PyPI, no root). Used when WebKitGTK is not available or you decline it. On KDE Plasma pywebview prefers Qt when both are installed.

The choice is saved in `~/.local/share/PersonalJobAgent/venv/gui` and kept on updates. To switch, remove the `venv` folder and run the installer again with `--gui gtk` or `--gui qt`.

## Update

As on Windows, the app checks GitHub for a newer release once per start. **Settings & activity → Updates → Update now** closes the app, runs `install.sh` in the background (its output goes to `~/.local/share/PersonalJobAgent/update.log`), switches to the new version and reopens the app. If anything fails, the version you had opens again. Running the install command again also updates.

A `git clone` updates itself with `git pull --ff-only`, and only when it has no local changes.

## Run from source

```bash
git clone https://github.com/wgKaminari/Local-AI-Agent-manager.git
cd Local-AI-Agent-manager
python3 -m venv --system-site-packages .venv   # --system-site-packages: use the system's GTK bindings
.venv/bin/pip install -r requirements.txt
.venv/bin/python run.py gui
```

Without WebKitGTK, add Qt instead: `.venv/bin/pip install "pywebview[qt]"`. `./start-personal-job-agent.sh` opens the app without keeping the terminal busy (set `PYTHON=.venv/bin/python`), and `python run.py shortcuts` adds it to the applications menu and desktop (`--remove` removes them). The same option is under **Settings & activity → Desktop app**.

## What is different from Windows

| | Windows | Linux |
|---|---|---|
| Install | `install.ps1` in PowerShell | `install.sh` in a terminal |
| App folder | `%LOCALAPPDATA%\PersonalJobAgent` | `~/.local/share/PersonalJobAgent` (`$XDG_DATA_HOME`) |
| Your data | `%USERPROFILE%\.norway-job-agent` | `~/.norway-job-agent` |
| Window | WebView2 | WebKitGTK or Qt WebEngine |
| Shortcuts | Start menu and desktop `.lnk` | `~/.local/share/applications/personal-job-agent.desktop` and a desktop launcher |
| One window per data folder | a named mutex | a lock file in `$XDG_RUNTIME_DIR`; starting the app again brings the open window forward |
| API keys, Gmail token | encrypted for your Windows account (DPAPI) | your login keyring (GNOME Keyring, KWallet or another Secret Service), through `secret-tool` |
| Local AI | Ollama for Windows | Ollama for Linux (`curl -fsSL https://ollama.com/install.sh \| sh`) |

### API keys and Gmail credentials

On Linux the API keys and the Gmail refresh token are stored in your login keyring; `ai-keys.json` and `gmail-token.json` in the data folder then only name the keyring entry ("Personal Job Agent: …" in Seahorse or KWalletManager). This needs `secret-tool`:

- Fedora: `sudo dnf install libsecret` (usually present);
- Ubuntu / Debian: `sudo apt install libsecret-tools`;
- Arch: `sudo pacman -S libsecret`.

Without a keyring (for example a minimal window manager without a Secret Service), the secrets are kept in those files with permissions `600`, readable only by you. Set `JOB_AGENT_KEYRING=off` to choose that on purpose. Credentials saved on Windows cannot be read on Linux (they are encrypted for the Windows account): add the keys and connect Gmail again on Linux.

### Local AI (Ollama)

Install Ollama with its official script, which also sets up a systemd service:

```bash
curl -fsSL https://ollama.com/install.sh | sh
```

The app starts `ollama serve` itself when the service is not running. Then use **Settings & activity → Download saved model** as on Windows. Ollama uses an NVIDIA or AMD GPU automatically when its drivers are installed.

### Phone access

The phone connects to your PC on port 8765 (or the next free one, shown in **Phone access**). Desktop distributions often block incoming connections:

- Fedora (firewalld): `sudo firewall-cmd --add-port=8765/tcp` (add `--permanent` to keep it after a restart);
- Ubuntu (ufw, when enabled): `sudo ufw allow 8765/tcp`.

## Moving from Windows

Copy `%USERPROFILE%\.norway-job-agent` from Windows to `~/.norway-job-agent` on Linux while the app is closed on both (include `vacancies.db-wal` and `vacancies.db-shm` if present). Profile, vacancies, notes, drafts and settings carry over. API keys and the Gmail connection must be added again (see above).

## Troubleshooting

- **The window does not open.** Run `~/.local/share/PersonalJobAgent/venv/bin/python ~/.local/share/PersonalJobAgent/app/run.py gui` in a terminal to see the error. "You must have either QT or GTK" means neither toolkit is available: install the WebKitGTK packages above, or `~/.local/share/PersonalJobAgent/venv/bin/pip install "pywebview[qt]"`.
- **A GTK window with WebKitGTK is blank on an NVIDIA GPU.** Start it with `WEBKIT_DISABLE_DMABUF_RENDERER=1`, or use Qt.
- **The menu entry does not appear.** Some desktops only reread `~/.local/share/applications` after logging out and in again.
- **The app says it cannot unlock the keyring.** Unlock the login keyring (it unlocks with your login password on most desktops), or remove the key in Settings and add it again.
- **Starting the app again shows nothing new.** One window per data folder is allowed; the open one is brought forward (some Wayland desktops only highlight it in the taskbar). Use `--data-dir` for a separate profile.
