"""
HTTP surface and job lifecycle.

Jobs here run a trivial `python -c` command instead of the real pipeline, so
the whole spawn -> stream -> terminal-state path is exercised without a GPU,
a model, or a network call.
"""

import sys
import time

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from whisperjav.webserver.app import create_app  # noqa: E402


@pytest.fixture
def client():
    app = create_app()
    with TestClient(app) as c:
        yield c


def wait_for(client, job_id, states, timeout=20.0):
    deadline = time.time() + timeout
    job = None
    while time.time() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["state"] in states:
            return job
        time.sleep(0.05)
    pytest.fail(f"Job stayed in {job and job['state']!r}, wanted one of {states}")


def submit_fake(client, script, flow="transcribe"):
    """Enqueue a job whose argv is replaced by a harmless python -c command."""
    manager = client.app.state.jobs
    argv = [sys.executable, "-u", "-c", script]
    job = manager.submit(flow, argv, "fake-command", ["fake-input"])
    return job.id


# --------------------------------------------------------------- basics

def test_health(client):
    body = client.get("/api/health").json()
    assert body["ok"] is True
    assert body["pid"] > 0


def test_index_is_served(client):
    res = client.get("/")
    assert res.status_code == 200
    assert "WhisperJAV" in res.text


def test_config_exposes_real_constants(client):
    cfg = client.get("/api/config").json()
    assert "balanced" in cfg["transcribe"]["mode"]
    assert "qwen" in cfg["transcribe"]["mode"]
    # SUPPORTED_TARGETS is a set literal; the endpoint must sort it so the
    # dropdown order is stable across restarts.
    assert cfg["translate"]["targets"] == sorted(cfg["translate"]["targets"])
    assert "chinese" in cfg["translate"]["targets"]
    ids = [p["id"] for p in cfg["translate"]["providers"]]
    assert "ollama" in ids and "deepseek" in ids
    assert "local" not in ids  # deprecated in v1.8.10


# --------------------------------------------------------------- browsing

def test_browse_root_lists_drives(client):
    data = client.get("/api/browse").json()
    assert data["path"] is None
    assert data["roots"]


def test_browse_bad_path_is_400_not_500(client):
    bad = r"Q:\definitely\not\here" if sys.platform == "win32" else "/nope/nope"
    res = client.get("/api/browse", params={"path": bad})
    assert res.status_code == 400
    assert res.json()["error"] == "not_found"


def test_browse_a_real_directory(client, tmp_path):
    (tmp_path / "v.mp4").write_bytes(b"x")
    data = client.get("/api/browse",
                      params={"path": str(tmp_path), "kind": "media"}).json()
    assert [f["name"] for f in data["files"]] == ["v.mp4"]


def test_browse_handles_ampersand_in_name(client, tmp_path):
    weird = tmp_path / "AB&C"
    weird.mkdir()
    (weird / "x.mp4").write_bytes(b"x")
    data = client.get("/api/browse",
                      params={"path": str(weird), "kind": "media"}).json()
    assert [f["name"] for f in data["files"]] == ["x.mp4"]


# --------------------------------------------------------------- validation

def test_submit_rejects_empty_inputs(client):
    res = client.post("/api/jobs", json={"flow": "transcribe", "inputs": []})
    assert res.status_code == 400


def test_submit_rejects_missing_input(client):
    res = client.post("/api/jobs", json={
        "flow": "transcribe", "inputs": [r"Q:\nope\a.mp4"]})
    assert res.status_code == 400
    assert "does not exist" in res.json()["detail"]


def test_submit_rejects_wrong_extension_for_flow(client, tmp_path):
    txt = tmp_path / "a.txt"
    txt.write_text("x", encoding="utf-8")
    res = client.post("/api/jobs", json={
        "flow": "transcribe", "inputs": [str(txt)]})
    assert res.status_code == 400


def test_translate_flow_requires_srt(client, tmp_path):
    mp4 = tmp_path / "a.mp4"
    mp4.write_bytes(b"x")
    res = client.post("/api/jobs", json={
        "flow": "translate", "inputs": [str(mp4)],
        "translate": {"provider": "ollama"}})
    assert res.status_code == 400
    assert ".srt" in res.json()["detail"]


def test_missing_api_key_is_caught_before_enqueue(client, tmp_path, monkeypatch):
    """
    Otherwise the user waits out a full transcription and the translation dies
    instantly at the end.
    """
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    srt = tmp_path / "a.srt"
    srt.write_text("1\n", encoding="utf-8")
    res = client.post("/api/jobs", json={
        "flow": "translate", "inputs": [str(srt)],
        "translate": {"provider": "deepseek"}})
    assert res.status_code == 400
    assert "DEEPSEEK_API_KEY" in res.json()["detail"]
    assert client.get("/api/jobs").json()["jobs"] == []


