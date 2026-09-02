#!/usr/bin/env python3
"""
HMP(hmp.co.kr) 일일 캡슐 출석체크 자동화 스크립트.
keymedi.py와 동일한 구조 — 로그인 방식과 완료 판정 로직만 HMP에 맞게 교체.

용법:
    python3 hmp.py                     # 헤드리스 실행, credentials.json은 스크립트 기준 상위 폴더에서 탐색
    python3 hmp.py --headed            # 브라우저 창을 띄워서 실행 (디버깅용)
    python3 hmp.py --credentials PATH  # credentials.json 위치 직접 지정
    python3 hmp.py --account bjh7790   # credentials.json 내 계정 키 지정 (기본값 bjh7790)

동작:
    1. https://www.hmp.co.kr/event/attendanceRouletteMain.hm 접속
    2. 로그인 필요 시 credentials.json의 id/password로 로그인
       (hmp 계정에 별도 id가 없으면 계정 키 자체를 id로 사용 — 예: "bjh7790".
        HMP 로그인 자동완성값도 이메일이 아닌 "bjh7790" 형태로 확인됨, keymedi와 동일 패턴)
    3. 이미 캡슐을 받았으면(#capsuleBtnComplete 표시) 그대로 종료 (already_done)
    4. 미완료면 #capsuleBtn 클릭 → "#10rewardPopup" 완료 팝업의 확인 버튼 클릭 후 종료 (success)
    5. 예상치 못한 화면이면 실패로 처리하고 스크린샷을 남김 (failed)

표준출력에 결과를 한 줄 JSON으로 출력한다. 예:
    {"site": "hmp", "account": "bjh7790", "status": "success", "points": 10, "message": "..."}
    ("points"는 HMP 단위상 정확히는 "캡슐" 수량이다.)

셀렉터 확인 근거 (2026-07-06, Claude in Chrome MCP로 실제 로그인 세션에서 DOM 직접 조회):
    - 로그인 폼: input[name="memId"], input[name="passwd"], 제출 버튼 button.btn_login (텍스트 "로그인")
      페이지: https://www.hmp.co.kr/login/loginForm.hm (미로그인 시 attendance URL 요청도
      이 URL로 redirect되고, 로그인 성공 후 원래 요청한 URL로 다시 돌아옴 — keymedi와
      달리 실제로 /login 경로로 리다이렉트되는 것을 확인함).
    - 캡슐 버튼: <button id="capsuleBtn" class="on"> (미완료 시 표시) /
      <button id="capsuleBtnComplete" class="off"> (완료 시 표시) — 두 버튼 모두 DOM에
      항상 존재하고 display:none/block으로 토글되는 구조. count()로는 구분 안 되므로
      반드시 가시성(is_visible)으로 판단해야 한다.
    - 완료 팝업: <div id="10rewardPopup"> 내부에 "확인" 텍스트 버튼(별도 class/id 없음).

룰렛(연속 10·20·30일)·지식커뮤니티 댓글·글쓰기도 이 스크립트에 포함되어 운영 중이다
(`_run_roulette` / `_run_comment` / `_run_post`).

주의: 이 스크립트도 keymedi.py와 마찬가지로 에이전트 샌드박스에서 실제 로그인까지
end-to-end 테스트하지 못했다 (샌드박스가 hmp.co.kr에 접근 불가). 첫 실행은 반드시
--headed로 한 번 확인할 것.
"""

import argparse
from datetime import datetime
import json
import re
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

import common
import notify

ATTENDANCE_URL = "https://www.hmp.co.kr/event/attendanceRouletteMain.hm"
COMM_HOME_URL = "https://www.hmp.co.kr/new/knowcomm/knowCommHome.hm"
COMM_DETAIL_URL = "https://www.hmp.co.kr/new/knowcomm/knowCommBoardDetail.hm"
# "나의 작성 글" 팝업. knowCommMyInfo.hm의 $KnowCommMyInfo.openMyPopup('BOARD')이
# window.open 하는 URL과 동일하며, 직접 GET으로도 열린다 (정찰 2026-08-03).
MY_POST_URL = "https://www.hmp.co.kr/new/knowcomm/knowCommMyInfoPopup.hm?schGbn=BOARD"
DEFAULT_TIMEOUT_MS = 15000
# 댓글 대상 후보 수 — 목록 상단 고정 게시물(공지·[지식스폰서])에 이미 옛날 댓글이
# 달려 있어도 그 아래 최신글까지 훑어보기 위한 여유분.
MAX_COMMENT_CANDIDATES = 8
SCRIPT_DIR = Path(__file__).resolve().parent


def verify_comment_saved(page_content: str, nickname: str) -> bool:
    if not page_content or not nickname:
        return False
    return nickname in page_content


def load_credentials(path: Path, account: str) -> dict:
    data = common.read_credentials(path)
    if account not in data:
        raise KeyError(f"credentials.json에 '{account}' 계정이 없습니다.")
    if "hmp" not in data[account]:
        raise KeyError(f"credentials.json의 '{account}' 계정에 hmp 항목이 없습니다.")
    hmp = data[account]["hmp"]
    if "password" not in hmp:
        raise KeyError(f"credentials.json의 '{account}'.hmp 에 password가 있어야 합니다.")
    # hmp 항목에 별도 id가 없으면 계정 키 자체를 로그인 id로 사용한다
    # (keymedi와 동일 패턴 — 로그인 id가 이메일이 아니라 "bjh7790" 같은 plain id).
    login_id = hmp.get("id", account)
    return {"id": login_id, "password": hmp["password"]}


