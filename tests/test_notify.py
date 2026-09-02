from notify import (
    build_message,
    resolve_credentials,
    resolve_level,
    send_telegram,
    severity_of,
    should_send,
)


def test_severity_mapping():
    assert severity_of({"status": "success", "verified_by": "modal"}) == "ok"
    assert severity_of({"status": "success"}) == "alert"  # missing verified_by -> unverified -> alert
    assert severity_of({"status": "already_done", "verified_by": "#capsuleBtnComplete"}) == "quiet"
    # 증거 없는 already_done도 강등된다 — 완료 표식 셀렉터가 바뀌면 조용히 오판하기 때문
    assert severity_of({"status": "already_done"}) == "alert"
    assert severity_of({"status": "no_answer"}) == "action"
    assert severity_of({"status": "failed"}) == "alert"


def test_severity_of_no_target():
    assert severity_of({"status": "no_target"}) == "quiet"


def test_nested_hmp_and_list_severity():
    # HMP dictionary containing top-level status: already_done, comment: {status: failed},
    # and roulette: [{status: already_done}, {status: failed, message: "네트워크 오류"}]
    hmp_res = {
        "status": "already_done",
        "verified_by": "evidence",
        "comment": {"status": "failed", "message": "저장 실패"},
        "roulette": [
            {"status": "already_done", "verified_by": "evidence"},
            {"status": "failed", "message": "네트워크 오류"},
        ],
    }
    assert severity_of(hmp_res) == "alert"


def test_should_send_actionable_mode():
    quiet_or_ok = {
        "keymedi": {"status": "already_done", "verified_by": "evidence"},
        "doctorville": {"status": "success", "verified_by": "modal"},
        "hmp": {
            "status": "already_done",
            "verified_by": "evidence",
            "roulette": [{"status": "already_done", "verified_by": "evidence"}],
        },
    }
    assert should_send(quiet_or_ok, "actionable") is False

    actionable_action = {
        "doctorville": {"status": "no_answer", "product": "우루사"},
    }
    assert should_send(actionable_action, "actionable") is True

    actionable_alert = {
        "keymedi": {"status": "failed", "message": "로그인 오류"},
    }
    assert should_send(actionable_alert, "actionable") is True

    assert should_send(quiet_or_ok, "all") is True


def test_build_message_all_mode():
    results = {
        "keymedi": {"status": "already_done", "verified_by": "evidence", "points": 10},
        "doctorville_bjh7790": {
            "attend": {"status": "success", "verified_by": "ok", "points": 50},
            "quiz": {"status": "no_answer", "product": "우루사"},
        },
    }
    msg = build_message(results, "all", "2026-08-31")
    assert "📋 *일일 자동화 결과*" in msg
    assert "키메디" in msg or "keymedi" in msg
    assert "우루사" in msg


def test_build_message_actionable_mode():
    # Actionable mode with actionable items
    results = {
        "keymedi": {"status": "already_done", "verified_by": "evidence", "points": 10},
        "doctorville_bjh7790": {
            "attend": {"status": "success", "verified_by": "ok", "points": 50},
            "quiz": {"status": "no_answer", "product": "우루사", "message": "정답 없음"},
        },
    }
    msg = build_message(results, "actionable", "2026-08-31")
    assert "❗ DocAuto (2026-08-31)" in msg
    assert "no_answer" in msg
    assert "우루사" in msg
    assert "quiz_answers.json" in msg

    # Actionable mode with NO actionable items -> returns ""
    quiet_results = {
        "keymedi": {"status": "already_done", "verified_by": "evidence"},
        "doctorville": {"status": "success", "verified_by": "modal"},
    }
    msg_empty = build_message(quiet_results, "actionable", "2026-08-31")
    assert msg_empty == ""


def test_notify_empty_env_level_fallback():
    quiet_results = {"keymedi": {"status": "already_done", "verified_by": "evidence"}}
    assert should_send(quiet_results, "") is True
    assert should_send(quiet_results, "  ") is True
    assert should_send(quiet_results, "invalid_level") is True


def test_build_message_actionable_preserves_questions_payload():
    results = {
        "doctorville_bjh7790": {
            "quiz": {
                "status": "no_answer",
                "product": "우루사",
                "message": "정답 미등록\n두번째 줄 메세지",
                "questions": [{"q": "Q1: 질문 내용 첫번째 줄\n질문 내용 두번째 줄", "options": ["A", "B"]}],
            }
        }
    }
    msg = build_message(results, "actionable", "2026-08-31")
    assert "우루사" in msg
    assert "Q1: 질문 내용 첫번째 줄" in msg
    assert "질문 내용 두번째 줄" in msg
    assert "options" in msg
    assert "[\n  {\n" in msg  # verifies indent=2 formatted JSON output


def test_build_message_all_renders_question_and_option_texts():
    """all 레벨에서 문항·보기 텍스트가 실제로 찍혀야 한다(키만 찍히던 버그)."""
    results = {
        "doctorville": {
            "quiz": {
                "status": "no_answer",
                "product": "우루사",
                "questions": [{"question": "다음 중 옳은 것은?", "options": ["가나다", "라마바"]}],
            }
        }
    }
    msg = build_message(results, "all", "2026-08-31")
    assert "question: 다음 중 옳은 것은?" in msg
    assert "1. 가나다" in msg
    assert "2. 라마바" in msg


def test_send_telegram_empty_text_returns_true():
    assert send_telegram("") is True
    assert send_telegram(None) is True


def test_resolve_credentials_falls_back_to_file(tmp_path, monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    creds_file = tmp_path / "credentials.json"
    creds_file.write_text('{"telegram": {"bot_token": "file_token", "chat_id": "file_chat"}}', encoding="utf-8")

    assert resolve_credentials(credentials_path=str(creds_file)) == ("file_token", "file_chat")


def test_resolve_credentials_env_beats_file(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "env_token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "env_chat")
    creds_file = tmp_path / "credentials.json"
    creds_file.write_text('{"telegram": {"bot_token": "file_token", "chat_id": "file_chat"}}', encoding="utf-8")

    assert resolve_credentials(credentials_path=str(creds_file)) == ("env_token", "env_chat")


def test_resolve_credentials_missing_file(tmp_path, monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)

    assert resolve_credentials(credentials_path=str(tmp_path / "none.json")) == ("", "")


def test_resolve_level():
    assert resolve_level("") == "all"
    assert resolve_level(None) == "all"
    assert resolve_level("  ACTIONABLE ") == "actionable"
    assert resolve_level("nonsense") == "all"


