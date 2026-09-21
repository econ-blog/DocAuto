"""seminar_live 실패 노드가 사유(message)를 싣는지 검증한다.

배경: 2026-09-18 19:34~19:40 KST seminar_block 런에서 doctorville.co.kr 접속이
막혀 seminar_live·seminar_survey가 같은 `Page.goto: Timeout 30000ms exceeded`로
죽었다. seminar_survey는 노드에 message를 남겨 claude_trigger가 재시도성으로
보고 첫 발생을 보류했으나(`retryable_first_occurrence`), seminar_live는 노드가
`{"status": "failed"}` 뿐이라 판정 재료가 없어 1회차부터 세션을 발사했다.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock

import claude_trigger
import seminar_live


TIMEOUT_MSG = (
    'Page.goto: Timeout 30000ms exceeded.\n'
    'Call log:\n  - navigating to "https://www.doctorville.co.kr/event/attend"'
)


def _run_with_goto_failure(monkeypatch, tmp_path) -> dict:
    creds_file = tmp_path / "credentials.json"
    creds_file.write_text(
        '{"bjh7790": {"email": "bjh7790@gmail.com",'
        ' "doctorville": {"id": "user", "password": "pw"}}}',
        encoding="utf-8",
    )

    mock_playwright = MagicMock()
    mock_playwright.__enter__.return_value.chromium.launch.return_value = MagicMock()

    def failing_goto(*args, **kwargs):
        raise TimeoutError(TIMEOUT_MSG)

    monkeypatch.setattr(seminar_live, "sync_playwright", lambda: mock_playwright)
    monkeypatch.setattr(seminar_live.common, "goto_with_retry", failing_goto)
    monkeypatch.setattr(seminar_live, "save_screenshot", lambda page, tag: "")
    monkeypatch.setattr(seminar_live.common, "log_error", lambda *a, **kw: None)

    return seminar_live.run_account(
        account="bjh7790",
        credentials_path=creds_file,
        headless=True,
        stay_seconds=0,
    )


def test_run_account_records_message_on_navigation_failure(monkeypatch, tmp_path):
    result = _run_with_goto_failure(monkeypatch, tmp_path)

    assert result["live_seminar"]["status"] == "failed"
    assert "Timeout 30000ms exceeded" in result["live_seminar"]["message"]


def test_transient_failure_defers_first_session_fire(monkeypatch, tmp_path):
    """사유가 실려야 claude_trigger가 일시 장애의 첫 발생을 보류한다."""
    result = _run_with_goto_failure(monkeypatch, tmp_path)

    results_file = tmp_path / "results-seminar_live.json"
    results_file.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")

    items = claude_trigger.collect_actionable_items([Path(results_file)])
    assert [it["status"] for it in items] == ["failed"]

    to_fire, deferred = claude_trigger.evaluate_items(items, [], "2026-09-18")
    assert to_fire == []
    assert [d["reason"] for d in deferred] == ["retryable_first_occurrence"]
