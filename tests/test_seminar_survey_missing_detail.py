"""사라진 세미나 상세: 홈으로 튕긴 조회는 장애가 아니라 '페이지 없음'이다.

2026-09-22 run 35719120212 — 세미나 5678(18:30~19:30, 비뇨기질환에서 프로바이오틱스의
최신지견과 의의)은 같은 내용이 5695로 다시 열렸고(5695는 입장·설문 모두 success),
옛 id의 상세는 m·www 모두 홈으로 리다이렉트됐다. 두 조회가 URL 불일치로 버려져
`probe_read_detail_page()`가 거짓이 되는 바람에, 신청만 남은 이 후보가 창이 열려
있는 시각에 `unverified`(alert)로 떠 유지보수 세션을 깨웠다. 자동화가 할 일은 없다.

접속 실패의 침묵까지 덮으면 안 되므로, 홈으로 튕긴 판정은 버튼을 실제로 읽어 냈고
로그인 증거가 있을 때만 선다.
"""

import sys
from pathlib import Path
from datetime import datetime
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import seminar_survey
import survey_detail
from common import KST

# 실측 payload(run 35719120212 세미나 5678)
M_HOME = {
    "url": "https://m.doctorville.co.kr/",
    "visible": ["메뉴 열기", "목록으로 보기", "닫기"],
    "hidden": [],
}
WWW_MAIN = {
    "url": "https://www.doctorville.co.kr/main",
    "visible": ["전체 메뉴 열기", "더보기", "로그아웃", "닫기"],
    "hidden": ["통합검색", "삭제", "취소하기", "동의하기", "로그아웃"],
}
DETAIL_ERRORS = [
    "m 상세: 세미나 5678 페이지가 아님(https://m.doctorville.co.kr/)",
    "www 상세: 세미나 5678 페이지가 아님(https://www.doctorville.co.kr/main)",
]

ITEM_5678 = {
    "id": 5678,
    "title": "비뇨기질환에서 프로바이오틱스의 최신지견과 의의",
    "start": "2026-09-22(화) 18:30 ~ 19:30",
    "from_applied": True,
}

NOW = datetime(2026, 9, 22, 20, 8, tzinfo=KST)


def _probe(monkeypatch, records: dict):
    monkeypatch.setattr(survey_detail, "LAST_DETAIL_PROBE", dict(records))


def _stub_unopened(monkeypatch, records: dict):
    monkeypatch.setattr(
        seminar_survey,
        "open_survey",
        lambda page, sid: (None, "설문 참여 버튼이 없음(설문 미제공 또는 종료)."),
    )
    monkeypatch.setattr(
        seminar_survey,
        "confirm_survey_done",
        lambda page, sid, retries=0: ("unknown", DETAIL_ERRORS),
    )
    monkeypatch.setattr(seminar_survey, "probe_saw_running_seminar", lambda: False)
    monkeypatch.setattr(seminar_survey, "probe_saw_ended_seminar", lambda: False)
    monkeypatch.setattr(seminar_survey, "probe_read_detail_page", lambda: False)
    monkeypatch.setattr(seminar_survey, "copy_probe", lambda: {})
    _probe(monkeypatch, records)


def test_probe_detail_gone_on_both_domains(monkeypatch):
    _probe(monkeypatch, {"m": M_HOME, "www": WWW_MAIN})
    assert survey_detail.probe_detail_gone() is True


def test_probe_detail_gone_needs_both_domains(monkeypatch):
    """한쪽만 홈이면 다른 쪽 판정이 살아 있다 — '페이지 없음'이 아니다."""
    _probe(monkeypatch, {"m": M_HOME})
    assert survey_detail.probe_detail_gone() is False

    _probe(
        monkeypatch,
        {
            "m": M_HOME,
            "www": {
                "url": "https://www.doctorville.co.kr/seminar/seminarDetail?seminarId=5678",
                "visible": ["세미나 종료"],
                "hidden": [],
            },
        },
    )
    assert survey_detail.probe_detail_gone() is False


def test_probe_detail_gone_needs_buttons(monkeypatch):
    """접속 실패의 침묵은 덮지 않는다 — 버튼을 읽어 냈어야 한다."""
    _probe(
        monkeypatch,
        {
            "m": {"url": "https://m.doctorville.co.kr/", "visible": [], "hidden": []},
            "www": {"url": "https://www.doctorville.co.kr/main", "visible": [], "hidden": []},
        },
    )
    assert survey_detail.probe_detail_gone() is False


def test_probe_detail_gone_needs_login_evidence(monkeypatch):
    """세션이 끊겨 튕긴 것이면 장애다 — 로그인 증거가 없으면 판정하지 않는다."""
    _probe(
        monkeypatch,
        {
            "m": M_HOME,
            "www": {
                "url": "https://www.doctorville.co.kr/main",
                "visible": ["전체 메뉴 열기", "로그인"],
                "hidden": [],
            },
        },
    )
    assert survey_detail.probe_detail_gone() is False


def test_vanished_detail_on_applied_candidate_is_no_target(monkeypatch):
    """5678 재현 — 사라진 상세를 `unverified`로 읽어 세션을 깨우던 자리."""
    _stub_unopened(monkeypatch, {"m": M_HOME, "www": WWW_MAIN})

    result = seminar_survey.run_survey(MagicMock(), ITEM_5678, now_dt=NOW)

    assert result["status"] == "no_target"
    assert result["detail_verdict"] == "unknown"
    assert "홈으로 튕김" in result["message"]


def test_vanished_detail_on_entered_seminar_still_alerts(monkeypatch):
    """입장한 세미나의 설문이 안 열리는 것은 여전히 실패다."""
    _stub_unopened(monkeypatch, {"m": M_HOME, "www": WWW_MAIN})

    result = seminar_survey.run_survey(
        MagicMock(),
        {**ITEM_5678, "from_applied": False, "entered_at": "2026-09-22T18:35:00+09:00"},
        now_dt=NOW,
    )

    assert result["status"] == "unverified"


def test_detail_access_failure_still_alerts(monkeypatch):
    """조회 자체가 실패한 침묵은 그대로 alert다(홈 리다이렉트가 아니다)."""
    _stub_unopened(
        monkeypatch,
        {
            "m": {"url": "", "visible": [], "hidden": []},
            "www": {"url": "", "visible": [], "hidden": []},
        },
    )

    result = seminar_survey.run_survey(MagicMock(), ITEM_5678, now_dt=NOW)

    assert result["status"] == "unverified"
