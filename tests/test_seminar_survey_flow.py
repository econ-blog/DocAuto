from datetime import datetime
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

    # 2026-08-31 이후 dismiss_alerts는 "보이는 버튼"만 클릭 후보로 삼는다.
    # (숨은 button.dialog__close.hidden을 집어 30초 대기하다 죽던 회귀)
    mock_btn = MagicMock()
    mock_btn.is_visible.return_value = True
    mock_btn_list = MagicMock()
    mock_btn_list.all.return_value = [mock_btn]
    mock_dialog.first.locator.return_value = mock_btn_list

    def mock_locator(sel):
        if '[role="dialog"][data-headlessui-state="open"]' in sel:
            return mock_dialog
        return MagicMock(count=lambda: 0)

    mock_page.locator.side_effect = mock_locator

    dismissed = seminar_survey.dismiss_alerts(mock_page, max_rounds=1)
    assert len(dismissed) == 1
    assert "작성 중인 정보를 불러왔습니다" in dismissed[0]
    mock_btn.click.assert_called_once()


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
    """1페이지 제출 후 문항이 사라지는 설문 창 mock.

    호출 횟수가 아니라 제출 여부로 답한다 — 진행 대기가 몇 번 읽든 결과가 같다.
    """
    q = {"number": "1", "question": "만족하셨습니까?", "kind": "radio", "name": "q1",
         "options": [{"text": "예", "id": "o1", "name": "q1", "value": "1", "qnum": "1", "index": 0},
                     {"text": "아니오", "id": "o2", "name": "q1", "value": "2", "qnum": "1", "index": 1}]}
    state = {"submitted": False}
    page = MagicMock()
    page.is_closed.return_value = False
    page.evaluate.side_effect = lambda js: [] if state["submitted"] else [dict(q)]
    page.submit_now = lambda: state.__setitem__("submitted", True)
    return page


def _submit_button(survey_page):
    """누르면 설문 창이 '제출됨' 상태로 넘어가는 버튼."""
    button = MagicMock()
    button.click.side_effect = survey_page.submit_now
    return button


def test_run_survey_verified_by_detail_button_after_submit(monkeypatch):
    """제출 후 상세에 '설문 참여 완료'가 보이면 success + 양성 증거."""
    survey_page = _submitted_survey_page()
    monkeypatch.setattr(seminar_survey, "open_survey", lambda page, sid: (survey_page, ""))
    monkeypatch.setattr(seminar_survey, "dismiss_alerts", lambda page: [])
    monkeypatch.setattr(seminar_survey, "apply_plan", lambda page, plan: None)
    monkeypatch.setattr(seminar_survey, "find_advance_button",
                        lambda page: (_submit_button(survey_page), "submit"))
    monkeypatch.setattr(seminar_survey, "tick_consents", lambda page: [])
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
    monkeypatch.setattr(seminar_survey, "find_advance_button",
                        lambda page: (_submit_button(survey_page), "submit"))
    monkeypatch.setattr(seminar_survey, "tick_consents", lambda page: [])
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
    closed = {"v": False}
    survey_page = MagicMock()
    # 호출 횟수가 아니라 실제 상태로 답한다 — 제출 버튼을 누르면 창이 닫힌다.
    survey_page.is_closed.side_effect = lambda: closed["v"]
    survey_page.evaluate.return_value = [
        {"number": "1", "question": "만족하셨습니까?", "kind": "radio", "name": "q1",
         "options": [{"text": "예", "id": "o1", "name": "q1", "value": "1", "qnum": "1", "index": 0},
                     {"text": "아니오", "id": "o2", "name": "q1", "value": "2", "qnum": "1", "index": 1}]}
    ]
    submit = MagicMock()
    submit.click.side_effect = lambda: closed.__setitem__("v", True)
    monkeypatch.setattr(seminar_survey, "open_survey", lambda page, sid: (survey_page, ""))
    monkeypatch.setattr(seminar_survey, "dismiss_alerts", lambda page: [])
    monkeypatch.setattr(seminar_survey, "apply_plan", lambda page, plan: None)
    monkeypatch.setattr(seminar_survey, "find_advance_button", lambda page: (submit, "submit"))
    monkeypatch.setattr(seminar_survey, "tick_consents", lambda page: [])
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


