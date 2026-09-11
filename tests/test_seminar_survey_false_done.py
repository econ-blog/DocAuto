"""거짓 `already_done` 방지와 '실제 종료 기준' 마감.

2026-09-11 실측 두 건이 출발점이다.
  - 세미나 5639(리브레, 18:30~20:00 공지): 제출한 적이 없는데 bjh7790이
    `already_done`. 같은 런에서 wonju는 공지 기준 마감에 걸려 `closed`.
  - 세미나 5696(카보메틱스, 19:00~22:10): 21:05, 즉 **끝나기 전**에
    wonju가 `already_done`. 같은 런의 bjh7790은 `unverified`.
계정마다 판정이 갈렸다는 것은 세미나 상세가 아니라 계정별로 내용이 다른
페이지(목록 등)를 읽었다는 뜻이다.
"""

from datetime import datetime, timedelta
from unittest.mock import MagicMock

from common import KST
import seminar_survey


# ---------------------------------------------------------------------------
# ① 상세 페이지 신원 확인
# ---------------------------------------------------------------------------

def test_detail_url_must_point_at_the_same_seminar():
    assert seminar_survey.detail_url_matches("https://m.doctorville.co.kr/cme/vod/5696", 5696)
    assert seminar_survey.detail_url_matches(
        "https://www.doctorville.co.kr/seminar/seminarDetail?seminarId=5696", "5696"
    )
    # 다른 세미나의 상세
    assert not seminar_survey.detail_url_matches("https://m.doctorville.co.kr/cme/vod/5633", 5696)
    # id가 없는 주소 = 목록·안내 페이지. 여기서 읽은 '응답완료'는 남의 것이다.
    assert not seminar_survey.detail_url_matches("https://m.doctorville.co.kr/cme/vod", 5696)
    assert not seminar_survey.detail_url_matches("https://m.doctorville.co.kr/cme/vodList", 5696)


def test_detail_url_check_does_not_block_when_url_is_unreadable():
    """URL을 못 읽으면 그것을 근거로 판정을 버리지는 않는다."""
    assert seminar_survey.detail_url_matches("", 5696)
    assert seminar_survey.detail_url_matches(None, 5696)


def test_read_detail_verdict_discards_other_pages(monkeypatch):
    """목록으로 떨어졌으면 '응답완료'가 보여도 판정하지 않는다."""
    page = MagicMock()
    page.url = "https://m.doctorville.co.kr/cme/vod"
    monkeypatch.setattr(seminar_survey.common, "goto_with_retry", lambda *a, **k: None)
    monkeypatch.setattr(seminar_survey, "read_detail_buttons", lambda p: (["응답완료", "목록"], []))
    monkeypatch.setattr(seminar_survey, "body_text", lambda p: "")

    verdict, buttons, err = seminar_survey.read_detail_verdict(page, 5696, mobile=True)

    assert verdict == "unknown"
    assert buttons == []
    assert "5696" in err


# ---------------------------------------------------------------------------
# ② 본문 전체로는 완료를 선언하지 않는다
# ---------------------------------------------------------------------------

def test_body_text_can_never_produce_done():
    """본문에는 다른 세미나의 완료 표시와 안내 문구가 섞인다."""
    texts = ["설문 응답완료 시 포인트가 지급됩니다."]
    assert seminar_survey.detect_survey_marker(texts) == "done"
    assert seminar_survey.detect_survey_marker(texts, allow_done=False) == "unknown"
    # 미참여 판정은 그대로 살려 둔다 — 틀려도 다음 런이 회복한다.
    assert seminar_survey.detect_survey_marker(["세미나 종료"], allow_done=False) == "not_done"


# ---------------------------------------------------------------------------
# ③ 진행 중인 세미나는 완료일 수 없다
# ---------------------------------------------------------------------------

def test_already_done_is_discarded_while_the_seminar_runs(monkeypatch):
    """5696 재현 — 완료 화면에는 '세미나 종료'가 나란히 뜬다.

    상세를 읽었는데 '세미나 종료'가 없다면 그 '응답완료'는 남의 페이지 것이다.
    """
    monkeypatch.setattr(seminar_survey, "open_survey", lambda page, sid: (None, "팝업 안 열림"))
    monkeypatch.setattr(
        seminar_survey, "confirm_survey_done",
        lambda page, sid, retries=0: ("done", ["응답완료", "목록"]),
    )
    monkeypatch.setattr(seminar_survey, "probe_saw_running_seminar", lambda: True)
    monkeypatch.setattr(seminar_survey, "probe_saw_ended_seminar", lambda: False)
    state = {}
    result = seminar_survey.run_survey(
        MagicMock(),
        {"id": 5696, "title": "CABOMETYX", "start": "2026-09-11(금) 19:00 ~ 22:10"},
        now_dt=datetime(2026, 9, 11, 21, 5, tzinfo=KST),
        state=state,
        account="wonju",
    )

    assert result["status"] == "unverified"
    assert "진행 중" in result["message"]
    assert seminar_survey.get_survey_meta(state, "wonju", 5696).get("ended_at") is None


