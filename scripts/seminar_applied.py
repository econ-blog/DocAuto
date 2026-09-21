#!/usr/bin/env python3
"""닥터빌 세미나 신청 이력(seminar_applied.json) 관리 모듈.

이미 신청된 세미나를 매 런마다 상세 페이지를 열어 다시 확인하지 않도록
신청 완료 목록을 영속화하고, 만료된 세미나 이력을 주기적으로 정리(prune)한다.
"""

from datetime import datetime
from pathlib import Path

import common

SCRIPT_DIR = Path(__file__).resolve().parent
SEMINAR_APPLIED_PATH = SCRIPT_DIR.parent / "seminar_applied.json"
APPLIED_PATH = SEMINAR_APPLIED_PATH
APPLIED_RETENTION_DAYS = 60
APPLIED_EXPIRY_DAYS = APPLIED_RETENTION_DAYS


def load_applied(path: Path | str = None) -> dict:
    return common.read_json(path or SEMINAR_APPLIED_PATH, default={})


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


def applied_on(data: dict, account: str, date_str: str) -> list[dict]:
    """그 날짜에 방송되는 신청 이력 항목들. 설문 대상의 **영속** 후보다.

    입장 이력(`scripts/state/seminar_entered.json`)은 Actions 캐시에만 있어
    블록 런이 한 번도 저장하지 못하면 통째로 비어 버린다. 그때 설문은 대상이
    없다고 조용히 끝났다(2026-09-21 run 35590827440: "입장 이력 파일 없음").
    신청 이력은 레포에 커밋되므로 캐시와 무관하게 남는다 — 실제 참여 여부는
    어차피 상세 페이지가 가리므로, 후보를 넓게 잡아도 오판이 늘지 않는다.
    """
    acc = data.get(account) if isinstance(data, dict) else None
    if not isinstance(acc, dict):
        return []
    out = []
    for sid, entry in acc.items():
        if not isinstance(entry, dict):
            continue
        if (entry.get("start_date") or entry.get("date")) != date_str:
            continue
        item = {"id": int(sid) if str(sid).isdigit() else sid}
        if entry.get("title"):
            item["title"] = entry["title"]
        if entry.get("start"):
            item["start"] = entry["start"]
        out.append(item)
    out.sort(key=lambda i: str(i["id"]))
    return out
