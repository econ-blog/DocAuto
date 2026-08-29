"""runlog(실행 로그 적재·표 렌더링) 단위 테스트."""

import pytest

import runlog
import tablepng


# ---------------------------------------------------------------------------
# 파일 적재 / 보관 개수
# ---------------------------------------------------------------------------

def test_append_daily_run_numbers_rows_in_order(tmp_path):
    runlog.append_daily_run({"출석": "success"}, date_str="2026-08-27", at="00:15", log_dir=tmp_path)
    data = runlog.append_daily_run({"출석": "already_done"}, date_str="2026-08-27", at="16:00", log_dir=tmp_path)

    assert [r["run"] for r in data["runs"]] == [1, 2]
    assert data["runs"][0]["at"] == "00:15"
    assert data["runs"][1]["cells"]["출석"] == "already_done"


def test_daily_columns_accumulate_across_runs(tmp_path):
    """런마다 계정 구성이 달라져도 앞선 런의 컬럼이 사라지면 안 된다."""
    runlog.append_daily_run({"A": "success"}, date_str="2026-08-27", log_dir=tmp_path)
    data = runlog.append_daily_run({"B": "success"}, date_str="2026-08-27", log_dir=tmp_path)

    assert data["columns"] == ["A", "B"]
    headers, rows = runlog.daily_table("2026-08-27", log_dir=tmp_path)
    assert headers == ["", "A", "B"]
    # run1에는 B가 없으므로 빈 칸으로 채워진다.
    assert rows[0][2] == runlog.EMPTY_CELL
    assert rows[1][1] == runlog.EMPTY_CELL


def test_prune_keeps_only_latest_files(tmp_path):
    for day in range(1, 11):
        runlog.append_daily_run({"A": "success"}, date_str=f"2026-08-{day:02d}", log_dir=tmp_path)

    removed = runlog.prune(runlog.KIND_DAILY, keep=7, log_dir=tmp_path)

    remaining = sorted(p.name for p in tmp_path.glob("daily-*.json"))
    assert len(remaining) == 7
    assert remaining[0] == "daily-2026-08-04.json"   # 오래된 3개가 지워진다
    assert len(removed) == 3


def test_prune_does_not_touch_other_kind(tmp_path):
    for day in range(1, 11):
        runlog.append_daily_run({"A": "success"}, date_str=f"2026-08-{day:02d}", log_dir=tmp_path)
    runlog.update_seminar("1", phase="apply", status="success", date_str="2026-08-01", log_dir=tmp_path)

    runlog.prune(runlog.KIND_DAILY, keep=7, log_dir=tmp_path)

    assert (tmp_path / "seminar-2026-08-01.json").exists()


# ---------------------------------------------------------------------------
# 상태 판정
# ---------------------------------------------------------------------------

def test_status_of_downgrades_success_without_evidence():
    """verified_by 없는 success는 unverified — notify.severity_of와 같은 규칙."""
    assert runlog.status_of({"status": "success", "verified_by": "x"}) == "success"
    assert runlog.status_of({"status": "success"}) == "unverified"
    assert runlog.status_of({"status": "failed"}) == "failed"
    assert runlog.status_of(None) == ""
    assert runlog.status_of({}) == ""


def test_merge_status_picks_the_worst():
    assert runlog.merge_status(["success", "failed"]) == "failed"
    assert runlog.merge_status(["success", "already_done"]) == "already_done"
    assert runlog.merge_status(["no_answer", "not_ready"]) == "no_answer"
    assert runlog.merge_status([]) == ""
    assert runlog.merge_status(["", "success"]) == "success"


def test_emoji_falls_back_for_unknown_status():
    assert runlog.emoji("success") == "✅"
    assert runlog.emoji("") == runlog.EMPTY_CELL
    assert runlog.emoji("some_new_status") == "❔"


# ---------------------------------------------------------------------------
# daily 결과 → 셀 평탄화
# ---------------------------------------------------------------------------

def test_daily_cells_flattens_runner_results():
    results = {
        "doctorville_bjh7790": {
            "attend": {"status": "success", "verified_by": "x"},
            "quiz": {"status": "no_answer"},
            "seminar": {"status": "skipped"},
        },
        "keymedi": {"status": "already_done"},
        "hmp": {
            "status": "success", "verified_by": "popup",
            "roulette": [{"status": "success", "verified_by": "y"}],
            "comment": {"status": "success", "verified_by": "z"},
            "post": {"status": "already_done"},
        },
        "precheck_quiz": {"precheck_quiz": {"status": "no_answer", "product": "징코샷"}},
    }
    cells = runlog.daily_cells(results, creds={"bjh7790": {"label": "승진"}})

    assert cells["출석\n승진"] == "success"
    assert cells["퀴즈\n승진"] == "no_answer"
    assert "세미나\n승진" not in cells      # skipped는 컬럼을 만들지 않는다
    assert cells["키메디\n출석"] == "already_done"
    assert cells["HMP\n캡슐"] == "success"
    assert cells["HMP\n룰렛"] == "success"
    assert cells["HMP\n글쓰기"] == "already_done"
    assert cells["익일\n퀴즈"] == "no_answer"


