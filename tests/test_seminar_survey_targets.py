"""설문 대상 선정: 입장 이력이 비어도 신청 이력으로 후보를 세운다.

2026-09-21 run 35590827440 — 블록 런이 상태 캐시를 한 번도 저장하지 못한 날,
설문은 "입장 이력 파일 없음"으로 조용히 끝났다. 창(실제 종료 + 1시간)은
아무도 모르게 닫혔다.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import seminar_applied
import seminar_survey
from survey_detail import has_survey_open_button


APPLIED = {
    "bjh7790": {
        "5644": {
            "date": "2026-09-21",
            "start_date": "2026-09-21",
            "start": "2026-09-21(월) 18:30 ~ 20:00",
            "title": "No.1 Collaboration WEB Symposium",
        },
        "5700": {
            "date": "2026-09-22",
            "start_date": "2026-09-22",
            "start": "2026-09-22(화) 13:00 ~ 14:00",
            "title": "내일 세미나",
        },
    },
    "wonju": {},
}


def _state(entered=None, survey=None):
    return {
        "version": 2,
        "date": "2026-09-21",
        "accounts": {
            "bjh7790": {
                "entered": entered or [],
                "blocks": {},
                "survey": survey or {},
            }
        },
    }


def test_applied_on_filters_by_broadcast_date():
    got = seminar_applied.applied_on(APPLIED, "bjh7790", "2026-09-21")
    assert got == [
        {
            "id": 5644,
            "title": "No.1 Collaboration WEB Symposium",
            "start": "2026-09-21(월) 18:30 ~ 20:00",
        }
    ]
    assert seminar_applied.applied_on(APPLIED, "wonju", "2026-09-21") == []
    assert seminar_applied.applied_on(APPLIED, "없는계정", "2026-09-21") == []


def test_empty_state_falls_back_to_applied():
    """캐시가 빈 날에도 후보가 선다 — 이것이 2026-09-21의 회귀다."""
    targets = seminar_survey.survey_targets(_state(), "bjh7790", APPLIED, "2026-09-21")
    assert [t["id"] for t in targets] == [5644]
    assert targets[0]["from_applied"] is True
    # 창 판정(scheduled_bounds)이 start에 걸려 있으므로 메타가 따라와야 한다.
    assert targets[0]["start"] == "2026-09-21(월) 18:30 ~ 20:00"


def test_entered_item_wins_and_is_not_duplicated():
    state = _state(entered=[{"id": 5644, "title": "T", "entered_at": "2026-09-21T18:35:00+09:00"}])
    targets = seminar_survey.survey_targets(state, "bjh7790", APPLIED, "2026-09-21")
    assert len(targets) == 1
    assert targets[0]["entered_at"] == "2026-09-21T18:35:00+09:00"
    assert "from_applied" not in targets[0]


def test_concluded_surveys_are_excluded_from_both_sources():
    state = _state(entered=[{"id": 5644}], survey={"5644": "done"})
    assert seminar_survey.survey_targets(state, "bjh7790", APPLIED, "2026-09-21") == []
    assert seminar_survey.survey_targets(_state(survey={"5644": "closed"}), "bjh7790", APPLIED, "2026-09-21") == []


def test_has_survey_open_button_separates_end_marker_from_survey_button():
    assert has_survey_open_button(["세미나 종료", "설문하기"]) is True
    assert has_survey_open_button(["세미나 종료"]) is False
    assert has_survey_open_button([]) is False


def test_no_target_is_quiet_and_last_in_rollup_priority():
    from notify import severity_of

    assert severity_of({"status": "no_target"}) == "quiet"
    assert seminar_survey.rollup_account_status(["no_target", "failed"]) == "failed"
    assert seminar_survey.rollup_account_status(["no_target", "success"]) == "success"
    assert seminar_survey.rollup_account_status(["no_target"]) == "no_target"


def test_unobserved_end_closes_after_announced_end_plus_window():
    """종료를 못 봤어도 공지된 종료 + 관측 오차 + 창이 지나면 closed(quiet)다.

    2026-09-21 세미나 5643(13:00~14:00)을 20:07에 시도해 `unverified`(alert)가
    났다. 설문 창은 실제 종료 + 1시간이라 그 시각에 열려 있을 수 없다 —
    "못 열었다"가 아니라 "이미 닫혔다"가 사실이다.
    """
    from datetime import datetime

    import common
    from survey_window import evaluate_survey_cutoff, unopened_status

    item = {"id": 5643, "start": "2026-09-21(월) 13:00 ~ 14:00"}

    def at(hhmm):
        return datetime.strptime(f"2026-09-21 {hhmm}", "%Y-%m-%d %H:%M").replace(tzinfo=common.KST)

    assert evaluate_survey_cutoff(item, at("12:00")) == "not_ready"
    assert evaluate_survey_cutoff(item, at("14:10")) == "ready"
    assert evaluate_survey_cutoff(item, at("15:20")) == "ready"
    assert evaluate_survey_cutoff(item, at("15:40")) == "closed"
    assert evaluate_survey_cutoff(item, at("20:07")) == "closed"
    assert unopened_status(item, at("20:07")) == "closed"
    # 창이 실제로 열려 있는 시간대의 실패는 그대로 alert여야 한다.
    assert unopened_status(item, at("14:40")) == "unverified"
# ---------------------------------------------------------------------------
# 빈 상세(표식 없음)도 '설문 대상 아님'이다 — 2026-09-21 세미나 5643
# ---------------------------------------------------------------------------
#
# run 35591694868(20:06)에서 5643(13:00~14:00)이 `unverified`로 떠 유지보수
# 세션을 깨웠다. m 상세는 '뒤로 가기'뿐(VOD 미등록)이고 www 상세는 메뉴·'관심'·
# '목록' 같은 껍데기 버튼만 있었다 — 종료도 설문도 아닌 빈 상세다. 설문 창은
# 6시간 전에 닫혔고 자동화가 할 일은 없었다.

from datetime import datetime
from unittest.mock import MagicMock

from common import KST

# 실측 www 상세 버튼(run 35591694868 payload)
EMPTY_DETAIL_BUTTONS = [
    "전체 메뉴 열기",
    "관심",
    "목록",
    "닥터빌 라이브세미나를 통해 선생님의 노하우를 공유해보세요!",
    "커뮤니티",
    "FAMILY SITE 목록 펼치기",
]

ITEM_5643 = {
    "id": 5643,
    "title": "Gastro-protection Strategies in NSAIDs Therapy",
    "start": "2026-09-21(월) 13:00 ~ 14:00",
    "from_applied": True,
}


def _stub_unopened(monkeypatch, verdict, buttons, running=False, probed=True):
    monkeypatch.setattr(seminar_survey, "open_survey", lambda page, sid: (None, "설문 참여 버튼이 없음(설문 미제공 또는 종료)."))
    monkeypatch.setattr(seminar_survey, "confirm_survey_done", lambda page, sid, retries=0: (verdict, buttons))
    monkeypatch.setattr(seminar_survey, "probe_saw_running_seminar", lambda: running)
    monkeypatch.setattr(seminar_survey, "probe_saw_ended_seminar", lambda: False)
    monkeypatch.setattr(seminar_survey, "probe_read_detail_page", lambda: probed)


# 시각은 20:06(실측) 대신 14:40으로 잡는다. `survey_window` 수정(공지 종료 +
# 관측 오차 + 창)이 들어간 뒤로 20:06은 `closed`로 먼저 끝나 아래 판정에
# 닿지 않는다. 20:06의 `closed`는 위 `test_unobserved_end_closes_...`가 덮고,
# 여기서는 창이 실제로 열려 있는 시각의 상세 판정을 본다.


def test_empty_detail_on_applied_candidate_is_no_target(monkeypatch):
    """5643 재현 — 빈 상세를 `unverified`로 읽어 세션을 깨우던 자리."""
    _stub_unopened(monkeypatch, "unknown", EMPTY_DETAIL_BUTTONS)

    result = seminar_survey.run_survey(
        MagicMock(), ITEM_5643, now_dt=datetime(2026, 9, 21, 14, 40, tzinfo=KST)
    )

    assert result["status"] == "no_target"
    assert result["detail_verdict"] == "unknown"
    assert result["detail_buttons"] == EMPTY_DETAIL_BUTTONS


def test_empty_detail_still_alerts_when_the_page_was_never_read(monkeypatch):
    """접속 실패의 침묵까지 덮으면 장애가 묻힌다 — 상세를 펼쳐 봤을 때만 quiet."""
    _stub_unopened(
        monkeypatch, "unknown", ["www 상세 재접속 실패: Timeout 30000ms exceeded"], probed=False
    )

    result = seminar_survey.run_survey(
        MagicMock(), ITEM_5643, now_dt=datetime(2026, 9, 21, 14, 40, tzinfo=KST)
    )

    assert result["status"] == "unverified"


def test_empty_detail_before_the_announced_end_is_not_no_target(monkeypatch):
    """방송 전·중의 빈 상세는 '대상 아님'이 아니라 '아직'이다."""
    _stub_unopened(monkeypatch, "unknown", EMPTY_DETAIL_BUTTONS)

    result = seminar_survey.run_survey(
        MagicMock(), ITEM_5643, now_dt=datetime(2026, 9, 21, 13, 20, tzinfo=KST)
    )

    assert result["status"] == "not_ready"


def test_empty_detail_on_entered_seminar_still_alerts(monkeypatch):
    """입장한 세미나의 설문이 안 열리는 것은 실패다 — 여기선 덮지 않는다."""
    _stub_unopened(monkeypatch, "unknown", EMPTY_DETAIL_BUTTONS)

    result = seminar_survey.run_survey(
        MagicMock(),
        {**ITEM_5643, "from_applied": False, "entered_at": "2026-09-21T13:05:00+09:00"},
        now_dt=datetime(2026, 9, 21, 14, 40, tzinfo=KST),
    )

    assert result["status"] == "unverified"


def test_open_survey_button_on_applied_candidate_still_alerts(monkeypatch):
    """'설문하기'가 보이는데 못 열었으면 그건 실패다(기존 규칙 유지)."""
    _stub_unopened(monkeypatch, "not_done", ["세미나 종료", "설문하기"])

    result = seminar_survey.run_survey(
        MagicMock(), ITEM_5643, now_dt=datetime(2026, 9, 21, 15, 0, tzinfo=KST)
    )

    assert result["status"] == "unverified"
