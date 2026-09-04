"""재시도 계측(common.log_retry / _retry_navigation)과 타임아웃 부분 결과 복원 회귀 테스트.

배경: 2026-09-04 닥터빌 런이 240초 타임아웃으로 죽으면서 이미 끝난 태스크까지
"계정 failed" 한 줄로 뭉개졌고, 그 앞의 백오프 재시도가 값을 했는지 판단할
숫자가 어디에도 없었다.
"""
import json
import sys
from pathlib import Path

import pytest
from playwright.sync_api import Error as PlaywrightError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import common  # noqa: E402
import daily_runner  # noqa: E402


class FakePage:
    """wait_for_timeout이 실제로 자지 않는 가짜 page."""
    url = "https://example.test/start"

    def __init__(self):
        self.waited_ms = []

    def wait_for_timeout(self, ms):
        self.waited_ms.append(ms)


def _entries(path):
    return [json.loads(l) for l in Path(path).read_text(encoding="utf-8").splitlines() if l.strip()]


def test_no_log_when_first_attempt_succeeds(monkeypatch, tmp_path):
    monkeypatch.setattr(common, "ERROR_LOG_DIR", tmp_path)
    page = FakePage()
    common._retry_navigation("goto", "u", lambda: None, page, 2, (3.0, 7.0, 15.0))
    assert not list(tmp_path.glob("retries-*.jsonl"))  # 평상시엔 0바이트


def test_recovered_retry_is_recorded(monkeypatch, tmp_path):
    monkeypatch.setattr(common, "ERROR_LOG_DIR", tmp_path)
    page = FakePage()
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise PlaywrightError("net::ERR_CONNECTION_CLOSED")

    common._retry_navigation("goto", "https://a.test/p?token=secret", flaky, page, 2, (3.0, 7.0, 15.0))

    entry = _entries(next(tmp_path.glob("retries-*.jsonl")))[0]
    assert entry["recovered"] is True
    assert entry["succeeded_at"] == 3
    assert entry["attempts"] == 2
    assert entry["waited_sec"] == 10.0  # 3 + 7
    assert entry["url"] == "https://a.test/p"  # 쿼리스트링은 남기지 않는다


def test_exhausted_retry_is_recorded_and_reraises(monkeypatch, tmp_path):
    monkeypatch.setattr(common, "ERROR_LOG_DIR", tmp_path)
    page = FakePage()

    def always_dead():
        raise PlaywrightError("net::ERR_CONNECTION_CLOSED")

    with pytest.raises(PlaywrightError):
        common._retry_navigation("goto", "https://a.test/p", always_dead, page, 2, (3.0, 7.0, 15.0))

    entry = _entries(next(tmp_path.glob("retries-*.jsonl")))[0]
    assert entry["recovered"] is False
    assert entry["succeeded_at"] == 0
    assert entry["attempts"] == 3
    assert entry["errors"][0]["msg"].startswith("net::ERR_CONNECTION_CLOSED")


def test_non_retryable_error_is_not_retried(monkeypatch, tmp_path):
    """코드/DOM 오류에 백오프를 태우지 않는다 — 1회 시도로 끝나야 한다."""
    monkeypatch.setattr(common, "ERROR_LOG_DIR", tmp_path)
    page = FakePage()

    def strict_violation():
        raise PlaywrightError("strict mode violation: locator resolved to 2 elements")

    with pytest.raises(PlaywrightError):
        common._retry_navigation("goto", "https://a.test/p", strict_violation, page, 2, (3.0, 7.0, 15.0))

    entry = _entries(next(tmp_path.glob("retries-*.jsonl")))[0]
    assert entry["attempts"] == 1
    assert entry["waited_sec"] == 0.0
    assert page.waited_ms == []


def test_salvage_progress_from_stderr():
    stderr = (
        "some playwright noise\n"
        '{"_progress": {"account": "bjh7790", "task": "attend", "status": "success"}}\n'
        '{"_progress": {"account": "bjh7790", "task": "quiz", "status": "already_done"}}\n'
        "not json\n"
    )
    assert daily_runner._salvage_progress(stderr) == {"attend": "success", "quiz": "already_done"}


def test_salvage_progress_handles_empty_and_bytes():
    assert daily_runner._salvage_progress(None) == {}
    assert daily_runner._salvage_progress("") == {}
    assert daily_runner._salvage_progress(
        '{"_progress": {"task": "attend", "status": "success"}}'.encode()
    ) == {"attend": "success"}


def test_run_script_timeout_attaches_partial(monkeypatch, tmp_path):
    """타임아웃이어도 이미 끝난 태스크는 알림에 남아야 한다."""
    import subprocess

    monkeypatch.setattr(common, "ERROR_LOG_DIR", tmp_path)

    def fake_run(*a, **kw):
        raise subprocess.TimeoutExpired(
            cmd="doctorville.py", timeout=240, output="",
            stderr='{"_progress": {"task": "attend", "status": "success"}}\n',
        )

    monkeypatch.setattr(daily_runner.subprocess, "run", fake_run)
    res = daily_runner.run_script("doctorville.py", ["--account", "bjh7790"], timeout=240)

    assert res["status"] == "failed"
    assert res["partial"] == {"attend": "success"}
    assert "attend=success" in res["message"]
