"""argv construction for the three web-UI flows."""

import sys

import pytest

from whisperjav.webserver import argbuild


def tail(argv):
    """Drop the interpreter + -X utf8 -u -m scaffolding."""
    assert argv[0] == sys.executable
    assert argv[1:5] == ["-X", "utf8", "-u", "-m"]
    return argv[5:]


TRANSCRIBE = {
    "mode": "faster",
    "language": "japanese",
    "subs_language": "native",
    "sensitivity": "balanced",
    "output_dir": r"D:\out",
    "output_format": "srt",
    "log_level": "INFO",
}

TRANSLATE = {
    "provider": "ollama",
    "target": "chinese",
    "source": "japanese",
    "tone": "standard",
}


def test_transcribe_argv():
    argv, _ = argbuild.build_command("transcribe", TRANSCRIBE, {}, [r"D:\a.mp4"])
    assert tail(argv) == [
        "whisperjav.main", r"D:\a.mp4",
        "--mode", "faster",
        "--language", "japanese",
        "--subs-language", "native",
        "--sensitivity", "balanced",
        "--output-dir", r"D:\out",
    ]


def test_transcribe_defaults_to_source_sentinel():
    argv, _ = argbuild.build_command("transcribe", {}, {}, [r"D:\a.mp4"])
    args = tail(argv)
    assert args[args.index("--output-dir") + 1] == "source"


def test_check_only_short_circuits():
    argv, _ = argbuild.build_command(
        "transcribe", {"check_only": True}, {}, [])
    assert tail(argv) == ["whisperjav.main", "--check", "--check-verbose"]


def test_transcribe_optional_flags():
    opts = dict(TRANSCRIBE, output_format="both", log_level="DEBUG",
                keep_temp=True, skip_existing=True, accept_cpu_mode=True,
                model=r"D:\models\ct2", temp_dir=r"D:\tmp")
    args = tail(argbuild.build_command("transcribe", opts, {}, ["a.mp4"])[0])
    for flag in ("--keep-temp", "--skip-existing", "--accept-cpu-mode"):
        assert flag in args
    assert args[args.index("--output-format") + 1] == "both"
    assert args[args.index("--log-level") + 1] == "DEBUG"
    assert args[args.index("--model") + 1] == r"D:\models\ct2"


@pytest.mark.parametrize("mode,flag", [
    ("qwen", "--qwen-model-id"),
    ("transformers", "--hf-model-id"),
])
def test_model_override_uses_the_mode_specific_flag(mode, flag):
    opts = dict(TRANSCRIBE, mode=mode, model=r"D:\models\Qwen3-ASR-1.7B-JA-hf")
    args = tail(argbuild.build_command("transcribe", opts, {}, ["a.mp4"])[0])
    assert args[args.index(flag) + 1] == r"D:\models\Qwen3-ASR-1.7B-JA-hf"
    assert "--model" not in args


def test_qwen_aligner_options():
    opts = dict(TRANSCRIBE, mode="qwen", qwen_timestamp_mode="aligner_interpolation",
                qwen_aligner=r"D:\models\Qwen3-ForcedAligner-0.6B-hf")
    args = tail(argbuild.build_command("transcribe", opts, {}, ["a.mp4"])[0])
    assert args[args.index("--qwen-timestamp-mode") + 1] == "aligner_interpolation"
    assert args[args.index("--qwen-aligner") + 1] == r"D:\models\Qwen3-ForcedAligner-0.6B-hf"


def test_qwen_vad_only_is_left_to_the_cli_default():
    opts = dict(TRANSCRIBE, mode="qwen", qwen_timestamp_mode="vad_only")
    args = tail(argbuild.build_command("transcribe", opts, {}, ["a.mp4"])[0])
    assert "--qwen-timestamp-mode" not in args
    assert "--qwen-aligner" not in args


def test_qwen_options_ignored_for_other_modes():
    opts = dict(TRANSCRIBE, mode="balanced", qwen_timestamp_mode="aligner_only",
                qwen_aligner=r"D:\models\aligner")
    args = tail(argbuild.build_command("transcribe", opts, {}, ["a.mp4"])[0])
    assert "--qwen-timestamp-mode" not in args
    assert "--qwen-aligner" not in args


def test_translate_argv_uses_dash_i():
    argv, _ = argbuild.build_command(
        "translate", {}, dict(TRANSLATE, include_original=True), [r"D:\a.srt"])
    args = tail(argv)
    assert args[0] == "whisperjav.translate.cli"
    assert args[1] == "-i"
    assert args[2] == r"D:\a.srt"
    assert "--include-original" in args
    assert args[args.index("--target") + 1] == "chinese"
    # Non-interactive: the subprocess has no stdin to answer a prompt with.
    assert "--yes" in args and "--stream" in args


def test_translate_no_include_original_is_explicit():
    args = tail(argbuild.build_command(
        "translate", {}, dict(TRANSLATE, include_original=False), ["a.srt"])[0])
    assert "--no-include-original" in args
    assert "--include-original" not in args


def test_translate_omits_output_when_unset_or_sentinel():
    for value in (None, "", "source"):
        args = tail(argbuild.build_command(
            "translate", {}, dict(TRANSLATE, output_dir=value), ["a.srt"])[0])
        assert "-o" not in args, value


def test_translate_passes_output_dir_when_real():
    args = tail(argbuild.build_command(
        "translate", {}, dict(TRANSLATE, output_dir=r"D:\out"), ["a.srt"])[0])
    assert args[args.index("-o") + 1] == r"D:\out"


def test_combined_flow_is_a_single_process():
    argv, _ = argbuild.build_command(
        "transcribe_translate", TRANSCRIBE,
        dict(TRANSLATE, include_original=True), [r"D:\a.mp4"])
    args = tail(argv)
    assert args[0] == "whisperjav.main"
    assert "whisperjav.translate.cli" not in args
    assert "--translate" in args
    assert args[args.index("--translate-provider") + 1] == "ollama"
    assert args[args.index("--translate-target") + 1] == "chinese"
    assert "--translate-include-original" in args
    assert "--yes" in args


def test_api_key_is_redacted_in_display_but_kept_in_argv():
    argv, display = argbuild.build_command(
        "translate", {}, dict(TRANSLATE, provider="deepseek",
                              api_key="sk-secret-value"), ["a.srt"])
    assert "sk-secret-value" in argv
    assert "sk-secret-value" not in display
    assert "--api-key ***" in display


def test_combined_flow_redacts_translate_key():
    _, display = argbuild.build_command(
        "transcribe_translate", TRANSCRIBE,
        dict(TRANSLATE, provider="deepseek", api_key="sk-nope"), ["a.mp4"])
    assert "sk-nope" not in display
    assert "--translate-api-key ***" in display


def test_empty_inputs_rejected():
    with pytest.raises(ValueError):
        argbuild.build_command("transcribe", TRANSCRIBE, {}, [])
    with pytest.raises(ValueError):
        argbuild.build_command("translate", {}, TRANSLATE, [])


def test_unknown_flow_rejected():
    with pytest.raises(ValueError):
        argbuild.build_command("nope", {}, {}, ["a.mp4"])
