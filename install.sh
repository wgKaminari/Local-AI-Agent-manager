#!/usr/bin/env bash
# Install or update Personal Job Agent on Linux.
#
# First install (any terminal):
#     curl -fsSL https://raw.githubusercontent.com/wgKaminari/Local-AI-Agent-manager/main/install.sh | bash
#
# The app is installed to ~/.local/share/PersonalJobAgent (app + a private
# Python environment) with an applications-menu entry and a desktop launcher.
# Your profile, vacancies and drafts stay in ~/.norway-job-agent and are never
# changed by installing or updating.
#
# The app itself runs this script to update: it waits for the app to close,
# installs the new version next to the old one, switches over, and reopens
# the app. If anything fails, the previous version is kept and reopened.
#
# A developer checkout (a git clone) is updated with git pull instead.
#
# Options:
#   --tag vX.Y.Z        Release to install (default: the latest release)
#   --install-root DIR  Where to install (default: ~/.local/share/PersonalJobAgent)
#   --wait-pid PID      Wait for this process (the running app) before switching
#   --checkout DIR      Update this git checkout with git pull instead
#   --python PATH       Python that runs the checkout (with --checkout)
#   --source-zip FILE   Install from a local source zip (tests, offline)
#   --gui gtk|qt|none   Desktop window toolkit (default: GTK if present, else Qt)
#   --no-shortcuts      Do not add menu and desktop launchers
#   --no-launch         Do not open the app at the end
#   --quiet             No questions (automated runs and the app's updater)

set -euo pipefail

REPO="wgKaminari/Local-AI-Agent-manager"
USER_AGENT="PersonalJobAgent-installer"
TAG=""
INSTALL_ROOT="${XDG_DATA_HOME:-$HOME/.local/share}/PersonalJobAgent"
WAIT_PID=0
CHECKOUT=""
PYTHON_ARG=""
SOURCE_ZIP=""
GUI="auto"
NO_SHORTCUTS=0
NO_LAUNCH=0
QUIET=0

while [ $# -gt 0 ]; do
    case "$1" in
        --tag) TAG="$2"; shift 2 ;;
        --install-root) INSTALL_ROOT="$2"; shift 2 ;;
        --wait-pid) WAIT_PID="$2"; shift 2 ;;
        --checkout) CHECKOUT="$2"; shift 2 ;;
        --python) PYTHON_ARG="$2"; shift 2 ;;
        --source-zip) SOURCE_ZIP="$2"; shift 2 ;;
        --gui) GUI="$2"; shift 2 ;;
        --no-shortcuts) NO_SHORTCUTS=1; shift ;;
        --no-launch) NO_LAUNCH=1; shift ;;
        --quiet) QUIET=1; shift ;;
        -h|--help) sed -n '2,29p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "Unknown option: $1 (see --help)" >&2; exit 2 ;;
    esac
done

case "$GUI" in auto|gtk|qt|none) ;; *) echo "--gui must be gtk, qt or none" >&2; exit 2 ;; esac
if [ -n "$TAG" ] && ! [[ "$TAG" =~ ^v?[0-9]+\.[0-9]+(\.[0-9]+)?$ ]]; then
    echo "--tag must be a release version such as v0.10.0" >&2; exit 2
fi

if [ -t 1 ]; then BOLD=$'\e[1;36m'; WARN=$'\e[1;33m'; ERR=$'\e[1;31m'; OK=$'\e[1;32m'; RESET=$'\e[0m'
else BOLD=""; WARN=""; ERR=""; OK=""; RESET=""; fi
step() { printf '%s==> %s%s\n' "$BOLD" "$1" "$RESET"; }
note() { printf '    %s\n' "$1"; }
warn() { printf '%s%s%s\n' "$WARN" "$1" "$RESET"; }

# Questions read from the terminal, so they also work with "curl ... | bash".
ask() {
    [ "$QUIET" = 1 ] && return 1
    [ -r /dev/tty ] || return 1
    local answer
    { printf '%s [y/N] ' "$1" > /dev/tty; } 2>/dev/null || return 1
    read -r answer < /dev/tty || return 1
    [[ "$answer" =~ ^([yY]|[yY][eE][sS]|[jJ]|[jJ][aA])$ ]]
}