def test_already_done_is_kept_when_the_detail_shows_the_seminar_ended(monkeypatch):
    """끝난 뒤의 '응답완료'는 사용자가 손으로 제출한 경우다 — 그대로 믿는다."""
    monkeypatch.setattr(seminar_survey, "open_survey", lambda page, sid: (None, "팝업 안 열림"))
    monkeypatch.setattr(
        seminar_survey, "confirm_survey_done",
        lambda page, sid, retries=0: ("done", ["응답완료", "세미나 종료"]),
    )
    monkeypatch.setattr(seminar_survey, "probe_saw_running_seminar", lambda: False)
    monkeypatch.setattr(seminar_survey, "probe_saw_ended_seminar", lambda: True)
    result = seminar_survey.run_survey(
        MagicMock(),
        {"id": 5642, "title": "골다공증", "start": "2026-09-10(목) 13:00 ~ 14:00"},
        now_dt=datetime(2026, 9, 10, 14, 53, tzinfo=KST),
    )

    assert result["status"] == "already_done"
    assert result["verified_by"] == "detail_button: 응답완료"


def test_unopened_is_quiet_while_the_seminar_is_still_running(monkeypatch):
    """설문은 세미나가 끝나야 열린다 — 진행 중에 못 여는 건 실패가 아니다."""
    monkeypatch.setattr(seminar_survey, "open_survey", lambda page, sid: (None, "팝업 안 열림"))
    monkeypatch.setattr(
        seminar_survey, "confirm_survey_done",
        lambda page, sid, retries=0: ("unknown", ["입장하기"]),
    )
    monkeypatch.setattr(seminar_survey, "probe_saw_running_seminar", lambda: True)
    monkeypatch.setattr(seminar_survey, "probe_saw_ended_seminar", lambda: False)
    result = seminar_survey.run_survey(
        MagicMock(),
        {"id": 5696, "start": "2026-09-11(금) 19:00 ~ 22:10"},
        now_dt=datetime(2026, 9, 11, 21, 5, tzinfo=KST),
    )
    assert result["status"] == "not_ready"


def test_unopened_after_the_end_is_still_an_alert(monkeypatch):
    """끝났는데 창이 안 열리면 그건 놓친 것이다 — 조용히 넘기지 않는다."""
    monkeypatch.setattr(seminar_survey, "open_survey", lambda page, sid: (None, "팝업 안 열림"))
    monkeypatch.setattr(
        seminar_survey, "confirm_survey_done",
        lambda page, sid, retries=0: ("unknown", ["목록"]),
    )
    monkeypatch.setattr(seminar_survey, "probe_saw_running_seminar", lambda: False)
    monkeypatch.setattr(seminar_survey, "probe_saw_ended_seminar", lambda: False)
    result = seminar_survey.run_survey(
        MagicMock(),
        {"id": 5639, "start": "2026-09-11(금) 18:30 ~ 20:00"},
        now_dt=datetime(2026, 9, 11, 21, 6, tzinfo=KST),
    )
    assert result["status"] == "unverified"


# ---------------------------------------------------------------------------
# ④ 창은 공지가 아니라 실제 종료 기준
# ---------------------------------------------------------------------------

def test_window_is_anchored_on_the_observed_end():
    """설문은 실제 종료에 열리고 1시간 뒤 닫힌다. 공지는 쓰지 않는다."""
    item = {"start": "2026-09-11(금) 18:30 ~ 20:00", "ended_at": "2026-09-11T21:10:00+09:00"}
    assert seminar_survey.get_survey_window(item) == (
        datetime(2026, 9, 11, 21, 10, tzinfo=KST),
        datetime(2026, 9, 11, 22, 10, tzinfo=KST),
    )
    assert seminar_survey.evaluate_survey_cutoff(
        item, datetime(2026, 9, 11, 21, 0, tzinfo=KST)) == "not_ready"
    assert seminar_survey.evaluate_survey_cutoff(
        item, datetime(2026, 9, 11, 21, 40, tzinfo=KST)) == "ready"
    assert seminar_survey.evaluate_survey_cutoff(
        item, datetime(2026, 9, 11, 22, 30, tzinfo=KST)) == "closed"


