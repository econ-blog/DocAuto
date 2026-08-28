from unittest.mock import MagicMock
import seminar_survey
from notify import severity_of


def test_rollup_verified_by_partial_missing_demotes_to_alert():
    """성공한 설문 중 1건이라도 verified_by가 없으면 계정 증거가 생성되지 않아 alert로 강등되어야 함."""
    surveys = [
        {"seminarId": 101, "status": "success", "verified_by": "completion_screen_verified"},
        {"seminarId": 102, "status": "success"},  # verified_by 누락
    ]
    verified = seminar_survey.rollup_verified_by(surveys)
    assert verified == ""

    account_node = {
        "status": seminar_survey.rollup_account_status([s["status"] for s in surveys]),
        "surveys": surveys,
    }
    if verified:
        account_node["verified_by"] = verified

    assert account_node["status"] == "success"
    # notify 게이트에서 unverified (alert)로 강등 판정
    assert severity_of(account_node) == "alert"


def test_dismiss_alerts_handles_headlessui_modals():
    """HeadlessUI 알림 모달이 떠 있을 때 버튼 클릭으로 해제하는지 검증."""
    mock_page = MagicMock()
    mock_dialog = MagicMock()
    mock_dialog.count.return_value = 1
    mock_dialog.first.inner_text.return_value = "작성 중인 정보를 불러왔습니다"

    mock_btn = MagicMock()
    mock_btn.count.return_value = 1
    mock_dialog.first.locator.return_value = mock_btn

    def mock_locator(sel):
        if '[role="dialog"][data-headlessui-state="open"]' in sel:
            return mock_dialog
        return MagicMock(count=lambda: 0)

    mock_page.locator.side_effect = mock_locator

    dismissed = seminar_survey.dismiss_alerts(mock_page, max_rounds=1)
    assert len(dismissed) == 1
    assert "작성 중인 정보를 불러왔습니다" in dismissed[0]
    mock_btn.first.click.assert_called_once()


def test_run_survey_collects_new_text_question_into_survey_text_answers(tmp_path, monkeypatch):
    """새로운 서술형(주관식) 문항을 만나면 survey_text_answers.json에 빈 값('')으로 정상 수집하고 incomplete_bank를 반환하는지 검증."""
    import json
    quiz_file = tmp_path / "survey_quiz_answers.json"
    text_file = tmp_path / "survey_text_answers.json"
    legacy_file = tmp_path / "survey_answers_legacy.json"

    quiz_file.write_text("{}", encoding="utf-8")
    text_file.write_text("{}", encoding="utf-8")
    legacy_file.write_text("{}", encoding="utf-8")

    bank_paths = {
        "quiz": quiz_file,
        "text": text_file,
        "legacy": legacy_file,
    }

    # 설문 팝업 창 mock
    mock_survey_page = MagicMock()
    mock_survey_page.is_closed.return_value = False
    mock_survey_page.evaluate.return_value = [
        {
            "number": "1",
            "question": "본 세미나에 대한 건의사항이나 후기를 자유롭게 작성해주세요.",
            "kind": "input",
            "name": "free.0",
            "options": [],
        }
    ]

    mock_main_page = MagicMock()
    monkeypatch.setattr(seminar_survey, "open_survey", lambda page, sid: (mock_survey_page, "호흡기 최신 지견"))
    monkeypatch.setattr(seminar_survey, "dismiss_alerts", lambda page: [])

    result = seminar_survey.run_survey(
        mock_main_page,
        5500,
        bank_paths=bank_paths,
    )

    # 1. status가 incomplete_bank로 판정되어야 함
    assert result["status"] == "incomplete_bank"
    assert "1페이지에 미등록 문항 1건" in result["message"]
    assert "주관식 1건" in result["message"]
    assert len(result["missing"]) == 1
    assert result["missing"][0]["bank"] == "text"
    assert result["missing"][0]["question"] == "본 세미나에 대한 건의사항이나 후기를 자유롭게 작성해주세요."

    # 2. survey_text_answers.json에 해당 문항이 빈 문자열("")로 저장되었는지 검증
    saved_text_bank = json.loads(text_file.read_text(encoding="utf-8"))
    assert "본 세미나에 대한 건의사항이나 후기를 자유롭게 작성해주세요." in saved_text_bank
    assert saved_text_bank["본 세미나에 대한 건의사항이나 후기를 자유롭게 작성해주세요."] == ""

    # 3. survey_quiz_answers.json이나 legacy_file에는 쓰이지 않아야 함
    assert json.loads(quiz_file.read_text(encoding="utf-8")) == {}
    assert json.loads(legacy_file.read_text(encoding="utf-8")) == {}


