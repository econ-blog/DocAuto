import json
from doctorville import (
    consolidate_products,
    load_quiz_answers_legacy,
    lookup_legacy_seq,
    lookup_product_bank,
    normalize_product,
    resolve_product_key,
    _record_answers,
    _record_missing_placeholders,
    _evict_answers,
    _evict_legacy_answers,
    coerce_bank_answer,
    product_has_answer,
    PLACEHOLDER_MARKER,
)


# --- 미등록 문항 자리표시자 -------------------------------------------------

def test_coerce_bank_answer_rejects_untrimmed_placeholder():
    """표시줄이 남아 있으면 미등록 — 보기를 찍어서 제출하는 사고를 막는다."""
    assert coerce_bank_answer([PLACEHOLDER_MARKER, "가", "나"]) is None


def test_coerce_bank_answer_rejects_multiple_remaining_options():
    """표시줄만 지우고 보기를 여러 줄 남겼으면 아직 고른 게 아니다."""
    assert coerce_bank_answer(["가", "나"]) is None


def test_coerce_bank_answer_accepts_single_trimmed_option():
    assert coerce_bank_answer(["나"]) == "나"
    assert coerce_bank_answer("  나  ") == "나"
    assert coerce_bank_answer("") is None


def test_product_has_answer_ignores_placeholder_only_product():
    assert product_has_answer({"Q1": [PLACEHOLDER_MARKER, "가"]}) is False
    assert product_has_answer({"Q1": [PLACEHOLDER_MARKER, "가"], "Q2": "나"}) is True


def test_match_quiz_bank_false_when_only_placeholders():
    """자리표시자만 있는 제품을 already_done으로 세면 precheck 알림이 사라진다."""
    from doctorville import match_quiz_bank

    bank = {"펙수클루정": {"Q": [PLACEHOLDER_MARKER, "가", "나"]}}
    assert match_quiz_bank("펙수클루정40mg", bank, {}) is False
    bank["펙수클루정"]["Q"] = ["나"]
    assert match_quiz_bank("펙수클루정40mg", bank, {}) is True


def test_record_missing_placeholders_seeds_options(tmp_path, monkeypatch):
    bank_file = tmp_path / "quiz_answers.json"
    bank_file.write_text(json.dumps({"프리스타일 리브레": {"Q1": "A1"}}), encoding="utf-8")
    monkeypatch.setattr("doctorville.QUIZ_ANSWERS_PATH", bank_file)

    added = _record_missing_placeholders(
        "프리스타일리브레", [{"question": "Q2", "options": ["1. 가", "2. 나"]}]
    )
    assert added == 1
    # 새 중복 제품 키를 만들지 않는다.
    assert json.loads(bank_file.read_text(encoding="utf-8")) == {
        "프리스타일 리브레": {"Q1": "A1", "Q2": [PLACEHOLDER_MARKER, "1. 가", "2. 나"]}
    }


def test_record_missing_placeholders_never_overwrites_existing(tmp_path, monkeypatch):
    """매칭에 실패했더라도 사람이 넣어둔 값을 자리표시자로 덮지 않는다."""
    bank_file = tmp_path / "quiz_answers.json"
    bank_file.write_text(json.dumps({"에빅사": {"Q1": "옛정답"}}), encoding="utf-8")
    monkeypatch.setattr("doctorville.QUIZ_ANSWERS_PATH", bank_file)

    added = _record_missing_placeholders("에빅사", [{"question": "Q1", "options": ["가", "나"]}])
    assert added == 0
    assert json.loads(bank_file.read_text(encoding="utf-8")) == {"에빅사": {"Q1": "옛정답"}}


# --- 제품명 표기 흔들림 ------------------------------------------------------

def test_normalize_product_collapses_space_and_punctuation():
    assert normalize_product("프리스타일 리브레") == normalize_product("프리스타일리브레")
    assert normalize_product("더-스피로킷") == normalize_product("더스피로킷")


def test_normalize_product_keeps_suffix_distinct():
    # "아림시스"와 "아림시스주"는 서로 다른 제품이고 정답도 다르다. 합치면 오답.
    assert normalize_product("아림시스") != normalize_product("아림시스주")
    assert normalize_product("리토바") != normalize_product("리토바젯")


def test_lookup_product_bank_finds_spacing_variant():
    answers = {"프리스타일 리브레": {"Q1": "A1"}}
    assert lookup_product_bank(answers, "프리스타일리브레") == {"Q1": "A1"}


