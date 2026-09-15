"""계정 간 엇갈린 설문 판정과 그 원인 (2026-09-15 세미나 5671·5681).

같은 런에서 한 계정은 `incomplete_bank`, 다른 계정은 `not_ready`가 나왔고 다음
런에서 짝이 뒤집혔다. 세 겹의 결함이었다.
  ① 팝업 열기가 단발이라 실패하면 그 런은 끝이었다.
  ② 판정을 이미 버린 상세 조회가 '진행 중'으로 읽혀, 못 연 것이 quiet
     `not_ready`로 묻혔다(공지 13:00~14:00 세미나를 14:39에 '진행 중'으로 봤다).
  ③ 한 계정이 창을 연 것이 증명된 런에서도 다른 계정의 조용한 실패가 그대로
     남았다. 두 설문 모두 손도 못 댄 채 17:05에 `closed`가 됐다.
"""

from datetime import datetime
from unittest.mock import MagicMock

import pytest

from common import KST
import seminar_survey


ITEM = {
    "id": 5671,
    "title": "ASCVD",
    "start": "2026-09-15(화) 13:00 ~ 14:00",
    "entered_at": "2026-09-15T13:02:00",
}


# ---------------------------------------------------------------------------
# ② 공지된 종료가 지난 뒤의 '진행 중' 관측은 믿지 않는다
# ---------------------------------------------------------------------------

def test_running_silences_only_before_the_scheduled_end():
    during = datetime(2026, 9, 15, 13, 40, tzinfo=KST)
    assert seminar_survey.unopened_status(ITEM, during, running=True) == "not_ready"


def test_running_after_the_scheduled_end_is_unverified():
    after = datetime(2026, 9, 15, 14, 39, tzinfo=KST)
    assert seminar_survey.unopened_status(ITEM, after, running=True) == "unverified"


def test_scheduled_end_grace_is_respected():
    # 공지된 종료(14:00) 직후는 아직 믿어 준다 — 공지보다 길어지는 세미나가 있다.
    assert not seminar_survey.scheduled_end_passed(
        ITEM, datetime(2026, 9, 15, 14, 20, tzinfo=KST)
    )
    assert seminar_survey.scheduled_end_passed(
        ITEM, datetime(2026, 9, 15, 14, 35, tzinfo=KST)
    )


def test_unknown_schedule_keeps_the_quiet_path():
    # 일정을 모르면 승격 근거가 없다. 종전대로 조용히 넘어간다.
    item = {"id": 9999}
    assert not seminar_survey.scheduled_end_passed(item, datetime(2026, 9, 15, 14, 39, tzinfo=KST))


# ---------------------------------------------------------------------------
# ① 팝업 열기 재시도
# ---------------------------------------------------------------------------

def test_open_survey_retries_after_a_popup_timeout(monkeypatch):
    calls = []
    survey_page = MagicMock()

    def fake_once(page, sid):
        calls.append(sid)
        if len(calls) == 1:
            return None, seminar_survey.SURVEY_POPUP_TIMEOUT_REASON
        return survey_page, ""

    monkeypatch.setattr(seminar_survey, "_open_survey_once", fake_once)
    page = MagicMock()
    got, err = seminar_survey.open_survey(page, 5671)
    assert got is survey_page and err == ""
    assert len(calls) == 2


def test_open_survey_does_not_retry_when_there_is_no_survey(monkeypatch):
    calls = []

    def fake_once(page, sid):
        calls.append(sid)
        return None, "설문 참여 버튼이 없음(설문 미제공 또는 종료)."

    monkeypatch.setattr(seminar_survey, "_open_survey_once", fake_once)
    got, err = seminar_survey.open_survey(MagicMock(), 5671)
    assert got is None and len(calls) == 1


def test_open_survey_reports_how_many_attempts(monkeypatch):
    monkeypatch.setattr(
        seminar_survey, "_open_survey_once",
        lambda page, sid: (None, seminar_survey.SURVEY_POPUP_TIMEOUT_REASON),
    )
    got, err = seminar_survey.open_survey(MagicMock(), 5671)
    assert got is None
    assert f"{seminar_survey.SURVEY_OPEN_ATTEMPTS}회 시도" in err


# ---------------------------------------------------------------------------
# ③ 계정 간 엇갈림 승격
# ---------------------------------------------------------------------------

def _results(bjh_status, wonju_status, sid=5671):
    return {
        "bjh7790": {"account": "bjh7790", "surveys": [{"seminarId": sid, "status": bjh_status}]},
        "wonju": {"account": "wonju", "surveys": [{"seminarId": sid, "status": wonju_status}]},
    }


@pytest.mark.parametrize("quiet", ["not_ready", "closed"])
def test_quiet_miss_is_escalated_when_another_account_opened_it(quiet):
    results = _results("incomplete_bank", quiet)
    changed = seminar_survey.escalate_divergent_surveys(results)
    assert changed == [("wonju", "5671", quiet)]
    r = results["wonju"]["surveys"][0]
    assert r["status"] == "unverified"
    assert r["diverged_from"] == quiet
    assert "bjh7790" in r["message"]
    # 계정 레벨 상태도 다시 계산된다 — notify가 보는 값이다.
    assert results["wonju"]["status"] == "unverified"
    # 창을 연 쪽은 건드리지 않는다.
    assert results["bjh7790"]["surveys"][0]["status"] == "incomplete_bank"


def test_no_escalation_without_proof_that_the_window_was_open():
    # 양쪽 다 못 열었으면 창이 열렸다는 증거가 없다. 종전 판정을 유지한다.
    results = _results("not_ready", "not_ready")
    assert seminar_survey.escalate_divergent_surveys(results) == []
    assert results["wonju"]["surveys"][0]["status"] == "not_ready"


def test_already_done_is_not_proof_that_the_window_was_open():
    # already_done은 상세 표식으로 나온다 — 설문 창을 열었다는 뜻이 아니다.
    results = _results("already_done", "not_ready")
    assert seminar_survey.escalate_divergent_surveys(results) == []


def test_escalation_clears_the_history_mark_so_the_next_run_retries():
    state = {
        "version": 2,
        "accounts": {
            "bjh7790": {"entered": [{"id": 5671}], "survey": {}},
            "wonju": {"entered": [{"id": 5671}], "survey": {"5671": "closed"}},
        },
    }
    results = _results("success", "closed")
    seminar_survey.escalate_divergent_surveys(results, state)
    assert "5671" not in state["accounts"]["wonju"]["survey"]
    assert 5671 in seminar_survey.pending_seminar_ids(state, "wonju")


def test_clear_survey_status_is_a_noop_when_nothing_is_marked():
    state = {"version": 2, "accounts": {"wonju": {"entered": [], "survey": {}}}}
    assert not seminar_survey.clear_survey_status(state, "wonju", 5671)


def test_summarize_account_counts_unverified():
    out = {"surveys": [{"status": "unverified"}, {"status": "success", "verified_by": "x"}]}
    seminar_survey.summarize_account(out)
    assert out["status"] == "unverified"
    assert "미확인 1건" in out["message"]
    # 성공이 아니면 계정 레벨 증거를 달지 않는다.
    assert "verified_by" not in out
