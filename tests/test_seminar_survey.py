import json

import seminar_survey
from seminar_survey import (
    add_missing_to_bank,
    load_bank,
    lookup_answer,
    mark_survey_done,
    match_option,
    normalize_question,
    pending_seminar_ids,
    resolve_page,
)


def test_normalize_question_strips_quiz_badge_and_star():
    assert normalize_question("[퀴즈]렉사프로는 무엇입니까?*") == "렉사프로는 무엇입니까?"
    assert normalize_question("  렉사프로는   무엇입니까? ") == "렉사프로는 무엇입니까?"


def test_lookup_answer_treats_empty_as_missing():
    bank = {"질문": "", "질문2": [], "질문3": "답"}
    assert lookup_answer(bank, "질문") is None
    assert lookup_answer(bank, "질문2") is None
    assert lookup_answer(bank, "질문3") == "답"
    assert lookup_answer(bank, "없는질문") is None


def test_lookup_answer_matches_badged_question():
    bank = {"렉사프로는 무엇입니까?": "1번"}
    assert lookup_answer(bank, "[퀴즈]렉사프로는 무엇입니까?*") == "1번"


def test_match_option_unique_only():
    opts = ["체중이 증가했다", "체중 변화가 없었다", "혈당이 감소했다"]
    assert match_option("혈당이 감소", opts) == 2
    assert match_option("체중", opts) is None  # 두 보기에 걸림
    assert match_option("없음", opts) is None


def test_match_option_by_number():
    opts = ["가", "나", "다"]
    assert match_option("1", opts) == 0
    assert match_option("3", opts) == 2
    assert match_option("0", opts) is None
    assert match_option("4", opts) is None


def test_match_option_number_wins_over_text_containing_digit():
    # 보기 텍스트에 숫자가 섞여 있어도 "1"은 언제나 첫 번째 보기를 뜻한다.
    opts = ["1일 2회 복용", "1일 1회 복용"]
    assert match_option("1", opts) == 0


def _choice_q(text, options, base="userQuestions.0.optionIds"):
    return {
        "number": "1",
        "question": text,
        "kind": "choice",
        "name": base,
        "options": [{"text": o, "name": base, "value": str(i), "type": "radio"} for i, o in enumerate(options)],
    }


def _quiz_q(text, options, **kw):
    return _choice_q(f"[퀴즈] {text}", options, **kw)


def _banks(quiz=None, text=None, legacy=None):
    return {"quiz": quiz or {}, "text": text or {}, "legacy": legacy or {}, "paths": {}}


def test_resolve_page_all_known():
    q = _quiz_q("문항1", ["가", "나", "다"])
    plan, missing = resolve_page([q], _banks(quiz={"문항1": "나"}))
    assert missing == []
    assert plan[0]["kind"] == "choice"
    assert plan[0]["targets"][0]["value"] == "1"


def test_resolve_page_number_answer():
    q = _quiz_q("문항1", ["가", "나", "다"])
    plan, missing = resolve_page([q], _banks(quiz={"문항1": "2"}))
    assert missing == []
    assert plan[0]["targets"][0]["value"] == "1"


def test_resolve_page_comma_separated_numbers():
    q = _quiz_q("문항1", ["가", "나", "다"])
    plan, missing = resolve_page([q], _banks(quiz={"문항1": "1,3"}))
    assert missing == []
    assert [t["value"] for t in plan[0]["targets"]] == ["0", "2"]


def test_resolve_page_comma_in_text_answer_is_not_split():
    q = _quiz_q("문항1", ["가, 나 둘 다", "다"])
    plan, missing = resolve_page([q], _banks(quiz={"문항1": "가, 나 둘 다"}))
    assert missing == []
    assert plan[0]["targets"][0]["value"] == "0"


