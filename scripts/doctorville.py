#!/usr/bin/env python3
"""
닥터빌(doctorville.co.kr) 일일 자동화 스크립트.
출석체크 / 오늘의 퀴즈 / 세미나 신청을 처리한다.

용법:
    python3 doctorville.py                           # bjh7790, 전체 태스크 (헤드리스)
    python3 doctorville.py --account wonju           # wonju 계정
    python3 doctorville.py --task attend             # 출석체크만
    python3 doctorville.py --task quiz               # 퀴즈만
    python3 doctorville.py --task seminar            # 세미나만
    python3 doctorville.py --headed                  # 브라우저 창 띄워서 실행 (디버깅용)
    python3 doctorville.py --credentials PATH        # credentials.json 경로 직접 지정

표준출력에 결과를 한 줄 JSON으로 출력한다. 예:
    {
      "site": "doctorville",
      "account": "bjh7790",
      "attend":  {"status": "success",      "points": 100},
      "quiz":    {"status": "success",      "product": "스피틴", "points": 500},
      "seminar": {"status": "success",      "applied": [5457], "count": 1}
    }

    status 값:
        success      — 완료 (포인트 적립)
        already_done — 오늘 이미 완료
        skipped      — --task 옵션으로 건너뜀
        no_answer    — quiz_answers.json에 정답 없음 (퀴즈 미시도)
        failed       — 예상치 못한 오류

mims 로그인 셀렉터 확인 방법 (첫 실행 전):
    --headed 로 실행 후 로그인 폼에서 F12 → 이메일 input의 name/id/type 확인.
    현재 스크립트는 input[type="email"] → input[type="text"]:visible 순서로 시도한다.
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

from playwright.sync_api import (
    sync_playwright,
    TimeoutError as PlaywrightTimeoutError,
    Error as PlaywrightError,
)

import common
import notify
import runlog

DOCTORVILLE_BASE    = "https://www.doctorville.co.kr"
ATTEND_URL          = f"{DOCTORVILLE_BASE}/event/attend"
PRODUCT_MAIN_URL    = f"{DOCTORVILLE_BASE}/product/main"
MEDICINE_LIST_URL   = f"{DOCTORVILLE_BASE}/product/medicineList"
SEMINAR_MAIN_URL    = f"{DOCTORVILLE_BASE}/seminar/main"
SEMINAR_DETAIL_URL  = f"{DOCTORVILLE_BASE}/seminar/seminarDetail"

DEFAULT_TIMEOUT_MS  = 30000
# 달력(오늘 셀) 마운트 대기. 없으면 "미출석"이 아니라 "아직 안 그려짐"일 뿐이다.
ATTEND_MARKER_TIMEOUT_MS = 8000
SCRIPT_DIR          = Path(__file__).resolve().parent
QUIZ_ANSWERS_PATH   = SCRIPT_DIR.parent / "quiz_answers.json"
LEGACY_ANSWERS_PATH = SCRIPT_DIR.parent / "quiz_answers_legacy.json"
SEMINAR_APPLIED_PATH = SCRIPT_DIR.parent / "seminar_applied.json"
APPLIED_RETENTION_DAYS = 60


# ---------------------------------------------------------------------------
# 유틸
# ---------------------------------------------------------------------------

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


PLACEHOLDER_MARKER = common.ANSWER_PLACEHOLDER_MARKER


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


def parse_calendar_cell(cell_html: str) -> dict:
    p_id = None
    quiz_id = None
    for input_tag in re.findall(r'<input[^>]*>', cell_html, re.IGNORECASE):
        class_m = re.search(r'class=["\']([^"\']+)["\']', input_tag, re.IGNORECASE)
        val_m = re.search(r'value=["\']([^"\']+)["\']', input_tag, re.IGNORECASE)
        if class_m and val_m:
            classes = class_m.group(1).split()
            if "pIdCls" in classes:
                p_id = val_m.group(1)
            if "quizIdCls" in classes:
                quiz_id = val_m.group(1)

    name_match = re.search(r'<span[^>]*class=["\'][^"\']*name[^"\']*["\'][^>]*>(.*?)</span>', cell_html, re.DOTALL | re.IGNORECASE)
    if name_match:
        product = re.sub(r'<[^>]+>', '', name_match.group(1)).strip()
    else:
        spans = re.findall(r'<span[^>]*>(.*?)</span>', cell_html, re.DOTALL | re.IGNORECASE)
        clean_spans = [re.sub(r'<[^>]+>', '', s).strip() for s in spans]
        non_day = [s for s in clean_spans if not s.isdigit() and s]
        product = non_day[0] if non_day else ""

    return {
        "product": product,
        "p_id": p_id,
        "quiz_id": quiz_id,
    }


def load_credentials(path: Path, account: str) -> dict:
    data = common.read_credentials(path)
    if account not in data:
        raise KeyError(f"credentials.json에 '{account}' 계정이 없습니다.")
    acc = data[account]
    if "doctorville" not in acc or "password" not in acc["doctorville"]:
        raise KeyError(f"credentials.json의 '{account}.doctorville.password'가 없습니다.")
    email = acc.get("email", "")
    if not email:
        raise KeyError(f"credentials.json의 '{account}.email'이 없습니다.")
    return {"email": email, "password": acc["doctorville"]["password"]}


def load_quiz_answers() -> dict:
    return common.read_json(QUIZ_ANSWERS_PATH, default={})


def load_quiz_answers_legacy() -> dict:
    return common.read_json(LEGACY_ANSWERS_PATH, default={})


def _record_answers(product: str, pairs: list[tuple[str, str]]) -> None:
    if not pairs:
        return
    data = consolidate_products(load_quiz_answers())
    key = resolve_product_key(data, product)
    prod_dict = data.setdefault(key, {})
    for q_text, ans_text in pairs:
        prod_dict[q_text] = ans_text
    common.write_json_atomic(QUIZ_ANSWERS_PATH, data)


def _record_missing_placeholders(product: str, missing: list[dict]) -> int:
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

    data = consolidate_products(load_quiz_answers())
    key = resolve_product_key(data, product)
    prod_dict = data.setdefault(key, {})
    added = 0
    for q_text, options in seeds:
        if q_text in prod_dict:
            continue
        prod_dict[q_text] = [PLACEHOLDER_MARKER, *options]
        added += 1
    if added:
        common.write_json_atomic(QUIZ_ANSWERS_PATH, data)
    return added


def _evict_answers(product: str, q_texts: list[str]) -> None:
    if not q_texts or not QUIZ_ANSWERS_PATH.exists():
        return
    data = load_quiz_answers()
    product = resolve_product_key(data, product)
    if product in data:
        for q_text in q_texts:
            data[product].pop(q_text, None)
        common.write_json_atomic(QUIZ_ANSWERS_PATH, data)


def _evict_legacy_answers(product: str) -> None:
    if not LEGACY_ANSWERS_PATH.exists():
        return
    data = load_quiz_answers_legacy()
    # 표기 변형 키까지 함께 지운다. 하나만 지우면 "더-스피로킷"/"더스피로킷" 같은
    # 쌍이 계속 남아 legacy가 줄지 않는다.
    np = normalize_product(product)
    pruned = {k: v for k, v in data.items() if normalize_product(k) != np}
    if len(pruned) != len(data):
        common.write_json_atomic(LEGACY_ANSWERS_PATH, pruned)


# ---------------------------------------------------------------------------
# 신청 이력 (seminar_applied.json)
#
# 세미나 목록의 `span.ico_apply` 배지는 "신청 가능 기간"을 뜻할 뿐 "내가 아직 신청
# 안 함"이 아니다. 그래서 이미 신청한 세미나도 매 런마다 목록에 다시 나오고,
# 상세 페이지를 열어봐야만 신청 여부를 알 수 있었다. 30분 간격 × 하루 18런 ×
# 2계정이면 같은 상세를 하루 36번 다시 여는 셈이다.
#
# 신청이 확인된 seminarId를 기록해 두고, 목록에 없던 **새 세미나만** 상세로 간다.
# 캐시가 아니라 커밋되는 파일인 이유: Actions 캐시 restore-key가 날짜 단위라
# 하루가 지나면 사라지는데, 신청 이력은 방송일까지 며칠~몇 주 유지돼야 한다.
# ---------------------------------------------------------------------------

def load_applied(path: Path | str = None) -> dict:
    return common.read_json(path or SEMINAR_APPLIED_PATH, default={})


def _handle_learned_answers(source: str, product: str, pairs: list[tuple[str, str]]) -> int:
    """Legacy 정답으로 성공 시 문제은행(quiz_answers.json)으로 승격하고 legacy에서 삭제."""
    if source == "legacy":
        _record_answers(product, pairs)
        _evict_legacy_answers(product)
        return len(pairs)
    return 0


def save_applied(data: dict, path: Path = None) -> None:
    path = Path(path or SEMINAR_APPLIED_PATH)
    common.write_json_atomic(path, data, sort_keys=True)


def applied_ids(data: dict, account: str) -> set:
    """계정의 신청 완료 seminarId 집합(문자열)."""
    acc = data.get(account) if isinstance(data, dict) else None
    return set(acc.keys()) if isinstance(acc, dict) else set()


def filter_new_seminars(seminar_ids: list, data: dict, account: str) -> list:
    """목록에서 아직 신청 이력이 없는 세미나만 남긴다(순서·중복 유지 안 함)."""
    known = applied_ids(data, account)
    seen = set()
    out = []
    for sid in seminar_ids:
        s = str(sid)
        if s in known or s in seen:
            continue
        seen.add(s)
        out.append(sid)
    return out


def record_applied(data: dict, account: str, seminar_id, title: str = "", start: str = "", now=None) -> dict:
    ts = (now or datetime.now(common.KST)).isoformat()
    entry = {"applied_at": ts}
    if title:
        entry["title"] = title
    if start:
        entry["start"] = start
        s_dt, e_dt = common.parse_dd_date(start)
        if s_dt is not None:
            entry["date"] = s_dt.strftime("%Y-%m-%d")
            entry["start_date"] = s_dt.strftime("%Y-%m-%d")
            entry["year"] = s_dt.year
            entry["month"] = s_dt.month
            entry["day"] = s_dt.day
            entry["start_time"] = s_dt.strftime("%H:%M")
            entry["start_hour"] = s_dt.hour
            entry["start_minute"] = s_dt.minute
            if e_dt is not None:
                entry["end_time"] = e_dt.strftime("%H:%M")
                entry["end_hour"] = e_dt.hour
                entry["end_minute"] = e_dt.minute

    data.setdefault(account, {})[str(seminar_id)] = entry
    return data


def _entry_expiry(entry: dict, days: int, now):
    """이력 1건이 만료됐는지. (만료여부, 사유)

    ① `start`(상세 페이지 dd.date)가 파싱되면 **방송이 끝난 시각**이 기준이다.
       지난 세미나는 다시 신청할 일이 없으므로 바로 버린다.
    ② `start`가 없거나 파싱 실패면 `applied_at` + days일을 백스톱으로 쓴다.
    """
    if not isinstance(entry, dict):
        return False, ""

    start = entry.get("start")
    if isinstance(start, str) and start:
        s_dt, e_dt = common.parse_dd_date(start)
        end = e_dt or s_dt
        if end is not None:
            return now > end, "past"

    ts = entry.get("applied_at")
    if isinstance(ts, str) and ts:
        try:
            dt = datetime.fromisoformat(ts)
        except (ValueError, TypeError):
            return False, ""
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=common.KST)
        return (now - dt).days > days, "stale"
    return False, ""


def prune_applied(data: dict, days: int = APPLIED_RETENTION_DAYS, now=None) -> tuple[dict, dict]:
    """날짜가 지난 세미나를 이력에서 버린다. (남은 이력, {사유: 건수}).

    잘못 버려도 자기 치유된다 — 다음 런에서 상세를 한 번 열어보고 "신청취소"를
    확인하면 그대로 다시 기록된다.
    """
    now = now or datetime.now(common.KST)
    out, counts = {}, {}
    for acc, items in (data or {}).items():
        if not isinstance(items, dict):
            continue
        kept = {}
        for sid, entry in items.items():
            expired, reason = _entry_expiry(entry, days, now)
            if expired:
                counts[reason] = counts.get(reason, 0) + 1
            else:
                kept[sid] = entry
        if kept:
            out[acc] = kept
    return out, counts


def prune_applied_file(path: Path = None, days: int = APPLIED_RETENTION_DAYS, now=None) -> dict:
    """이력 파일을 정리해 저장한다. daily에서 하루 1회 부른다.

    30분마다 도는 seminar_block에서 하지 않는 이유: 정리 자체가 파일을 바꿔
    커밋을 만들기 때문이다. 지난 세미나가 이력에 남아 있어도 "상세를 열지 않는다"는
    동작은 그대로 맞다 — 정리는 순전히 파일 크기 관리다.
    """
    path = Path(path or SEMINAR_APPLIED_PATH)
    data = load_applied(path)
    pruned, counts = prune_applied(data, days=days, now=now)
    removed = sum(counts.values())
    if removed:
        save_applied(pruned, path)
    total = sum(len(v) for v in pruned.values())
    result = {
        "removed": removed,
        "remaining": total,
        "by_reason": counts,
        "message": f"신청 이력 정리: {removed}건 제거(잔여 {total}건).",
    }
    # status: "success"에 verified_by가 없으면 notify가 unverified(alert)로 강등해
    # 런이 빨갛게 된다. 지울 게 없으면 성공이 아니라 skipped(quiet)가 맞다.
    if removed:
        result["status"] = "success"
        result["verified_by"] = f"seminar_applied.json rewritten: -{removed}"
    else:
        result["status"] = "skipped"
    return result


def save_screenshot(page, tag: str) -> str:
    return common.save_screenshot(page, f"doctorville_{tag}")


def legacy_to_choice_indices(seq: str, question_choices: list[list[str]]) -> list[int] | None:
    if len(seq) != len(question_choices):
        return None
    indices = []
    for char, choices in zip(seq, question_choices):
        char_lower = char.lower()
        if char.isdigit():
            idx = int(char) - 1
            if 0 <= idx < len(choices):
                indices.append(idx)
            else:
                return None
        elif char_lower == 'o':
            matched = False
            for idx, label in enumerate(choices):
                if label.strip().upper() == 'O':
                    indices.append(idx)
                    matched = True
                    break
            if not matched:
                return None
        elif char_lower == 'x':
            matched = False
            for idx, label in enumerate(choices):
                if label.strip().upper() == 'X':
                    indices.append(idx)
                    matched = True
                    break
            if not matched:
                return None
        else:
            return None
    return indices


def parse_wrong_numbers(text: str) -> list[int]:
    match = re.search(r'([\d\s,]+)\s*번\s*오답', text)
    if not match:
        return []
    nums_str = match.group(1)
    return [int(n.strip()) for n in re.findall(r'\d+', nums_str) if int(n.strip()) >= 1]



# ---------------------------------------------------------------------------
# 로그인
# ---------------------------------------------------------------------------

def ensure_logged_in(page, creds: dict) -> bool:
    """
    현재 페이지가 닥터빌 인트로(/intro) 또는 mims 로그인 페이지라면 로그인을 시도한다.
    로그인 불필요(이미 세션 유지)이거나 성공하면 True, 실패하면 False 반환.

    셀렉터 근거 (2026-07-08, Playwright 헤드리스 networkidle 대기 후 DOM 직접 조회):
    - 인트로 로그인 링크: a.btn_join.union (href에 mims-account.shop.co.kr/login?cb=... 포함)
    - mims 이메일: input[name="identifier"]
    - mims 비밀번호: input[type="password"]
    - mims 제출: button[type="submit"]:has-text("로그인")
    """
    url = page.url

    # ① 이미 닥터빌 본문 페이지 — 로그인 불필요
    if "doctorville.co.kr" in url and "/intro" not in url and "mims-account" not in url:
        return True

    # ② 닥터빌 인트로 페이지 — mims 로그인 URL 추출 후 직접 이동
    if "doctorville.co.kr" in url and "/intro" in url:
        # SPA 렌더링 대기 — networkidle 타임아웃 허용
        try:
            page.wait_for_load_state("networkidle", timeout=DEFAULT_TIMEOUT_MS)
        except PlaywrightTimeoutError:
            pass
        # networkidle 이후에도 DOM 마운트가 늦을 수 있으므로 명시적 대기 후 재시도
        mims_url = None
        for _ in range(3):
            mims_url = page.evaluate("""
                () => {
                    // mims-account 링크 중 /login 포함된 것만 추출 (회원가입 링크 제외)
                    const all = document.querySelectorAll('a[href*="mims-account.shop.co.kr"]');
                    for (const a of all) {
                        if (a.href.includes('/login')) return a.href;
                    }
                    return null;
                }
            """)
            if mims_url:
                break
            page.wait_for_timeout(1500)
        if not mims_url:
            return False
        # mims는 Next.js SPA — networkidle 타임아웃 가능성 있어 domcontentloaded 사용
        try:
            common.goto_with_retry(page, mims_url, wait_until="domcontentloaded", timeout_ms=DEFAULT_TIMEOUT_MS)
        except (PlaywrightTimeoutError, PlaywrightError):
            pass  # 페이지는 이미 로드됨, input 대기는 _do_mims_login에서 처리

    # ③ mims 로그인 페이지
    if "mims-account" in page.url:
        return _do_mims_login(page, creds)

    return True


def _do_mims_login(page, creds: dict) -> bool:
    """mims-account.shop.co.kr 로그인 폼을 채우고 제출한다.
    셀렉터: input[name="identifier"], input[type="password"], button[type="submit"]
    """
    try:
        page.wait_for_selector('input[name="identifier"]', timeout=DEFAULT_TIMEOUT_MS)
    except PlaywrightTimeoutError:
        return False

    page.fill('input[name="identifier"]', creds["email"])
    page.fill('input[type="password"]', creds["password"])
    page.click('button[type="submit"]:has-text("로그인")')

    # 클릭 후 doctorville.co.kr로 리다이렉트될 때까지 대기.
    # wait_for_load_state("load")는 mims 페이지 자체가 이미 loaded 상태이므로
    # 리다이렉트 완료 전에 리턴될 수 있음 — wait_for_url로 교체.
    try:
        page.wait_for_url("*doctorville.co.kr*", timeout=DEFAULT_TIMEOUT_MS)
    except PlaywrightTimeoutError:
        pass

    return "doctorville.co.kr" in page.url and "mims-account" not in page.url


# ---------------------------------------------------------------------------
# 태스크 ① 출석체크
# ---------------------------------------------------------------------------

def _attend_points(page, today_cell: str) -> int:
    """오늘 셀 아이콘의 alt에서 적립 포인트를 읽는다(평상 100, 보너스일 500)."""
    try:
        alt = page.locator(f"{today_cell} img").first.get_attribute("alt")
        return int(alt)
    except Exception:
        return 100


def _attend_marked(page, today_cell: str, timeout_ms: int = ATTEND_MARKER_TIMEOUT_MS) -> bool:
    """달력이 렌더된 뒤에 오늘 셀의 완료 표식을 확인한다.

    달력은 진입 직후엔 아직 붙어 있지 않다. 마운트 전에 `count()`를 읽으면 출석이
    처리됐는데도 0이 나와, 클릭 분기로 새어 "완료 확인 실패"로 끝난다
    (키메디에서 이미 같은 함정에 4번 빠졌다).
    """
    try:
        page.wait_for_selector("td[data-date]", state="attached", timeout=timeout_ms)
        page.wait_for_selector(today_cell, state="attached", timeout=timeout_ms)
        return True
    except PlaywrightTimeoutError:
        return False


def task_attend(page, creds: dict) -> dict:
    result = {"status": "failed", "points": 0}

    common.goto_with_retry(page, ATTEND_URL, wait_until="domcontentloaded", timeout_ms=DEFAULT_TIMEOUT_MS)
    if not ensure_logged_in(page, creds):
        result["message"] = "로그인 실패"
        result["screenshot"] = save_screenshot(page, "attend_login")
        return result

    # 로그인 후 출석 페이지가 아니면 재이동
    if "/event/attend" not in page.url:
        common.goto_with_retry(page, ATTEND_URL, wait_until="domcontentloaded", timeout_ms=DEFAULT_TIMEOUT_MS)

    # 이미 완료 여부 확인 — 달력의 오늘 셀이 유일하게 날짜까지 확정되는 증거다.
    # `td[data-date="YYYY-MM-DD"] > div.point.complete` (미출석/미래 날짜는 `.complete` 없음).
    # 출석은 페이지 진입만으로 처리되므로 이 확인이 먼저다 (R2 정찰, 2026-08-10).
    today_cell = f'td[data-date="{datetime.now(common.KST):%Y-%m-%d}"] div.point.complete'
    if _attend_marked(page, today_cell):
        result["status"] = "already_done"
        result["verified_by"] = today_cell
        result["message"] = "출석 완료 (페이지 진입 시 자동 처리)."
        return result

    # 진입으로 적립됐지만 그 응답의 달력엔 아직 반영되지 않는 경우가 있다.
    # 버튼을 누르기 전에 재접속해서 한 번 더 본다(진입=출석이므로 이게 정상 경로).
    common.goto_with_retry(page, ATTEND_URL, wait_until="domcontentloaded", timeout_ms=DEFAULT_TIMEOUT_MS)
    if _attend_marked(page, today_cell):
        result["status"] = "success"
        result["verified_by"] = today_cell
        result["points"] = _attend_points(page, today_cell)
        result["message"] = f"출석 완료, {result['points']}P 적립 (재접속 확인)."
        return result

    # 미출석 — "N월 N일 출석하기" 버튼이 보인다.
    # 두 버튼(`btn.point_down` / `btn.complete`)이 항상 DOM에 공존하며 display로만
    # 토글되므로, 존재 여부가 아니라 visible 여부로 판정해야 한다.
    attend_btn = page.locator('button.btn.point_down, button:has-text("출석하기"), a:has-text("출석하기")')
    try:
        attend_btn.first.wait_for(state="visible", timeout=DEFAULT_TIMEOUT_MS)
    except PlaywrightTimeoutError:
        result["status"] = "unverified"
        result["message"] = "출석 버튼도 오늘 출석 표식도 없음"
        result["screenshot"] = save_screenshot(page, "attend_no_marker")
        return result

    btn_text = attend_btn.first.inner_text().strip()
    r2_data = {}
    if common.is_recon_enabled():
        r2_data = {
            "btn_text_before": btn_text,
            "url_before": page.url,
        }

    attend_btn.first.click()

    # 완료 팝업("오늘도 출석 완료" / "적립완료") — 숨김 상태로도 DOM에 상주하므로
    # 반드시 visible 대기여야 한다(wait_for_selector 기본 state).
    try:
        page.wait_for_selector(
            "text=출석 완료, text=적립완료, text=출석완료",
            timeout=DEFAULT_TIMEOUT_MS
        )
        if common.is_recon_enabled():
            try:
                from recon import dump_recon_data
                r2_data["url_after"] = page.url
                r2_data["popup_text"] = page.evaluate("() => document.body ? document.body.innerText.slice(0, 500) : ''")
                dump_recon_data("R2", r2_data, page=page)
            except Exception:
                pass

        # 팝업 닫기
        close_btn = page.locator('button:has-text("확인"), .btn_close, [class*="close"]')
        if close_btn.count() > 0:
            close_btn.first.click()
        result["status"] = "success"
        result["verified_by"] = "popup: 출석 완료"
        result["points"] = _attend_points(page, today_cell)
        result["message"] = f"출석 완료, {result['points']}P 적립."
    except PlaywrightTimeoutError:
        # 팝업 없이 바로 완료 처리되는 경우도 있음 — 달력 표식으로 재확인
        try:
            common.reload_with_retry(page, wait_until="domcontentloaded")
        except Exception:
            pass
        if _attend_marked(page, today_cell):
            result["status"] = "success"
            result["verified_by"] = today_cell
            result["points"] = _attend_points(page, today_cell)
            result["message"] = f"출석 완료, {result['points']}P 적립 (팝업 없이 처리됨)."
        else:
            result["message"] = "출석 버튼 클릭 후 완료 확인 실패."
            result["screenshot"] = save_screenshot(page, "attend_fail")

    return result


# ---------------------------------------------------------------------------
# 태스크 ② 오늘의 퀴즈
# ---------------------------------------------------------------------------

def _get_today_quiz_product(page) -> tuple[str | None, str | None, str | None]:
    """
    /product/main의 이달의 퀴즈 캘린더에서 오늘 날짜의 제품명과 pId, quizId를 추출한다.

    pId는 캘린더 표에서 오늘 셀(`td.today`)에 내장된 hidden input(`.pIdCls`)
    값을 직접 읽는다. 의약품(medicineList)뿐 아니라 의료기기(instrumentList)
    등 카테고리와 무관하게 항상 존재하므로, 카테고리별 목록 페이지에서 이름으로
    검색하는 것보다 안정적이다(2026-07-20, 모비케어=의료기기가 medicineList에
    없어 pId 조회 실패했던 문제 확인 후 수정).
    반환: (제품명, pId, quizId) — 각각 못 찾으면 None
    """
    common.goto_with_retry(page, PRODUCT_MAIN_URL, wait_until="domcontentloaded", timeout_ms=DEFAULT_TIMEOUT_MS)
    page.wait_for_timeout(2000)  # SPA 로딩 대기

    # ".quiz_calender" 요소의 텍스트에서 제품명 추출
    # 예: "2026년 7월 8일\n스피틴\n고지혈증 치료제\n..."
    try:
        calendar = page.locator(".quiz_calender")
        calendar.first.wait_for(state="visible", timeout=DEFAULT_TIMEOUT_MS)
        text = calendar.first.inner_text()
    except PlaywrightTimeoutError:
        return None, None, None

    # 날짜 다음 줄이 제품명
    product = None
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    for i, line in enumerate(lines):
        # "N년 N월 N일" 패턴 이후 줄이 제품명
        if re.search(r"\d+년\s*\d+월\s*\d+일", line):
            if i + 1 < len(lines):
                product = lines[i + 1]
            break

    pid = None
    pid_input = page.locator("td.today input.pIdCls")
    if pid_input.count() > 0:
        pid = pid_input.first.get_attribute("value")

    quiz_id = None
    quiz_input = page.locator("td.today input.quizIdCls")
    if quiz_input.count() > 0:
        quiz_id = quiz_input.first.get_attribute("value")

    return product, pid, quiz_id


def _get_product_pid(page, product_name: str) -> str | None:
    """
    /product/medicineList에서 제품명과 일치하는 링크의 pId를 반환한다.
    대소문자·공백 무시, 부분 일치.
    """
    common.goto_with_retry(page, MEDICINE_LIST_URL, wait_until="domcontentloaded", timeout_ms=DEFAULT_TIMEOUT_MS)
    page.wait_for_timeout(1000)

    links = page.locator("a[href*='productView']")
    count = links.count()
    name_normalized = product_name.replace(" ", "").lower()

    for i in range(count):
        link = links.nth(i)
        text = link.inner_text().replace(" ", "").lower()
        href = link.get_attribute("href") or ""
        if name_normalized in text:
            m = re.search(r"pId=(\d+)", href)
            if m:
                return m.group(1)
    return None


def task_quiz(page, creds: dict) -> dict:
    result = {"status": "failed", "points": 0, "product": ""}
    answers = load_quiz_answers()

    # 오늘 퀴즈 제품명·pId 확인 (pId는 캘린더 셀에 내장 — 의약품/의료기기 공통)
    product, pid, quiz_id = _get_today_quiz_product(page)
    if not product:
        result["status"] = "failed"
        result["message"] = "이달의 퀴즈 캘린더에서 오늘 제품명을 찾지 못함."
        result["screenshot"] = save_screenshot(page, "quiz_calendar")
        return result

    result["product"] = product
    if quiz_id:
        result["quiz_id"] = quiz_id

    # pId 조회 — 캘린더에서 못 찾았으면 medicineList 검색으로 폴백(의약품 한정)
    if not pid:
        pid = _get_product_pid(page, product)
    if not pid:
        result["message"] = f"캘린더·medicineList 모두에서 '{product}' pId를 찾지 못함."
        result["screenshot"] = save_screenshot(page, "quiz_pid")
        return result

    # 제품 상세 페이지 이동
    product_url = f"{DOCTORVILLE_BASE}/product/productView?pId={pid}"
    common.goto_with_retry(page, product_url, wait_until="domcontentloaded", timeout_ms=DEFAULT_TIMEOUT_MS)

    # 퀴즈 완료 여부 확인
    quiz_banner = page.locator("#btn_quiz_banner")
    try:
        quiz_banner.wait_for(state="attached", timeout=DEFAULT_TIMEOUT_MS)
        banner_class = quiz_banner.get_attribute("class") or ""
        if "ico_finish" in banner_class:
            result["status"] = "already_done"
            result["verified_by"] = "#btn_quiz_banner.ico_finish"
            result["message"] = f"'{product}' 퀴즈 이미 완료."
            return result
    except PlaywrightTimeoutError:
        result["message"] = "퀴즈 배너(#btn_quiz_banner)를 찾지 못함."
        result["screenshot"] = save_screenshot(page, "quiz_banner")
        return result

    # 퀴즈 배너 클릭 → 레이어 열기
    quiz_banner.scroll_into_view_if_needed()
    page.wait_for_timeout(500)
    quiz_banner.click()

    # 퀴즈 레이어(#quizLayerPop) 열릴 때까지 대기
    quiz_layer = page.locator("#quizLayerPop")
    try:
        quiz_layer.wait_for(state="visible", timeout=DEFAULT_TIMEOUT_MS)
    except PlaywrightTimeoutError:
        result["message"] = "퀴즈 레이어(#quizLayerPop)가 열리지 않음."
        result["screenshot"] = save_screenshot(page, "quiz_layer")
        return result

    page.wait_for_timeout(1500)
    save_screenshot(page, "quiz_layer_open")

    # 퀴즈 레이어가 열린 즉시 이미 완료 상태인지 확인 ('축하드립니다', '내일 다시 만나요' 배너)
    has_congrats = False
    try:
        congrats_cnt = quiz_layer.locator(":text('축하드립니다')").count()
        next_day_cnt = quiz_layer.locator(":text('내일 다시 만나요')").count()
        if (isinstance(congrats_cnt, int) and congrats_cnt > 0) or (isinstance(next_day_cnt, int) and next_day_cnt > 0):
            has_congrats = True
    except (TypeError, Exception):
        has_congrats = False

    if has_congrats:
        result["status"] = "already_done"
        result["verified_by"] = ":text('축하드립니다')"
        result["message"] = f"'{product}' 퀴즈 오늘 이미 완료 ('퀴즈 성공을 축하드립니다' 확인)."
        close_btn = quiz_layer.locator(".btn_cancel, .btn_close, button:has-text('닫기')").first
        if close_btn.is_visible():
            close_btn.click()
        return result

    question_areas = quiz_layer.locator(".question_area")
    qcount = question_areas.count()
    if qcount == 0:
        result["message"] = "퀴즈 레이어에서 문항(.question_area)을 찾지 못함."
        result["screenshot"] = save_screenshot(page, "quiz_questions")
        return result

    q_texts = []
    choices_per_q = []
    values_per_q = []

    for i in range(qcount):
        qa = question_areas.nth(i)
        q_text = " ".join(qa.locator(".txt_question").inner_text().split())
        q_texts.append(q_text)

        choice_lis = qa.locator(".question_choice li")
        c_count = choice_lis.count()
        c_labels = []
        c_vals = []
        for c in range(c_count):
            li = choice_lis.nth(c)
            label_text = " ".join(li.locator("label").inner_text().split())
            val = li.locator('input[type="radio"]').get_attribute("value")
            c_labels.append(label_text)
            c_vals.append(val)
        choices_per_q.append(c_labels)
        values_per_q.append(c_vals)

    source = None
    plan: list[tuple[str, str]] = []
    selected_pairs: list[tuple[str, str]] = []
    missing: list[dict] = []

    # 1. Bank 매칭 시도
    product_bank = lookup_product_bank(answers, product)
    bank_plan = []
    bank_pairs = []
    bank_missing = []

    for i in range(qcount):
        q_text = q_texts[i]
        answer_text = coerce_bank_answer(product_bank.get(q_text))
        matched_val = None
        matched_label = None
        if answer_text is not None:
            answer_norm = " ".join(answer_text.split())
            for label, val in zip(choices_per_q[i], values_per_q[i]):
                if label == answer_norm:
                    matched_val = val
                    matched_label = label
                    break
        if matched_val is None:
            bank_missing.append({
                "question": q_text,
                "options": choices_per_q[i],
                "recorded_answer_not_matched": answer_text,
            })
        else:
            bank_plan.append((f"an_{i + 1}", matched_val))
            bank_pairs.append((q_text, matched_label))

    if not bank_missing:
        source = "bank"
        plan = bank_plan
        selected_pairs = bank_pairs
    else:
        # 2. Legacy 매칭 시도
        legacy_answers = load_quiz_answers_legacy()
        legacy_seq = lookup_legacy_seq(legacy_answers, product)
        legacy_indices = None
        if isinstance(legacy_seq, str):
            legacy_indices = legacy_to_choice_indices(legacy_seq, choices_per_q)

        if legacy_indices is not None:
            source = "legacy"
            plan = []
            selected_pairs = []
            for i, idx in enumerate(legacy_indices):
                plan.append((f"an_{i + 1}", values_per_q[i][idx]))
                selected_pairs.append((q_texts[i], choices_per_q[i][idx]))
        else:
            missing = bank_missing

    if missing or not source:
        result["status"] = "no_answer"
        result["questions"] = missing
        added = _record_missing_placeholders(product, missing)
        result["bank_seeded"] = added
        result["message"] = (
            f"'{product}' 퀴즈: {len(missing)}개 문항 정답 매칭 실패 — "
            f"quiz_answers.json에 {added}개 문항을 보기와 함께 깔아뒀다. 정답만 남기고 지우면 된다."
        )
        close_btn = quiz_layer.locator(".btn_cancel, .btn_close").first
        if close_btn.is_visible():
            close_btn.click()
        return result

    for name, val in plan:
        radio = quiz_layer.locator(f'input[name="{name}"][value="{val}"]').first
        try:
            radio.wait_for(state="attached", timeout=5000)
            radio.click()
        except PlaywrightTimeoutError:
            result["message"] = f"{name} 라디오 버튼(value={val}) 찾기 실패."
            result["screenshot"] = save_screenshot(page, "quiz_radio")
            return result

    # "정답 도전" 버튼 클릭 — 레이어 내부 .btn_answer
    submit_btn = quiz_layer.locator(".btn_answer")
    if submit_btn.count() == 0 or not submit_btn.is_visible():
        if quiz_layer.locator(":text('축하드립니다')").count() > 0:
            result["status"] = "already_done"
            result["verified_by"] = ":text('축하드립니다')"
            result["message"] = f"'{product}' 퀴즈 오늘 이미 완료 ('퀴즈 성공을 축하드립니다' 확인)."
            close_btn = quiz_layer.locator(".btn_cancel, .btn_close").first
            if close_btn.is_visible():
                close_btn.click()
            return result
        close_btn = quiz_layer.locator(".btn_cancel, .btn_close").first
        if close_btn.is_visible():
            close_btn.click()
        page.wait_for_timeout(500)
        banner_class2 = page.locator("#btn_quiz_banner").get_attribute("class") or ""
        if "ico_finish" in banner_class2:
            result["status"] = "already_done"
            result["verified_by"] = "#btn_quiz_banner.ico_finish"
            result["message"] = f"'{product}' 퀴즈 이미 완료 (ico_finish 확인)."
            return result
        result["message"] = "'정답 도전' 버튼을 찾지 못함."
        result["screenshot"] = save_screenshot(page, "quiz_submit")
        return result

    try:
        submit_btn.scroll_into_view_if_needed()
        page.wait_for_timeout(300)
        submit_btn.click()
    except PlaywrightTimeoutError:
        result["message"] = "'정답 도전' 버튼 클릭 실패."
        result["screenshot"] = save_screenshot(page, "quiz_submit")
        return result

    # 결과 팝업 대기 — "정답입니다" 또는 "오답입니다"
    try:
        page.wait_for_selector(":text('정답입니다')", timeout=DEFAULT_TIMEOUT_MS)
        ok_btn = page.locator('button:has-text("확인")').last
        if ok_btn.is_visible():
            ok_btn.click()
        result["status"] = "success"
        result["verified_by"] = ":text('정답입니다')"
        result["points"] = 500
        result["source"] = source
        result["learned"] = _handle_learned_answers(source, product, selected_pairs)
        result["message"] = f"'{product}' 퀴즈 정답 ({source}), 500P 적립."
    except PlaywrightTimeoutError:
        try:
            wrong_el = page.wait_for_selector(":text('오답입니다')", timeout=3000)
            wrong_text = wrong_el.inner_text().strip() if wrong_el else ""
            ok_btn = page.locator('button:has-text("확인")').last
            if ok_btn.is_visible():
                ok_btn.click()

            wrong_nums = parse_wrong_numbers(wrong_text)
            if wrong_nums and all(1 <= w <= qcount for w in wrong_nums):
                correct_pairs = [selected_pairs[w - 1] for w in range(1, qcount + 1) if w not in wrong_nums]
                _record_answers(product, correct_pairs)
                if source == "bank":
                    wrong_q_texts = [selected_pairs[w - 1][0] for w in wrong_nums]
                    _evict_answers(product, wrong_q_texts)

            if source == "legacy":
                _evict_legacy_answers(product)

            result["status"] = "failed"
            result["source"] = source
            result["message"] = f"'{product}' 퀴즈 오답 ({wrong_text}, 출처: {source}) — quiz_answers.json 확인 필요."
        except PlaywrightTimeoutError:
            banner_class = page.locator("#btn_quiz_banner").get_attribute("class") or ""
            if "ico_finish" in banner_class:
                result["status"] = "success"
                result["verified_by"] = "banner: ico_finish"
                result["points"] = 500
                result["source"] = source
                result["learned"] = _handle_learned_answers(source, product, selected_pairs)
                result["message"] = f"'{product}' 퀴즈 완료 확인 (ico_finish, 출처: {source})."
            else:
                result["status"] = "failed"
                result["message"] = "퀴즈 제출 후 결과 팝업을 확인하지 못함."
                result["screenshot"] = save_screenshot(page, "quiz_result")

    return result


# ---------------------------------------------------------------------------
# 태스크 ③ 세미나 신청
# ---------------------------------------------------------------------------

# 목록에서 seminarId와 제목을 함께 긁는 JS.
#
# **r""" 을 유지할 것.** 일반 문자열이면 \n이 파이썬 단계에서 진짜 줄바꿈이 되어
# JS 문자열 리터럴이 끊긴다 — 2026-08-28에 이걸로 두 계정 신청이 전부
# `SyntaxError: Invalid or unexpected token`으로 죽었다.
#
# 조회를 a.list_detail 안으로 한정하는 게 핵심이다. document 전역으로 뒤지면
# 사이트 헤더("엠서클 통합회원")가 제목으로 잡힌다.
#
# 순회 기준은 a.list_detail이지 span.ico_apply가 아니다. ico_apply(신청 가능
# 배지)로 훑으면 **이미 신청한 세미나는 아예 안 잡힌다** — 2026-09-02 run
# 33582276817이 두 계정 모두 no_target으로 끝나면서 확인됐다. 신청 대상은
# applicable 플래그로 가리고, 제목은 목록에 있는 전부에서 긁는다(이미 신청한
# 세미나의 오염된 이력 제목을 페이지 로드 없이 고치는 유일한 경로다).
SEMINAR_LIST_JS = r"""
    () => Array.from(document.querySelectorAll('a.list_detail')).map(aEl => {
        let sid = null;
        try { sid = new URL(aEl.href).searchParams.get('seminarId'); } catch(e) { return null; }
        if (!sid) return null;

        const titEl = aEl.querySelector('.tit, dt, .title, strong');
        let title = titEl ? titEl.innerText.trim() : '';
        if (!title) {
            const lines = (aEl.innerText || '').split('\n').map(l => l.trim()).filter(Boolean);
            const filtered = lines.filter(l =>
                !/^(입장|신청|방송중|마감|신청완료|사전신청)/.test(l) &&
                !/\d{2}:\d{2}/.test(l) &&
                !/^(연자|정원):/.test(l)
            );
            title = filtered.length > 0 ? filtered[0] : '';
        }
        return { id: sid, title: title, applicable: !!aEl.querySelector('span.ico_apply') };
    }).filter(Boolean)
"""