def test_daily_cells_surfaces_post_precheck_alert():
    """글쓰기 자체는 성공이어도 중복 판정 실패(unverified)가 칸에 드러나야 한다."""
    results = {"hmp": {
        "status": "success", "verified_by": "popup",
        "post": {"status": "success", "verified_by": "p"},
        "post_precheck": {"status": "unverified"},
    }}
    assert runlog.daily_cells(results)["HMP\n글쓰기"] == "unverified"


def test_daily_cells_ignores_unknown_keys():
    assert runlog.daily_cells({"seminar_applied_prune": {"status": "success"}}) == {}


# ---------------------------------------------------------------------------
# 세미나 표
# ---------------------------------------------------------------------------

def test_split_times_parses_doctorville_dd_date():
    assert runlog.split_times("2026-08-26(수) 17:00 ~ 18:30") == ("17:00", "18:30")
    assert runlog.split_times("17:00") == ("17:00", "")
    assert runlog.split_times("") == ("", "")
    assert runlog.split_times(None) == ("", "")


def test_update_seminar_fills_one_cell_per_phase(tmp_path):
    kw = {"date_str": "2026-08-27", "log_dir": tmp_path}
    runlog.update_seminar("5587", phase="apply", status="success", account="bjh7790",
                          title="당뇨 세미나", start="2026-08-27(목) 12:00 ~ 13:00", **kw)
    runlog.update_seminar("5587", phase="live", status="success", account="bjh7790", **kw)
    runlog.update_seminar("5587", phase="survey", status="not_ready", account="bjh7790", **kw)

    headers, rows = runlog.seminar_table("2026-08-27", log_dir=tmp_path,
                                         labels={"bjh7790": "승진"})
    assert headers == ["세미나", "시작", "종료", "신청\n승진", "입장\n승진", "설문\n승진"]
    assert rows == [["당뇨 세미나", "12:00", "13:00", "✅", "✅", "⏳"]]


def test_already_done_does_not_overwrite_a_recorded_result(tmp_path):
    """한 번 초록(✅)이면 계속 초록. 30분 뒤 런의 '이미 완료'가 덮으면 안 된다.

    세미나 블록은 같은 세미나를 30분마다 다시 훑는다. 첫 런에서 실제로 신청·입장에
    성공(✅)한 뒤 다음 런은 이력을 보고 already_done(☑️)을 적어, 표가 성공을
    잃어버리는 문제가 있었다(2026-08-28 사용자 지적).
    """
    kw = {"date_str": "2026-08-27", "log_dir": tmp_path}
    runlog.update_seminar("1", phase="live", status="success", account="bjh7790",
                          title="T", **kw)
    runlog.update_seminar("1", phase="live", status="already_done", account="bjh7790", **kw)

    rows = runlog.seminar_table("2026-08-27", log_dir=tmp_path, accounts=["bjh7790"])[1]
    assert rows[0][4] == "✅"


def test_already_done_does_not_overwrite_a_failure_either(tmp_path):
    """'이미 완료'는 새 결과가 아니라 옛 결과의 재확인이라 아무것도 바꾸지 않는다."""
    kw = {"date_str": "2026-08-27", "log_dir": tmp_path}
    runlog.update_seminar("1", phase="apply", status="failed", account="bjh7790",
                          title="T", **kw)
    runlog.update_seminar("1", phase="apply", status="already_done", account="bjh7790", **kw)

    rows = runlog.seminar_table("2026-08-27", log_dir=tmp_path, accounts=["bjh7790"])[1]
    assert rows[0][3] == "❌"


def test_already_done_still_fills_an_empty_cell(tmp_path):
    """어제 신청해둔 세미나는 오늘 첫 기록이 already_done이다 — 빈 칸은 채워야 한다."""
    kw = {"date_str": "2026-08-27", "log_dir": tmp_path}
    runlog.update_seminar("1", phase="apply", status="already_done", account="bjh7790",
                          title="T", **kw)

    rows = runlog.seminar_table("2026-08-27", log_dir=tmp_path, accounts=["bjh7790"])[1]
    assert rows[0][3] == "☑️"


