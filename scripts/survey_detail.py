#!/usr/bin/env python3
"""세미나 상세 페이지 및 설문 완료 마커 확인(Survey Detail & Markers) 모듈.

모바일(m.doctorville.co.kr/cme/vod) 및 데스크톱 상세 페이지의 버튼 상태를 조회하여
설문 완료(done), 미완료(not_done), 진행 중(running), 종료(ended)를 판정한다.
"""

import re
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
# 진행 중 표식으로 읽으면 안 되는 문구. '재입장하기'(다시보기 입장)에는 '입장하기'가
# 통째로 들어 있어 끝난 세미나에도 걸린다.
SEMINAR_NOT_RUNNING_MARKERS = ("재입장하기",)
# 진행 중 표식은 **버튼 라벨**에서만 읽는다. www 상세는 홍보 문구까지 버튼으로
# 실어 오는데('닥터빌 라이브세미나를 통해 선생님의 노하우를 공유해보세요!'),
# 거기 들어 있는 '라이브'가 걸려 www 조회는 사실상 언제나 "진행 중"이었다.
# 2026-09-17 세미나 5666(19:37 실측, run 35210903336): m 상세가 '세미나 종료'를
# 띄우고 있는데도 www의 '라이브'·'재입장하기'가 진행 중 근거가 되어, 설문 창을
# 못 연 계정이 unverified(alert)가 아니라 quiet not_ready로 묻혔다.
RUNNING_LABEL_MAX_LEN = 12

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

    두 문구는 상호 배타가 아니다 — 참여 완료 화면에는 '설문 참여 완료'와
    '세미나 종료'가 나란히 뜬다. 그래서 완료 표시를 먼저 본다.

    `allow_done=False`는 완료 판정을 쓰면 안 되는 근거(페이지 본문 전체 등)에
    쓴다. 본문에는 다른 세미나의 '응답완료'나 안내 문구가 섞여 들어오는데,
    거짓 `done`은 상태 파일에 done으로 굳어 다시 시도조차 안 되므로 거짓
    `not_done`(재시도로 회복된다)보다 훨씬 비싸다.
    """
    if allow_done and matched_done_marker(texts):
        return "done"
    joined = " ".join(strip_spaces(t) for t in (texts or []) if t)
    if any(strip_spaces(m) in joined for m in SURVEY_PENDING_MARKERS):
        return "not_done"
    return "unknown"


def matched_done_marker(texts) -> str:
    """완료 표시 중 실제로 걸린 문구. 없으면 빈 문자열.

    `verified_by`에 무엇을 보고 성공으로 판정했는지 그대로 싣기 위해 따로 둔다 —
    도메인마다 문구가 달라서 '설문 참여 완료'로 뭉뚱그리면 증거가 사실과 어긋난다.
    """
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
    """상세 조회가 **그 세미나 페이지**에 실제로 도달했는지 URL로 가른다.

    2026-09-11 실측: 아직 끝나지 않은 세미나(5696 카보메틱스, 19:00~22:10)를
    21:05에 조회했는데 `already_done`이 나왔다. 같은 런에서 다른 계정은
    `unverified`였다 — 같은 페이지라면 나올 수 없는 차이다. m 상세
    (`/cme/vod/{id}`)가 아직 없는 회차라 목록으로 떨어지고, 그 목록에 들어 있던
    **다른 세미나의 '응답완료'**가 걸린 것으로 본다(목록 내용이 계정마다 다르니
    계정별로 판정이 갈린 것도 설명된다). 세미나 id가 없는 주소의 판정은 버린다.

    URL을 못 읽으면(테스트 mock 등) 판정 근거로 쓰지 않고 통과시킨다.
    """
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


def seminar_ended(texts) -> bool:
    """상세에 '세미나 종료'가 떠 있는가 — 방송이 끝났다는 사이트의 표시."""
    joined = " ".join(strip_spaces(t) for t in (texts or []) if t)
    return strip_spaces(SEMINAR_END_MARKER) in joined


def seminar_running(texts) -> bool:
    """상세에 방송 전·중 표식이 떠 있는가. '세미나 종료'가 같이 있으면 아니다.

    진행 중 판정은 설문을 못 연 것을 quiet으로 덮는 쪽이라, 애매한 문구는 근거로
    쓰지 않는다. 그래서 두 가지를 거른다:
      - 문장(홍보 문구 등)은 보지 않는다. 버튼 라벨 길이만 대조한다.
      - '재입장하기'는 다시보기 입장이라 끝난 세미나에도 남으므로 지운다.
    """
    if seminar_ended(texts):
        return False
    for text in texts or []:
        label = strip_spaces(text)
        if not label or len(label) > RUNNING_LABEL_MAX_LEN:
            continue
        for marker in SEMINAR_NOT_RUNNING_MARKERS:
            label = label.replace(strip_spaces(marker), " ")
        if any(strip_spaces(m) in label for m in SEMINAR_RUNNING_MARKERS):
            return True
    return False


def usable_probes() -> list:
    """판정 근거로 써도 되는 상세 조회 기록만.

    `read_detail_verdict`가 URL 불일치·로그아웃 등으로 **이미 버린** 조회도
    진단용으로 `LAST_DETAIL_PROBE`에 남는다. 버린 기록을 진행 중 판정에 쓰면
    남의 페이지가 "아직 안 끝났다"가 된다 — 2026-09-15 세미나 5671·5681.
    """
    return [
        rec for rec in LAST_DETAIL_PROBE.values()
        if isinstance(rec, dict) and rec.get("usable")
    ]


def probe_saw_running_seminar() -> bool:
    """마지막 상세 조회에서 "아직 안 끝났다"를 관측했는가.

    쓸 수 있는 조회에서 방송 전·중 표식을 봤을 때만 참이다. 같은 조회에서
    '세미나 종료'를 봤으면 종료가 이긴다 — 설문은 세미나가 끝나야 열린다.
    """
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
    read_texts = visible or hidden
    # `usable`은 아래 검증을 다 통과한 뒤에야 참이 된다. 버린 조회는 진단으로만
    # 남고 진행 중·종료 판정에는 쓰이지 않는다.
    rec = {
        "url": final_url,
        "visible": visible,
        "hidden": hidden,
        "ended": bool(read_texts) and seminar_ended(read_texts),
        "running": bool(read_texts) and seminar_running(read_texts),
        "usable": False,
    }
    LAST_DETAIL_PROBE["m" if mobile else "www"] = rec

    # 다른 세미나 페이지(목록·안내)로 떨어졌으면 여기서 읽은 것은 전부 남의
    # 상태다. 버튼도 본문도 쓰지 않는다.
    if not detail_url_matches(final_url, seminar_id):
        host = "m" if mobile else "www"
        return "unknown", [], f"{host} 상세: 세미나 {seminar_id} 페이지가 아님({final_url})"

    verdict = detect_survey_marker(buttons)
    if verdict == "unknown":
        # 버튼 셀렉터가 안 맞을 수도 있으니 본문 전체로 한 번 더 본다. 단
        # 본문에는 다른 세미나의 완료 표시와 안내 문구가 섞이므로 여기서
        # `done`은 만들지 않는다 — 놓친 완료는 다음 런이 회복하지만, 거짓
        # 완료는 상태에 굳어 영원히 재시도되지 않는다.
        verdict = detect_survey_marker([body], allow_done=False)

    if mobile:
        if not is_mobile_session(page, buttons + [body]):
            return "unknown", [], "m 상세: www로 리다이렉트됐거나 안내 페이지"
        # 완료 표시는 그대로 믿는다. 반대로 '미참여'는 로그아웃 화면에서도 똑같이
        # 보이므로(로그인 증거가 없으면 '설문하기'만 뜬다) 채택하지 않는다.
        if verdict == "not_done" and not has_login_evidence(buttons + [body]):
            # 보류하는 것은 **설문 참여 판정**뿐이다. 페이지 신원은 위에서 이미
            # 확인했고, '세미나 종료'는 로그인 여부와 무관한 사이트 상태이므로
            # 종료·진행 중 관측은 살린다. 예전에는 이 기록까지 통째로 버려서,
            # m이 '세미나 종료'를 띄우고 있는데도 www의 '재입장하기'가 유일한
            # 근거가 되어 진행 중으로 읽혔다(2026-09-17 세미나 5666).
            rec["usable"] = True
            return "unknown", buttons, "m 상세: 로그인 증거 없이 미참여로 보임 — 판정 보류"
    rec["usable"] = True
    return verdict, buttons, ""


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
