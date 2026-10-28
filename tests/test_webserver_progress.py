"""Progress parsing and overall-percentage math."""

import pytest

from whisperjav.webserver.progress import (
    ProgressTracker,
    normalize,
    parse_line,
    split_stream_line,
    strip_ansi,
)

PREFIX = "2026-08-15 10:02:11 - whisperjav - INFO - "


def env(message):
    """Wrap a message in the real console log envelope."""
    return PREFIX + message


def kind_of(raw):
    event = parse_line(normalize(raw))
    return event.kind if event else None


# --------------------------------------------------------------- normalize

def test_strip_ansi():
    assert strip_ansi("\x1b[36mhello\x1b[0m") == "hello"


def test_ansi_wrapped_line_still_parses():
    """
    The guard against the re.match trap.

    logger.py sets _USE_COLOR = True whenever colorama imports, regardless of
    tty, so colour codes can reach a pipe. A regression here is silent: the
    progress bar simply stops moving.
    """
    raw = "\x1b[36m" + env("Step 3/5: Transcribing scenes") + "\x1b[0m"
    event = parse_line(normalize(raw))
    assert event is not None
    assert event.kind == "step"
    assert event.data == {"index": 3, "total": 5, "name": "Transcribing scenes"}


def test_normalize_strips_only_the_first_envelope():
    assert normalize(env("Step 1/5: x")) == "Step 1/5: x"


# --------------------------------------------------------------- patterns

@pytest.mark.parametrize("raw,expected", [
    (env("Found 2 media file(s) to process:"), "found_files"),
    (env("[1/2] Starting: SDMT940C.mp4"), "file_start"),
    (env("Step 3/5: Transcribing scenes"), "step"),
    (env("Progress: Transcribing scenes - 400 items queued"), "task_start"),
    (env("Progress: Transcribing scenes 180/400 (45%) elapsed 3:02, ETA 3:41"),
     "task_progress"),
    (env("\u2713 Complete: SDMT940C.mp4"), "file_done"),
    (env("\u2717 Failed: SDMT940C.mp4"), "file_done"),
    (env("Starting translation..."), "tr_phase"),
    (env("Translation progress: starting - 18 scenes / 42 batches"), "tr_start"),
    (env("Translation progress: batch 21/42 (50%)"), "tr_batch"),
    (env("Translation progress: scene 3/18 complete"), "tr_scene"),
    (env("Translation complete: SDMT940C.english.srt"), "tr_done"),
    ("Translating [1/3]: a.srt", "tr_file"),
    ("Complete: a.english.srt", "tr_file_done"),
    ("[TRANSLATE]   Target language (resolved): Simplified Chinese", "diag"),
    ("TRANSLATION FAILED", "error"),
    (env("just some unrelated chatter"), None),
])
def test_line_classification(raw, expected):
    assert kind_of(raw) == expected


def test_task_progress_fields():
    event = parse_line(normalize(
        env("Progress: Transcribing scenes 180/400 (45%) elapsed 3:02, ETA 3:41")))
    assert event.data["current"] == 180
    assert event.data["total"] == 400
    assert event.data["pct"] == 45
    assert event.data["elapsed"] == "3:02"
    assert event.data["eta"] == "3:41"


def test_task_progress_without_eta_and_with_hours():
    event = parse_line(normalize(
        env("Progress: Transcribing 180/400 (45%) elapsed 1:03:02")))
    assert event.data["elapsed"] == "1:03:02"
    assert event.data["eta"] is None


def test_checkmark_complete_is_not_mistaken_for_translate_complete():
    """
    R_TR_FILEDONE is ^-anchored (after prefix stripping) precisely so that
    "✓ Complete: SDMT940C.mp4" classifies as file_done, not tr_file_done.
    """
    assert kind_of(env("\u2713 Complete: SDMT940C.mp4")) == "file_done"
    assert kind_of("Complete: a.english.srt") == "tr_file_done"


