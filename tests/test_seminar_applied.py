import json
from datetime import datetime

from common import KST
from doctorville import (
    applied_ids,
    filter_new_seminars,
    load_applied,
    prune_applied,
    prune_applied_file,
    record_applied,
    save_applied,
)


def _entry(start=None, applied_at="2026-08-01T10:00:00+09:00"):
    e = {"applied_at": applied_at}
    if start:
        e["start"] = start
    return e


# --- 목록 필터링 ------------------------------------------------------------

def test_filter_new_seminars_skips_recorded():
    data = {"bjh7790": {"5533": _entry()}}
    assert filter_new_seminars(["5533", "5540"], data, "bjh7790") == ["5540"]


def test_filter_new_seminars_is_per_account():
    data = {"bjh7790": {"5533": _entry()}}
    assert filter_new_seminars(["5533"], data, "wonju") == ["5533"]


def test_filter_new_seminars_dedupes_list():
    assert filter_new_seminars(["5533", "5533"], {}, "bjh7790") == ["5533"]


def test_filter_new_seminars_matches_int_and_str_ids():
    data = {"bjh7790": {"5533": _entry()}}
    assert filter_new_seminars([5533, 5540], data, "bjh7790") == [5540]


def test_applied_ids_on_empty_file():
    assert applied_ids({}, "bjh7790") == set()


def test_record_applied_keys_by_string():
    data = record_applied({}, "bjh7790", 5533, "제목", "2026-08-10(월) 13:00 ~ 14:00")
    assert list(data["bjh7790"]) == ["5533"]
    assert data["bjh7790"]["5533"]["title"] == "제목"
    assert "applied_at" in data["bjh7790"]["5533"]


def test_record_applied_numeric_fields():
    data = record_applied({}, "bjh7790", 5533, "당뇨병 최신 치료", "2026-08-20(목) 13:30 ~ 15:00")
    entry = data["bjh7790"]["5533"]
    assert entry["title"] == "당뇨병 최신 치료"
    assert entry["start"] == "2026-08-20(목) 13:30 ~ 15:00"
    assert entry["date"] == "2026-08-20"
    assert entry["start_date"] == "2026-08-20"
    assert entry["year"] == 2026
    assert entry["month"] == 8
    assert entry["day"] == 20
    assert entry["start_time"] == "13:30"
    assert entry["start_hour"] == 13
    assert entry["start_minute"] == 30
    assert entry["end_time"] == "15:00"
    assert entry["end_hour"] == 15
    assert entry["end_minute"] == 0


# --- 날짜 지난 항목 정리 ----------------------------------------------------

def test_prune_drops_seminar_whose_broadcast_ended():
    data = {"bjh7790": {"5533": _entry(start="2026-08-10(월) 13:00 ~ 14:00")}}
    pruned, counts = prune_applied(data, now=datetime(2026, 8, 11, 9, 0, tzinfo=KST))
    assert pruned == {}
    assert counts == {"past": 1}


def test_prune_keeps_seminar_still_upcoming():
    data = {"bjh7790": {"5533": _entry(start="2026-08-20(목) 13:00 ~ 14:00")}}
    pruned, counts = prune_applied(data, now=datetime(2026, 8, 12, 9, 0, tzinfo=KST))
    assert list(pruned["bjh7790"]) == ["5533"]
    assert counts == {}


def test_prune_keeps_seminar_during_broadcast():
    data = {"bjh7790": {"5533": _entry(start="2026-08-12(수) 13:00 ~ 14:00")}}
    pruned, _ = prune_applied(data, now=datetime(2026, 8, 12, 13, 30, tzinfo=KST))
    assert list(pruned["bjh7790"]) == ["5533"]


def test_prune_falls_back_to_applied_at_without_start():
    data = {"bjh7790": {"5533": _entry(applied_at="2026-01-01T10:00:00+09:00")}}
    pruned, counts = prune_applied(data, days=60, now=datetime(2026, 8, 12, tzinfo=KST))
    assert pruned == {}
    assert counts == {"stale": 1}


def test_prune_falls_back_when_start_is_unparseable():
    data = {"bjh7790": {"5533": _entry(start="일시 미정", applied_at="2026-08-11T10:00:00+09:00")}}
    pruned, counts = prune_applied(data, days=60, now=datetime(2026, 8, 12, tzinfo=KST))
    assert list(pruned["bjh7790"]) == ["5533"]
    assert counts == {}


def test_prune_keeps_entry_with_no_usable_dates():
    data = {"bjh7790": {"5533": {}}}
    pruned, counts = prune_applied(data, now=datetime(2026, 8, 12, tzinfo=KST))
    assert list(pruned["bjh7790"]) == ["5533"]
    assert counts == {}


def test_prune_drops_account_that_becomes_empty():
    data = {
        "bjh7790": {"5533": _entry(start="2026-08-10(월) 13:00 ~ 14:00")},
        "wonju": {"5540": _entry(start="2026-08-20(목) 13:00 ~ 14:00")},
    }
    pruned, _ = prune_applied(data, now=datetime(2026, 8, 12, tzinfo=KST))
    assert list(pruned) == ["wonju"]


# --- 파일 입출력 ------------------------------------------------------------

def test_prune_applied_file_roundtrip(tmp_path):
    path = tmp_path / "seminar_applied.json"
    save_applied({
        "bjh7790": {
            "5533": _entry(start="2026-08-10(월) 13:00 ~ 14:00"),
            "5540": _entry(start="2026-08-20(목) 13:00 ~ 14:00"),
        }
    }, path)

    res = prune_applied_file(path, now=datetime(2026, 8, 12, tzinfo=KST))
    assert res["removed"] == 1
    assert res["remaining"] == 1
    assert list(load_applied(path)["bjh7790"]) == ["5540"]
    assert path.read_text(encoding="utf-8").endswith("\n")
    # success에는 반드시 양성 증거가 붙어야 unverified로 강등되지 않는다.
    assert res["status"] == "success"
    assert res["verified_by"]


def test_prune_applied_file_does_not_rewrite_when_nothing_expires(tmp_path):
    path = tmp_path / "seminar_applied.json"
    path.write_text(json.dumps({"bjh7790": {"5540": _entry(start="2026-08-20(목) 13:00 ~ 14:00")}}), encoding="utf-8")
    before = path.read_text(encoding="utf-8")

    res = prune_applied_file(path, now=datetime(2026, 8, 12, tzinfo=KST))
    assert res["removed"] == 0
    assert path.read_text(encoding="utf-8") == before
    # 지울 게 없으면 success가 아니라 quiet 상태여야 한다(증거 없는 success 금지).
    assert res["status"] == "skipped"


def test_load_applied_on_missing_file(tmp_path):
    assert load_applied(tmp_path / "nope.json") == {}


def test_load_applied_on_corrupt_file(tmp_path):
    path = tmp_path / "seminar_applied.json"
    path.write_text("{ broken", encoding="utf-8")
    assert load_applied(path) == {}


def test_seminar_applied_module_direct_imports():
    import seminar_applied
    import doctorville
    assert seminar_applied.load_applied is doctorville.load_applied
    assert seminar_applied.save_applied is doctorville.save_applied
    assert seminar_applied.applied_ids is doctorville.applied_ids
    assert seminar_applied.filter_new_seminars is doctorville.filter_new_seminars
    assert seminar_applied.record_applied is doctorville.record_applied
    assert seminar_applied.prune_applied is doctorville.prune_applied
    assert seminar_applied.prune_applied_file is doctorville.prune_applied_file
    assert seminar_applied.SEMINAR_APPLIED_PATH == doctorville.SEMINAR_APPLIED_PATH

