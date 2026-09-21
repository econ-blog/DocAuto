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
