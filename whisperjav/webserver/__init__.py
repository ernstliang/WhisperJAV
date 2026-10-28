"""
WhisperJAV local web server.

A browser UI for transcription and translation, served on loopback only.
Jobs run as subprocesses of the CLI (never in-process) — see app.py for why.

Entry points:
    whisperjav-web                  (console script)
    python -m whisperjav.webserver
"""

__all__ = ["create_app", "main"]


def __getattr__(name):
    # Lazy so that `import whisperjav.webserver` does not require fastapi.
    if name in ("create_app", "main"):
        from whisperjav.webserver.app import create_app, main
        return {"create_app": create_app, "main": main}[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