def test_already_done_is_per_account(tmp_path):
    """한 계정의 재확인이 다른 계정 칸을 막으면 안 된다."""
    kw = {"date_str": "2026-08-27", "log_dir": tmp_path}
    runlog.update_seminar("1", phase="live", status="success", account="bjh7790",
                          title="T", **kw)
    runlog.update_seminar("1", phase="live", status="already_done", account="wonju", **kw)

    rows = runlog.seminar_table("2026-08-27", log_dir=tmp_path,
                                accounts=["bjh7790", "wonju"])[1]
    assert rows[0][5:7] == ["✅", "☑️"]


def test_other_statuses_still_overwrite(tmp_path):
    """정답을 채워 넣어 ❓가 ✅로 바뀌는 정상 갱신은 그대로 동작해야 한다."""
    kw = {"date_str": "2026-08-27", "log_dir": tmp_path}
    runlog.update_seminar("1", phase="survey", status="incomplete_bank", account="bjh7790",
                          title="T", **kw)
    runlog.update_seminar("1", phase="survey", status="success", account="bjh7790", **kw)

    rows = runlog.seminar_table("2026-08-27", log_dir=tmp_path, accounts=["bjh7790"])[1]
    assert rows[0][5] == "✅"


def test_update_seminar_does_not_erase_metadata(tmp_path):
    """뒤 단계가 title/start를 모른 채 호출해도 앞 단계가 채운 값이 남아야 한다."""
    kw = {"date_str": "2026-08-27", "log_dir": tmp_path}
    runlog.update_seminar("1", phase="apply", status="success", title="원제목",
                          start="2026-08-27(목) 12:00 ~ 13:00", **kw)
    runlog.update_seminar("1", phase="live", status="success", title="", start="", **kw)

    rows = runlog.seminar_table("2026-08-27", log_dir=tmp_path)[1]
    assert rows[0][0] == "원제목"
    assert rows[0][1] == "12:00"


def test_seminar_columns_split_by_account(tmp_path):
    """계정을 한 칸에 합치면 ❌가 떠도 어느 계정인지 알 수 없다 (2026-08-28)."""
    kw = {"date_str": "2026-08-27", "log_dir": tmp_path}
    runlog.update_seminar("1", phase="live", status="success", account="bjh7790", title="T", **kw)
    runlog.update_seminar("1", phase="live", status="failed", account="wonju", **kw)

    headers, rows = runlog.seminar_table("2026-08-27", log_dir=tmp_path,
                                         accounts=["bjh7790", "wonju"],
                                         labels={"bjh7790": "승진", "wonju": "원주"})

    assert headers == ["세미나", "시작", "종료",
                       "신청\n승진", "신청\n원주",
                       "입장\n승진", "입장\n원주",
                       "설문\n승진", "설문\n원주"]
    # 입장: 승진 성공 / 원주 실패가 각자 자기 칸에 보인다.
    assert rows[0][5:7] == ["✅", "❌"]
    assert rows[0][3:5] == [runlog.EMPTY_CELL, runlog.EMPTY_CELL]


def test_seminar_account_columns_follow_credentials_order(tmp_path):
    """컬럼 순서는 credentials 순서를 따르고, 로그에만 있는 계정은 뒤에 붙는다."""
    kw = {"date_str": "2026-08-27", "log_dir": tmp_path}
    runlog.update_seminar("1", phase="apply", status="success", account="zzz", title="T", **kw)
    runlog.update_seminar("1", phase="apply", status="success", account="wonju", **kw)

    headers = runlog.seminar_table("2026-08-27", log_dir=tmp_path,
                                   accounts=["bjh7790", "wonju"])[0]
    assert headers[3:6] == ["신청\nbjh7790", "신청\nwonju", "신청\nzzz"]


def test_seminar_table_falls_back_to_merged_column_without_accounts(tmp_path):
    """계정 없이 적힌 옛 로그도 그대로 읽혀야 한다 — 단계당 한 칸으로 합친다."""
    kw = {"date_str": "2026-08-27", "log_dir": tmp_path}
    runlog.update_seminar("1", phase="live", status="failed", title="T", **kw)

    headers, rows = runlog.seminar_table("2026-08-27", log_dir=tmp_path)
    assert headers == ["세미나", "시작", "종료", "신청", "입장", "설문"]
    assert rows[0][4] == "❌"


