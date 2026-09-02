#!/usr/bin/env python3
"""
키메디(keymedi.com) 일일 출석체크 자동화 스크립트.

용법:
    python3 keymedi.py                     # 헤드리스 실행, credentials.json은 스크립트 기준 상위 폴더에서 탐색
    python3 keymedi.py --headed            # 브라우저 창을 띄워서 실행 (디버깅용)
    python3 keymedi.py --credentials PATH  # credentials.json 위치 직접 지정
    python3 keymedi.py --account bjh7790   # credentials.json 내 계정 키 지정 (기본값 bjh7790)

동작:
    1. https://www.keymedi.com/mypage/attendance 접속
    2. 로그인 필요 시 credentials.json의 id/password로 로그인
    3. 이미 출석했으면("출석완료" 버튼) 그대로 종료 (already_done)
    4. 미출석이면 "출석체크하기" 클릭 → "광고보고 출석하기" 팝업 클릭(있는 경우) →
       완료 모달 확인 후 종료 (success)
    5. 예상치 못한 화면이면 실패로 처리하고 스크린샷을 남김 (failed)

표준출력에 결과를 한 줄 JSON으로 출력한다. 예:
    {"site": "keymedi", "account": "bjh7790", "status": "success", "points": 100, "message": "..."}

CLAUDE.md 자동화 원칙 참고:
- 로그인 폼은 자동완성에 의존하지 않고 매번 fill()로 직접 입력한다
  (Chrome 확장에서 관측된 "자동완성 값이 JS에서 빈 문자열로 읽힘" 문제는
   Playwright의 실제 키 입력 이벤트에는 해당하지 않는 것으로 추정되나,
   그와 무관하게 항상 명시적으로 채워 넣는 편이 안전하다).
- "광고보고 출석하기" 클릭은 새 탭(광고)을 띄우는 경우가 있으므로 별도로 처리한다.

주의: 이 스크립트는 실행 환경(에이전트 샌드박스)에서 실제 로그인까지
end-to-end 테스트하지 못했다 — 샌드박스에 브라우저 실행에 필요한 시스템
라이브러리(libXdamage 등)가 없고, keymedi.com 자체도 샌드박스 네트워크에서
접근이 안 됐다. 로그인 폼 셀렉터(input[name=uid], input[name=password],
button:has-text("로그인"))와 출석 버튼 텍스트는 2026-07-05 실제 로그인된
세션에서 DOM을 직접 조회해 확인한 값이라 신뢰도는 높지만, 첫 실행은 반드시
--headed로 눈으로 한 번 확인할 것을 권장한다.
"""

import argparse
import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

import common

ATTENDANCE_URL = "https://www.keymedi.com/mypage/attendance"
DEFAULT_TIMEOUT_MS = 15000
SCRIPT_DIR = Path(__file__).resolve().parent


def load_credentials(path: Path, account: str) -> dict:
    data = common.read_credentials(path)
    if account not in data:
        raise KeyError(f"credentials.json에 '{account}' 계정이 없습니다.")
    if "keymedi" not in data[account]:
        raise KeyError(f"credentials.json의 '{account}' 계정에 keymedi 항목이 없습니다.")
    km = data[account]["keymedi"]
    if "id" not in km or "password" not in km:
        raise KeyError(
            f"credentials.json의 '{account}'.keymedi 에 id/password가 모두 있어야 합니다."
        )
    return km


