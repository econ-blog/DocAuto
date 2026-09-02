"""common.log_error — 영구 오류 로그(logs/errors-YYYY-MM.jsonl) 회귀 테스트."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import common  # noqa: E402
import runlog  # noqa: E402


def _reload_error_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(common, "ERROR_LOG_DIR", tmp_path)


def test_log_error_writes_traceback_and_class(monkeypatch, tmp_path):
    _reload_error_dir(monkeypatch, tmp_path)
    try:
        raise ValueError("셀렉터 없음")
    except ValueError as e:
        path = common.log_error("hmp", e, account="bjh7790", task="capsule",
                                screenshot="/x/y/hmp_bjh7790_20260902.png")

    entry = json.loads(Path(path).read_text(encoding="utf-8").strip())
    assert entry["script"] == "hmp"
    assert entry["account"] == "bjh7790"
    assert entry["task"] == "capsule"
    assert entry["exc_type"] == "ValueError"
    assert "셀렉터 없음" in entry["message"]
    assert "ValueError" in entry["traceback"]
    # 경로가 아니라 파일명만 남긴다(러너마다 절대경로가 달라 비교가 안 된다)
    assert entry["screenshot"] == "hmp_bjh7790_20260902.png"
    assert entry["ts"]


def test_log_error_appends(monkeypatch, tmp_path):
    _reload_error_dir(monkeypatch, tmp_path)
    common.log_error("keymedi", RuntimeError("1회차"))
    path = common.log_error("keymedi", RuntimeError("2회차"))
    lines = Path(path).read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 2


def test_log_error_accepts_plain_string(monkeypatch, tmp_path):
    _reload_error_dir(monkeypatch, tmp_path)
    path = common.log_error("daily_runner", "JSON 파싱 실패. stdout: ...")
    entry = json.loads(Path(path).read_text(encoding="utf-8").strip())
    assert entry["exc_type"] == ""
    assert entry["traceback"] == ""
    assert "JSON 파싱 실패" in entry["message"]


def test_log_error_never_raises(monkeypatch):
    # 쓸 수 없는 경로여도 본 흐름을 죽이면 안 된다
    monkeypatch.setattr(common, "ERROR_LOG_DIR", Path("/proc/does/not/exist"))
    assert common.log_error("hmp", RuntimeError("x")) == ""


def test_prune_keeps_error_log(tmp_path):
    for i in range(10):
        (tmp_path / f"daily-2026-08-{i:02d}.json").write_text("{}", encoding="utf-8")
    err = tmp_path / "errors-2026-08.jsonl"
    err.write_text('{"script": "hmp"}\n', encoding="utf-8")

    removed = runlog.prune("daily", log_dir=tmp_path)

    assert len(removed) == 3  # 10 - KEEP_FILES(7)
    assert err.exists(), "오류 로그는 prune 대상이 아니다"


def test_is_retryable_rejects_non_network_errors():
    assert common._is_retryable(Exception("net::ERR_CONNECTION_CLOSED"))
    assert common._is_retryable(Exception("Timeout 15000ms exceeded"))
    # strict mode violation 같은 코드 문제는 재시도해도 나아지지 않는다
    assert not common._is_retryable(Exception("strict mode violation: locator resolved to 3 elements"))
    assert not common._is_retryable(Exception("Target page, context or browser has been closed"))
