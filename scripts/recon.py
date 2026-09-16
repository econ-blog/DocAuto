"""
정찰 스크립트 — 스펙(2026-07-31-notify-policy-cron-split-design.md) §10.

구현 전에 DOM 사실만 확인한다. 클릭·제출 등 부작용 있는 동작은 하지 않는다.

    python3 scripts/recon.py --item R2 --headed
    python3 scripts/recon.py --item R3 --headed
    python3 scripts/recon.py --item R4

R2: 출석 페이지 진입만으로 출석이 처리된 뒤 남는 "오늘 출석됨" 표식
R3: 세미나 상세의 시작 시각 표기 위치 (상태 파일 v2의 `start` 필드용)
R4: 이달의 퀴즈 캘린더에서 내일 셀에 제품명·pId가 채워지는지 (모듈 1 성립 여부)
R5: 세미나 목록에서 방송 날짜가 어느 노드에 있는지 (앵커 밖으로 나갔는지)

산출물은 scripts/logs/recon_<item>_<ts>.{json,png}. gitignore 대상이며
설문·개인정보가 찍힐 수 있으므로 커밋하지 않는다.
"""

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import common
import doctorville
from playwright.sync_api import sync_playwright


def dump_recon_data(item_id: str, data: dict, page=None) -> str:
    common.LOG_DIR.mkdir(parents=True, exist_ok=True)
    if page is not None:
        try:
            shot_path = common.save_screenshot(page, f"recon_{item_id}")
            if shot_path:
                data["screenshot"] = shot_path
        except Exception:
            pass
    ts = datetime.now(common.KST).strftime("%Y%m%d_%H%M%S")
    path = common.LOG_DIR / f"recon_{item_id}_{ts}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path)


# ---------------------------------------------------------------------------
# R2: 출석 완료 표식 — 페이지 진입만으로 출석이 처리되어 버튼이 사라진 상태에서
#     "오늘 날짜가 출석 처리됨"을 증명할 수 있는 DOM 표식을 찾는다.
# ---------------------------------------------------------------------------

ATTEND_JS = """
(today) => {
  const short = (s, n) => (s || '').replace(/\\s+/g, ' ').trim().slice(0, n);

  // 1) 출석 버튼 존재 여부
  const btns = Array.from(document.querySelectorAll('button, a'))
    .filter(el => (el.innerText || '').includes('출석'))
    .map(el => ({
      tag: el.tagName, id: el.id || null, class: el.className || null,
      text: short(el.innerText, 60), href: el.getAttribute('href')
    }));

  // 2) 달력형 셀 전수 — 오늘 셀의 클래스/자식 마크업이 핵심
  const cellSel = 'td, li, .day, [class*="day"], [class*="date"], [class*="attend"]';
  const cells = Array.from(document.querySelectorAll(cellSel))
    .filter(el => el.children.length < 12)
    .map(el => ({
      tag: el.tagName, class: el.className || null,
      text: short(el.innerText, 40),
      html: short(el.outerHTML, 400),
      hasToday: (el.innerText || '').includes(today.d) && el.className.length > 0,
      imgs: Array.from(el.querySelectorAll('img')).map(i => ({
        src: i.getAttribute('src'), alt: i.getAttribute('alt'), cls: i.className
      }))
    }))
    .filter(c => c.text.length > 0)
    .slice(0, 300);

  // 3) "출석/적립/완료/일째/연속" 문구를 가진 모든 노드
  const kw = ['출석', '적립', '완료', '일째', '연속', 'point', 'P'];
  const textNodes = Array.from(document.querySelectorAll('body *'))
    .filter(el => el.children.length === 0)
    .map(el => ({
      tag: el.tagName, class: el.className || null, id: el.id || null,
      text: short(el.innerText, 80)
    }))
    .filter(n => n.text && kw.some(k => n.text.includes(k)))
    .slice(0, 200);

  // 4) 상태를 담을 법한 hidden input / data-* 속성
  const inputs = Array.from(document.querySelectorAll('input')).map(el => ({
    type: el.type, name: el.getAttribute('name'), id: el.id || null,
    class: el.className || null, value: short(el.value, 60)
  })).slice(0, 100);

  const dataAttrs = Array.from(document.querySelectorAll('[data-date], [data-day], [data-attend], [data-status]'))
    .map(el => ({
      tag: el.tagName, class: el.className || null,
      attrs: Object.fromEntries(Array.from(el.attributes).map(a => [a.name, a.value])),
      text: short(el.innerText, 40)
    })).slice(0, 100);

  return {
    url: location.href,
    title: document.title,
    today,
    attendButtons: btns,
    bodyText: short(document.body.innerText, 4000),
    cells,
    textNodes,
    inputs,
    dataAttrs
  };
}
"""


