#!/usr/bin/env python3
"""세미나 설문 가능 시간 창(Survey Window) 및 마감 판정 모듈.

설문 창은 공지된 일정이 아니라 실제 종료 시각을 따른다:
[실제 종료, 실제 종료 + 1시간].
실제 종료 관측 전에는 공지 시작 30분 전부터 사전 게이트로 탐색을 허용한다.
"""

import re
from datetime import datetime, timedelta
from pathlib import Path

import common
from common import KST, parse_dd_date

# 설문 창은 실제 종료 후 1시간 동안 유지
SURVEY_CLOSE_GRACE = timedelta(hours=1)
# 실제 종료 관측 전 사전 게이트: 공지 시작 30분 전부터 시도
SURVEY_PROBE_LEAD = timedelta(minutes=30)
# 관측되지 않은 세미나 최대 재시도 한도 (공지 시작 기준)
SURVEY_STALE_AFTER = timedelta(hours=12)
# 공지 종료 후 '진행 중' 관측을 신뢰할 수 있는 최대 유예 시간
SURVEY_RUNNING_GRACE = timedelta(minutes=30)


def parse_kst(value) -> datetime | None:
    """ISO 문자열을 KST datetime으로. 못 읽으면 None."""
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None
    return dt.replace(tzinfo=common.KST) if dt.tzinfo is None else dt


def observed_end(item: dict) -> datetime | None:
    """관측된 실제 종료 시각. 없으면 None.

    `ended_at`은 상세에서 '세미나 종료'를 처음 본 시각, 또는 설문 창이 실제로
    열린 시각이다(설문은 세미나가 끝나야 열린다). 공지된 종료는 쓰지 않는다.
    """
    return parse_kst((item or {}).get("ended_at"))


def scheduled_bounds(item: dict) -> tuple[datetime | None, datetime | None]:
    """공지된 일정만으로 계산한 (설문 오픈, 세미나 종료). 관측은 섞지 않는다."""
    if not isinstance(item, dict):
        return None, None
    start_str = item.get("start")
    if start_str and isinstance(start_str, str):
        s_dt, e_dt = parse_dd_date(start_str)
        if s_dt:
            return s_dt + timedelta(minutes=30), e_dt or (s_dt + timedelta(hours=1))
        m = re.search(r"(\d{4}-\d{2}-\d{2})\s*\([^)]+\)\s*(\d{2}:\d{2})", start_str)
        if m:
            d_str, s_str = m.groups()
            try:
                s_dt = datetime.strptime(f"{d_str} {s_str}", "%Y-%m-%d %H:%M").replace(tzinfo=common.KST)
                return s_dt + timedelta(minutes=30), s_dt + timedelta(hours=1)
            except ValueError:
                pass

    ent_dt = parse_kst(item.get("entered_at"))
    if ent_dt is not None:
        return ent_dt + timedelta(minutes=30), ent_dt + timedelta(hours=1)
    return None, None


def get_survey_window(item: dict) -> tuple[datetime | None, datetime | None]:
    """설문 가능 시간 창 (open_dt, close_dt). **실제 종료 기준**이다.

    종료를 아직 관측하지 못했으면 창을 모른다 — (None, None)을 돌려주고,
    시도 여부는 `evaluate_survey_cutoff`의 사전 게이트가 정한다.
    """
    ended = observed_end(item)
    if ended is None:
        return None, None
    return ended, ended + SURVEY_CLOSE_GRACE


def get_survey_cutoff(item: dict) -> datetime | None:
    """설문 마감 시각 (실제 종료 1시간 후). 종료 미관측이면 None."""
    return get_survey_window(item)[1]


def evaluate_survey_cutoff(item: dict, now_dt: datetime = None) -> str:
    """설문 시도 가능 여부 판정.

    - 종료를 관측했으면 창은 [종료, 종료 + 1시간]이다.
    - 관측 전이면 공지 시작 30분 전부터 시도한다 — 종료를 확인하려면 어차피
      한 번은 열어 봐야 하고, 그 전에는 열어 볼 이유가 없다. 공지된 **종료**는
      어느 쪽 판정에도 쓰지 않는다(공지가 양쪽으로 틀리는 것이 확인됐다).
    """
    if now_dt is None:
        now_dt = datetime.now(common.KST)
    elif now_dt.tzinfo is None:
        now_dt = now_dt.replace(tzinfo=common.KST)

    open_dt, close_dt = get_survey_window(item)
    if open_dt is not None:
        if now_dt < open_dt:
            return "not_ready"
        if close_dt is not None and now_dt > close_dt:
            return "closed"
        return "ready"

    # 종료 미관측 — 사전 게이트만 본다.
    probe_from, _ = scheduled_bounds(item)
    if probe_from is None:
        return "ready"
    if now_dt < probe_from:
        return "not_ready"
    if now_dt > probe_from - SURVEY_PROBE_LEAD + SURVEY_STALE_AFTER:
        return "closed"
    return "ready"


def scheduled_end_passed(item: dict, now_dt: datetime = None) -> bool:
    """공지된 종료 + 여유가 지났는가. 일정을 모르면 False(판정에 쓰지 않는다)."""
    _, end_dt = scheduled_bounds(item)
    if end_dt is None:
        return False
    if now_dt is None:
        now_dt = datetime.now(common.KST)
    elif now_dt.tzinfo is None:
        now_dt = now_dt.replace(tzinfo=common.KST)
    return now_dt > end_dt + SURVEY_RUNNING_GRACE


def unopened_status(item: dict, now_dt: datetime = None, running: bool = False) -> str:
    """설문에 손도 못 댔을 때의 상태.

    - 창이 아직 안 열렸으면 `not_ready`, 마감 후면 `closed` — 둘 다 정상이므로 quiet.
    - **창이 열려 있는데도 못 열었으면 `unverified`**(alert). 성공도 실패도 확인
      못 한 상태다.

    2026-09-09 세미나 5627: 같은 런에서 bjh7790은 success, wonju만 `not_ready`로
    떨어졌다. 창은 열려 있었으니 "아직 안 열림"이 아니라 그냥 못 연 것이었는데,
    `not_ready`가 quiet이라 텔레그램에 뜨지 않았다. 손으로 재시도해서 붙였을 뿐,
    안 봤으면 창이 닫힐 때까지 한 계정만 누락된 채로 끝났다. 창이 열린 동안의
    실패는 조용히 넘기지 않는다.

    `running=True`는 상세에서 "아직 안 끝났다"를 본 경우다. 설문은 세미나가
    끝나야 열리므로 이때 못 여는 것은 실패가 아니다 — quiet `not_ready`.
    **단 공지된 종료가 `SURVEY_RUNNING_GRACE`만큼 지났으면 그 관측을 믿지 않는다.**
    2026-09-15 세미나 5671(13:00~14:00)을 14:39에 '진행 중'으로 읽고 조용히
    넘겼다. 공지는 양쪽으로 틀리지만 40분씩 틀리지는 않는다 — 관측 쪽이 틀렸다.
    """
    st = evaluate_survey_cutoff(item, now_dt)
    if st in ("not_ready", "closed"):
        return st
    if running and not scheduled_end_passed(item, now_dt):
        # 세미나가 아직 안 끝났다 — 설문은 원래 이때 안 열린다. 정상이므로 quiet.
        return "not_ready"
    # 공지된 종료가 한참 지났는데도 '진행 중'으로 보인다면 관측 쪽이 틀렸다고
    # 본다. 13:00~14:00 세미나가 14:39에 진행 중일 수는 없다(2026-09-15 5671).
    return "unverified"