wait_for_app() {
    [ "$WAIT_PID" -gt 0 ] 2>/dev/null || return 0
    step "Waiting for Personal Job Agent to close"
    local waited=0
    while kill -0 "$WAIT_PID" 2>/dev/null; do
        sleep 0.5
        waited=$((waited + 1))
        if [ "$waited" -ge 120 ]; then
            echo "The app did not close within a minute. Close it and run the update again." >&2
            return 1
        fi
    done
}

python_ok() {
    "$@" -c 'import sys, venv; sys.exit(sys.version_info < (3, 12))' >/dev/null 2>&1
}

# The distribution's own python3 comes first: its PyGObject (the GTK window)
# is visible only to that interpreter.
find_python() {
    local candidate
    for candidate in python3 python3.14 python3.13 python3.12 python; do
        if command -v "$candidate" >/dev/null 2>&1 && python_ok "$candidate"; then
            command -v "$candidate"; return 0
        fi
    done
    return 1
}

package_manager() {
    local manager
    for manager in dnf apt-get pacman zypper; do
        command -v "$manager" >/dev/null 2>&1 && { echo "$manager"; return 0; }
    done
    return 1
}

# Packages for: Python with venv, and the GTK window (PyGObject + WebKitGTK).
python_packages() {
    case "$1" in
        dnf) echo "python3" ;;
        apt-get) echo "python3 python3-venv" ;;
        pacman) echo "python" ;;
        zypper) echo "python3" ;;
    esac
}
gtk_packages() {
    case "$1" in
        dnf) echo "python3-gobject gtk3 webkit2gtk4.1" ;;
        apt-get) echo "python3-gi gir1.2-gtk-3.0 gir1.2-webkit2-4.1" ;;
        pacman) echo "python-gobject gtk3 webkit2gtk-4.1" ;;
        zypper) echo "python3-gobject-Gdk typelib-1_0-Gtk-3_0 typelib-1_0-WebKit2-4_1" ;;
    esac
}
install_packages() {
    local manager="$1"; shift
    case "$manager" in
        dnf) sudo dnf install -y "$@" ;;
        apt-get) sudo apt-get update && sudo apt-get install -y "$@" ;;
        pacman) sudo pacman -S --needed --noconfirm "$@" ;;
        zypper) sudo zypper --non-interactive install "$@" ;;
    esac
}

require_python() {
    local python manager packages
    if python=$(find_python); then echo "$python"; return 0; fi
    warn "Python 3.12 or newer (with venv) is required and was not found." >&2
    if manager=$(package_manager); then
        packages=$(python_packages "$manager")
        if ask "Install it now with: sudo $manager install $packages ?"; then
            # shellcheck disable=SC2086
            install_packages "$manager" $packages >&2
            if python=$(find_python); then echo "$python"; return 0; fi
        fi
    fi
    echo "Install Python 3.12 or newer with your distribution's package manager (on Debian/Ubuntu also python3-venv), then run this installer again." >&2
    return 1
}

gtk_ready() {
    "$1" -c "
import gi
gi.require_version('Gtk', '3.0')
try:
    gi.require_version('WebKit2', '4.1')
except ValueError:
    gi.require_version('WebKit2', '4.0')
from gi.repository import Gtk, WebKit2
" >/dev/null 2>&1
}

# The window needs GTK with WebKitGTK (small; from the distribution) or Qt
# (about 200 MB from PyPI, no root needed). Decided once, for the venv.
choose_gui() {
    local base="$1" manager packages
    if [ "$GUI" = "auto" ]; then
        if gtk_ready "$base"; then GUI=gtk
        elif manager=$(package_manager) && packages=$(gtk_packages "$manager") \
                && ask "The app window needs WebKitGTK. Install it now with: sudo $manager install $packages ?"; then
            # shellcheck disable=SC2086
            if install_packages "$manager" $packages && gtk_ready "$base"; then GUI=gtk; else GUI=qt; fi
        else
            GUI=qt
        fi
    fi
    if [ "$GUI" = "gtk" ] && ! gtk_ready "$base"; then
        echo "--gui gtk needs PyGObject and WebKitGTK for $base; see docs/linux.md." >&2
        return 1
    fi
}

