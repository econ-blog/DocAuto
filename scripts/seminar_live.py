#!/usr/bin/env python3
"""
닥터빌 라이브 세미나 자동 입장 스크립트.

daily 루틴(daily_runner.py)과 무관한 **수동/자동 루틴** 스크립트다.
/seminar/main에서 현재 "입장하기"가 가능한(=신청 완료 + 방송 중) 라이브 세미나를
모두 찾아 각각 입장 → 팝업 창에서 --stay-seconds초 대기 → 팝업 닫기를 반복한다.

용법:
    python3 seminar_live.py                          # bjh7790+wonju 순회 (헤드리스), 각 20초
    python3 seminar_live.py --account bjh7790        # 단일 계정만
    python3 seminar_live.py --stay-seconds 30        # 체류 시간 변경
    python3 seminar_live.py --headed                 # 브라우저 창 표시 (디버깅용)
    python3 seminar_live.py --no-telegram            # 텔레그램 전송 생략
    python3 seminar_live.py --credentials PATH       # credentials.json 경로 직접 지정
    python3 seminar_live.py --state-file PATH        # 상태 저장 경로 지정
    python3 seminar_live.py --block {lunch,evening,manual,auto}
    python3 seminar_live.py --ignore-state           # 상태 무시 재입장
    python3 seminar_live.py --always-notify          # 변화가 없어도 텔레그램 전송
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
from common import KST as kst, parse_dd_date
import doctorville
import notify
import runlog

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_TIMEOUT_MS = doctorville.DEFAULT_TIMEOUT_MS
ENTER_BTN_WAIT_MS = 10000  # 상세 페이지에서 입장 버튼이 뜨는지 확인하는 대기 시간(짧게)


def save_screenshot(page, tag: str) -> str:
    return common.save_screenshot(page, f"seminar_live_{tag}")


def upgrade_to_v2(state: dict) -> dict:
    """Upgrades state dict from schema v1 to schema v2 in-place and returns it.

    In v2:
    - version is set to 2.
    - entered list items are upgraded from int N to {"id": N, "title": None, "start": None, "entered_at": None}.
    - survey_done list is replaced by survey dict {"N": "done"}.
    """
    if not isinstance(state, dict):
        return {"version": 2, "accounts": {}}
    if state.get("version") == 2:
        return state
    state["version"] = 2
    accounts = state.setdefault("accounts", {})
    if isinstance(accounts, dict):
        for acc, acc_data in accounts.items():
            if not isinstance(acc_data, dict):
                continue
            entered_raw = acc_data.get("entered", [])
            new_entered = []
            for item in entered_raw:
                if isinstance(item, int):
                    new_entered.append({"id": item, "title": None, "start": None, "entered_at": None})
                elif isinstance(item, str) and item.isdigit():
                    new_entered.append({"id": int(item), "title": None, "start": None, "entered_at": None})
                elif isinstance(item, dict):
                    entry = {
                        "id": item.get("id"),
                        "title": item.get("title"),
                        "start": item.get("start"),
                        "entered_at": item.get("entered_at"),
                    }
                    new_entered.append(entry)
                else:
                    new_entered.append(item)
            acc_data["entered"] = new_entered

            survey_done = acc_data.pop("survey_done", [])
            survey_dict = acc_data.setdefault("survey", {})
            if isinstance(survey_done, list):
                for sid in survey_done:
                    survey_dict[str(sid)] = "done"
    return state


def _default_account_state() -> dict:
    return {"entered": [], "blocks": {"lunch": [], "evening": [], "manual": []}, "survey": {}}


def merge_state(state: dict, today_str: str, accounts: list[str] = None) -> dict:
    if accounts is None:
        accounts = ["bjh7790", "wonju"]
    if not isinstance(state, dict):
        state = {}
    state = upgrade_to_v2(state)
    if state.get("date") != today_str:
        return {
            "version": 2,
            "date": today_str,
            "accounts": {acc: _default_account_state() for acc in accounts},
        }
    state["version"] = 2
    acc_map = state.setdefault("accounts", {})
    for acc in accounts:
        acc_data = acc_map.setdefault(acc, _default_account_state())
        acc_data.setdefault("entered", [])
        acc_data.setdefault("blocks", {"lunch": [], "evening": [], "manual": []})
        acc_data.setdefault("survey", {})
    return state


def load_state(path: Path | str, today_str: str = None) -> dict:
    if today_str is None:
        today_str = datetime.now(common.KST).strftime("%Y-%m-%d")
    data = common.read_json(path, default={})
    if isinstance(data, dict) and data:
        data = upgrade_to_v2(data)
    return merge_state(data, today_str)


def save_state(state: dict, path: Path | str) -> None:
    common.write_json_atomic(path, state)


def update_entered_state(
    state: dict,
    account: str,
    seminar_id: int | str,
    block_name: str,
    path: Path | str = None,
    title: str = None,
    start: str = None,
    entered_at: str = None,
) -> None:
    state = upgrade_to_v2(state)
    sid = int(seminar_id)
    acc_map = state.setdefault("accounts", {})
    acc_data = acc_map.setdefault(
        account, {"entered": [], "blocks": {"lunch": [], "evening": [], "manual": []}, "survey": {}}
    )
    entered_list = acc_data.setdefault("entered", [])
    found = False
    for item in entered_list:
        if isinstance(item, dict) and item.get("id") == sid:
            found = True
            if title is not None:
                item["title"] = title
            if start is not None:
                item["start"] = start
            if entered_at is not None:
                item["entered_at"] = entered_at
            break
    if not found:
        entered_list.append({
            "id": sid,
            "title": title,
            "start": start,
            "entered_at": entered_at,
        })
    blocks_map = acc_data.setdefault("blocks", {})
    block_list = blocks_map.setdefault(block_name, [])
    if sid not in block_list:
        block_list.append(sid)
    if path is not None:
        save_state(state, path)


def determine_block_name(block_arg: str) -> str:
    if block_arg != "auto":
        return block_arg
    hour = datetime.now(common.KST).hour
    return "lunch" if hour < 16 else "evening"



# ---------------------------------------------------------------------------
# 라이브 세미나 목록 추출
# ---------------------------------------------------------------------------

def get_live_seminar_info(page) -> list[dict]:
    """/seminar/main에서 현재 '입장하기'가 뜬 세미나의 [{"id": "5473", "title": "..."}, ...] 목록을 순서·중복없이 반환."""
    common.goto_with_retry(page, doctorville.SEMINAR_MAIN_URL, wait_until="domcontentloaded", timeout_ms=DEFAULT_TIMEOUT_MS)
    page.wait_for_timeout(1500)  # SPA 렌더링 대기 (task_seminar와 동일 패턴)

    items = page.evaluate("""
        () => Array.from(document.querySelectorAll('span.ico_enter')).map(span => {
            const aEl = span.closest('a.list_detail');
            if (!aEl) return null;
            let sid = null;
            try { sid = new URL(aEl.href).searchParams.get('seminarId'); } catch(e) { return null; }
            if (!sid) return null;

            const titEl = aEl.querySelector('.tit, dt, .title, strong');
            let title = titEl ? titEl.innerText.trim() : '';
            if (!title) {
                const lines = (aEl.innerText || '').split('\\n').map(l => l.trim()).filter(Boolean);
                const filtered = lines.filter(l =>
                    !/^(입장|신청|방송중|마감|신청완료|사전신청)/.test(l) &&
                    !/\\d{2}:\\d{2}/.test(l) &&
                    !/^(연자|정원):/.test(l)
                );
                title = filtered.length > 0 ? filtered[0] : '';
            }
            return { id: sid, title: title };
        }).filter(Boolean)
    """)

    seen = set()
    deduped = []
    for item in items:
        sid = item["id"]
        if sid not in seen:
            seen.add(sid)
            deduped.append(item)
    return deduped


def get_live_seminar_ids(page) -> list[str]:
    """/seminar/main에서 현재 '입장하기'가 뜬 세미나의 seminarId 목록을 순서·중복없이 반환."""
    return [item["id"] for item in get_live_seminar_info(page)]


# ---------------------------------------------------------------------------
# 세미나 상세 입장 & 스트리밍 창 팝업 대기
# ---------------------------------------------------------------------------

def is_enter_window(start_str_or_item, now_dt: datetime = None) -> tuple[bool, str, datetime | None, datetime | None]:
    """라이브 입장 가능 시간대인지 확인: [시작 1시간 전 ~ 끝나는 시간].

    Returns: (can_enter, reason, start_dt, end_dt)
    """
    if now_dt is None:
        now_dt = datetime.now(common.KST)
    elif now_dt.tzinfo is None:
        now_dt = now_dt.replace(tzinfo=common.KST)

    start_str = start_str_or_item.get("start") if isinstance(start_str_or_item, dict) else start_str_or_item
    if not start_str or not isinstance(start_str, str):
        return True, "시간 미확인", None, None

    s_dt, e_dt = parse_dd_date(start_str)
    if s_dt is None:
        return True, "시간 파싱 불가", None, None

    earliest = s_dt - timedelta(hours=1)
    latest = e_dt or (s_dt + timedelta(hours=1))

    if now_dt < earliest:
        return False, f"입장 가능 시간 전 (시작 1시간 전({earliest.strftime('%H:%M')})부터 가능, 현재 {now_dt.strftime('%H:%M')})", s_dt, latest
    if now_dt > latest:
        return False, f"세미나 종료({latest.strftime('%H:%M')})로 입장 불가 (현재 {now_dt.strftime('%H:%M')})", s_dt, latest

    return True, "입장 가능 시간", s_dt, latest


def enter_and_wait(page, seminar_id: str, stay_seconds: int, now_dt: datetime = None) -> dict:
    """단일 seminarId 상세 페이지로 이동해 '입장하기' 클릭 → 팝업창에서 stay_seconds초 대기 후 닫기."""
    detail_url = f"{doctorville.SEMINAR_DETAIL_URL}?seminarId={seminar_id}"
    common.goto_with_retry(page, detail_url, wait_until="domcontentloaded", timeout_ms=DEFAULT_TIMEOUT_MS)
    page.wait_for_timeout(1000)

    date_text = page.evaluate("""
        () => {
            const dd = document.querySelector('dd.date');
            return dd ? dd.innerText.trim() : '';
        }
    """)
    start_dt, end_dt = parse_dd_date(date_text)
    start_str = date_text if start_dt is not None else None
    entered_at_str = datetime.now(common.KST).isoformat()

    can_enter, reason, _, _ = is_enter_window(date_text, now_dt)
    if not can_enter:
        return {
            "status": "skipped",
            "message": f"세미나 {seminar_id}: {reason}",
            "start": start_str,
            "entered_at": entered_at_str,
        }

    btn = page.locator("a.btn_bn").first
    if btn.count() == 0 or not btn.is_visible():
        return {
            "status": "skipped",
            "message": f"세미나 {seminar_id}: 입장버튼(a.btn_bn) 없음",
            "start": start_str,
            "entered_at": entered_at_str,
        }

    btn_text = btn.inner_text().strip()
    if "입장" not in btn_text:
        return {
            "status": "skipped",
            "message": f"세미나 {seminar_id}: 버튼 텍스트 '{btn_text}' (입장불가)",
            "start": start_str,
            "entered_at": entered_at_str,
        }

    # popup 이벤트 수신 준비 후 클릭
    try:
        with page.expect_popup(timeout=DEFAULT_TIMEOUT_MS) as popup_info:
            btn.click()
        popup = popup_info.value
    except PlaywrightTimeoutError:
        return {
            "status": "failed",
            "message": f"세미나 {seminar_id}: 팝업창(expect_popup) 열림 타임아웃",
            "screenshot": save_screenshot(page, f"popup_fail_{seminar_id}"),
            "start": start_str,
            "entered_at": entered_at_str,
        }

    try:
        try:
            popup.wait_for_load_state("domcontentloaded", timeout=DEFAULT_TIMEOUT_MS)
        except PlaywrightTimeoutError:
            pass

        popup.wait_for_timeout(stay_seconds * 1000)
    finally:
        try:
            popup.close()
        except Exception:
            pass

    return {
        "status": "success",
        "verified_by": "popup_acquired",
        "message": f"세미나 {seminar_id}: {stay_seconds}초 체류 완료",
        "start": start_str,
        "entered_at": entered_at_str,
    }


# ---------------------------------------------------------------------------
# 계정별 세미나 종합 수행
# ---------------------------------------------------------------------------

def _state_start(state: dict, account: str, seminar_id: int) -> str:
    """상태 파일에 남은 세미나 시작 일시 원문을 찾는다(표의 시간 칸 채우기용)."""
    if not state or not account:
        return ""
    for item in state.get("accounts", {}).get(account, {}).get("entered", []):
        if isinstance(item, dict) and item.get("id") == seminar_id:
            return item.get("start") or ""
    return ""


def _log_seminar(seminar_id, status: str, account: str, title: str = "", start: str = "") -> None:
    """세미나 표의 '입장' 칸을 채운다. 로깅 실패가 입장 자체를 죽이면 안 된다."""
    try:
        runlog.update_seminar(
            seminar_id, phase="live", status=status, account=account or "_",
            title=title or "", start=start or "",
        )
    except Exception as e:
        print(f"[seminar_live] 세미나 로그 기록 실패({seminar_id}): {e}", file=sys.stderr)


def task_live_seminar(
    page,
    stay_seconds: int,
    account: str = "",
    state: dict = None,
    block_name: str = "manual",
    state_file: Path | str = None,
    ignore_state: bool = False,
    dry_run: bool = False,
) -> dict:
    result = {
        "status": "failed",
        "entered": [],
        "already_entered": [],
        "skipped": [],
        "failed": [],
        "count": 0,
    }

    items = get_live_seminar_info(page)
    if not items:
        result["status"] = "no_target"
        result["message"] = "입장 가능한 라이브 세미나 없음."
        return result

    already_entered_set = set()
    if state and account and not ignore_state:
        acc_data = state.get("accounts", {}).get(account, {})
        already_entered_set = {
            item["id"] if isinstance(item, dict) else int(item)
            for item in acc_data.get("entered", [])
            if (isinstance(item, dict) and item.get("id") is not None) or isinstance(item, (int, str))
        }

    entered: list[int] = []
    already_entered: list[int] = []
    skipped: list[int] = []
    failed_list: list[dict] = []

    for item in items:
        sid = item["id"]
        title = item.get("title") or None
        sid_int = int(sid)
        if sid_int in already_entered_set:
            already_entered.append(sid_int)
            _log_seminar(sid, "already_done", account, title, _state_start(state, account, sid_int))
            continue

        if dry_run:
            skipped.append(sid_int)
            print(f"[dry-run] 계정 '{account}' 세미나 {sid_int} 입장 스킵 (dry-run 모드)")
            continue

        r = enter_and_wait(page, sid, stay_seconds)
        _log_seminar(sid, r["status"], account, title, r.get("start"))
        if r["status"] == "success":
            entered.append(sid_int)
            start_val = r.get("start")
            entered_at_val = r.get("entered_at") or datetime.now(common.KST).isoformat()
            if state and account and state_file:
                update_entered_state(
                    state,
                    account,
                    sid_int,
                    block_name,
                    state_file,
                    title=title,
                    start=start_val,
                    entered_at=entered_at_val,
                )
        elif r["status"] == "skipped":
            skipped.append(sid_int)
        else:
            failed_list.append({"seminarId": sid_int, "message": r.get("message", "")})

    result["entered"] = entered
    result["already_entered"] = already_entered
    result["skipped"] = skipped
    result["failed"] = failed_list
    result["count"] = len(entered)

    if failed_list:
        result["status"] = "failed"
        result["message"] = (
            f"입장 {len(entered)}건, 이미입장 {len(already_entered)}건, 스킵 {len(skipped)}건, 실패 {len(failed_list)}건."
        )
    elif entered:
        # 이번 런에서 실제로 팝업을 잡은 경우에만 양성 증거를 붙인다.
        result["status"] = "success"
        result["verified_by"] = "popup_acquired"
        result["message"] = (
            f"입장 {len(entered)}건 완료, 이미입장 {len(already_entered)}건, 스킵 {len(skipped)}건."
        )
    elif already_entered:
        result["status"] = "already_done"
        # 서버가 아니라 로컬 상태(seminar_entered.json)에 근거한 판정이다.
        result["verified_by"] = "cache: state.entered"
        result["message"] = f"이미입장 {len(already_entered)}건, 스킵 {len(skipped)}건. 신규 입장 없음."
    else:
        result["status"] = "skipped"
        result["message"] = f"스킵 {len(skipped)}건. 입장 가능한 세미나 없음."

    return result


def run_account(
    account: str,
    credentials_path: Path,
    headless: bool,
    stay_seconds: int,
    state: dict = None,
    block_name: str = "manual",
    state_file: Path | str = None,
    ignore_state: bool = False,
    dry_run: bool = False,
) -> dict:
    output = {
        "site": "doctorville_live_seminar",
        "account": account,
        "live_seminar": {"status": "failed"},
    }

    try:
        creds = doctorville.load_credentials(credentials_path, account)
    except KeyError as e:
        output["live_seminar"] = {"status": "failed", "message": str(e)}
        return output

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(locale="ko-KR", ignore_https_errors=True)
        page = context.new_page()
        page.set_default_timeout(DEFAULT_TIMEOUT_MS)

        try:
            common.goto_with_retry(page, doctorville.ATTEND_URL, wait_until="load", timeout_ms=DEFAULT_TIMEOUT_MS)
            if not doctorville.ensure_logged_in(page, creds):
                output["live_seminar"] = {"status": "failed", "message": "로그인 실패"}
                return output

            output["live_seminar"] = task_live_seminar(
                page,
                stay_seconds,
                account=account,
                state=state,
                block_name=block_name,
                state_file=state_file,
                ignore_state=ignore_state,
                dry_run=dry_run,
            )

        except Exception as e:
            output["error"] = f"예외 발생: {e}"
            shot = save_screenshot(page, "error")
            common.log_error("seminar_live", e, account=account, screenshot=shot)
        finally:
            browser.close()

    return output


# ---------------------------------------------------------------------------
# 텔레그램 요약 (notify의 emoji/축약 헬퍼 재사용)
# ---------------------------------------------------------------------------

ACCOUNT_LABELS = {"bjh7790": "승진(bjh7790)", "wonju": "원주(wonju)"}
BLOCK_LABELS = {"lunch": "점심", "evening": "저녁", "manual": "수동"}


def format_telegram_message(
    results: dict, date_str: str, stay_seconds: int, block_name: str = ""
) -> str:
    header = f"🎥 *라이브 세미나 입장 결과* ({date_str})"
    if block_name:
        b_label = BLOCK_LABELS.get(block_name, block_name)
        header += f" [{b_label}]"
    lines = [header, ""]

    for acc, r in results.items():
        label = ACCOUNT_LABELS.get(acc, acc)
        ls = r.get("live_seminar", {})
        e = notify.format_status_emoji(ls.get("status", "failed"))
        entered = ls.get("entered", [])
        already_entered = ls.get("already_entered", [])
        skipped = ls.get("skipped", [])
        failed = ls.get("failed", [])

        lines.append(f"{label} {e}")
        lines.append(
            f"  입장 {len(entered)}건(각 {stay_seconds}초) / 이미입장 {len(already_entered)}건 / 스킵 {len(skipped)}건 / 실패 {len(failed)}건"
        )
        if entered:
            lines.append(f"  └ 신규 입장 seminarId: {entered}")
        if already_entered:
            lines.append(f"  └ 이미 입장 seminarId: {already_entered}")
        for f in failed[:3]:
            lines.append(f"  └ 실패 {f['seminarId']}: {notify.shorten(f.get('message', ''))}")
        if r.get("error"):
            lines.append(f"  └ 스크립트 예외: {notify.shorten(r['error'])}")
        lines.append("")

    return "\n".join(lines).rstrip()


# ---------------------------------------------------------------------------
# 메인
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="닥터빌 라이브 세미나 자동 입장")
    parser.add_argument(
        "--account", default="all", choices=["all", "bjh7790", "wonju"],
        help="처리할 계정 (기본: all = bjh7790+wonju 순회)"
    )
    parser.add_argument(
        "--stay-seconds", type=int, default=20,
        help="세미나 팝업 창에 머무는 시간(초, 기본 20)"
    )
    parser.add_argument(
        "--credentials",
        default=str(SCRIPT_DIR.parent / "credentials.json"),
        help="credentials.json 경로 (기본: 스크립트 상위 폴더)"
    )
    parser.add_argument(
        "--state-file",
        default=str(SCRIPT_DIR / "state" / "seminar_entered.json"),
        help="상태 저장 JSON 파일 경로"
    )
    parser.add_argument(
        "--block", default="auto", choices=["lunch", "evening", "manual", "auto"],
        help="실행 블록 지정 (기본: auto = KST 시각 유도)"
    )
    parser.add_argument(
        "--ignore-state", action="store_true",
        help="상태 무시하고 전체 세미나 재입장"
    )
    parser.add_argument(
        "--always-notify", action="store_true",
        help="변화가 없어도 텔레그램 전송"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="라이브 세미나 목록만 확인하고 입장은 클릭하지 않음"
    )
    parser.add_argument(
        "--headed", action="store_true",
        help="브라우저 창을 띄워서 실행 (기본: headless)"
    )
    parser.add_argument(
        "--no-telegram", action="store_true",
        help="텔레그램 전송 건너뜀"
    )
    args = parser.parse_args()

    credentials_path = Path(args.credentials)
    state_file = Path(args.state_file)

    creds = common.read_credentials(credentials_path) if credentials_path.exists() else {}
    if args.account == "all":
        accounts = common.list_accounts(creds, "doctorville")
        if not accounts:
            accounts = ["bjh7790", "wonju"]
    else:
        accounts = [args.account]

    today_str = datetime.now(common.KST).strftime("%Y-%m-%d")
    state = load_state(state_file, today_str)
    block_name = determine_block_name(args.block)

    results = {}
    for i, acc in enumerate(accounts, start=1):
        print(f"[{i}/{len(accounts)}] {acc} 라이브 세미나 입장 시작 (블록: {block_name})...")
        results[acc] = run_account(
            acc,
            credentials_path,
            headless=not args.headed,
            stay_seconds=args.stay_seconds,
            state=state,
            block_name=block_name,
            state_file=state_file,
            ignore_state=args.ignore_state,
            dry_run=args.dry_run,
        )
        print(json.dumps(results[acc], ensure_ascii=False, indent=2))

    print("\n=== 최종 결과 ===")
    print(json.dumps(results, ensure_ascii=False, indent=2))

    failed = any(
        r.get("live_seminar", {}).get("status") in {"failed", "unverified", "blocked"} or r.get("error")
        for r in results.values()
    )

    if not args.no_telegram:
        notify_level = get_notify_level(args.always_notify)
        if notify.should_send(results, notify_level):
            date_str = datetime.now(kst).strftime("%Y-%m-%d %H:%M")
            msg = format_telegram_message(results, date_str, args.stay_seconds, block_name=block_name)
            print("\n[telegram] 전송 중...")
            ok = notify.send_telegram(msg, credentials_path=str(credentials_path))
            print(f"[telegram] {'성공' if ok else '실패'}")
        else:
            print("\n[telegram] 전송 조건 미충족. 건너뜀.")
    else:
        print("\n[telegram] 건너뜀 (--no-telegram)")

    sys.exit(1 if failed else 0)


def get_notify_level(always_notify: bool = False) -> str:
    if always_notify:
        return "all"
    return notify.resolve_level(os.environ.get("NOTIFY_LEVEL"))



if __name__ == "__main__":
    main()
