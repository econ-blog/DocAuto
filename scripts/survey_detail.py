#!/usr/bin/env python3
"""세미나 상세 페이지 및 설문 완료 마커 확인(Survey Detail & Markers) 모듈.

모바일(m.doctorville.co.kr/cme/vod) 및 데스크톱 상세 페이지의 버튼 상태를 조회하여
설문 완료(done), 미완료(not_done), 진행 중(running), 종료(ended)를 판정한다.
"""

import re
import sys
from pathlib import Path

import common
import doctorville
from survey_bank import normalize, strip_spaces

SURVEY_DONE_MARKER = "설문 참여 완료"
SEMINAR_END_MARKER = "세미나 종료"

SURVEY_DONE_MARKERS = (
    SURVEY_DONE_MARKER,   # m(모바일)
    "응답완료",            # www(데스크톱) — 실측
    "설문 응답 완료",
    "설문 완료",
)
SURVEY_PENDING_MARKERS = (
    SEMINAR_END_MARKER,
    "설문하기",
)
SEMINAR_RUNNING_MARKERS = ("입장하기", "방송중", "라이브")

MOBILE_BASE = "https://m.doctorville.co.kr"
MOBILE_DETAIL_URL = f"{MOBILE_BASE}/cme/vod"
MOBILE_FALLBACK_MARKERS = ("닥터빌로 이동하기",)
MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1"
)
MOBILE_LOGIN_MARKERS = ("로그아웃", "마이페이지")

DEFAULT_TIMEOUT_MS = doctorville.DEFAULT_TIMEOUT_MS
DETAIL_SETTLE_MS = 2000
MOBILE_RENDER_TIMEOUT_MS = 8000
MOBILE_POLL_MS = 500
DETAIL_RECHECK_WAIT_MS = 3000

LAST_DETAIL_PROBE: dict = {}

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


def detect_survey_marker(texts, allow_done: bool = True) -> str:
    """세미나 상세에서 읽은 문자열들로 설문 참여 여부를 판정한다.

    - ``done``     — '설문 참여 완료'가 있다. 설문을 실제로 마쳤다는 사이트의 표시.
    - ``not_done`` — '세미나 종료'만 있다. 입장을 못 했거나 제한 시간 내에
                     답을 못 낸 경우로, 설문에 참여하지 못한 상태다(실측 화면).
    - ``unknown``  — 둘 다 없다. 방송 전·중이거나 마크업이 바뀐 것.
    """
    if allow_done and matched_done_marker(texts):
        return "done"
    joined = " ".join(strip_spaces(t) for t in (texts or []) if t)
    if any(strip_spaces(m) in joined for m in SURVEY_PENDING_MARKERS):
        return "not_done"
    return "unknown"


def matched_done_marker(texts) -> str:
    """완료 표시 중 실제로 걸린 문구. 없으면 빈 문자열."""
    joined = " ".join(strip_spaces(t) for t in (texts or []) if t)
    for marker in SURVEY_DONE_MARKERS:
        if strip_spaces(marker) in joined:
            return marker
    return ""


def body_text(page) -> str:
    try:
        if page.is_closed():
            return ""
        return page.evaluate("() => document.body ? document.body.innerText : ''") or ""
    except Exception:
        return ""


def detail_url_matches(url: str, seminar_id) -> bool:
    """상세 조회가 그 세미나 페이지에 실제로 도달했는지 URL로 가른다."""
    url = str(url or "")
    if not url.lower().startswith("http"):
        return True
    sid = str(seminar_id)
    m = re.search(r"seminarId=(\d+)", url)
    if m:
        return m.group(1) == sid
    m = re.search(r"/(?:cme/)?vod/(\d+)", url)
    if m:
        return m.group(1) == sid
    return False


def read_detail_buttons(page) -> tuple[list[str], list[str]]:
    """세미나 상세의 버튼 텍스트를 (보이는 것, 숨은 것)으로 갈라 돌려준다."""
    seen, visible, hidden = set(), [], []
    try:
        for entry in page.evaluate(DETAIL_BUTTON_JS) or []:
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


_ORIGINAL_READ_DETAIL_BUTTONS = read_detail_buttons
_ORIGINAL_BODY_TEXT = body_text


def _current_read_detail_buttons(page):
    mod = sys.modules.get("seminar_survey")
    fn = getattr(mod, "read_detail_buttons", None)
    if fn is not None and fn is not read_detail_buttons and fn is not _ORIGINAL_READ_DETAIL_BUTTONS:
        return fn(page)
    return read_detail_buttons(page)


