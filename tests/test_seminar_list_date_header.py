"""방송 날짜는 앵커 밖 날짜별 헤더에 있다 (2026-09-16 R5 정찰).

목록은 평평한 형제 나열이다:
    [div.seminar_day 9/16][a.list_detail]...[div.seminar_day 9/17][a.list_detail]...

앵커 innerText에는 시각만 있어, 2026-09-16 daily 런은 66건 중 66건이
일시 파싱에 실패하고 두 계정 모두 unverified로 끝났다. SEMINAR_LIST_JS가
헤더를 찾아 raw 앞에 붙인다.

날짜는 모두 주입한 now 기준으로 판정한다 — 하드코딩한 오늘 날짜로
테스트를 쓰면 다음 날 터진다(2026-09-16 00:15 런 전면 중단의 원인).
"""

from datetime import datetime

import doctorville
from common import KST

NOW = datetime(2026, 9, 16, 0, 18, tzinfo=KST)

# 실제 목록 텍스트(R5 정찰 채취) — 헤더 + 앵커 innerText 결합 형태.
REAL = "9/16 수요일 18:30 ~20:00 호흡기질환 Breathe Well Symposium #좌장: 박성훈 교수(한림의대) 신청완료 6324명 /7000명"
REAL_TOMORROW = "9/17 목요일 19:00 ~20:30 대한내분비학회 제4회 ENstagram Webinar 신청완료 6505명 /7000명"
NO_HEADER = "18:30 ~20:00 호흡기질환 Breathe Well Symposium 신청완료 6324명 /7000명"


# --- JS 배선 ----------------------------------------------------------------

def test_list_js_walks_previous_siblings_for_day_header():
    js = doctorville.SEMINAR_LIST_JS
    assert ".seminar_day" in js
    assert "previousElementSibling" in js
    # 헤더가 조상이 아니라 앞 형제다 — closest()로는 못 찾는다.
    assert "listDate" in js


def test_list_js_is_raw_string():
    """일반 문자열이면 \\s가 파이썬 단계에서 깨져 JS 정규식이 무력화된다."""
    assert r"\s+" in doctorville.SEMINAR_LIST_JS


def test_list_js_slices_after_prepending_date():
    """200자 자르기를 날짜 결합보다 먼저 하면 긴 제목에서 날짜가 잘려 나간다."""
    js = doctorville.SEMINAR_LIST_JS
    prepend = js.index("listDate ? listDate + ' ' + body")
    assert js.index(".slice(0, 200)", prepend) > prepend


# --- 파싱 -------------------------------------------------------------------

def test_real_list_text_parses_with_header():
    assert doctorville.parse_list_datetime(REAL, NOW) == "2026-09-16(수) 18:30 ~ 20:00"


def test_slash_format_month_day_is_covered_by_existing_regex():
    """`9/16`은 기존 _LIST_SHORT_DATE_RE가 이미 읽는다 — 정규식은 손대지 않았다."""
    assert doctorville.parse_list_datetime("10/1 목요일 13:00 ~14:00 내분비질환", NOW) \
        == "2026-10-01(목) 13:00 ~ 14:00"


def test_capacity_text_is_not_mistaken_for_a_date():
    """'6324명 /7000명'의 슬래시가 날짜로 잡히면 엉뚱한 날에 행이 생긴다."""
    assert doctorville.parse_list_datetime(NO_HEADER, NOW) == ""


def test_missing_header_stays_fail_closed():
    """헤더를 못 찾으면 행을 만들지 않고 실패로 센다(없는 날짜를 지어내지 않는다)."""
    rows, unparsed = doctorville.list_rows_for_today(
        [{"id": "5685", "title": "A", "raw": NO_HEADER}], NOW)
    assert rows == []
    assert unparsed == 1


def test_only_todays_rows_are_kept():
    rows, unparsed = doctorville.list_rows_for_today([
        {"id": "5685", "title": "오늘", "raw": REAL},
        {"id": "5734", "title": "내일", "raw": REAL_TOMORROW},
    ], NOW)
    assert unparsed == 0
    assert [r["id"] for r in rows] == ["5685"]
    assert rows[0]["start"] == "2026-09-16(수) 18:30 ~ 20:00"
