#!/usr/bin/env bash
# Open Personal Job Agent from this project folder (Linux), without keeping a
# terminal busy. The counterpart of Start-Personal-Job-Agent.cmd on Windows.
# "python3 run.py shortcuts" adds the app to the applications menu instead.
cd "$(dirname "$(readlink -f "$0")")" || exit 1
python="${PYTHON:-python3}"
if ! command -v "$python" >/dev/null 2>&1; then
    echo "Python is not available. Install Python 3.12 or newer with your package manager." >&2
    exit 1
fi
setsid "$python" run.py gui "$@" >/dev/null 2>&1 < /dev/null &
