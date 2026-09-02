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
    mock_btn.inner_text.side_effect = ["신청하기"]
    # 재진입 확인은 a.btn_bn의 .first를 읽는다(strict 위반 방지).
    mock_btn.first.inner_text.return_value = "신청취소"

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
    mock_btn.inner_text.side_effect = ["신청하기"]
    mock_btn.first.inner_text.return_value = "신청하기"

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
    mock_btn.inner_text.side_effect = ["신청하기"]
    # 재진입 확인은 a.btn_bn의 .first를 읽는다(strict 위반 방지).
    mock_btn.first.inner_text.return_value = "신청취소"

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
    # 로그는 항상 "오늘"(KST) 파일에 쌓인다. 상세 문자열의 날짜는 표의 시작·종료
    # 시각을 만드는 데만 쓰이므로 오늘 날짜로 맞춰 준다.
    today = runlog.today_str()
    mock_page.evaluate.side_effect = [
        [{"id": "5498", "title": "COPD 진단하고 치료하기"}],
        ("", f"{today}(금) 13:00 ~ 14:00"),
    ]
    mock_btn = MagicMock()
    mock_btn.inner_text.return_value = "마감"
    mock_page.locator.return_value = mock_btn
    monkeypatch.setattr(doctorville.common, "goto_with_retry", lambda *a, **k: None)

    doctorville.task_seminar(mock_page, {}, account="bjh7790", applied_path=applied_file)

    # 이력에는 없어야 한다 — 신청한 게 아니므로 다음 런에서 재시도해야 한다.
    assert json.loads(applied_file.read_text(encoding="utf-8")) == {}

    # 표에는 마감으로 올라온다.
    rows = runlog.seminar_table(today)[1]
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


def test_task_seminar_leaves_applied_titles_untouched(tmp_path, monkeypatch):
    """이미 신청한 세미나의 이력 제목은 목록 제목으로 덮어쓰지 않는다.

    제목 복구는 오염된 이력 108건을 한 번 되돌리려고 넣었던 일회성 장치다.
    이력이 정리된 지금은 매 런마다 파일만 더럽히므로 제거했다.
    """
    applied_file = tmp_path / "seminar_applied.json"
    applied_file.write_text(json.dumps({"bjh7790": {
        "5597": {                                    # 미래 방송분 + 오염된 제목
            "applied_at": "2026-08-20T16:42:12+09:00",
            "title": "엠서클 통합회원",
            "start": "2026-09-09(수) 18:00 ~ 19:30",
            "start_date": "2026-09-09",
        },
        "5607": {                                    # 이미 멀쩡한 제목은 건드리지 않는다
            "applied_at": "2026-08-24T18:02:44+09:00",
            "title": "진짜 세미나 이름",
            "start": "2026-09-08(화) 18:30 ~ 20:00",
            "start_date": "2026-09-08",
        },
    }}), encoding="utf-8")

    mock_page = MagicMock()
    mock_page.evaluate.side_effect = [
        [
            {"id": "5597", "title": "ALL 4 ONE WEB Symposium"},
            {"id": "5607", "title": "엠서클 통합회원"},   # 목록 쪽이 오염돼도 무시
        ],
    ]
    monkeypatch.setattr(doctorville.common, "goto_with_retry", lambda *a, **k: None)

    res = doctorville.task_seminar(mock_page, {}, account="bjh7790", applied_path=applied_file)

    assert res["skipped_known"] == 2
    saved = json.loads(applied_file.read_text(encoding="utf-8"))["bjh7790"]
    assert saved["5597"]["title"] == "엠서클 통합회원"
    assert saved["5607"]["title"] == "진짜 세미나 이름"


def test_task_seminar_no_target_writes_nothing(tmp_path, monkeypatch):
    """신청 가능한 세미나가 없으면 이력 파일을 건드리지 않고 no_target으로 끝난다."""
    applied_file = tmp_path / "seminar_applied.json"
    applied_file.write_text(json.dumps({"bjh7790": {"5597": {
        "applied_at": "2026-08-20T16:42:12+09:00",
        "title": "엠서클 통합회원",
        "start": "2026-09-09(수) 18:00 ~ 19:30",
        "start_date": "2026-09-09",
    }}}), encoding="utf-8")

    mock_page = MagicMock()
    mock_page.evaluate.side_effect = [
        # 이미 신청해서 ico_apply 배지가 없다 → 신청 대상 0건
        [{"id": "5597", "title": "ALL 4 ONE WEB Symposium", "applicable": False}],
    ]
    monkeypatch.setattr(doctorville.common, "goto_with_retry", lambda *a, **k: None)

    res = doctorville.task_seminar(mock_page, {}, account="bjh7790", applied_path=applied_file)

    assert res["status"] == "no_target"
    saved = json.loads(applied_file.read_text(encoding="utf-8"))["bjh7790"]
    assert saved["5597"]["title"] == "엠서클 통합회원"


def test_task_seminar_only_applies_to_applicable_entries(tmp_path, monkeypatch):
    """applicable=False인 항목은 신청 대상 집계에서 빠진다(상세를 열지 않는다)."""
    applied_file = tmp_path / "seminar_applied.json"
    applied_file.write_text(json.dumps({"bjh7790": {
        "5700": {"applied_at": "2026-09-01T10:00:00+09:00", "title": "신청 가능한 것"},
        "5701": {"applied_at": "2026-09-01T10:00:00+09:00", "title": "이미 신청한 것"},
    }}), encoding="utf-8")

    mock_page = MagicMock()
    mock_page.evaluate.side_effect = [
        [
            {"id": "5700", "title": "신청 가능한 것", "applicable": True},
            {"id": "5701", "title": "이미 신청한 것", "applicable": False},
        ],
    ]
    monkeypatch.setattr(doctorville.common, "goto_with_retry", lambda *a, **k: None)

    res = doctorville.task_seminar(mock_page, {}, account="bjh7790", applied_path=applied_file)

    # 신청 대상 후보는 5700 하나뿐이다. 5701은 목록에 있었지만 집계에 안 들어간다.
    assert res["skipped_known"] == 1