def _current_body_text(page):
    mod = sys.modules.get("seminar_survey")
    fn = getattr(mod, "body_text", None)
    if fn is not None and fn is not body_text and fn is not _ORIGINAL_BODY_TEXT:
        return fn(page)
    return body_text(page)


def seminar_ended(texts) -> bool:
    """상세에 '세미나 종료'가 떠 있는가 — 방송이 끝났다는 사이트의 표시."""
    joined = " ".join(strip_spaces(t) for t in (texts or []) if t)
    return strip_spaces(SEMINAR_END_MARKER) in joined


def seminar_running(texts) -> bool:
    """상세에 방송 전·중 표식이 떠 있는가. '세미나 종료'가 같이 있으면 아니다."""
    if seminar_ended(texts):
        return False
    joined = " ".join(strip_spaces(t) for t in (texts or []) if t)
    return any(strip_spaces(m) in joined for m in SEMINAR_RUNNING_MARKERS)


def usable_probes() -> list:
    """판정 근거로 써도 되는 상세 조회 기록만."""
    return [
        rec for rec in LAST_DETAIL_PROBE.values()
        if isinstance(rec, dict) and rec.get("usable")
    ]


def probe_saw_running_seminar() -> bool:
    """마지막 상세 조회에서 '아직 안 끝났다'를 관측했는가."""
    probes = usable_probes()
    if any(rec.get("ended") for rec in probes):
        return False
    return any(rec.get("running") for rec in probes)


def probe_saw_ended_seminar() -> bool:
    """마지막 상세 조회에서 '세미나 종료'를 봤는가 — 실제 종료의 관측이다."""
    return any(rec.get("ended") for rec in usable_probes())


def has_login_evidence(texts) -> bool:
    joined = " ".join(strip_spaces(t) for t in (texts or []) if t)
    return any(strip_spaces(m) in joined for m in MOBILE_LOGIN_MARKERS)


def is_mobile_session(page, texts) -> bool:
    """모바일 상세가 실제로 열렸는지. www로 튕겼거나 안내 페이지면 판정 불가다."""
    try:
        url = page.url or ""
    except Exception:
        url = ""
    if url and not url.startswith(MOBILE_BASE):
        return False
    joined = " ".join(strip_spaces(t) for t in (texts or []) if t)
    return not any(strip_spaces(m) in joined for m in MOBILE_FALLBACK_MARKERS)


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

    visible, hidden = _current_read_detail_buttons(page)
    body = _current_body_text(page)
    if mobile:
        waited = 0
        while (
            waited < MOBILE_RENDER_TIMEOUT_MS
            and detect_survey_marker((visible or hidden) + [body]) == "unknown"
        ):
            page.wait_for_timeout(MOBILE_POLL_MS)
            waited += MOBILE_POLL_MS
            visible, hidden = _current_read_detail_buttons(page)
            body = _current_body_text(page)

    buttons = visible or hidden

    try:
        final_url = str(page.url or "")
    except Exception:
        final_url = ""
    read_texts = visible or hidden
    rec = {
        "url": final_url,
        "visible": visible,
        "hidden": hidden,
        "ended": bool(read_texts) and seminar_ended(read_texts),
        "running": bool(read_texts) and seminar_running(read_texts),
        "usable": False,
    }
    LAST_DETAIL_PROBE["m" if mobile else "www"] = rec

    if not detail_url_matches(final_url, seminar_id):
        host = "m" if mobile else "www"
        return "unknown", [], f"{host} 상세: 세미나 {seminar_id} 페이지가 아님({final_url})"

    verdict = detect_survey_marker(buttons)
    if verdict == "unknown":
        verdict = detect_survey_marker([body], allow_done=False)

    if mobile:
        if not is_mobile_session(page, buttons + [body]):
            return "unknown", [], "m 상세: www로 리다이렉트됐거나 안내 페이지"
        if verdict == "not_done" and not has_login_evidence(buttons + [body]):
            return "unknown", buttons, "m 상세: 로그인 증거 없이 미참여로 보임 — 판정 보류"
    rec["usable"] = True
    return verdict, buttons, ""


def confirm_survey_done(page, seminar_id, retries: int = 0) -> tuple[str, list[str]]:
    """세미나 상세에 재접속해 완료 표시로 설문 완료 여부를 판정한다."""
    errors: list[str] = []
    buttons: list[str] = []
    verdict = "unknown"
    LAST_DETAIL_PROBE.clear()
    for attempt in range(retries + 1):
        if attempt:
            page.wait_for_timeout(DETAIL_RECHECK_WAIT_MS)

        verdict, buttons, err = read_detail_verdict(page, seminar_id, mobile=True)
        if err:
            errors.append(err)
        if verdict == "done":
            return verdict, buttons

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
