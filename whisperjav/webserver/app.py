"""
FastAPI application and launcher for the WhisperJAV local web UI.

Loopback-only by design: /api/browse can enumerate the entire filesystem and
there is no authentication, so binding a non-loopback address requires an
explicit --allow-remote.
"""

import argparse
import os
import socket
import sys
import threading
import webbrowser
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

from whisperjav.webserver import argbuild, browse
from whisperjav.webserver.jobs import JobManager
from whisperjav.webserver.models import SubmitRequest
from whisperjav.webserver.paths import (
    MEDIA_EXTS,
    OUTPUT_DIR_SOURCE_SENTINEL,
    REPO_ROOT,
    SRT_EXTS,
    default_output_dir,
    is_loopback_host,
    normalize_path,
)

STATIC_DIR = Path(__file__).parent / "static"
DEFAULT_PORT = 8317


# --------------------------------------------------------------------------
# Config assembly — options come from the real constants, never hardcoded in JS
# --------------------------------------------------------------------------

def _translate_choices() -> Dict[str, Any]:
    from whisperjav.translate.providers import (
        PROVIDER_CONFIGS,
        SUPPORTED_SOURCES,
        SUPPORTED_TARGETS,
    )
    providers = []
    for name, cfg in PROVIDER_CONFIGS.items():
        if name == "local":  # deprecated in v1.8.10, removed in v1.9.0
            continue
        env_var = cfg.get("env_var")
        providers.append({
            "id": name,
            "default_model": cfg.get("model") or "",
            "env_var": env_var,
            "needs_key": bool(env_var),
            "key_present": bool(env_var and os.environ.get(env_var)),
        })
    return {
        "providers": providers,
        # SUPPORTED_* are set literals (providers.py:81-82) with arbitrary
        # iteration order — sort so the dropdown is stable across restarts.
        "targets": sorted(SUPPORTED_TARGETS),
        "sources": sorted(SUPPORTED_SOURCES),
        "tones": ["standard", "pornify"],
    }


def _saved_translate_settings() -> Dict[str, Any]:
    try:
        from whisperjav.translate.settings import load_settings
        saved = load_settings() or {}
    except Exception:  # noqa: BLE001 - a bad settings file must not break the UI
        return {}
    return {
        "provider": saved.get("provider"),
        "model": saved.get("model"),
        "target_language": saved.get("target_language"),
        "tone": saved.get("tone"),
        "include_original": saved.get("include_original"),
        "scene_threshold": saved.get("scene_threshold"),
        "max_batch_size": saved.get("max_batch_size"),
        "ollama_url": saved.get("ollama_url"),
    }


def build_config() -> Dict[str, Any]:
    try:
        from whisperjav.__version__ import __version__
    except Exception:  # noqa: BLE001
        __version__ = "unknown"

    return {
        "version": __version__,
        "defaults": {
            "output_dir": str(default_output_dir()),
            "home": str(Path.home()),
            "repo_root": str(REPO_ROOT),
            "source_sentinel": OUTPUT_DIR_SOURCE_SENTINEL,
        },
        "transcribe": {
            # Mirrors whisperjav/main.py:165,173,289,295,319
            "mode": ["fidelity", "balanced", "fast", "faster", "transformers", "qwen"],
            "sensitivity": ["conservative", "balanced", "aggressive"],
            "language": ["japanese", "korean", "chinese", "english"],
            "subs_language": ["native", "direct-to-english"],
            "output_format": ["srt", "vtt", "both"],
            "log_level": ["DEBUG", "INFO", "WARNING", "ERROR"],
        },
        "translate": _translate_choices(),
        "saved": _saved_translate_settings(),
    }


# --------------------------------------------------------------------------
# Submit validation
# --------------------------------------------------------------------------

def _validate_submit(req: SubmitRequest) -> List[str]:
    """Return normalized input paths, or raise ValueError with a plain message."""
    if req.flow not in argbuild.FLOWS:
        raise ValueError(f"Unknown flow: {req.flow}")

    if req.flow.startswith("transcribe") and req.transcribe.check_only:
        return []

    if not req.inputs:
        raise ValueError("Select at least one input file or folder.")

    wanted = SRT_EXTS if req.flow == "translate" else MEDIA_EXTS
    label = ".srt subtitle" if req.flow == "translate" else "media"

    normalized: List[str] = []
    for raw in req.inputs:
        p = normalize_path(raw)
        if not p.exists():
            raise ValueError(f"Input does not exist: {p}")
        if p.is_file() and p.suffix.lower() not in wanted:
            raise ValueError(f"Not a {label} file: {p}")
        normalized.append(str(p))

    if req.flow != "translate":
        out_dir = (req.transcribe.output_dir or "").strip()
        if out_dir and out_dir != OUTPUT_DIR_SOURCE_SENTINEL:
            try:
                normalize_path(out_dir).mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise ValueError(
                    f"Cannot use output directory {out_dir}: {exc}") from exc

    if req.flow != "transcribe":
        _validate_provider_key(req.translate)

    return normalized