def test_lookup_product_bank_merges_duplicate_keys():
    answers = {"프리스타일리브레": {"Q1": "A1"}, "프리스타일 리브레": {"Q2": "A2"}}
    assert lookup_product_bank(answers, "프리스타일 리브레") == {"Q1": "A1", "Q2": "A2"}


def test_lookup_product_bank_does_not_borrow_from_other_product():
    answers = {"아림시스": {"Q1": "A1"}}
    assert lookup_product_bank(answers, "아림시스주") == {}


def test_lookup_legacy_seq_finds_variant():
    assert lookup_legacy_seq({"더-스피로킷": "342"}, "더스피로킷") == "342"


def test_lookup_legacy_seq_refuses_conflicting_variants():
    # 같은 제품인데 시퀀스가 다르다 — 어느 쪽이 맞는지 알 수 없으므로 찍지 않는다.
    legacy = {"더-스피로킷": "342", "더스피로킷": "324"}
    assert lookup_legacy_seq(legacy, "더 스피로킷") is None


def test_lookup_legacy_seq_exact_key_still_wins_over_conflict():
    # 렌더된 이름이 키와 정확히 일치하면 그 값을 쓴다(lookup_answer와 같은 우선순위).
    # 충돌 감지는 어느 키와도 정확히 일치하지 않을 때만 작동한다.
    legacy = {"더-스피로킷": "342", "더스피로킷": "324"}
    assert lookup_legacy_seq(legacy, "더스피로킷") == "324"


def test_lookup_legacy_seq_accepts_agreeing_variants():
    legacy = {"프리스타일리브레": "111", "프리스타일 리브레": "111"}
    assert lookup_legacy_seq(legacy, "프리스타일 리브레") == "111"


def test_resolve_product_key_prefers_exact():
    data = {"시너지아정": {}, "시너지아 정": {}}
    assert resolve_product_key(data, "시너지아 정") == "시너지아 정"
    assert resolve_product_key(data, "없는제품") == "없는제품"


def test_consolidate_products_merges_into_richest_key():
    data = {"프리스타일리브레": {}, "프리스타일 리브레": {"Q1": "A1", "Q2": "A2"}}
    assert consolidate_products(data) == {"프리스타일 리브레": {"Q1": "A1", "Q2": "A2"}}


def test_consolidate_products_leaves_distinct_products_alone():
    data = {"아림시스": {"Q1": "A1"}, "아림시스주": {"Q1": "B1"}}
    assert consolidate_products(data) == data


def test_record_answers_writes_into_existing_variant_key(tmp_path, monkeypatch):
    bank_file = tmp_path / "quiz_answers.json"
    bank_file.write_text(json.dumps({"프리스타일 리브레": {"Q1": "A1"}}), encoding="utf-8")
    monkeypatch.setattr("doctorville.QUIZ_ANSWERS_PATH", bank_file)

    _record_answers("프리스타일리브레", [("Q2", "A2")])
    updated = json.loads(bank_file.read_text(encoding="utf-8"))
    # 새 중복 키를 만들지 않는다.
    assert updated == {"프리스타일 리브레": {"Q1": "A1", "Q2": "A2"}}


def test_evict_legacy_answers_removes_all_variants(tmp_path, monkeypatch):
    legacy_file = tmp_path / "quiz_answers_legacy.json"
    legacy_file.write_text(
        json.dumps({"더-스피로킷": "342", "더스피로킷": "324", "우루사": "222"}), encoding="utf-8"
    )
    monkeypatch.setattr("doctorville.LEGACY_ANSWERS_PATH", legacy_file)

    _evict_legacy_answers("더 스피로킷")
    assert json.loads(legacy_file.read_text(encoding="utf-8")) == {"우루사": "222"}


def test_load_quiz_answers_legacy(tmp_path, monkeypatch):
    legacy_file = tmp_path / "quiz_answers_legacy.json"
    legacy_file.write_text(json.dumps({"에빅사": "111"}), encoding="utf-8")
    monkeypatch.setattr("doctorville.LEGACY_ANSWERS_PATH", legacy_file)
    
    data = load_quiz_answers_legacy()
    assert data == {"에빅사": "111"}

