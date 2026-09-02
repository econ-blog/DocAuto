#!/usr/bin/env python3
"""
인터엠디(intermd.co.kr) "오늘의 퀴즈" 자동 응답 스크립트.

용법:
    python3 intermd.py                     # 헤드리스 실행
    python3 intermd.py --headed            # 브라우저 창 표시 (디버깅)
    python3 intermd.py --credentials PATH  # credentials.json 경로 지정
    python3 intermd.py --answer-file PATH  # intermd_answer.json 경로 지정

동작:
    1. https://www.intermd.co.kr/login/loginView.do 로그인 (#memberId / #memberPw)
    2. 홈에서 a#quizBtn 클릭 → 퀴즈 레이어(.quizPop__quiz) 노출
    3. intermd_answer.json의 answer가 숫자면 1-based 보기 번호로, 아니면 보기
       텍스트에 부분 포함으로 "유일하게" 매칭될 때만 그 보기를 선택하고
       button#saveBtn으로 제출
    4. 매칭 0건 또는 2건 이상이면 아무것도 클릭하지 않고 no_answer 반환
       (오늘 문항·보기 텍스트를 결과 JSON에 담아 텔레그램으로 알린다)

정답은 사용자가 텔레그램으로 `인터엠디:프로미나드`(보기 텍스트) 또는 `인터엠디 4`
(보기 번호) 형식 메시지를 보내면 telegram_inbox.py가 intermd_answer.json에
덮어쓴다(이력 누적 없음).

DOM 근거 (2026-07-27 실측):
    로그인: #memberId, #memberPw, button.loginForm__btn--login
    캡차:   #captchaText — 평소 부모 div.fail이 display:none. 로그인 실패가
            누적되면 노출된다. 보이면 캡차를 풀지 않고 failed로 중단한다.
    퀴즈:   a#quizBtn → h2.pollSurvey__title / div.pollSurvey__body
            span.inputbox__radio label > input[type=radio] + span.text
    제출:   button#saveBtn
    결과:   [data-cont="state2"] 정답 / [data-cont="state4"] 오답
            p.quizOverlap[data-cont="over"] 가시 = 이미 참여함
"""

import argparse
import json
import re
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

import common

LOGIN_URL = "https://www.intermd.co.kr/login/loginView.do"
HOME_URL = "https://www.intermd.co.kr/home.do"
DEFAULT_TIMEOUT_MS = 20000
SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_ANSWER_FILE = SCRIPT_DIR.parent / "intermd_answer.json"


def normalize(text: str) -> str:
    """공백을 하나로 접고 앞뒤를 잘라낸 비교용 문자열."""
    return re.sub(r"\s+", " ", (text or "")).strip()


def strip_question_prefix(text: str) -> str:
    """문항 텍스트 앞의 '퀴즈' 배지와 뒤의 필수표시(*)를 제거한다."""
    t = normalize(text)
    t = re.sub(r"^\[?퀴즈\]?\s*", "", t)
    return t.rstrip("*").strip()


def match_choice(answer: str, choices: list[str]) -> int | None:
    """answer가 부분 포함되는 보기의 인덱스를 반환한다.

    유일하게 매칭될 때만 인덱스를 반환하고, 0건이거나 2건 이상이면 None.
    완전 일치가 정확히 하나 있으면 부분 포함 다중 매칭보다 우선한다.

    저장값이 숫자만이면 **1-based 보기 번호**로 해석한다("4" = 네 번째 보기).
    번호는 화면에 렌더링된 순서에 의존하므로, 보기 순서가 사용자·날짜별로 섞이면
    엉뚱한 보기를 고른다. 확실히 하려면 보기 텍스트 일부를 보내는 편이 안전하다.
    """
    a = normalize(answer)
    if not a:
        return None
    if a.isdigit():
        idx = int(a) - 1
        return idx if 0 <= idx < len(choices) else None
    norm = [normalize(c) for c in choices]
    exact = [i for i, c in enumerate(norm) if c == a]
    if len(exact) == 1:
        return exact[0]
    hits = [i for i, c in enumerate(norm) if a in c]
    return hits[0] if len(hits) == 1 else None


BLOCK_PATTERNS = ("403 forbidden", "access denied", "503 service", "접근이 거부", "비정상적인 접근")