def _wait_for_dialogs(page, dialogs_seen: list, expected: int = 2, timeout_ms: int = 8000,
                      step_ms: int = 250) -> None:
    """confirm+alert 두 다이얼로그가 모두 잡힐 때까지 짧게 폴링한다.

    예전엔 무조건 8초를 잤다. 다이얼로그는 on_dialog 핸들러가 즉시 기록하므로
    보통 1~3초면 끝난다 — 나머지는 순수 낭비였다. 못 채우면 예전과 같이
    timeout_ms까지 기다린 뒤 빠진다(판정 로직은 그대로).
    """
    waited = 0
    while waited < timeout_ms:
        if len(dialogs_seen) >= expected:
            # 마지막 다이얼로그 직후 DOM 갱신 여유
            page.wait_for_timeout(300)
            return
        page.wait_for_timeout(step_ms)
        waited += step_ms


def _run_roulette(page, account: str) -> list[dict]:
    """연속 출석 룰렛 자동화 (10·20·30일 달성 시 버튼 활성화).

    참여 가능한 "룰렛 참여하기" 버튼(visible)을 모두 찾아 순서대로 처리한다.

    확인된 흐름 (2026-07-14, 2026-07-15 보정):
      1. "룰렛 참여하기" 버튼 클릭
      1b. (뜰 때도/안 뜰 때도 있음) 참여 여부 확인 팝업(.pop.cont, onclick=
          "roueletteAttendYnPopup(N)") → "확인"/"예" 클릭으로 닫기.
          2026-07-15: 이 팝업을 처리하지 않아 #startAbled가 안 뜨고(1차 시도 실패),
          팝업이 안 닫힌 채 남아 재시도 때 버튼을 가려 클릭이 막히는 연쇄 실패 발생.
      2. 룰렛 휠이 페이지에 표시됨
      3. #startAbled 버튼 클릭 → POST /ajax/event/rouelettePercentage.hm 호출
      4. 결과 팝업 표시 (이미지 alt = "[마일리지] X 캡슐 적립 완료" 또는 상품권 텍스트)
      5. "확인" 버튼 클릭으로 닫기

    결과 구조:
      [{"status": "success"|"failed", "points": int, "message": str}, ...]
    """
    results = []

    for _attempt in range(3):  # 최대 3회 (10·20·30일)
        # 참여 가능한 버튼(visible) 재탐색 — 클릭 후 버튼이 "참여 완료"로 바뀌므로 매번 새로 찾는다
        all_btns = page.locator('button').all()
        avail = [b for b in all_btns if b.is_visible() and b.inner_text().strip() == "룰렛 참여하기"]

        if not avail:
            break  # 더 이상 가능한 룰렛 없음

        slot = {"status": "failed", "points": 0, "message": ""}

        try:
            avail[0].click()
            page.wait_for_timeout(1000)

            # "룰렛 참여하기" 클릭 시 곧장 휠이 뜨는 게 아니라 참여 여부를 묻는
            # 확인 팝업(.pop.cont, onclick="roueletteAttendYnPopup(N)")이 먼저 뜨는
            # 경우가 있다(2026-07-15 확인). 안 닫으면 다음 재시도 때 이 팝업이
            # 버튼을 가려 클릭이 계속 막힌다 — 있으면 확인/예 버튼을 눌러 닫는다.
            for i in range(page.locator('.pop.cont').count()):
                cand = page.locator('.pop.cont').nth(i)
                if cand.is_visible():
                    confirm_btn = cand.get_by_text(re.compile("확인|예"))
                    if confirm_btn.count() > 0:
                        confirm_btn.first.click()
                        page.wait_for_timeout(1000)
                    break

            # START 버튼 클릭
            start_btn = page.locator('#startAbled')
            try:
                start_btn.wait_for(state="visible", timeout=5000)
            except PlaywrightTimeoutError:
                slot["status"] = "no_target"
                slot["message"] = "START 버튼이 표시되지 않음"
                results.append(slot)
                continue

            start_btn.click()

            # 룰렛 애니메이션 대기 (약 4초) + 결과 팝업 대기
            page.wait_for_timeout(5000)

            # 당첨 결과 읽기 — 각 캡슐 수량에 대응하는 이미지 alt로 판별
            won_capsules = 0
            won_msg = ""
            for amount in [1500, 1000, 700, 500, 200, 100]:
                img = page.locator(f'img[alt="[마일리지] {amount} 캡슐 적립 완료"]')
                if img.count() > 0 and img.first.is_visible():
                    won_capsules = amount
                    won_msg = f"{amount}캡슐 당첨"
                    break

            # 상품권 당첨 확인
            if not won_msg:
                if page.locator('text=GS25 5000원 상품권').count() > 0 and \
                   page.locator('text=GS25 5000원 상품권').first.is_visible():
                    won_msg = "GS25 5000원 상품권 당첨"
                elif page.locator('text=스타벅스').count() > 0 and \
                     page.locator('text=스타벅스').first.is_visible():
                    won_msg = "스타벅스 아이스 아메리카노 당첨"

            if not won_msg:
                won_msg = "룰렛 참여 완료 (결과 팝업 감지 실패)"

            # 확인 버튼 클릭 (보이는 것만)
            for cb in page.locator('button:has-text("확인")').all():
                if cb.is_visible():
                    cb.click()
                    page.wait_for_timeout(500)
                    break

            if won_msg == "룰렛 참여 완료 (결과 팝업 감지 실패)":
                slot["status"] = "unverified"
                slot["points"] = won_capsules
                slot["message"] = won_msg
            else:
                slot["status"] = "success"
                slot["verified_by"] = f"alt: {won_msg}"
                slot["points"] = won_capsules
                slot["message"] = won_msg

        except Exception as e:
            slot["message"] = f"룰렛 처리 예외: {e}"
            common.save_screenshot(page, f"hmp_{account}_roulette")

        results.append(slot)

    return results


