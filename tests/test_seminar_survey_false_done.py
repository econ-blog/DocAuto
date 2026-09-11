"""거짓 `already_done` 방지와 '실제 종료 기준' 마감.

2026-09-11 실측 두 건이 출발점이다.
  - 세미나 5639(리브레, 18:30~20:00 공지): 제출한 적이 없는데 bjh7790이
    `already_done`. 같은 런에서 wonju는 공지 기준 마감에 걸려 `closed`.
  - 세미나 5696(카보메틱스, 19:00~22:10): 21:05, 즉 **끝나기 전**에
    wonju가 `already_done`. 같은 런의 bjh7790은 `unverified`.
계정마다 판정이 갈렸다는 것은 세미나 상세가 아니라 계정별로 내용이 다른
페이지(목록 등)를 읽었다는 뜻이다.
"""

from datetime import datetime
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
# ③ 안 끝난 세미나는 완료일 수 없다
# ---------------------------------------------------------------------------

def test_seminar_has_ended_uses_the_schedule():
    item = {"start": "2026-09-11(금) 19:00 ~ 22:10"}
    assert not seminar_survey.seminar_has_ended(item, datetime(2026, 9, 11, 21, 5, tzinfo=KST))
    assert seminar_survey.seminar_has_ended(item, datetime(2026, 9, 11, 22, 10, tzinfo=KST))


def test_seminar_has_ended_respects_a_running_observation():
    """공지가 22:10이어도 22:40에 진행 중이었으면 아직 안 끝난 것이다."""
    item = {
        "start": "2026-09-11(금) 19:00 ~ 22:10",
        "running_at": "2026-09-11T22:40:00+09:00",
    }
    assert not seminar_survey.seminar_has_ended(item, datetime(2026, 9, 11, 22, 30, tzinfo=KST))


def test_already_done_is_discarded_before_the_seminar_ends(monkeypatch):
    """5696 재현 — 21:05의 done은 상세가 아니라 남의 페이지를 읽은 것이다."""
    monkeypatch.setattr(seminar_survey, "open_survey", lambda page, sid: (None, "팝업 안 열림"))
    monkeypatch.setattr(
        seminar_survey, "confirm_survey_done",
        lambda page, sid, retries=0: ("done", ["응답완료", "목록"]),
    )
    monkeypatch.setattr(seminar_survey, "probe_saw_running_seminar", lambda: False)
    state = {}
    result = seminar_survey.run_survey(
        MagicMock(),
        {"id": 5696, "title": "CABOMETYX", "start": "2026-09-11(금) 19:00 ~ 22:10"},
        now_dt=datetime(2026, 9, 11, 21, 5, tzinfo=KST),
        state=state,
        account="wonju",
    )

    assert result["status"] == "unverified"
    assert "아직 안 끝났" in result["message"]
    # 상태에 done으로 굳어 다음 런이 건너뛰는 일이 없어야 한다.
    assert seminar_survey.get_survey_meta(state, "wonju", 5696).get("status") != "done"


def test_already_done_still_works_after_the_seminar_ends(monkeypatch):
    """끝난 뒤의 '응답완료'는 사용자가 손으로 제출한 경우다 — 그대로 믿는다."""
    monkeypatch.setattr(seminar_survey, "open_survey", lambda page, sid: (None, "팝업 안 열림"))
    monkeypatch.setattr(
        seminar_survey, "confirm_survey_done",
        lambda page, sid, retries=0: ("done", ["응답완료", "목록"]),
    )
    monkeypatch.setattr(seminar_survey, "probe_saw_running_seminar", lambda: False)
    result = seminar_survey.run_survey(
        MagicMock(),
        {"id": 5642, "title": "골다공증", "start": "2026-09-10(목) 13:00 ~ 14:00"},
        now_dt=datetime(2026, 9, 10, 14, 53, tzinfo=KST),
    )

    assert result["status"] == "already_done"
    assert result["verified_by"] == "detail_button: 응답완료"