def recon_r2(page) -> dict:
    """출석 페이지의 '오늘 출석됨' 표식 후보를 전수 덤프한다 (클릭하지 않음)."""
    now = datetime.now(KST)
    today = {
        "iso": now.strftime("%Y-%m-%d"),
        "d": str(now.day),
        "md": f"{now.month}월 {now.day}일",
        "dot": now.strftime("%Y.%m.%d"),
    }

    common.goto_with_retry(page, doctorville.ATTEND_URL, wait_until="domcontentloaded")
    page.wait_for_timeout(3000)

    data = {"pass1_on_entry": page.evaluate(ATTEND_JS, today)}
    data["screenshot"] = common.save_screenshot(page, "recon_R2_entry")

    # 진입만으로 출석이 처리된다면, 새로고침 후에도 동일 표식이 남아야 한다.
    common.reload_with_retry(page, wait_until="domcontentloaded")
    page.wait_for_timeout(3000)
    data["pass2_after_reload"] = page.evaluate(ATTEND_JS, today)
    data["screenshot_reload"] = common.save_screenshot(page, "recon_R2_reload")

    return data


# ---------------------------------------------------------------------------
# R4: 이달의 퀴즈 캘린더 — 내일 셀에 제품명·pId가 있는가
# ---------------------------------------------------------------------------

CALENDAR_JS = """
() => {
  const cal = document.querySelector('.quiz_calender');
  if (!cal) return {found: false};
  const cells = Array.from(cal.querySelectorAll('td')).map((td, i) => {
    const pid = td.querySelector('input.pIdCls');
    const anyInput = Array.from(td.querySelectorAll('input')).map(el => ({
      name: el.getAttribute('name'), cls: el.className, value: el.value
    }));
    return {
      index: i,
      class: td.className || null,
      text: (td.innerText || '').trim(),
      pIdCls: pid ? pid.value : null,
      inputs: anyInput,
      links: Array.from(td.querySelectorAll('a')).map(a => a.getAttribute('href'))
    };
  });
  return {found: true, cellCount: cells.length, cells, calendarText: (cal.innerText || '').trim()};
}
"""


def recon_r4(page) -> dict:
    common.goto_with_retry(page, doctorville.PRODUCT_MAIN_URL, wait_until="domcontentloaded")
    page.wait_for_timeout(3000)

    data = page.evaluate(CALENDAR_JS)
    today = datetime.now(common.KST)
    tomorrow = today + timedelta(days=1)
    data["today"] = today.strftime("%Y-%m-%d")
    data["tomorrow"] = tomorrow.strftime("%Y-%m-%d")

    if data.get("found"):
        cells = data["cells"]
        # 날짜 숫자로 오늘/내일 셀을 추정한다(클래스 today는 오늘 셀에만 있음).
        def cell_for_day(day: int):
            for c in cells:
                first = (c["text"].splitlines() or [""])[0].strip()
                if first == str(day):
                    return c
            return None

        data["todayCell"] = cell_for_day(today.day)
        data["tomorrowCell"] = cell_for_day(tomorrow.day)
        data["cellsWithPid"] = [c["index"] for c in cells if c.get("pIdCls")]

    data["screenshot"] = common.save_screenshot(page, "recon_R4_calendar")
    return data