def test_resolve_page_reports_missing_with_options():
    q = _quiz_q("문항1", ["가", "나"])
    plan, missing = resolve_page([q], _banks())
    assert plan == []
    # 보기에는 입력할 번호가 붙고, 채워 넣을 족보 이름이 함께 나온다.
    assert missing == [{
        "question": "문항1",
        "options": ["1. 가", "2. 나"],
        "option_texts": ["가", "나"],
        "bank": "quiz",
    }]


def test_resolve_page_unmatched_stored_value_is_missing():
    q = _quiz_q("문항1", ["가", "나"])
    _, missing = resolve_page([q], _banks(quiz={"문항1": "다"}))
    assert len(missing) == 1


def test_resolve_page_multi_select_list_value():
    q = _quiz_q("문항1", ["가", "나", "다"])
    plan, missing = resolve_page([q], _banks(quiz={"문항1": ["가", "다"]}))
    assert missing == []
    assert [t["value"] for t in plan[0]["targets"]] == ["0", "2"]


def test_resolve_page_free_text():
    q = {"number": "1", "question": "의견을 적어주세요", "kind": "input", "name": "free.0", "options": []}
    plan, missing = resolve_page([q], _banks(text={"의견을 적어주세요": "좋았습니다"}))
    assert missing == []
    assert plan == [{"kind": "input", "name": "free.0", "value": "좋았습니다"}]


# --- 문항 3분류 -------------------------------------------------------------

def test_is_quiz_badged():
    from seminar_survey import is_quiz_badged
    assert is_quiz_badged("[퀴즈] 문항") is True
    assert is_quiz_badged("  [퀴즈]문항") is True
    assert is_quiz_badged("문항 [퀴즈] 중간") is False
    assert is_quiz_badged("만족하셨습니까?") is False


def test_classify_question_three_kinds():
    from seminar_survey import classify_question
    assert classify_question({"kind": "input", "question": "의견은?"}) == "text"
    assert classify_question(_quiz_q("문항1", ["가", "나"])) == "quiz"
    assert classify_question(_choice_q("만족하셨습니까?", ["가", "나"])) == "general"


def test_classify_question_uses_quiz_bank_when_badge_missing():
    # 배지가 빠져 렌더되는 세미나가 있다. 퀴즈 족보에 있으면 배지 없이도 퀴즈다.
    from seminar_survey import classify_question
    q = _choice_q("렉사프로는 무엇입니까?", ["가", "나"])
    assert classify_question(q, {"렉사프로는 무엇입니까?": "1"}) == "quiz"
    assert classify_question(q, {}) == "general"


def test_classify_question_quiz_bank_match_ignores_notation_drift():
    from seminar_survey import classify_question
    q = _choice_q("Edoxaban 30 mg 처방?", ["가", "나"])
    assert classify_question(q, {"edoxaban 30mg 처방?": ""}) == "quiz"


def test_general_question_always_picks_second_option():
    q = _choice_q("이번 세미나에 만족하셨습니까?", ["매우 만족", "만족", "보통"])
    plan, missing = resolve_page([q], _banks())
    assert missing == []
    assert [t["value"] for t in plan[0]["targets"]] == ["1"]


def test_general_question_ignores_legacy_bank():
    # 일반 문항은 족보를 아예 보지 않는다. legacy에 3번이 적혀 있어도 2번.
    q = _choice_q("만족하셨습니까?", ["가", "나", "다"])
    plan, _ = resolve_page([q], _banks(legacy={"만족하셨습니까?": "3"}))
    assert [t["value"] for t in plan[0]["targets"]] == ["1"]


def test_general_question_without_second_option_is_missing():
    q = _choice_q("보기가 하나뿐", ["가"])
    plan, missing = resolve_page([q], _banks())
    assert plan == []
    assert missing[0]["bank"] is None


def test_quiz_falls_back_to_legacy_bank():
    q = _quiz_q("문항1", ["가", "나", "다"])
    plan, missing = resolve_page([q], _banks(legacy={"문항1": "3"}))
    assert missing == []
    assert [t["value"] for t in plan[0]["targets"]] == ["2"]


