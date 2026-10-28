"""
The find_alignment guard against ctranslate2's divide-by-zero.

No model or GPU needed: the guard short-circuits before touching self, so it
can be driven with dummies. The crash it prevents is a hardware fault
(0xC0000094) that cannot be caught with try/except, which is why the guard has
to sit in front of the native call rather than around it.
"""

import pytest

pytest.importorskip("faster_whisper")

from faster_whisper import WhisperModel  # noqa: E402

from whisperjav.modules import faster_whisper_patches  # noqa: E402,F401


def test_guard_is_installed_on_import():
    assert getattr(WhisperModel.find_alignment, "_whisperjav_guarded", False)


def test_guard_is_idempotent():
    before = WhisperModel.find_alignment
    faster_whisper_patches.install_find_alignment_guard()
    faster_whisper_patches.install_find_alignment_guard()
    assert WhisperModel.find_alignment is before, "guard double-wrapped"


@pytest.mark.parametrize("num_frames", [0, 1])
def test_short_segment_returns_empty_alignments(num_frames):
    """
    Verified against faster-whisper 1.2.1 / ctranslate2 4.7.1: num_frames of
    0 and 1 crash the process inside the native align(); 2 and above are fine.
    """
    out = WhisperModel.find_alignment(
        None, None, [[123, 456]], None, num_frames)
    assert out == [[]]


def test_batch_shape_is_preserved():
    """
    add_word_timestamps indexes median_max_durations[segment_idx], so the
    guard must return one entry per batch element, not a bare [].
    """
    out = WhisperModel.find_alignment(
        None, None, [[1], [2], [3]], None, 0)
    assert out == [[], [], []]


def test_normal_frame_count_delegates_to_the_original(monkeypatch):
    seen = {}

    def fake_original(self, tokenizer, text_tokens, encoder_output,
                      num_frames, *a, **kw):
        seen["num_frames"] = num_frames
        return ["real alignment"]

    monkeypatch.setattr(WhisperModel, "find_alignment", fake_original)
    monkeypatch.delattr(faster_whisper_patches.WhisperModel.find_alignment,
                        "_whisperjav_guarded", raising=False)
    faster_whisper_patches.install_find_alignment_guard()

    assert WhisperModel.find_alignment(None, None, [[1]], None, 2) == \
        ["real alignment"]
    assert seen["num_frames"] == 2
    # ...and the guard still short-circuits below the threshold.
    assert WhisperModel.find_alignment(None, None, [[1]], None, 1) == [[]]