def _collect_board_seqs(page, limit: int = MAX_COMMENT_CANDIDATES) -> list[str]:
    """목록에서 게시물 boardSeq를 모아 **최신순(번호 내림차순)** 으로 반환한다.

    목록 최상단 몇 개는 고정(공지·[지식스폰서]) 게시물이라 날짜와 무관하게 항상
    같은 자리에 있다(2026-07-29 실측: 2518741·2501691·2496228이 최신글 2522297보다
    위에 노출). boardSeq는 증가하는 값이므로 내림차순 정렬로 실제 최신글을 앞에 둔다.
    """
    onclicks = page.locator('a[onclick*="goDetail"]').evaluate_all(
        "els => els.map(e => e.getAttribute('onclick') || '')"
    )
    return parse_board_seqs(onclicks, limit)


def parse_board_seqs(onclicks: list[str], limit: int = MAX_COMMENT_CANDIDATES) -> list[str]:
    """onclick 문자열 목록에서 boardSeq를 뽑아 중복 제거 후 내림차순 정렬한다."""
    seqs = []
    for oc in onclicks:
        m = re.search(r"goDetail\('(\d+)'\)", oc or "")
        if m and m.group(1) not in seqs:
            seqs.append(m.group(1))
    return sorted(seqs, key=int, reverse=True)[:limit]


def _run_comment(page, account: str) -> dict:
    """지식커뮤니티에서 **아직 내 댓글이 없는 최신 게시물**에 '감사합니다' 댓글 작성.

    흐름:
      1. knowCommHome.hm → 게시물 boardSeq 수집 후 최신순 정렬 (onclick 속성 파싱)
      2. 앞에서부터 knowCommBoardDetail.hm?boardSeq=XXXX 로 이동 (GET, 서버가 session에 저장)
      3. #cmtDiv .cmtName 에 내 닉네임이 있으면 **다음 후보로 넘어간다**
      4. textarea[name="cmtCntnt"]에 '감사합니다' 입력 → 등록하기 클릭
      5. confirm 다이얼로그(지식내공 안내) 수락 → 저장 완료 alert 수신 → success 반환

    버그 수정 (2026-07-29): 예전에는 목록 첫 링크 하나만 보고 내 댓글이 있으면
    already_done으로 끝냈다. 그런데 첫 링크는 고정 게시물이라 매일 같은 글이고,
    거기 남긴 옛날 댓글 때문에 실제로는 오늘 댓글을 하나도 안 쓰고 "작성 완료"로
    보고했다. 이제 후보를 순회해 내 댓글이 없는 글을 찾아 실제로 작성한다.

    셀렉터 확인 근거 (2026-07-15, Claude in Chrome MCP로 실제 로그인 세션에서 DOM 직접 조회):
      - 목록 링크: a[onclick*="goDetail"] — onclick 값: $KnowCommHome.goDetail('XXXXXX')
      - 상세 URL: GET /new/knowcomm/knowCommBoardDetail.hm?boardSeq=XXXX 접근 가능
        (파라미터 없는 URL로 리다이렉트되지만 서버 세션에 boardSeq 저장됨)
      - 댓글 textarea: textarea[name="cmtCntnt"], placeholder "답변을 입력해주세요."
      - 등록 버튼: form.cmtForm button[onclick*="saveCmt"] (type=button)
      - 등록하기 클릭 시 confirm → 수락 → AJAX POST /ajax/knowcomm/insertKnowCommComments.hm
      - 성공(rtn_code=="100"): alert "저장 완료"
      - 오류: alert "오류 발생.." 또는 "본인 인증 실패" → 페이지 reload
      - 내 닉네임: form.cmtForm 안 첫 번째 SPAN 텍스트 (예: "두들")
      - 기존 댓글 작성자: #cmtDiv .cmtName (JS로 동적 렌더링, 페이지 로드 후 ~2초 대기)

    버그 수정 (2026-07-16): 게시물에 기존 댓글이 있으면 그 댓글의 답글/수정 폼도
    동일한 textarea[name="cmtCntnt"]를 가져 매칭이 2개 이상 되면서 strict mode
    violation이 났다. 새 댓글 폼은 #cmtDiv 바깥의 form.cmtForm 중 값이 비어있는
    것으로 스코프해서 찾는다(자세한 내용은 아래 구현 참조).
    """
    result: dict = {"status": "failed", "message": "", "board_seq": ""}

    common.goto_with_retry(page, COMM_HOME_URL, wait_until="load", timeout_ms=DEFAULT_TIMEOUT_MS)
    page.wait_for_timeout(2000)

    try:
        page.locator('a[onclick*="goDetail"]').first.wait_for(state="visible", timeout=10000)
    except PlaywrightTimeoutError:
        result["message"] = "게시물 목록을 찾을 수 없음 — 셀렉터 변경 가능성."
        result["screenshot"] = common.save_screenshot(page, f"hmp_{account}_comment")
        return result

    seqs = _collect_board_seqs(page)
    if not seqs:
        result["message"] = "boardSeq 추출 실패 — onclick 형식 변경 가능성."
        result["screenshot"] = common.save_screenshot(page, f"hmp_{account}_comment")
        return result

    # 한 게시물의 로딩 지연·오류가 나머지 후보 시도를 막지 않도록, 실패는 기록만 하고
    # 다음 후보로 넘어간다(2026-07-30: 상세 goto 타임아웃 1건에 댓글 작업 전체가 중단됐음).
    skipped = []
    failures = []
    for board_seq in seqs:
        r = _comment_on_board(page, account, board_seq)
        if r["status"] == "already_done":
            skipped.append(board_seq)
            continue
        if r["status"] == "failed":
            failures.append(r)
            continue
        return r

    if failures:
        last = failures[-1]
        last["message"] = f"후보 {len(failures)}개 게시물 모두 실패. 마지막 오류: {last['message']}"
        return last

    if skipped:
        result["status"] = "already_done"
        result["verified_by"] = "cmtName_match"
        result["board_seq"] = skipped[0]
        result["message"] = f"후보 {len(skipped)}개 게시물에 이미 모두 댓글 작성됨 (게시물 {', '.join(skipped)})."
    else:
        # 후보 자체가 없는 것은 "이미 했다"가 아니라 "할 대상이 없다"다.
        # already_done으로 뭉뚱그리면 증거 없는 완료로 남는다.
        result["status"] = "no_target"
        result["board_seq"] = ""
        result["message"] = "댓글 작성 가능한 후보 게시물 없음"
    return result


