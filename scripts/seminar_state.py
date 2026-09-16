#!/usr/bin/env python3
"""세미나 당일 상태(seminar_state.json) 관리 모듈.

라이브 입장(seminar_live)과 설문(seminar_survey)이 공유하는 상태 파일의
스키마 마이그레이션, 적재, 병합, 저장 및 질의/갱신 함수를 제공한다.
"""

from datetime import datetime
from pathlib import Path

import common


def upgrade_to_v2(state: dict) -> dict:
    """Upgrades state dict from schema v1 to schema v2 in-place and returns it.

    In v2:
    - version is set to 2.
    - entered list items are upgraded from int N to {"id": N, "title": None, "start": None, "entered_at": None}.
    - survey_done list is replaced by survey dict {"N": "done"}.
    """
    if not isinstance(state, dict):
        return {"version": 2, "accounts": {}}
    if state.get("version") == 2 and "survey_done" not in str(state):
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


def pending_seminar_ids(state: dict, account: str) -> list[int]:
    """당일 입장했으나 아직 설문하지 않은 세미나 ID 목록."""
    if not isinstance(state, dict):
        return []
    state = upgrade_to_v2(state)
    acc = state.get("accounts", {}).get(account, {})
    survey_map = acc.get("survey", {})
    survey_done_list = acc.get("survey_done", [])
    pending = []
    for item in acc.get("entered", []):
        sid = item["id"] if isinstance(item, dict) else int(item)
        sid_str = str(sid)
        if sid_str not in survey_map and sid not in survey_done_list and int(sid) not in survey_done_list:
            pending.append(sid)
    return pending


def get_entered_item(state: dict, account: str, seminar_id: int | str) -> dict:
    if isinstance(state, dict):
        acc = state.get("accounts", {}).get(account, {})
        for item in acc.get("entered", []):
            if isinstance(item, dict) and str(item.get("id")) == str(seminar_id):
                return item
            elif isinstance(item, int) and str(item) == str(seminar_id):
                return {"id": item}
    return {"id": int(seminar_id) if str(seminar_id).isdigit() else seminar_id}


def mark_survey_status(state: dict, account: str, seminar_id: int | str, status_str: str = "done", path=None) -> None:
    if not isinstance(state, dict):
        return
    state = upgrade_to_v2(state)
    acc = state.setdefault("accounts", {}).setdefault(account, {})
    survey = acc.setdefault("survey", {})
    sid_str = str(seminar_id)
    survey[sid_str] = status_str
    if path is not None:
        save_state(state, path)


def clear_survey_status(state: dict, account: str, seminar_id: int | str, path=None) -> bool:
    """설문 이력 표시를 지운다 — 다음 런이 이 세미나를 다시 집게 된다.

    잘못 박힌 `closed`를 그대로 두면 `pending_seminar_ids`가 영영 건너뛴다.
    """
    if not isinstance(state, dict):
        return False
    acc = (state.get("accounts") or {}).get(account) or {}
    survey = acc.get("survey")
    if not isinstance(survey, dict) or str(seminar_id) not in survey:
        return False
    survey.pop(str(seminar_id))
    if path is not None:
        save_state(state, path)
    return True


def get_survey_meta(state: dict, account: str, seminar_id: int | str) -> dict:
    """세미나별 설문 부가 상태(진행 중 관측 시각 등). 없으면 빈 dict."""
    if not isinstance(state, dict):
        return {}
    acc = (state.get("accounts") or {}).get(account) or {}
    meta = (acc.get("survey_meta") or {}).get(str(seminar_id))
    return meta if isinstance(meta, dict) else {}


def mark_survey_ended(state: dict, account: str, seminar_id: int | str, at: str, path=None) -> None:
    """"이 시각에 세미나가 끝나 있었다"를 기록한다. 설문 창의 기준점이다.

    관측 시각은 실제 종료보다 뒤다(블록 간격만큼). 그래서 **가장 이른 관측**만
    남긴다 — 나중 관측으로 덮으면 창이 통째로 뒤로 밀린다.
    """
    if not isinstance(state, dict) or not account:
        return
    state = upgrade_to_v2(state)
    acc = state.setdefault("accounts", {}).setdefault(account, {})
    meta = acc.setdefault("survey_meta", {}).setdefault(str(seminar_id), {})
    prev = meta.get("ended_at")
    if isinstance(prev, str) and prev and prev <= at:
        return
    meta["ended_at"] = at
    if path is not None:
        save_state(state, path)


def mark_survey_done(state: dict, account: str, seminar_id: int | str, path=None) -> None:
    mark_survey_status(state, account, seminar_id, "done", path)