def test_record_answers(tmp_path, monkeypatch):
    bank_file = tmp_path / "quiz_answers.json"
    bank_file.write_text(json.dumps({"에빅사": {"Q1": "A1"}}), encoding="utf-8")
    monkeypatch.setattr("doctorville.QUIZ_ANSWERS_PATH", bank_file)

    # Empty pairs should not touch file
    _record_answers("에빅사", [])
    
    _record_answers("에빅사", [("Q2", "A2")])
    content = bank_file.read_text(encoding="utf-8")
    updated = json.loads(content)
    assert updated["에빅사"] == {"Q1": "A1", "Q2": "A2"}
    assert content.endswith("\n")

def test_evict_answers(tmp_path, monkeypatch):
    bank_file = tmp_path / "quiz_answers.json"
    bank_file.write_text(json.dumps({"펙수클루정": {"Q1": "WRONG_A1", "Q2": "RIGHT_A2"}}), encoding="utf-8")
    monkeypatch.setattr("doctorville.QUIZ_ANSWERS_PATH", bank_file)

    # Evict Q1 from bank
    _evict_answers("펙수클루정", ["Q1"])
    content = bank_file.read_text(encoding="utf-8")
    updated = json.loads(content)
    assert updated["펙수클루정"] == {"Q2": "RIGHT_A2"}
    assert content.endswith("\n")

def test_evict_legacy_answers(tmp_path, monkeypatch):
    legacy_file = tmp_path / "quiz_answers_legacy.json"
    legacy_file.write_text(json.dumps({"에빅사": "111", "우루사": "222"}), encoding="utf-8")
    monkeypatch.setattr("doctorville.LEGACY_ANSWERS_PATH", legacy_file)

    _evict_legacy_answers("에빅사")
    content = legacy_file.read_text(encoding="utf-8")
    updated = json.loads(content)
    assert updated == {"우루사": "222"}
    assert content.endswith("\n")


from unittest.mock import MagicMock, patch
import sys
import doctorville

def test_task_quiz_no_answer_payload(monkeypatch, tmp_path):
    bank_file = tmp_path / "quiz_answers.json"
    bank_file.write_text("{}", encoding="utf-8")
    monkeypatch.setattr("doctorville.QUIZ_ANSWERS_PATH", bank_file)

    mock_page = MagicMock()

    monkeypatch.setattr("doctorville._get_today_quiz_product", lambda p: ("테스트제품", "123", "999"))
    monkeypatch.setattr("doctorville._get_product_pid", lambda p, prod: "123")
    monkeypatch.setattr("doctorville.load_quiz_answers", lambda: {})
    monkeypatch.setattr("doctorville.load_quiz_answers_legacy", lambda: {})
    monkeypatch.setattr("doctorville.save_screenshot", lambda p, tag: "dummy.png")

    mock_banner = MagicMock()
    mock_banner.get_attribute.return_value = ""

    mock_layer = MagicMock()

    mock_q_area = MagicMock()
    mock_q_area.locator.side_effect = lambda sel: (
        MagicMock(inner_text=lambda: "Q1. 질문내용") if ".txt_question" in sel
        else MagicMock(count=lambda: 2, nth=lambda i: MagicMock(
            locator=lambda s: (
                MagicMock(inner_text=lambda: f"보기{i+1}") if "label" in s
                else MagicMock(get_attribute=lambda attr: f"val{i+1}")
            )
        ))
    )

    mock_q_areas = MagicMock()
    mock_q_areas.count.return_value = 1
    mock_q_areas.nth.return_value = mock_q_area

    mock_close_btn = MagicMock()
    mock_close_btn.is_visible.return_value = False

    def layer_locator(sel):
        if ".question_area" in sel:
            return mock_q_areas
        if ".btn_cancel" in sel or ".btn_close" in sel:
            return mock_close_btn
        return MagicMock()

    mock_layer.locator.side_effect = layer_locator

    def page_locator(sel):
        if sel == "#btn_quiz_banner":
            return mock_banner
        if sel == "#quizLayerPop":
            return mock_layer
        return MagicMock()

    mock_page.locator.side_effect = page_locator

    res = doctorville.task_quiz(mock_page, {"email": "a", "password": "b"})

    assert res["status"] == "no_answer"
    assert "questions" in res
    assert res["questions"] == [
        {
            "question": "Q1. 질문내용",
            "options": ["보기1", "보기2"],
            "recorded_answer_not_matched": None,
        }
    ]
    assert "\n" not in res["message"]