def _seminar_detail_meta(page) -> tuple[str, str]:
    """상세 페이지의 (제목, 일시). 이력 파일에 남길 메타데이터일 뿐 판정에는 안 쓴다."""
    try:
        return tuple(page.evaluate("""
            () => {
                const banned = /^(라이브세미나|라이브\\s*세미나|닥터빌|세미나|상세|사전신청|신청하기|목록|다시보기)$/;
                const candidates = Array.from(document.querySelectorAll(
                    '.seminar_view .tit, .view_title, .view_tit, .detail_tit, dl.seminar_info dt, .tit_area .tit, .tit_area, h3.tit, h4.tit, .tit, dt, h3, h4, strong'
                ));
                let title = '';
                for (const el of candidates) {
                    const text = (el.innerText || '').trim();
                    if (text && !banned.test(text.replace(/\\s+/g, '')) && text.length >= 2) {
                        title = text;
                        break;
                    }
                }
                const d = document.querySelector('dd.date');
                return [title, d ? d.innerText.trim() : ''];
            }
        """))
    except Exception:
        return "", ""


def _log_seminar(sid, status: str, account: str, title: str = "", start: str = "") -> None:
    """세미나 표의 '신청' 칸을 채운다. 로깅 실패가 신청 자체를 죽이면 안 된다."""
    try:
        runlog.update_seminar(
            sid, phase="apply", status=status, account=account or "_",
            title=title, start=start,
        )
    except Exception as e:
        print(f"[doctorville] 세미나 로그 기록 실패({sid}): {e}", file=sys.stderr)


