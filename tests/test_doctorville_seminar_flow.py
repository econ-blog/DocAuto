import json
from unittest.mock import MagicMock
import doctorville


def test_task_seminar_records_already_applied_detail(tmp_path, monkeypatch):
    """상세 페이지에서 '신청취소' 버튼을 만나면 seminar_applied.json에 기록하고 재방문을 방지하는지 검증."""
    applied_file = tmp_path / "seminar_applied.json"
    applied_file.write_text("{}", encoding="utf-8")

    mock_page = MagicMock()
    # 목록에서 5533 세미나 발견 후 상세에서 제목/일시 반환
    mock_page.evaluate.side_effect = [
        ["5533"],  # ico_apply 추출
        ("호흡기 심포지엄", "2026-08-20(목) 13:00 ~ 14:00"),  # _seminar_detail_meta
    ]

    mock_btn = MagicMock()
    mock_btn.inner_text.return_value = "신청취소"
    mock_page.locator.return_value = mock_btn

    monkeypatch.setattr(doctorville.common, "goto_with_retry", lambda *args, **kwargs: None)

    res = doctorville.task_seminar(mock_page, {}, account="bjh7790", applied_path=applied_file)

    # 신규 신청은 0건이지만 이미 신청된 세미나를 확인했으므로 dirty=True, already_done
    assert res["status"] == "already_done"
    assert res["count"] == 0

    # seminar_applied.json에 기록되었는지 확인
    saved = json.loads(applied_file.read_text(encoding="utf-8"))
    assert "5533" in saved["bjh7790"]
    assert saved["bjh7790"]["5533"]["title"] == "호흡기 심포지엄"
    assert saved["bjh7790"]["5533"]["start"] == "2026-08-20(목) 13:00 ~ 14:00"


def test_task_seminar_skipped_when_no_new_targets(tmp_path, monkeypatch):
    """목록의 모든 세미나가 이미 seminar_applied.json에 있어서 건너뛰면 already_done을 반환해야 함."""
    applied_file = tmp_path / "seminar_applied.json"
    applied_file.write_text(json.dumps({
        "bjh7790": {
            "5533": {"applied_at": "2026-08-10T10:00:00+09:00"}
        }
    }), encoding="utf-8")

    mock_page = MagicMock()
    mock_page.evaluate.return_value = ["5533"]

    monkeypatch.setattr(doctorville.common, "goto_with_retry", lambda *args, **kwargs: None)

    res = doctorville.task_seminar(mock_page, {}, account="bjh7790", applied_path=applied_file)

    assert res["status"] == "already_done"
    assert res["skipped_known"] == 1
    assert res["count"] == 0
    assert "신규 신청 대상 없음" in res["message"]


def test_task_seminar_new_application_success_sets_dirty_and_records(tmp_path, monkeypatch):
    """신규 세미나 신청 성공 시 applied 목록에 추가되고, verified_by 포함 success를 반환하며 파일에 기록되어야 함."""
    applied_file = tmp_path / "seminar_applied.json"
    applied_file.write_text("{}", encoding="utf-8")

    mock_page = MagicMock()
    mock_page.evaluate.return_value = ["6001"]

    mock_btn = MagicMock()
    # 첫 상세 진입: "신청하기", 신청 후 재진입: "신청취소"
    mock_btn.inner_text.side_effect = ["신청하기", "신청취소"]

    mock_confirm_btn = MagicMock()
    mock_confirm_btn.count.return_value = 1
    mock_confirm_btn.first.is_visible.return_value = True

    def locator_side_effect(sel):
        if "btn_bn" in sel:
            return mock_btn
        if "btn_confirm" in sel:
            return mock_confirm_btn
        return MagicMock()

    mock_page.locator.side_effect = locator_side_effect
    monkeypatch.setattr(doctorville.common, "goto_with_retry", lambda *args, **kwargs: None)
    monkeypatch.setattr(doctorville, "_seminar_detail_meta", lambda p: ("당뇨 심포지엄", "2026-08-25(화) 19:00 ~ 20:00"))

    res = doctorville.task_seminar(mock_page, {}, account="bjh7790", applied_path=applied_file)

    assert res["status"] == "success"
    assert res["count"] == 1
    assert res["applied"] == [6001]
    assert res["verified_by"] == "a.btn_bn: 신청취소"
    assert "신청 완료 1건" in res["message"]

    # seminar_applied.json에 정상 기록되었는지 확인
    saved = json.loads(applied_file.read_text(encoding="utf-8"))
    assert "6001" in saved["bjh7790"]
    assert saved["bjh7790"]["6001"]["title"] == "당뇨 심포지엄"
    assert saved["bjh7790"]["6001"]["start"] == "2026-08-25(화) 19:00 ~ 20:00"


