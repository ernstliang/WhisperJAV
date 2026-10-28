"""
Turn raw subprocess log lines into structured progress.

Every pattern here is anchored to a real emission site; the comments carry the
file:line so the two stay findable together. Two normalization steps are
mandatory before matching:

1. Strip ANSI. whisperjav/utils/logger.py:9-31 sets _USE_COLOR = True whenever
   colorama imports, independent of tty, so colour codes can reach a pipe.
2. Strip the `asctime - name - level - ` envelope
   (whisperjav/utils/logger.py:86-91). Every progress message is *inside* it,
   which is why matching must use re.search, never re.match. Stripping the
   prefix first is what lets a few patterns be safely ^-anchored.

Regressions here are silent — the bar simply stops moving — so the ANSI sample
in tests/test_webserver_progress.py is the guard.
"""

import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

ANSI_RE = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]")
LOG_PREFIX_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} - [\w.]+ - \w+ - "
)

# --- whisperjav.main / unified_progress.py -------------------------------
R_FOUND_FILES = re.compile(r"Found (\d+) media file\(s\) to process")           # main.py:2125
R_FILE_START = re.compile(r"\[(\d+)/(\d+)\] Starting:\s*(.+?)\s*$")             # unified_progress.py:303
R_STEP = re.compile(r"\bStep (\d+)/(\d+):\s*(.+?)\s*$")                         # :337
R_TASK_START = re.compile(
    r"\bProgress:\s+(?P<name>.+?)\s+-\s+(?P<tot>\d+) items queued")             # :375
R_TASK_PROG = re.compile(
    r"\bProgress:\s+(?P<name>.+?)\s+(?P<cur>\d+)/(?P<tot>\d+)\s+"
    r"\((?P<pct>\d+)%\)\s+elapsed\s+(?P<elapsed>\d+:\d{2}(?::\d{2})?)"
    r"(?:,\s*ETA\s+(?P<eta>\d+:\d{2}(?::\d{2})?))?")                            # :408-411
R_FILE_DONE = re.compile(
    "([✓✗])\\s*(Complete|Failed):\\s*(.+?)\\s*$")                     # :451
R_SKIPPED = re.compile(r"Skipping \(output exists\):\s*(.+)")                   # main.py:1265

# --- translation: shared by both flows (translate/core.py -> whisperjav logger)
R_TR_START = re.compile(
    r"Translation progress: starting - (\d+) scenes / (\d+) batches")           # core.py:607
R_TR_BATCH = re.compile(
    r"Translation progress: batch (\d+)/(\d+) \((\d+)%\)")                      # :620
R_TR_BATCH_N = re.compile(r"Translation progress: (\d+) batches done")          # :624
R_TR_SCENE = re.compile(
    r"Translation progress: scene (\d+)/(\d+) complete")                        # :632
R_TR_SCENE_N = re.compile(r"Translation progress: scene (\d+) complete")        # :634

# --- flow 3 only: whisperjav.main translates in-process -------------------
R_TR_PHASE = re.compile(r"^Starting translation\.\.\.$")                        # main.py:1299
R_TR_DONE_M = re.compile(r"Translation complete:\s*(.+?)\s*$")                  # main.py:1323

# --- flow 2 only: translate/cli.py writes these to stderr -----------------
R_TR_FILE = re.compile(r"Translating \[(\d+)/(\d+)\]:\s+(.+?)\s*$")             # cli.py:718
R_TR_FILEDONE = re.compile(r"^Complete:\s*(.+\.srt)\s*$")                       # cli.py:851
R_TR_FILEFAIL = re.compile(r"^Failed:\s*(.+?)\s*$")                             # cli.py:853
R_TR_BATCHERR = re.compile(r"Batch processing finished with (\d+) errors")      # cli.py:867
R_TR_HARDFAIL = re.compile(r"TRANSLATION FAILED")

# --- diagnostics worth showing in the header, not the bar -----------------
R_DIAG_TARGET = re.compile(
    r"\[TRANSLATE\]\s+Target language \(resolved\):\s*(.+?)\s*$")               # core.py
R_DIAG_BILING = re.compile(
    r"\[TRANSLATE\]\s+Include original \(bilingual\):\s*(.+?)\s*$")

ASR_WEIGHT_IN_COMBINED = 0.85
DEFAULT_STEP_TOTAL = 5  # unified_progress.py:294 starts steps with total=5


def strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text)