def task_seminar(page, creds: dict, account: str = None, applied_path: Path = None) -> dict:
    result = {"status": "failed", "applied": [], "count": 0}

    common.goto_with_retry(page, SEMINAR_MAIN_URL, wait_until="domcontentloaded", timeout_ms=DEFAULT_TIMEOUT_MS)
    page.wait_for_timeout(1000)

    # 신청 가능 세미나 추출 (CLAUDE.md DOM 패턴).
    # 제목도 여기서 같이 긁는다. 상세 페이지의 _seminar_detail_meta는 document
    # 전역을 뒤져서 헤더·푸터("엠서클 통합회원")를 집어왔다 — 이력 108건이 전부
    # 그 값이었다(2026-08-28 확인). 목록은 a.list_detail 안으로 스코프가 한정돼
    # 그런 오염이 구조적으로 불가능하다. seminar_live.get_live_seminar_info와
    # 같은 패턴이다.
    listed = page.evaluate(SEMINAR_LIST_JS)

    seminar_ids = []
    list_titles = {}
    seen = set()
    for item in listed or []:
        # 정상 경로는 {"id","title","applicable"}. DOM이 바뀌어 문자열만 오더라도
        # id는 건지고 제목만 포기한다 — 제목 때문에 신청 자체가 멈추면 안 된다.
        # applicable 키가 없는 옛 형태면 신청 대상으로 본다(기존 동작 유지).
        if isinstance(item, dict):
            sid = item.get("id")
            title = item.get("title") or ""
            applicable = item.get("applicable", True)
        else:
            sid, title, applicable = item, "", True
        if not sid:
            continue
        sid = str(sid)
        if sid not in seen:
            seen.add(sid)
            if applicable:
                seminar_ids.append(sid)
        list_titles[sid] = title

    applied = []
    failed = []

    # 정리(prune)는 daily가 하루 1회 맡는다. 여기서 하면 30분마다 파일이 바뀌어
    # 커밋만 늘고, 지난 세미나가 남아 있어도 "상세를 안 연다"는 동작은 옳다.
    applied_data = load_applied(applied_path)
    targets = filter_new_seminars(seminar_ids, applied_data, account) if account else list(seminar_ids)
    result["skipped_known"] = len(seminar_ids) - len(targets)
    dirty = False

    # 이미 신청한 세미나는 상세를 열지 않으므로 제목을 새로 알 길이 없다(이력에
    # 남은 제목은 대부분 오염돼 있다). 목록에서 긁은 제목은 페이지 로드 없이
    # 공짜로 얻은 것이니, 이력의 오염된 제목을 여기서 덮어써 영구히 고친다 —
    # 표에만 채워 넣던 예전 코드는 이력을 손대지 않아 오염이 계속 남았다.
    repaired = 0
    if account:
        today_str = datetime.now(common.KST).strftime("%Y-%m-%d")
        for sid, listed_title in list_titles.items():
            record = applied_data.get(account, {}).get(str(sid))
            clean = runlog.clean_title(listed_title)
            if not (record and clean):
                continue
            # 이력에 이미 멀쩡한 제목이 있으면 건드리지 않는다.
            if not runlog.clean_title(record.get("title", "")):
                record["title"] = clean
                repaired += 1
                dirty = True
            if record.get("start_date") == today_str:
                _log_seminar(sid, "already_done", account, clean, record.get("start", ""))

    # 제목 복구를 마친 뒤에 판정한다. 신청할 게 없어도 복구분은 저장해야 한다.
    if not seminar_ids:
        if dirty:
            save_applied(applied_data, applied_path)
        result["status"] = "no_target"
        result["message"] = "신청 가능한 세미나 없음"
        result["count"] = 0
        result["titles_repaired"] = repaired
        return result

    for sid in targets:
        detail_url = f"{SEMINAR_DETAIL_URL}?seminarId={sid}"
        common.goto_with_retry(page, detail_url, wait_until="domcontentloaded", timeout_ms=DEFAULT_TIMEOUT_MS)

        btn = page.locator("a.btn_bn")
        try:
            btn.wait_for(state="visible", timeout=DEFAULT_TIMEOUT_MS)
        except PlaywrightTimeoutError:
            failed.append(sid)
            continue

        btn_text = btn.inner_text() or ""
        title, start = _seminar_detail_meta(page)
        # 상세 제목은 사이트 공통 요소가 잡히는 일이 잦다. 목록 제목이 있으면 그쪽을 쓴다.
        title = runlog.clean_title(title) or runlog.clean_title(list_titles.get(sid, "")) or title

        if "신청취소" in btn_text:
            # 이미 신청되어 있다. 기록해 두면 다음 런부터 상세를 열지 않는다 —
            # 여기가 지금 낭비의 대부분이다.
            if account:
                record_applied(applied_data, account, sid, title, start)
                dirty = True
            _log_seminar(sid, "already_done", account, title, start)
            continue

        if "신청하기" not in btn_text:
            # 마감·정원초과 등 신청 불가. 신청한 게 아니므로 이력에는 기록하지 않는다
            # (기록하면 재시도를 영영 안 하게 된다). 다만 "그날 예정된 세미나"이긴
            # 하므로 표에는 마감으로 올린다 — 이게 없으면 신청 못 한 세미나가
            # 표에서 통째로 사라진다(2026-08-28 세미나 5498 누락 사례).
            _log_seminar(sid, "closed", account, title, start)
            continue

        btn.click()

        # 개인정보 동의 모달 처리
        # button.btn_confirm이 페이지 내 여러 개 존재 — visible한 "동의합니다." 버튼 우선,
        # 없으면 visible한 첫 번째 btn_confirm 클릭
        try:
            agree_btn = page.locator('button.btn_confirm:has-text("동의합니다.")')
            if agree_btn.count() > 0 and agree_btn.first.is_visible():
                agree_btn.first.click()
            else:
                confirm = page.locator("button.btn_confirm").first
                confirm.wait_for(state="visible", timeout=5000)
                confirm.click()
        except PlaywrightTimeoutError:
            pass  # 모달 없는 세미나

        # 완료 확인 — 상세 페이지 재진입 후 a.btn_bn 텍스트 = "신청취소" 검증
        common.goto_with_retry(page, detail_url, wait_until="domcontentloaded", timeout_ms=DEFAULT_TIMEOUT_MS)
        try:
            btn_text = page.locator("a.btn_bn").inner_text()
            if "신청취소" in btn_text:
                applied.append(int(sid))
                # 기록은 "신청취소" 확인 후에만 한다. 클릭했다는 사실만으로
                # 기록하면 실패한 신청을 영영 재시도하지 않게 된다.
                if account:
                    record_applied(applied_data, account, sid, title, start)
                    dirty = True
                _log_seminar(sid, "success", account, title, start)
            else:
                failed.append(sid)
                _log_seminar(sid, "unverified", account, title, start)
        except Exception:
            failed.append(sid)
            _log_seminar(sid, "unverified", account, title, start)

    if dirty:
        save_applied(applied_data, applied_path)

    result["applied"] = applied
    result["count"] = len(applied)
    result["titles_repaired"] = repaired

    skipped = result["skipped_known"]
    suffix = f" (이력으로 상세 조회 생략 {skipped}건)" if skipped else ""

    if failed:
        result["status"] = "unverified"
        result["message"] = f"신청 시도 후 상세 재확인 실패 — 완료 {len(applied)}건, 미검증 {len(failed)}건: {failed}{suffix}"
    elif applied:
        result["status"] = "success"
        result["verified_by"] = "a.btn_bn: 신청취소"
        result["message"] = f"신청 완료 {len(applied)}건{suffix}."
    elif result["skipped_known"] > 0 or dirty:
        result["status"] = "already_done"
        # 서버 확인이 아니라 로컬 이력(seminar_applied.json)에 근거한 판정이다.
        # cache: 접두사로 서버 증거와 구분되게 남긴다.
        result["verified_by"] = "cache: seminar_applied.json skipped_known"
        result["message"] = f"신규 신청 대상 없음{suffix}."
    else:
        result["status"] = "skipped"
        result["message"] = f"신청 가능한 세미나 없음{suffix}."

    return result