def test_quiz_bank_wins_over_legacy():
    q = _quiz_q("문항1", ["가", "나", "다"])
    plan, _ = resolve_page([q], _banks(quiz={"문항1": "1"}, legacy={"문항1": "3"}))
    assert [t["value"] for t in plan[0]["targets"]] == ["0"]


def test_quiz_empty_placeholder_does_not_leak_to_general():
    # 퀴즈 족보에 빈 값으로만 등록된 문항은 "2번 찍기"로 새면 안 된다.
    q = _choice_q("렉사프로는 무엇입니까?", ["가", "나", "다"])
    plan, missing = resolve_page([q], _banks(quiz={"렉사프로는 무엇입니까?": ""}))
    assert plan == []
    assert missing[0]["bank"] == "quiz"


def test_free_text_falls_back_to_legacy():
    q = {"number": "1", "question": "의견을 적어주세요", "kind": "input", "name": "free.0", "options": []}
    plan, missing = resolve_page([q], _banks(legacy={"의견을 적어주세요": "좋았습니다"}))
    assert missing == []
    assert plan[0]["kind"] == "input"
    assert plan[0]["value"] == "좋았습니다"
    assert plan[0]["promote"] == {"bank": "text", "question": "의견을 적어주세요", "answer": "좋았습니다"}


def test_free_text_missing_targets_text_bank():
    q = {"number": "1", "question": "의견을 적어주세요", "kind": "input", "name": "free.0", "options": []}
    _, missing = resolve_page([q], _banks())
    assert missing[0]["bank"] == "text"


# --- legacy → 종류별 족보 승격 ---------------------------------------------

def test_legacy_number_is_promoted_as_option_text():
    q = _quiz_q("문항1", ["가", "나", "다"])
    plan, _ = resolve_page([q], _banks(legacy={"문항1": "3"}))
    assert plan[0]["promote"] == {"bank": "quiz", "question": "문항1", "answer": "다"}


def test_quiz_bank_hit_is_not_promoted():
    q = _quiz_q("문항1", ["가", "나", "다"])
    plan, _ = resolve_page([q], _banks(quiz={"문항1": "3"}))
    assert "promote" not in plan[0]


def test_promotion_survives_prefix_overlapping_options():
    # 한 보기가 다른 보기의 접두사여도 완전 일치가 먼저라 왕복이 성립한다.
    q = _quiz_q("문항1", ["체중 증가", "체중 증가 없음"])
    plan, _ = resolve_page([q], _banks(legacy={"문항1": "1"}))
    assert [t["value"] for t in plan[0]["targets"]] == ["0"]
    assert plan[0]["promote"]["answer"] == "체중 증가"


def test_promotion_skipped_when_option_text_is_ambiguous():
    # 같은 텍스트의 보기가 둘이면 텍스트로는 되찾을 수 없다 — 승격하지 않는다.
    q = _quiz_q("문항1", ["가", "가", "나"])
    plan, _ = resolve_page([q], _banks(legacy={"문항1": "1"}))
    assert [t["value"] for t in plan[0]["targets"]] == ["0"]
    assert "promote" not in plan[0]


def test_multi_select_legacy_promotes_list_of_texts():
    q = _quiz_q("문항1", ["가", "나", "다"])
    plan, _ = resolve_page([q], _banks(legacy={"문항1": "1,3"}))
    assert plan[0]["promote"]["answer"] == ["가", "다"]


def test_apply_promotions_moves_key_out_of_legacy(tmp_path):
    from seminar_survey import apply_promotions
    quiz_path = tmp_path / "survey_quiz_answers.json"
    legacy_path = tmp_path / "survey_answers_legacy.json"
    legacy = {"문항1": "3", "남는문항": "2"}
    legacy_path.write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")
    banks = {
        "quiz": {}, "text": {}, "legacy": dict(legacy),
        "paths": {"quiz": quiz_path}, "legacy_path": legacy_path,
    }
    plan = [{"kind": "choice", "targets": [],
             "promote": {"bank": "quiz", "question": "문항1", "answer": "다"}}]
    assert apply_promotions(banks, plan) == {"quiz": 1}
    assert load_bank(quiz_path) == {"문항1": "다"}
    # legacy에서는 빠진다 — 복사만 하면 legacy가 영영 줄지 않는다.
    assert load_bank(legacy_path) == {"남는문항": "2"}
    assert banks["legacy"] == {"남는문항": "2"}