# ---------------------------------------------------------------------------
# R3: 세미나 상세의 시작 시각 표기
# ---------------------------------------------------------------------------

TIMETEXT_JS = """
() => {
  const pat = /(\\d{4}[.\\-\\/]\\s?\\d{1,2}[.\\-\\/]\\s?\\d{1,2})|(\\d{1,2}:\\d{2})|(오전|오후)\\s?\\d{1,2}시/;
  const out = [];
  const walk = document.querySelectorAll('body *');
  for (const el of walk) {
    // 자식 요소가 없는(리프) 노드의 텍스트만 본다 — 상위 컨테이너 중복 제거
    if (el.children.length > 0) continue;
    const t = (el.innerText || '').trim();
    if (!t || t.length > 120) continue;
    if (!pat.test(t)) continue;
    let path = el.tagName.toLowerCase();
    if (el.id) path += '#' + el.id;
    if (el.className && typeof el.className === 'string') path += '.' + el.className.trim().split(/\\s+/).join('.');
    const parent = el.parentElement;
    let parentPath = null;
    if (parent) {
      parentPath = parent.tagName.toLowerCase();
      if (parent.id) parentPath += '#' + parent.id;
      if (parent.className && typeof parent.className === 'string') {
        parentPath += '.' + parent.className.trim().split(/\\s+/).join('.');
      }
    }
    out.push({selector: path, parent: parentPath, text: t});
    if (out.length >= 60) break;
  }
  return out;
}
"""

LIST_JS = """
() => Array.from(document.querySelectorAll('a.list_detail')).slice(0, 12).map(a => {
  const u = new URL(a.href, location.origin);
  return {
    seminarId: u.searchParams.get('seminarId'),
    text: (a.innerText || '').trim(),
    hasApply: !!a.querySelector('span.ico_apply'),
    hasEnter: !!a.querySelector('span.ico_enter')
  };
})
"""


def recon_r3(page, seminar_id: str | None) -> dict:
    data = {}
    common.goto_with_retry(page, doctorville.SEMINAR_MAIN_URL, wait_until="domcontentloaded")
    page.wait_for_timeout(2500)
    listing = page.evaluate(LIST_JS)
    data["listing"] = listing
    data["listScreenshot"] = common.save_screenshot(page, "recon_R3_list")

    if not seminar_id:
        for item in listing:
            if item.get("seminarId"):
                seminar_id = item["seminarId"]
                break
    if not seminar_id:
        data["error"] = "세미나 목록에서 seminarId를 찾지 못함"
        return data

    data["seminarId"] = seminar_id
    url = f"https://www.doctorville.co.kr/seminar/seminarDetail?seminarId={seminar_id}"
    common.goto_with_retry(page, url, wait_until="domcontentloaded")
    page.wait_for_timeout(2500)

    data["detailUrl"] = url
    data["timeTextCandidates"] = page.evaluate(TIMETEXT_JS)
    data["screenshot"] = common.save_screenshot(page, "recon_R3_detail")
    return data



# ---------------------------------------------------------------------------
# R5: 세미나 목록에서 **방송 날짜가 어느 DOM 노드에 있는지** 찾는다.
#
# 2026-09-16 daily 런: a.list_detail 66건을 다 잡고 list_ready도 true인데
# 일시 파싱 66건 전부 실패했다. raw 샘플이 "13:00 ~14:00 내분비질환 ..."로
# **시각만 있고 날짜가 없다**. 렌더 레이스가 아니라 날짜가 앵커 밖으로
# 나갔다는 뜻이다(그룹 헤더 등). SEMINAR_LIST_JS는 aEl.innerText만 읽으므로
# 앵커 밖 날짜는 구조적으로 못 본다.
#
# 그래서 이 정찰은 앵커 자체가 아니라 **앵커의 조상 체인과 앞 형제**를 본다.
# 부작용 없음(클릭·제출 안 함).
# ---------------------------------------------------------------------------