def test_accountless_log_shows_in_every_account_column(tmp_path):
    """어느 계정 것인지 모르는 기록은 감추지 않고 모든 계정 칸에 비친다."""
    kw = {"date_str": "2026-08-27", "log_dir": tmp_path}
    runlog.update_seminar("1", phase="live", status="failed", title="T", **kw)
    runlog.update_seminar("1", phase="apply", status="success", account="bjh7790", **kw)

    rows = runlog.seminar_table("2026-08-27", log_dir=tmp_path,
                                accounts=["bjh7790", "wonju"])[1]
    assert rows[0][5:7] == ["❌", "❌"]


def test_seminar_rows_include_applied_but_not_yet_entered(tmp_path):
    """신청만 해두고 입장 시간이 안 된 세미나도 '그날 예정'이라 표에 있어야 한다."""
    applied = {"bjh7790": {"5590": {
        "title": "저녁 세미나", "start": "2026-08-27(목) 19:00 ~ 20:00",
        "start_date": "2026-08-27", "start_time": "19:00", "end_time": "20:00",
    }}}
    headers, rows = runlog.seminar_table("2026-08-27", log_dir=tmp_path, applied=applied)

    assert headers[3] == "신청\nbjh7790"
    assert rows == [["저녁 세미나", "19:00", "20:00", "☑️", runlog.EMPTY_CELL, runlog.EMPTY_CELL]]


def test_seminar_rows_exclude_other_days(tmp_path):
    applied = {"bjh7790": {
        "1": {"title": "오늘", "start_date": "2026-08-27", "start_time": "19:00"},
        "2": {"title": "내일", "start_date": "2026-08-28", "start_time": "19:00"},
    }}
    runlog.update_seminar("3", phase="live", status="success", title="지난주",
                          start="2026-08-20(목) 12:00 ~ 13:00",
                          date_str="2026-08-27", log_dir=tmp_path)

    titles = [r[0] for r in runlog.seminar_table("2026-08-27", log_dir=tmp_path, applied=applied)[1]]
    assert titles == ["오늘"]


def test_seminar_rows_sorted_by_start_time(tmp_path):
    applied = {"a": {
        "1": {"title": "저녁", "start_date": "2026-08-27", "start_time": "19:00"},
        "2": {"title": "점심", "start_date": "2026-08-27", "start_time": "12:00"},
    }}
    titles = [r[0] for r in runlog.seminar_table("2026-08-27", log_dir=tmp_path, applied=applied)[1]]
    assert titles == ["점심", "저녁"]


def test_junk_title_falls_back_to_seminar_number(tmp_path):
    """'라이브세미나'·'엠서클 통합회원'은 사이트 공통 요소지 세미나 이름이 아니다."""
    applied = {"a": {
        "5579": {"title": "라이브세미나", "start_date": "2026-08-27", "start_time": "17:00"},
        "5596": {"title": "엠서클 통합회원", "start_date": "2026-08-27", "start_time": "18:00"},
    }}
    titles = [r[0] for r in runlog.seminar_table("2026-08-27", log_dir=tmp_path, applied=applied)[1]]
    assert titles == ["세미나 5579", "세미나 5596"]


def test_real_title_shown_without_seminar_number(tmp_path):
    """제목이 있으면 번호는 뺀다(사용자 요청 2026-08-28)."""
    applied = {"a": {"5498": {
        "title": "[TH] O.M.T Web Symposium", "start_date": "2026-08-27", "start_time": "17:00",
    }}}
    assert runlog.seminar_table("2026-08-27", log_dir=tmp_path, applied=applied)[1][0][0] == "[TH] O.M.T Web Symposium"


def test_long_title_truncated_to_display_width(tmp_path):
    applied = {"a": {"1": {
        "title": "56세, 66세 국가건강검진 폐기능검사로 COPD 진단하고 치료하기",
        "start_date": "2026-08-27", "start_time": "13:00",
    }}}
    shown = runlog.seminar_table("2026-08-27", log_dir=tmp_path, applied=applied)[1][0][0]
    assert shown.endswith("…")
    assert runlog.display_width(shown) <= runlog.TITLE_MAX_COLS


def test_clean_title_strips_junk_and_keeps_real_names():
    assert runlog.clean_title("엠서클 통합회원") == ""
    assert runlog.clean_title(" 라이브세미나 ") == ""
    assert runlog.clean_title("") == ""
    assert runlog.clean_title("ARB Strategies in Atrial Fibrillation") == "ARB Strategies in Atrial Fibrillation"


def test_truncate_is_a_noop_below_the_limit():
    assert runlog.truncate("짧은 제목") == "짧은 제목"