def test_apply_promotions_evicts_notation_variant_legacy_keys(tmp_path):
    from seminar_survey import apply_promotions
    quiz_path = tmp_path / "survey_quiz_answers.json"
    legacy_path = tmp_path / "survey_answers_legacy.json"
    legacy_path.write_text(json.dumps({"Edoxaban 30 mg 처방?": "2"}, ensure_ascii=False), encoding="utf-8")
    banks = {
        "quiz": {}, "text": {}, "legacy": {"Edoxaban 30 mg 처방?": "2"},
        "paths": {"quiz": quiz_path}, "legacy_path": legacy_path,
    }
    plan = [{"kind": "choice", "targets": [],
             "promote": {"bank": "quiz", "question": "edoxaban 30mg 처방?", "answer": "나"}}]
    apply_promotions(banks, plan)
    assert load_bank(legacy_path) == {}


def test_apply_promotions_noop_without_promotions(tmp_path):
    from seminar_survey import apply_promotions
    assert apply_promotions({"paths": {}}, [{"kind": "choice", "targets": []}]) == {}


# --- 보기 텍스트 매칭의 표기 흔들림 -----------------------------------------

def test_match_option_tolerates_dash_and_spacing_variants():
    opts = ["5mg - 궤양성 대장염", "10mg - 류마티스 관절염"]
    assert match_option("10mg – 류마티스 관절염", opts) == 1
    opts2 = ["PPG(Photoplethysmography)", "Oscillometric"]
    assert match_option("PPG (Photoplethysmography)", opts2) == 0


def test_match_option_canonical_fallback_still_requires_uniqueness():
    # canonical로 뭉개면 두 보기가 같아진다 — 그 단계에서는 고르지 않는다.
    opts = ["가-나", "가 나"]
    assert match_option("가.나", opts) is None


def test_match_option_exact_normalized_wins_before_canonical():
    opts = ["가 나", "가나"]
    assert match_option("가나", opts) == 1


def test_add_missing_to_banks_routes_by_bank_key(tmp_path):
    from seminar_survey import add_missing_to_banks, format_bank_counts
    quiz_path = tmp_path / "survey_quiz_answers.json"
    text_path = tmp_path / "survey_text_answers.json"
    legacy_path = tmp_path / "survey_answers_legacy.json"
    legacy_path.write_text(json.dumps({"기존": "2"}), encoding="utf-8")
    banks = {
        "quiz": {}, "text": {}, "legacy": {"기존": "2"},
        "paths": {"quiz": quiz_path, "text": text_path},
    }
    counts = add_missing_to_banks(banks, [
        {"question": "퀴즈문항", "options": [], "bank": "quiz"},
        {"question": "주관식문항", "options": [], "bank": "text"},
        {"question": "보기없음", "options": [], "bank": None},
    ])
    assert counts == {"quiz": 1, "text": 1}
    assert load_bank(quiz_path) == {"퀴즈문항": ""}
    assert load_bank(text_path) == {"주관식문항": ""}
    # legacy는 읽기 전용 — 절대 갱신되지 않는다.
    assert load_bank(legacy_path) == {"기존": "2"}
    assert format_bank_counts(counts) == "퀴즈 1건, 주관식 1건"


