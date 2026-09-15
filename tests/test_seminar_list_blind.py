"""목록 스캔이 통째로 실패한 런을 조용히 넘기지 않는다.

2026-09-15 00:15 런: `a.list_detail` 63건을 잡고도 `span.ico_apply` 0건,
일시 파싱 63건 실패. 신청 대상 0건이라 `no_target`(quiet)으로 끝나 텔레그램이
울리지 않았고, 며칠째 신청 0건인 것을 아무도 몰랐다. 같은 페이지를 읽는
`seminar_live`는 1500ms를 기다리고 정상 동작했다 — 고정 sleep 레이스다.
"""

import json
from datetime import datetime
from unittest.mock import MagicMock

import pytest

import doctorville
import notify
from common import KST

NOW = datetime(2026, 9, 15, 0, 18, tzinfo=KST)


def _listed(n, raw_ok=False, applicable=False):
    """목록 스캔 결과 n건. raw_ok=False면 껍데기(일시 없음)."""
    raw = "2026-09-15(화) 18:30 ~ 20:00 펙수클루 Triple Symposium" if raw_ok else "펙수클루 Triple Symposium"
    return [{"id": str(5680 + i), "title": "", "raw": raw, "applicable": applicable} for i in range(n)]


# --- raw 샘플 --------------------------------------------------------------

def test_unparsed_raw_samples_collects_failures():
    samples = doctorville.unparsed_raw_samples(_listed(5), NOW)
    assert len(samples) == 3  # 기본 limit
    assert all("펙수클루" in s for s in samples)


def test_unparsed_raw_samples_skips_parseable_items():
    assert doctorville.unparsed_raw_samples(_listed(3, raw_ok=True), NOW) == []


def test_unparsed_raw_samples_truncates():
    long_raw = [{"id": "1", "raw": "가" * 500}]
    assert len(doctorville.unparsed_raw_samples(long_raw, NOW)[0]) == 120


# --- 렌더 대기 --------------------------------------------------------------

def test_wait_for_seminar_list_returns_false_on_timeout():
    page = MagicMock()
    page.wait_for_function.side_effect = Exception("timeout")
    assert doctorville.wait_for_seminar_list(page) is False


def test_wait_for_seminar_list_returns_true_when_rendered():
    assert doctorville.wait_for_seminar_list(MagicMock()) is True


def test_list_ready_js_is_raw_string():
    """일반 문자열이면 \\d가 파이썬 단계에서 깨져 JS 정규식이 무력화된다."""
    assert r"\d{1,2}:\d{2}" in doctorville.LIST_READY_JS


# --- 판정 -------------------------------------------------------------------

def _run_task_seminar(tmp_path, monkeypatch, listed, ready=True):
    applied_file = tmp_path / "seminar_applied.json"
    applied_file.write_text("{}", encoding="utf-8")
    page = MagicMock()
    page.evaluate.return_value = listed
    if not ready:
        page.wait_for_function.side_effect = Exception("timeout")
    monkeypatch.setattr(doctorville.common, "goto_with_retry", lambda *a, **k: None)
    monkeypatch.setattr(doctorville, "_log_seminar", lambda *a, **k: None)
    return doctorville.task_seminar(page, {}, account="bjh7790", applied_path=applied_file)


def test_blind_scan_is_alert_not_quiet(tmp_path, monkeypatch):
    res = _run_task_seminar(tmp_path, monkeypatch, _listed(63), ready=False)

    assert res["list_blind"] is True
    assert res["list_ready"] is False
    assert res["list_items"] == 63
    assert res["list_unparsed"] == 63
    assert res["status"] == "unverified"
    assert res["list_raw_samples"]
    # quiet로 새지 않는지 — 이게 이 픽스의 전부다.
    assert notify.severity_of(res) == "alert"


def test_empty_list_stays_no_target(tmp_path, monkeypatch):
    """항목 자체가 0건이면 스캔 실패가 아니라 진짜 대상 없음이다."""
    res = _run_task_seminar(tmp_path, monkeypatch, [])
    assert res["status"] == "no_target"
    assert res["list_blind"] is False


def test_parseable_list_without_targets_stays_no_target(tmp_path, monkeypatch):
    """일시는 읽혔는데 신청 가능 배지만 없는 날 = 정상(마감·이미 신청)."""
    res = _run_task_seminar(tmp_path, monkeypatch, _listed(4, raw_ok=True))
    assert res["status"] == "no_target"
    assert res["list_blind"] is False
    assert res["listed_today"] == 4


def test_no_target_severity_is_quiet():
    assert notify.severity_of({"status": "no_target"}) == "quiet"