def test_run_survey_unverified_when_window_is_open_but_popup_never_opens(monkeypatch):
    """창이 열려 있는데 못 열었으면 unverified(alert)다.

    2026-09-09 세미나 5627: 같은 런에서 한 계정만 not_ready로 떨어졌는데 quiet이라
    알림이 안 갔다. 창이 열린 동안의 실패는 조용히 넘기지 않는다.
    """
    monkeypatch.setattr(seminar_survey, "open_survey", lambda page, sid: (None, "팝업 안 열림"))
    _stub_detail(monkeypatch, "unknown")

    result = seminar_survey.run_survey(MagicMock(), 5600, bank_paths={})

    assert result["status"] == "unverified"
    # 다음 런이 깜깜하지 않도록 갈림길의 증거는 그대로 남긴다.
    assert result["detail_verdict"] == "unknown"


def test_run_survey_not_ready_when_window_has_not_opened(monkeypatch):
    """창 자체가 안 열렸으면 종전대로 not_ready — 30분 뒤 다시 시도한다."""
    monkeypatch.setattr(seminar_survey, "open_survey", lambda page, sid: (None, "팝업 안 열림"))
    _stub_detail(monkeypatch, "unknown")
    item = {"id": 5600, "start": "2026-09-09(수) 18:30 ~ 20:00"}
    now = datetime(2026, 9, 9, 18, 35, tzinfo=seminar_survey.kst)  # 창은 19:00부터

    result = seminar_survey.run_survey(MagicMock(), item, bank_paths={}, now_dt=now)

    assert result["status"] == "not_ready"


# ---------------------------------------------------------------------------
# www(데스크톱) 상세의 완료 문구 — 2026-08-31 세미나 5633
# 사용자는 설문을 마쳤는데 두 계정 모두 unverified로 떨어졌다. 상세에 m의
# '설문 참여 완료'도 '세미나 종료'도 없고 '응답완료'/'설문하기'만 있었다.
# ---------------------------------------------------------------------------

def test_detect_survey_marker_accepts_the_desktop_wording():
    """www 상세의 '응답완료'도 완료 표시다(2026-08-31 실측)."""
    assert seminar_survey.detect_survey_marker(["응답완료", "목록"]) == "done"
    assert seminar_survey.matched_done_marker(["응답완료", "목록"]) == "응답완료"


def test_detect_survey_marker_treats_survey_button_as_not_done():
    """아직 누를 수 있는 '설문하기'가 보이면 미참여다."""
    assert seminar_survey.detect_survey_marker(["설문하기", "목록"]) == "not_done"
    # 완료 표시와 같이 잡히면 완료가 이긴다.
    assert seminar_survey.detect_survey_marker(["설문하기", "응답완료"]) == "done"


def test_matched_done_marker_is_empty_without_evidence():
    assert seminar_survey.matched_done_marker(["설문하기", "목록"]) == ""


def _detail_page(entries):
    page = MagicMock()
    page.evaluate.return_value = entries
    return page


def test_read_detail_buttons_splits_visible_and_hidden():
    """상세에는 안 보이는 팝업·템플릿 버튼이 섞여 있어 갈라 읽어야 한다."""
    page = _detail_page([
        {"t": "응답완료", "v": True},
        {"t": "목록", "v": True},
        {"t": "설문하기", "v": False},
        {"t": "동의합니다.", "v": False},
    ])
    visible, hidden = seminar_survey.read_detail_buttons(page)
    assert visible == ["응답완료", "목록"]
    assert hidden == ["설문하기", "동의합니다."]


def test_read_detail_buttons_accepts_plain_strings():
    """JS가 예전 형식(문자열 목록)을 돌려줘도 판정을 포기하지 않는다."""
    assert seminar_survey.read_detail_buttons(_detail_page(["응답완료"])) == (["응답완료"], [])


def test_read_detail_buttons_survives_evaluate_failure():
    page = MagicMock()
    page.evaluate.side_effect = RuntimeError("boom")
    assert seminar_survey.read_detail_buttons(page) == ([], [])


