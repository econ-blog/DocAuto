#!/usr/bin/env python3
"""일일/세미나 실행 로그의 적재·조회·표 렌더링.

daily와 seminar_block은 실행 단위가 다르므로 로그도 두 종류로 나뉜다.

- daily   : 한 런이 표의 한 행(run1, run2 …). 런마다 append 된다.
- seminar : 세미나 하나가 표의 한 행. 신청/입장/설문 세 단계가 각자
            같은 파일을 read-modify-write 하며 자기 칸만 채운다.

로그는 레포의 ``logs/`` 에 커밋된다. GitHub Actions는 런마다 새 체크아웃이라
파일을 영속화하지 않으면 run2 이후의 append가 불가능하기 때문이다.
종류별로 최근 ``KEEP_FILES`` 일치만 남기고 오래된 파일은 지운다.
"""

import os
import unicodedata
from datetime import datetime
from pathlib import Path

import common

REPO_ROOT = Path(__file__).resolve().parent.parent

# 기본은 레포의 logs/. 테스트는 DOCAUTO_LOG_DIR로 tmp를 가리켜, 스크립트 내부에서
# 로그를 쓰는 함수(task_seminar 등)를 호출해도 레포가 더럽혀지지 않게 한다.
LOG_DIR = Path(os.environ.get("DOCAUTO_LOG_DIR") or (REPO_ROOT / "logs"))

KIND_DAILY = "daily"
KIND_SEMINAR = "seminar"
KINDS = (KIND_DAILY, KIND_SEMINAR)

KEEP_FILES = 7

# 세미나 표의 단계 컬럼 (로그 키 → 표시명)
SEMINAR_PHASES = (("apply", "신청"), ("live", "입장"), ("survey", "설문"))

STATUS_EMOJIS = {
    "success": "✅",
    "already_done": "☑️",
    "skipped": "⏭️",
    "no_target": "⏭️",
    "no_answer": "❓",
    "incomplete_bank": "❓",
    "failed": "❌",
    "unverified": "⚠️",
    "blocked": "🚫",
    "not_ready": "⏳",
    "closed": "🔒",
}

EMPTY_CELL = "·"

# 여러 계정/항목을 한 칸에 합칠 때, 나쁜 쪽이 이긴다.
_STATUS_RANK = {
    "failed": 6, "blocked": 6, "unverified": 5,
    "no_answer": 4, "incomplete_bank": 4,
    "not_ready": 3, "closed": 3,
    "skipped": 2, "no_target": 2,
    "already_done": 1, "success": 0,
}


# ---------------------------------------------------------------------------
# 파일 입출력
# ---------------------------------------------------------------------------

def today_str() -> str:
    return datetime.now(common.KST).strftime("%Y-%m-%d")


def resolve_log_dir(log_dir: Path | str = None) -> Path:
    """인자 → DOCAUTO_LOG_DIR 환경변수 → 레포의 logs/ 순으로 로그 디렉터리를 정한다."""
    if log_dir:
        return Path(log_dir)
    return Path(os.environ.get("DOCAUTO_LOG_DIR") or (REPO_ROOT / "logs"))


def log_path(kind: str, date_str: str = None, log_dir: Path | str = None) -> Path:
    return resolve_log_dir(log_dir) / f"{kind}-{date_str or today_str()}.json"


def load(kind: str, date_str: str = None, log_dir: Path | str = None) -> dict:
    """해당 날짜 로그를 읽는다. 없으면 빈 스켈레톤을 만들어 반환한다."""
    date_str = date_str or today_str()
    path = log_path(kind, date_str, log_dir)
    data = common.read_json(path, default=None) if path.exists() else None
    if not isinstance(data, dict):
        data = {}
    data.setdefault("kind", kind)
    data.setdefault("date", date_str)
    if kind == KIND_DAILY:
        data.setdefault("columns", [])
        data.setdefault("runs", [])
    else:
        data.setdefault("seminars", {})
    return data


def save(data: dict, log_dir: Path | str = None) -> Path:
    d = resolve_log_dir(log_dir)
    d.mkdir(parents=True, exist_ok=True)
    path = log_path(data["kind"], data["date"], d)
    # 세미나 블록은 세 스크립트가 같은 파일을 번갈아 쓴다. 중간에 죽어도
    # 반쯤 쓰인 JSON이 남지 않도록 원자적 교체를 쓴다.
    common.write_json_atomic(path, data)
    return path


def prune(kind: str, keep: int = KEEP_FILES, log_dir: Path | str = None) -> list[str]:
    """종류별로 최근 keep개만 남기고 삭제. 지운 파일명 목록을 반환한다."""
    d = resolve_log_dir(log_dir)
    if not d.exists():
        return []
    # 파일명이 kind-YYYY-MM-DD.json 이라 사전순 = 날짜순이다.
    files = sorted(d.glob(f"{kind}-*.json"))
    removed = []
    for path in files[:max(0, len(files) - keep)]:
        try:
            path.unlink()
            removed.append(path.name)
        except OSError:
            pass
    return removed


