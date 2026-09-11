from datetime import datetime
import json
from unittest.mock import MagicMock
from common import KST
import seminar_live
import seminar_survey
from seminar_survey import (
    evaluate_survey_cutoff,
    get_survey_cutoff,
    run_survey,
    run_survey_for_item,
    mark_survey_status,
    rollup_account_status,
    rollup_verified_by,
)
from notify import severity_of


def test_evaluate_survey_cutoff_before_deadline():
    item = {
        "id": 5473,
        "title": "Breathe Well Symposium (호흡기)",
        "start": "2026-08-10(월) 13:00 ~ 14:00",
        "entered_at": "2026-08-10T13:05:00+09:00",
    }
    # Window is 13:00+30m (13:30) ~ 14:00+1h (15:00) KST
    # 13:15 KST is before open (13:30) -> not_ready
    before_open = datetime(2026, 8, 10, 13, 15, tzinfo=KST)
    assert evaluate_survey_cutoff(item, before_open) == "not_ready"

    # 13:30 정각부터 열린다 (경계 포함)
    assert evaluate_survey_cutoff(item, datetime(2026, 8, 10, 13, 30, tzinfo=KST)) == "ready"

    # 14:30 KST is within window -> ready
    in_window = datetime(2026, 8, 10, 14, 30, tzinfo=KST)
    assert evaluate_survey_cutoff(item, in_window) == "ready"


def test_evaluate_survey_cutoff_after_deadline():
    item = {
        "id": 5473,
        "title": "Breathe Well Symposium (호흡기)",
        "start": "2026-08-10(월) 13:00 ~ 14:00",
        "entered_at": "2026-08-10T13:05:00+09:00",
    }
    # 마감은 **공지된 종료**가 아니라 관측된 실제 종료 + 1시간이다
    # (2026-09-11 사용자 지시). 관측이 없으면 15:30은 아직 확인하러 갈 시간대다.
    now_kst = datetime(2026, 8, 10, 15, 30, tzinfo=KST)
    assert evaluate_survey_cutoff(item, now_kst) == "ready"

    ended = {**item, "ended_at": "2026-08-10T14:20:00+09:00"}
    assert evaluate_survey_cutoff(ended, now_kst) == "closed"
    assert evaluate_survey_cutoff(
        ended, datetime(2026, 8, 10, 15, 10, tzinfo=KST)
    ) == "ready"


def test_get_survey_cutoff_needs_an_observed_end():
    """공지만으로는 마감을 못 정한다 — 공지가 양쪽으로 틀리는 것이 확인됐다."""
    item = {
        "start": "2026-08-10(월) 13:00 ~ 14:00",
    }
    assert get_survey_cutoff(item) is None

    ended = {**item, "ended_at": "2026-08-10T14:20:00+09:00"}
    assert get_survey_cutoff(ended) == datetime(2026, 8, 10, 15, 20, tzinfo=KST)


def test_fallback_cutoff_start_only():
    """종료 시각이 없는 항목도 사전 게이트(시작 30분 후)는 그대로 쓴다."""
    item = {
        "start": "2026-08-10(월) 13:00",
    }
    before_now = datetime(2026, 8, 10, 13, 15, tzinfo=KST)
    in_now = datetime(2026, 8, 10, 13, 45, tzinfo=KST)
    assert evaluate_survey_cutoff(item, before_now) == "not_ready"
    assert evaluate_survey_cutoff(item, in_now) == "ready"

    ended = {**item, "ended_at": "2026-08-10T14:00:00+09:00"}
    assert evaluate_survey_cutoff(ended, datetime(2026, 8, 10, 15, 30, tzinfo=KST)) == "closed"


def test_fallback_cutoff_entered_at_only():
    item = {
        "entered_at": "2026-08-10T13:00:00+09:00",
    }
    before_now = datetime(2026, 8, 10, 13, 15, tzinfo=KST)
    in_now = datetime(2026, 8, 10, 13, 45, tzinfo=KST)
    assert evaluate_survey_cutoff(item, before_now) == "not_ready"
    assert evaluate_survey_cutoff(item, in_now) == "ready"

    ended = {**item, "ended_at": "2026-08-10T14:00:00+09:00"}
    assert evaluate_survey_cutoff(ended, datetime(2026, 8, 10, 15, 30, tzinfo=KST)) == "closed"


def test_naive_datetime_conversion():
    item = {
        "start": "2026-08-10(월) 13:00 ~ 14:00",
    }
    item = {**item, "ended_at": "2026-08-10T14:00:00+09:00"}
    # Window: 14:00 ~ 15:00 KST (실제 종료 기준)
    naive_before = datetime(2026, 8, 10, 13, 15)
    naive_in = datetime(2026, 8, 10, 14, 30)
    naive_after = datetime(2026, 8, 10, 15, 30)

    assert evaluate_survey_cutoff(item, naive_before) == "not_ready"
    assert evaluate_survey_cutoff(item, naive_in) == "ready"
    assert evaluate_survey_cutoff(item, naive_after) == "closed"