def test_api_key_from_env_satisfies_the_guard(client, tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    srt = tmp_path / "a.srt"
    srt.write_text("1\n", encoding="utf-8")
    res = client.post("/api/jobs", json={
        "flow": "translate", "inputs": [str(srt)],
        "translate": {"provider": "deepseek"}})
    assert res.status_code == 202


def test_submitted_command_redacts_the_key(client, tmp_path):
    srt = tmp_path / "a.srt"
    srt.write_text("1\n", encoding="utf-8")
    res = client.post("/api/jobs", json={
        "flow": "translate", "inputs": [str(srt)],
        "translate": {"provider": "deepseek", "api_key": "sk-secret"}})
    assert res.status_code == 202
    assert "sk-secret" not in res.json()["command"]


# --------------------------------------------------------------- lifecycle

def test_job_runs_to_completion_and_streams_logs(client):
    job_id = submit_fake(client, "print('hello from the job')")
    job = wait_for(client, job_id, {"completed", "failed"})
    assert job["state"] == "completed"
    assert job["exit_code"] == 0

    logs = client.get(f"/api/jobs/{job_id}/logs", params={"since": 0}).json()
    assert any("hello from the job" in line for line in logs["lines"])
    assert logs["next_since"] == job["log_seq"]


def test_failed_job_reports_exit_code(client):
    job_id = submit_fake(client, "import sys; sys.exit(3)")
    job = wait_for(client, job_id, {"completed", "failed"})
    assert job["state"] == "failed"
    assert job["exit_code"] == 3
    assert "3" in job["error"]


def test_progress_is_parsed_from_a_running_job(client):
    script = (
        "print('2026-08-15 10:00:00 - whisperjav - INFO - "
        "Found 1 media file(s) to process:');"
        "print('2026-08-15 10:00:00 - whisperjav - INFO - "
        "[1/1] Starting: a.mp4');"
        "print('2026-08-15 10:00:00 - whisperjav - INFO - "
        "Step 3/5: Transcribing scenes');"
        "print('2026-08-15 10:00:00 - whisperjav - INFO - "
        "Progress: Transcribing scenes - 10 items queued');"
        "print('2026-08-15 10:00:00 - whisperjav - INFO - "
        "Progress: Transcribing scenes 5/10 (50%) elapsed 0:05, ETA 0:05')"
    )
    job_id = submit_fake(client, script)
    job = wait_for(client, job_id, {"completed", "failed"})
    assert job["state"] == "completed"
    p = job["progress"]
    assert p["file_index"] == 1 and p["file_total"] == 1
    assert p["step_index"] == 3 and p["step_name"] == "Transcribing scenes"
    assert p["eta_str"] == "0:05"
    # mark_complete pins a finished job at 100 regardless of the last line.
    assert p["overall_pct"] == 100.0


def test_incremental_log_cursor(client):
    job_id = submit_fake(client, "print('one'); print('two'); print('three')")
    wait_for(client, job_id, {"completed", "failed"})

    first = client.get(f"/api/jobs/{job_id}", params={"with_logs": 0}).json()
    assert first["lines"]
    second = client.get(f"/api/jobs/{job_id}",
                        params={"with_logs": first["next_since"]}).json()
    assert second["lines"] == []
    assert second["next_since"] == first["next_since"]


def test_cancel_a_running_job(client):
    job_id = submit_fake(client, "import time; time.sleep(120)")
    wait_for(client, job_id, {"running"})
    res = client.post(f"/api/jobs/{job_id}/cancel")
    assert res.status_code == 200
    job = wait_for(client, job_id, {"cancelled"}, timeout=15)
    assert job["state"] == "cancelled"


def test_cancel_immediately_after_submit(client):
    """
    Regression: the state used to flip to "running" before Popen returned, so
    a cancel landing in that window found no process and did nothing.
    """
    job_id = submit_fake(client, "import time; time.sleep(120)")
    client.post(f"/api/jobs/{job_id}/cancel")
    job = wait_for(client, job_id, {"cancelled"}, timeout=15)
    assert job["state"] == "cancelled"


def test_second_job_queues_behind_the_first(client):
    slow = submit_fake(client, "import time; time.sleep(120)")
    wait_for(client, slow, {"running"})
    queued = submit_fake(client, "print('later')")
    assert client.get(f"/api/jobs/{queued}").json()["state"] == "queued"
    client.post(f"/api/jobs/{slow}/cancel")
    wait_for(client, queued, {"completed"}, timeout=20)


def test_cancel_a_queued_job_never_starts_it(client):
    slow = submit_fake(client, "import time; time.sleep(120)")
    wait_for(client, slow, {"running"})
    queued = submit_fake(client, "print('should not run')")
    client.post(f"/api/jobs/{queued}/cancel")
    job = client.get(f"/api/jobs/{queued}").json()
    assert job["state"] == "cancelled"
    assert job["pid"] is None
    client.post(f"/api/jobs/{slow}/cancel")


def test_delete_rejects_an_active_job(client):
    job_id = submit_fake(client, "import time; time.sleep(120)")
    wait_for(client, job_id, {"running"})
    assert client.delete(f"/api/jobs/{job_id}").status_code == 409
    client.post(f"/api/jobs/{job_id}/cancel")


def test_delete_a_finished_job(client):
    job_id = submit_fake(client, "print('done')")
    wait_for(client, job_id, {"completed"})
    assert client.delete(f"/api/jobs/{job_id}").status_code == 204
    assert client.get(f"/api/jobs/{job_id}").status_code == 404


def test_native_crash_code_is_decoded(client):
    """
    A real run died with 3221225620. "Exited with code 3221225620" tells the
    user nothing; the message must name the fault.
    """
    from whisperjav.webserver.jobs import describe_exit_code

    msg = describe_exit_code(3221225620)
    assert "INTEGER_DIVIDE_BY_ZERO" in msg
    assert "0xC0000094" in msg
    assert "no Python" in msg  # explains why there is no traceback

    assert "ACCESS_VIOLATION" in describe_exit_code(0xC0000005)
    assert "STACK_BUFFER_OVERRUN" in describe_exit_code(0xC0000409)
    assert describe_exit_code(3) == "Exited with code 3"
    assert describe_exit_code(None)


def test_unknown_job_is_404(client):
    assert client.get("/api/jobs/deadbeef").status_code == 404