def detect_block(page) -> str:
    """WAF 차단 페이지면 감지된 문구를 반환한다. 아니면 빈 문자열.

    2026-07-28 GitHub Actions 실행에서 로그인 페이지가 통째로 `403 Forbidden`
    (Apache 기본 문서)로 내려왔다. 이때 `#memberId` 대기가 20초 타임아웃으로
    끝나 셀렉터 문제처럼 보이므로, 차단은 차단이라고 말하게 한다.
    """
    try:
        body = normalize(page.inner_text("body", timeout=3000)).lower()
    except Exception:
        return ""
    if len(body) > 300:  # 정상 페이지는 본문이 길다 — 차단 페이지는 한 줄짜리다.
        return ""
    for pat in BLOCK_PATTERNS:
        if pat in body:
            return pat
    return ""


def load_answer(path: Path | str) -> str:
    """intermd_answer.json에서 answer 문자열을 읽는다. 없으면 빈 문자열."""
    data = common.read_json(path, default={})
    return data.get("answer", "") if isinstance(data, dict) else ""


def load_credentials(path: Path, account: str) -> dict:
    data = common.read_credentials(path)
    if account not in data:
        raise KeyError(f"credentials.json에 '{account}' 계정이 없습니다.")
    im = data[account].get("intermd")
    if not im or "password" not in im:
        raise KeyError(f"credentials.json의 '{account}' 계정에 intermd.password가 없습니다.")
    # HMP와 동일 규칙: id가 없으면 계정 키 자체를 로그인 id로 사용한다.
    return {"id": im.get("id") or account, "password": im["password"]}


def read_quiz(page) -> dict:
    """퀴즈 레이어에서 문항·보기를 읽어온다."""
    return page.evaluate(
        """() => {
            const blocks = Array.from(document.querySelectorAll('div.pollSurvey__body'));
            return blocks.map(body => {
                const tit = body.previousElementSibling;
                return {
                    question: tit ? tit.innerText : '',
                    choices: Array.from(body.querySelectorAll('span.inputbox__radio label')).map(l => ({
                        text: (l.querySelector('span.text') || l).innerText,
                        name: (l.querySelector('input') || {}).name || '',
                        value: (l.querySelector('input') || {}).value || '',
                    })),
                };
            });
        }"""
    )