def run(account: str, credentials_path: Path, headless: bool) -> dict:
    creds = load_credentials(credentials_path, account)
    result = {"site": "keymedi", "account": account, "status": "failed", "points": 0, "message": ""}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(locale="ko-KR")
        page = context.new_page()
        page.set_default_timeout(DEFAULT_TIMEOUT_MS)

        try:
            common.goto_with_retry(page, ATTENDANCE_URL, wait_until="domcontentloaded", timeout_ms=DEFAULT_TIMEOUT_MS)

            # 로그인 필요 여부는 URL이 아니라 폼(input[name=uid]) 가시성으로 판단한다.
            # (2026-07-06: 미로그인 시 별도 /login URL로 가지 않고 attendance URL
            #  그대로 로그인 폼이 표시되는 것을 확인. 상세 배경은 common.form_login 참조.)
            login_result = common.form_login(
                page,
                'input[name="uid"]',
                'input[name="password"]',
                'button:has-text("로그인")',
                creds["id"],
                creds["password"],
                DEFAULT_TIMEOUT_MS,
            )
            if login_result is False:
                result["message"] = "로그인 실패 — 아이디/비밀번호 또는 셀렉터 확인 필요."
                result["screenshot"] = common.save_screenshot(page, f"keymedi_{account}")
                return result
            if login_result is True:
                page.wait_for_load_state("domcontentloaded")
                # 로그인 후 attendance 페이지로 리다이렉트 안 되는 경우 대비
                if "/mypage/attendance" not in page.url:
                    common.goto_with_retry(page, ATTENDANCE_URL, wait_until="domcontentloaded", timeout_ms=DEFAULT_TIMEOUT_MS)

            # 출석 관련 버튼(출석체크하기 or 출석완료)이 실제로 로드될 때까지 대기.
            # 범용 button 출현만 기다리면 네비/기타 버튼이 먼저 뜨는 시점에 count 체크를
            # 해버려 출석 버튼이 아직 없다 → else → already_done 오판이 발생함
            # (2026-07-14 확인). 출석 버튼 중 하나가 나타날 때까지 명시적으로 기다린다.
            try:
                page.wait_for_selector(
                    'button:has-text("출석체크하기"), button:has-text("출석완료")',
                    timeout=DEFAULT_TIMEOUT_MS,
                )
            except PlaywrightTimeoutError:
                result["message"] = "출석 버튼(출석체크하기/출석완료)이 로드되지 않음 — 페이지 구조 변경 또는 로그인 실패 가능성."
                result["screenshot"] = common.save_screenshot(page, f"keymedi_{account}")
                return result

            # "출석체크하기"를 먼저 확인한다.
            # 달력형 페이지에는 이전 날짜의 "출석완료" 버튼도 여러 개 존재해서,
            # already_done을 먼저 체크하면 오늘 미출석 상태에서도 already_done을
            # 반환하는 오판이 생김(2026-07-12 확인). 순서를 뒤집어 출석체크하기가
            # 존재하면 무조건 클릭하고, 없을 때만 already_done으로 처리한다.
            #
            # 주의: is_visible() 체크를 제거함(2026-07-13 수정).
            # 달력이 세로로 길어 오늘 날짜 버튼이 초기 뷰포트 밖에 위치하는 경우
            # is_visible() → False → else 분기 → 이전 날짜 "출석완료" 를 already_done
            # 으로 오판하는 버그 재발. 버튼이 count > 0 이면 scroll 후 클릭한다.
            #
            # 재발 버그(2026-07-16, opus 자문으로 원인 확정): 바로 위 wait_for_selector가
            # OR 셀렉터라서 과거 날짜의 "출석완료" 버튼만 먼저 DOM에 붙어도 즉시 리턴되고,
            # 그 순간 attend_btn.count()를 바로 체크하면 오늘 "출석체크하기" 버튼이
            # 아직 마운트되기 전이라 0으로 읽혀 already_done 오판으로 이어질 수 있다.
            # 즉시 판단하지 않고 최대 3초(500ms x 6) 짧게 폴링해 안정화를 기다린다.
            attend_btn = page.locator('button:has-text("출석체크하기")')
            found_attend = False
            for _ in range(6):
                if attend_btn.count() > 0:
                    found_attend = True
                    break
                page.wait_for_timeout(500)

            if found_attend:
                try:
                    attend_btn.first.scroll_into_view_if_needed()
                    page.wait_for_timeout(500)
                except Exception:
                    pass
                attend_btn.first.click()
            else:
                # 출석체크하기 버튼이 없으면 → 이미 완료 또는 페이지 구조 문제.
                # already_done 오판이 반복 재발한 이력이 있어(2026-07-12~16),
                # 이 분기는 오판이어도 다음에 근거가 남도록 항상 스크린샷을 남긴다.
                already_done = page.locator('button:has-text("출석완료")')
                if already_done.count() > 0 and already_done.first.is_visible():
                    result["status"] = "already_done"
                    result["message"] = "오늘 이미 출석체크 완료된 상태로 판단(출석체크하기 버튼 미발견)."
                    result["screenshot"] = common.save_screenshot(page, f"keymedi_{account}_already_done")
                    return result
                result["message"] = "출석체크하기 버튼을 찾을 수 없음 — 페이지 구조 변경 가능성."
                result["screenshot"] = common.save_screenshot(page, f"keymedi_{account}")
                return result

            # "광고보고 출석하기" 팝업 처리 — 새 탭이 뜰 수 있으므로 대비
            ad_btn = page.locator('button:has-text("광고보고 출석하기")')
            try:
                ad_btn.wait_for(state="visible", timeout=5000)
                try:
                    with context.expect_page(timeout=3000) as new_page_info:
                        ad_btn.first.click()
                    new_page = new_page_info.value
                    new_page.close()
                except PlaywrightTimeoutError:
                    # 새 탭이 안 뜨는 경우도 있으므로 무시하고 계속 진행
                    pass
            except PlaywrightTimeoutError:
                # 팝업이 아예 안 뜨는 날도 있을 수 있음 — 바로 완료 확인으로 진행
                pass

            # 완료 모달 확인
            try:
                page.wait_for_selector("text=출석체크가 완료되었습니다", timeout=DEFAULT_TIMEOUT_MS)
                confirm_btn = page.locator('button:has-text("확인")')
                if confirm_btn.count() > 0:
                    confirm_btn.first.click()
                result["status"] = "success"
                result["verified_by"] = "modal: 출석체크가 완료되었습니다"
                result["points"] = 100
                result["message"] = "출석체크 완료, 100포인트 적립."
            except PlaywrightTimeoutError:
                result["message"] = "출석 버튼은 눌렀으나 완료 모달을 확인하지 못함."
                result["screenshot"] = common.save_screenshot(page, f"keymedi_{account}")

        except Exception as e:
            result["message"] = f"예외 발생: {e}"
            result["screenshot"] = common.save_screenshot(page, f"keymedi_{account}")
            common.log_error("keymedi", e, account=account, screenshot=result["screenshot"])
        finally:
            browser.close()

    return result


def main():
    parser = argparse.ArgumentParser(description="키메디 일일 출석체크 자동화")
    parser.add_argument("--account", default="bjh7790", help="credentials.json 내 계정 키 (기본: bjh7790)")
    parser.add_argument(
        "--credentials",
        default=str(SCRIPT_DIR.parent / "credentials.json"),
        help="credentials.json 경로 (기본: 스크립트 상위 폴더)",
    )
    parser.add_argument("--headed", action="store_true", help="브라우저 창을 띄워서 실행 (기본은 headless)")
    args = parser.parse_args()

    result = run(args.account, Path(args.credentials), headless=not args.headed)
    print(json.dumps(result, ensure_ascii=False))
    sys.exit(0 if result["status"] in ("success", "already_done") else 1)


if __name__ == "__main__":
    main()