# ---------------------------------------------------------------------------
# 설문 완료 판정 — 제출 직후 화면이 아니라 세미나 상세 재접속으로 확인한다
# (2026-08-28). 상세에는 참여했으면 '설문 참여 완료', 아니면 '세미나 종료'만 뜬다.
# ---------------------------------------------------------------------------

def test_detect_survey_marker_reads_the_detail_buttons():
    done = seminar_survey.detect_survey_marker(["설문 참여 완료", "세미나 종료"])
    not_done = seminar_survey.detect_survey_marker(["세미나 종료"])
    unknown = seminar_survey.detect_survey_marker(["입장하기", "신청취소"])

    # 완료 화면에는 두 문구가 나란히 뜬다 — 완료 표시가 이긴다.
    assert done == "done"
    assert not_done == "not_done"
    assert unknown == "unknown"
    assert seminar_survey.detect_survey_marker([]) == "unknown"


def test_detect_survey_marker_ignores_whitespace_splits():
    """버튼 문구가 줄바꿈으로 쪼개져 와도 같은 판정이어야 한다."""
    assert seminar_survey.detect_survey_marker(["설문\n참여  완료"]) == "done"


def _stub_detail(monkeypatch, verdict, buttons=None):
    calls = []

    def fake(page, seminar_id, retries=0):
        calls.append((seminar_id, retries))
        return verdict, buttons if buttons is not None else []

    monkeypatch.setattr(seminar_survey, "confirm_survey_done", fake)
    return calls


def _submitted_survey_page():
    """1페이지 제출 후 문항이 사라진 설문 창 mock."""
    page = MagicMock()
    page.is_closed.return_value = False
    page.evaluate.side_effect = [
        [{"number": "1", "question": "만족하셨습니까?", "kind": "radio", "name": "q1",
          "options": [{"text": "예", "id": "o1", "name": "q1", "value": "1", "qnum": "1", "index": 0},
                      {"text": "아니오", "id": "o2", "name": "q1", "value": "2", "qnum": "1", "index": 1}]}],
        [],   # 제출 후 — 문항 없음
    ]
    return page


def test_run_survey_verified_by_detail_button_after_submit(monkeypatch):
    """제출 후 상세에 '설문 참여 완료'가 보이면 success + 양성 증거."""
    survey_page = _submitted_survey_page()
    monkeypatch.setattr(seminar_survey, "open_survey", lambda page, sid: (survey_page, ""))
    monkeypatch.setattr(seminar_survey, "dismiss_alerts", lambda page: [])
    monkeypatch.setattr(seminar_survey, "apply_plan", lambda page, plan: None)
    monkeypatch.setattr(seminar_survey, "find_advance_button", lambda page: (MagicMock(), "submit"))
    calls = _stub_detail(monkeypatch, "done", ["설문 참여 완료", "세미나 종료"])

    result = seminar_survey.run_survey(MagicMock(), 5600, bank_paths={})

    assert result["status"] == "success"
    assert result["verified_by"] == f"detail_button: {seminar_survey.SURVEY_DONE_MARKER}"
    assert result["pages"] == 1
    # 제출 직후에는 표시가 늦을 수 있으니 한 번은 다시 열어 본다.
    assert calls == [(5600, 1)]


