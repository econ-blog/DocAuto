import json
from datetime import datetime
from common import KST
from seminar_live import parse_dd_date, upgrade_to_v2, load_state, update_entered_state

def test_parse_dd_date_valid():
    start_dt, end_dt = parse_dd_date("2026-08-10(월) 13:00 ~ 14:00")
    assert start_dt == datetime(2026, 8, 10, 13, 0, tzinfo=KST)
    assert end_dt == datetime(2026, 8, 10, 14, 0, tzinfo=KST)

def test_parse_dd_date_invalid():
    assert parse_dd_date(None) == (None, None)
    assert parse_dd_date("invalid") == (None, None)

def test_upgrade_to_v2():
    v1_dict = {
        "date": "2026-07-31",
        "accounts": {
            "bjh7790": {
                "entered": [5473],
                "blocks": {"lunch": [5473]},
                "survey_done": [5473]
            }
        }
    }
    v2 = upgrade_to_v2(v1_dict)
    assert v2["version"] == 2
    assert v2["accounts"]["bjh7790"]["entered"] == [
        {"id": 5473, "title": None, "start": None, "entered_at": None}
    ]
    assert v2["accounts"]["bjh7790"]["survey"] == {"5473": "done"}

def test_load_state_v1_file(tmp_path):
    tmp_file = tmp_path / "seminar_entered.json"
    v1_data = {
        "date": "2026-08-01",
        "accounts": {
            "bjh7790": {
                "entered": [100],
                "survey_done": [100]
            }
        }
    }
    tmp_file.write_text(json.dumps(v1_data), encoding="utf-8")
    loaded = load_state(tmp_file, "2026-08-01")
    assert loaded["version"] == 2
    assert loaded["accounts"]["bjh7790"]["entered"] == [
        {"id": 100, "title": None, "start": None, "entered_at": None}
    ]
    assert loaded["accounts"]["bjh7790"]["survey"] == {"100": "done"}

def test_update_entered_state_with_metadata(tmp_path):
    state_file = tmp_path / "seminar_entered.json"
    state_file.write_text('{"version":2,"date":"2026-08-01","accounts":{"bjh7790":{"entered":[],"survey":{}}}}', encoding="utf-8")

    state = load_state(state_file, "2026-08-01")
    update_entered_state(
        state, "bjh7790", 5473, "lunch", state_file,
        title="호흡기 심포지엄", start="2026-08-10(월) 13:00 ~ 14:00", entered_at="2026-08-10T13:05:00+09:00"
    )
    loaded = load_state(state_file, "2026-08-01")
    entered_item = loaded["accounts"]["bjh7790"]["entered"][0]
    assert entered_item["id"] == 5473
    assert entered_item["title"] == "호흡기 심포지엄"
    assert entered_item["start"] == "2026-08-10(월) 13:00 ~ 14:00"
    assert entered_item["entered_at"] == "2026-08-10T13:05:00+09:00"

def test_task_live_seminar_no_target(monkeypatch):
    from unittest.mock import MagicMock
    from seminar_live import task_live_seminar
    mock_page = MagicMock()
    monkeypatch.setattr("seminar_live.get_live_seminar_info", lambda p: [], raising=False)
    monkeypatch.setattr("seminar_live.get_live_seminar_ids", lambda p: [], raising=False)

    res = task_live_seminar(mock_page, stay_seconds=20, account="bjh7790")
    assert res["status"] == "no_target"


def _live_page(monkeypatch, items):
    from unittest.mock import MagicMock
    monkeypatch.setattr("seminar_live.get_live_seminar_info", lambda p: items, raising=False)
    return MagicMock()


def test_task_live_seminar_already_entered_marks_evidence_as_cache(monkeypatch):
    """이력 기반 already_done은 서버 증거를 참칭하면 안 된다.

    2026-09-02: already_done도 verified_by가 없으면 unverified로 강등되도록 규칙이
    바뀌었다. 이 판정의 근거는 서버가 아니라 로컬 상태(seminar_entered.json)이므로
    `cache:` 접두사로 남겨, 표·로그에서 서버 증거(popup_acquired 등)와 구분된다.
    """
    from seminar_live import task_live_seminar
    page = _live_page(monkeypatch, [{"id": 5473, "title": "심포지엄"}])
    state = {"date": "2026-08-02", "accounts": {"bjh7790": {"entered": [{"id": 5473}]}}}

    res = task_live_seminar(page, stay_seconds=20, account="bjh7790", state=state)
    assert res["status"] == "already_done"
    assert res["verified_by"].startswith("cache:")
    assert "popup" not in res["verified_by"], "서버 증거를 참칭하면 안 된다"
    assert res["already_entered"] == [5473]
    assert res["entered"] == []


def test_task_live_seminar_dry_run_has_no_false_evidence(monkeypatch):
    from seminar_live import task_live_seminar
    page = _live_page(monkeypatch, [{"id": 5473, "title": "심포지엄"}])

    res = task_live_seminar(page, stay_seconds=20, account="bjh7790", dry_run=True)
    assert res["status"] == "skipped"
    assert "verified_by" not in res
    assert res["skipped"] == [5473]


def test_task_live_seminar_actual_entry_keeps_evidence(monkeypatch):
    from seminar_live import task_live_seminar
    page = _live_page(monkeypatch, [{"id": 5473, "title": "심포지엄"}])
    monkeypatch.setattr(
        "seminar_live.enter_and_wait",
        lambda p, sid, secs: {"status": "success", "verified_by": "popup_acquired"},
        raising=False,
    )

    res = task_live_seminar(page, stay_seconds=20, account="bjh7790")
    assert res["status"] == "success"
    assert res["verified_by"] == "popup_acquired"
    assert res["entered"] == [5473]