def test_add_missing_to_bank_writes_options_as_placeholder(tmp_path):
    """보기가 있으면 값 자리에 [표시, 보기…]를 깔아 손으로 정답만 남기게 한다."""
    from seminar_survey import PLACEHOLDER_MARKER

    bank_path = tmp_path / "survey_answers.json"
    bank_path.write_text(json.dumps({"기존": "값"}), encoding="utf-8")
    added = add_missing_to_bank(bank_path, [
        {"question": "새문항", "options": ["1. 가", "2. 나"], "option_texts": ["가", "나"]},
        {"question": "기존", "options": []},
    ])
    assert added == 1
    assert load_bank(bank_path) == {
        "기존": "값",
        "새문항": [PLACEHOLDER_MARKER, "가", "나"],
    }


def test_add_missing_to_bank_writes_empty_for_text_question(tmp_path):
    """주관식은 고를 보기가 없으므로 종전대로 빈 문자열."""
    bank_path = tmp_path / "survey_text.json"
    added = add_missing_to_bank(bank_path, [{"question": "의견", "options": [], "option_texts": []}])
    assert added == 1
    assert load_bank(bank_path) == {"의견": ""}


def test_placeholder_value_is_never_submitted():
    """표시가 남아 있는 동안은 미등록이다(보기를 전부 제출하는 사고 방지)."""
    from seminar_survey import PLACEHOLDER_MARKER

    bank = {"문항1": [PLACEHOLDER_MARKER, "가", "나"]}
    assert lookup_answer(bank, "문항1") is None

    q = _quiz_q("문항1", ["가", "나"])
    plan, missing = resolve_page([q], _banks(quiz=bank))
    assert plan == []
    assert [m["question"] for m in missing] == ["문항1"]


def test_placeholder_trimmed_to_single_option_becomes_answer():
    """정답만 남기고 지우면 그 보기가 선택된다."""
    q = _quiz_q("문항1", ["가", "나"])
    plan, missing = resolve_page([q], _banks(quiz={"문항1": ["나"]}))
    assert missing == []
    assert plan == [{"kind": "choice", "targets": [q["options"][1]]}]


def test_pending_seminar_ids_excludes_done():
    state = {"accounts": {"bjh7790": {"entered": [1, 2, 3], "survey_done": [2]}}}
    assert pending_seminar_ids(state, "bjh7790") == [1, 3]
    assert pending_seminar_ids(state, "wonju") == []


def test_mark_survey_done_is_idempotent():
    state = {"accounts": {"bjh7790": {"entered": [1]}}}
    mark_survey_done(state, "bjh7790", 1)
    mark_survey_done(state, "bjh7790", "1")
    assert state["accounts"]["bjh7790"]["survey"] == {"1": "done"}


# --- canonical(유하게 대조) 매칭 -------------------------------------------

def test_canonical_ignores_space_case_and_punctuation():
    from seminar_survey import canonical_question
    assert canonical_question("edoxaban 30 mg 처방") == canonical_question("Edoxaban 30mg 처방")
    assert canonical_question("‘실제’ 진료에서") == canonical_question("'실제' 진료에서")


def test_canonical_strips_required_and_multiselect_annotations():
    from seminar_survey import canonical_question
    base = "주요 이유는 무엇입니까?"
    assert canonical_question(base + "*(최소 1개 선택)") == canonical_question(base)
    assert canonical_question(base + " (복수 선택 가능)*(최소 1개 선택)") == canonical_question(base)
    assert canonical_question(base + " (복수응답 가능)*(최대 6개 선택)") == canonical_question(base)


def test_lookup_answer_matches_spacing_and_case_variant():
    bank = {"Edoxaban 30mg 처방 경험이 있으십니까?": "2"}
    assert lookup_answer(bank, "edoxaban 30 mg 처방 경험이 있으십니까?") == "2"


def test_lookup_answer_matches_annotation_variant():
    bank = {"주요 이유는 무엇입니까?": "2"}
    assert lookup_answer(bank, "주요 이유는 무엇입니까? (복수 선택 가능)*(최소 1개 선택)") == "2"


def test_lookup_answer_exact_key_wins_over_canonical():
    bank = {"질문 A?": "1", "질문A?": "3"}
    assert lookup_answer(bank, "질문 A?") == "1"