# ---------------------------------------------------------------------------
# 메인
def run_precheck_quiz(page, credentials_path: str = None) -> dict:
    common.goto_with_retry(page, PRODUCT_MAIN_URL, wait_until="domcontentloaded", timeout_ms=DEFAULT_TIMEOUT_MS)
    page.wait_for_timeout(2000)

    today_td = page.locator("td.today")
    if not today_td.count():
        return {"status": "not_ready", "message": "오늘 캘린더 셀 미발견"}

    next_td = today_td.locator("xpath=following-sibling::td[1]")
    if not next_td.count():
        next_td = today_td.locator("xpath=ancestor::tr/following-sibling::tr[1]/td[1]")
    if not next_td.count():
        return {"status": "not_ready", "message": "내일 캘린더 셀 미발견"}

    info = parse_calendar_cell(next_td.inner_html())
    if not info["product"]:
        return {"status": "not_ready", "message": "내일 셀 제품명 비어있음"}

    bank = load_quiz_answers()
    legacy = load_quiz_answers_legacy()

    is_matched = match_quiz_bank(info["product"], bank, legacy)
    if is_matched:
        return {
            "status": "already_done",
            "product": info["product"],
            "quiz_id": info["quiz_id"],
            "verified_by": "quiz_bank_match"
        }
    return {
        "status": "no_answer",
        "product": info["product"],
        "quiz_id": info["quiz_id"],
        "message": f"내일 퀴즈: {info['product']} — 정답 없음"
    }