def test_progress_ordering_prefers_percentage_over_queued():
    assert kind_of(env("Progress: X 1/2 (50%) elapsed 0:01")) == "task_progress"
    assert kind_of(env("Progress: X - 2 items queued")) == "task_start"


# --------------------------------------------------------------- flow 1

def feed_all(tracker, messages):
    for m in messages:
        tracker.feed(normalize(m))
    return tracker.snapshot()


def test_transcribe_overall_progress():
    t = ProgressTracker("transcribe")
    snap = feed_all(t, [
        env("Found 1 media file(s) to process:"),
        env("[1/1] Starting: a.mp4"),
        env("Step 1/5: Audio extraction"),
    ])
    assert snap["file_total"] == 1
    assert 0 <= snap["overall_pct"] <= 20

    snap = feed_all(t, [
        env("Step 3/5: Transcribing scenes"),
        env("Progress: Transcribing scenes - 400 items queued"),
        env("Progress: Transcribing scenes 200/400 (50%) elapsed 3:02, ETA 3:02"),
    ])
    # (step 3-1 + 0.5) / 5 = 50%
    assert snap["overall_pct"] == pytest.approx(50.0, abs=0.5)
    assert snap["eta_str"] == "3:02"


def test_transcribe_reaches_100_at_the_end():
    t = ProgressTracker("transcribe")
    snap = feed_all(t, [
        env("[1/1] Starting: a.mp4"),
        env("Step 5/5: Post-processing"),
        env("Progress: Refining - 10 items queued"),
        env("Progress: Refining 10/10 (100%) elapsed 0:05"),
    ])
    assert snap["overall_pct"] == pytest.approx(100.0, abs=0.1)


def test_file_start_total_overrides_found_files():
    t = ProgressTracker("transcribe")
    snap = feed_all(t, [
        env("Found 9 media file(s) to process:"),
        env("[1/2] Starting: a.mp4"),
    ])
    assert snap["file_total"] == 2


def test_progress_is_monotonic_under_out_of_order_lines():
    t = ProgressTracker("transcribe")
    feed_all(t, [
        env("[1/1] Starting: a.mp4"),
        env("Step 4/5: Stitching"),
        env("Progress: X - 10 items queued"),
        env("Progress: X 10/10 (100%) elapsed 0:05"),
    ])
    high = t.snapshot()["overall_pct"]
    # A late line from an earlier step must not rewind the bar.
    t.feed(normalize(env("Step 2/5: Scene detection")))
    assert t.snapshot()["overall_pct"] == high


# --------------------------------------------------------------- flow 2

def test_translate_flow_progress():
    t = ProgressTracker("translate")
    snap = feed_all(t, [
        "Translating [1/2]: a.srt",
        env("Translation progress: starting - 10 scenes / 20 batches"),
        env("Translation progress: batch 10/20 (50%)"),
    ])
    # first of two files, half done -> 25%
    assert snap["overall_pct"] == pytest.approx(25.0, abs=0.5)
    assert snap["translate"]["batch_done"] == 10

    snap = feed_all(t, ["Complete: a.chinese.srt", "Translating [2/2]: b.srt"])
    assert snap["overall_pct"] == pytest.approx(50.0, abs=0.5)


def test_translate_falls_back_to_scene_fraction():
    t = ProgressTracker("translate")
    snap = feed_all(t, [
        "Translating [1/1]: a.srt",
        env("Translation progress: starting - 4 scenes / 0 batches"),
        env("Translation progress: scene 2/4 complete"),
    ])
    assert snap["overall_pct"] == pytest.approx(50.0, abs=0.5)


# --------------------------------------------------------------- flow 3