def _patch_detail_read(monkeypatch, visible, hidden):
    monkeypatch.setattr(seminar_survey.common, "goto_with_retry", lambda *a, **k: None)
    monkeypatch.setattr(seminar_survey, "read_detail_buttons", lambda page: (visible, hidden))
    monkeypatch.setattr(seminar_survey, "body_text", lambda page: "")


def test_confirm_survey_done_judges_by_visible_buttons_only(monkeypatch):
    """숨은 '응답완료'로 완료를 선언하면 미참여를 성공으로 올린다 — 보이는 것만 본다."""
    _patch_detail_read(monkeypatch, ["설문하기", "목록"], ["응답완료"])
    verdict, buttons = seminar_survey.confirm_survey_done(MagicMock(), 5633)
    assert verdict == "not_done"
    assert buttons == ["설문하기", "목록"]


def test_confirm_survey_done_falls_back_to_hidden_when_nothing_is_visible(monkeypatch):
    """보이는 버튼이 하나도 없으면 읽기가 실패한 것이므로 그때는 전체를 본다."""
    _patch_detail_read(monkeypatch, [], ["응답완료", "목록"])
    assert seminar_survey.confirm_survey_done(MagicMock(), 5633)[0] == "done"


def test_finalize_after_submit_names_the_marker_it_actually_saw(monkeypatch):
    """verified_by는 실제로 걸린 문구를 실어야 한다 — 도메인마다 문구가 다르다."""
    monkeypatch.setattr(
        seminar_survey, "confirm_survey_done",
        lambda page, sid, retries=0: ("done", ["응답완료", "목록"]),
    )
    out = seminar_survey.finalize_after_submit(MagicMock(), 5633, 1, "세미나")
    assert out["status"] == "success"
    assert out["verified_by"] == "detail_button: 응답완료"


def test_finalize_after_submit_keeps_hidden_buttons_for_diagnosis(monkeypatch):
    """판정 실패 시 다음 런에서 문구를 확정할 수 있도록 숨은 버튼까지 남긴다."""
    monkeypatch.setattr(
        seminar_survey, "confirm_survey_done",
        lambda page, sid, retries=0: ("unknown", ["목록"]),
    )
    monkeypatch.setattr(seminar_survey, "read_detail_buttons", lambda page: (["목록"], ["숨은버튼"]))
    monkeypatch.setattr(seminar_survey.common, "save_screenshot", lambda page, name: "shot.png")
    out = seminar_survey.finalize_after_submit(MagicMock(), 5633, 1)
    assert out["status"] == "unverified"
    assert out["detail_buttons_hidden"] == ["숨은버튼"]


# ---------------------------------------------------------------------------
# 상세 판정은 모바일 우선 (2026-08-31 사용자 지시)
# www는 완료 표시가 '응답완료' 한 단어라 사람이 눈으로 확인하기 어렵다.
# m은 '설문 참여 완료'가 그대로 뜬다.
# ---------------------------------------------------------------------------

class _FakeDetailPage:
    """도메인별로 다른 버튼을 돌려주는 상세 페이지 mock."""

    def __init__(self, by_host, url_after_goto=None):
        self.by_host = by_host          # {"m": [...], "www": [...]}
        self.visited = []
        self.headers = []
        self.url = ""
        self._url_after_goto = url_after_goto

    def goto(self, url, **kwargs):
        self.visited.append(url)
        self.url = self._url_after_goto or url

    def set_extra_http_headers(self, headers):
        self.headers.append(headers)

    def wait_for_timeout(self, ms):
        pass

    def _host(self):
        return "m" if self.url.startswith(seminar_survey.MOBILE_BASE) else "www"

    def buttons(self):
        return self.by_host.get(self._host(), [])


def _patch_detail_transport(monkeypatch):
    monkeypatch.setattr(
        seminar_survey.common, "goto_with_retry",
        lambda page, url, **kw: page.goto(url),
    )
    monkeypatch.setattr(
        seminar_survey, "read_detail_buttons", lambda page: (page.buttons(), []),
    )
    monkeypatch.setattr(seminar_survey, "body_text", lambda page: "")