# ---------------------------------------------------------------------------
# 상태값 헬퍼
# ---------------------------------------------------------------------------

def status_of(node) -> str:
    """결과 노드에서 status를 뽑는다. verified_by 없는 success는 unverified로 강등.

    notify.severity_of와 같은 규칙이라 표와 알림의 판정이 어긋나지 않는다.
    """
    if not isinstance(node, dict):
        return ""
    st = node.get("status")
    if not st:
        return ""
    if st == "success" and not node.get("verified_by"):
        return "unverified"
    return st


def merge_status(statuses) -> str:
    """여러 상태 중 가장 나쁜 것을 고른다. (계정 2개를 한 칸에 합칠 때)"""
    vals = [s for s in statuses if s]
    if not vals:
        return ""
    return max(vals, key=lambda s: _STATUS_RANK.get(s, 6))


def emoji(status: str) -> str:
    if not status:
        return EMPTY_CELL
    return STATUS_EMOJIS.get(status, "❔")


# ---------------------------------------------------------------------------
# daily 로그
# ---------------------------------------------------------------------------

def append_daily_run(cells: dict, date_str: str = None, at: str = None,
                     log_dir: Path | str = None) -> dict:
    """daily 런 1건을 append 한다. cells는 {컬럼명: status} 이다."""
    data = load(KIND_DAILY, date_str, log_dir)
    # 컬럼은 날짜 파일 안에서 누적한다. 런마다 계정 구성이 달라져도
    # 앞선 런의 컬럼이 사라지지 않게 하기 위함이다.
    for col in cells:
        if col not in data["columns"]:
            data["columns"].append(col)
    data["runs"].append({
        "run": len(data["runs"]) + 1,
        "at": at or datetime.now(common.KST).strftime("%H:%M"),
        "cells": dict(cells),
    })
    save(data, log_dir)
    return data


def daily_cells(results: dict, creds: dict = None) -> dict:
    """daily_runner의 결과 dict를 {컬럼명: status}로 평탄화한다."""
    creds = creds or {}
    cells: dict[str, str] = {}

    for key, node in results.items():
        if not isinstance(node, dict):
            continue

        if key.startswith("doctorville_"):
            acc = key[len("doctorville_"):]
            label = common.account_label(creds, acc)
            found = False
            for task, name in (("attend", "출석"), ("quiz", "퀴즈"), ("seminar", "세미나")):
                st = status_of(node.get(task))
                if st and st != "skipped":
                    cells[f"{name}\n{label}"] = st
                    found = True
            if not found:
                # 서브프로세스가 통째로 죽으면 task별 노드가 없다({"status": "failed"}).
                # 그대로 두면 실패가 표에서 사라지므로 계정 단위 칸으로 살린다.
                top = status_of(node)
                if top and top != "skipped":
                    cells[f"닥터빌\n{label}"] = top

        elif key == "keymedi":
            cells["키메디\n출석"] = status_of(node)

        elif key == "hmp":
            cells["HMP\n캡슐"] = status_of(node)
            roulette = node.get("roulette")
            if isinstance(roulette, list) and roulette:
                cells["HMP\n룰렛"] = merge_status(status_of(r) for r in roulette)
            cells["HMP\n댓글"] = status_of(node.get("comment"))
            cells["HMP\n글쓰기"] = merge_status([
                status_of(node.get("post")), status_of(node.get("post_precheck")),
            ])

        elif key == "precheck_quiz":
            # --task precheck_quiz 는 단일 계정 결과를 그대로 찍는다.
            cells["익일\n퀴즈"] = status_of(node.get("precheck_quiz")) or status_of(node)

    return {k: v for k, v in cells.items() if v}


def daily_table(date_str: str = None, log_dir: Path | str = None) -> tuple[list[str], list[list[str]]]:
    """daily 로그를 (헤더, 행들)로 변환한다. 행 = run1, run2 …"""
    data = load(KIND_DAILY, date_str, log_dir)
    headers = [""] + list(data["columns"])
    rows = []
    for entry in data["runs"]:
        label = f"run{entry['run']}\n{entry.get('at', '')}".strip()
        rows.append([label] + [emoji(entry["cells"].get(c, "")) for c in data["columns"]])
    return headers, rows


# ---------------------------------------------------------------------------
# seminar 로그
# ---------------------------------------------------------------------------

