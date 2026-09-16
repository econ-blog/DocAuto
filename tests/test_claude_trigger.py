import json
import urllib.error
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import claude_trigger
from common import KST


def test_compute_fingerprint_excludes_account():
    fp1 = claude_trigger.compute_fingerprint("2026-09-15", "seminar_survey", "survey", "unverified", "5473")
    fp2 = claude_trigger.compute_fingerprint("2026-09-15", "seminar_survey", "survey", "unverified", "5473")
    assert fp1 == "2026-09-15/seminar_survey/survey/unverified/5473"
    assert fp1 == fp2

    # Verify normalization for empty fields
    fp_empty = claude_trigger.compute_fingerprint("2026-09-15", "", "", "failed", "")
    assert fp_empty == "2026-09-15/_/_/failed/_"


def test_collect_actionable_items(tmp_path):
    results_file = tmp_path / "results-seminar_survey.json"
    data = {
        "bjh7790": {
            "status": "unverified",
            "surveys": [
                {
                    "seminarId": 5473,
                    "status": "unverified",
                    "message": "상세 버튼 프로브 불일치",
                    "verified_by": "",
                },
                {
                    "seminarId": 5474,
                    "status": "success",
                    "verified_by": "#capsuleBtnComplete",
                },
            ],
        },
        "wonju": {
            "status": "action",
            "surveys": [
                {
                    "seminarId": 5475,
                    "status": "no_answer",
                    "product": "우루사",
                }
            ],
        },
    }
    results_file.write_text(json.dumps(data), encoding="utf-8")

    items = claude_trigger.collect_actionable_items([results_file])
    assert len(items) >= 2
    statuses = {it["status"] for it in items}
    assert "unverified" in statuses
    assert "no_answer" in statuses
    assert "success" not in statuses


def test_evaluate_items_dedup_and_daily_cap():
    items = [
        {"script": "doctorville", "task": "quiz", "status": "no_answer", "target": "우루사", "node": {}},
        {"script": "seminar_survey", "task": "survey", "status": "unverified", "target": "5473", "node": {}},
    ]

    # Case 1: Clean history -> both should fire
    to_fire, deferred = claude_trigger.evaluate_items(items, [], "2026-09-15")
    assert len(to_fire) == 2
    assert len(deferred) == 0

    # Case 2: One already fired today -> defer it
    history = [
        {
            "ts": "2026-09-15T11:05:00",
            "fp": "2026-09-15/doctorville/quiz/no_answer/우루사",
            "outcome": "fired",
        }
    ]
    to_fire, deferred = claude_trigger.evaluate_items(items, history, "2026-09-15")
    assert len(to_fire) == 1
    assert to_fire[0]["target"] == "5473"
    assert len(deferred) == 1
    assert deferred[0]["reason"] == "already_fired_today"

    # Case 3: Daily cap (4 fires) reached
    full_history = [
        {"ts": f"2026-09-15T10:0{i}:00", "fp": f"fp{i}", "outcome": "fired"}
        for i in range(4)
    ]
    to_fire, deferred = claude_trigger.evaluate_items(items, full_history, "2026-09-15")
    assert len(to_fire) == 0
    assert len(deferred) == 2
    assert all(d["reason"] == "daily_cap_reached" for d in deferred)


def test_evaluate_items_retryable_error_delay():
    retryable_item = {
        "script": "doctorville",
        "task": "attend",
        "status": "failed",
        "target": "TimeoutError",
        "node": {"message": "net::ERR_CONNECTION_TIMED_OUT occurred"},
    }

    # First occurrence -> deferred
    to_fire, deferred = claude_trigger.evaluate_items([retryable_item], [], "2026-09-15")
    assert len(to_fire) == 0
    assert len(deferred) == 1
    assert deferred[0]["reason"] == "retryable_first_occurrence"

    # Second occurrence today -> promoted to fire!
    history = [
        {
            "ts": "2026-09-15T00:15:00",
            "fp": "2026-09-15/doctorville/attend/failed/TimeoutError",
            "outcome": "deferred",
            "reason": "retryable_first_occurrence",
        }
    ]
    to_fire, deferred = claude_trigger.evaluate_items([retryable_item], history, "2026-09-15")
    assert len(to_fire) == 1
    assert len(deferred) == 0