def test_confirm_survey_done_reads_the_mobile_detail_first(monkeypatch):
    """m에서 완료가 확인되면 www는 열지 않는다."""
    page = _FakeDetailPage({"m": ["설문 참여 완료", "세미나 종료", "로그아웃"]})
    _patch_detail_transport(monkeypatch)

    verdict, buttons = seminar_survey.confirm_survey_done(page, 5633)

    assert verdict == "done"
    assert "설문 참여 완료" in buttons
    assert page.visited == [f"{seminar_survey.MOBILE_DETAIL_URL}/5633"]
    # 모바일 UA를 씌웠다가 원상복구해야 www 조회가 데스크톱으로 돈다.
    assert page.headers == [{"User-Agent": seminar_survey.MOBILE_UA}, {}]


def test_confirm_survey_done_falls_back_to_www(monkeypatch):
    """m이 안내 페이지로 떨어지면 그 판정은 버리고 www로 간다(2026-08-31 실측)."""
    page = _FakeDetailPage({
        "m": ["뒤로 가기", "닥터빌로 이동하기"],   # m 상세가 아님 → 판정 폐기
        "www": ["응답완료", "목록"],
    })
    _patch_detail_transport(monkeypatch)

    verdict, buttons = seminar_survey.confirm_survey_done(page, 5633)

    assert verdict == "done"
    assert buttons == ["응답완료", "목록"]
    assert len(page.visited) == 2


def test_confirm_survey_done_ignores_mobile_when_redirected_to_www(monkeypatch):
    """m 주소가 www로 튕기면 모바일 판정으로 치지 않는다."""
    page = _FakeDetailPage(
        {"www": ["설문하기", "로그아웃"]},
        url_after_goto="https://www.doctorville.co.kr/seminar/seminarDetail?seminarId=5633",
    )
    _patch_detail_transport(monkeypatch)

    assert seminar_survey.confirm_survey_done(page, 5633)[0] == "not_done"


def test_confirm_survey_done_keeps_mobile_verdict_when_www_says_nothing(monkeypatch):
    """www가 판정 불가여도 로그인된 m의 미참여 판정은 살린다."""
    page = _FakeDetailPage({
        "m": ["세미나 종료", "로그아웃"],
        "www": ["목록"],
    })
    _patch_detail_transport(monkeypatch)

    verdict, buttons = seminar_survey.confirm_survey_done(page, 5633)
    assert verdict == "not_done"
    assert buttons == ["세미나 종료", "로그아웃"]


def test_confirm_survey_done_reports_both_navigation_failures(monkeypatch):
    """두 도메인 다 못 열면 무엇이 실패했는지 결과에 남는다."""
    page = _FakeDetailPage({})
    monkeypatch.setattr(
        seminar_survey.common, "goto_with_retry",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("ERR_CONNECTION_CLOSED")),
    )
    verdict, buttons = seminar_survey.confirm_survey_done(page, 5633)

    assert verdict == "unknown"
    assert len(buttons) == 2
    assert any(b.startswith("m 상세 재접속 실패") for b in buttons)
    assert any(b.startswith("www 상세 재접속 실패") for b in buttons)


def test_mobile_not_done_needs_login_evidence(monkeypatch):
    """로그아웃 화면에도 '설문하기'는 뜬다 — 로그인 증거 없는 미참여는 채택하지 않는다."""
    page = _FakeDetailPage({"m": ["설문하기"], "www": ["목록"]})
    _patch_detail_transport(monkeypatch)

    assert seminar_survey.confirm_survey_done(page, 5633)[0] == "unknown"


def test_mobile_done_is_taken_without_login_evidence(monkeypatch):
    """완료 표시는 로그아웃 화면에 뜰 수 없으므로 그대로 믿는다."""
    page = _FakeDetailPage({"m": ["설문 참여 완료", "세미나 종료"]})
    _patch_detail_transport(monkeypatch)

    assert seminar_survey.confirm_survey_done(page, 5633)[0] == "done"