def test_lookup_answer_rejects_conflicting_canonical_collision():
    # 표기만 다른 두 키가 서로 다른 답을 들고 있으면 어느 쪽도 쓰지 않는다.
    bank = {"질문 A?": "1", "질문A?": "3"}
    assert lookup_answer(bank, "질  문  A?") is None


def test_lookup_answer_does_not_fuzzy_match_similar_questions():
    # 실제 문제은행에 있는 difflib 유사도 0.92짜리 서로 다른 문항.
    bank = {"1차 예방 당뇨병 환자에서 스타틴 치료 시작 시 고려 기준은?": "2"}
    assert lookup_answer(bank, "1차 예방 중등도 위험군 환자에서 스타틴 치료 시작 시 고려 기준은?") is None


def test_add_missing_skips_canonical_duplicate(tmp_path):
    bank_path = tmp_path / "survey_answers.json"
    bank_path.write_text(json.dumps({"주요 이유는 무엇입니까?": ""}), encoding="utf-8")
    added = add_missing_to_bank(bank_path, [{"question": "주요 이유는 무엇입니까?*(최소 1개 선택)", "options": []}])
    assert added == 0
    assert list(load_bank(bank_path)) == ["주요 이유는 무엇입니까?"]


# --- 다음/제출하기 버튼 -----------------------------------------------------

def test_classify_advance_label():
    from seminar_survey import classify_advance_label
    assert classify_advance_label("다음") == "next"
    assert classify_advance_label("다음 페이지") == "next"
    assert classify_advance_label("제출하기") == "submit"
    assert classify_advance_label("설문 완료") == "submit"
    assert classify_advance_label("이전") is None
    assert classify_advance_label("임시저장") is None
    assert classify_advance_label("닫기") is None
    assert classify_advance_label("") is None


def test_page_fingerprint_distinguishes_pages():
    from seminar_survey import page_fingerprint
    p1 = [{"number": "1", "question": "문항1"}]
    p2 = [{"number": "2", "question": "문항2"}]
    assert page_fingerprint(p1) == page_fingerprint([{"number": "1", "question": " 문항1 *"}])
    assert page_fingerprint(p1) != page_fingerprint(p2)


def _static_item(text):
    """응답 컨트롤이 없는 항목 (2026-08-24 세미나 5587의 `<p>` 기반 항목)."""
    return {"number": "2", "question": text, "kind": "unknown", "name": "", "options": []}


def test_static_item_is_skipped_not_missing():
    # 답할 컨트롤이 없는 항목은 미등록으로 페이지를 막지 않고 그냥 건너뛴다.
    plan, missing = resolve_page([_static_item("안내문입니다.")], _banks())
    assert plan == []
    assert missing == []


def test_static_item_does_not_block_real_question_on_same_page():
    q = _choice_q("이번 세미나에 만족하셨습니까?", ["매우 만족", "만족", "보통"])
    questions = [q] + [_static_item(f"항목{i}") for i in range(10)]
    plan, missing = resolve_page(questions, _banks())
    assert missing == []
    assert len(plan) == 1
    assert [t["value"] for t in plan[0]["targets"]] == ["1"]


def test_option_less_choice_question_is_still_missing():
    # 보기가 1개뿐인 선택형은 여전히 미등록이다. static 건너뛰기와 혼동하지 않는다.
    plan, missing = resolve_page([_choice_q("보기가 하나뿐", ["가"])], _banks())
    assert plan == []
    assert missing[0]["bank"] is None


# ---------------------------------------------------------------------------
# dismiss_alerts — 숨은 닫기 버튼 (세미나 5609, 2026-08-31)
# ---------------------------------------------------------------------------

class _FakeButton:
    def __init__(self, visible, on_click=None, label=""):
        self._visible = visible
        self._on_click = on_click
        self.label = label
        self.clicks = 0

    def is_visible(self):
        return self._visible

    def click(self, timeout=None):
        self.clicks += 1
        if self._on_click:
            self._on_click()