def _validate_provider_key(tr) -> None:
    """
    Catch a missing API key at submit time.

    Otherwise the user waits out a full transcription and the translation dies
    instantly at the end — the single most expensive way to learn this.
    """
    from whisperjav.translate.providers import PROVIDER_CONFIGS

    cfg = PROVIDER_CONFIGS.get(tr.provider)
    if cfg is None:
        raise ValueError(f"Unknown translation provider: {tr.provider}")
    env_var = cfg.get("env_var")
    if not env_var:
        return
    if (tr.api_key or "").strip():
        return
    if os.environ.get(env_var):
        return
    raise ValueError(
        f"{env_var} is not set and no API key was provided for "
        f"provider '{tr.provider}'."
    )


def _resolve_output_dir(req: SubmitRequest) -> Optional[str]:
    if req.flow == "translate":
        raw = (req.translate.output_dir or "").strip()
    else:
        raw = (req.transcribe.output_dir or "").strip()
    if not raw or raw == OUTPUT_DIR_SOURCE_SENTINEL:
        return None
    return str(normalize_path(raw))


# --------------------------------------------------------------------------
# App
# --------------------------------------------------------------------------

def create_app() -> "FastAPI":  # noqa: F821
    from fastapi import FastAPI, HTTPException, Query, Request
    from fastapi.concurrency import run_in_threadpool
    from fastapi.responses import FileResponse, JSONResponse
    from fastapi.staticfiles import StaticFiles

    manager = JobManager()

    @asynccontextmanager
    async def lifespan(_app):
        manager.start()
        try:
            yield
        finally:
            manager.shutdown()

    app = FastAPI(title="WhisperJAV Web", lifespan=lifespan, docs_url=None,
                  redoc_url=None)
    app.state.jobs = manager

    if STATIC_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.exception_handler(browse.BrowseError)
    async def _browse_error(_request: "Request", exc: browse.BrowseError):
        return JSONResponse(
            status_code=exc.status,
            content={"error": exc.code, "detail": exc.message},
        )

    @app.get("/")
    async def index():
        page = STATIC_DIR / "index.html"
        if not page.is_file():
            raise HTTPException(500, "static/index.html is missing")
        return FileResponse(str(page), headers={"Cache-Control": "no-store"})

    @app.get("/api/health")
    async def health():
        cfg = build_config()
        return {"ok": True, "version": cfg["version"], "pid": os.getpid()}

    @app.get("/api/config")
    async def config():
        return await run_in_threadpool(build_config)

    # -- browsing --------------------------------------------------------

    @app.get("/api/browse")
    async def api_browse(path: str = Query(default=""), kind: str = "media",
                         show_hidden: int = 0):
        # A cold network drive can block for seconds; keep it off the loop so
        # an in-flight job's polling does not freeze with it.
        return await run_in_threadpool(
            browse.list_dir, path, kind, bool(show_hidden))

    @app.get("/api/browse/expand")
    async def api_browse_expand(path: str, kind: str = "media",
                                recursive: int = 0):
        files = await run_in_threadpool(
            browse.expand_dir_files, path, kind, bool(recursive))
        return {"files": files}

    # -- ollama ----------------------------------------------------------

    def _ollama_manager(base_url: Optional[str] = None):
        from whisperjav.translate.ollama_manager import OllamaManager
        return OllamaManager(base_url=base_url) if base_url else OllamaManager()

    @app.get("/api/ollama/status")
    async def ollama_status(base_url: Optional[str] = None):
        def work():
            try:
                mgr = _ollama_manager(base_url)
                return {
                    "installed": bool(mgr.detect_installation()),
                    "running": bool(mgr.detect_server()),
                    "base_url": mgr.base_url,
                }
            except Exception as exc:  # noqa: BLE001
                return {"installed": False, "running": False,
                        "base_url": base_url, "error": str(exc)}
        return await run_in_threadpool(work)

    @app.get("/api/ollama/models")
    async def ollama_models(base_url: Optional[str] = None):
        # Never shell out to `whisperjav-translate --list-ollama-models`:
        # translate/cli.py:212 marks -i as required and the utility branch runs
        # after parse_args(), so that command always exits 2.
        def work():
            try:
                return {"models": _ollama_manager(base_url).list_models()}
            except Exception as exc:  # noqa: BLE001
                return {"models": [], "error": str(exc)}
        return await run_in_threadpool(work)

    # -- jobs ------------------------------------------------------------

    def _job_payload(job, with_logs: Optional[int]) -> Dict[str, Any]:
        payload = job.view()
        if with_logs is not None:
            payload.update(job.logs_since(max(0, with_logs)))
        return payload

    @app.post("/api/jobs", status_code=202)
    async def submit_job(req: SubmitRequest):
        try:
            inputs = await run_in_threadpool(_validate_submit, req)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

        transcribe = req.transcribe.model_dump()
        translate = req.translate.model_dump()
        output_dir = _resolve_output_dir(req)
        if output_dir:
            if req.flow == "translate":
                translate["output_dir"] = output_dir
            else:
                transcribe["output_dir"] = output_dir

        try:
            argv, display = argbuild.build_command(
                req.flow, transcribe, translate, inputs)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

        debug = bool(translate.get("debug")) or \
            str(transcribe.get("log_level", "")).upper() == "DEBUG"
        job = manager.submit(req.flow, argv, display, inputs,
                             output_dir=output_dir, debug=debug)
        return {
            "id": job.id, "state": job.state,
            "position": manager.position(job.id), "command": job.command_display,
        }

    @app.get("/api/jobs")
    async def list_jobs():
        return {"jobs": [job.view() for job in manager.list()]}

    @app.get("/api/jobs/{job_id}")
    async def get_job(job_id: str, with_logs: Optional[int] = None):
        job = manager.get(job_id)
        if job is None:
            raise HTTPException(404, "No such job")
        return _job_payload(job, with_logs)

    @app.get("/api/jobs/{job_id}/logs")
    async def get_job_logs(job_id: str, since: int = 0, limit: int = 2000):
        job = manager.get(job_id)
        if job is None:
            raise HTTPException(404, "No such job")
        payload = job.logs_since(max(0, since), limit)
        payload["state"] = job.state
        return payload

    @app.post("/api/jobs/{job_id}/cancel")
    async def cancel_job(job_id: str):
        job = await run_in_threadpool(manager.cancel, job_id)
        if job is None:
            raise HTTPException(404, "No such job")
        return job.view()

    @app.delete("/api/jobs/{job_id}", status_code=204)
    async def delete_job(job_id: str):
        try:
            removed = manager.delete(job_id)
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc
        if not removed:
            raise HTTPException(404, "No such job")
        return None

    return app


