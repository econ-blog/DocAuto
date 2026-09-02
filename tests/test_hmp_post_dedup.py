from hmp import kst_today_dot, parse_post_dates


def test_parse_post_dates_picks_only_date_cells():
    cells = ["\n\t2026.08.03\n", "0", "여행/취미", " 2026.07.31 ", "", "3"]
    assert parse_post_dates(cells) == ["2026.08.03", "2026.07.31"]


def test_parse_post_dates_rejects_near_misses():
    assert parse_post_dates(["2026.8.3", "26.08.03", "2026-08-03", "2026.08.03 등록"]) == []


def test_parse_post_dates_empty():
    assert parse_post_dates([]) == []


def test_kst_today_dot_format():
    import re

    assert re.fullmatch(r"\d{4}\.\d{2}\.\d{2}", kst_today_dot())


def test_broken_selector_node_alerts_and_fails_run():
    """목록 셀렉터가 깨져 중복 판정을 못 하면 알림(alert) + exit 1이 나와야 한다."""
    from daily_runner import evaluate_exit_code
    from notify import severity_of

    results = {
        "hmp": {
            "site": "hmp",
            "status": "already_done",
            "verified_by": "evidence",
            "post": {"status": "success", "verified_by": "rtn_code_100"},
            "post_precheck": {
                "status": "unverified",
                "message": "글쓰기 중복 판정 실패 — 등록일자 셀을 찾지 못함.",
            },
        }
    }
    assert severity_of(results) == "alert"
    assert evaluate_exit_code(results) == 1


def test_successful_precheck_stays_quiet():
    from daily_runner import evaluate_exit_code
    from notify import severity_of

    results = {
        "hmp": {
            "site": "hmp",
            "status": "already_done",
            "verified_by": "evidence",
            "post": {"status": "already_done", "verified_by": "my_post_list_date_match"},
        }
    }
    assert severity_of(results) == "quiet"
    assert evaluate_exit_code(results) == 0


def test_today_detection_matches_list_format():
    today = kst_today_dot()
    dates = parse_post_dates([today, "2026.07.31", today])
    assert dates.count(today) == 2