LIST_DOM_JS = r"""
() => {
  const DATE = /(\d{4}\s*[-./]\s*\d{1,2}\s*[-./]\s*\d{1,2})|(?<![\d:])\d{1,2}\s*[-./]\s*\d{1,2}(?![\d:])|\d{1,2}\s*월\s*\d{1,2}\s*일/;
  const desc = el => {
    if (!el) return null;
    let s = el.tagName.toLowerCase();
    if (el.id) s += '#' + el.id;
    if (el.className && typeof el.className === 'string' && el.className.trim()) {
      s += '.' + el.className.trim().split(/\s+/).join('.');
    }
    return s;
  };
  const flat = (t, n) => (t || '').replace(/\s+/g, ' ').trim().slice(0, n);
  // 자기 자신의 텍스트 노드만 — 자식 innerText가 섞이면 어디에 있는 값인지 모른다.
  const ownText = el => Array.from(el.childNodes)
    .filter(n => n.nodeType === 3).map(n => n.textContent.trim())
    .filter(Boolean).join(' ');

  const anchors = Array.from(document.querySelectorAll('a.list_detail'));

  const items = anchors.slice(0, 6).map((a, i) => {
    const chain = [];
    let cur = a.parentElement;
    for (let d = 0; d < 6 && cur && cur.tagName !== 'BODY'; d++) {
      const prev = [];
      let sib = cur.previousElementSibling;
      for (let k = 0; k < 3 && sib; k++) {
        const t = flat(sib.innerText, 70);
        prev.push({ sel: desc(sib), text: t, hasDate: DATE.test(t) });
        sib = sib.previousElementSibling;
      }
      const own = flat(ownText(cur), 70);
      chain.push({ depth: d, sel: desc(cur), ownText: own, ownHasDate: DATE.test(own), prevSiblings: prev });
      cur = cur.parentElement;
    }
    const inner = flat(a.innerText, 200);
    return {
      index: i,
      seminarId: (() => { try { return new URL(a.href).searchParams.get('seminarId'); } catch (e) { return null; } })(),
      innerText: inner,
      innerHasDate: DATE.test(inner),
      outerHTML: a.outerHTML.slice(0, 1200),
      hasApply: !!a.querySelector('span.ico_apply'),
      hasEnter: !!a.querySelector('span.ico_enter'),
      spans: Array.from(a.querySelectorAll('span, em, i, dt, dd'))
        .map(s => desc(s) + ' = ' + flat(s.innerText, 30)).slice(0, 15),
      ancestors: chain
    };
  });

  // 페이지 전체에서 날짜처럼 보이는 리프 노드 — 날짜가 실제로 어디 찍혀 있는지.
  const dateNodes = [];
  for (const el of document.querySelectorAll('body *')) {
    if (el.children.length > 0) continue;
    const t = flat(el.innerText, 60);
    if (!t || !DATE.test(t)) continue;
    dateNodes.push({ sel: desc(el), parent: desc(el.parentElement), text: t });
    if (dateNodes.length >= 40) break;
  }

  // 앵커와 날짜 헤더(div.seminar_day)의 실제 관계.
  // 1차 R5(2026-09-16)는 조상 6단 x 앞형제 3개만 봐서 관계를 못 잡았다.
  // 하루치 세미나가 여러 건이면 헤더는 3개보다 훨씬 앞 형제다 — 무제한으로 훑는다.
  const dayLink = anchors.slice(0, 10).map((a, i) => {
    const ancestorHeader = a.closest('.seminar_day');
    let node = a, hops = 0, found = null, foundFrom = null;
    // 앵커 자신 → 조상 순으로 올라가며 각 단계의 앞 형제를 끝까지 훑는다.
    outer: for (let d = 0; d < 8 && node && node.tagName !== 'BODY'; d++) {
      let sib = node.previousElementSibling;
      while (sib && hops < 300) {
        hops++;
        const hit = sib.matches && sib.matches('.seminar_day') ? sib : (sib.querySelector ? sib.querySelector('.seminar_day') : null);
        if (hit) { found = hit; foundFrom = 'depth' + d + ' 앞형제 ' + hops + '번째'; break outer; }
        sib = sib.previousElementSibling;
      }
      node = node.parentElement;
    }
    const hdr = ancestorHeader || found;
    return {
      index: i,
      seminarId: (() => { try { return new URL(a.href).searchParams.get('seminarId'); } catch (e) { return null; } })(),
      viaAncestor: !!ancestorHeader,
      via: ancestorHeader ? 'closest(.seminar_day)' : foundFrom,
      headerSel: desc(hdr),
      headerText: flat(hdr && hdr.innerText, 40),
      headerParent: desc(hdr && hdr.parentElement)
    };
  });

  // 첫 날짜 헤더 주변 구조 — 그룹핑이 형제 나열인지 중첩인지 눈으로 확인한다.
  const firstHeader = document.querySelector('.seminar_day');
  const headerContext = firstHeader ? {
    sel: desc(firstHeader),
    parent: desc(firstHeader.parentElement),
    parentHTML: flat(firstHeader.parentElement && firstHeader.parentElement.outerHTML, 1500),
    nextSiblings: (() => {
      const out = []; let sib = firstHeader.nextElementSibling;
      for (let k = 0; k < 5 && sib; k++) {
        out.push({ sel: desc(sib), anchors: sib.querySelectorAll ? sib.querySelectorAll('a.list_detail').length : 0, text: flat(sib.innerText, 50) });
        sib = sib.nextElementSibling;
      }
      return out;
    })()
  } : null;

  return {
    url: location.href,
    anchorCount: anchors.length,
    dayLink,
    headerContext,
    anchorsWithApply: anchors.filter(a => !!a.querySelector('span.ico_apply')).length,
    anchorsWithDate: anchors.filter(a => DATE.test(flat(a.innerText, 200))).length,
    items,
    dateNodes
  };
}
"""