def test_announced_end_never_closes_the_window():
    """5639 재현 — 공지 종료(20:00)만 보고 21:06에 닫으면 열려 있는 설문을 버린다."""
    item = {"start": "2026-09-11(금) 18:30 ~ 20:00"}
    assert seminar_survey.get_survey_cutoff(item) is None
    assert seminar_survey.evaluate_survey_cutoff(
        item, datetime(2026, 9, 11, 21, 6, tzinfo=KST)) == "ready"


def test_probe_gate_keeps_the_request_count_unchanged():
    """종료를 관측하려면 열어 봐야 하지만, 시작 30분 전까지는 건드리지 않는다."""
    item = {"start": "2026-09-11(금) 18:30 ~ 20:00"}
    assert seminar_survey.evaluate_survey_cutoff(
        item, datetime(2026, 9, 11, 18, 45, tzinfo=KST)) == "not_ready"
    assert seminar_survey.evaluate_survey_cutoff(
        item, datetime(2026, 9, 11, 19, 5, tzinfo=KST)) == "ready"


def test_unobserved_seminars_stop_being_retried_eventually():
    """끝내 종료를 못 봐도 영원히 재시도하지는 않는다."""
    item = {"start": "2026-09-11(금) 18:30 ~ 20:00"}
    stale = datetime(2026, 9, 11, 18, 30, tzinfo=KST) + seminar_survey.SURVEY_STALE_AFTER
    assert seminar_survey.evaluate_survey_cutoff(item, stale + timedelta(minutes=1)) == "closed"


def test_opening_the_survey_records_the_actual_end(monkeypatch):
    """설문 창이 열렸다 = 세미나가 끝났다. 공지보다 이른 종료도 이걸로 잡힌다."""
    monkeypatch.setattr(seminar_survey, "open_survey", lambda page, sid: (MagicMock(), ""))
    monkeypatch.setattr(seminar_survey, "read_questions", lambda p: [])
    monkeypatch.setattr(seminar_survey, "dismiss_alerts", lambda p, **k: [])
    monkeypatch.setattr(seminar_survey, "tick_consents", lambda p: [])
    monkeypatch.setattr(
        seminar_survey, "unopened_status", lambda item, now=None, running=False: "unverified"
    )
    state = {}
    seminar_survey.run_survey(
        MagicMock(),
        {"id": 5694, "start": "2026-09-10(목) 20:00 ~ 22:00"},
        now_dt=datetime(2026, 9, 10, 21, 5, tzinfo=KST),
        state=state,
        account="bjh7790",
    )

    ended = seminar_survey.get_survey_meta(state, "bjh7790", 5694).get("ended_at")
    assert ended and ended.startswith("2026-09-10T21:05")


def test_ended_at_keeps_the_earliest_observation():
    """관측은 실제 종료보다 뒤다 — 나중 관측으로 덮으면 창이 통째로 밀린다."""
    state = {}
    seminar_survey.mark_survey_ended(state, "bjh7790", 5694, "2026-09-10T21:05:00+09:00")
    seminar_survey.mark_survey_ended(state, "bjh7790", 5694, "2026-09-10T21:35:00+09:00")
    assert seminar_survey.get_survey_meta(state, "bjh7790", 5694)["ended_at"].startswith(
        "2026-09-10T21:05"
    )


def test_probe_helpers_read_the_last_probe():
    seminar_survey.LAST_DETAIL_PROBE.clear()
    seminar_survey.LAST_DETAIL_PROBE["m"] = {"visible": ["입장하기"], "hidden": [], "ended": False}
    assert seminar_survey.probe_saw_running_seminar()
    assert not seminar_survey.probe_saw_ended_seminar()

    seminar_survey.LAST_DETAIL_PROBE["m"] = {"visible": ["세미나 종료"], "hidden": [], "ended": True}
    assert not seminar_survey.probe_saw_running_seminar()
    assert seminar_survey.probe_saw_ended_seminar()

    seminar_survey.LAST_DETAIL_PROBE["m"] = {"visible": [], "hidden": [], "ended": False}
    assert not seminar_survey.probe_saw_running_seminar()
    seminar_survey.LAST_DETAIL_PROBE.clear()