venv_gui() {
    # Which toolkit an existing environment was made with.
    if [ -f "$1/gui" ]; then cat "$1/gui"; else echo qt; fi
}

latest_tag() {
    local body
    body=$(curl -fsSL -H "User-Agent: $USER_AGENT" "https://api.github.com/repos/$REPO/releases/latest" 2>/dev/null) || return 0
    printf '%s' "$body" | sed -n 's/^[[:space:]]*"tag_name":[[:space:]]*"\([^"]*\)".*/\1/p' | head -n 1
}

# get_source DESTINATION PYTHON -> sets SOURCE_DIR and LABEL
get_source() {
    local destination="$1" python="$2" zip url
    mkdir -p "$destination"
    zip="$destination/source.zip"
    if [ -n "$SOURCE_ZIP" ]; then
        cp -- "$SOURCE_ZIP" "$zip"
        LABEL="${TAG:-local}"
    else
        command -v curl >/dev/null 2>&1 || { echo "curl is required to download the app." >&2; return 1; }
        LABEL="${TAG:-$(latest_tag)}"
        if [ -n "$LABEL" ]; then url="https://github.com/$REPO/archive/refs/tags/$LABEL.zip"
        else url="https://github.com/$REPO/archive/refs/heads/main.zip"; LABEL=main; fi
        step "Downloading Personal Job Agent $LABEL"
        curl -fsSL -H "User-Agent: $USER_AGENT" -o "$zip" "$url"
    fi
    if ! "$python" -m zipfile -e "$zip" "$destination/extracted" >/dev/null 2>&1; then
        echo "The download is damaged or incomplete. Check your internet connection and try again." >&2
        return 1
    fi
    SOURCE_DIR=$(find "$destination/extracted" -mindepth 1 -maxdepth 1 -type d | head -n 1)
    if [ -z "$SOURCE_DIR" ] || [ ! -f "$SOURCE_DIR/run.py" ]; then
        echo "The downloaded archive does not contain Personal Job Agent." >&2
        return 1
    fi
}

install_dependencies() {
    local python="$1" requirements="$2" gui="$3"
    step "Installing dependencies"
    "$python" -m pip install --disable-pip-version-check --quiet -r "$requirements"
    if [ "$gui" = "qt" ]; then
        note "Adding the Qt window toolkit (WebKitGTK is not available)."
        "$python" -m pip install --disable-pip-version-check --quiet "pywebview[qt]>=6,<7"
    fi
}

install_release() {
    local app="$INSTALL_ROOT/app" venv="$INSTALL_ROOT/venv" staging base old
    local venv_python="$venv/bin/python"
    staging="$INSTALL_ROOT/staging-$(date +%s)-$$"
    STAGING="$staging"
    mkdir -p "$INSTALL_ROOT"
    if [ -x "$venv_python" ]; then
        base="$venv_python"
    else
        base=$(require_python)
    fi
    get_source "$staging" "$base"
    if [ ! -x "$venv_python" ]; then
        choose_gui "$base"
        step "Creating a private Python environment"
        # GTK comes from the distribution, so that environment sees its packages.
        if [ "$GUI" = "gtk" ]; then "$base" -m venv --system-site-packages "$venv"
        else "$base" -m venv "$venv"; fi
        echo "$GUI" > "$venv/gui"
    fi
    install_dependencies "$venv_python" "$SOURCE_DIR/requirements.txt" "$(venv_gui "$venv")"
    wait_for_app
    step "Switching to the new version"
    old="$INSTALL_ROOT/app.old"
    rm -rf "$old"
    if [ -d "$app" ]; then mv "$app" "$old"; fi
    if ! mv "$SOURCE_DIR" "$app"; then
        if [ -d "$old" ]; then mv "$old" "$app"; fi
        return 1
    fi
    rm -rf "$old"
    chmod +x "$app/install.sh" "$app/start-personal-job-agent.sh" 2>/dev/null || true
    printf '{\n  "tag": "%s",\n  "installed_at": "%s",\n  "repository": "%s"\n}\n' \
        "$LABEL" "$(date -Iseconds)" "$REPO" > "$INSTALL_ROOT/install.json"
    if [ "$NO_SHORTCUTS" = 0 ]; then
        step "Adding the app to the applications menu and desktop"
        "$venv_python" "$app/run.py" shortcuts >/dev/null || note "Launchers could not be created; open the app with the command below."
    fi
    APP="$app"; APP_PYTHON="$venv_python"
}