def test_build_payload_under_limit_and_truncation():
    # Large traceback to trigger truncation
    large_tb = "Traceback (most recent call last):\n" + ("  File 'test.py', line 1, in foo\n" * 2000)
    errors = [{"script": "hmp", "message": "Failed", "traceback": large_tb}]
    items = [
        {
            "fp": "2026-09-15/hmp/comment/failed/_",
            "account": "bjh7790",
            "path": "hmp > comment",
            "node": {"status": "failed", "message": "Failed", "extra_huge": "x" * 10000},
        }
    ]

    payload_text = claude_trigger.build_payload(
        "일일 의료 포털 자동화",
        "https://github.com/econ-blog/DocAuto/actions/runs/12345",
        "2026-09-15T00:15:00+09:00",
        items,
        errors,
        failed_steps=[],
        screenshots=["test.png"],
    )

    assert len(payload_text) <= 65536
    parsed = json.loads(payload_text)
    assert parsed["workflow"] == "일일 의료 포털 자동화"
    assert len(parsed["errors"]) == 1
    assert len(parsed["errors"][0]["traceback"]) <= 600


def test_fire_routine_success_and_failure():
    # Success case
    mock_resp = MagicMock()
    mock_resp.__enter__.return_value.read.return_value = json.dumps({"claude_code_session_url": "https://claude.ai/session/123"}).encode("utf-8")
    with patch("urllib.request.urlopen", return_value=mock_resp):
        ok, session_url = claude_trigger.fire_routine("https://api.anthropic.com/fire", "test_token", '{"text": "test"}')
        assert ok is True
        assert session_url == "https://claude.ai/session/123"

    # HTTP error case
    err = urllib.error.HTTPError(
        url="https://api.anthropic.com/fire",
        code=429,
        msg="Too Many Requests",
        hdrs={},
        fp=MagicMock(read=lambda: b'{"error": "rate limit"}'),
    )
    with patch("urllib.request.urlopen", side_effect=err):
        ok, msg = claude_trigger.fire_routine("https://api.anthropic.com/fire", "test_token", '{"text": "test"}')
        assert ok is False
        assert "HTTPError 429" in msg


def test_cli_dry_run_and_step_failure(tmp_path, capsys):
    log_dir = tmp_path / "logs"
    log_dir.mkdir(parents=True)

    test_args = [
        "claude_trigger.py",
        "--outcome", "pytest=failure",
        "--dry-run",
        "--log-dir", str(log_dir),
        "--workflow", "일일 의료 포털 자동화",
    ]

    with patch("sys.argv", test_args):
        claude_trigger.main()

    captured = capsys.readouterr()
    assert "[DRY RUN] 생성된 페이로드:" in captured.out
    assert "pytest" in captured.out

    # Dry-run should not write 'fired' entries
    today_file = log_dir / f"claude-triggers-{datetime.now(KST):%Y-%m}.jsonl"
    if today_file.exists():
        content = today_file.read_text("utf-8")
        assert '"outcome": "fired"' not in content


# --- 지문 안정성 회귀 (2026-09-16) -------------------------------------------
# 초판은 severity_of(하위 트리 최대값)로 항목을 골라 정상 부모까지 잡았고, task에
# 리스트 인덱스와 계정 키가 섞여 들어갔다. 같은 문제가 다음 런에서 다른 지문이 되어
# 30분마다 도는 세미나 블록에서 세션이 중복으로 떴다.

def _write(tmp_path, name, data):
    p = tmp_path / name
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return p


def test_clean_parent_of_actionable_child_is_not_an_item(tmp_path):
    """자기 status가 success(증거 있음)인 계정 노드는 항목이 되지 않는다."""
    path = _write(tmp_path, "results-seminar_survey.json", {
        "bjh7790": {
            "account": "bjh7790", "status": "success", "verified_by": "detail_button: 응답완료",
            "surveys": [
                {"seminarId": "5632", "status": "incomplete_bank", "message": "족보 미기입"},
                {"seminarId": "5640", "status": "success", "verified_by": "detail_button"},
            ],
        }
    })
    items = claude_trigger.collect_actionable_items([path])
    assert len(items) == 1
    assert items[0]["status"] == "incomplete_bank"
    assert items[0]["task"] == "surveys"
    assert items[0]["path"] == "seminar_survey > bjh7790 > surveys[0]"