def test_run_survey_cutoff_closed_and_state_update(tmp_path):
    state_file = tmp_path / "seminar_entered.json"
    state = {
        "version": 2,
        "date": "2026-08-10",
        "accounts": {
            "bjh7790": {
                "entered": [
                    {
                        "id": 5473,
                        "title": "호흡기 심포지엄",
                        "start": "2026-08-10(월) 13:00 ~ 14:00",
                        "entered_at": "2026-08-10T13:05:00+09:00",
                        # 실제 종료 관측 — 마감(15:00)의 근거는 이것이지 공지가 아니다.
                        "ended_at": "2026-08-10T14:00:00+09:00",
                    }
                ],
                "survey": {},
            }
        },
    }
    state_file.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

    item = state["accounts"]["bjh7790"]["entered"][0]
    now_past = datetime(2026, 8, 10, 16, 0, tzinfo=KST)

    res = run_survey_for_item(None, item, now_dt=now_past, state=state, state_file=state_file, account="bjh7790")
    assert res["status"] == "closed"

    loaded = seminar_live.load_state(state_file, "2026-08-10")
    assert loaded["accounts"]["bjh7790"]["survey"]["5473"] == "closed"


def test_incomplete_bank_message_includes_title(tmp_path):
    from seminar_survey import resolve_page, add_missing_to_banks, format_bank_counts

    quiz_file = tmp_path / "survey_quiz_answers.json"
    banks = {"quiz": {}, "text": {}, "legacy": {}, "paths": {"quiz": quiz_file}}

    questions = [
        {
            "question": "[퀴즈] 새로운 문항",
            "kind": "choice",
            "name": "q1",
            "options": [{"text": "보기1"}, {"text": "보기2"}],
        }
    ]
    plan, missing = resolve_page(questions, banks)
    assert len(missing) == 1

    item = {
        "id": 5473,
        "title": "호흡기 심포지엄",
        "start": "2026-08-10(월) 13:00 ~ 14:00",
    }

    counts = add_missing_to_banks(banks, missing)
    pages_done = 0
    prefix = f"[{item['title']}] " if item.get("title") else ""
    msg = (
        f"{prefix}{pages_done + 1}페이지에 미등록 문항 {len(missing)}건 — 제출하지 않음"
        f"({format_bank_counts(counts)} 빈 값 추가)."
    )
    assert "호흡기 심포지엄" in msg
    assert "퀴즈 1건" in msg


def test_mark_survey_status_closed():
    state = {
        "version": 2,
        "accounts": {
            "bjh7790": {
                "entered": [{"id": 5473, "title": "Test"}],
                "survey": {},
            }
        },
    }
    mark_survey_status(state, "bjh7790", 5473, "closed")
    assert state["accounts"]["bjh7790"]["survey"]["5473"] == "closed"


def test_seminar_survey_imports_os():
    assert hasattr(seminar_survey, "os") is True


def test_rollup_account_status():
    assert rollup_account_status([]) == "no_target"
    assert rollup_account_status(["not_ready", "not_ready"]) == "not_ready"
    assert rollup_account_status(["closed", "closed"]) == "closed"
    assert rollup_account_status(["already_done", "already_done"]) == "already_done"


def test_rollup_verified_by():
    assert rollup_verified_by([]) == ""
    assert rollup_verified_by([{"status": "not_ready"}]) == ""
    # 성공 설문에 증거가 없으면 계정 증거도 만들지 않는다
    assert rollup_verified_by([{"status": "success"}]) == ""
    assert rollup_verified_by(
        [{"status": "success", "verified_by": "completion_screen_verified"}, {"status": "closed"}]
    ) == "surveys_verified: 1건"


def test_account_node_with_rollup_evidence_is_not_demoted():
    surveys = [{"status": "success", "verified_by": "completion_screen_verified"}]
    account = {"status": rollup_account_status([s["status"] for s in surveys]), "surveys": surveys}
    assert severity_of(account) == "alert"  # 증거 없으면 강등

    account["verified_by"] = rollup_verified_by(surveys)
    assert severity_of(account) == "ok"


def test_run_survey_incomplete_bank_questions_payload(tmp_path, monkeypatch):
    bank_paths = {
        "quiz": tmp_path / "survey_quiz_answers.json",
        "text": tmp_path / "survey_text_answers.json",
        "legacy": tmp_path / "survey_answers_legacy.json",
    }

    mock_survey_page = MagicMock()
    mock_survey_page.is_closed.return_value = False

    monkeypatch.setattr("seminar_survey.open_survey", lambda page, sid: (mock_survey_page, ""))
    monkeypatch.setattr("seminar_survey.read_questions", lambda p: [
        {
            "question": "[퀴즈] 미등록 질문",
            "kind": "choice",
            "name": "q1",
            "options": [{"text": "보기1"}, {"text": "보기2"}],
        }
    ])

    res = run_survey(MagicMock(), 5473, bank_paths=bank_paths)
    assert res["status"] == "incomplete_bank"
    assert "questions" in res
    assert res["questions"] == [
        {
            "question": "미등록 질문",
            "options": ["1. 보기1", "2. 보기2"],
            "option_texts": ["보기1", "보기2"],
            "bank": "quiz",
        }
    ]