def update_seminar(seminar_id, phase: str = None, status: str = "", account: str = "",
                   title: str = None, start: str = None, end: str = None,
                   date_str: str = None, log_dir: Path | str = None) -> dict:
    """세미나 1건의 한 칸(신청/입장/설문)을 갱신한다.

    account를 주면 계정별로 따로 적재하고, 렌더링 때 합쳐서 한 칸으로 보여준다.
    phase 없이 호출하면 제목·시간 메타데이터만 채운다(= 표에 행만 만든다).
    """
    sid = str(seminar_id)
    data = load(KIND_SEMINAR, date_str, log_dir)
    entry = data["seminars"].setdefault(sid, {"id": sid})

    # 메타데이터는 값이 있을 때만 덮어쓴다. 단계마다 알 수 있는 정보가 달라서,
    # 나중 단계가 None으로 앞 단계의 제목·시간을 지우면 안 된다.
    if title:
        entry["title"] = title
    if start:
        entry["start"] = start
    if end:
        entry["end"] = end

    if phase and status:
        slot = entry.setdefault(phase, {})
        if not isinstance(slot, dict):
            slot = {}
            entry[phase] = slot
        slot[account or "_"] = status
        entry["updated_at"] = datetime.now(common.KST).strftime("%H:%M")

    save(data, log_dir)
    return data


def split_times(raw: str) -> tuple[str, str]:
    """'2026-08-26(수) 17:00 ~ 18:30' → ('17:00', '18:30').

    닥터빌 상세의 dd.date 원문을 그대로 로그에 담아두고 렌더링 때 쪼갠다.
    파싱이 안 되면 첫 시각만이라도 건진다.
    """
    if not raw or not isinstance(raw, str):
        return "", ""
    s_dt, e_dt = common.parse_dd_date(raw)
    if s_dt is not None:
        return s_dt.strftime("%H:%M"), e_dt.strftime("%H:%M") if e_dt else ""
    import re
    found = re.findall(r"\b(\d{1,2}):(\d{2})\b", raw)
    times = [f"{int(h):02d}:{m}" for h, m in found]
    return (times[0] if times else "", times[1] if len(times) > 1 else "")


def seminar_rows(date_str: str = None, log_dir: Path | str = None,
                 applied: dict = None, accounts: list[str] = None) -> list[dict]:
    """오늘의 세미나 행 목록을 만든다.

    로그에 기록된 세미나 ∪ seminar_applied.json에서 시작일이 오늘인 세미나.
    후자를 섞는 이유는, 신청만 해두고 아직 입장 시간이 안 된 세미나도
    "그날 예정된 세미나"로 표에 보여야 하기 때문이다.
    """
    date_str = date_str or today_str()
    data = load(KIND_SEMINAR, date_str, log_dir)
    # 세미나 목록 페이지에는 다른 날짜 세미나도 섞여 있다. 날짜가 확인되는
    # 항목 중 오늘이 아닌 것은 표에서 뺀다(날짜 미상은 남긴다).
    merged: dict[str, dict] = {}
    for sid, entry in data["seminars"].items():
        raw = str(entry.get("start") or "")
        import re as _re
        found = _re.search(r"\d{4}-\d{2}-\d{2}", raw)
        if found and found.group(0) != date_str:
            continue
        merged[sid] = dict(entry)

    for acc, seminars in (applied or {}).items():
        if accounts and acc not in accounts:
            continue
        if not isinstance(seminars, dict):
            continue
        for sid, info in seminars.items():
            if not isinstance(info, dict):
                continue
            raw = info.get("start") or ""
            # start_date가 있으면 그걸로 정확히 거르고, 없는 옛 항목은 원문 대조로 폴백.
            day = info.get("start_date") or ""
            if (day and day != date_str) or (not day and date_str not in str(raw)):
                continue
            entry = merged.setdefault(str(sid), {"id": str(sid)})
            if not entry.get("title"):
                entry["title"] = info.get("title") or ""
            if not entry.get("start"):
                entry["start"] = info.get("start_time") or raw
            if not entry.get("end"):
                entry["end"] = info.get("end_time") or ""
            # 신청 이력에 있다 = 이미 신청된 세미나다. 이번 런에서 신청 단계가
            # 돌지 않았어도(이력 덕에 상세를 건너뜀) 신청 칸은 완료로 보여야 한다.
            slot = entry.setdefault("apply", {})
            if not slot.get(acc):
                slot[acc] = "already_done"

    def sort_key(sid):
        start, _ = split_times(merged[sid].get("start", ""))
        return (start or "99:99", sid)

    return [merged[sid] for sid in sorted(merged, key=sort_key)]


