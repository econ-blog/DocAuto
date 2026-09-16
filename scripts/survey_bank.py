#!/usr/bin/env python3
"""세미나 설문 문제은행(survey_quiz_answers.json, survey_text_answers.json, survey_answers_legacy.json) 관리 모듈.

문항 정규화, canonical 대조, 답안 조회, 미등록 문항 등록, 레거시 정답 승격 및 페이지 해석(resolve_page) 기능을 제공한다.
브라우저나 Playwright 의존성이 전혀 없어 독립적으로 실행/검증 가능하다.
"""

import re
from pathlib import Path

import common

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_QUIZ_BANK_FILE = SCRIPT_DIR.parent / "survey_quiz_answers.json"
DEFAULT_TEXT_BANK_FILE = SCRIPT_DIR.parent / "survey_text_answers.json"
DEFAULT_LEGACY_BANK_FILE = SCRIPT_DIR.parent / "survey_answers_legacy.json"

PLACEHOLDER_MARKER = common.ANSWER_PLACEHOLDER_MARKER
BLANK_ANSWER_MARKER = "(빈칸)"
BANK_LABELS = {"quiz": "퀴즈", "text": "주관식"}
GENERAL_OPTION_INDEX = 1  # 0-based → 2번 보기

_QUIZ_BADGE_RE = re.compile(r"^\[\s*퀴즈\s*\]\s*")
_ANNOTATION_PATTERNS = (
    re.compile(r"\*?\(\s*(?:최소|최대)\s*\d+\s*개\s*선택\s*\)"),
    re.compile(r"\(\s*(?:복수\s*(?:응답|선택)|중복)\s*(?:가능)?\s*\)"),
)


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def strip_spaces(text: str) -> str:
    """공백을 전부 없앤 대조용 문자열. 사이트가 버튼 문구를 줄바꿈으로 쪼개도 걸린다."""
    return re.sub(r"\s+", "", text or "")


def is_quiz_badged(text: str) -> bool:
    """화면 텍스트가 `[퀴즈]` 배지로 시작하는지."""
    return bool(_QUIZ_BADGE_RE.match(normalize(text)))


def normalize_question(text: str) -> str:
    """문항 텍스트를 문제은행 키 형태로 정규화한다."""
    t = _QUIZ_BADGE_RE.sub("", normalize(text))
    return t.rstrip("*").strip()


def canonical_question(text: str) -> str:
    """대조 전용 키. 표기 흔들림(공백·대소문자·구두점·안내문구)을 제거한다."""
    t = normalize_question(text)
    for pat in _ANNOTATION_PATTERNS:
        t = pat.sub("", t)
    t = t.lower()
    return re.sub(r"[^0-9a-z가-힣]", "", t)


def build_canonical_index(bank: dict) -> dict:
    """canonical 키 → 값. 서로 다른 값으로 충돌하는 키는 버린다(오답 방지)."""
    index, conflicts = {}, set()
    for k, v in (bank or {}).items():
        ck = canonical_question(k)
        if not ck:
            continue
        if ck in index and index[ck] != v:
            conflicts.add(ck)
        else:
            index[ck] = v
    for ck in conflicts:
        index.pop(ck, None)
    return index


def _coerce_answer(value):
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, list):
        items = [str(v).strip() for v in value if str(v).strip()]
        if any(i == PLACEHOLDER_MARKER for i in items):
            return None
        return items or None
    return None


def load_bank(path: str | Path) -> dict:
    return common.read_json(path, default={})


def lookup_answer(bank: dict, question: str, index: dict = None):
    """문제은행에서 문항의 답을 찾는다. 미등록이면 None."""
    answer = _coerce_answer((bank or {}).get(normalize_question(question)))
    if answer is not None:
        return answer
    if index is None:
        index = build_canonical_index(bank or {})
    return _coerce_answer(index.get(canonical_question(question)))


def load_banks(
    quiz_path: str | Path = DEFAULT_QUIZ_BANK_FILE,
    text_path: str | Path = DEFAULT_TEXT_BANK_FILE,
    legacy_path: str | Path = DEFAULT_LEGACY_BANK_FILE,
) -> dict:
    """quiz / text / legacy 족보를 한 번에 읽어 묶는다."""
    return {
        "quiz": load_bank(quiz_path),
        "text": load_bank(text_path),
        "legacy": load_bank(legacy_path),
        "paths": {"quiz": Path(quiz_path), "text": Path(text_path)},
        "legacy_path": Path(legacy_path),
    }