# ---------------------------------------------------------------------------
# 표 렌더링
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text,width", [
    ("abc", 3), ("한글", 4), ("✅", 2), ("run1", 4), ("", 0), ("한a글", 5),
])
def test_display_width_counts_wide_chars_as_two(text, width):
    assert runlog.display_width(text) == width


def test_pad_aligns_to_display_width():
    assert runlog.pad("한글", 6) == "한글  "
    assert runlog.pad("한글", 6, "right") == "  한글"
    assert runlog.pad("넘치는글자", 2) == "넘치는글자"   # 잘라내지 않는다


def test_render_text_table_columns_line_up():
    out = runlog.render_text_table(["", "출석\n승진"], [["run1", "✅"]])
    lines = out.splitlines()
    # 한글·이모지 폭을 제대로 셌다면 모든 줄의 표시 폭이 같다.
    assert len({runlog.display_width(l) for l in lines}) == 1


def test_render_text_table_pads_ragged_rows():
    out = runlog.render_text_table(["A", "B", "C"], [["1"]])
    assert len({runlog.display_width(l) for l in out.splitlines()}) == 1


# ---------------------------------------------------------------------------
# PNG 렌더 (HTML 조립까지만 — 브라우저는 CI/로컬에서만)
# ---------------------------------------------------------------------------

def test_build_html_escapes_and_breaks_header_lines():
    html = tablepng.build_html("제목", ["", "출석\n승진"], [["run1", "✅"]])
    assert "출석<br>승진" in html
    assert "제목" in html


def test_build_html_escapes_injected_markup():
    html = tablepng.build_html("t", ["세미나"], [["<script>alert(1)</script>"]])
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_render_png_returns_none_without_rows(tmp_path):
    assert tablepng.render_png("t", ["a"], [], tmp_path / "x.png") is None


def test_daily_cells_keeps_crashed_doctorville_visible():
    """서브프로세스가 통째로 죽으면 task별 노드가 없다 — 실패가 표에서 사라지면 안 된다."""
    results = {"doctorville_wonju": {"status": "failed", "message": "타임아웃"}}
    assert runlog.daily_cells(results, creds={"wonju": {"label": "원주"}}) == {"닥터빌\n원주": "failed"}


def test_daily_cells_prefers_task_columns_when_present():
    results = {"doctorville_wonju": {"status": "failed", "attend": {"status": "success", "verified_by": "x"}}}
    cells = runlog.daily_cells(results)
    assert cells == {"출석\nwonju": "success"}


def test_log_dir_honors_env_override(monkeypatch, tmp_path):
    """conftest가 이 훅으로 테스트 로그를 격리한다 — 깨지면 레포 logs/가 더럽혀진다."""
    monkeypatch.setenv("DOCAUTO_LOG_DIR", str(tmp_path / "elsewhere"))
    assert runlog.resolve_log_dir() == tmp_path / "elsewhere"
    assert runlog.resolve_log_dir(tmp_path / "explicit") == tmp_path / "explicit"


def test_seminar_hook_writes_to_isolated_dir_not_repo():
    """doctorville._log_seminar 같은 내부 훅도 격리된 디렉터리를 써야 한다."""
    import doctorville
    doctorville._log_seminar("9999", "success", "acct", "격리 확인", "2026-01-01(금) 09:00 ~ 10:00")
    assert not (runlog.REPO_ROOT / "logs" / "seminar-2026-01-01.json").exists()
    assert runlog.resolve_log_dir() != runlog.REPO_ROOT / "logs"


def test_daily_cells_omits_seminar():
    """daily 표에 세미나 칸을 만들지 않는다 (사용자 지시 2026-08-29).

    세미나는 세미나 표에서 계정 × 단계로 따로 본다.
    """
    cells = runlog.daily_cells({
        "doctorville_bjh7790": {
            "attend": {"status": "success"},
            "quiz": {"status": "success"},
            "seminar": {"status": "success"},
        },
    })
    assert not [c for c in cells if c.startswith("세미나")]
    assert [c for c in cells if c.startswith("출석")]


def test_daily_table_hides_seminar_columns_from_old_logs(tmp_path):
    """컬럼은 날짜 파일 안에 누적된다 — 이미 적힌 세미나 컬럼도 렌더링에서 뺀다."""
    runlog.append_daily_run(
        {"출석\nbjh7790": "success", "세미나\nbjh7790": "success"},
        date_str="2026-08-29", at="09:12", log_dir=tmp_path,
    )
    headers, rows = runlog.daily_table("2026-08-29", log_dir=tmp_path)
    assert headers == ["", "출석\nbjh7790"]
    assert rows == [["run1\n09:12", "✅"]]
