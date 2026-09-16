#!/usr/bin/env python3
"""닥터빌 퀴즈 정답/문제은행(quiz_answers.json, quiz_answers_legacy.json) 관리 모듈.

제품명 정규화, 문제은행 조회, 답안 제출 텍스트 파싱, 정답 영속화 및 레거시 승격 기능을 제공한다.
브라우저/Playwright 의존성이 없어 독립적으로 실행/검증 가능하다.
"""

import re
from pathlib import Path

import common

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
QUIZ_ANSWERS_PATH = REPO_ROOT / "quiz_answers.json"
LEGACY_ANSWERS_PATH = REPO_ROOT / "quiz_answers_legacy.json"
PLACEHOLDER_MARKER = common.ANSWER_PLACEHOLDER_MARKER


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", "", text or "").lower()


def normalize_product(name: str) -> str:
    """제품명 대조 키. 공백·구두점·대소문자 차이를 제거한다.

    사이트가 같은 제품을 날마다 다르게 렌더한다(실측: "프리스타일리브레" /
    "프리스타일 리브레", "더-스피로킷" / "더스피로킷"). 문자열 완전 일치로 조회하면
    한 표기로 배운 답이 다른 표기에서 안 보여 `no_answer`로 떨어진다.

    접미사가 다른 이름은 **합치지 않는다** — "아림시스"와 "아림시스주"는 실제로
    서로 다른 제품이고 각각 다른 정답을 들고 있다. 부분 포함 매칭은 그래서 안 쓴다.
    """
    return re.sub(r"[^0-9a-z가-힣]", "", (name or "").lower())


def resolve_product_key(data: dict, product: str) -> str:
    """렌더된 제품명에 대응하는 기존 키. 없으면 렌더된 이름을 그대로 쓴다."""
    if product in data:
        return product
    np = normalize_product(product)
    if np:
        for k in data:
            if normalize_product(k) == np:
                return k
    return product


def lookup_product_bank(answers: dict, product: str) -> dict:
    """제품 문제은행. 표기만 다른 중복 키가 남아 있으면 합쳐서 돌려준다."""
    np = normalize_product(product)
    merged = {}
    if np:
        for k in sorted(answers):
            v = answers[k]
            if isinstance(v, dict) and normalize_product(k) == np:
                merged.update(v)
    exact = answers.get(product)
    if isinstance(exact, dict):
        merged.update(exact)
    return merged


def lookup_legacy_seq(legacy: dict, product: str) -> str | None:
    """legacy 시퀀스. 표기 변형 키가 서로 다른 값이면 어느 쪽도 쓰지 않는다.

    실제로 충돌이 있다: "더-스피로킷"=342 / "더스피로킷"=324. 둘 중 하나는 틀린
    값이고 판별할 방법이 없으므로 찍지 않고 `no_answer`로 보낸다.
    """
    seq = legacy.get(product)
    if isinstance(seq, str):
        return seq
    np = normalize_product(product)
    if not np:
        return None
    hits = {v for k, v in legacy.items() if isinstance(v, str) and normalize_product(k) == np}
    return hits.pop() if len(hits) == 1 else None


def consolidate_products(data: dict) -> dict:
    """표기만 다른 중복 제품 키를 하나로 합친다. 답이 많은 키가 대표가 된다."""
    groups = {}
    for k in data:
        groups.setdefault(normalize_product(k), []).append(k)
    out = {}
    for keys in groups.values():
        if len(keys) == 1:
            out[keys[0]] = data[keys[0]]
            continue
        dicts = [k for k in keys if isinstance(data[k], dict)]
        if not dicts:
            out[sorted(keys)[0]] = data[sorted(keys)[0]]
            continue
        winner = max(sorted(dicts), key=lambda k: len(data[k]))
        merged = {}
        for k in sorted(dicts):
            if k != winner:
                merged.update(data[k])
        merged.update(data[winner])
        out[winner] = merged
    return out


def coerce_bank_answer(value):
    """족보 값 → 제출에 쓸 정답 텍스트. 아직 못 고르는 값이면 None.

    미등록 문항은 값 자리에 `[표시줄, 보기…]`가 깔려 있다. 표시줄이 남아 있거나
    보기가 여러 줄 남아 있으면 사람이 아직 안 고른 것이다 — 찍지 않고 `no_answer`로
    보낸다. 정답 한 줄만 남기면 그 보기가 곧 정답이다.
    """
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, list):
        items = [str(v).strip() for v in value if str(v).strip()]
        if len(items) != 1 or items[0] == PLACEHOLDER_MARKER:
            return None
        return items[0]
    return None