def _comment_on_board(page, account: str, board_seq: str) -> dict:
    """게시물 1건에 댓글을 작성한다. 이미 내 댓글이 있으면 already_done."""
    result: dict = {"status": "failed", "message": "", "board_seq": board_seq}

    try:
        # 2. 상세 페이지로 이동 (GET 파라미터로 접근, 서버가 세션에 boardSeq 저장)
        # wait_until="load"는 광고·트래커 등 서브리소스까지 기다려서 상세 페이지에서만
        # 15초를 넘기는 일이 잦았다(2026-07-30 CI, boardSeq=2523310 3회 재시도 전부 타임아웃).
        # 아래에서 어차피 2초 대기 + 개별 요소 wait_for로 렌더링을 기다리므로
        # domcontentloaded로 충분하다.
        common.goto_with_retry(
            page,
            f"{COMM_DETAIL_URL}?boardSeq={board_seq}",
            wait_until="domcontentloaded",
            timeout_ms=DEFAULT_TIMEOUT_MS,
        )
        page.wait_for_timeout(2000)  # JS 렌더링 대기 (#cmtDiv 동적 주입)

        # 3. 내 닉네임 추출 (cmtForm 첫 SPAN = 현재 로그인 사용자 표시명)
        my_name = ""
        try:
            my_name = page.locator("form.cmtForm span").first.inner_text(timeout=3000).strip()
        except PlaywrightTimeoutError:
            pass

        # 4. 이미 내 댓글이 있으면 이 게시물은 건너뛴다(호출자가 다음 후보로 넘어감)
        if my_name:
            existing_names = [n.strip() for n in page.locator("#cmtDiv .cmtName").all_inner_texts()]
            if my_name in existing_names:
                result["status"] = "already_done"
                result["verified_by"] = f"cmtName: {my_name}"
                result["message"] = f"이미 댓글 있음 (게시물 {board_seq}, 닉네임 {my_name})."
                return result

        # 5. 댓글 입력창 열기
        # 2026-07-16 실제 로컬 실행으로 재확인: 댓글 작성 폼(form.cmtForm)은 댓글이
        # 0개인 게시물에서도 기본 상태에서는 접혀있다(textarea count=1이지만
        # is_visible()=False) — "댓글" 토글 버튼을 눌러야 펼쳐진다. 처음 fix 때는
        # 이걸 놓쳐서 "새 댓글 입력창을 찾을 수 없음"으로 계속 실패했음(디버그
        # 스크립트로 실제 DOM 조회해 확인). 먼저 토글을 눌러 펼친다.
        toggle_btn = page.locator('button:has-text("댓글")').first
        try:
            toggle_btn.wait_for(state="visible", timeout=5000)
            toggle_btn.click()
            page.wait_for_timeout(800)
        except PlaywrightTimeoutError:
            pass  # 토글 버튼이 없으면 이미 펼쳐진 상태일 수 있음 — 계속 진행

        # 게시물에 기존 댓글이 있으면 그 댓글의 답글/수정용 폼도 동일하게
        # textarea[name="cmtCntnt"]를 가져서(값이 기존 댓글 텍스트로 채워진 채)
        # 매칭 개수가 2개 이상이 되고 strict mode violation이 났었다(opus 자문
        # 반영). 새 댓글용 최상단 폼은 기존 댓글 목록 컨테이너(#cmtDiv) 바깥에
        # 있으므로 그 조건으로 스코프하고, 혹시 몰라 값이 비어있는지도 재확인해
        # 실수로 남의 댓글 수정 폼을 잡아 덮어쓰는 사고를 막는다.
        main_form = None
        all_forms = page.locator('form.cmtForm')
        try:
            all_forms.first.wait_for(state="attached", timeout=10000)
        except PlaywrightTimeoutError:
            result["message"] = "댓글 폼(form.cmtForm)을 찾을 수 없음 — 페이지 구조 변경 가능성."
            result["screenshot"] = common.save_screenshot(page, f"hmp_{account}_comment")
            return result

        for i in range(all_forms.count()):
            f = all_forms.nth(i)
            try:
                in_cmt_div = f.evaluate("el => !!el.closest('#cmtDiv')")
            except Exception:
                in_cmt_div = False
            if in_cmt_div:
                continue  # 기존 댓글의 답글/수정 폼 — 새 댓글 폼이 아님
            ta = f.locator('textarea[name="cmtCntnt"]')
            if ta.count() == 0 or not ta.first.is_visible():
                continue
            if (ta.first.input_value() or "").strip():
                continue  # 값이 이미 차 있으면 기존 댓글 수정 폼일 가능성 — 제외
            main_form = f
            break

        if main_form is None:
            result["message"] = "새 댓글 입력창을 찾을 수 없음 — 페이지 구조 변경 가능성."
            result["screenshot"] = common.save_screenshot(page, f"hmp_{account}_comment")
            return result

        cmt_textarea = main_form.locator('textarea[name="cmtCntnt"]').first
        cmt_textarea.fill("감사합니다")

        # 6. 다이얼로그 핸들러 등록 (confirm + 결과 alert 순서대로 수락)
        dialogs_seen: list[str] = []

        def on_dialog(dialog):
            dialogs_seen.append(dialog.message)
            dialog.accept()

        page.on("dialog", on_dialog)
        try:
            # 7. 등록하기 클릭 → saveCmt() → confirm → AJAX → alert
            # 텍스트를 채운 폼(main_form)과 동일한 폼의 버튼을 눌러야 한다 — 별도로
            # form.cmtForm 전체에서 다시 찾으면 다른 폼(기존 댓글 폼)의 버튼을 누를 위험이 있다.
            main_form.locator('button[onclick*="saveCmt"]').first.click()

            # 8. confirm(즉시) + AJAX + alert(수초 내) 대기 — 둘 다 잡히면 즉시 진행
            _wait_for_dialogs(page, dialogs_seen)
        finally:
            try:
                page.remove_listener("dialog", on_dialog)
            except Exception:
                pass

        # 9. 결과 판정
        if any("저장 완료" in d for d in dialogs_seen):
            common.goto_with_retry(
                page,
                f"{COMM_DETAIL_URL}?boardSeq={board_seq}",
                wait_until="domcontentloaded",
                timeout_ms=DEFAULT_TIMEOUT_MS,
            )
            page.wait_for_timeout(2000)
            if verify_comment_saved(page.content(), my_name):
                result["status"] = "success"
                result["verified_by"] = f"nickname_verified: {my_name}"
                result["message"] = f"댓글 '감사합니다' 작성 완료 (게시물 {board_seq})."
            else:
                result["status"] = "unverified"
                result["message"] = f"저장 완료 alert 수신했으나 상세 재조회에서 닉네임({my_name}) 미확인 (게시물 {board_seq})."
        elif any(kw in d for d in dialogs_seen for kw in ("오류", "에러", "실패")):
            err = next(d for d in dialogs_seen if any(kw in d for kw in ("오류", "에러", "실패")))
            result["message"] = f"서버 응답 오류: {err}"
            result["screenshot"] = common.save_screenshot(page, f"hmp_{account}_comment")
        elif dialogs_seen:
            # confirm은 떴으나 결과 alert 미수신 — AJAX 타임아웃 또는 응답 구조 변경
            result["message"] = f"결과 alert 미수신 (확인된 다이얼로그: {dialogs_seen})."
            result["screenshot"] = common.save_screenshot(page, f"hmp_{account}_comment")
        else:
            # 다이얼로그 자체가 안 뜸 — 입력값 검증 실패(빈 textarea 등) 또는 셀렉터 오류
            result["message"] = "등록하기 클릭 후 다이얼로그 없음 — 입력값 검증 실패 가능성."
            result["screenshot"] = common.save_screenshot(page, f"hmp_{account}_comment")

    except Exception as e:
        result["message"] = f"댓글 작성 예외: {e}"
        result["screenshot"] = common.save_screenshot(page, f"hmp_{account}_comment")

    return result


