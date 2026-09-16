from doctorville import legacy_to_choice_indices, parse_wrong_numbers
from telegram_inbox import parse_inbox_line
from seminar_live import merge_state

def test_legacy_to_choice_indices_valid():
    choices = [
        ["보기1", "보기2", "보기3"],
        ["O", "X"],
        ["1번", "2번", "3번", "4번"]
    ]
    # "1o4" -> index 0, index 0 ('O'), index 3
    result = legacy_to_choice_indices("1o4", choices)
    assert result == [0, 0, 3]

def test_legacy_to_choice_indices_invalid():
    choices = [["보기1", "보기2"], ["O", "X"]]
    # length mismatch
    assert legacy_to_choice_indices("1", choices) is None
    # index out of range
    assert legacy_to_choice_indices("3o", choices) is None
    # 'x' not in choices: "1x" with [["A", "B"], ["C", "D"]]
    assert legacy_to_choice_indices("1x", [["A", "B"], ["C", "D"]]) is None

def test_parse_wrong_numbers():
    assert parse_wrong_numbers("1, 3번 오답입니다.") == [1, 3]
    assert parse_wrong_numbers("0, 1, 3번 오답입니다.") == [1, 3]
    assert parse_wrong_numbers("2번 오답입니다.") == [2]
    assert parse_wrong_numbers("정답입니다") == []
    assert parse_wrong_numbers("축하드립니다") == []

def test_parse_inbox_line():
    assert parse_inbox_line("에빅사 ooo") == ("에빅사", "ooo")
    assert parse_inbox_line("프리스타일 리브레 111") == ("프리스타일 리브레", "111")
    assert parse_inbox_line("스피틴 OOX") == ("스피틴", "oox")
    assert parse_inbox_line("invalid_line") is None
    assert parse_inbox_line("제품명 12z") is None

def test_merge_state():
    old_state = {
        "date": "2026-07-24",
        "accounts": {"bjh7790": {"entered": [{"id": 123, "title": None, "start": None, "entered_at": None}], "blocks": {"lunch": [123], "evening": [], "manual": []}}}
    }
    # Date mismatch resets state
    merged = merge_state(old_state, "2026-07-25")
    assert merged["date"] == "2026-07-25"
    assert merged["accounts"]["bjh7790"]["entered"] == []

    # Same date retains state
    same_date = merge_state({"date": "2026-07-25", "accounts": {"bjh7790": {
        "entered": [{"id": 123, "title": None, "start": None, "entered_at": None}], "blocks": {"lunch": [123], "evening": [], "manual": []}}}}, "2026-07-25")
    assert same_date["accounts"]["bjh7790"]["entered"] == [{"id": 123, "title": None, "start": None, "entered_at": None}]