def product_has_answer(bank_entry) -> bool:
    """제품 문제은행에 실제로 쓸 수 있는 정답이 하나라도 있는가."""
    if not isinstance(bank_entry, dict):
        return False
    return any(coerce_bank_answer(v) is not None for v in bank_entry.values())


def match_quiz_bank(product_name: str, bank: dict, legacy: dict) -> bool:
    norm_p = normalize_text(product_name)
    if not norm_p:
        return False
    # 미기입 자리표시자만 들어 있는 제품 키는 "정답 있음"이 아니다. 이걸 세면
    # precheck가 익일 퀴즈를 already_done으로 덮어 알림이 안 간다.
    usable_bank = [k for k, v in bank.items() if product_has_answer(v)]
    for k in usable_bank + list(legacy.keys()):
        norm_k = normalize_text(k)
        if norm_k and norm_k in norm_p:
            return True
    return False


def load_quiz_answers(path: Path | str = None) -> dict:
    return common.read_json(path or QUIZ_ANSWERS_PATH, default={})


def save_quiz_answers(bank: dict, path: Path | str = None) -> None:
    common.write_json_atomic(path or QUIZ_ANSWERS_PATH, bank)


def load_legacy_answers(path: Path | str = None) -> dict:
    return common.read_json(path or LEGACY_ANSWERS_PATH, default={})


def save_legacy_answers(legacy: dict, path: Path | str = None) -> None:
    common.write_json_atomic(path or LEGACY_ANSWERS_PATH, legacy)


def _record_answers(product: str, pairs: list[tuple[str, str]], path: Path | str = None) -> None:
    if not pairs:
        return
    p = Path(path or QUIZ_ANSWERS_PATH)
    data = consolidate_products(load_quiz_answers(p))
    key = resolve_product_key(data, product)
    prod_dict = data.setdefault(key, {})
    for q_text, ans_text in pairs:
        prod_dict[q_text] = ans_text
    common.write_json_atomic(p, data)


def _record_missing_placeholders(product: str, missing: list[dict], path: Path | str = None) -> int:
    """미등록 문항을 보기와 함께 문제은행에 깔아둔다. 추가된 문항 수 반환.

    이미 값이 있는 문항은 건드리지 않는다 — 자리표시자로 덮으면 사람이 넣어둔
    정답이 날아간다. 매칭에 실패한 기존 정답도 그대로 두고 `_evict_answers`에 맡긴다.
    """
    seeds = [
        (m["question"], [str(o) for o in m.get("options") or []])
        for m in missing
        if m.get("question") and m.get("options")
    ]
    if not seeds:
        return 0

    p = Path(path or QUIZ_ANSWERS_PATH)
    data = consolidate_products(load_quiz_answers(p))
    key = resolve_product_key(data, product)
    prod_dict = data.setdefault(key, {})
    added = 0
    for q_text, options in seeds:
        if q_text in prod_dict:
            continue
        prod_dict[q_text] = [PLACEHOLDER_MARKER, *options]
        added += 1
    if added:
        common.write_json_atomic(p, data)
    return added


def _evict_answers(product: str, q_texts: list[str], path: Path | str = None) -> None:
    p = Path(path or QUIZ_ANSWERS_PATH)
    if not q_texts or not p.exists():
        return
    data = load_quiz_answers(p)
    product = resolve_product_key(data, product)
    if product in data:
        for q_text in q_texts:
            data[product].pop(q_text, None)
        common.write_json_atomic(p, data)


def _evict_legacy_answers(product: str, path: Path | str = None) -> None:
    p = Path(path or LEGACY_ANSWERS_PATH)
    if not p.exists():
        return
    data = load_legacy_answers(p)
    # 표기 변형 키까지 함께 지운다. 하나만 지우면 "더-스피로킷"/"더스피로킷" 같은
    # 쌍이 계속 남아 legacy가 줄지 않는다.
    np = normalize_product(product)
    pruned = {k: v for k, v in data.items() if normalize_product(k) != np}
    if len(pruned) != len(data):
        common.write_json_atomic(p, pruned)


def _handle_learned_answers(
    source: str,
    product: str,
    pairs: list[tuple[str, str]],
    quiz_path: Path | str = None,
    legacy_path: Path | str = None,
) -> int:
    """Legacy 정답으로 성공 시 문제은행(quiz_answers.json)으로 승격하고 legacy에서 삭제."""
    if source == "legacy":
        _record_answers(product, pairs, path=quiz_path)
        _evict_legacy_answers(product, path=legacy_path)
        return len(pairs)
    return 0