def normalize(raw: str) -> str:
    """Strip colour codes, trailing newline, and the log envelope."""
    return LOG_PREFIX_RE.sub("", strip_ansi(raw).rstrip("\r\n"), count=1)


# Several pipelines draw their own bar with a leading carriage return and
# end="" (e.g. balanced_pipeline.py:353 "\rTranscribing: [===] 200/207 ...").
# The CR terminates the *previous* line, so the bar fragment has no terminator
# of its own and gets glued to the next logger record — producing
# "... ETA: 15s2026-08-15 16:55:12 - whisperjav - INFO - ...". Break before any
# embedded log envelope so the console shows one record per line.
_EMBEDDED_RECORD = re.compile(
    r"(?<!\A)(?=\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} - [\w.]+ - \w+ - )")


def split_stream_line(raw: str) -> list:
    """Split one raw stdout line into the log records it actually contains."""
    text = strip_ansi(raw).rstrip("\r\n").lstrip("\r")
    if not text.strip():
        return []
    return [part for part in _EMBEDDED_RECORD.split(text) if part.strip()]


@dataclass
class Event:
    kind: str
    data: Dict[str, Any] = field(default_factory=dict)


def parse_line(line: str) -> Optional[Event]:
    """
    Classify one *normalized* line. Pure — no state, no I/O.

    Ordering is load-bearing:
      * R_TASK_PROG before R_TASK_START (both begin with "Progress:")
      * R_FILE_DONE before R_TR_FILEDONE — the latter is ^-anchored precisely
        so it does not swallow "✓ Complete: SDMT940C.mp4"
    """
    m = R_TASK_PROG.search(line)
    if m:
        return Event("task_progress", {
            "name": m.group("name"),
            "current": int(m.group("cur")),
            "total": int(m.group("tot")),
            "pct": int(m.group("pct")),
            "elapsed": m.group("elapsed"),
            "eta": m.group("eta"),
        })

    m = R_TASK_START.search(line)
    if m:
        return Event("task_start", {
            "name": m.group("name"), "total": int(m.group("tot")),
        })

    m = R_STEP.search(line)
    if m:
        return Event("step", {
            "index": int(m.group(1)), "total": int(m.group(2)),
            "name": m.group(3),
        })

    m = R_FILE_START.search(line)
    if m:
        return Event("file_start", {
            "index": int(m.group(1)), "total": int(m.group(2)),
            "name": m.group(3),
        })

    m = R_FILE_DONE.search(line)
    if m:
        return Event("file_done", {
            "success": m.group(1) == "✓", "name": m.group(3),
        })

    m = R_FOUND_FILES.search(line)
    if m:
        return Event("found_files", {"total": int(m.group(1))})

    m = R_SKIPPED.search(line)
    if m:
        return Event("skipped", {"name": m.group(1)})

    m = R_TR_START.search(line)
    if m:
        return Event("tr_start", {
            "scenes_total": int(m.group(1)), "batches_total": int(m.group(2)),
        })

    m = R_TR_BATCH.search(line)
    if m:
        return Event("tr_batch", {
            "done": int(m.group(1)), "total": int(m.group(2)),
            "pct": int(m.group(3)),
        })

    m = R_TR_BATCH_N.search(line)
    if m:
        return Event("tr_batch_n", {"done": int(m.group(1))})

    m = R_TR_SCENE.search(line)
    if m:
        return Event("tr_scene", {
            "done": int(m.group(1)), "total": int(m.group(2)),
        })

    m = R_TR_SCENE_N.search(line)
    if m:
        return Event("tr_scene_n", {"done": int(m.group(1))})

    m = R_TR_FILE.search(line)
    if m:
        return Event("tr_file", {
            "index": int(m.group(1)), "total": int(m.group(2)),
            "name": m.group(3),
        })

    if R_TR_PHASE.search(line):
        return Event("tr_phase", {})

    m = R_TR_DONE_M.search(line)
    if m:
        return Event("tr_done", {"name": m.group(1)})

    m = R_TR_FILEDONE.search(line)
    if m:
        return Event("tr_file_done", {"name": m.group(1), "success": True})

    m = R_TR_FILEFAIL.search(line)
    if m:
        return Event("tr_file_done", {"name": m.group(1), "success": False})

    if R_TR_HARDFAIL.search(line):
        return Event("error", {"message": line})

    m = R_TR_BATCHERR.search(line)
    if m:
        return Event("error", {
            "message": line, "failed_count": int(m.group(1)),
        })

    m = R_DIAG_TARGET.search(line)
    if m:
        return Event("diag", {"key": "target_language", "value": m.group(1)})

    m = R_DIAG_BILING.search(line)
    if m:
        return Event("diag", {"key": "include_original", "value": m.group(1)})

    return None