def bank_has_key(bank: dict, question: str) -> bool:
    """값의 유무와 무관하게 키 자체가 족보에 있는지(정규화·canonical 양쪽으로)."""
    if normalize_question(question) in (bank or {}):
        return True
    ck = canonical_question(question)
    return bool(ck) and ck in {canonical_question(k) for k in (bank or {})}


def classify_question(q: dict, quiz_bank: dict = None) -> str:
    """문항 종류를 'quiz' | 'text' | 'general'로 판정한다."""
    if q.get("kind") == "input":
        return "text"
    if is_quiz_badged(q.get("question", "")):
        return "quiz"
    if quiz_bank and bank_has_key(quiz_bank, q.get("question", "")):
        return "quiz"
    return "general"


def lookup_in_banks(banks: dict, question: str, kind: str, indexes: dict = None):
    """종류별 족보 → legacy 폴백 순으로 답을 찾는다. (답, 출처) 또는 (None, None)."""
    if indexes is None:
        indexes = {k: build_canonical_index(banks.get(k, {})) for k in ("quiz", "text", "legacy")}
    answer = lookup_answer(banks.get(kind, {}), question, indexes.get(kind))
    if answer is not None:
        return answer, kind
    answer = lookup_answer(banks.get("legacy", {}), question, indexes.get("legacy"))
    return (answer, "legacy") if answer is not None else (None, None)


def match_option(answer: str, options: list[str]) -> int | None:
    """저장값에 해당하는 보기 인덱스. 판정 불가면 None."""
    a = normalize(answer)
    if not a:
        return None
    if a.isdigit():
        idx = int(a) - 1
        return idx if 0 <= idx < len(options) else None
    norm = [normalize(o) for o in options]
    ca = canonical_question(a)
    candidates = [(a, norm)]
    if ca:
        candidates.append((ca, [canonical_question(o) for o in options]))
    for needle, hay in candidates:
        exact = [i for i, o in enumerate(hay) if o == needle]
        if len(exact) == 1:
            return exact[0]
        hits = [i for i, o in enumerate(hay) if needle and needle in o]
        if len(hits) == 1:
            return hits[0]
    return None


def promotable_option_texts(indices: list[int], options: list[str]):
    """legacy 보기 번호를 승격용 보기 텍스트로 바꾼다. 왕복 검증 실패 시 None."""
    texts = []
    for idx in indices:
        if not 0 <= idx < len(options):
            return None
        text = options[idx]
        if not text or match_option(text, options) != idx:
            return None
        texts.append(text)
    if not texts:
        return None
    return texts[0] if len(texts) == 1 else texts


def _evict_legacy_keys(legacy: dict, questions: list[str]) -> dict:
    """승격된 문항의 키를 legacy에서 지운 사본을 만든다(표기 변형 키까지 함께)."""
    targets = {canonical_question(q) for q in questions}
    targets.discard("")
    return {k: v for k, v in (legacy or {}).items() if canonical_question(k) not in targets}


def apply_promotions(banks: dict, plan: list[dict]) -> dict:
    """plan의 승격 표시를 실제 파일에 반영한다. {족보: 승격건수}."""
    promotions = [s["promote"] for s in plan if s.get("promote")]
    if not promotions:
        return {}

    counts, moved = {}, []
    for name, path in banks.get("paths", {}).items():
        items = [p for p in promotions if p["bank"] == name]
        if not items:
            continue
        bank = load_bank(path)
        for p in items:
            bank[p["question"]] = p["answer"]
            moved.append(p["question"])
        common.write_json_atomic(path, dict(sorted(bank.items())))
        banks[name] = bank
        counts[name] = len(items)

    legacy_path = banks.get("legacy_path")
    if moved and legacy_path:
        pruned = _evict_legacy_keys(banks.get("legacy", {}), moved)
        if len(pruned) != len(banks.get("legacy", {})):
            common.write_json_atomic(Path(legacy_path), dict(sorted(pruned.items())))
            banks["legacy"] = pruned
    return counts


def _resolve_input(q, text, banks, indexes, _miss):
    """주관식(입력란) 파트를 푼다. 미등록이면 _miss를 부르고 None."""
    answer, source = lookup_in_banks(banks, text, "text", indexes)
    if answer is None:
        _miss("text", option_texts=[])
        return None
    if isinstance(answer, list):
        answer = " ".join(answer)
    if answer == BLANK_ANSWER_MARKER:
        answer = ""
    name = q.get("free_name") or q.get("name")
    step = {"kind": "input", "name": name, "value": answer}
    if source == "legacy":
        step["promote"] = {
            "bank": "text",
            "question": normalize_question(text),
            "answer": answer,
        }
    return step