class _FakeLocator:
    def __init__(self, buttons_by_selector, text="알림"):
        self._by_selector = buttons_by_selector
        self._text = text

    def inner_text(self):
        return self._text

    def locator(self, selector):
        return _FakeButtonList(self._by_selector.get(selector, []))


class _FakeButtonList:
    def __init__(self, buttons):
        self._buttons = buttons

    def all(self):
        return list(self._buttons)


class _FakeDialogRoot:
    def __init__(self, page):
        self._page = page

    def count(self):
        return 1 if self._page.dialog_open else 0

    @property
    def first(self):
        return self._page.dialog


class _FakeKeyboard:
    def __init__(self, page):
        self._page = page
        self.pressed = []

    def press(self, key):
        self.pressed.append(key)


class _FakeSurveyPage:
    def __init__(self, dialog, dialog_open=True):
        self.dialog = dialog
        self.dialog_open = dialog_open
        self.keyboard = _FakeKeyboard(self)
        self.waits = []

    def locator(self, selector):
        return _FakeDialogRoot(self)

    def wait_for_timeout(self, ms):
        self.waits.append(ms)


def test_dismiss_alerts_skips_hidden_close_button():
    """숨은 button.dialog__close.hidden을 집어 30초 대기하다 죽던 회귀."""
    hidden = _FakeButton(visible=False, label="hidden close")
    page_holder = {}

    def close():
        page_holder["page"].dialog_open = False

    visible = _FakeButton(visible=True, on_click=close, label="확인")
    dialog = _FakeLocator({
        'button:has-text("확인"), button:has-text("닫기")': [hidden, visible],
        "button": [hidden, visible],
    })
    page = _FakeSurveyPage(dialog)
    page_holder["page"] = page

    closed = seminar_survey.dismiss_alerts(page)

    assert hidden.clicks == 0, "숨은 버튼은 클릭 후보가 되면 안 된다"
    assert visible.clicks == 1
    assert closed == ["알림"]


def test_dismiss_alerts_falls_back_to_escape():
    """보이는 버튼이 하나도 없으면 Esc로 시도하고, 예외를 던지지 않는다."""
    hidden = _FakeButton(visible=False)
    dialog = _FakeLocator({
        'button:has-text("확인"), button:has-text("닫기")': [],
        "button": [hidden],
    })
    page = _FakeSurveyPage(dialog)

    class _EscapeKeyboard(_FakeKeyboard):
        def press(self, key):
            super().press(key)
            page.dialog_open = False

    page.keyboard = _EscapeKeyboard(page)

    closed = seminar_survey.dismiss_alerts(page)

    assert page.keyboard.pressed == ["Escape"]
    assert closed == ["알림"]


def test_dismiss_alerts_gives_up_without_raising():
    """닫을 수단이 전혀 없어도 설문 전체를 failed로 만들지 않는다."""
    dialog = _FakeLocator({
        'button:has-text("확인"), button:has-text("닫기")': [],
        "button": [],
    })
    page = _FakeSurveyPage(dialog)

    assert seminar_survey.dismiss_alerts(page) == []


def test_dismiss_alerts_click_timeout_tries_next_candidate():
    """첫 후보 클릭이 타임아웃해도 다음 후보로 넘어간다."""
    page_holder = {}

    def boom():
        raise RuntimeError("Timeout 3000ms exceeded")

    def close():
        page_holder["page"].dialog_open = False

    flaky = _FakeButton(visible=True, on_click=boom)
    good = _FakeButton(visible=True, on_click=close)
    dialog = _FakeLocator({
        'button:has-text("확인"), button:has-text("닫기")': [flaky, good],
        "button": [flaky, good],
    })
    page = _FakeSurveyPage(dialog)
    page_holder["page"] = page

    closed = seminar_survey.dismiss_alerts(page)

    assert flaky.clicks == 1 and good.clicks == 1
    assert closed == ["알림"]