class ProgressTracker:
    """
    Accumulates events into a snapshot dict.

    Must be constructed with the flow: whisperjav.main --translate and
    whisperjav.translate.cli emit *different* line shapes (flow 3 has
    "Starting translation..." and no "Translating [i/N]"; flow 2 is the
    reverse), so a single flow-agnostic formula would be wrong for one of them.
    """

    def __init__(self, flow: str, output_dir: Optional[str] = None):
        self.flow = flow
        self.output_dir = output_dir

        self.phase = "translate" if flow == "translate" else "transcribe"
        self.file_total = 1
        self.file_index = 0
        self.file_name: Optional[str] = None
        self.files_done = 0

        self.step_index = 0
        self.step_total = DEFAULT_STEP_TOTAL
        self.step_name: Optional[str] = None

        self.task_name: Optional[str] = None
        self.task_current = 0
        self.task_total = 0
        self.task_pct = 0
        self.elapsed_str: Optional[str] = None
        self.eta_str: Optional[str] = None

        self.tr_scenes_total = 0
        self.tr_batches_total = 0
        self.tr_batch_done = 0
        self.tr_scene_done = 0
        self.tr_pct = 0.0
        self.tr_file_index = 0
        self.tr_file_total = 1
        self.tr_files_done = 0

        self.diagnostics: Dict[str, str] = {}
        self.output_files: list = []
        self.last_error: Optional[str] = None
        self.last_message: Optional[str] = None

        self._max_pct = 0.0

    # -- ingestion --------------------------------------------------------

    def feed(self, normalized: str) -> Optional[Event]:
        event = parse_line(normalized)
        if event is None:
            self._maybe_capture_output_path(normalized)
            return None

        self.last_message = normalized
        handler = getattr(self, f"_on_{event.kind}", None)
        if handler is not None:
            handler(event.data)
        return event

    def _maybe_capture_output_path(self, line: str) -> None:
        """
        translate/cli.py:849 prints the result path bare to stdout, with no log
        envelope. Anything unmatched that names an existing .srt is a result.
        """
        candidate = line.strip().strip('"')
        if not candidate.lower().endswith(".srt"):
            return
        if len(candidate) > 4096 or " - INFO - " in candidate:
            return
        try:
            if os.path.isfile(candidate):
                self._add_output(candidate)
        except OSError:
            pass

    def _add_output(self, path: str) -> None:
        if path not in self.output_files:
            self.output_files.append(path)

    # -- handlers ---------------------------------------------------------

    def _on_found_files(self, d):
        self.file_total = max(1, d["total"])

    def _on_file_start(self, d):
        self.file_index = d["index"]
        self.file_total = max(1, d["total"])  # authoritative over found_files
        self.file_name = d["name"]
        self.step_index = 0
        self.task_total = 0
        self.task_pct = 0
        self.phase = "transcribe"
        self._reset_translation_counters()

    def _on_step(self, d):
        self.step_index = d["index"]
        self.step_total = max(1, d["total"])
        self.step_name = d["name"]
        self.task_total = 0
        self.task_pct = 0

    def _on_task_start(self, d):
        self.task_name = d["name"]
        self.task_total = d["total"]
        self.task_current = 0
        self.task_pct = 0

    def _on_task_progress(self, d):
        self.task_name = d["name"]
        self.task_current = d["current"]
        self.task_total = d["total"]
        self.task_pct = d["pct"]
        self.elapsed_str = d["elapsed"]
        self.eta_str = d["eta"]

    def _on_file_done(self, d):
        self.files_done = max(self.files_done, self.file_index)
        self.step_index = self.step_total
        self.task_pct = 100
        if not d["success"]:
            self.last_error = f"Failed: {d['name']}"

    def _on_skipped(self, d):
        self.files_done += 1
        self.file_index = max(self.file_index, self.files_done)

    def _on_tr_phase(self, _d):
        self.phase = "translate"
        self._reset_translation_counters()

    def _on_tr_start(self, d):
        self.phase = "translate"
        self.tr_scenes_total = d["scenes_total"]
        self.tr_batches_total = d["batches_total"]
        self.tr_batch_done = 0
        self.tr_scene_done = 0
        self.tr_pct = 0.0

    def _on_tr_batch(self, d):
        self.phase = "translate"
        self.tr_batch_done = d["done"]
        self.tr_batches_total = d["total"] or self.tr_batches_total
        self.tr_pct = float(d["pct"])

    def _on_tr_batch_n(self, d):
        # No total available — hold the fraction rather than invent one.
        self.tr_batch_done = d["done"]

    def _on_tr_scene(self, d):
        self.tr_scene_done = d["done"]
        self.tr_scenes_total = d["total"] or self.tr_scenes_total
        if not self.tr_batches_total and self.tr_scenes_total:
            self.tr_pct = 100.0 * self.tr_scene_done / self.tr_scenes_total

    def _on_tr_scene_n(self, d):
        self.tr_scene_done = d["done"]

    def _on_tr_file(self, d):
        self.phase = "translate"
        self.tr_file_index = d["index"]
        self.tr_file_total = max(1, d["total"])
        self._reset_translation_counters()

    def _on_tr_file_done(self, d):
        self.tr_files_done = max(self.tr_files_done, self.tr_file_index)
        self.tr_pct = 100.0
        if not d["success"]:
            self.last_error = f"Failed: {d['name']}"

    def _on_tr_done(self, d):
        self.tr_pct = 100.0
        name = d.get("name")
        if name and self.output_dir:
            candidate = os.path.join(self.output_dir, os.path.basename(name))
            if os.path.isfile(candidate):
                self._add_output(candidate)

    def _on_error(self, d):
        self.last_error = d.get("message")

    def _on_diag(self, d):
        self.diagnostics[d["key"]] = d["value"]

    def _reset_translation_counters(self):
        self.tr_scenes_total = 0
        self.tr_batches_total = 0
        self.tr_batch_done = 0
        self.tr_scene_done = 0
        self.tr_pct = 0.0

    # -- output -----------------------------------------------------------

    def _overall_pct(self) -> float:
        if self.flow == "translate":
            done = max(self.tr_files_done, self.tr_file_index - 1, 0)
            frac = min(1.0, self.tr_pct / 100.0)
            pct = (done + frac) / max(1, self.tr_file_total) * 100.0
        else:
            intra_step = min(1.0, self.task_pct / 100.0) if self.task_total else 0.0
            intra_file = (max(0, self.step_index - 1) + intra_step) / self.step_total
            intra_file = min(1.0, intra_file)

            if self.flow == "transcribe_translate":
                # Both phases must share one scale, otherwise ASR finishing at
                # 100% would pin the monotonic clamp before translation even
                # starts. ASR owns [0, w); translation owns [w, 1].
                w = ASR_WEIGHT_IN_COMBINED
                if self.phase == "translate":
                    intra_file = w + (1.0 - w) * min(1.0, self.tr_pct / 100.0)
                else:
                    intra_file = w * intra_file

            base = max(0, self.file_index - 1)
            pct = (base + intra_file) / max(1, self.file_total) * 100.0

        return max(0.0, min(100.0, pct))

    def snapshot(self) -> Dict[str, Any]:
        # Monotonic clamp: unified_progress.py throttles at ~10% milestones, so
        # the bar jumps by design. Never interpolate — a smoothed bar lies
        # during a stall.
        pct = self._overall_pct()
        self._max_pct = max(self._max_pct, pct)
        return {
            "overall_pct": round(self._max_pct, 1),
            "phase": self.phase,
            "file_index": self.file_index,
            "file_total": self.file_total,
            "file_name": self.file_name,
            "step_index": self.step_index,
            "step_total": self.step_total,
            "step_name": self.step_name,
            "task_name": self.task_name,
            "task_current": self.task_current,
            "task_total": self.task_total,
            "task_pct": self.task_pct,
            "elapsed_str": self.elapsed_str,
            "eta_str": self.eta_str,
            "translate": {
                "scenes_total": self.tr_scenes_total,
                "batches_total": self.tr_batches_total,
                "batch_done": self.tr_batch_done,
                "scene_done": self.tr_scene_done,
                "pct": round(self.tr_pct, 1),
                "file_index": self.tr_file_index,
                "file_total": self.tr_file_total,
            },
            "diagnostics": dict(self.diagnostics),
            "last_message": self.last_message,
            "last_error": self.last_error,
        }

    def mark_complete(self) -> None:
        """Called when the process exits 0 — pin the bar at 100."""
        self._max_pct = 100.0