# --------------------------------------------------------------------------
# Launcher
# --------------------------------------------------------------------------

def _bind_port(host: str, port: int, attempts: int = 10) -> int:
    for candidate in range(port, port + attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind((host, candidate))
                return candidate
            except OSError:
                continue
    raise SystemExit(
        f"Could not bind any port in {port}-{port + attempts - 1} on {host}.")


def main() -> None:
    try:
        from whisperjav.utils.console import setup_console
        setup_console()
    except Exception:  # noqa: BLE001
        pass

    parser = argparse.ArgumentParser(
        prog="whisperjav-web",
        description="Local browser UI for WhisperJAV transcription and translation.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--no-open-browser", action="store_true")
    parser.add_argument("--log-level", default="info",
                        choices=["critical", "error", "warning", "info", "debug"])
    parser.add_argument(
        "--allow-remote", action="store_true",
        help="Permit binding a non-loopback address. Required because this "
             "server has no authentication and can read the whole filesystem.")
    args = parser.parse_args()

    if not is_loopback_host(args.host) and not args.allow_remote:
        print(
            f"Refusing to bind {args.host}.\n"
            "This server has no authentication and /api/browse can read your "
            "entire filesystem.\nRe-run with --allow-remote if that is really "
            "what you want.",
            file=sys.stderr)
        sys.exit(2)

    try:
        import uvicorn
    except ImportError:
        try:
            from whisperjav.utils.console import print_missing_extra_error
            print_missing_extra_error(
                extra_name="web", missing_packages=["fastapi", "uvicorn"],
                feature_description="the local web UI")
        except Exception:  # noqa: BLE001
            print("Missing dependencies. Install with: "
                  "pip install 'whisperjav[web]'", file=sys.stderr)
        sys.exit(1)

    port = _bind_port(args.host, args.port)
    url = f"http://{args.host}:{port}/"
    print(f"WhisperJAV Web UI -> {url}")
    if port != args.port:
        print(f"(port {args.port} was busy; bound {port} instead)")

    if not args.no_open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    uvicorn.run(create_app(), host=args.host, port=port,
                log_level=args.log_level, access_log=False)


if __name__ == "__main__":
    main()