def test_fingerprint_ignores_list_index_and_account(tmp_path):
    """같은 세미나면 리스트 자리·계정이 달라도 지문이 같아야 세션이 한 번만 뜬다."""
    first = _write(tmp_path, "results-seminar_survey.json", {
        "bjh7790": {"account": "bjh7790", "status": "success", "verified_by": "x", "surveys": [
            {"seminarId": "5632", "status": "incomplete_bank"},
            {"seminarId": "5640", "status": "success", "verified_by": "y"},
        ]},
        "wonju": {"account": "wonju", "status": "success", "verified_by": "x", "surveys": [
            {"seminarId": "5632", "status": "incomplete_bank"},
        ]},
    })
    second = _write(tmp_path, "results-seminar_survey2.json", {
        "wonju": {"account": "wonju", "status": "success", "verified_by": "x", "surveys": [
            {"seminarId": "5640", "status": "success", "verified_by": "y"},
            {"seminarId": "5632", "status": "incomplete_bank"},
        ]},
    })
    fps = {claude_trigger.compute_fingerprint("2026-09-16", "seminar_survey", it["task"],
                                              it["status"], it["target"])
           for it in claude_trigger.collect_actionable_items([first])}
    assert fps == {"2026-09-16/seminar_survey/surveys/incomplete_bank/5632"}

    later = claude_trigger.collect_actionable_items([second])
    assert claude_trigger.compute_fingerprint("2026-09-16", "seminar_survey", later[0]["task"],
                                              later[0]["status"], later[0]["target"]) in fps


def test_module_key_stays_in_task(tmp_path):
    """keymedi처럼 account 필드가 키와 다른 노드는 모듈 이름이므로 task에 남는다."""
    path = _write(tmp_path, "results-daily_runner.json", {
        "doctorville_bjh7790": {"account": "bjh7790",
                                "quiz": {"status": "no_answer", "product": "징코샷"}},
        "keymedi": {"site": "keymedi", "account": "bjh7790", "status": "failed",
                    "message": "출석 버튼 없음"},
    })
    tasks = {it["task"] for it in claude_trigger.collect_actionable_items([path])}
    assert tasks == {"quiz", "keymedi"}


def test_daily_cap_counts_fires_not_items():
    """상한은 세션 수다. 항목 4개가 한 번에 실려 나갔다고 그날이 끝나면 안 된다."""
    items = [{"script": "seminar_survey", "task": "surveys", "status": "unverified",
              "target": "5999", "node": {"status": "unverified"}}]
    one_fire = [{"ts": "2026-09-16T11:00:00+09:00", "fp": f"2026-09-16/x/y/z/{i}",
                 "outcome": "fired", "fire_id": "run1:110000"} for i in range(4)]
    to_fire, deferred = claude_trigger.evaluate_items(items, one_fire, "2026-09-16")
    assert len(to_fire) == 1 and not deferred

    four_fires = [{"ts": "2026-09-16T11:00:00+09:00", "fp": f"2026-09-16/x/y/z/{i}",
                   "outcome": "fired", "fire_id": f"run{i}:110000"} for i in range(4)]
    to_fire, deferred = claude_trigger.evaluate_items(items, four_fires, "2026-09-16")
    assert not to_fire
    assert deferred[0]["reason"] == "daily_cap_reached"


def test_truncated_payload_is_still_valid_json():
    """세션이 payload를 파싱할 수 있어야 한다 — 잘리는 것은 내용이지 JSON이 아니다."""
    items = [{"fp": f"fp{i}", "account": "bjh7790", "path": "p",
              "node": {"status": "failed", "message": "x" * 5000, "dom_dump": "y" * 5000}}
             for i in range(500)]
    errors = [{"script": "seminar_survey", "traceback": "t" * 5000} for _ in range(20)]
    text = claude_trigger.build_payload("wf", "url", "kst", items, errors, ["survey"], [])
    assert len(text) <= claude_trigger.MAX_PAYLOAD_CHARS
    parsed = json.loads(text)
    assert parsed["truncated"] is True
    assert len(parsed["items"]) == 10
