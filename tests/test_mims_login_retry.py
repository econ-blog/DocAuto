"""mims 로그인 재시도 + 응답 헤더 기록 회귀 테스트.

배경: 2026-09 오류 로그 30건 중 28건이 net::ERR_CONNECTION_CLOSED이고
그중 27건이 _do_mims_login의 wait_for_url 한 줄에서 났다. 과거 코드는
PlaywrightTimeoutError만 잡아 이 예외를 전파했고 계정 런이 통째로 죽었다.
"""
import json
import sys
from pathlib import Path

import pytest
from playwright.sync_api import Error as PlaywrightError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import common  # noqa: E402
import doctorville  # noqa: E402

CREDS = {"email": "a@b.c", "password": "pw"}


class FakePage:
    """_do_mims_login이 건드리는 표면만 흉내낸다."""

    def __init__(self, script):
        self.script = list(script)   # 시도별 결과: 예외 인스턴스 또는 최종 URL
        self.url = "https://mims-account.shop.co.kr/login"
        self.attempts = 0
        self.sleeps = []
        self.listeners = []

    # --- 폼 조작 -------------------------------------------------------
    def wait_for_selector(self, sel, timeout=None):
        return None

    def fill(self, sel, value):
        return None

    def click(self, sel):
        outcome = self.script[self.attempts] if self.attempts < len(self.script) else self.script[-1]
        self.attempts += 1
        if isinstance(outcome, BaseException):
            raise outcome
        self.url = outcome

    def wait_for_url(self, pattern, timeout=None):
        return None

    def goto(self, url, wait_until=None, timeout=None):
        self.url = url

    def wait_for_timeout(self, ms):
        self.sleeps.append(ms)

    # --- 이벤트 --------------------------------------------------------
    def on(self, event, handler):
        self.listeners.append((event, handler))

    def remove_listener(self, event, handler):
        self.listeners.remove((event, handler))


class FakeResponse:
    def __init__(self, url, status, headers, method="GET"):
        self.url = url
        self.status = status
        self.headers = headers
        self.request = type("R", (), {"method": method})()


DONE_URL = "https://www.doctorville.co.kr/event/attend"
CONN_CLOSED = PlaywrightError("net::ERR_CONNECTION_CLOSED")


def test_connection_closed_is_retried_not_raised(monkeypatch, tmp_path):
    monkeypatch.setattr(common, "ERROR_LOG_DIR", tmp_path)
    page = FakePage([CONN_CLOSED, DONE_URL])
    assert doctorville._do_mims_login(page, CREDS) is True
    assert page.attempts == 2
    assert page.sleeps == [3000]          # 첫 백오프만 소비


def test_all_attempts_fail_returns_false_and_logs(monkeypatch, tmp_path):
    monkeypatch.setattr(common, "ERROR_LOG_DIR", tmp_path)
    page = FakePage([CONN_CLOSED])
    assert doctorville._do_mims_login(page, CREDS) is False
    assert page.attempts == 3             # 기본 retries=2 → 총 3회
    entry = json.loads((tmp_path / next(p.name for p in tmp_path.iterdir())).read_text(encoding="utf-8").strip())
    assert entry["task"] == "mims_login"
    assert "ERR_CONNECTION_CLOSED" in entry["message"]
    assert "responses" in entry["extra"]


def test_non_network_error_propagates(monkeypatch, tmp_path):
    """strict mode violation 같은 코드 결함에 45초를 태우면 안 된다."""
    monkeypatch.setattr(common, "ERROR_LOG_DIR", tmp_path)
    page = FakePage([PlaywrightError("strict mode violation: locator resolved to 2 elements")])
    with pytest.raises(PlaywrightError):
        doctorville._do_mims_login(page, CREDS)
    assert page.attempts == 1


def test_redirect_completed_despite_closed_socket(monkeypatch, tmp_path):
    """소켓이 끊겨도 리다이렉트가 끝나 있으면 성공으로 본다."""
    monkeypatch.setattr(common, "ERROR_LOG_DIR", tmp_path)

    class Page(FakePage):
        def click(self, sel):
            self.attempts += 1
            self.url = DONE_URL
            raise CONN_CLOSED

    page = Page([])
    assert doctorville._do_mims_login(page, CREDS) is True
    assert page.attempts == 1


def test_listener_detached(monkeypatch, tmp_path):
    monkeypatch.setattr(common, "ERROR_LOG_DIR", tmp_path)
    page = FakePage([DONE_URL])
    doctorville._do_mims_login(page, CREDS)
    assert page.listeners == []


# --- ResponseRecorder ---------------------------------------------------

def test_recorder_whitelists_headers_and_strips_query():
    page = FakePage([DONE_URL])
    rec = common.ResponseRecorder(page, url_filter=("mims-account",))
    handler = page.listeners[0][1]
    handler(FakeResponse(
        "https://mims-account.shop.co.kr/login?token=SECRET&id=me@x.com",
        403,
        {"Server": "nginx", "CF-Ray": "abc123", "Set-Cookie": "sid=SECRET", "Retry-After": "30"},
        method="POST",
    ))
    handler(FakeResponse("https://other.example.com/x", 200, {"Server": "a"}))
    assert len(rec.records) == 1
    r = rec.records[0]
    assert r["status"] == 403 and r["method"] == "POST"
    assert "token=SECRET" not in r["url"] and "?" not in r["url"]
    assert r["headers"] == {"server": "nginx", "cf-ray": "abc123", "retry-after": "30"}
    assert "set-cookie" not in r["headers"]


def test_recorder_is_ring_buffer_and_swallows_errors():
    page = FakePage([DONE_URL])
    rec = common.ResponseRecorder(page, limit=3)
    handler = page.listeners[0][1]
    for i in range(10):
        handler(FakeResponse(f"https://x/{i}", 200, {}))
    assert len(rec.records) == 3
    assert rec.records[-1]["url"].endswith("/9")

    class Broken:
        url = property(lambda self: (_ for _ in ()).throw(RuntimeError("boom")))

    handler(Broken())   # 예외가 밖으로 나오면 안 된다
    assert len(rec.records) == 3