def test_run_survey_unverified_when_detail_shows_only_seminar_end(monkeypatch):
    """'세미나 종료'만 남았으면 설문에 참여하지 못한 것 — 성공으로 올리지 않는다."""
    survey_page = _submitted_survey_page()
    monkeypatch.setattr(seminar_survey, "open_survey", lambda page, sid: (survey_page, ""))
    monkeypatch.setattr(seminar_survey, "dismiss_alerts", lambda page: [])
    monkeypatch.setattr(seminar_survey, "apply_plan", lambda page, plan: None)
    monkeypatch.setattr(seminar_survey, "find_advance_button", lambda page: (MagicMock(), "submit"))
    monkeypatch.setattr(seminar_survey.common, "save_screenshot", lambda page, name: "shot.png")
    _stub_detail(monkeypatch, "not_done", ["세미나 종료"])

    result = seminar_survey.run_survey(MagicMock(), 5600, bank_paths={})

    assert result["status"] == "unverified"
    assert "verified_by" not in result
    # 사이트가 문구를 바꿨을 때를 대비해 무엇이 보였는지 결과에 남긴다.
    assert result["detail_buttons"] == ["세미나 종료"]
    assert seminar_survey.SEMINAR_END_MARKER in result["message"]


def test_run_survey_confirms_success_even_when_the_window_closes(monkeypatch):
    """제출 후 창이 닫혀도 상세에 완료 표시가 있으면 성공이다(예전엔 무조건 unverified)."""
    survey_page = MagicMock()
    survey_page.is_closed.side_effect = [False, True, True, True]
    survey_page.evaluate.return_value = [
        {"number": "1", "question": "만족하셨습니까?", "kind": "radio", "name": "q1",
         "options": [{"text": "예", "id": "o1", "name": "q1", "value": "1", "qnum": "1", "index": 0},
                     {"text": "아니오", "id": "o2", "name": "q1", "value": "2", "qnum": "1", "index": 1}]}
    ]
    monkeypatch.setattr(seminar_survey, "open_survey", lambda page, sid: (survey_page, ""))
    monkeypatch.setattr(seminar_survey, "dismiss_alerts", lambda page: [])
    monkeypatch.setattr(seminar_survey, "apply_plan", lambda page, plan: None)
    monkeypatch.setattr(seminar_survey, "find_advance_button", lambda page: (MagicMock(), "submit"))
    _stub_detail(monkeypatch, "done", ["설문 참여 완료", "세미나 종료"])

    result = seminar_survey.run_survey(MagicMock(), 5600, bank_paths={})

    assert result["status"] == "success"
    assert result["verified_by"] == f"detail_button: {seminar_survey.SURVEY_DONE_MARKER}"


def test_run_survey_marks_already_done_when_popup_never_opens_but_detail_says_done(monkeypatch):
    """설문 창이 안 열리는 이유가 '이미 참여'인지 상세로 가른다 — 이력이 날아간 경우."""
    monkeypatch.setattr(seminar_survey, "open_survey", lambda page, sid: (None, "팝업 안 열림"))
    _stub_detail(monkeypatch, "done", ["설문 참여 완료", "세미나 종료"])
    state = {"version": 2, "accounts": {"bjh7790": {"entered": [{"id": 5600}]}}}

    result = seminar_survey.run_survey(
        MagicMock(), 5600, bank_paths={}, state=state, account="bjh7790",
    )

    assert result["status"] == "already_done"
    assert result["verified_by"] == f"detail_button: {seminar_survey.SURVEY_DONE_MARKER}"
    # 다음 런이 같은 세미나를 또 붙들지 않도록 이력에 못을 박는다.
    assert state["accounts"]["bjh7790"]["survey"]["5600"] == "done"


def test_run_survey_still_not_ready_when_detail_has_no_done_marker(monkeypatch):
    """상세에 완료 표시가 없으면 종전대로 not_ready — 30분 뒤 다시 시도한다."""
    monkeypatch.setattr(seminar_survey, "open_survey", lambda page, sid: (None, "팝업 안 열림"))
    _stub_detail(monkeypatch, "unknown")

    result = seminar_survey.run_survey(MagicMock(), 5600, bank_paths={})

    assert result["status"] == "not_ready"