def test_mobile_detail_waits_for_the_marker_to_render(monkeypatch):
    """m 상세는 뼈대를 먼저 그린다 — 표식이 늦게 붙어도 놓치지 않아야 한다."""
    page = _FakeDetailPage({"m": ["뒤로 가기"]})
    _patch_detail_transport(monkeypatch)

    reads = {"n": 0}

    def late_buttons(_page):
        reads["n"] += 1
        # 세 번째 읽기부터 완료 표식이 붙는다.
        if reads["n"] >= 3:
            return ["설문 참여 완료", "세미나 종료"], []
        return ["뒤로 가기"], []

    monkeypatch.setattr(seminar_survey, "read_detail_buttons", late_buttons)

    assert seminar_survey.confirm_survey_done(page, 5602)[0] == "done"


# ---------------------------------------------------------------------------
# 페이지 진행 (세미나 5616, 2026-09-07)
# ---------------------------------------------------------------------------

def _radio_question(number="1", text="만족하셨습니까?"):
    return {
        "number": number, "question": text, "kind": "radio", "name": f"q{number}",
        "options": [
            {"text": "예", "id": f"o{number}a", "name": f"q{number}", "value": "1",
             "qnum": number, "index": 0},
            {"text": "아니오", "id": f"o{number}b", "name": f"q{number}", "value": "2",
             "qnum": number, "index": 1},
        ],
    }


class _ScriptedPage:
    """읽을 때마다 정해진 문항 목록을 내주는 설문 창.

    `evaluate`에 넘어온 JS로 어떤 조회인지 가른다 — 호출 순서에 묶이지 않는다.
    """

    def __init__(self, reads, errors=None):
        self._reads = list(reads)
        self._errors = errors or []
        self.reads = 0
        self.waited_ms = 0
        self.probed = 0

    def is_closed(self):
        return False

    def evaluate(self, js):
        if "CSS.escape" in js:            # tick_consents
            return []
        if 'role="alert"' in js:          # page_error_texts
            return list(self._errors)
        if "outside:" in js:              # stuck_probe
            self.probed += 1
            return {"questions": [], "outside": []}
        self.reads += 1                   # read_questions
        idx = min(self.reads - 1, len(self._reads) - 1)
        return list(self._reads[idx])

    def wait_for_timeout(self, ms):
        self.waited_ms += ms

    def close(self):
        pass


def test_wait_for_page_change_polls_until_the_page_actually_changes():
    """고정 5초로 한 번만 읽던 것이 문제였다 — 늦게 바뀌어도 잡아야 한다."""
    p1, p2 = [_radio_question("1")], [_radio_question("2", "다른 문항")]
    page = _ScriptedPage([p1, p1, p1, p2])
    before = seminar_survey.page_fingerprint(p1)

    moved, questions = seminar_survey.wait_for_page_change(page, before)

    assert moved is True
    assert seminar_survey.page_fingerprint(questions) != before
    # 바뀌자마자 끝난다 — 남은 대기 시간을 통째로 태우지 않는다.
    assert page.waited_ms < seminar_survey.ADVANCE_WAIT_MS


def test_wait_for_page_change_reports_no_change_after_timeout():
    """정말 안 바뀌면 제한 시간까지 기다린 뒤 False를 돌려준다."""
    p1 = [_radio_question("1")]
    page = _ScriptedPage([p1])

    moved, questions = seminar_survey.wait_for_page_change(page, seminar_survey.page_fingerprint(p1))

    assert moved is False
    assert questions == p1
    assert page.waited_ms >= seminar_survey.ADVANCE_WAIT_MS


def test_wait_for_page_change_treats_a_closed_window_as_progress():
    page = _ScriptedPage([[_radio_question("1")]])
    page.is_closed = lambda: True

    assert seminar_survey.wait_for_page_change(page, "무엇이든") == (True, None)


def _stuck_run(monkeypatch, page, advance=None):
    button = advance or MagicMock()
    monkeypatch.setattr(seminar_survey, "open_survey", lambda p, sid: (page, ""))
    monkeypatch.setattr(seminar_survey, "dismiss_alerts", lambda p: [])
    monkeypatch.setattr(seminar_survey, "apply_plan", lambda p, plan: None)
    monkeypatch.setattr(seminar_survey, "find_advance_button", lambda p: (button, "next"))
    monkeypatch.setattr(seminar_survey, "dump_survey_dom", lambda p, sid: "dom.html")
    monkeypatch.setattr(seminar_survey.common, "save_screenshot", lambda p, name: "shot.png")
    _stub_detail(monkeypatch, "unknown", [])
    return button, seminar_survey.run_survey(MagicMock(), 5616, bank_paths={})