def test_task_seminar_recheck_failure_does_not_record_and_returns_unverified(tmp_path, monkeypatch):
    """신청 클릭 후 상세 재확인에서 '신청취소'가 확인되지 않으면 unverified 반환 및 이력에 기록하지 않아야 함."""
    applied_file = tmp_path / "seminar_applied.json"
    applied_file.write_text("{}", encoding="utf-8")

    mock_page = MagicMock()
    mock_page.evaluate.return_value = ["6002"]

    mock_btn = MagicMock()
    # 첫 진입: "신청하기", 재진입 시에도 여전히 "신청하기" (신청 실패 상태)
    mock_btn.inner_text.side_effect = ["신청하기", "신청하기"]

    mock_confirm_btn = MagicMock()
    mock_confirm_btn.count.return_value = 0

    def locator_side_effect(sel):
        if "btn_bn" in sel:
            return mock_btn
        if "btn_confirm" in sel:
            return mock_confirm_btn
        return MagicMock()

    mock_page.locator.side_effect = locator_side_effect
    monkeypatch.setattr(doctorville.common, "goto_with_retry", lambda *args, **kwargs: None)
    monkeypatch.setattr(doctorville, "_seminar_detail_meta", lambda p: ("실패 세미나", "2026-08-25(화) 19:00 ~ 20:00"))

    res = doctorville.task_seminar(mock_page, {}, account="bjh7790", applied_path=applied_file)

    assert res["status"] == "unverified"
    assert res["count"] == 0
    assert res["applied"] == []
    assert "미검증 1건" in res["message"]

    # 영구 skip 방지: 실패한 세미나는 seminar_applied.json에 기록되지 않아야 함
    saved = json.loads(applied_file.read_text(encoding="utf-8"))
    assert "bjh7790" not in saved or "6002" not in saved.get("bjh7790", {})


def test_task_seminar_closed_seminar_skipped_not_dirty(tmp_path, monkeypatch):
    """상세 버튼이 '신청마감' 또는 '정원초과'이면 건너뛰고 skipped를 반환하며 파일은 수정되지 않아야 함."""
    applied_file = tmp_path / "seminar_applied.json"
    applied_file.write_text("{}", encoding="utf-8")

    mock_page = MagicMock()
    mock_page.evaluate.return_value = ["6003"]

    mock_btn = MagicMock()
    mock_btn.inner_text.return_value = "접수마감"
    mock_page.locator.return_value = mock_btn

    monkeypatch.setattr(doctorville.common, "goto_with_retry", lambda *args, **kwargs: None)
    monkeypatch.setattr(doctorville, "_seminar_detail_meta", lambda p: ("마감 세미나", "2026-08-25(화) 19:00 ~ 20:00"))

    res = doctorville.task_seminar(mock_page, {}, account="bjh7790", applied_path=applied_file)

    assert res["status"] == "skipped"
    assert res["count"] == 0
    assert res["applied"] == []
    assert "신청 가능한 세미나 없음" in res["message"]

    # dirty=False 이므로 파일은 변경되지 않음
    assert json.loads(applied_file.read_text(encoding="utf-8")) == {}


def test_task_seminar_account_none_does_not_persist(tmp_path, monkeypatch):
    """account=None 인 경우 파일 입출력 없이 세미나 신청만 수행해야 함."""
    mock_page = MagicMock()
    mock_page.evaluate.return_value = ["6004"]

    mock_btn = MagicMock()
    mock_btn.inner_text.side_effect = ["신청하기", "신청취소"]

    mock_confirm_btn = MagicMock()
    mock_confirm_btn.count.return_value = 1
    mock_confirm_btn.first.is_visible.return_value = True

    def locator_side_effect(sel):
        if "btn_bn" in sel:
            return mock_btn
        if "btn_confirm" in sel:
            return mock_confirm_btn
        return MagicMock()

    mock_page.locator.side_effect = locator_side_effect
    monkeypatch.setattr(doctorville.common, "goto_with_retry", lambda *args, **kwargs: None)
    monkeypatch.setattr(doctorville, "_seminar_detail_meta", lambda p: ("익명 세미나", "2026-08-25(화) 19:00 ~ 20:00"))

    res = doctorville.task_seminar(mock_page, {}, account=None)

    assert res["status"] == "success"
    assert res["applied"] == [6004]