# ---------------------------------------------------------------------------
# ④ 마감은 공지가 아니라 실제 종료 기준
# ---------------------------------------------------------------------------

def test_running_observation_pushes_the_deadline():
    """5639 재현 — 21:00에 아직 방송 중이었으면 21:00 마감은 틀렸다."""
    item = {"start": "2026-09-11(금) 18:30 ~ 20:00"}
    at_2106 = datetime(2026, 9, 11, 21, 6, tzinfo=KST)
    assert seminar_survey.evaluate_survey_cutoff(item, at_2106) == "closed"

    observed = {**item, "running_at": "2026-09-11T21:00:00+09:00"}
    assert seminar_survey.evaluate_survey_cutoff(observed, at_2106) == "ready"
    assert seminar_survey.get_survey_cutoff(observed) == datetime(2026, 9, 11, 22, 0, tzinfo=KST)


def test_running_observation_cannot_extend_forever():
    """판정 마크업이 깨져 관측이 계속 갱신돼도 상한을 넘지 못한다."""
    item = {
        "start": "2026-09-11(금) 18:30 ~ 20:00",
        "running_at": "2026-09-13T09:00:00+09:00",
    }
    cap = datetime(2026, 9, 11, 20, 0, tzinfo=KST) + seminar_survey.SURVEY_RUNNING_EXTEND_CAP
    assert seminar_survey.get_survey_cutoff(item) == cap + seminar_survey.SURVEY_CLOSE_GRACE


def test_scheduled_deadline_is_unchanged_without_an_observation():
    """제때 끝난 세미나의 조용한 마감은 그대로다 — 알림이 늘어나면 안 된다."""
    item = {"start": "2026-09-10(목) 13:00 ~ 14:00"}
    assert seminar_survey.get_survey_cutoff(item) == datetime(2026, 9, 10, 15, 0, tzinfo=KST)
    assert seminar_survey.evaluate_survey_cutoff(
        item, datetime(2026, 9, 10, 15, 30, tzinfo=KST)
    ) == "closed"


def test_running_observation_is_recorded_in_state(monkeypatch):
    """상세를 읽었는데 '세미나 종료'가 없으면 진행 중으로 기록한다."""
    monkeypatch.setattr(seminar_survey, "open_survey", lambda page, sid: (None, "팝업 안 열림"))
    monkeypatch.setattr(
        seminar_survey, "confirm_survey_done",
        lambda page, sid, retries=0: ("unknown", ["입장하기", "목록"]),
    )
    monkeypatch.setattr(seminar_survey, "probe_saw_running_seminar", lambda: True)
    state = {}
    seminar_survey.run_survey(
        MagicMock(),
        {"id": 5639, "title": "Libre", "start": "2026-09-11(금) 18:30 ~ 20:00"},
        now_dt=datetime(2026, 9, 11, 21, 0, tzinfo=KST),
        state=state,
        account="bjh7790",
    )

    running = seminar_survey.get_survey_meta(state, "bjh7790", 5639).get("running_at")
    assert running and running.startswith("2026-09-11T21:00")


def test_probe_saw_running_seminar_reads_the_last_probe():
    seminar_survey.LAST_DETAIL_PROBE.clear()
    seminar_survey.LAST_DETAIL_PROBE["m"] = {"visible": ["입장하기"], "hidden": [], "ended": False}
    assert seminar_survey.probe_saw_running_seminar()

    seminar_survey.LAST_DETAIL_PROBE["m"] = {"visible": ["세미나 종료"], "hidden": [], "ended": True}
    assert not seminar_survey.probe_saw_running_seminar()

    seminar_survey.LAST_DETAIL_PROBE["m"] = {"visible": [], "hidden": [], "ended": False}
    assert not seminar_survey.probe_saw_running_seminar()
    seminar_survey.LAST_DETAIL_PROBE.clear()