def test_run_survey_retries_the_advance_click_before_declaring_stuck(monkeypatch):
    """한 번 눌러서 안 넘어가면 다시 눌러 본다. 페이지가 그대로면 중복 제출이 아니다."""
    page = _ScriptedPage([[_radio_question("1")]])

    button, result = _stuck_run(monkeypatch, page)

    assert button.click.call_count == seminar_survey.ADVANCE_ATTEMPTS
    assert result["status"] == "failed"
    # 눌린 횟수와 무관하게 "제출한 페이지"는 1이다.
    assert result["pages"] == 1


def test_stuck_result_carries_why_it_did_not_advance(monkeypatch):
    """artifact는 7일이면 사라진다 — 원인은 결과 JSON에 실려야 한다(세미나 5616)."""
    page = _ScriptedPage([[_radio_question("1")]], errors=["필수 항목입니다."])

    _, result = _stuck_run(monkeypatch, page)

    assert result["page_errors"] == ["필수 항목입니다."]
    assert result["stuck_probe"] == {"questions": [], "outside": []}
    assert result["dom_dump"] == "dom.html"
    assert result["screenshot"] == "shot.png"
    assert result["advance"] == "next"


def test_dismissed_alert_text_is_kept_in_the_result(monkeypatch):
    """진행이 막힌 이유가 알림 문구에 적혀 있는데 예전엔 버려졌다."""
    page = _ScriptedPage([[_radio_question("1")]])
    monkeypatch.setattr(seminar_survey, "dismiss_alerts", lambda p: ["필수 문항에 응답해 주세요."])
    monkeypatch.setattr(seminar_survey, "open_survey", lambda p, sid: (page, ""))
    monkeypatch.setattr(seminar_survey, "apply_plan", lambda p, plan: None)
    monkeypatch.setattr(seminar_survey, "find_advance_button", lambda p: (MagicMock(), "next"))
    monkeypatch.setattr(seminar_survey, "dump_survey_dom", lambda p, sid: "")
    monkeypatch.setattr(seminar_survey.common, "save_screenshot", lambda p, name: "")
    _stub_detail(monkeypatch, "unknown", [])

    result = seminar_survey.run_survey(MagicMock(), 5616, bank_paths={})

    assert "필수 문항에 응답해 주세요." in result["alerts"]


# ---------------------------------------------------------------------------
# 문항 목록 밖의 개인정보 동의 체크박스 (tick_consents)
# ---------------------------------------------------------------------------

def test_tick_consents_returns_labels_it_checked():
    page = MagicMock()
    page.evaluate.return_value = ["개인정보 수집·이용에 동의합니다."]

    assert seminar_survey.tick_consents(page) == ["개인정보 수집·이용에 동의합니다."]


def test_tick_consents_never_raises():
    """여기서 죽으면 답을 다 채운 설문까지 통째로 실패한다."""
    page = MagicMock()
    page.evaluate.side_effect = RuntimeError("boom")

    assert seminar_survey.tick_consents(page) == []


def test_checked_consents_are_recorded_in_the_result(monkeypatch):
    page = _ScriptedPage([[_radio_question("1")]])
    monkeypatch.setattr(seminar_survey, "tick_consents", lambda p: ["개인정보 수집에 동의합니다."])
    monkeypatch.setattr(seminar_survey, "open_survey", lambda p, sid: (page, ""))
    monkeypatch.setattr(seminar_survey, "dismiss_alerts", lambda p: [])
    monkeypatch.setattr(seminar_survey, "apply_plan", lambda p, plan: None)
    monkeypatch.setattr(seminar_survey, "find_advance_button", lambda p: (MagicMock(), "next"))
    monkeypatch.setattr(seminar_survey, "dump_survey_dom", lambda p, sid: "")
    monkeypatch.setattr(seminar_survey.common, "save_screenshot", lambda p, name: "")
    _stub_detail(monkeypatch, "unknown", [])

    result = seminar_survey.run_survey(MagicMock(), 5616, bank_paths={})

    assert result["consents"] == ["개인정보 수집에 동의합니다."]