def recon_r5(page) -> dict:
    """세미나 목록 DOM 구조. 날짜 노드의 위치를 앵커 기준으로 특정한다."""
    common.goto_with_retry(page, doctorville.SEMINAR_MAIN_URL, wait_until="domcontentloaded")
    # 운영 코드와 같은 대기를 쓴다 — 정찰이 더 오래 기다리면 레이스를 못 본다.
    ready = doctorville.wait_for_seminar_list(page)
    data = page.evaluate(LIST_DOM_JS)
    data["list_ready"] = ready
    data["today"] = datetime.now(common.KST).strftime("%Y-%m-%d")

    # 운영 스캐너를 그대로 돌려 본다. 읽기만 하므로 부작용이 없다 —
    # 날짜 헤더 픽스를 다음 daily 런을 기다리지 않고 여기서 확인하는 유일한 길이다.
    listed = page.evaluate(doctorville.SEMINAR_LIST_JS)
    rows, unparsed = doctorville.list_rows_for_today(listed)
    data["scan"] = {
        "items": len(listed),
        "unparsed": unparsed,
        "today_rows": len(rows),
        "applicable": sum(1 for i in listed if i.get("applicable")),
        "with_list_date": sum(1 for i in listed if i.get("listDate")),
        "sample_raw": [(i.get("raw") or "")[:120] for i in listed[:3]],
        "sample_rows": rows[:5],
    }
    return data