def test_task_seminar_confirm_modal_uses_visible_selector(tmp_path, monkeypatch):
    """동의 모달은 :visible로 걸러 잡는다.

    2026-09-02 세미나 5675: 폴백이 `button.btn_confirm`의 첫 번째를 잡았는데
    그게 숨은 버튼이라 5초 뒤 타임아웃 → except로 삼켜져 동의를 못 눌렀고,
    두 계정 모두 신청 실패(unverified)로 끝났다. 숨은 버튼을 후보에서 빼야 한다.
    """
    applied_file = tmp_path / "seminar_applied.json"
    applied_file.write_text("{}", encoding="utf-8")

    mock_page = MagicMock()
    mock_page.evaluate.return_value = ["6010"]

    mock_btn = MagicMock()
    mock_btn.inner_text.side_effect = ["신청하기"]
    # 재진입 확인은 a.btn_bn의 .first를 읽는다(strict 위반 방지).
    mock_btn.first.inner_text.return_value = "신청취소"

    seen = []

    def locator_side_effect(sel):
        if "btn_bn" in sel:
            return mock_btn
        if "btn_confirm" in sel:
            seen.append(sel)
            m = MagicMock()
            # "동의합니다." 버튼은 이 세미나에 없다 → 폴백 경로를 타게 한다.
            m.count.return_value = 0 if "동의합니다." in sel else 1
            return m
        return MagicMock()

    mock_page.locator.side_effect = locator_side_effect
    monkeypatch.setattr(doctorville.common, "goto_with_retry", lambda *a, **k: None)
    monkeypatch.setattr(doctorville, "_seminar_detail_meta", lambda p: ("모달 세미나", ""))

    res = doctorville.task_seminar(mock_page, {}, account="bjh7790", applied_path=applied_file)

    assert res["status"] == "success"
    assert seen, "btn_confirm 조회가 아예 없었다"
    assert all(":visible" in sel for sel in seen), seen


def test_task_seminar_unverified_saves_screenshot(tmp_path, monkeypatch):
    """미검증 건은 스크린샷을 남긴다 — 이 경로는 예외가 없어 log_error가 안 찍힌다."""
    applied_file = tmp_path / "seminar_applied.json"
    applied_file.write_text("{}", encoding="utf-8")

    mock_page = MagicMock()
    mock_page.evaluate.return_value = ["6011"]

    mock_btn = MagicMock()
    mock_btn.inner_text.side_effect = ["신청하기"]
    mock_btn.first.inner_text.return_value = "신청하기"

    def locator_side_effect(sel):
        if "btn_bn" in sel:
            return mock_btn
        m = MagicMock()
        m.count.return_value = 0
        return m

    mock_page.locator.side_effect = locator_side_effect
    monkeypatch.setattr(doctorville.common, "goto_with_retry", lambda *a, **k: None)
    monkeypatch.setattr(doctorville, "_seminar_detail_meta", lambda p: ("실패 세미나", ""))
    monkeypatch.setattr(doctorville, "save_screenshot", lambda p, tag: f"/logs/{tag}.png")

    res = doctorville.task_seminar(mock_page, {}, account="bjh7790", applied_path=applied_file)

    assert res["status"] == "unverified"
    assert res["screenshots"] == {"6011": "/logs/seminar_6011_unverified.png"}


def test_task_seminar_unverified_carries_text_diagnostics(tmp_path, monkeypatch):
    """미검증 건은 스크린샷뿐 아니라 텍스트 근거(버튼 문구·모달 처리)를 남겨야 한다.

    2026-09-02 세미나 5675가 두 계정 두 런 연속 unverified로 떨어졌는데, 이
    경로는 예외를 안 던져 errors 로그가 없고 스크린샷은 artifact 7일 만료라
    사후 진단이 불가능했다.
    """
    applied_file = tmp_path / "seminar_applied.json"
    applied_file.write_text("{}", encoding="utf-8")

    mock_page = MagicMock()
    mock_page.evaluate.return_value = ["6001"]

    mock_btn = MagicMock()
    mock_btn.inner_text.side_effect = ["신청하기"]
    mock_btn.first.inner_text.return_value = "신청하기"
    mock_btn.count.return_value = 2

    mock_confirm_btn = MagicMock()
    mock_confirm_btn.count.return_value = 1

    def locator_side_effect(sel):
        if "btn_bn" in sel:
            return mock_btn
        if "btn_confirm" in sel:
            return mock_confirm_btn
        return MagicMock()

    mock_page.locator.side_effect = locator_side_effect
    monkeypatch.setattr(doctorville.common, "goto_with_retry", lambda *a, **k: None)
    monkeypatch.setattr(doctorville, "_seminar_detail_meta", lambda p: ("미검증 세미나", ""))
    monkeypatch.setattr(doctorville, "save_screenshot", lambda *a, **k: "shot.png")

    res = doctorville.task_seminar(mock_page, {}, account="bjh7790", applied_path=applied_file)

    assert res["status"] == "unverified"
    diag = res["diagnostics"]["6001"]
    assert diag["btn_before"] == "신청하기"
    assert diag["btn_after"] == "신청하기"
    assert diag["btn_count_after"] == 2
    assert diag["modal"] == "동의합니다."
