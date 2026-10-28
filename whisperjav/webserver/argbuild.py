"""
Turn a web-form options dict into a CLI argv list.

Pure functions, no I/O, no state — everything here is unit-testable without a
subprocess. Deliberately a *narrow* rewrite rather than a reuse of
whisperjav/webview_gui/api.py:97-213 `build_args`: that method is not pure (it
reads self.default_output), its transformers branch carries GUI-only concepts,
and its module hard-imports pywebview at api.py:19-20.
"""

import sys
from typing import Any, Dict, List, Tuple

from whisperjav.webserver.paths import OUTPUT_DIR_SOURCE_SENTINEL

FLOWS = ("transcribe", "translate", "transcribe_translate")

# Flags whose *value* must never be shown back to the browser.
_SECRET_FLAGS = {"--api-key", "--translate-api-key"}

# -X utf8 / -u: see whisperjav/webview_gui/api.py:380, which passes only
# `-X utf8`. Logger output flushes per record, but the bare print() summary at
# whisperjav/main.py:1390-1408 is block-buffered into a pipe and would only
# appear at exit. -u fixes that.
_PY_PREFIX = [sys.executable, "-X", "utf8", "-u", "-m"]


def _s(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _flag(args: List[str], name: str, value: Any) -> None:
    """Append `name value` when value is a non-empty string/number."""
    text = _s(value)
    if text:
        args += [name, text]


def _switch(args: List[str], name: str, value: Any) -> None:
    if value:
        args.append(name)


def build_transcribe_argv(opts: Dict[str, Any], inputs: List[str]) -> List[str]:
    """argv for `python -m whisperjav.main` (flow 1)."""
    args: List[str] = list(_PY_PREFIX) + ["whisperjav.main"]

    if opts.get("check_only"):
        # Mirrors whisperjav/webview_gui/api.py:126-129 — a seconds-long smoke
        # test of the whole spawn/stream/terminal-state path with no model load.
        return args + ["--check", "--check-verbose"]

    if not inputs:
        raise ValueError("At least one input file or folder is required.")
    args += list(inputs)

    mode = _s(opts.get("mode")) or "balanced"
    args += ["--mode", mode]

    _flag(args, "--language", opts.get("language") or "japanese")
    _flag(args, "--subs-language", opts.get("subs_language") or "native")
    _flag(args, "--sensitivity", opts.get("sensitivity") or "balanced")
    _flag(args, "--model", opts.get("model"))

    output_dir = _s(opts.get("output_dir")) or OUTPUT_DIR_SOURCE_SENTINEL
    args += ["--output-dir", output_dir]

    output_format = _s(opts.get("output_format")) or "srt"
    if output_format != "srt":
        args += ["--output-format", output_format]

    _flag(args, "--temp-dir", opts.get("temp_dir"))
    _switch(args, "--keep-temp", opts.get("keep_temp"))
    _switch(args, "--skip-existing", opts.get("skip_existing"))
    _switch(args, "--accept-cpu-mode", opts.get("accept_cpu_mode"))

    log_level = _s(opts.get("log_level")) or "INFO"
    if log_level != "INFO":
        args += ["--log-level", log_level]

    return args


def _append_translate_flags_for_main(args: List[str], tr: Dict[str, Any]) -> None:
    """Translation Options group of whisperjav.main (flow 3)."""
    args.append("--translate")
    _flag(args, "--translate-provider", tr.get("provider"))
    _flag(args, "--translate-target", tr.get("target"))
    _flag(args, "--translate-tone", tr.get("tone"))
    _flag(args, "--translate-model", tr.get("model"))
    _flag(args, "--translate-api-key", tr.get("api_key"))
    _flag(args, "--translate-endpoint", tr.get("endpoint"))
    _flag(args, "--translate-title", tr.get("movie_title"))
    _flag(args, "--translate-actress", tr.get("actress"))
    _flag(args, "--translate-plot", tr.get("movie_plot"))
    _switch(args, "--translate-include-original", tr.get("include_original"))
    _flag(args, "--ollama-url", tr.get("ollama_url"))
    _flag(args, "--ollama-num-ctx", tr.get("ollama_num_ctx"))
    # Non-interactive: the subprocess has no stdin, so any confirmation prompt
    # (model pull, server start) would hang the job forever.
    args.append("--yes")


def build_translate_argv(opts: Dict[str, Any], inputs: List[str]) -> List[str]:
    """argv for `python -m whisperjav.translate.cli` (flow 2)."""
    if not inputs:
        raise ValueError("At least one .srt file or folder is required.")

    args: List[str] = list(_PY_PREFIX) + ["whisperjav.translate.cli", "-i"]
    args += list(inputs)

    _flag(args, "--provider", opts.get("provider"))
    _flag(args, "--target", opts.get("target"))
    _flag(args, "--source", opts.get("source") or "japanese")
    _flag(args, "--tone", opts.get("tone"))
    _flag(args, "--model", opts.get("model"))
    _flag(args, "--api-key", opts.get("api_key"))
    _flag(args, "--endpoint", opts.get("endpoint"))
    _flag(args, "--scene-threshold", opts.get("scene_threshold"))
    _flag(args, "--max-batch-size", opts.get("max_batch_size"))
    _flag(args, "--movie-title", opts.get("movie_title"))
    _flag(args, "--actress", opts.get("actress"))
    _flag(args, "--movie-plot", opts.get("movie_plot"))
    _flag(args, "--ollama-url", opts.get("ollama_url"))
    _flag(args, "--ollama-num-ctx", opts.get("ollama_num_ctx"))

    if opts.get("include_original"):
        args.append("--include-original")
    elif opts.get("include_original") is False:
        args.append("--no-include-original")

    # translate/cli.py:697-711 treats -o as a *file path* when it is not an
    # existing directory and there is a single input. Only pass it when the
    # caller resolved a real directory, and never for the "source" sentinel.
    out_dir = _s(opts.get("output_dir"))
    if out_dir and out_dir != OUTPUT_DIR_SOURCE_SENTINEL:
        args += ["-o", out_dir]

    args.append("--stream")
    args.append("--yes")
    _switch(args, "--debug", opts.get("debug"))

    return args


def build_command(
    flow: str,
    transcribe: Dict[str, Any],
    translate: Dict[str, Any],
    inputs: List[str],
) -> Tuple[List[str], str]:
    """Return (argv, redacted_display_string)."""
    if flow not in FLOWS:
        raise ValueError(f"Unknown flow {flow!r}; expected one of {FLOWS}.")

    if flow == "translate":
        argv = build_translate_argv(translate or {}, inputs)
    else:
        argv = build_transcribe_argv(transcribe or {}, inputs)
        if flow == "transcribe_translate" and not (transcribe or {}).get("check_only"):
            _append_translate_flags_for_main(argv, translate or {})

    return argv, redact_command(argv)


def redact_command(argv: List[str]) -> str:
    """
    Human-readable command with secrets masked.

    The display string is rendered into the DOM and routinely pasted into bug
    reports, so API keys must never survive this function.
    """
    parts: List[str] = []
    skip_next = False
    # Drop the interpreter path and the -X/-u/-m scaffolding; show the module.
    tokens = list(argv)
    if tokens and tokens[0] == sys.executable:
        tokens = tokens[1:]
    while tokens and tokens[0] in ("-X", "utf8", "-u", "-m"):
        tokens.pop(0)

    for token in tokens:
        if skip_next:
            parts.append("***")
            skip_next = False
            continue
        if token in _SECRET_FLAGS:
            parts.append(token)
            skip_next = True
            continue
        parts.append(f'"{token}"' if " " in token else token)
    return " ".join(parts)