def summarize_r5(data: dict) -> str:
    """잡 로그에 바로 읽을 요약. 전체 덤프는 artifact JSON에 있다."""
    lines = [
        f"list_ready={data.get('list_ready')} anchors={data.get('anchorCount')} "
        f"with_apply={data.get('anchorsWithApply')} with_date={data.get('anchorsWithDate')}",
        "",
        "-- 날짜를 담은 노드 (앵커 밖 포함) --",
    ]
    nodes = data.get("dateNodes") or []
    if not nodes:
        lines.append("(없음 — 페이지 어디에도 날짜 표기가 없다)")
    for n in nodes[:12]:
        lines.append(f"  {n['sel']}  (부모 {n['parent']})  = {n['text']}")

    scan = data.get("scan")
    if scan:
        lines += ["", "-- 운영 스캐너(SEMINAR_LIST_JS) 결과 --",
                  f"  items={scan['items']} unparsed={scan['unparsed']} "
                  f"today_rows={scan['today_rows']} applicable={scan['applicable']} "
                  f"with_list_date={scan['with_list_date']}"]
        for r in scan.get("sample_raw", []):
            lines.append(f"    raw: {r}")
        for r in scan.get("sample_rows", []):
            lines.append(f"    row: {r.get('start')} | {r.get('title')}")

    lines += ["", "-- 앵커 → 날짜 헤더 연결 --"]
    for d in (data.get("dayLink") or [])[:10]:
        lines.append(
            f"  [{d['index']}] sid={d['seminarId']} via={d['via']} "
            f"header={d['headerSel']} ({d['headerParent']}) = {d['headerText']!r}")

    hc = data.get("headerContext")
    if hc:
        lines += ["", "-- 첫 날짜 헤더 주변 --",
                  f"  {hc['sel']} (부모 {hc['parent']})"]
        for n in hc.get("nextSiblings", []):
            lines.append(f"    다음형제 {n['sel']} anchors={n['anchors']} = {n['text']}")
        lines.append(f"  부모 HTML: {hc['parentHTML'][:600]}")

    lines += ["", "-- 앵커별 조상 체인에서 날짜가 걸린 지점 --"]
    for item in (data.get("items") or [])[:3]:
        lines.append(f"[{item['index']}] seminarId={item['seminarId']} innerHasDate={item['innerHasDate']}")
        lines.append(f"    innerText: {item['innerText'][:100]}")
        for anc in item.get("ancestors", []):
            hits = [p for p in anc.get("prevSiblings", []) if p.get("hasDate")]
            if anc.get("ownHasDate") or hits:
                lines.append(f"    depth{anc['depth']} {anc['sel']} own={anc['ownText']!r}")
                for h in hits:
                    lines.append(f"        앞형제 {h['sel']} = {h['text']}")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--item", required=True, choices=["R2", "R3", "R4", "R5"])
    parser.add_argument("--account", default="bjh7790")
    parser.add_argument("--seminar-id", default=None)
    parser.add_argument("--credentials", default="credentials.json")
    parser.add_argument("--headed", action="store_true")
    args = parser.parse_args()

    creds = doctorville.load_credentials(Path(args.credentials), args.account)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.headed)
        context = browser.new_context(locale="ko-KR", ignore_https_errors=True)
        page = context.new_page()
        page.set_default_timeout(doctorville.DEFAULT_TIMEOUT_MS)
        try:
            common.goto_with_retry(page, doctorville.ATTEND_URL, wait_until="load")
            if not doctorville.ensure_logged_in(page, creds):
                print(json.dumps({"error": "로그인 실패"}, ensure_ascii=False))
                sys.exit(1)

            if args.item == "R2":
                data = recon_r2(page)
            elif args.item == "R4":
                data = recon_r4(page)
            elif args.item == "R5":
                data = recon_r5(page)
            else:
                data = recon_r3(page, args.seminar_id)
        finally:
            context.close()
            browser.close()

    out = dump_recon_data(args.item, data)
    print(f"[recon] {args.item} → {out}")
    if args.item == "R5":
        print(summarize_r5(data))
    else:
        print(json.dumps(data, ensure_ascii=False, indent=2)[:4000])


if __name__ == "__main__":
    main()
