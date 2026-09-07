"""목록 스캔에서 '오늘 방송분 전부'를 표 행으로 뽑는 경로.

2026-09-07 세미나 5657(New WAVE Webinar)이 정원 마감이라 목록에
`span.ico_apply`가 없었고, 그래서 신청 대상에도 상세 `closed` 경로에도 들지 못해
표에서 통째로 사라졌다. 행의 출처를 '신청 이력'에서 '목록'으로 넓힌 뒤의 회귀 테스트.
"""

from datetime import datetime

import pytest

from common import KST
from doctorville import list_rows_for_today, parse_list_datetime

NOW = datetime(2026, 9, 7, 0, 18, tzinfo=KST)


# --- 일시 파싱 --------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    # 상세 dd.date와 같은 형식이 목록에 그대로 있는 경우
    ("New WAVE Webinar 2026-09-07(월) 13:00 ~ 14:00", "2026-09-07(월) 13:00 ~ 14:00"),
    ("2026.09.07 13:00~14:00 New WAVE Webinar", "2026-09-07(월) 13:00 ~ 14:00"),
    # 연도 없는 축약 표기
    ("09.07 13:00~14:00 정원마감", "2026-09-07(월) 13:00 ~ 14:00"),
    ("9/7 13:00 ~ 14:00", "2026-09-07(월) 13:00 ~ 14:00"),
    ("9월 7일 13:00~14:00", "2026-09-07(월) 13:00 ~ 14:00"),
    # 끝 시각이 없으면 시작 시각까지만. 없는 값을 지어내지 않는다.
    ("09.07 13:00 시작", "2026-09-07(월) 13:00"),
])
def test_parse_list_datetime(raw, expected):
    assert parse_list_datetime(raw, NOW) == expected


@pytest.mark.parametrize("raw", [
    "", None, "제 13차 웹심포지엄", "13:00~14:00 날짜없음", "2026-02-30 13:00~14:00",
])
def test_parse_list_datetime_fails_closed(raw):
    """날짜나 시각을 못 찾으면 빈 문자열. 호출부는 행을 만들지 않는다."""
    assert parse_list_datetime(raw, NOW) == ""


def test_year_inference_crosses_new_year():
    """12월 말에 본 '01.03'은 내년, 1월 초에 본 '12.30'은 작년."""
    dec = datetime(2026, 12, 30, 23, 0, tzinfo=KST)
    assert parse_list_datetime("01.03 13:00~14:00", dec).startswith("2027-01-03")
    jan = datetime(2027, 1, 2, 0, 20, tzinfo=KST)
    assert parse_list_datetime("12.30 13:00~14:00", jan).startswith("2026-12-30")


# --- 행 구성 ----------------------------------------------------------------

def _item(sid, raw, title="", applicable=False):
    return {"id": sid, "title": title, "raw": raw, "applicable": applicable}


def test_rows_include_unapplicable_seminar():
    """정원 마감(applicable=False)이어도 오늘 방송분이면 행이 생긴다 — 5657 회귀."""
    listed = [
        _item("5657", "New WAVE Webinar 09.07 13:00~14:00 정원마감", "New WAVE Webinar"),
        _item("5613", "제미글로 09.07 12:30~13:30", "제미글로", applicable=True),
    ]
    rows, unparsed = list_rows_for_today(listed, NOW)
    assert [r["id"] for r in rows] == ["5657", "5613"]
    assert rows[0]["start"] == "2026-09-07(월) 13:00 ~ 14:00"
    assert unparsed == 0


def test_rows_exclude_other_days():
    listed = [
        _item("5657", "09.07 13:00~14:00"),
        _item("5597", "09.09 18:00~19:30"),
    ]
    rows, _ = list_rows_for_today(listed, NOW)
    assert [r["id"] for r in rows] == ["5657"]


def test_unparsed_items_make_no_rows():
    """파싱 실패는 fail-closed. 행을 만들면 수 주치 세미나가 오늘 표에 쏟아진다."""
    listed = [_item("1", "제목만 있음"), _item("2", "09.07 13:00~14:00")]
    rows, unparsed = list_rows_for_today(listed, NOW)
    assert [r["id"] for r in rows] == ["2"]
    assert unparsed == 1


def test_rows_dedupe_and_drop_junk_titles():
    listed = [
        _item("5657", "09.07 13:00~14:00", "New WAVE Webinar"),
        _item("5657", "09.07 13:00~14:00", "New WAVE Webinar"),
        _item("5613", "09.07 12:30~13:30", "엠서클 통합회원"),
    ]
    rows, _ = list_rows_for_today(listed, NOW)
    assert [r["id"] for r in rows] == ["5657", "5613"]
    assert rows[1]["title"] == ""  # clean_title이 사이트 공통 요소를 지운다


def test_non_dict_items_are_ignored():
    rows, unparsed = list_rows_for_today(["5657", None, _item("5613", "09.07 12:30~13:30")], NOW)
    assert [r["id"] for r in rows] == ["5613"]
    assert unparsed == 0