# ---------------------------------------------------------------------------

def run(account: str, credentials_path: Path, headless: bool, tasks: list[str]) -> dict:
    creds = load_credentials(credentials_path, account)
    output = {
        "site": "doctorville",
        "account": account,
        "attend":  {"status": "skipped"},
        "quiz":    {"status": "skipped"},
        "seminar": {"status": "skipped"},
    }
    if "precheck_quiz" in tasks:
        output["precheck_quiz"] = {"status": "skipped"}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(locale="ko-KR", ignore_https_errors=True)
        page = context.new_page()
        page.set_default_timeout(DEFAULT_TIMEOUT_MS)

        try:
            # 최초 로그인 (출석 페이지로 이동하며 세션 확보)
            common.goto_with_retry(page, ATTEND_URL, wait_until="domcontentloaded", timeout_ms=DEFAULT_TIMEOUT_MS)
            if not ensure_logged_in(page, creds):
                for t in tasks:
                    output[t] = {"status": "failed", "message": "로그인 실패"}
                return output

            task_dispatch = {
                "attend": lambda: task_attend(page, creds),
                "quiz": lambda: task_quiz(page, creds),
                "seminar": lambda: task_seminar(page, creds, account=account),
                "precheck_quiz": lambda: run_precheck_quiz(page, str(credentials_path)),
            }
            for t in tasks:
                if t in task_dispatch:
                    try:
                        output[t] = task_dispatch[t]()
                    except Exception as e:
                        output[t] = {"status": "failed", "message": f"{t} 중 예외 발생: {e}"}
                        shot = save_screenshot(page, f"{t}_error")
                        common.log_error("doctorville", e, account=account, task=t, screenshot=shot)

        except Exception as e:
            output["error"] = f"예외 발생: {e}"
            # 예외로 중단된 태스크는 초기값 "skipped"로 남아 텔레그램에 ⏭️로 보고된다.
            # 실패를 건너뜀으로 오인하지 않도록 미실행 태스크를 failed로 바꾼다.
            for t in tasks:
                if output.get(t, {}).get("status") == "skipped":
                    output[t] = {"status": "failed", "message": f"예외 발생: {e}"}
            shot = save_screenshot(page, "error")
            common.log_error("doctorville", e, account=account, task=",".join(tasks), screenshot=shot)
        finally:
            browser.close()

    return output


