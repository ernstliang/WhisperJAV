#!/usr/bin/env bash
# ==============================================================================
# WhisperJAV Web UI - Quick Start (Git Bash / WSL / Linux / macOS)
# ==============================================================================
#
# Usage:
#   ./start-web.sh                 Start (reuses an instance already running)
#   ./start-web.sh restart         Stop the running instance, then start
#   ./start-web.sh stop            Stop the running instance and exit
#   ./start-web.sh --port 9000     Any other args pass through to whisperjav-web
#
# The server binds 127.0.0.1 only. It has no authentication and can browse the
# whole filesystem, so exposing it needs an explicit --allow-remote.
# ==============================================================================

set -u

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PORT="${WHISPERJAV_WEB_PORT:-8317}"
BASE="http://127.0.0.1:${PORT}"

# Windows venvs use Scripts/, POSIX ones use bin/.
if [ -x "$REPO/.venv/Scripts/python.exe" ]; then
    PY="$REPO/.venv/Scripts/python.exe"
elif [ -x "$REPO/.venv/bin/python" ]; then
    PY="$REPO/.venv/bin/python"
else
    echo "[ERROR] Virtual environment not found under $REPO/.venv" >&2
    echo "        Create it with: uv sync --python 3.12 --extra all" >&2
    exit 1
fi

is_up() { curl -s -m 2 -o /dev/null "$BASE/api/health"; }

open_browser() {
    if command -v cmd.exe >/dev/null 2>&1; then cmd.exe /c start "" "$1" >/dev/null 2>&1
    elif command -v xdg-open >/dev/null 2>&1; then xdg-open "$1" >/dev/null 2>&1 &
    elif command -v open >/dev/null 2>&1; then open "$1" >/dev/null 2>&1 &
    fi
}

stop_server() {
    if ! is_up; then
        echo "Nothing running on port $PORT."
        return 0
    fi

    # Refuse to kill a server that is mid-job: shutdown terminates the whole
    # process tree, so an in-flight transcription would be lost.
    if curl -s -m 5 "$BASE/api/jobs" | grep -q '"state": *"running"'; then
        echo "[WARNING] A job is still running on this server."
        echo "          Stopping now would kill it and lose the work in progress."
        read -r -p "Stop anyway? [y/N] " answer
        case "$answer" in
            [yY]) ;;
            *) echo "Aborted."; return 1 ;;
        esac
    fi

    echo "Stopping the instance on port $PORT ..."
    if command -v powershell.exe >/dev/null 2>&1; then
        powershell.exe -NoProfile -Command \
            "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { \$_.CommandLine -like '*whisperjav.webserver*' } | ForEach-Object { Stop-Process -Id \$_.ProcessId -Force }" \
            >/dev/null 2>&1 || true
    else
        pkill -f "whisperjav.webserver" >/dev/null 2>&1 || true
    fi

    # Give the lifespan hook a moment to kill child process trees.
    sleep 1.5
    if is_up; then
        echo "[ERROR] Port $PORT is still answering. Something else may be using it." >&2
        return 1
    fi
    echo "Stopped."
}

echo
echo "=============================================================="
echo "  WhisperJAV Web UI"
echo "=============================================================="
echo

case "${1:-}" in
    stop)
        stop_server
        exit $?
        ;;
    restart)
        stop_server || exit 1
        shift
        ;;
    *)
        if is_up; then
            echo "Already running on $BASE/"
            echo "Opening the browser. Use './start-web.sh restart' to reload changed"
            echo "server code (pipeline and frontend changes need no restart)."
            open_browser "$BASE/"
            exit 0
        fi
        ;;
esac

echo "Starting on $BASE/"
echo "Press Ctrl+C to stop. Stopping also cancels any running job."
echo
exec "$PY" -m whisperjav.webserver --port "$PORT" "$@"