# ---------------------------------------------------------------------------
# 조건부로 열리는 문항 / 일시적 "문항 0건" (세미나 5616·5623, 2026-09-07)
# ---------------------------------------------------------------------------

def test_wait_for_page_change_does_not_trust_a_single_empty_read():
    """재렌더 중 잠깐 0건으로 읽히는 것을 '제출 완료'로 오해하면 안 된다."""
    p1 = [_radio_question("1")]
    # 한 번만 0건이었다가 원래 페이지로 돌아온다.
    page = _ScriptedPage([p1, [], p1, p1, p1, p1, p1, p1])

    moved, questions = seminar_survey.wait_for_page_change(
        page, seminar_survey.page_fingerprint(p1), timeout_ms=2000
    )

    assert moved is False
    assert questions == p1


def test_wait_for_page_change_accepts_a_sustained_empty_page():
    """정말 제출돼서 계속 0건이면 넘어간 것으로 본다."""
    p1 = [_radio_question("1")]
    page = _ScriptedPage([p1] + [[]] * (seminar_survey.EMPTY_CONFIRM_POLLS + 2))

    moved, questions = seminar_survey.wait_for_page_change(page, seminar_survey.page_fingerprint(p1))

    assert moved is True
    assert questions == []


def test_answerable_count_ignores_items_without_controls():
    assert seminar_survey.answerable_count([
        _radio_question("1"),
        {"number": "2", "question": "안내문", "kind": "unknown", "name": "", "options": []},
    ]) == 1


class _RevealPage:
    """1번에 답해야 11번의 보기가 생기는 설문 창(세미나 5616 실측)."""

    def __init__(self):
        self.answered = False
        self.reads = 0

    def _questions(self):
        q11 = (_radio_question("11", "전반적으로 만족하셨습니까?") if self.answered
               else {"number": "11", "question": "전반적으로 만족하셨습니까?",
                     "kind": "unknown", "name": "", "options": []})
        return [_radio_question("1", "전공의 이신가요?"), q11]

    def is_closed(self):
        return False

    def evaluate(self, js):
        if "CSS.escape" in js:
            return []
        if 'role="alert"' in js:
            return []
        if "outside:" in js:
            return {"questions": [], "outside": []}
        self.reads += 1
        return self._questions()

    def wait_for_timeout(self, ms):
        pass

    def close(self):
        pass


def test_run_survey_fills_questions_that_only_appear_after_answering(monkeypatch):
    """세미나 5616 11번: 1번에 답하기 전에는 보기가 없어 static으로 빠졌다."""
    page = _RevealPage()
    applied = []

    def fake_apply(survey_page, plan):
        applied.append(plan)
        page.answered = True      # 1번에 답하면 11번 보기가 열린다

    monkeypatch.setattr(seminar_survey, "open_survey", lambda p, sid: (page, ""))
    monkeypatch.setattr(seminar_survey, "dismiss_alerts", lambda p: [])
    monkeypatch.setattr(seminar_survey, "tick_consents", lambda p: [])
    monkeypatch.setattr(seminar_survey, "apply_plan", fake_apply)
    monkeypatch.setattr(seminar_survey, "find_advance_button", lambda p: (MagicMock(), "next"))
    monkeypatch.setattr(seminar_survey, "dump_survey_dom", lambda p, sid: "")
    monkeypatch.setattr(seminar_survey.common, "save_screenshot", lambda p, name: "")
    _stub_detail(monkeypatch, "unknown", [])

    result = seminar_survey.run_survey(MagicMock(), 5616, bank_paths={})

    # 1라운드는 1번만, 2라운드에서 열린 11번까지 채운다.
    assert len(applied) >= 2
    assert len(applied[0]) == 1
    assert len(applied[-1]) == 2
    assert result.get("revealed") == 1
    assert "static_items" not in result   # 11번이 더 이상 static이 아니다
