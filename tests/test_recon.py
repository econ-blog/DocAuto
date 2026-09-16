import json
import os
from pathlib import Path
from common import is_recon_enabled
from recon import dump_recon_data

def test_is_recon_enabled(monkeypatch):
    monkeypatch.setenv("RECON", "1")
    assert is_recon_enabled() is True
    monkeypatch.delenv("RECON", raising=False)
    assert is_recon_enabled() is False

def test_dump_recon_data_creates_json_file():
    data = {"url": "https://example.com/survey", "body": "Survey complete"}
    file_path = dump_recon_data("R1", data, page=None)
    
    assert isinstance(file_path, str)
    assert os.path.exists(file_path)
    path_obj = Path(file_path)
    
    assert path_obj.name.startswith("recon_R1_")
    assert path_obj.name.endswith(".json")
    
    # Check outputs are strictly contained within scripts/logs/
    expected_dir = Path(__file__).resolve().parent.parent / "scripts" / "logs"
    assert path_obj.parent.resolve() == expected_dir.resolve()
    
    with open(file_path, "r", encoding="utf-8") as f:
        loaded = json.load(f)
    assert loaded["url"] == "https://example.com/survey"
    assert loaded["body"] == "Survey complete"


def test_list_dom_js_is_raw_string():
    """일반 문자열이면 \\d가 파이썬 단계에서 깨져 날짜 탐지가 전부 무력화된다."""
    import recon
    assert r"\d{4}" in recon.LIST_DOM_JS
    assert r"\s+" in recon.LIST_DOM_JS


def test_summarize_r5_reports_out_of_anchor_date():
    """정찰의 목적은 '앵커 밖 어느 노드에 날짜가 있나' 한 줄이다."""
    import recon
    out = recon.summarize_r5({
        "list_ready": True, "anchorCount": 66, "anchorsWithApply": 0, "anchorsWithDate": 0,
        "dateNodes": [{"sel": "p.date", "parent": "div.group", "text": "2026.09.16 (수)"}],
        "items": [{
            "index": 0, "seminarId": "5700", "innerHasDate": False, "innerText": "13:00 ~14:00",
            "ancestors": [{"depth": 1, "sel": "li.item", "ownText": "", "ownHasDate": False,
                           "prevSiblings": [{"sel": "p.date", "text": "2026.09.16 (수)", "hasDate": True}]}],
        }],
    })
    assert "p.date" in out
    assert "앞형제" in out
    assert "with_date=0" in out


def test_summarize_r5_says_so_when_no_date_anywhere():
    import recon
    out = recon.summarize_r5({"list_ready": True, "anchorCount": 66, "anchorsWithApply": 0,
                              "anchorsWithDate": 0, "dateNodes": [], "items": []})
    assert "없음" in out


def test_summarize_r5_reports_operational_scan():
    """픽스 검증의 핵심 숫자(unparsed / today_rows)가 요약에 없으면 정찰 의미가 없다."""
    import recon
    out = recon.summarize_r5({
        "list_ready": True, "anchorCount": 67, "anchorsWithApply": 3, "anchorsWithDate": 0,
        "dateNodes": [], "items": [], "dayLink": [], "headerContext": None,
        "scan": {"items": 67, "unparsed": 0, "today_rows": 4, "applicable": 3,
                 "with_list_date": 67, "sample_raw": ["9/16 수요일 18:30 ~20:00"],
                 "sample_rows": [{"start": "2026-09-16(수) 18:30 ~ 20:00", "title": "Breathe Well"}]},
    })
    assert "unparsed=0" in out
    assert "today_rows=4" in out
    assert "Breathe Well" in out
