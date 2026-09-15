from notify import (
    resolve_credentials,
    send_telegram,
    severity_of,
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