def run(account: str, credentials_path: Path, answer_path: Path, headless: bool = True) -> dict:
    result = {"site": "intermd", "account": account, "status": "failed", "message": ""}

    try:
        creds = load_credentials(credentials_path, account)
    except Exception as e:
        result["message"] = str(e)
        return result

    answer = load_answer(answer_path)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        # 2026-07-29: GitHub Actions에서 로그인 페이지가 403 Forbidden으로 차단됐다
        # (러너 IP/헤더 기준 WAF 추정 — 같은 런에서 키메디·닥터빌·HMP는 정상).
        # 한국어 로케일·Accept-Language를 명시해 최소한 헤더 기준 차단은 피한다.
        context = browser.new_context(
            viewport={"width": 1440, "height": 1000},
            locale="ko-KR",
            extra_http_headers={"Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7"},
        )
        page = context.new_page()
        page.set_default_timeout(DEFAULT_TIMEOUT_MS)

        # 제출 시 confirm 대화상자가 뜬다. Playwright 기본값은 자동 취소(dismiss)라
        # 핸들러가 없으면 제출이 조용히 무산된다(2026-07-27 실측).
        dialogs_seen: list[str] = []

        def on_dialog(dialog):
            dialogs_seen.append(dialog.message)
            dialog.accept()

        page.on("dialog", on_dialog)

        try:
            common.goto_with_retry(page, LOGIN_URL, wait_until="domcontentloaded", timeout_ms=DEFAULT_TIMEOUT_MS)

            # 차단 페이지(403/503)는 로그인 폼이 없으므로 #memberId 대기가 20초
            # 타임아웃으로 끝난다. 원인이 셀렉터 변경으로 오해되지 않도록 먼저 본다.
            blocked = detect_block(page)
            if blocked:
                result["message"] = f"접속 차단됨({blocked}) — 실행 환경 IP/헤더가 막혔을 가능성."
                result["screenshot"] = common.save_screenshot(page, f"intermd_{account}_blocked")
                return result

            page.wait_for_selector("#memberId", timeout=DEFAULT_TIMEOUT_MS)
            page.fill("#memberId", creds["id"])
            page.fill("#memberPw", creds["password"])
            page.click("button.loginForm__btn--login")
            page.wait_for_timeout(5000)

            captcha = page.locator("#captchaText")
            if captcha.count() > 0 and captcha.first.is_visible():
                result["message"] = "자동입력방지문구(캡차)가 노출됨 — 수동 로그인 필요."
                result["screenshot"] = common.save_screenshot(page, f"intermd_{account}_captcha")
                return result

            if "login" in page.url:
                result["message"] = f"로그인 실패 — 현재 URL: {page.url}"
                result["screenshot"] = common.save_screenshot(page, f"intermd_{account}_login")
                return result

            if "/home.do" not in page.url:
                common.goto_with_retry(page, HOME_URL, wait_until="domcontentloaded", timeout_ms=DEFAULT_TIMEOUT_MS)
            page.wait_for_timeout(2000)

            page.wait_for_selector("a#quizBtn", timeout=DEFAULT_TIMEOUT_MS)
            page.click("a#quizBtn")
            page.wait_for_timeout(4000)

            overlap = page.locator('p.quizOverlap[data-cont="over"]')
            if overlap.count() > 0 and overlap.first.is_visible():
                result["status"] = "already_done"
                result["verified_by"] = 'p.quizOverlap[data-cont="over"]'
                result["message"] = "오늘 퀴즈에 이미 참여함."
                return result

            blocks = read_quiz(page)
            if not blocks:
                result["message"] = "퀴즈 레이어에서 문항을 찾지 못함 — 페이지 구조 변경 가능성."
                result["screenshot"] = common.save_screenshot(page, f"intermd_{account}_noquiz")
                return result
            if len(blocks) != 1:
                result["status"] = "no_answer"
                result["message"] = f"문항이 {len(blocks)}개 — 1문항 전제와 달라 시도하지 않음."
                result["quiz"] = blocks
                return result

            block = blocks[0]
            question = strip_question_prefix(block["question"])
            choices = [normalize(c["text"]) for c in block["choices"]]
            result["question"] = question
            result["choices"] = choices

            if not answer:
                result["status"] = "no_answer"
                result["message"] = f"저장된 정답 없음 (텔레그램으로 `인터엠디:<정답>` 전송 필요). 문항: {question}"
                return result

            idx = match_choice(answer, choices)
            if idx is None:
                result["status"] = "no_answer"
                result["message"] = (
                    f"저장된 정답 '{answer}'이(가) 오늘 보기와 유일하게 매칭되지 않음 — 시도하지 않음. 문항: {question}"
                )
                return result

            target = block["choices"][idx]
            page.locator(
                f'input[name="{target["name"]}"][value="{target["value"]}"]'
            ).first.check(force=True)
            page.wait_for_timeout(500)
            page.click("button#saveBtn")
            page.wait_for_timeout(4000)

            correct = page.locator('[data-cont="state2"], [data-cont="state3"]')
            incorrect = page.locator('[data-cont="state4"]')
            picked = choices[idx]
            if incorrect.count() > 0 and incorrect.first.is_visible():
                result["status"] = "success"
                result["verified_by"] = "[data-cont='state4'] (오답 확인)"
                result["correct"] = False
                result["message"] = f"제출 완료(오답): '{picked}'. 저장된 정답을 갱신해주세요."
            elif correct.count() > 0 and correct.first.is_visible():
                result["status"] = "success"
                result["verified_by"] = "[data-cont='state2/state3'] (정답 확인)"
                result["correct"] = True
                result["message"] = f"제출 완료(정답): '{picked}'."
            else:
                result["status"] = "failed"
                result["message"] = (
                    f"제출 버튼을 눌렀으나 결과 표시를 확인하지 못함: '{picked}'. "
                    f"대화상자: {dialogs_seen or '없음'}"
                )
                result["screenshot"] = common.save_screenshot(page, f"intermd_{account}_result")

        except PlaywrightTimeoutError as e:
            result["message"] = f"타임아웃: {e}"
            result["screenshot"] = common.save_screenshot(page, f"intermd_{account}")
        except Exception as e:
            result["message"] = f"예외 발생: {e}"
            result["screenshot"] = common.save_screenshot(page, f"intermd_{account}")
            common.log_error("intermd", e, account=account, screenshot=result["screenshot"])
        finally:
            browser.close()

    return result


def main():
    parser = argparse.ArgumentParser(description="인터엠디 오늘의 퀴즈 자동 응답")
    parser.add_argument("--account", default="bjh7790", help="credentials.json 내 계정 키")
    parser.add_argument(
        "--credentials",
        default=str(SCRIPT_DIR.parent / "credentials.json"),
        help="credentials.json 경로",
    )
    parser.add_argument("--answer-file", default=str(DEFAULT_ANSWER_FILE), help="intermd_answer.json 경로")
    parser.add_argument("--headed", action="store_true", help="브라우저 창 표시")
    args = parser.parse_args()

    result = run(args.account, Path(args.credentials), Path(args.answer_file), headless=not args.headed)
    print(json.dumps(result, ensure_ascii=False))
    sys.exit(0 if result["status"] in ("success", "already_done", "no_answer") else 1)


if __name__ == "__main__":
    main()
