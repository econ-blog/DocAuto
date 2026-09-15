from common import list_accounts, account_label, is_recon_enabled, KST

def test_list_accounts():
    creds = {
        "telegram": {"bot_token": "xxx"},
        "bjh7790": {"label": "승진", "doctorville": {}, "hmp": {}},
        "wonju": {"doctorville": {}}
    }
    assert list_accounts(creds) == ["bjh7790", "wonju"]
    assert list_accounts(creds, site="hmp") == ["bjh7790"]
    assert list_accounts(creds, site="doctorville") == ["bjh7790", "wonju"]

def test_account_label():
    creds = {
        "bjh7790": {"label": "승진"},
        "wonju": {}
    }
    assert account_label(creds, "bjh7790") == "승진"
    assert account_label(creds, "wonju") == "wonju"

def test_is_recon_enabled(monkeypatch):
    monkeypatch.setenv("RECON", "1")
    assert is_recon_enabled() is True
    monkeypatch.delenv("RECON", raising=False)
    assert is_recon_enabled() is False

def test_kst_timezone():
    assert KST.utcoffset(None).total_seconds() == 9 * 3600

def test_goto_with_retry_network_error():
    from common import goto_with_retry
    from playwright.sync_api import Error as PlaywrightError

    class MockPage:
        def __init__(self):
            self.calls = 0
            self.timeouts = []
        def goto(self, url, wait_until="load", timeout=15000):
            self.calls += 1
            if self.calls == 1:
                raise PlaywrightError("Page.goto: net::ERR_CONNECTION_CLOSED at https://www.doctorville.co.kr/event/attend")
            return None
        def wait_for_timeout(self, ms):
            self.timeouts.append(ms)

    page = MockPage()
    goto_with_retry(page, "https://www.doctorville.co.kr/event/attend", retries=2)
    assert page.calls == 2
    assert page.timeouts == [3000]

def test_goto_with_retry_raises_after_max_retries():
    import pytest
    from common import goto_with_retry
    from playwright.sync_api import Error as PlaywrightError

    class MockFailingPage:
        def __init__(self):
            self.calls = 0
            self.timeouts = []
        def goto(self, url, wait_until="load", timeout=15000):
            self.calls += 1
            raise PlaywrightError("Page.goto: net::ERR_CONNECTION_CLOSED at https://www.doctorville.co.kr/event/attend")
        def wait_for_timeout(self, ms):
            self.timeouts.append(ms)

    page = MockFailingPage()
    with pytest.raises(PlaywrightError) as exc_info:
        goto_with_retry(page, "https://www.doctorville.co.kr/event/attend", retries=2)
    assert "net::ERR_CONNECTION_CLOSED" in str(exc_info.value)
    assert page.calls == 3
    assert page.timeouts == [3000, 7000]

def test_reload_with_retry_network_error():
    from common import reload_with_retry
    from playwright.sync_api import Error as PlaywrightError

    class MockPage:
        def __init__(self):
            self.calls = 0
            self.timeouts = []
        def reload(self, wait_until="domcontentloaded", timeout=15000):
            self.calls += 1
            if self.calls == 1:
                raise PlaywrightError("Page.reload: net::ERR_CONNECTION_CLOSED")
            return None
        def wait_for_timeout(self, ms):
            self.timeouts.append(ms)

    page = MockPage()
    reload_with_retry(page, retries=2)
    assert page.calls == 2
    assert page.timeouts == [3000]

def test_reload_with_retry_raises_after_max_retries():
    import pytest
    from common import reload_with_retry
    from playwright.sync_api import Error as PlaywrightError

    class MockFailingPage:
        def __init__(self):
            self.calls = 0
            self.timeouts = []
        def reload(self, wait_until="domcontentloaded", timeout=15000):
            self.calls += 1
            raise PlaywrightError("Page.reload: net::ERR_CONNECTION_CLOSED")
        def wait_for_timeout(self, ms):
            self.timeouts.append(ms)

    page = MockFailingPage()
    with pytest.raises(PlaywrightError) as exc_info:
        reload_with_retry(page, retries=2)
    assert "net::ERR_CONNECTION_CLOSED" in str(exc_info.value)
    assert page.calls == 3
    assert page.timeouts == [3000, 7000]


def test_load_credentials_all_sites(tmp_path):
    import json
    import pytest
    from common import load_credentials

    creds_file = tmp_path / "credentials.json"
    data = {
        "user1": {
            "email": "user1@example.com",
            "doctorville": {"password": "pass_dv"},
            "keymedi": {"id": "km_id", "password": "pass_km"},
            "hmp": {"password": "pass_hmp"},
            "intermd": {"password": "pass_im"},
        },
        "user2": {
            "hmp": {"id": "custom_hmp", "password": "pass_hmp2"},
            "intermd": {"id": "custom_im", "password": "pass_im2"},
        },
    }
    creds_file.write_text(json.dumps(data), encoding="utf-8")

    # doctorville
    dv = load_credentials(creds_file, "user1", "doctorville")
    assert dv == {"email": "user1@example.com", "password": "pass_dv"}

    # keymedi
    km = load_credentials(creds_file, "user1", "keymedi")
    assert km == {"id": "km_id", "password": "pass_km"}

    # hmp default id and explicit id
    hmp1 = load_credentials(creds_file, "user1", "hmp")
    assert hmp1 == {"id": "user1", "password": "pass_hmp"}
    hmp2 = load_credentials(creds_file, "user2", "hmp")
    assert hmp2 == {"id": "custom_hmp", "password": "pass_hmp2"}

    # intermd default id and explicit id
    im1 = load_credentials(creds_file, "user1", "intermd")
    assert im1 == {"id": "user1", "password": "pass_im"}
    im2 = load_credentials(creds_file, "user2", "intermd")
    assert im2 == {"id": "custom_im", "password": "pass_im2"}

    # unknown site
    with pytest.raises(ValueError, match="지원하지 않는 사이트입니다"):
        load_credentials(creds_file, "user1", "unknown_site")

    # missing account
    with pytest.raises(KeyError, match="계정이 없습니다"):
        load_credentials(creds_file, "nonexistent", "doctorville")



