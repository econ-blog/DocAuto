from seminar_live import load_state, save_state, update_entered_state, determine_block_name

def test_load_and_save_state(tmp_path):
    state_file = tmp_path / "seminar_entered.json"
    initial = {"date": "2026-07-25", "accounts": {"bjh7790": {
        "entered": [{"id": 5457, "title": None, "start": None, "entered_at": None}], "blocks": {"lunch": [5457], "evening": [], "manual": []}}}}
    save_state(initial, state_file)
    
    loaded = load_state(state_file, "2026-07-25")
    assert loaded["accounts"]["bjh7790"]["entered"] == [{"id": 5457, "title": None, "start": None, "entered_at": None}]

def test_update_entered_state(tmp_path):
    state_file = tmp_path / "seminar_entered.json"
    state = load_state(state_file, "2026-07-25")
    
    update_entered_state(state, "bjh7790", 5460, "lunch", state_file)
    reloaded = load_state(state_file, "2026-07-25")
    assert any(e.get("id") == 5460 for e in reloaded["accounts"]["bjh7790"]["entered"])
    assert 5460 in reloaded["accounts"]["bjh7790"]["blocks"]["lunch"]

def test_determine_block_name():
    assert determine_block_name("lunch") == "lunch"
    assert determine_block_name("evening") == "evening"
    assert determine_block_name("manual") == "manual"
    assert determine_block_name("auto") in ["lunch", "evening"]


def test_is_enter_window():
    from datetime import datetime
    from common import KST
    from seminar_live import is_enter_window

    # Seminar running 13:00 ~ 14:00. Allowed window: 12:00 ~ 14:00
    start_str = "2026-08-10(월) 13:00 ~ 14:00"

    # 11:30 KST (< 12:00) -> False (too early)
    too_early = datetime(2026, 8, 10, 11, 30, tzinfo=KST)
    can_enter, reason, _, _ = is_enter_window(start_str, too_early)
    assert can_enter is False
    assert "입장 가능 시간 전" in reason

    # 12:00 KST (boundary) -> True
    boundary_start = datetime(2026, 8, 10, 12, 0, tzinfo=KST)
    can_enter, _, _, _ = is_enter_window(start_str, boundary_start)
    assert can_enter is True

    # 13:30 KST (during broadcast) -> True
    during = datetime(2026, 8, 10, 13, 30, tzinfo=KST)
    can_enter, _, _, _ = is_enter_window(start_str, during)
    assert can_enter is True

    # 14:00 KST (boundary end) -> True
    boundary_end = datetime(2026, 8, 10, 14, 0, tzinfo=KST)
    can_enter, _, _, _ = is_enter_window(start_str, boundary_end)
    assert can_enter is True

    # 14:01 KST (> 14:00) -> False (ended)
    after_end = datetime(2026, 8, 10, 14, 1, tzinfo=KST)
    can_enter, reason, _, _ = is_enter_window(start_str, after_end)
    assert can_enter is False
    assert "세미나 종료" in reason