def main():
    parser = argparse.ArgumentParser(description="닥터빌 일일 자동화")
    parser.add_argument(
        "--account", default="bjh7790",
        help="credentials.json 내 계정 키 (기본: bjh7790, 전체: all)"
    )
    parser.add_argument(
        "--task", default="all",
        choices=["all", "attend", "quiz", "seminar", "precheck_quiz"],
        help="실행할 태스크 (기본: all)"
    )
    parser.add_argument(
        "--credentials",
        default=str(SCRIPT_DIR.parent / "credentials.json"),
        help="credentials.json 경로 (기본: 스크립트 상위 폴더)"
    )
    parser.add_argument(
        "--headed", action="store_true",
        help="브라우저 창을 띄워서 실행 (기본: headless)"
    )
    args = parser.parse_args()

    tasks = ["attend", "quiz", "seminar"] if args.task == "all" else [args.task]
    credentials_path = Path(args.credentials)

    if args.account == "all":
        creds = common.read_credentials(credentials_path) if credentials_path.exists() else {}
        accounts = common.list_accounts(creds, "doctorville")
        if not accounts:
            accounts = ["bjh7790", "wonju"]
    else:
        accounts = [args.account]

    all_results = {}
    for acc in accounts:
        result = run(acc, credentials_path, headless=not args.headed, tasks=tasks)
        all_results[acc] = result

    if len(accounts) == 1 and args.account != "all":
        print(json.dumps(all_results[accounts[0]], ensure_ascii=False, indent=2))
    else:
        print(json.dumps(all_results, ensure_ascii=False, indent=2))

    if args.task == "seminar":
        notify_level = os.environ.get("NOTIFY_LEVEL", "all")
        date_str = datetime.now(common.KST).strftime("%Y-%m-%d")
        if notify.should_send(all_results, notify_level):
            msg = notify.build_message(all_results, notify_level, date_str)
            if msg:
                notify.send_telegram(msg, credentials_path=credentials_path)

    failed = any(
        acc_res.get(t, {}).get("status") in {"failed", "unverified", "blocked"}
        for acc_res in all_results.values()
        if isinstance(acc_res, dict)
        for t in tasks
    )
    sys.exit(1 if failed else 0)



if __name__ == "__main__":
    main()