def kst_today_dot() -> str:
    """오늘(KST)을 HMP 목록 표기와 같은 YYYY.MM.DD 형식으로 반환."""
    return datetime.now(common.KST).strftime("%Y.%m.%d")


def parse_post_dates(cell_texts: list[str]) -> list[str]:
    """표 셀 텍스트 목록에서 YYYY.MM.DD 형태의 등록일자만 추려낸다."""
    dates = []
    for text in cell_texts:
        stripped = text.strip()
        if re.fullmatch(r"\d{4}\.\d{2}\.\d{2}", stripped):
            dates.append(stripped)
    return dates


def _already_posted_today(page, account: str) -> dict:
    """'나의 작성 글' 목록을 조회해 오늘 이미 글을 썼는지 판정한다.

    판정 근거 (2026-08-03 정찰, 실제 로그인 세션 DOM 조회):
      - URL: knowCommMyInfoPopup.hm?schGbn=BOARD (window.open 대상이지만 직접 GET 가능)
      - 표 헤더: 카테고리 / 협진과 / 제목 / 조회수 / 답변 / 좋아요 / 등록일자
      - 데이터 행 마지막 td가 등록일자, 형식 "2026.08.03", 최신순 정렬
      - 제목이 아니라 **날짜만** 본다. 오늘 이미 어떤 글이든 올렸으면 하루 1회
        적립은 이미 끝났으므로 자동 글쓰기를 다시 할 이유가 없다.

    목록을 못 읽으면 판정을 포기하고 글쓰기를 진행한다(fail-open). 중복 게시보다
    "오늘 글이 아예 안 올라감"이 더 손해이고, 증거 없이 already_done으로 보고하면
    안 되기 때문이다.
    """
    try:
        common.goto_with_retry(
            page, MY_POST_URL, wait_until="load", timeout_ms=DEFAULT_TIMEOUT_MS
        )
        page.wait_for_timeout(2000)
        cells = page.locator("table tr td:last-child").all_inner_texts()
    except Exception as e:
        return {"posted": False, "checked": False, "message": f"내 글 목록 조회 실패: {e}"}

    dates = parse_post_dates(cells)
    if not dates:
        return {"posted": False, "checked": False, "message": "등록일자 셀을 찾지 못함 — 셀렉터 변경 가능성."}

    today = kst_today_dot()
    return {
        "posted": today in dates,
        "checked": True,
        "today_count": dates.count(today),
        "message": "",
    }


