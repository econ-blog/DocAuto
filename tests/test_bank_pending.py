"""족보 미기입 조회 스크립트.

판정 규칙을 자체 구현하지 않고 각 모듈 함수를 그대로 쓰는지까지 확인한다 —
규칙이 갈라지면 스크립트만 "다 찼다"고 하고 런은 계속 막힌다.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import bank_pending  # noqa: E402
import common  # noqa: E402

MARKER = common.ANSWER_PLACEHOLDER_MARKER


def _bank(tmp_path, monkeypatch, name, filename, data):
    (tmp_path / filename).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(bank_pending, "ROOT", tmp_path)
    return name


def test_quiz_bank_lists_seeded_options(tmp_path, monkeypatch):
    _bank(tmp_path, monkeypatch, "quiz", "quiz_answers.json", {
        "채워진제품": {"문항A": "정답"},
        "미기입제품": {"문항B": [MARKER, "가", "나"]},
    })
    rows = bank_pending.pending("quiz")
    assert [r["product"] for r in rows] == ["미기입제품"]
    assert rows[0]["options"] == ["가", "나"]


def test_quiz_bank_multi_line_list_is_still_pending(tmp_path, monkeypatch):
    """닥터빌은 보기가 한 줄만 남아야 정답이다(표시줄을 지워도 두 줄이면 미기입)."""
    _bank(tmp_path, monkeypatch, "quiz", "quiz_answers.json", {
        "제품": {"문항": ["가", "나"]},
    })
    assert len(bank_pending.pending("quiz")) == 1


def test_survey_text_empty_string_is_pending(tmp_path, monkeypatch):
    _bank(tmp_path, monkeypatch, "survey_text", "survey_text_answers.json", {
        "적어주세요": "",
        "이건 채움": "그럭저럭임",
        "빈칸 제출 지시": "(빈칸)",
    })
    rows = bank_pending.pending("survey_text")
    assert [r["question"] for r in rows] == ["적어주세요"]
    assert rows[0]["options"] == []


def test_survey_quiz_multi_select_list_is_not_pending(tmp_path, monkeypatch):
    """설문은 복수 선택이 있어 여러 줄도 정답일 수 있다 — 닥터빌과 규칙이 다르다."""
    _bank(tmp_path, monkeypatch, "survey_quiz", "survey_quiz_answers.json", {
        "복수선택": ["1", "3"],
        "아직": [MARKER, "가", "나"],
    })
    assert [r["question"] for r in bank_pending.pending("survey_quiz")] == ["아직"]