def _resolve_choice(q, text, options, kind, banks, indexes, _miss):
    """선택 파트를 푼다. 미등록이면 _miss를 부르고 None."""
    if kind == "general":
        if len(options) <= GENERAL_OPTION_INDEX:
            _miss(None)
            return None
        return {"kind": "choice", "targets": [q["options"][GENERAL_OPTION_INDEX]]}

    answer, source = lookup_in_banks(banks, text, kind, indexes)
    if answer is None:
        _miss(kind)
        return None

    if answer == BLANK_ANSWER_MARKER:
        _miss(kind)
        return None

    if isinstance(answer, str) and "," in answer:
        parts = [p.strip() for p in answer.split(",")]
        answer = parts if all(p.isdigit() for p in parts if p) else answer
    wanted = answer if isinstance(answer, list) else [answer]
    indices = []
    for w in wanted:
        idx = match_option(w, options)
        if idx is None:
            _miss(kind)
            return None
        indices.append(idx)
    step = {"kind": "choice", "targets": [q["options"][i] for i in indices]}
    if source == "legacy":
        promoted = promotable_option_texts(indices, options)
        if promoted is not None:
            step["promote"] = {
                "bank": kind,
                "question": normalize_question(text),
                "answer": promoted,
            }
    return step


def resolve_page(questions: list[dict], banks: dict) -> tuple[list[dict], list[dict]]:
    """페이지의 문항들을 종류별 규칙으로 풀어 (적용계획, 미등록문항)을 만든다."""
    plan, missing = [], []
    indexes = {k: build_canonical_index(banks.get(k, {})) for k in ("quiz", "text", "legacy")}
    for q in questions:
        text = q.get("question", "")
        options = [normalize(o["text"]) for o in q.get("options", [])]
        form = q.get("kind")

        def _miss(bank_name, option_texts=None):
            opts = options if option_texts is None else option_texts
            missing.append({
                "question": normalize_question(text),
                "options": [f"{i + 1}. {o}" for i, o in enumerate(opts)],
                "option_texts": list(opts),
                "bank": bank_name,
            })

        if form == "unknown":
            continue

        kind = classify_question(q, banks.get("quiz", {}))

        if form not in ("input", "unknown"):
            step = _resolve_choice(q, text, options, kind, banks, indexes, _miss)
            if step is None:
                continue
            plan.append(step)

        if form in ("input", "mixed"):
            step = _resolve_input(q, text, banks, indexes, _miss)
            if step is not None:
                plan.append(step)
    return plan, missing


def placeholder_value(option_texts: list[str] | None):
    """족보에 깔아둘 미기입 값. 보기가 있으면 [표시, 보기…], 없으면 빈 문자열."""
    options = [normalize(o) for o in (option_texts or []) if normalize(o)]
    return [PLACEHOLDER_MARKER, *options] if options else ""


def add_missing_to_bank(bank_path: str | Path, missing: list[dict]) -> int:
    """미등록 문항을 미기입 값으로 문제은행에 추가한다. 추가된 개수 반환."""
    bank_path = Path(bank_path)
    bank = load_bank(bank_path)
    canon = {canonical_question(k) for k in bank}
    added = 0
    for m in missing:
        key = m["question"]
        ck = canonical_question(key)
        if key not in bank and ck not in canon:
            bank[key] = placeholder_value(m.get("option_texts"))
            canon.add(ck)
            added += 1
    if added:
        common.write_json_atomic(bank_path, dict(sorted(bank.items())))
    return added


def add_missing_to_banks(banks: dict, missing: list[dict]) -> dict:
    """미등록 문항을 `bank` 키가 가리키는 족보에 나눠 넣는다. {족보: 추가건수}."""
    counts = {}
    for name, path in banks.get("paths", {}).items():
        items = [m for m in missing if m.get("bank") == name]
        if items:
            counts[name] = add_missing_to_bank(path, items)
    return counts


def format_bank_counts(counts: dict) -> str:
    """{'quiz': 2, 'text': 1} → '퀴즈 2건, 주관식 1건'."""
    parts = [f"{BANK_LABELS.get(k, k)} {v}건" for k, v in sorted(counts.items()) if v]
    return ", ".join(parts) if parts else "추가 없음"