def _run_post(page, account: str) -> dict:
    """지식커뮤니티에 매일 글 1개 작성.

    내용:
      - Topic: 여행/취미 (TOPIC_13)
      - 제목: 오늘도 화이팅
      - 본문: {요일}요일이네요. 다들 화이팅하세요.  (요일은 실행 시점 자동 결정)
      - 태그: 화이팅

    흐름:
      1. knowCommHome.hm → .btnWrite 클릭 → #writePopupDiv 팝업 대기
      2. #_topicNm 클릭(드롭다운 열기) → input[name="topicGbn"][value="TOPIC_13"] 클릭
      3. input#title 에 제목 입력
      4. iframe#innoditor_0 body에 본문 HTML 설정 +
         textarea#innoditorSource_0 값도 동기화 (ajaxForm 직렬화 대비)
      5. input#tag 에 '화이팅' 입력 → Enter
      6. .botSubmit button[onclick*="saveBoard"] 클릭
      7. confirm 다이얼로그 수락 → 성공 alert "게시글이 작성 완료 됐습니다." 확인

    셀렉터 확인 근거 (2026-07-15, Claude in Chrome MCP로 실제 로그인 세션에서 DOM 직접 조회):
      - 글쓰기 버튼: button.btnWrite (onclick: $KnowCommHome.knowBoardWriteViewRender(0))
      - 팝업: div#writePopupDiv (.__ nkPop popComm __nkRe)
      - 토픽 드롭다운: div.__nkMulSel — input#_topicNm 클릭 시 ul.flt 표시
        여행/취미 = input[name="topicGbn"][value="TOPIC_13"]
      - 제목: input#title (placeholder "Q. 제목을 입력하세요.")
      - 본문 에디터: iframe#innoditor_0 (contenteditable body) +
        textarea#innoditorSource_0 (폼 직렬화용 소스 textarea)
      - 태그: input#tag (placeholder "※태그 입력 시 #을 제외한 텍스트만 입력 ...")
      - 등록 버튼: .botSubmit button[onclick*="saveBoard"] — prgSt="F" 최종 등록
      - AJAX: POST /ajax/knowcomm/insertKnowCommBoard.hm
      - 성공(rtn_code==100): alert "게시글이 작성 완료 됐습니다."
      - 오류: alert "오류 발생 재 로그인후..." 또는 "해당 글 본인인증에 실패..."

    버그 수정 (2026-07-16): input[name="topicGbn"][value="TOPIC_13"] 직접 클릭이
    "element is not visible"로 타임아웃(실제 <input>이 커스텀 스타일링을 위해
    시각적으로 숨겨져 있고 pill/label만 보이는 패턴으로 추정). 보이는 라벨
    텍스트("여행/취미")를 우선 클릭하고, 못 찾으면 force 클릭 폴백 + 선택 여부를
    is_checked()로 검증한다.
    """
    DAYS_KO = ["월", "화", "수", "목", "금", "토", "일"]
    day = DAYS_KO[datetime.now(common.KST).weekday()]
    title = "오늘도 화이팅"
    content_html = f"<p>{day}요일이네요. 다들 화이팅하세요.</p>"
    content_text = f"{day}요일이네요. 다들 화이팅하세요."

    result: dict = {"status": "failed", "message": ""}

    try:
        # 1. 커뮤니티 홈으로 이동
        common.goto_with_retry(page, COMM_HOME_URL, wait_until="load", timeout_ms=DEFAULT_TIMEOUT_MS)
        page.wait_for_timeout(2000)

        # 2. 글쓰기 버튼 클릭 → 팝업 열기
        write_btn = page.locator('button.btnWrite, a.btnWrite')
        try:
            write_btn.wait_for(state="visible", timeout=10000)
        except PlaywrightTimeoutError:
            result["message"] = "글쓰기 버튼을 찾을 수 없음 — 셀렉터 변경 가능성."
            result["screenshot"] = common.save_screenshot(page, f"hmp_{account}_post")
            return result

        write_btn.click()

        popup = page.locator('#writePopupDiv')
        try:
            popup.wait_for(state="visible", timeout=10000)
        except PlaywrightTimeoutError:
            result["message"] = "글쓰기 팝업(#writePopupDiv)이 나타나지 않음."
            result["screenshot"] = common.save_screenshot(page, f"hmp_{account}_post")
            return result
        page.wait_for_timeout(1000)

        # 3. 토픽 드롭다운 열기 → 여행/취미 선택
        # 2026-07-16 버그: input[name="topicGbn"][value="TOPIC_13"] 직접 클릭이
        # "element is not visible"로 15초 타임아웃(스크린샷상 pill 자체는 멀쩡히
        # 보임 — opus 자문 결과 오버레이/애니메이션 문제가 아니라 커스텀 스타일링을
        # 위해 실제 <input>이 시각적으로 숨겨진 패턴으로 판단). 사용자가 실제로
        # 클릭하는 보이는 라벨/pill 텍스트를 우선 클릭하고, 못 찾으면 force 클릭으로
        # 폴백한다. 클릭 후 실제로 선택됐는지 checked 상태로 검증해 조용히 실패하는
        # 것을 막는다.
        page.locator('#writePopupDiv #_topicNm').click()
        page.wait_for_timeout(500)

        topic_input = page.locator('#writePopupDiv input[name="topicGbn"][value="TOPIC_13"]')
        topic_label = page.locator('#writePopupDiv label:has-text("여행/취미")')
        if topic_label.count() > 0 and topic_label.first.is_visible():
            topic_label.first.click()
        else:
            topic_input.click(force=True)
        page.wait_for_timeout(300)

        try:
            topic_checked = topic_input.is_checked()
        except Exception:
            topic_checked = None
        if topic_checked is False:
            # 라벨 클릭이 실제 input 선택으로 이어지지 않음 — force 클릭으로 재시도
            topic_input.click(force=True)
            page.wait_for_timeout(300)

        # 4. 제목 입력
        page.locator('#writePopupDiv #title').fill(title)

        # 5. 본문 입력 — iframe body에 HTML 직접 설정 + source textarea 동기화
        try:
            inner_frame = page.frame_locator('#writePopupDiv #innoditor_0')
            inner_frame.locator('body').evaluate(
                f"el => {{ el.innerHTML = {repr(content_html)}; "
                f"el.dispatchEvent(new Event('input', {{bubbles: true}})); }}"
            )
        except Exception:
            pass  # iframe 접근 실패 시 source textarea만으로 시도
        # source textarea도 동기화 (ajaxForm 직렬화 시 이 값이 사용됨)
        page.evaluate(
            f"document.querySelector('#innoditorSource_0').value = {repr(content_html)}"
        )

        # 6. 태그 입력 → Enter
        tag_input = page.locator('#writePopupDiv #tag')
        tag_input.fill("화이팅")
        tag_input.press("Enter")
        page.wait_for_timeout(500)

        # 7. 다이얼로그 핸들러 등록 (confirm + 결과 alert 순서대로 수락)
        dialogs_seen: list[str] = []

        def on_dialog(dialog):
            dialogs_seen.append(dialog.message)
            dialog.accept()

        page.on("dialog", on_dialog)
        try:
            # 8. 등록하기 클릭 → saveBoard() → confirm → AJAX → alert
            page.locator('#writePopupDiv .botSubmit button[onclick*="saveBoard"]').click()

            # 9. confirm(즉시) + AJAX + alert 대기 — 둘 다 잡히면 즉시 진행
            _wait_for_dialogs(page, dialogs_seen)
        finally:
            try:
                page.remove_listener("dialog", on_dialog)
            except Exception:
                pass

        # 10. 결과 판정
        if any("작성 완료" in d for d in dialogs_seen):
            result["status"] = "success"
            result["verified_by"] = "rtn_code_100"
            result["message"] = f"글 작성 완료: '{title}' ({day}요일)"
        elif any(kw in d for d in dialogs_seen for kw in ("오류", "에러", "실패", "인증")):
            err = next(d for d in dialogs_seen if any(kw in d for kw in ("오류", "에러", "실패", "인증")))
            result["message"] = f"서버 오류: {err}"
            result["screenshot"] = common.save_screenshot(page, f"hmp_{account}_post")
        elif dialogs_seen:
            result["message"] = f"결과 alert 미수신 (확인된 다이얼로그: {dialogs_seen})."
            result["screenshot"] = common.save_screenshot(page, f"hmp_{account}_post")
        else:
            result["message"] = "등록하기 클릭 후 다이얼로그 없음 — 입력값 검증 실패 또는 셀렉터 오류."
            result["screenshot"] = common.save_screenshot(page, f"hmp_{account}_post")

    except Exception as e:
        result["message"] = f"글쓰기 예외: {e}"
        result["screenshot"] = common.save_screenshot(page, f"hmp_{account}_post")

    return result


