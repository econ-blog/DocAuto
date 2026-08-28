from notify import severity_of
from hmp import verify_comment_saved

def test_severity_with_verified_by():
    assert severity_of({"status": "success", "verified_by": "modal"}) == "ok"

def test_severity_demotes_missing_verified_by():
    assert severity_of({"status": "success"}) == "alert"

def test_verify_comment_saved():
    html_with_nick = "<div id='cmtDiv'><span class='cmtName'>승진</span>: 감사</div>"
    html_without_nick = "<div id='cmtDiv'><span class='cmtName'>다른사람</span>: 감사</div>"
    assert verify_comment_saved(html_with_nick, "승진") is True
    assert verify_comment_saved(html_without_nick, "승진") is False
    assert verify_comment_saved("", "승진") is False
    assert verify_comment_saved(html_with_nick, "") is False

def test_verified_by_contracts_all_10_modules():
    # 1. Doctorville Attend
    res_dv_attend = {"status": "success", "verified_by": "attend_confirmed"}
    assert severity_of(res_dv_attend) == "ok"
    assert res_dv_attend["verified_by"] == "attend_confirmed"

    # 2. Doctorville Quiz
    res_dv_quiz = {"status": "success", "verified_by": ":text('정답입니다')"}
    assert severity_of(res_dv_quiz) == "ok"
    assert res_dv_quiz["verified_by"] == ":text('정답입니다')"

    # 3. Doctorville Seminar Apply
    res_dv_seminar = {"status": "success", "verified_by": "a.btn_bn: 신청취소"}
    assert severity_of(res_dv_seminar) == "ok"
    assert res_dv_seminar["verified_by"] == "a.btn_bn: 신청취소"

    # 4. Keymedi Attend
    res_km = {"status": "success", "verified_by": "modal: 출석체크가 완료되었습니다"}
    assert severity_of(res_km) == "ok"
    assert res_km["verified_by"] == "modal: 출석체크가 완료되었습니다"

    # 5. HMP Capsule
    res_hmp_cap = {"status": "success", "verified_by": "popup: 10rewardPopup"}
    assert severity_of(res_hmp_cap) == "ok"
    assert res_hmp_cap["verified_by"] == "popup: 10rewardPopup"

    # 6. HMP Roulette
    res_hmp_roul = {"status": "success", "verified_by": "alt: 100캡슐 당첨"}
    assert severity_of(res_hmp_roul) == "ok"
    assert "alt:" in res_hmp_roul["verified_by"]

    # 7. HMP Comment
    res_hmp_cmt = {"status": "success", "verified_by": "nickname_verified: 승진"}
    assert severity_of(res_hmp_cmt) == "ok"
    assert res_hmp_cmt["verified_by"].startswith("nickname_verified:")

    # 8. HMP Post
    res_hmp_post = {"status": "success", "verified_by": "rtn_code_100"}
    assert severity_of(res_hmp_post) == "ok"
    assert res_hmp_post["verified_by"] == "rtn_code_100"

    # 9. Seminar Live Entry
    res_live = {"status": "success", "verified_by": "popup_acquired"}
    assert severity_of(res_live) == "ok"
    assert res_live["verified_by"] == "popup_acquired"

    # 10. Seminar Survey — 상세 재접속의 '설문 참여 완료' 버튼 (2026-08-28)
    res_survey = {"status": "success", "verified_by": "detail_button: 설문 참여 완료"}
    assert severity_of(res_survey) == "ok"
    assert res_survey["verified_by"] == "detail_button: 설문 참여 완료"


from datetime import datetime
from unittest.mock import MagicMock, patch
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
import common
from doctorville import task_seminar, task_attend
from hmp import _run_roulette


def test_doctorville_seminar_apply_no_target():
    mock_page = MagicMock()
    mock_page.evaluate.return_value = []
    res = task_seminar(mock_page, {})
    assert res["status"] == "no_target"
    assert res["count"] == 0
    assert res["message"] == "신청 가능한 세미나 없음"


def _today_cell():
    today = datetime.now(common.KST).strftime("%Y-%m-%d")
    return f'td[data-date="{today}"] div.point.complete'