def test_combined_flow_crosses_the_asr_weight_at_the_phase_switch():
    """
    Flow 3 never emits 'Translating [i/N]' — whisperjav/main.py:1302 calls
    translate_with_config() in-process. The tracker must not depend on it.
    """
    t = ProgressTracker("transcribe_translate")
    feed_all(t, [
        env("Found 1 media file(s) to process:"),
        env("[1/1] Starting: a.mp4"),
        env("Step 5/5: Post-processing"),
        env("Progress: X - 1 items queued"),
        env("Progress: X 1/1 (100%) elapsed 0:05"),
    ])
    snap = t.feed(normalize(env("Starting translation..."))) or t.snapshot()
    snap = t.snapshot()
    assert snap["phase"] == "translate"
    assert snap["overall_pct"] == pytest.approx(85.0, abs=0.5)

    snap = feed_all(t, [
        env("Translation progress: starting - 4 scenes / 8 batches"),
        env("Translation progress: batch 4/8 (50%)"),
    ])
    assert snap["overall_pct"] == pytest.approx(92.5, abs=0.5)

    snap = feed_all(t, [env("Translation complete: a.chinese.srt")])
    assert snap["overall_pct"] == pytest.approx(100.0, abs=0.1)


def test_combined_flow_ignores_missing_translating_line():
    t = ProgressTracker("transcribe_translate")
    snap = feed_all(t, [
        env("[1/1] Starting: a.mp4"),
        env("Starting translation..."),
    ])
    assert snap["translate"]["file_index"] == 0
    assert snap["phase"] == "translate"


# --------------------------------------------------------------- misc

def test_diagnostics_are_collected():
    t = ProgressTracker("translate")
    snap = feed_all(t, [
        "[TRANSLATE]   Target language (resolved): Simplified Chinese",
        "[TRANSLATE]   Include original (bilingual): True",
    ])
    assert snap["diagnostics"]["target_language"] == "Simplified Chinese"
    assert snap["diagnostics"]["include_original"] == "True"


def test_bare_srt_stdout_line_is_captured_as_output(tmp_path):
    """translate/cli.py:849 prints the result path bare to stdout."""
    srt = tmp_path / "result.srt"
    srt.write_text("1\n", encoding="utf-8")
    t = ProgressTracker("translate")
    t.feed(normalize(str(srt)))
    assert str(srt) in t.output_files


def test_nonexistent_srt_line_is_not_captured():
    t = ProgressTracker("translate")
    t.feed(normalize(r"D:\definitely\missing\nope.srt"))
    assert t.output_files == []


# --------------------------------------------------- carriage-return merging

def test_split_stream_line_separates_a_glued_progress_bar():
    """
    Verbatim from a real run: the pipeline's own bar is drawn with a leading
    \\r and end="", so it has no terminator and the next logger record gets
    glued onto it.
    """
    raw = ("Transcribing: [====------] 200/207 [96.6%] | FLNS-113_scene_0199.wav"
           " | ETA: 15s" + env("Starting speech segmentation with: silero-v3.1"))
    parts = split_stream_line(raw)
    assert len(parts) == 2
    assert parts[0].endswith("ETA: 15s")
    assert parts[1] == env("Starting speech segmentation with: silero-v3.1")


def test_split_stream_line_leaves_a_normal_record_alone():
    line = env("Step 4/6: Transcribing scenes with VAD")
    assert split_stream_line(line) == [line]


def test_split_stream_line_drops_blank_and_bare_cr():
    assert split_stream_line("\r\n") == []
    assert split_stream_line("   \n") == []


def test_glued_progress_line_still_parses_after_splitting():
    raw = ("Transcribing: [=====-----] 190/207 [91.8%] | x.wav | ETA: 36s"
           + env("Progress: Transcribing scenes 190/207 (92%) elapsed 6:39, ETA 0:35"))
    kinds = [parse_line(normalize(p)) for p in split_stream_line(raw)]
    assert kinds[1] is not None
    assert kinds[1].kind == "task_progress"
    assert kinds[1].data["current"] == 190
    assert kinds[1].data["eta"] == "0:35"


def test_mark_complete_pins_the_bar():
    t = ProgressTracker("transcribe")
    t.mark_complete()
    assert t.snapshot()["overall_pct"] == 100.0