def seminar_table(date_str: str = None, log_dir: Path | str = None,
                  applied: dict = None, accounts: list[str] = None
                  ) -> tuple[list[str], list[list[str]]]:
    """세미나 로그를 (헤더, 행들)로 변환한다. 행 = 세미나."""
    headers = ["세미나", "시작", "종료"] + [name for _, name in SEMINAR_PHASES]
    rows = []
    for entry in seminar_rows(date_str, log_dir, applied, accounts):
        sid = entry.get("id", "")
        title = entry.get("title") or f"세미나 {sid}"
        # 닥터빌은 제목이 통째로 '라이브세미나'인 경우가 흔해 그대로면 행 구분이 안 된다.
        if title.replace(" ", "") == "라이브세미나":
            title = f"라이브세미나 #{sid}"
        start, end_from_raw = split_times(entry.get("start", ""))
        end, _ = split_times(entry.get("end", ""))
        row = [title, start, end or end_from_raw]
        for phase, _ in SEMINAR_PHASES:
            slot = entry.get(phase)
            row.append(emoji(merge_status(slot.values()) if isinstance(slot, dict) else ""))
        rows.append(row)
    return headers, rows


# ---------------------------------------------------------------------------
# 고정폭 텍스트 표 (PNG 렌더 실패 시 폴백)
# ---------------------------------------------------------------------------

def display_width(text: str) -> int:
    """모노스페이스 기준 표시 폭. 한글·이모지는 2칸으로 센다."""
    width = 0
    for ch in str(text):
        if unicodedata.combining(ch):
            continue
        if unicodedata.east_asian_width(ch) in ("W", "F") or ord(ch) >= 0x1F300:
            width += 2
        else:
            width += 1
    return width


def pad(text: str, width: int, align: str = "left") -> str:
    fill = max(0, width - display_width(text))
    if align == "right":
        return " " * fill + str(text)
    if align == "center":
        left = fill // 2
        return " " * left + str(text) + " " * (fill - left)
    return str(text) + " " * fill


def render_text_table(headers: list[str], rows: list[list[str]]) -> str:
    """고정폭 텍스트 표. 헤더의 개행(\\n)은 공백으로 눕힌다."""
    flat_headers = [" ".join(str(h).split("\n")) for h in headers]
    all_rows = [flat_headers] + [[" ".join(str(c).split("\n")) for c in r] for r in rows]
    ncols = max(len(r) for r in all_rows) if all_rows else 0
    all_rows = [r + [""] * (ncols - len(r)) for r in all_rows]
    widths = [max(display_width(r[i]) for r in all_rows) for i in range(ncols)]

    def line(left, mid, right):
        return left + mid.join("─" * (w + 2) for w in widths) + right

    def row_text(cells, first_align="center"):
        # 첫 열은 라벨(세미나명·run번호), 나머지는 상태 이모지라 가운데 정렬이 읽기 좋다.
        return "│" + "│".join(
            f" {pad(c, widths[i], first_align if i == 0 else 'center')} "
            for i, c in enumerate(cells)
        ) + "│"

    out = [line("┌", "┬", "┐"), row_text(all_rows[0]), line("├", "┼", "┤")]
    for r in all_rows[1:]:
        out.append(row_text(r, "left"))
    out.append(line("└", "┴", "┘"))
    return "\n".join(out)


# ---------------------------------------------------------------------------
# 표 전송 (PNG 우선, 실패 시 고정폭 텍스트)
# ---------------------------------------------------------------------------

LEGEND = "✅성공 ☑️이미완료 ❓정답필요 ❌실패 ⚠️미검증 ⏭️건너뜀 ⏳대기 🔒마감 ·해당없음"

PNG_DIR = REPO_ROOT / "scripts" / "logs"   # gitignore·artifact 대상


def send_table(title: str, headers: list[str], rows: list[list[str]],
               png_name: str, credentials_path=None, legend: str = LEGEND) -> dict:
    """표를 PNG로 렌더해 텔레그램으로 보낸다. 렌더/전송 실패 시 텍스트 표로 폴백."""
    import notify
    import tablepng

    if not rows:
        return {"status": "no_target", "message": "표에 넣을 행이 없음."}

    png = tablepng.render_png(title, headers, rows, PNG_DIR / png_name, legend=legend)
    if png is not None:
        if notify.send_photo(png, caption=title, credentials_path=credentials_path):
            return {"status": "success", "verified_by": "telegram_sendPhoto", "png": str(png)}
        print("[runlog] 사진 전송 실패 — 텍스트 표로 폴백")

    text = f"{title}\n<pre>{render_text_table(headers, rows)}</pre>\n{legend}"
    ok = notify.send_telegram(text, credentials_path=credentials_path, parse_mode="HTML")
    if ok:
        return {"status": "success", "verified_by": "telegram_text_fallback"}
    return {"status": "failed", "message": "표 전송 실패 (PNG·텍스트 모두)."}
