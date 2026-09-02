#!/usr/bin/env python3
"""
닥터빌 라이브 세미나 설문조사 자동 응답 스크립트.

seminar_live.py로 입장에 성공한 세미나는 방송 팝업에서 설문에 참여할 수 있다.
이 스크립트는 당일 입장 이력(scripts/state/seminar_entered.json)을 읽어 아직
설문하지 않은 세미나만 골라, 문항 종류별 규칙에 따라 응답한다.

용법:
    python3 seminar_survey.py                     # 두 계정 순회 (헤드리스)
    python3 seminar_survey.py --account bjh7790
    python3 seminar_survey.py --seminar-id 5473   # 상태 무시하고 특정 세미나만
    python3 seminar_survey.py --headed
    python3 seminar_survey.py --no-telegram

문항 3분류 (classify_question):
    - quiz    — 화면 텍스트가 `[퀴즈]`로 시작하는 선택형. 정답이 존재하므로
                추측 금지. survey_quiz_answers.json에서만 답을 찾는다.
    - text    — 입력란(textarea / input[type=text])만 있는 주관식.
                survey_text_answers.json에서 답을 찾는다.
    - general — 나머지 선택형(만족도·선호도 등). 정답이 없으므로 족보를 보지 않고
                항상 **2번 보기**를 고른다. 미등록으로 막히지 않는다.

    분류 이전에, 응답 컨트롤(라디오·체크박스·입력란)이 하나도 없는 항목은
    `resolve_page`가 건너뛴다. 안내문·읽기 전용 표시이지 문항이 아니다.

    `[퀴즈]` 배지는 같은 문항이라도 세미나에 따라 빠질 때가 있다(실측). 그래서
    배지가 없더라도 **퀴즈 족보에 키가 이미 있으면 quiz로 분류**한다 — 값이 빈
    문자열이면 general로 새는 대신 incomplete_bank로 막힌다.

문제은행 (세미나 구분 없는 단일 파일, 형식은 세 파일 모두 동일):
    { "<문항 텍스트>": "<보기 번호 또는 답변 텍스트>" }
    - 선택형: 값이 숫자만이면 1-based 보기 번호("2" = 두 번째 보기).
      숫자가 아니면 보기 텍스트에 부분 포함으로 "유일 매칭"될 때만 선택한다.
      복수 선택은 "1,3" 또는 ["1", "3"].
    - 입력형(주관식): 저장값을 그대로 입력한다.
    - 값이 빈 문자열("")이면 항상 미등록으로 취급한다.
    - 번호는 위치 기반이라 같은 문항이라도 세미나마다 보기 순서가 다르면 오답이
      될 수 있다. 순서가 흔들릴 만한 문항은 텍스트로 적어두는 편이 안전하다.

    survey_answers_legacy.json은 3분류 도입 전에 쌓인 단일 족보로, **읽기 전용
    폴백**이다. quiz/text 족보에서 못 찾으면 여기서 한 번 더 찾는다. 새 키는
    절대 쓰지 않는다(general 문항 "2"가 대부분이라 오염원이 된다).

미등록 문항이 하나라도 있으면 그 페이지를 제출하지 않고 중단한다
(status=incomplete_bank). 설문은 페이지 순차 제출형이라 뒷 페이지는 앞 페이지를
제출해야 볼 수 있으므로, 페이지 단위 검증이 도달 가능한 최대 안전선이다.

완료 판정 (2026-08-28 도입, 2026-08-31 모바일 우선으로 변경):
    제출 직후 완료 화면 문구가 아니라, **세미나 상세에 재접속했을 때** 사이트가
    보여주는 **보이는** 버튼으로 판정한다(`confirm_survey_done`). 상세는
    **m(모바일)을 먼저** 열고, 로그인 상태로 못 열리면 www로 폴백한다.
      - 완료 표시가 보이면 설문을 마친 것 → success (`detail_button: …`).
        문구는 도메인마다 다르다 — m은 '설문 참여 완료', www는 '응답완료'
        (`SURVEY_DONE_MARKERS`).
      - '세미나 종료'·'설문하기'만 보이면 아직 참여하지 않은 것
      - 둘 다 없으면 판정 불가 → unverified
    완료 화면 대조는 '제출'·'참여' 같은 흔한 단어에 걸려 오탐이 났고, 제출 후
    창이 닫혀 버리면 아예 읽을 수도 없었다. 재접속 판정에는 두 약점이 다 없어
    창이 닫힌 경우에도 성공을 확정할 수 있다.

DOM 근거 (2026-07-27 실측):
    방송 팝업: /seminar/broadcastSeminarPopup?viewType=2&seminarId=<ID>
      → a#surveyEnter("설문 참여") → button.btn_answer:has-text("설문하기")
      → survey.villeway.com 새 창(expect_popup)
    설문 폼:  form[id^="surveyForm"], 문항 li[data-question-number],
      문항 텍스트 = label > div 첫 줄, 보기 = ol li label 안의
      input[type=radio|checkbox] + span.col-start-2
    진행:     input[type=submit][value="제출하기"] (마지막 페이지)
              여러 페이지 설문은 마지막 페이지 전까지 "다음" 버튼이 대신 나온다.

    2026-08-24 세미나 5587 추가 실측 — 같은 `li[data-question-number]` 안에
    **응답 컨트롤이 없는 `<p>` 기반 항목**이 섞여 나온다. 구조는
    `span(번호 배지) | p | p`이고 `label > div`가 없다. 화면에는 보이며
    (`hidden:false`, 높이 84px) 텍스트도 44~83자로 들어 있다. 그 항목이
    무엇인지(이미 응답된 문항의 읽기 전용 표시인지 안내문인지)는 미확인 —
    MEMORY.md "설문 `<p>` 기반 항목" 참고.
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

import common
from common import KST as kst, parse_dd_date, write_json_atomic
import doctorville
import seminar_live
from seminar_live import upgrade_to_v2
import notify
import runlog

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_TIMEOUT_MS = doctorville.DEFAULT_TIMEOUT_MS
DEFAULT_QUIZ_BANK_FILE = SCRIPT_DIR.parent / "survey_quiz_answers.json"
DEFAULT_TEXT_BANK_FILE = SCRIPT_DIR.parent / "survey_text_answers.json"
DEFAULT_LEGACY_BANK_FILE = SCRIPT_DIR.parent / "survey_answers_legacy.json"
DEFAULT_STATE_FILE = SCRIPT_DIR / "state" / "seminar_entered.json"
BROADCAST_URL = "https://www.doctorville.co.kr/seminar/broadcastSeminarPopup?viewType=2&seminarId={sid}"
MAX_PAGES = 10  # 무한 루프 방지 (실측 설문은 1~2페이지)

# 설문 창은 열릴 때 즉시 열린다. 30초(DEFAULT_TIMEOUT_MS)는 "안 열림"을 확인하는
# 데만 쓰이는 시간이었다. 아직 설문이 안 열린 세미나는 done/closed가 찍힐 때까지
# 30분마다 재시도되므로, 이 대기가 설문 스텝 247초 중 180초를 먹고 있었다
# (실측: 세미나 3건 × 계정 2개 × 30초).
SURVEY_POPUP_TIMEOUT_MS = 8000

# 문항이 통째로 빈 값으로 읽혔을 때 렌더를 한 번 더 기다리는 시간.
BLANK_RETRY_WAIT_MS = 5000

# 설문 완료의 양성 증거. 제출 직후 완료 화면이 아니라 **세미나 상세에 재접속했을
# 때** 사이트가 보여주는 버튼으로 판정한다(2026-08-28 사용자 실측 화면):
#   - 설문까지 마친 세미나  → '설문 참여 완료' + '세미나 종료' 두 버튼
#   - 입장 못 했거나 제한 시간 내 미응답 → '세미나 종료' 한 버튼
# 이 두 문구는 m(모바일) 기준이다. 자동화가 도는 www(데스크톱)는 문구가 달라
# 아래 SURVEY_DONE_MARKERS / SURVEY_PENDING_MARKERS로 넓혔다(2026-08-31).
# 완료 화면 문구 대조는 '제출'·'참여' 같은 흔한 단어에 걸려 오탐이 났고, 창이
# 닫혀 버리면 아예 읽을 수도 없었다. 재접속 판정에는 두 약점이 다 없다.
SURVEY_DONE_MARKER = "설문 참여 완료"
SEMINAR_END_MARKER = "세미나 종료"

# 같은 상세 페이지라도 도메인마다 버튼 문구가 다르다. m(모바일)은 '설문 참여 완료',
# www(데스크톱)는 '응답완료'다 — 자동화가 도는 www에는 '설문 참여 완료'도
# '세미나 종료'도 없어서, 실제로 제출을 마친 설문이 계속 unverified로 떨어졌다
# (2026-08-31 세미나 5633, 두 계정 모두. 결과 JSON의 detail_buttons에
# '응답완료'·'설문하기'가 찍혀 있었다).
SURVEY_DONE_MARKERS = (
    SURVEY_DONE_MARKER,   # m(모바일)
    "응답완료",            # www(데스크톱) — 실측
    "설문 응답 완료",
    "설문 완료",
)
# 설문에 아직 참여하지 않았다는 표시. '설문하기'는 아직 누를 수 있는 버튼이므로
# 미참여다. 완료 표시와 같이 잡히면 완료가 이긴다(아래 detect_survey_marker).
SURVEY_PENDING_MARKERS = (
    SEMINAR_END_MARKER,
    "설문하기",
)

# 판정은 **모바일 상세를 먼저** 본다(2026-08-31 사용자 지시). www(데스크톱)는
# 완료 표시가 '응답완료' 한 단어뿐이고 숨은 템플릿 버튼과 섞여 있어 사람이
# 눈으로 확인하기도 어렵다. m(모바일)은 '설문 참여 완료'/'세미나 종료'가 그대로
# 뜨는 화면이라 판정도 검증도 쉽다. 모바일에서 못 읽으면 www로 폴백한다.
MOBILE_BASE = "https://m.doctorville.co.kr"
# m에는 www와 같은 /seminar/seminarDetail 경로가 없다. 2026-08-31 실측(세미나
# 5602)에서 그 주소는 '뒤로 가기 / 닥터빌로 이동하기'만 있는 안내 페이지로 떨어졌다.
# 사용자가 실제로 보는 모바일 상세는 /cme/vod/{seminarId}다.
MOBILE_DETAIL_URL = f"{MOBILE_BASE}/cme/vod"
# m 상세를 못 열었을 때 뜨는 안내 페이지의 표식. 이게 보이면 판정 불가다.
MOBILE_FALLBACK_MARKERS = ("닥터빌로 이동하기",)
# m은 UA로 데스크톱을 가려내 www로 돌려보낸다. 헤더만 바꿔도 서버 판정에는 걸린다.
MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1"
)
# 모바일 판정을 믿어도 되는지 가르는 표식. 세션 쿠키가 서브도메인으로 안 넘어가
# 로그아웃 상태로 열리면 '설문하기'만 보여 **미참여로 오판**한다. 그래서
# 로그인 증거가 없으면 모바일 판정은 통째로 버리고 www로 간다.
MOBILE_LOGIN_MARKERS = ("로그아웃", "마이페이지")

# 상세 페이지 로드 후 버튼 영역이 그려질 때까지의 여유.
DETAIL_SETTLE_MS = 2000
# m 상세는 뼈대만 먼저 그리고 버튼을 나중에 채운다. 2026-08-31 실측에서 같은
# 세미나(5602)를 같은 시각에 열었는데 한 계정은 '설문 참여 완료'를 읽었고 다른
# 계정은 '뒤로 가기' 하나만 잡혔다. 표식이 나올 때까지 짧게 더 기다린다.
MOBILE_RENDER_TIMEOUT_MS = 8000
MOBILE_POLL_MS = 500
# 제출 직후에는 표시가 아직 안 바뀌었을 수 있어 한 번 더 열어 본다.
DETAIL_RECHECK_WAIT_MS = 3000


# ---------------------------------------------------------------------------
# 순수 함수 (테스트 대상)
# ---------------------------------------------------------------------------

def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def strip_spaces(text: str) -> str:
    """공백을 전부 없앤 대조용 문자열. 사이트가 버튼 문구를 줄바꿈으로 쪼개도 걸린다."""
    return re.sub(r"\s+", "", text or "")


def detect_survey_marker(texts) -> str:
    """세미나 상세에서 읽은 문자열들로 설문 참여 여부를 판정한다.

    - ``done``     — '설문 참여 완료'가 있다. 설문을 실제로 마쳤다는 사이트의 표시.
    - ``not_done`` — '세미나 종료'만 있다. 입장을 못 했거나 제한 시간 내에
                     답을 못 낸 경우로, 설문에 참여하지 못한 상태다(실측 화면).
    - ``unknown``  — 둘 다 없다. 방송 전·중이거나 마크업이 바뀐 것.

    두 문구는 상호 배타가 아니다 — 참여 완료 화면에는 '설문 참여 완료'와
    '세미나 종료'가 나란히 뜬다. 그래서 완료 표시를 먼저 본다.
    """
    if matched_done_marker(texts):
        return "done"
    joined = " ".join(strip_spaces(t) for t in texts if t)
    if any(strip_spaces(m) in joined for m in SURVEY_PENDING_MARKERS):
        return "not_done"
    return "unknown"


def matched_done_marker(texts) -> str:
    """완료 표시 중 실제로 걸린 문구. 없으면 빈 문자열.

    `verified_by`에 무엇을 보고 성공으로 판정했는지 그대로 싣기 위해 따로 둔다 —
    도메인마다 문구가 달라서 '설문 참여 완료'로 뭉뚱그리면 증거가 사실과 어긋난다.
    """
    joined = " ".join(strip_spaces(t) for t in texts if t)
    for marker in SURVEY_DONE_MARKERS:
        if strip_spaces(marker) in joined:
            return marker
    return ""


_QUIZ_BADGE_RE = re.compile(r"^\[\s*퀴즈\s*\]\s*")


def is_quiz_badged(text: str) -> bool:
    """화면 텍스트가 `[퀴즈]` 배지로 시작하는지."""
    return bool(_QUIZ_BADGE_RE.match(normalize(text)))


def normalize_question(text: str) -> str:
    """문항 텍스트를 문제은행 키 형태로 정규화한다.

    화면 텍스트에는 `[퀴즈]` 배지와 필수 표시 `*`가 붙는데, 같은 문항이 세미나에
    따라 배지 유무만 다르게 나오는 경우가 있어 둘 다 제거하고 키로 삼는다.
    """
    t = _QUIZ_BADGE_RE.sub("", normalize(text))
    return t.rstrip("*").strip()


# 같은 문항이 세미나마다 아래 정도의 차이로 다르게 렌더된다(실측): 공백 유무
# ("30 mg"/"30mg"), 대소문자("Dapagliflozin"/"dapagliflozin"), 따옴표 종류,
# 필수·복수응답 안내 문구 유무. 이 차이만으로 문제은행이 중복 키로 불어나므로
# 대조용 정규화 키를 따로 둔다.
_ANNOTATION_PATTERNS = (
    re.compile(r"\*?\(\s*(?:최소|최대)\s*\d+\s*개\s*선택\s*\)"),
    re.compile(r"\(\s*(?:복수\s*(?:응답|선택)|중복)\s*(?:가능)?\s*\)"),
)


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
    for k, v in bank.items():
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


# 퀴즈 족보(doctorville)와 같은 문구를 쓴다. 정의는 common에 있다.
PLACEHOLDER_MARKER = common.ANSWER_PLACEHOLDER_MARKER


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
    """문제은행에서 문항의 답을 찾는다. 미등록이면 None.

    ① 정규화 키 완전 일치 → ② canonical 키 일치(공백·대소문자·구두점·안내문구
    무시) 순으로 대조한다. 유사도 기반 근사 매칭은 쓰지 않는다 — 이 문제은행에는
    "1차 예방 당뇨병 환자에서…" / "1차 예방 중등도 위험군 환자에서…"처럼
    difflib 유사도 0.92인 서로 다른 문항이 실제로 들어 있어, 근사 매칭은 오답을
    제출한다.

    빈 문자열·빈 리스트는 "채워 넣기 대기 중"이므로 미등록으로 취급한다.
    """
    answer = _coerce_answer(bank.get(normalize_question(question)))
    if answer is not None:
        return answer
    if index is None:
        index = build_canonical_index(bank)
    return _coerce_answer(index.get(canonical_question(question)))


# --- 문항 3분류 + 족보 묶음 -------------------------------------------------

GENERAL_OPTION_INDEX = 1  # 0-based → 2번 보기


def load_banks(
    quiz_path: str | Path = DEFAULT_QUIZ_BANK_FILE,
    text_path: str | Path = DEFAULT_TEXT_BANK_FILE,
    legacy_path: str | Path = DEFAULT_LEGACY_BANK_FILE,
) -> dict:
    """quiz / text / legacy 족보를 한 번에 읽어 묶는다.

    `paths`에는 **쓰기 가능한** 족보만 담는다. legacy는 읽기 전용이라 빠진다.
    """
    return {
        "quiz": load_bank(quiz_path),
        "text": load_bank(text_path),
        "legacy": load_bank(legacy_path),
        "paths": {"quiz": Path(quiz_path), "text": Path(text_path)},
        "legacy_path": Path(legacy_path),
    }


def bank_has_key(bank: dict, question: str) -> bool:
    """값의 유무와 무관하게 키 자체가 족보에 있는지(정규화·canonical 양쪽으로)."""
    if normalize_question(question) in bank:
        return True
    ck = canonical_question(question)
    return bool(ck) and ck in {canonical_question(k) for k in bank}


def classify_question(q: dict, quiz_bank: dict = None) -> str:
    """문항 종류를 'quiz' | 'text' | 'general'로 판정한다."""
    if q.get("kind") == "input":
        return "text"
    if is_quiz_badged(q.get("question", "")):
        return "quiz"
    # 배지가 빠져 렌더되는 세미나가 있어, 이미 퀴즈로 등록된 문항은 배지 없이도
    # 퀴즈로 취급한다. 그러지 않으면 정답 있는 문항에 "2번"을 제출하게 된다.
    if quiz_bank and bank_has_key(quiz_bank, q.get("question", "")):
        return "quiz"
    return "general"


def lookup_in_banks(banks: dict, question: str, kind: str, indexes: dict = None):
    """종류별 족보 → legacy 폴백 순으로 답을 찾는다. (답, 출처) 또는 (None, None).

    출처가 "legacy"면 호출자가 승격 대상으로 표시한다(promote).
    """
    if indexes is None:
        indexes = {k: build_canonical_index(banks.get(k, {})) for k in ("quiz", "text", "legacy")}
    answer = lookup_answer(banks.get(kind, {}), question, indexes.get(kind))
    if answer is not None:
        return answer, kind
    answer = lookup_answer(banks.get("legacy", {}), question, indexes.get("legacy"))
    return (answer, "legacy") if answer is not None else (None, None)


def match_option(answer: str, options: list[str]) -> int | None:
    """저장값에 해당하는 보기 인덱스. 판정 불가면 None.

    저장값이 숫자만이면 **1-based 보기 번호**로 해석한다("2" = 두 번째 보기).
    그 외에는 보기 텍스트에 부분 포함으로 유일 매칭될 때만 인정한다.

    번호 방식은 위치 기반이라, 같은 문항이라도 세미나에 따라 보기 순서가 다르면
    엉뚱한 보기를 고른다. 순서가 흔들릴 가능성이 있는 문항은 텍스트로 적어둘 것.
    """
    a = normalize(answer)
    if not a:
        return None
    if a.isdigit():
        idx = int(a) - 1
        return idx if 0 <= idx < len(options) else None
    norm = [normalize(o) for o in options]
    # 표기 그대로 → canonical(공백·대소문자·구두점 무시) 순으로, 각 단계마다
    # 완전 일치 → 부분 포함. 보기 텍스트를 답으로 적는 것이 기본 형식이라
    # 하이픈 종류("–"/"-")나 괄호 앞뒤 공백 차이로 매칭이 깨지면 안 된다.
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
    """legacy 보기 번호를 승격용 보기 텍스트로 바꾼다. 왕복 검증 실패 시 None.

    번호는 위치 기반이라 다른 세미나에서 보기 순서가 바뀌면 오답이 된다. 텍스트는
    순서에 무관하므로 승격은 항상 개선이다 — 단 **그 텍스트로 다시 찾았을 때 같은
    보기가 유일하게 나올 때만**이다. 유일하지 않으면(보기 텍스트가 서로 포함
    관계이거나 중복이면) 승격하지 않고 legacy 값을 그대로 둔다.
    """
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
    return {k: v for k, v in legacy.items() if canonical_question(k) not in targets}


def apply_promotions(banks: dict, plan: list[dict]) -> dict:
    """plan의 승격 표시를 실제 파일에 반영한다. {족보: 승격건수}.

    승격은 legacy에서 종류별 족보로 **옮기는** 것이다 — 복사만 하면 legacy가
    영영 줄지 않아 삭제할 수 없다.
    """
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
        write_json_atomic(path, dict(sorted(bank.items())))
        banks[name] = bank
        counts[name] = len(items)

    legacy_path = banks.get("legacy_path")
    if moved and legacy_path:
        pruned = _evict_legacy_keys(banks.get("legacy", {}), moved)
        if len(pruned) != len(banks.get("legacy", {})):
            write_json_atomic(Path(legacy_path), dict(sorted(pruned.items())))
            banks["legacy"] = pruned
    return counts


def resolve_page(questions: list[dict], banks: dict) -> tuple[list[dict], list[dict]]:
    """페이지의 문항들을 종류별 규칙으로 풀어 (적용계획, 미등록문항)을 만든다.

    일반 문항은 족보를 보지 않고 항상 2번 보기를 고르므로 미등록이 되지 않는다.
    퀴즈·주관식만 미등록이 될 수 있고, 미등록 항목에는 채워 넣을 족보를 가리키는
    `bank` 키가 붙는다(고를 보기 자체가 없으면 None).

    응답 컨트롤이 없는 항목(kind="unknown")은 계획에도 미등록에도 넣지 않는다.
    """
    plan, missing = [], []
    indexes = {k: build_canonical_index(banks.get(k, {})) for k in ("quiz", "text", "legacy")}
    for q in questions:
        text = q.get("question", "")
        options = [normalize(o["text"]) for o in q.get("options", [])]

        def _miss(bank_name):
            missing.append({
                "question": normalize_question(text),
                "options": [f"{i + 1}. {o}" for i, o in enumerate(options)],
                # 족보에 깔아둘 보기 원문(번호 없음) — 저장값 형식이 보기 텍스트라
                # 사람이 한 줄 남기면 그대로 매칭된다.
                "option_texts": list(options),
                "bank": bank_name,
            })

        if q.get("kind") == "unknown":
            # 라디오·체크박스·입력란이 하나도 없는 항목. 답할 컨트롤이 없으므로
            # 문항이 아니라 안내문·읽기 전용 표시다(2026-08-24 세미나 5587 실측:
            # `<p>` 두 개로만 된 항목 10건). 미등록으로 막지 않고 건너뛴다.
            # 만약 이것이 실제로는 답해야 하는 필수 문항이었다면 진행 버튼이
            # 먹지 않아 `seen_pages` 지문 검사가 잡는다 — 오답이 제출되지는 않는다.
            continue

        kind = classify_question(q, banks.get("quiz", {}))

        if kind == "general":
            if len(options) <= GENERAL_OPTION_INDEX:
                # 보기가 2개 미만이면 "2번"이 존재하지 않는다. DOM 이상이므로
                # 아무 보기나 찍지 않고 사람이 보게 남긴다.
                _miss(None)
                continue
            plan.append({
                "kind": "choice",
                "targets": [q["options"][GENERAL_OPTION_INDEX]],
            })
            continue

        answer, source = lookup_in_banks(banks, text, kind, indexes)
        if answer is None:
            _miss(kind)
            continue

        if q.get("kind") == "input":
            if isinstance(answer, list):
                answer = " ".join(answer)
            step = {"kind": "input", "name": q["name"], "value": answer}
            if source == "legacy":
                step["promote"] = {"bank": kind, "question": normalize_question(text), "answer": answer}
            plan.append(step)
            continue

        # 복수 선택은 리스트(["1", "3"])뿐 아니라 "1,3" 형태도 받는다.
        if isinstance(answer, str) and "," in answer:
            parts = [p.strip() for p in answer.split(",")]
            answer = parts if all(p.isdigit() for p in parts if p) else answer
        wanted = answer if isinstance(answer, list) else [answer]
        indices = []
        for w in wanted:
            idx = match_option(w, options)
            if idx is None:
                indices = None
                break
            indices.append(idx)
        if indices is None:
            _miss(kind)
            continue
        step = {"kind": "choice", "targets": [q["options"][i] for i in indices]}
        if source == "legacy":
            promoted = promotable_option_texts(indices, options)
            if promoted is not None:
                step["promote"] = {
                    "bank": kind,
                    "question": normalize_question(text),
                    "answer": promoted,
                }
        plan.append(step)
    return plan, missing


def get_survey_window(item: dict) -> tuple[datetime | None, datetime | None]:
    """세미나 설문 가능 시간 창 (open_dt, close_dt) 반환.

    시작 시간 30분 후 ~ 끝나는 시간 1시간 후.
    """
    if not isinstance(item, dict):
        return None, None
    start_str = item.get("start")
    if start_str and isinstance(start_str, str):
        s_dt, e_dt = parse_dd_date(start_str)
        if s_dt:
            open_dt = s_dt + timedelta(minutes=30)
            end_dt = e_dt or (s_dt + timedelta(hours=1))
            close_dt = end_dt + timedelta(hours=1)
            return open_dt, close_dt
        m = re.search(r"(\d{4}-\d{2}-\d{2})\s*\([^)]+\)\s*(\d{2}:\d{2})", start_str)
        if m:
            d_str, s_str = m.groups()
            try:
                s_dt = datetime.strptime(f"{d_str} {s_str}", "%Y-%m-%d %H:%M").replace(tzinfo=common.KST)
                return s_dt + timedelta(minutes=30), s_dt + timedelta(hours=2)
            except ValueError:
                pass

    ent_str = item.get("entered_at")
    if ent_str and isinstance(ent_str, str):
        try:
            ent_dt = datetime.fromisoformat(ent_str)
            if ent_dt.tzinfo is None:
                ent_dt = ent_dt.replace(tzinfo=common.KST)
            return ent_dt + timedelta(minutes=30), ent_dt + timedelta(hours=2)
        except (ValueError, TypeError):
            pass
    return None, None


def get_survey_cutoff(item: dict) -> datetime | None:
    """설문 마감 시각 (종료 1시간 후)."""
    _, close_dt = get_survey_window(item)
    return close_dt


def evaluate_survey_cutoff(item: dict, now_dt: datetime = None) -> str:
    """설문 시도 가능 여부 판정.
    - 'ready': 설문 가능 시간대 (시작 30분 후 ~ 종료 1시간 후, 또는 시간 정보 없음)
    - 'not_ready': 설문 시작 전 (시작 30분 후 이전)
    - 'closed': 설문 마감 후 (종료 1시간 후 경과)
    """
    if now_dt is None:
        now_dt = datetime.now(common.KST)
    elif now_dt.tzinfo is None:
        now_dt = now_dt.replace(tzinfo=common.KST)

    open_dt, close_dt = get_survey_window(item)
    if open_dt is not None and now_dt < open_dt:
        return "not_ready"
    if close_dt is not None and now_dt > close_dt:
        return "closed"
    return "ready"


def placeholder_value(option_texts: list[str] | None):
    """족보에 깔아둘 미기입 값. 보기가 있으면 [표시, 보기…], 없으면 빈 문자열.

    주관식은 고를 보기가 없으므로 종전대로 빈 문자열이다.
    """
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
        write_json_atomic(bank_path, dict(sorted(bank.items())))
    return added


def add_missing_to_banks(banks: dict, missing: list[dict]) -> dict:
    """미등록 문항을 `bank` 키가 가리키는 족보에 나눠 넣는다. {족보: 추가건수}.

    `bank`가 None인 항목(보기 자체가 없는 DOM 이상)은 어디에도 쓰지 않는다.
    legacy는 읽기 전용이라 `banks["paths"]`에 없고, 따라서 절대 갱신되지 않는다.
    """
    counts = {}
    for name, path in banks.get("paths", {}).items():
        items = [m for m in missing if m.get("bank") == name]
        if items:
            counts[name] = add_missing_to_bank(path, items)
    return counts


BANK_LABELS = {"quiz": "퀴즈", "text": "주관식"}


def format_bank_counts(counts: dict) -> str:
    """{'quiz': 2, 'text': 1} → '퀴즈 2건, 주관식 1건'."""
    parts = [f"{BANK_LABELS.get(k, k)} {v}건" for k, v in sorted(counts.items()) if v]
    return ", ".join(parts) if parts else "추가 없음"


def pending_seminar_ids(state: dict, account: str) -> list[int]:
    """당일 입장했으나 아직 설문하지 않은 세미나 ID 목록."""
    if not isinstance(state, dict):
        return []
    state = upgrade_to_v2(state)
    acc = state.get("accounts", {}).get(account, {})
    survey_map = acc.get("survey", {})
    survey_done_list = acc.get("survey_done", [])
    pending = []
    for item in acc.get("entered", []):
        sid = item["id"] if isinstance(item, dict) else int(item)
        sid_str = str(sid)
        if sid_str not in survey_map and sid not in survey_done_list and int(sid) not in survey_done_list:
            pending.append(sid)
    return pending


def get_entered_item(state: dict, account: str, seminar_id: int | str) -> dict:
    if isinstance(state, dict):
        acc = state.get("accounts", {}).get(account, {})
        for item in acc.get("entered", []):
            if isinstance(item, dict) and str(item.get("id")) == str(seminar_id):
                return item
            elif isinstance(item, int) and str(item) == str(seminar_id):
                return {"id": item}
    return {"id": int(seminar_id) if str(seminar_id).isdigit() else seminar_id}


def mark_survey_status(state: dict, account: str, seminar_id: int | str, status_str: str = "done", path=None) -> None:
    if not isinstance(state, dict):
        return
    state = upgrade_to_v2(state)
    acc = state.setdefault("accounts", {}).setdefault(account, {})
    survey = acc.setdefault("survey", {})
    sid_str = str(seminar_id)
    survey[sid_str] = status_str
    if path is not None:
        seminar_live.save_state(state, path)


def mark_survey_done(state: dict, account: str, seminar_id: int | str, path=None) -> None:
    mark_survey_status(state, account, seminar_id, "done", path)


SURVEY_STATUS_PRIORITY = (
    "failed", "unverified", "incomplete_bank", "success", "already_done", "not_ready", "closed"
)


def rollup_account_status(statuses: list[str]) -> str:
    """Compute top-level account survey status from individual survey statuses."""
    if not statuses:
        return "no_target"
    return next((s for s in SURVEY_STATUS_PRIORITY if s in statuses), statuses[0])


def rollup_verified_by(surveys: list[dict]) -> str:
    """계정 레벨 양성 증거. 성공한 설문이 전부 verified_by를 가질 때만 생성한다.

    없으면 notify가 계정 노드를 unverified(alert)로 강등한다.
    """
    evidence = [s.get("verified_by") for s in surveys if s.get("status") == "success"]
    if not evidence or not all(evidence):
        return ""
    return f"surveys_verified: {len(evidence)}건"



# ---------------------------------------------------------------------------
# 브라우저 동작
# ---------------------------------------------------------------------------

def read_questions(survey_page) -> list[dict]:
    """현재 설문 페이지의 문항·보기·입력란을 읽는다."""
    return survey_page.evaluate(
        """() => Array.from(document.querySelectorAll('li[data-question-number]')).map(li => {
            // 문항 텍스트 자리가 세미나마다 다르다. 선택형은 label > div,
            // 안내문·읽기 전용 항목은 p다(2026-08-24 세미나 5587 실측).
            // 셀렉터 목록은 문서 순서상 먼저 나오는 요소를 주므로 둘 다 커버된다.
            const head = li.querySelector('label > div, p');
            const question = head ? (head.innerText || '').split('\\n')[0] : '';
            const qnum = li.getAttribute('data-question-number');
            const options = Array.from(li.querySelectorAll('input[type=radio], input[type=checkbox]')).map((inp, i) => {
                const lbl = inp.closest('label');
                const span = lbl ? lbl.querySelector('span') : null;
                // 만족도 척도형 문항은 보기 텍스트가 input을 감싼 label이 아니라
                // 같은 id를 가리키는 별도 label[for]에 들어 있다.
                let text = span ? span.innerText : '';
                if (!(text || '').trim() && inp.id) {
                    const outer = document.querySelector('label[for="' + inp.id + '"]:not(:has(input))');
                    if (outer) text = outer.innerText;
                }
                return { text, name: inp.name, value: inp.value, type: inp.type,
                         id: inp.id, qnum, index: i };
            });
            const free = li.querySelector('textarea, input[type=text]');
            return {
                number: qnum,
                question,
                kind: options.length ? 'choice' : (free ? 'input' : 'unknown'),
                name: free ? free.name : (options[0] || {}).name || '',
                options,
            };
        })"""
    )


def is_blank_question(q: dict) -> bool:
    """문항 텍스트·보기·입력란이 모두 비어 있는 문항(추출 실패 의심).

    2026-08-24 세미나 5587(전공의를 위한 응급실 증례강의)에서 `li[data-question-number]`
    10건이 전부 이 상태로 읽혔다. 문항이 실제로 비어 있을 수는 없으므로 렌더가
    아직 안 끝났거나 마크업이 다른 경우다.
    """
    return (
        not normalize(q.get("question", ""))
        and not q.get("options")
        and q.get("kind") != "input"
    )


def dump_survey_dom(survey_page, seminar_id) -> str:
    """설문 페이지 DOM을 logs/에 저장하고 경로를 반환한다(실패 시 빈 문자열).

    문항 추출이 통째로 실패했을 때 마크업을 확인할 유일한 수단이다. 설문 페이지에는
    이름·소속이 들어가므로 **artifact 전용이며 커밋하지 않는다**(scripts/logs/는
    gitignore 대상).
    """
    try:
        html = survey_page.content()
    except Exception:
        return ""
    common.LOG_DIR.mkdir(exist_ok=True)
    ts = datetime.now(kst).strftime("%Y%m%d_%H%M%S")
    path = common.LOG_DIR / f"survey_{seminar_id}_dom_{ts}.html"
    try:
        path.write_text(html, encoding="utf-8")
    except OSError:
        return ""
    return str(path)


def probe_questions(survey_page) -> list[dict]:
    """빈 문항의 원인을 가리는 구조 정보만 읽는다(문항 텍스트는 담지 않는다).

    innerText는 비었는데 textContent가 있으면 요소가 숨겨진 것(렌더 안 됨)이고,
    둘 다 비었으면 내용 자체가 아직 없는 것이다. `head`가 null이면 문항 텍스트가
    `label > div`가 아닌 다른 자리에 있다는 뜻이다. 길이·태그명만 담으므로 이름·
    소속 같은 개인정보가 로그로 새지 않는다.
    """
    try:
        return survey_page.evaluate(
            """() => Array.from(document.querySelectorAll('li[data-question-number]')).map(li => {
                const head = li.querySelector('label > div');
                const r = li.getBoundingClientRect();
                return {
                    n: li.getAttribute('data-question-number'),
                    li_inner: (li.innerText || '').length,
                    li_text: (li.textContent || '').length,
                    head_inner: head ? (head.innerText || '').length : -1,
                    head_text: head ? (head.textContent || '').length : -1,
                    hidden: li.offsetParent === null,
                    height: Math.round(r.height),
                    fields: Array.from(li.querySelectorAll('input, select, textarea'))
                        .map(e => e.tagName.toLowerCase() + ':' + (e.type || '')).join(','),
                    kids: Array.from(li.children)
                        .map(e => e.tagName.toLowerCase() + '.' + String(e.className || '').slice(0, 30)).join(' | '),
                    iframes: li.querySelectorAll('iframe').length,
                };
            })"""
        )
    except Exception:
        return []


def dismiss_alerts(survey_page, max_rounds: int = 3) -> list[str]:
    """설문 창의 headlessui 모달(알림)을 닫는다. 닫은 메시지 목록을 반환.

    임시저장 초안이 있으면 "작성 중인 정보를 불러왔습니다" 알림이 뜨는데, 이 모달의
    backdrop이 포인터 이벤트를 가로채 제출 버튼 클릭이 타임아웃된다(2026-07-28 실측).
    """
    closed = []
    for _ in range(max_rounds):
        # 모달 루트는 크기가 0이라 is_visible()이 False다. 존재 여부로만 판단한다.
        dialog = survey_page.locator('[role="dialog"][data-headlessui-state="open"]')
        if dialog.count() == 0:
            break
        text = normalize(dialog.first.inner_text())
        btn = dialog.first.locator('button:has-text("확인"), button:has-text("닫기")')
        if btn.count() == 0:
            btn = dialog.first.locator("button")
        if btn.count() == 0:
            break
        btn.first.click()
        survey_page.wait_for_timeout(1000)
        closed.append(text)
    return closed


def classify_advance_label(label: str) -> str | None:
    """버튼 라벨을 진행 종류로 분류한다. 'next' | 'submit' | None(누르면 안 되는 버튼).

    "임시저장"에 '저장'이 들어가고 "이전"도 버튼이라, 눌러도 되는 라벨만 화이트리스트로
    받는다.
    """
    t = normalize(label)
    if not t:
        return None
    if any(bad in t for bad in ("이전", "취소", "임시", "저장", "닫기", "목록")):
        return None
    if "다음" in t:
        return "next"
    if "제출" in t or "완료" in t:
        return "submit"
    return None


def page_fingerprint(questions: list[dict]) -> str:
    """페이지 식별자. 제출 후에도 같은 페이지면 진행이 막힌 것이다."""
    return "|".join(f"{q.get('number')}:{normalize_question(q.get('question', ''))}" for q in questions)


def body_text(survey_page) -> str:
    try:
        if survey_page.is_closed():
            return ""
        return survey_page.evaluate("() => document.body ? document.body.innerText : ''") or ""
    except Exception:
        return ""


ADVANCE_SELECTOR = (
    'input[type=submit], button[type=submit], input[type=button], button, a[role="button"]'
)


def find_advance_button(survey_page):
    """페이지 진행 버튼을 찾는다. (locator, 'next'|'submit') 또는 (None, None).

    설문이 여러 페이지면 마지막 페이지 전까지는 "제출하기"가 아니라 "다음"이 나온다.
    둘 다 보이면 'next'를 먼저 누른다 — 남은 페이지를 건너뛰고 제출해 버리는 쪽이
    더 위험하기 때문이다.
    """
    candidates = survey_page.locator(ADVANCE_SELECTOR)
    picked = {}
    for i in range(candidates.count()):
        el = candidates.nth(i)
        try:
            if not el.is_visible():
                continue
            label = el.get_attribute("value") or el.inner_text() or ""
        except Exception:
            continue
        kind = classify_advance_label(label)
        if kind and kind not in picked:
            picked[kind] = el
    for kind in ("next", "submit"):
        if kind in picked:
            return picked[kind], kind
    return None, None


def option_locator(survey_page, opt: dict):
    """보기 input의 로케이터. name이 비어 있는 문항이 있어 id → name → 위치 순으로 찾는다."""
    if opt.get("id"):
        return survey_page.locator(f'[id="{opt["id"]}"]')
    if opt.get("name"):
        return survey_page.locator(
            f'input[name="{opt["name"]}"][value="{opt["value"]}"]'
        ).first
    return survey_page.locator(
        f'li[data-question-number="{opt["qnum"]}"] input[type=radio], '
        f'li[data-question-number="{opt["qnum"]}"] input[type=checkbox]'
    ).nth(opt["index"])


def apply_plan(survey_page, plan: list[dict]) -> None:
    for step in plan:
        if step["kind"] == "input":
            survey_page.locator(f'[name="{step["name"]}"]').first.fill(step["value"])
            continue
        for t in step["targets"]:
            option_locator(survey_page, t).check(force=True)


# 상세 페이지의 버튼 텍스트를 긁는다. 브라우저에 넘기는 JS는 r-문자열로 쓴다
# (2026-08-28 사고: 일반 문자열의 \n이 파이썬 단계에서 줄바꿈이 되어 SyntaxError).
DETAIL_BUTTON_JS = r"""
() => {
    const sel = 'a.btn_bn, .btn_area a, .btn_area button, .btn_wrap a, .btn_wrap button, '
              + 'a[class*="btn"], button[class*="btn"], button, input[type=button], input[type=submit]';
    const out = [];
    document.querySelectorAll(sel).forEach(el => {
        const t = ((el.innerText || el.textContent || el.value || '') + '').replace(/\s+/g, ' ').trim();
        if (!t || t.length > 40) return;
        const cs = window.getComputedStyle(el);
        const visible = el.getClientRects().length > 0
                     && cs.visibility !== 'hidden'
                     && cs.display !== 'none'
                     && cs.opacity !== '0';
        out.push({t: t, v: visible});
    });
    return out;
}
"""


def read_detail_buttons(page) -> tuple[list[str], list[str]]:
    """세미나 상세의 버튼 텍스트를 (보이는 것, 숨은 것)으로 갈라 돌려준다.

    상세 페이지에는 안 보이는 팝업·템플릿 버튼이 잔뜩 들어 있다(실측: 로그아웃,
    '동의합니다.', '세미나 제안 제출' …). 그 안에 '설문하기'와 '응답완료'가 같이
    있어서 전부 뭉쳐 보면 상태를 가릴 수 없다. 그래서 판정은 보이는 것만 쓴다.

    읽기에 실패하면 두 목록 모두 빈 목록이다 — 여기서 죽으면 설문 전체가 죽는다.
    """
    seen, visible, hidden = set(), [], []
    try:
        for entry in page.evaluate(DETAIL_BUTTON_JS) or []:
            # 예전 형식(문자열 목록)도 받아 준다 — 판정 불가로 버리는 것보다 낫다.
            if isinstance(entry, dict):
                t, is_visible = normalize(entry.get("t")), bool(entry.get("v"))
            else:
                t, is_visible = normalize(entry), True
            key = (t, is_visible)
            if not t or key in seen:
                continue
            seen.add(key)
            (visible if is_visible else hidden).append(t)
    except Exception:
        return [], []
    return visible, hidden


def confirm_survey_done(page, seminar_id, retries: int = 0) -> tuple[str, list[str]]:
    """세미나 상세에 재접속해 완료 표시로 설문 완료 여부를 판정한다.

    **모바일(m) 상세를 먼저 보고, 판정이 안 서면 www로 폴백한다**(2026-08-31).
    m은 사용자가 눈으로 확인하는 화면 그대로 '설문 참여 완료'/'세미나 종료'가
    떠서 판정도 검증도 쉽다. m이 로그아웃 상태로 열리거나 www로 튕기면 그
    판정은 통째로 버린다 — 로그아웃 화면의 '설문하기'를 미참여로 읽으면
    실제로 마친 설문을 놓친다.

    반환: (판정, 상세에서 읽은 버튼 텍스트들). 판정은 done / not_done / unknown.
    버튼 텍스트를 함께 돌려주는 이유는, 사이트가 문구를 바꿨을 때 결과 JSON만
    보고도 무엇이 있었는지 알 수 있어야 하기 때문이다.

    retries는 판정이 done이 아닐 때 다시 열어 보는 횟수다. 제출 직후에는 표시가
    아직 안 바뀌었을 수 있어 1회를 준다.
    """
    errors: list[str] = []
    buttons: list[str] = []
    verdict = "unknown"
    LAST_DETAIL_PROBE.clear()
    for attempt in range(retries + 1):
        if attempt:
            page.wait_for_timeout(DETAIL_RECHECK_WAIT_MS)

        # ① 모바일 상세 — 문구가 사람이 보는 화면과 같아 우선한다.
        verdict, buttons, err = read_detail_verdict(page, seminar_id, mobile=True)
        if err:
            errors.append(err)
        if verdict == "done":
            return verdict, buttons

        # ② www 상세 — 모바일이 판정 불가일 때만. 여기서 not_done을 덮어쓰지
        #    않도록, 모바일이 낸 not_done은 www가 done일 때만 뒤집힌다.
        m_verdict, m_buttons = verdict, buttons
        verdict, buttons, err = read_detail_verdict(page, seminar_id, mobile=False)
        if err:
            errors.append(err)
        if verdict == "unknown" and m_verdict != "unknown":
            verdict, buttons = m_verdict, m_buttons
        if verdict == "done":
            return verdict, buttons

    if verdict == "unknown" and not buttons and errors:
        return "unknown", errors
    return verdict, buttons


# 마지막 상세 조회의 원본 기록(도메인별 URL·보이는 버튼·숨은 버튼). 진단 전용이며
# 판정에는 쓰지 않는다 — 판정이 안 설 때 무엇을 봤는지 결과 JSON에 실어 보낸다.
LAST_DETAIL_PROBE: dict = {}


def copy_probe() -> dict:
    """진단 기록의 스냅샷. 결과 JSON에 실리므로 문자열만 담는다."""
    return {
        host: {
            "url": str(rec.get("url") or ""),
            "visible": [str(t) for t in rec.get("visible") or []],
            "hidden": [str(t) for t in rec.get("hidden") or []],
        }
        for host, rec in LAST_DETAIL_PROBE.items()
    }


def read_detail_verdict(page, seminar_id, mobile: bool) -> tuple[str, list[str], str]:
    """상세 1회 조회. (판정, 판정에 쓴 버튼들, 실패 사유) — 실패해도 예외는 안 낸다."""
    detail_url = (
        f"{MOBILE_DETAIL_URL}/{seminar_id}"
        if mobile
        else f"{doctorville.SEMINAR_DETAIL_URL}?seminarId={seminar_id}"
    )
    try:
        if mobile:
            page.set_extra_http_headers({"User-Agent": MOBILE_UA})
        try:
            common.goto_with_retry(
                page, detail_url, wait_until="domcontentloaded", timeout_ms=DEFAULT_TIMEOUT_MS
            )
            page.wait_for_timeout(DETAIL_SETTLE_MS)
        finally:
            if mobile:
                page.set_extra_http_headers({})
    except Exception as e:
        return "unknown", [], f"{'m' if mobile else 'www'} 상세 재접속 실패: {e}"

    visible, hidden = read_detail_buttons(page)
    body = body_text(page)
    if mobile:
        # 버튼이 아직 안 그려졌을 수 있다. 표식이 잡히거나 시간이 다 될 때까지만.
        waited = 0
        while (
            waited < MOBILE_RENDER_TIMEOUT_MS
            and detect_survey_marker((visible or hidden) + [body]) == "unknown"
        ):
            page.wait_for_timeout(MOBILE_POLL_MS)
            waited += MOBILE_POLL_MS
            visible, hidden = read_detail_buttons(page)
            body = body_text(page)
    # 보이는 버튼이 하나도 없으면 읽기 자체가 실패한 것이다. 그때만 숨은 것까지
    # 본다 — 평소에 숨은 템플릿을 섞으면 '응답완료'가 늘 걸려 오판이 된다.
    buttons = visible or hidden

    try:
        final_url = str(page.url or "")
    except Exception:
        final_url = ""
    LAST_DETAIL_PROBE["m" if mobile else "www"] = {
        "url": final_url,
        "visible": visible,
        "hidden": hidden,
    }

    verdict = detect_survey_marker(buttons)
    if verdict == "unknown":
        # 버튼 셀렉터가 안 맞을 수도 있으니 본문 전체로 한 번 더 본다.
        verdict = detect_survey_marker([body])

    if mobile:
        if not is_mobile_session(page, buttons + [body]):
            return "unknown", [], "m 상세: www로 리다이렉트됐거나 안내 페이지"
        # 완료 표시는 그대로 믿는다. 반대로 '미참여'는 로그아웃 화면에서도 똑같이
        # 보이므로(로그인 증거가 없으면 '설문하기'만 뜬다) 채택하지 않는다.
        if verdict == "not_done" and not has_login_evidence(buttons + [body]):
            return "unknown", buttons, "m 상세: 로그인 증거 없이 미참여로 보임 — 판정 보류"
    return verdict, buttons, ""


def has_login_evidence(texts) -> bool:
    joined = " ".join(strip_spaces(t) for t in texts if t)
    return any(strip_spaces(m) in joined for m in MOBILE_LOGIN_MARKERS)


def is_mobile_session(page, texts) -> bool:
    """모바일 상세가 실제로 열렸는지. www로 튕겼거나 안내 페이지면 판정 불가다."""
    try:
        url = page.url or ""
    except Exception:
        url = ""
    if url and not url.startswith(MOBILE_BASE):
        return False
    joined = " ".join(strip_spaces(t) for t in texts if t)
    return not any(strip_spaces(m) in joined for m in MOBILE_FALLBACK_MARKERS)


def finalize_after_submit(page, seminar_id, pages_done: int, title: str = "") -> dict:
    """제출한 뒤 상세를 재확인해 success / unverified를 가른다."""
    verdict, buttons = confirm_survey_done(page, seminar_id, retries=1)
    prefix = f"[{title}] " if title else ""
    out = {"pages": pages_done}
    if verdict == "done":
        marker = matched_done_marker(buttons) or SURVEY_DONE_MARKER
        out["status"] = "success"
        out["verified_by"] = f"detail_button: {marker}"
        out["message"] = f"{prefix}설문 제출 완료({pages_done}페이지) — 상세에서 '{marker}' 확인."
        return out

    out["status"] = "unverified"
    out["detail_buttons"] = buttons
    # 사이트가 또 다른 문구를 쓰는지 다음 런에서 바로 보이도록 도메인별 원본을 남긴다.
    if LAST_DETAIL_PROBE:
        out["detail_probe"] = copy_probe()
    hidden = read_detail_buttons(page)[1]
    if hidden:
        out["detail_buttons_hidden"] = hidden
    reason = (
        f"'{SEMINAR_END_MARKER}'만 표시됨(설문 미참여)"
        if verdict == "not_done"
        else f"완료 표시({', '.join(SURVEY_DONE_MARKERS)})를 찾지 못함"
    )
    out["message"] = (
        f"{prefix}설문 제출({pages_done}페이지) 후 상세 재확인 — {reason}. "
        f"버튼: {', '.join(buttons) if buttons else '없음'}"
    )
    out["screenshot"] = common.save_screenshot(page, f"survey_{seminar_id}_unverified")
    return out


def open_survey(page, seminar_id) -> tuple[object, str]:
    """방송 팝업에서 설문 창을 연다. (설문 페이지, 실패 사유) 중 하나를 반환."""
    common.goto_with_retry(
        page, BROADCAST_URL.format(sid=seminar_id), wait_until="domcontentloaded", timeout_ms=DEFAULT_TIMEOUT_MS
    )
    page.wait_for_timeout(3000)

    enter = page.locator("a#surveyEnter")
    if enter.count() == 0 or not enter.first.is_visible():
        return None, "설문 참여 버튼이 없음(설문 미제공 또는 종료)."
    enter.first.click()
    page.wait_for_timeout(2000)

    # 개인정보 활용 동의 안내 레이어 — 정책상 항상 동의한다.
    start = page.locator('button.btn_answer:has-text("설문하기")')
    if start.count() == 0:
        return None, "설문 안내 레이어에서 '설문하기' 버튼을 찾지 못함."
    try:
        with page.expect_popup(timeout=SURVEY_POPUP_TIMEOUT_MS) as popup_info:
            start.first.click()
        survey_page = popup_info.value
    except PlaywrightTimeoutError:
        return None, "설문 창(팝업)이 열리지 않음 — 이미 참여했거나 마감되었을 수 있음."

    # 제출 confirm이 뜨는 경우 Playwright 기본값은 자동 취소(dismiss)라 제출이
    # 조용히 무산된다(intermd.py에서 실측된 문제). 설문은 항상 수락한다.
    survey_page.on("dialog", lambda d: d.accept())
    survey_page.wait_for_load_state("domcontentloaded", timeout=DEFAULT_TIMEOUT_MS)
    survey_page.wait_for_timeout(5000)
    dismiss_alerts(survey_page)
    return survey_page, ""


def run_survey(
    page,
    item_or_id,
    bank_paths: dict = None,
    now_dt: datetime = None,
    now_kst: datetime = None,
    state: dict = None,
    state_file: Path = None,
    account: str = None,
) -> dict:
    """세미나 1건의 설문을 처리한다.

    bank_paths: {"quiz": ..., "text": ..., "legacy": ...} (없는 키는 기본 경로).
    """
    bank_paths = bank_paths or {}
    if now_dt is None:
        now_dt = now_kst

    if isinstance(item_or_id, dict):
        item = item_or_id
        seminar_id = item.get("id")
    else:
        seminar_id = item_or_id
        item = {"id": int(seminar_id) if str(seminar_id).isdigit() else seminar_id}

    title = item.get("title") or ""
    sid_val = int(seminar_id) if str(seminar_id).isdigit() else seminar_id
    result = {"seminarId": sid_val, "status": "failed", "message": ""}
    if title:
        result["title"] = title

    timing = evaluate_survey_cutoff(item, now_dt)
    if timing != "ready":
        result["status"] = timing
        if timing == "closed" and state is not None and account:
            mark_survey_status(state, account, sid_val, "closed", state_file)
        prefix = f"[{title}] " if title else ""
        if timing == "not_ready":
            result["message"] = f"{prefix}설문 시작 전 (시작 30분 후부터 가능)."
        elif timing == "closed":
            result["message"] = f"{prefix}설문 마감 (종료 1시간 후 경과)."
        else:
            result["message"] = f"{prefix}{timing}"
        return result

    if page is None:
        result["status"] = "ready"
        prefix = f"[{title}] " if title else ""
        result["message"] = f"{prefix}ready: page is None"
        return result

    survey_page, err = open_survey(page, seminar_id)
    if survey_page is None:
        prefix = f"[{title}] " if title else ""
        # 설문 창이 안 열리는 이유는 "아직 안 열림"과 "이미 참여함" 두 가지인데
        # 팝업만 봐서는 구분이 안 됐다. 상세의 '설문 참여 완료'가 그걸 가른다 —
        # 이력 파일이 날아갔거나 사용자가 손으로 응답한 경우가 여기로 온다.
        verdict, detail_buttons = confirm_survey_done(page, seminar_id)
        if verdict == "done":
            marker = matched_done_marker(detail_buttons) or SURVEY_DONE_MARKER
            result["status"] = "already_done"
            result["verified_by"] = f"detail_button: {marker}"
            result["message"] = f"{prefix}이미 설문 참여 완료 — 상세에서 '{marker}' 확인."
            if state is not None and account:
                mark_survey_status(state, account, sid_val, "done", state_file)
            return result

        st = evaluate_survey_cutoff(item, now_dt)
        result["status"] = "closed" if st == "closed" else "not_ready"
        if result["status"] == "closed" and state is not None and account:
            mark_survey_status(state, account, sid_val, "closed", state_file)
        result["message"] = f"{prefix}{result['status']}: {err}"
        # 창이 안 열렸는데 완료 판정도 안 서면, 사이트 문구를 모르는 것이다.
        # 이 갈림길에 증거를 안 남기면 다음 런에서도 똑같이 깜깜하다.
        result["detail_verdict"] = verdict
        result["detail_buttons"] = detail_buttons
        if LAST_DETAIL_PROBE:
            result["detail_probe"] = copy_probe()
        return result

    try:
        pages_done = 0
        seen_pages = []
        promoted = {}
        for _ in range(MAX_PAGES):
            questions = read_questions(survey_page)
            if any(is_blank_question(q) for q in questions):
                # 렌더가 덜 끝났을 수 있으니 한 번 더 읽는다. 그래도 비어 있으면
                # 마크업이 다른 것이므로 스크린샷·DOM을 남겨 다음 작업 거리로 삼는다.
                # 한 페이지에 정상 문항과 빈 문항이 섞여 나오므로(2026-08-24 세미나
                # 5587) 전부가 아니라 하나라도 비면 걸러야 한다.
                survey_page.wait_for_timeout(BLANK_RETRY_WAIT_MS)
                questions = read_questions(survey_page)
                blanks = [q for q in questions if is_blank_question(q)]
                if blanks:
                    result["blank_questions"] = len(blanks)
                    result["questions_total"] = len(questions)
                    result["blank_probe"] = probe_questions(survey_page)
                    result["screenshot"] = common.save_screenshot(
                        survey_page, f"survey_{seminar_id}_blank"
                    )
                    result["dom_dump"] = dump_survey_dom(survey_page, seminar_id)
            if not questions:
                if pages_done:
                    result.update(finalize_after_submit(page, seminar_id, pages_done, title))
                else:
                    st = evaluate_survey_cutoff(item, now_dt)
                    result["status"] = "closed" if st == "closed" else "not_ready"
                    if result["status"] == "closed" and state is not None and account:
                        mark_survey_status(state, account, sid_val, "closed", state_file)
                    prefix = f"[{title}] " if title else ""
                    result["message"] = f"{prefix}{result['status']}: 설문 창에 문항이 없음."
                return result

            # 진행 버튼을 눌렀는데 같은 문항이 다시 나오면 앞으로 못 간 것이다
            # (필수 미응답 등). 같은 페이지를 반복 제출하지 않고 끊는다.
            fp = page_fingerprint(questions)
            if fp in seen_pages:
                stuck_verdict, stuck_buttons = (
                    confirm_survey_done(page, seminar_id) if pages_done else ("unknown", [])
                )
                if stuck_verdict == "done":
                    marker = matched_done_marker(stuck_buttons) or SURVEY_DONE_MARKER
                    result["status"] = "success"
                    result["verified_by"] = f"detail_button: {marker}"
                    result["pages"] = pages_done
                    result["message"] = f"설문 제출 완료({pages_done}페이지) — 상세에서 '{marker}' 확인."
                    return result
                result["status"] = "failed"
                result["pages"] = pages_done
                result["message"] = f"{pages_done}페이지 진행 후에도 같은 문항이 다시 표시됨 — 중단."
                result["screenshot"] = common.save_screenshot(survey_page, f"survey_{seminar_id}_stuck")
                return result
            seen_pages.append(fp)

            banks = load_banks(
                bank_paths.get("quiz", DEFAULT_QUIZ_BANK_FILE),
                bank_paths.get("text", DEFAULT_TEXT_BANK_FILE),
                bank_paths.get("legacy", DEFAULT_LEGACY_BANK_FILE),
            )
            plan, missing = resolve_page(questions, banks)
            static_items = sum(1 for q in questions if q.get("kind") == "unknown")
            if static_items:
                result["static_items"] = static_items
            if missing:
                counts = add_missing_to_banks(banks, missing)
                result["status"] = "incomplete_bank"
                result["missing"] = missing
                result["questions"] = missing
                prefix = f"[{title}] " if title else ""
                result["message"] = (
                    f"{prefix}{pages_done + 1}페이지에 미등록 문항 {len(missing)}건 — 제출하지 않음"
                    f"({format_bank_counts(counts)} 빈 값 추가)."
                )
                return result

            for name, n in apply_promotions(banks, plan).items():
                promoted[name] = promoted.get(name, 0) + n
            if promoted:
                result["promoted"] = dict(promoted)

            apply_plan(survey_page, plan)
            dismiss_alerts(survey_page)
            advance, kind = find_advance_button(survey_page)
            if advance is None:
                result["message"] = f"{pages_done + 1}페이지에서 제출/다음 버튼을 찾지 못함."
                result["screenshot"] = common.save_screenshot(survey_page, f"survey_{seminar_id}_nosubmit")
                return result
            advance.click()
            survey_page.wait_for_timeout(5000)
            pages_done += 1

            if not survey_page.is_closed():
                dismiss_alerts(survey_page)

            if kind == "submit" and common.is_recon_enabled():
                try:
                    from recon import dump_recon_data
                    url_str = ""
                    body_500 = ""
                    if not survey_page.is_closed():
                        url_str = survey_page.url
                        body_500 = body_text(survey_page)[:500]
                    r1_data = {
                        "url": url_str,
                        "body": body_500,
                        "pages_done": pages_done,
                        "seminarId": seminar_id,
                    }
                    dump_recon_data("R1", r1_data, page=survey_page if not survey_page.is_closed() else None)
                except Exception:
                    pass

            if survey_page.is_closed():
                # 창이 닫히면 완료 화면은 읽을 수 없다. 예전엔 여기서 무조건
                # unverified였지만, 판정 근거가 상세 페이지로 옮겨 왔으므로
                # 창이 닫힌 것은 더 이상 판정을 막지 않는다. 마지막이 "다음"이라
                # 남은 페이지를 못 채웠다면 상세에 완료 표시가 안 뜬다.
                result.update(finalize_after_submit(page, seminar_id, pages_done, title))
                return result

            # 완료 판정은 다음 반복의 "문항 없음" 분기가 맡는다. 여기서 상세를
            # 매 페이지 열어 보면 다중 페이지 설문이 중간에 성공으로 끊길 수 있고
            # (앞 세미나 응답의 잔상), 페이지마다 왕복이 붙는다.

        result["message"] = f"페이지가 {MAX_PAGES}회를 넘어 중단."
        return result
    finally:
        try:
            if not survey_page.is_closed():
                survey_page.close()
        except Exception:
            pass


run_survey_for_item = run_survey


def _log_seminar(seminar_id, status: str, account: str, item: dict = None) -> None:
    """세미나 표의 '설문' 칸을 채운다. 로깅 실패가 설문 자체를 죽이면 안 된다."""
    item = item or {}
    try:
        runlog.update_seminar(
            seminar_id, phase="survey", status=status, account=account or "_",
            title=item.get("title") or "", start=item.get("start") or "",
        )
    except Exception as e:
        print(f"[seminar_survey] 세미나 로그 기록 실패({seminar_id}): {e}", file=sys.stderr)


def run_account(
    account: str,
    credentials_path: Path,
    bank_paths: dict,
    headless: bool,
    seminar_ids: list,
    state: dict = None,
    state_file: Path = None,
) -> dict:
    output = {"site": "doctorville_survey", "account": account, "status": "no_target", "surveys": []}

    if not seminar_ids:
        output["message"] = "설문 대상 세미나 없음."
        return output

    try:
        creds = doctorville.load_credentials(credentials_path, account)
    except KeyError as e:
        output["status"] = "failed"
        output["message"] = str(e)
        return output

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(locale="ko-KR", ignore_https_errors=True)
        page = context.new_page()
        page.set_default_timeout(DEFAULT_TIMEOUT_MS)

        try:
            common.goto_with_retry(page, doctorville.ATTEND_URL, wait_until="load", timeout_ms=DEFAULT_TIMEOUT_MS)
            if not doctorville.ensure_logged_in(page, creds):
                output["status"] = "failed"
                output["message"] = "로그인 실패"
                return output

            for sid in seminar_ids:
                try:
                    item = get_entered_item(state, account, sid)
                    r = run_survey(page, item, bank_paths, state=state, state_file=state_file, account=account)
                except Exception as e:
                    r = {"seminarId": int(sid) if str(sid).isdigit() else sid, "status": "failed", "message": f"예외 발생: {e}"}
                output["surveys"].append(r)
                _log_seminar(sid, r["status"], account, item if isinstance(item, dict) else {})
                if r["status"] in ("success", "already_done") and state is not None:
                    mark_survey_status(state, account, sid, "done", state_file)
                elif r["status"] == "closed" and state is not None:
                    mark_survey_status(state, account, sid, "closed", state_file)

            statuses = [r["status"] for r in output["surveys"]]
            output["status"] = rollup_account_status(statuses)
            verified = rollup_verified_by(output["surveys"])
            if output["status"] == "success" and verified:
                output["verified_by"] = verified
            output["message"] = (
                f"성공 {statuses.count('success')}건, 이미완료 {statuses.count('already_done')}건, "
                f"미등록 {statuses.count('incomplete_bank')}건, "
                f"마감 {statuses.count('closed')}건, 미오픈 {statuses.count('not_ready')}건, "
                f"실패 {statuses.count('failed')}건."
            )
        except Exception as e:
            output["status"] = "failed"
            output["message"] = f"예외 발생: {e}"
            output["screenshot"] = common.save_screenshot(page, f"survey_{account}")
        finally:
            browser.close()

    return output





def main():
    parser = argparse.ArgumentParser(description="닥터빌 세미나 설문조사 자동 응답")
    parser.add_argument("--account", default="all", help="계정 ID (all 지정 시 전체 계정)")
    parser.add_argument("--credentials", default=str(SCRIPT_DIR.parent / "credentials.json"))
    parser.add_argument("--quiz-bank-file", default=str(DEFAULT_QUIZ_BANK_FILE), help="퀴즈 족보 경로")
    parser.add_argument("--text-bank-file", default=str(DEFAULT_TEXT_BANK_FILE), help="주관식 족보 경로")
    parser.add_argument("--legacy-bank-file", default=str(DEFAULT_LEGACY_BANK_FILE), help="구 단일 족보(읽기 전용) 경로")
    parser.add_argument("--state-file", default=str(DEFAULT_STATE_FILE), help="seminar_entered.json 경로")
    parser.add_argument("--seminar-id", action="append", help="상태 무시하고 특정 세미나만 처리(반복 지정 가능)")
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--no-telegram", action="store_true")
    args = parser.parse_args()

    date_str = datetime.now(common.KST).strftime("%Y-%m-%d")
    creds = common.read_credentials(Path(args.credentials))
    accounts = common.list_accounts(creds, "doctorville") if args.account == "all" else [args.account]

    state = None
    state_file = None
    if not args.seminar_id:
        state_file = Path(args.state_file)
        if not state_file.exists():
            print(json.dumps(
                {"site": "doctorville_survey", "status": "skipped", "message": "입장 이력 파일 없음 — 설문 대상 없음."},
                ensure_ascii=False,
            ))
            sys.exit(0)
        state = seminar_live.load_state(state_file, date_str)

    results = {}
    for account in accounts:
        ids = args.seminar_id if args.seminar_id else pending_seminar_ids(state, account)
        results[account] = run_account(
            account,
            Path(args.credentials),
            {
                "quiz": Path(args.quiz_bank_file),
                "text": Path(args.text_bank_file),
                "legacy": Path(args.legacy_bank_file),
            },
            headless=not args.headed,
            seminar_ids=ids,
            state=state,
            state_file=state_file,
        )
        print(json.dumps(results[account], ensure_ascii=False))

    print("\n=== 최종 결과 ===")
    print(json.dumps(results, ensure_ascii=False, indent=2))

    if not args.no_telegram and any(r.get("surveys") for r in results.values()):
        notify_level = notify.resolve_level(os.environ.get("NOTIFY_LEVEL"))
        if notify.should_send(results, notify_level):
            msg = notify.build_message(results, notify_level, date_str)
            if msg:
                ok = notify.send_telegram(msg, credentials_path=args.credentials)
                print(f"[telegram] {'성공' if ok else '실패'}")

    failed = any(
        r.get("status") in {"failed", "unverified", "blocked"}
        for r in results.values()
    )
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()