def test_task_seminar_prefers_list_title_over_polluted_detail_title(tmp_path, monkeypatch):
    """상세 전역 조회는 헤더('엠서클 통합회원')를 집어온다 — 목록 제목이 이겨야 한다.

    2026-08-28: seminar_applied.json 108건의 제목이 전부 '엠서클 통합회원'/'라이브세미나'였다.
    """
    applied_file = tmp_path / "seminar_applied.json"
    applied_file.write_text("{}", encoding="utf-8")

    mock_page = MagicMock()
    mock_page.evaluate.side_effect = [
        [{"id": "5596", "title": "ARB Strategies in Atrial Fibrillation"}],
        ("엠서클 통합회원", "2026-08-28(금) 13:00 ~ 14:00"),  # 오염된 상세 제목
    ]
    mock_btn = MagicMock()
    mock_btn.inner_text.return_value = "신청취소"
    mock_page.locator.return_value = mock_btn
    monkeypatch.setattr(doctorville.common, "goto_with_retry", lambda *a, **k: None)

    doctorville.task_seminar(mock_page, {}, account="bjh7790", applied_path=applied_file)

    saved = json.loads(applied_file.read_text(encoding="utf-8"))
    assert saved["bjh7790"]["5596"]["title"] == "ARB Strategies in Atrial Fibrillation"


def test_task_seminar_logs_closed_seminar_to_table(tmp_path, monkeypatch):
    """마감·정원초과는 이력에 기록하지 않되, 표에는 '그날 예정된 세미나'로 올라와야 한다.

    2026-08-28: 세미나 5498이 이 경로라 표에서 통째로 빠졌다(5개 중 4개만 표시).
    """
    import runlog

    applied_file = tmp_path / "seminar_applied.json"
    applied_file.write_text("{}", encoding="utf-8")

    mock_page = MagicMock()
    mock_page.evaluate.side_effect = [
        [{"id": "5498", "title": "COPD 진단하고 치료하기"}],
        ("", "2026-08-28(금) 13:00 ~ 14:00"),
    ]
    mock_btn = MagicMock()
    mock_btn.inner_text.return_value = "마감"
    mock_page.locator.return_value = mock_btn
    monkeypatch.setattr(doctorville.common, "goto_with_retry", lambda *a, **k: None)

    doctorville.task_seminar(mock_page, {}, account="bjh7790", applied_path=applied_file)

    # 이력에는 없어야 한다 — 신청한 게 아니므로 다음 런에서 재시도해야 한다.
    assert json.loads(applied_file.read_text(encoding="utf-8")) == {}

    # 표에는 마감으로 올라온다.
    rows = runlog.seminar_table("2026-08-28")[1]
    assert rows == [["COPD 진단하고 치료하기", "13:00", "14:00", "🔒", "·", "·"]]


def test_task_seminar_survives_list_without_titles(tmp_path, monkeypatch):
    """DOM이 바뀌어 목록이 문자열 id만 돌려줘도 신청 자체는 계속돼야 한다."""
    applied_file = tmp_path / "seminar_applied.json"
    applied_file.write_text("{}", encoding="utf-8")

    mock_page = MagicMock()
    mock_page.evaluate.side_effect = [
        ["5533"],
        ("호흡기 심포지엄", "2026-08-20(목) 13:00 ~ 14:00"),
    ]
    mock_btn = MagicMock()
    mock_btn.inner_text.return_value = "신청취소"
    mock_page.locator.return_value = mock_btn
    monkeypatch.setattr(doctorville.common, "goto_with_retry", lambda *a, **k: None)

    res = doctorville.task_seminar(mock_page, {}, account="bjh7790", applied_path=applied_file)
    assert res["status"] == "already_done"
    saved = json.loads(applied_file.read_text(encoding="utf-8"))
    assert saved["bjh7790"]["5533"]["title"] == "호흡기 심포지엄"


def test_task_seminar_fills_todays_titles_without_opening_details(tmp_path, monkeypatch):
    """이미 신청한 세미나도 목록 제목만으로 표에 이름이 채워져야 한다(상세 로드 없이)."""
    import runlog
    from datetime import datetime
    import common

    today = datetime.now(common.KST).strftime("%Y-%m-%d")
    applied_file = tmp_path / "seminar_applied.json"
    applied_file.write_text(json.dumps({"bjh7790": {"5585": {
        "applied_at": "2026-08-18T18:33:37+09:00",
        "title": "라이브세미나",                      # 오염된 이력 제목
        "start": f"{today}(금) 17:00 ~ 18:30",
        "start_date": today, "start_time": "17:00", "end_time": "18:30",
    }}}), encoding="utf-8")

    mock_page = MagicMock()
    mock_page.evaluate.side_effect = [
        [{"id": "5585", "title": "[TH] Love Life Love Liver web Symposium"}],
    ]
    monkeypatch.setattr(doctorville.common, "goto_with_retry", lambda *a, **k: None)

    res = doctorville.task_seminar(mock_page, {}, account="bjh7790", applied_path=applied_file)

    # 상세를 한 번도 열지 않았다(목록 evaluate 1회만 소비).
    assert res["skipped_known"] == 1
    rows = runlog.seminar_table(today)[1]
    assert rows[0][0] == "[TH] Love Life Love Liver w…"
    assert rows[0][1:4] == ["17:00", "18:30", "☑️"]
