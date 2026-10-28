# WhisperJAV Web UI Guide

> Local browser interface for transcription and translation · v1.8.14

The Web UI serves a page from your own machine where you can browse local files,
configure a job, start it, and watch live progress. It is a different thing from
`whisperjav-gui`, which is a native desktop window.

| | Web UI (`whisperjav-web`) | Desktop GUI (`whisperjav-gui`) |
|---|---|---|
| Runs as | Local HTTP server, viewed in a browser | Native PyWebView window |
| File picking | Browses the **server's** disk | Native OS file dialog |
| Reachable from | Any browser on this machine | The desktop session only |

---

## Table of Contents

1. [Quick Start](#1-quick-start)
2. [Script Commands](#2-script-commands)
3. [When You Need to Restart](#3-when-you-need-to-restart)
4. [The Three Job Types](#4-the-three-job-types)
5. [Ports and Options](#5-ports-and-options)
6. [Security](#6-security)
7. [Troubleshooting](#7-troubleshooting)

---

## 1. Quick Start

Two launcher scripts sit at the repository root. They locate the virtual
environment relative to their own location, so you can call them from anywhere.

=== "Windows"

    Double-click **`start-web.bat`**, or from a terminal:

    ```bat
    start-web.bat
    ```

=== "Git Bash / Linux / macOS"

    ```bash
    ./start-web.sh
    ```

The browser opens at <http://127.0.0.1:8317/> automatically. Press ++ctrl+c++ in
the terminal to stop the server.

You can also skip the scripts entirely:

```bash
whisperjav-web                                  # if the package is installed
python -m whisperjav.webserver --port 8317      # from a source checkout
```

The scripts add three things over the bare command: they find the venv for you,
they reuse an instance that is already running instead of fighting over the
port, and they refuse to stop a server that has a job in flight.

---

## 2. Script Commands

```
start-web.bat                 Start. Reuses an instance already running.
start-web.bat restart         Stop the running instance, then start.
start-web.bat stop            Stop the running instance and exit.
start-web.bat --port 9000     Any other argument passes through to whisperjav-web.
```

`./start-web.sh` takes the same verbs.

**Reuse.** Running the script when a server is already up does not start a
second one — it prints the URL and opens the browser.

**Stopping is protected.** `stop` and `restart` first check `/api/jobs`. If a job
is running, the script warns and asks before proceeding, because shutting the
server down terminates the whole job process tree:

```
[WARNING] A job is still running on this server.
          Stopping now would kill it and lose the work in progress.
Stop anyway? [y/N]
```

Answering anything other than `y` aborts and leaves both the server and the job
untouched.

!!! warning "Job history is not saved"
    Jobs live in memory. Stopping the server clears the history and cancels
    anything queued or running. There is no resume.

---

## 3. When You Need to Restart

Most changes do **not** need a restart, because every job runs as a fresh
subprocess of the CLI.

| What changed | Restart needed? |
|---|---|
| Running another job | **No** — just press Start again |
| Pipeline code (`whisperjav/main.py`, `pipelines/`, `translate/`, `utils/`) | **No** — the next job spawns a new process |
| Frontend (`webserver/static/*.html`, `*.js`, `*.css`) | **No** — refresh the page (++ctrl+f5++ if it looks stale) |
| Web server code (`whisperjav/webserver/*.py`) | **Yes** — `start-web.bat restart` |
| `pyproject.toml` | **Yes**, and reinstall |

There is deliberately no `--reload` flag. Uvicorn's reloader runs jobs under a
worker process that it kills on reload, which would silently destroy a
long-running transcription.

---

## 4. The Three Job Types

Pick one with the radio buttons at the top of the page.

| Mode | Input | What runs |
|---|---|---|
| **Transcribe** | Video / audio | `whisperjav.main` → SRT |
| **Translate** | An existing `.srt` | `whisperjav.translate.cli` → translated SRT |
| **Transcribe + Translate** | Video / audio | A **single** `whisperjav.main --translate` process |

Files are chosen with the **Browse…** button, which browses this machine's own
disk — nothing is uploaded, so a 40 GB video costs nothing to select. Use
**Add whole folder** to queue every matching file in a directory.

The progress panel shows overall percentage, the current file, the current step,
the sub-task counter with ETA, and the last log line. The log console below it
streams the process output. Output paths appear with a copy button when the job
finishes.

For translation, the model box may be left empty; the effective model is
resolved as **form field → `settings.json` → provider default**, and the hint
under the box tells you which one will actually be used.

---

## 5. Ports and Options

The default port is **8317**. Override it either way:

=== "Argument"

    ```bat
    start-web.bat --port 9000
    ```

=== "Environment variable"

    ```bat
    set WHISPERJAV_WEB_PORT=9000
    start-web.bat
    ```

If the port is busy, `whisperjav-web` tries the next 9 ports and prints the one
it actually bound.

Full option list:

| Option | Meaning |
|---|---|
| `--host` | Bind address. Default `127.0.0.1`. |
| `--port` | Default `8317`, with automatic fallback. |
| `--no-open-browser` | Do not open a browser on start. |
| `--log-level` | `critical` / `error` / `warning` / `info` / `debug`. |
| `--allow-remote` | Required to bind a non-loopback address. See below. |

---

## 6. Security

The server binds `127.0.0.1` only, has **no authentication**, and its directory
browser can read anywhere the user account can read. Binding it to a reachable
address therefore requires an explicit opt-in:

```
$ whisperjav-web --host 0.0.0.0
Refusing to bind 0.0.0.0.
This server has no authentication and /api/browse can read your entire
filesystem.
Re-run with --allow-remote if that is really what you want.
```

Do not expose it to an untrusted network.

API keys typed into the translation form are never written to browser storage,
and the command preview shown in the UI masks them as `--translate-api-key ***`.

---

## 7. Troubleshooting

**"Virtual environment not found"**

The script could not find `.venv` next to itself. Create it from the repository
root:

```bash
uv sync --python 3.12 --extra all
```

**The page will not load**

Check the server is actually answering:

```bash
curl http://127.0.0.1:8317/api/health
```

A healthy server returns `{"ok":true,"version":"1.8.14","pid":...}`. If it
answers but the browser cannot reach it, confirm the browser is running on this
same machine — `127.0.0.1` is not reachable from another host by design.

**Two `python.exe` processes per server**

Expected on a uv-created virtual environment. `.venv\Scripts\python.exe` is a
trampoline that re-executes the real interpreter with the same arguments, so you
see a parent and a child. Only one of them holds the listening socket:

```powershell
Get-NetTCPConnection -LocalPort 8317 -State Listen
```

The `stop` verb matches on the command line and clears both.

**Port still in use after stopping**

Usually a partially killed instance. `start-web.bat stop` verifies the port is
released and reports an error if something is still answering. To inspect
manually:

```powershell
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object { $_.CommandLine -like '*whisperjav.webserver*' }
```

**A job failed with "Process crashed: ..."**

A native fault inside a loaded library, not a Python error, so there is no
traceback. The decoded fault name and the last step reached are in the job's
error message and log tail. Jobs run with `PYTHONFAULTHANDLER=1`, so the Python
stack at the moment of the crash is printed into the log as well.

**Progress bar sits still**

Percentages advance in ~10% milestones by design, and a single long scene or a
model download can hold it flat for minutes. The elapsed timer and the last log
line keep moving — use those to tell a slow job from a stuck one.