def run(account: str, credentials_path: Path, headless: bool) -> dict:
    creds = load_credentials(credentials_path, account)
    result = {"site": "hmp", "account": account, "status": "failed", "points": 0, "message": ""}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(locale="ko-KR")
        page = context.new_page()
        page.set_default_timeout(DEFAULT_TIMEOUT_MS)

        try:
            # "load" 사용 — HMP 캡슐 버튼은 JS가 domcontentloaded 이후에 렌더링하므로
            # domcontentloaded만으로는 버튼이 아직 DOM에 없을 수 있다.
            common.goto_with_retry(page, ATTENDANCE_URL, wait_until="load", timeout_ms=DEFAULT_TIMEOUT_MS)

            # HMP는 미로그인 시 /login/loginForm.hm 로 리다이렉트되지만, URL 매칭보다
            # 로그인 폼 가시성으로 판단하는 편이 견고하다(keymedi.py 교훈, common.form_login 참조).
            login_result = common.form_login(
                page,
                'input[name="memId"]',
                'input[name="passwd"]',
                'button.btn_login:has-text("로그인")',
                creds["id"],
                creds["password"],
                DEFAULT_TIMEOUT_MS,
            )
            if login_result is False:
                result["message"] = "로그인 실패 — 아이디/비밀번호 또는 셀렉터 확인 필요."
                result["screenshot"] = common.save_screenshot(page, f"hmp_{account}")
                return result
            if login_result is True:
                page.wait_for_load_state("domcontentloaded")
                # 로그인 후 attendance 페이지로 리다이렉트 안 되는 경우 대비
                if "attendanceRouletteMain" not in page.url:
                    common.goto_with_retry(page, ATTENDANCE_URL, wait_until="domcontentloaded", timeout_ms=DEFAULT_TIMEOUT_MS)

            # 캡슐 구간만 따로 감싼다. 이 블록에서 예외가 나면 예전에는 바깥
            # except로 빠져 룰렛·댓글·글쓰기가 통째로 건너뛰어졌다 — 셋 다 캡슐
            # 성공 여부와 무관한 작업이라 같이 죽을 이유가 없다.
            try:
                # 2026-07-07 확인: 페이지 리뉴얼로 #capsuleBtn / #capsuleBtnComplete ID 사라짐.
                # 새 버튼 텍스트: "오늘의 캡슐 받기". element 타입이 button인지 a인지 불명이므로
                # get_by_text()로 타입 무관하게 찾는다. 구버전 ID도 fallback으로 유지.
                # CSS 셀렉터에 text= 을 섞으면 파싱 오류 발생 — 반드시 locator를 분리해야 한다.

                # JS 렌더링 대기 — load 이후에도 SPA 요소가 늦게 붙는 경우 대비
                page.wait_for_timeout(2000)

                complete_btn = page.locator('#capsuleBtnComplete')
                active_by_id = page.locator('#capsuleBtn')
                active_by_text = page.get_by_text("오늘의 캡슐 받기", exact=True)

                # 셋 중 하나라도 DOM에 붙을 때까지 대기
                found = False
                for loc in [active_by_id, active_by_text, complete_btn]:
                    try:
                        loc.first.wait_for(state="attached", timeout=5000)
                        found = True
                        break
                    except PlaywrightTimeoutError:
                        continue

                if not found:
                    result["message"] = "캡슐 버튼을 찾을 수 없음 — 페이지 구조 변경 가능성."
                    result["screenshot"] = common.save_screenshot(page, f"hmp_{account}")
                    # 캡슐 버튼 이상이어도 로그인 세션은 살아있으므로 댓글 시도는 계속한다
                elif complete_btn.count() > 0 and complete_btn.first.is_visible():
                    result["status"] = "already_done"
                    result["verified_by"] = "#capsuleBtnComplete visible"
                    result["message"] = "오늘 이미 캡슐 출석 완료된 상태."
                else:
                    # ID 버튼이 보이면 우선 사용, 아니면 텍스트 버튼 사용
                    if active_by_id.count() > 0 and active_by_id.first.is_visible():
                        active_btn = active_by_id
                    elif active_by_text.count() > 0 and active_by_text.first.is_visible():
                        active_btn = active_by_text
                    else:
                        result["message"] = "오늘의 캡슐 받기 버튼이 보이지 않음 — 페이지 구조 변경 가능성."
                        result["screenshot"] = common.save_screenshot(page, f"hmp_{account}")
                        active_btn = None

                    if active_btn is not None:
                        active_btn.first.click()

                        # 완료 팝업 확인 — id="10rewardPopup" 은 숫자로 시작해 CSS 셀렉터로 쓸 수 없다.
                        # [id="..."] 속성 셀렉터로 우회한다.
                        try:
                            popup = page.locator('[id="10rewardPopup"]')
                            popup.wait_for(state="visible", timeout=DEFAULT_TIMEOUT_MS)
                            confirm_btn = popup.locator('button:has-text("확인")')
                            if confirm_btn.count() > 0:
                                confirm_btn.first.click()
                            result["status"] = "success"
                            result["verified_by"] = "popup: 10rewardPopup"
                            result["points"] = 10
                            result["message"] = "캡슐 출석 완료, 10캡슐 적립."
                        except PlaywrightTimeoutError:
                            result["message"] = "캡슐 버튼은 눌렀으나 완료 팝업을 확인하지 못함."
                            result["screenshot"] = common.save_screenshot(page, f"hmp_{account}")
            except Exception as e:
                result["message"] = f"캡슐 구간 예외: {e}"
                result["screenshot"] = common.save_screenshot(page, f"hmp_{account}")
                common.log_error("hmp", e, account=account, task="capsule",
                                 screenshot=result["screenshot"])

            # 룰렛 처리 (연속 10·20·30일 달성 시 버튼 활성화)
            if result["status"] in ("success", "already_done"):
                roulette_results = _run_roulette(page, account)
                if roulette_results:
                    result["roulette"] = roulette_results

            # 지식커뮤니티 댓글 작성 (캡슐 결과와 무관하게 항상 시도)
            result["comment"] = _run_comment(page, account)

            # 지식커뮤니티 글쓰기 (하루 1회)
            # 2026-08-03: 체크가 없어서 daily CI와 로컬 실행이 겹치면 같은 글이
            # 하루에 3건까지 올라갔다("나의 작성 글"에서 확인). 서버의 내 글 목록을
            # 근거로 오늘치가 이미 있으면 건너뛴다.
            posted = _already_posted_today(page, account)
            if posted["posted"]:
                result["post"] = {
                    "status": "already_done",
                    "message": f"오늘({kst_today_dot()}) 이미 글 {posted['today_count']}건 작성됨.",
                    "verified_by": "my_post_list_date_match",
                }
            else:
                result["post"] = _run_post(page, account)
                if not posted["checked"]:
                    # 판정에 실패한 채로 글을 썼다 = 중복 게시 위험이 조용히 돌아온 상태.
                    # post 자체는 성공했을 수 있으므로 post의 status를 왜곡하지 않고,
                    # 별도 노드로 unverified(alert)를 올려 알림·exit 1을 유도한다.
                    result["post_precheck"] = {
                        "status": "unverified",
                        "message": f"글쓰기 중복 판정 실패 — {posted['message']} 목록 셀렉터 확인 필요.",
                        "screenshot": common.save_screenshot(page, f"hmp_{account}_postcheck"),
                    }

        except Exception as e:
            result["message"] = f"예외 발생: {e}"
            result["screenshot"] = common.save_screenshot(page, f"hmp_{account}")
            common.log_error("hmp", e, account=account, screenshot=result["screenshot"])
        finally:
            browser.close()

    return result


def main():
    parser = argparse.ArgumentParser(description="HMP 일일 캡슐 출석체크 자동화")
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
    failed = notify.severity_of(result) == "alert" or result.get("status") not in ("success", "already_done")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