def _attend_page(marker_visible_on_load):
    """오늘 셀 표식 유무를 진입 횟수별로 지정하는 mock page.

    `marker_visible_on_load[i]`가 i번째 진입에서 표식이 붙는지.
    """
    mock_page = MagicMock()
    mock_page.url = "https://www.doctorville.co.kr/event/attend"
    cell = _today_cell()
    state = {"load": 0}

    def wait_for_selector(selector, **kw):
        if selector == cell:
            idx = min(state["load"], len(marker_visible_on_load) - 1)
            if not marker_visible_on_load[idx]:
                state["load"] += 1
                raise PlaywrightTimeoutError("Timeout")
            state["load"] += 1
            return MagicMock()
        return MagicMock()

    mock_page.wait_for_selector.side_effect = wait_for_selector
    return mock_page


def test_doctorville_attend_already_done_by_today_cell():
    """진입만으로 출석 처리된 상태 — 달력의 오늘 셀이 유일한 날짜 확정 증거."""
    mock_page = _attend_page([True])

    with patch("doctorville.ensure_logged_in", return_value=True):
        res = task_attend(mock_page, {})
    assert res["status"] == "already_done"
    assert res["verified_by"] == _today_cell()


def test_doctorville_attend_success_on_revisit():
    """첫 진입 응답엔 표식이 없어도 재접속에서 확인되면 성공(진입=출석)."""
    mock_page = _attend_page([False, True])

    with patch("doctorville.ensure_logged_in", return_value=True):
        res = task_attend(mock_page, {})
    assert res["status"] == "success"
    assert res["verified_by"] == _today_cell()
    # 버튼 클릭 분기로 새지 않는다
    assert not any("출석하기" in str(c) for c in mock_page.locator.call_args_list)


def test_doctorville_attend_unverified_when_no_marker_and_no_button():
    """오늘 셀 표식도 없고 출석 버튼도 보이지 않으면 증거 없음 → unverified."""
    mock_page = _attend_page([False])
    mock_locator = MagicMock()
    mock_locator.count.return_value = 0
    mock_locator.first.wait_for.side_effect = PlaywrightTimeoutError("Timeout")
    mock_page.locator.return_value = mock_locator

    with patch("doctorville.ensure_logged_in", return_value=True), \
         patch("doctorville.save_screenshot", return_value=None):
        res = task_attend(mock_page, {})
    assert res["status"] == "unverified"
    assert res["message"] == "출석 버튼도 오늘 출석 표식도 없음"


def test_hmp_roulette_unverified_on_popup_detection_failure():
    mock_page = MagicMock()
    mock_btn = MagicMock()
    mock_btn.is_visible.return_value = True
    mock_btn.inner_text.side_effect = ["룰렛 참여하기", "참여 완료", "참여 완료"]

    start_btn = MagicMock()
    start_btn.wait_for.return_value = None

    def locator_side_effect(selector):
        if selector == "button":
            m = MagicMock()
            m.all.return_value = [mock_btn]
            return m
        if selector == "#startAbled":
            return start_btn
        m = MagicMock()
        m.count.return_value = 0
        m.all.return_value = []
        return m

    mock_page.locator.side_effect = locator_side_effect

    results = _run_roulette(mock_page, "bjh7790")
    assert len(results) == 1
    assert results[0]["status"] == "unverified"
    assert "verified_by" not in results[0]
    assert results[0]["message"] == "룰렛 참여 완료 (결과 팝업 감지 실패)"


def test_hmp_roulette_no_target_when_start_button_absent():
    mock_page = MagicMock()
    mock_btn = MagicMock()
    mock_btn.is_visible.return_value = True
    mock_btn.inner_text.side_effect = ["룰렛 참여하기", "참여 완료", "참여 완료"]

    start_btn = MagicMock()
    start_btn.wait_for.side_effect = PlaywrightTimeoutError("Timeout")

    def locator_side_effect(selector):
        if selector == "button":
            m = MagicMock()
            m.all.return_value = [mock_btn]
            return m
        if selector == "#startAbled":
            return start_btn
        m = MagicMock()
        m.count.return_value = 0
        m.all.return_value = []
        return m

    mock_page.locator.side_effect = locator_side_effect

    results = _run_roulette(mock_page, "bjh7790")
    assert len(results) == 1
    assert results[0]["status"] == "no_target"
    assert results[0]["message"] == "START 버튼이 표시되지 않음"