update_checkout() {
    command -v git >/dev/null 2>&1 || { echo "git is not installed, so this checkout cannot be updated automatically." >&2; return 1; }
    local python="${PYTHON_ARG:-$(find_python)}"
    wait_for_app
    step "Updating $CHECKOUT with git pull"
    git -C "$CHECKOUT" pull --ff-only || { echo "git pull failed. Open the project folder and resolve it with git." >&2; return 1; }
    step "Installing dependencies"
    "$python" -m pip install --disable-pip-version-check --quiet -r "$CHECKOUT/requirements.txt"
    APP="$CHECKOUT"; APP_PYTHON="$python"; LABEL=latest
}

start_app() {
    [ "$NO_LAUNCH" = 1 ] && return 0
    step "Opening Personal Job Agent"
    (cd "$1" && setsid "$2" "$1/run.py" gui >/dev/null 2>&1 < /dev/null &)
}

offer_ollama() {
    if [ "$QUIET" = 1 ] || [ "$WAIT_PID" -gt 0 ] || command -v ollama >/dev/null 2>&1; then return 0; fi
    echo
    warn "Cover letters and answers are written by a free local AI (Ollama), which is not installed."
    if ask "Install Ollama now with its official installer (asks for sudo; about 1 GB with a model)?"; then
        curl -fsSL https://ollama.com/install.sh | sh
    else
        note "You can install it later from https://ollama.com/download/linux, or use your own cloud AI key in the app's Settings."
    fi
}

# Runs in a subshell with errexit on, outside any "if" (where bash would
# ignore set -e), and reports what it installed through a result file.
install_or_update() {
    set -e
    trap 'rm -rf "${STAGING:-}"' EXIT
    if [ -n "$CHECKOUT" ]; then update_checkout; else install_release; fi
    printf '%s\n%s\n%s\n' "$APP" "$APP_PYTHON" "$LABEL" > "$RESULT"
}

main() {
    local status previous previous_python
    RESULT=$(mktemp "${TMPDIR:-/tmp}/job-agent-install.XXXXXX")
    set +e
    ( install_or_update )
    status=$?
    set -e
    if [ "$status" = 0 ] && [ -s "$RESULT" ]; then
        { read -r APP; read -r APP_PYTHON; read -r LABEL; } < "$RESULT"
        rm -f "$RESULT"
        offer_ollama
        start_app "$APP" "$APP_PYTHON"
        echo
        printf '%sPersonal Job Agent %s is ready.%s\n' "$OK" "$LABEL" "$RESET"
        note "Open it from the applications menu or desktop, or run: \"$APP_PYTHON\" \"$APP/run.py\" gui"
        return 0
    fi
    rm -f "$RESULT"
    echo
    printf '%sInstall or update failed.%s See the messages above.\n' "$ERR" "$RESET"
    # An update never leaves you without the app: reopen the version you had.
    if [ "$WAIT_PID" -gt 0 ]; then
        if [ -n "$CHECKOUT" ]; then previous="$CHECKOUT"; previous_python="${PYTHON_ARG:-}"
        else previous="$INSTALL_ROOT/app"; previous_python="$INSTALL_ROOT/venv/bin/python"; fi
        if [ -f "$previous/run.py" ] && [ -x "$previous_python" ]; then
            note "Reopening the version you had."
            start_app "$previous" "$previous_python"
        fi
    fi
    return 1
}

APP=""; APP_PYTHON=""; LABEL=""; SOURCE_DIR=""; STAGING=""; RESULT=""
# Everything above is only definitions; with "curl ... | bash" the whole
# script has been read before anything starts.
main
