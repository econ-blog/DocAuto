#!/usr/bin/env python3
"""DocAuto Claude Cloud Routine Trigger.

Triggers an Anthropic Claude Routine session when an automated run encounters
actionable errors or incomplete states (severity action or alert).
"""

import argparse
import glob
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

import common
from common import KST
import notify

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
DEFAULT_LOGS_DIR = REPO_ROOT / "logs"

MAX_FIRES_PER_DAY = 4
MAX_PAYLOAD_CHARS = 65536

# 지문에 넣지 않는 키. status는 노드 자신, 나머지는 payload의 node에 이미 들어간다.
SKIP_KEYS = ("status", "verified_by", "questions", "options")
DOCTORVILLE_PREFIX = "doctorville_"

# 보류 사유 중 파일에 남기는 것. 재시도성 1회차만 남긴다 — 그 기록이 "두 번째부터
# 발사" 규칙의 1회차 표식이기 때문이다. 나머지(이미 발사·상한)는 매 런 쌓이기만 한다.
PERSISTED_DEFERRALS = ("retryable_first_occurrence",)


def _account_of(key: str, value) -> str:
    """이 경로 세그먼트가 계정이면 계정명, 아니면 빈 문자열.

    per-account 결과 dict은 자기 안에 `account` 필드를 들고 있다(닥터빌·HMP·키메디·
    설문 공통). 키 이름과 그 필드를 대조하면 credentials.json 없이도 계정 세그먼트를
    가려낼 수 있다 — CI에서만 존재하는 파일에 지문 안정성을 걸지 않기 위해서다.
    `keymedi`처럼 모듈 이름이 키인 노드는 account가 키와 다르므로 task에 그대로 남는다.
    """
    name = key[len(DOCTORVILLE_PREFIX):] if key.startswith(DOCTORVILLE_PREFIX) else key
    if isinstance(value, dict) and value.get("account") == name:
        return name
    if key.startswith(DOCTORVILLE_PREFIX):
        return name
    return ""


def _extract_target(node: dict) -> str:
    """Extract semantic target identifier (seminarId, product, exception, etc.)."""
    if not isinstance(node, dict):
        return "_"
    if node.get("seminarId"):
        return str(node["seminarId"])
    if node.get("product"):
        return str(node["product"])
    if node.get("exc_type"):
        return str(node["exc_type"])
    if node.get("quiz_id"):
        return str(node["quiz_id"])
    return "_"


def compute_fingerprint(kst_date: str, script: str, task: str, status: str, target: str) -> str:
    """Generate deduplication fingerprint: {KST_DATE}/{script}/{task}/{status}/{target}."""
    s_script = script.strip() or "_"
    s_task = task.strip() or "_"
    s_status = status.strip() or "_"
    s_target = str(target).strip() or "_"
    return f"{kst_date}/{s_script}/{s_task}/{s_status}/{s_target}"


def collect_actionable_items(results_paths: list[Path]) -> list[dict]:
    """결과 JSON에서 severity가 action 이상인 노드를 항목으로 뽑는다.

    판정은 **그 노드 자신의 status**로만 한다(`notify._node_sev`). `severity_of`는
    하위 트리의 최대값이라, 자식 하나가 incomplete_bank면 정상(success)인 부모까지
    항목이 되어 payload에 같은 내용이 두 번 실리고 status=success짜리 지문이 생긴다.

    지문에 쓰는 task는 **계정 세그먼트와 리스트 인덱스를 뺀** 경로다. 인덱스를
    넣으면 같은 세미나가 다음 런에서 다른 자리에 오는 것만으로 지문이 바뀌어
    같은 문제로 세션이 또 뜬다. 사람이 읽을 위치는 path에 그대로 남긴다.
    """
    items = []
    for p in results_paths:
        if not p.exists():
            continue
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            print(f"[claude_trigger] 결과 파일 읽기 실패 ({p}): {e}", file=sys.stderr)
            continue

        script_guess = p.stem.replace("results-", "")

        def _walk(obj, path_parts: list, task_parts: list, account: str):
            if isinstance(obj, dict):
                if "status" in obj:
                    sev = notify._node_sev(obj)
                    if notify.SEVERITY_ORDER.get(sev, 0) >= notify.SEVERITY_ORDER["action"]:
                        items.append({
                            "script": script_guess,
                            "account": obj.get("account") or account or "",
                            "task": ".".join(task_parts) or "_",
                            "path": " > ".join(path_parts),
                            "status": obj.get("status", ""),
                            "target": _extract_target(obj),
                            "node": obj,
                        })
                for k, v in obj.items():
                    if k in SKIP_KEYS:
                        continue
                    acc = _account_of(k, v)
                    _walk(
                        v,
                        path_parts + [k],
                        task_parts if acc else task_parts + [k],
                        acc or account,
                    )
            elif isinstance(obj, list):
                for idx, elem in enumerate(obj):
                    head = path_parts[:-1] + [f"{path_parts[-1]}[{idx}]"] if path_parts else [f"[{idx}]"]
                    _walk(elem, head, task_parts, account)

        _walk(data, [script_guess], [], "")
    return items


def collect_recent_errors(log_dir: Path, kst_now: datetime, run_id: str) -> list[dict]:
    """Read errors-YYYY-MM.jsonl and collect errors for the current run."""
    err_file = log_dir / f"errors-{kst_now:%Y-%m}.jsonl"
    if not err_file.exists():
        return []
    matched = []
    kst_date = kst_now.strftime("%Y-%m-%d")
    try:
        with open(err_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except Exception:
                    continue
                gh_run = str(entry.get("gh_run") or "")
                ts = str(entry.get("ts") or "")
                if run_id and gh_run == run_id:
                    matched.append(entry)
                elif not run_id and ts.startswith(kst_date):
                    matched.append(entry)
    except Exception as e:
        print(f"[claude_trigger] 오류 로그 읽기 실패: {e}", file=sys.stderr)
    return matched


def read_trigger_history(log_dir: Path, kst_now: datetime) -> list[dict]:
    """Read claude-triggers-YYYY-MM.jsonl history."""
    hist_file = log_dir / f"claude-triggers-{kst_now:%Y-%m}.jsonl"
    if not hist_file.exists():
        return []
    history = []
    try:
        with open(hist_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    history.append(json.loads(line))
                except Exception:
                    pass
    except Exception as e:
        print(f"[claude_trigger] 트리거 이력 읽기 실패: {e}", file=sys.stderr)
    return history


def _fire_key(entry: dict) -> str:
    """한 번의 발사(=세션 1개)를 식별하는 값."""
    return str(entry.get("fire_id") or entry.get("run_id") or entry.get("ts") or "")


def evaluate_items(
    items: list[dict],
    history: list[dict],
    kst_date: str,
) -> tuple[list[dict], list[dict]]:
    """발사할 항목과 보류 항목을 가른다.

    상한은 **발사 횟수(세션 수)**로 센다. 한 번 발사에 새 지문을 모두 묶어 싣기
    때문에, 항목 수로 세면 항목 4개짜리 런 한 번에 그날 상한이 차 버린다.

    Returns: (발사 항목, 보류 항목)
    """
    today = [h for h in history if str(h.get("ts", "")).startswith(kst_date)]
    fired_today = [h for h in today if h.get("outcome") == "fired"]
    fires_today = len({_fire_key(h) for h in fired_today})
    fired_fps_today = {h.get("fp") for h in fired_today}
    seen_fps_today = {h.get("fp") for h in today}

    to_fire: list[dict] = []
    deferred: list[dict] = []
    chosen_fps_this_run = set()
    cap_reached = fires_today >= MAX_FIRES_PER_DAY

    for it in items:
        fp = compute_fingerprint(kst_date, it["script"], it["task"], it["status"], it["target"])
        it["fp"] = fp

        if fp in chosen_fps_this_run:
            continue

        if fp in fired_fps_today:
            deferred.append({**it, "reason": "already_fired_today"})
            continue

        if cap_reached:
            deferred.append({**it, "reason": "daily_cap_reached"})
            continue

        # 재시도성(네트워크·타임아웃) 실패는 같은 날 재발했을 때만 세션을 띄운다.
        msg = str(it.get("node", {}).get("message", ""))
        if it.get("status") == "failed" and common.is_retryable_error(msg):
            if fp not in seen_fps_today:
                deferred.append({**it, "reason": "retryable_first_occurrence"})
                continue

        to_fire.append(it)
        chosen_fps_this_run.add(fp)

    return to_fire, deferred


def build_payload(
    workflow: str,
    run_url: str,
    kst_str: str,
    items: list[dict],
    errors: list[dict],
    failed_steps: list[str],
    screenshots: list[str],
) -> str:
    """Build routine JSON payload under 65,536 characters."""
    payload = {
        "workflow": workflow,
        "run_url": run_url,
        "kst": kst_str,
        "items": [
            {
                "fp": it.get("fp", ""),
                "account": it.get("account", ""),
                "path": it.get("path", ""),
                "node": it.get("node", {}),
            }
            for it in items
        ],
        "errors": errors,
        "failed_steps": failed_steps,
        "screenshots": screenshots,
    }

    text = json.dumps(payload, ensure_ascii=False)
    if len(text) <= MAX_PAYLOAD_CHARS:
        return text

    # Truncation step 1: Shorten tracebacks in errors
    for err in payload["errors"]:
        if "traceback" in err and len(err["traceback"]) > 500:
            err["traceback"] = "..." + err["traceback"][-500:]

    text = json.dumps(payload, ensure_ascii=False)
    if len(text) <= MAX_PAYLOAD_CHARS:
        return text

    # Truncation step 2: Strip node details to essentials
    for it in payload["items"]:
        node = it.get("node", {})
        if isinstance(node, dict):
            it["node"] = {
                "status": node.get("status"),
                "message": (node.get("message") or "")[:200],
                "verified_by": node.get("verified_by"),
            }

    text = json.dumps(payload, ensure_ascii=False)
    if len(text) <= MAX_PAYLOAD_CHARS:
        return text

    # Truncation step 3: 건수를 줄인다. 문자열을 그대로 자르면 JSON이 깨져
    # 세션이 payload를 파싱하지 못하므로, 자르는 것은 내용이지 결과 문자열이 아니다.
    payload["errors"] = payload["errors"][-3:]
    payload["items"] = payload["items"][:10]
    payload["truncated"] = True
    return json.dumps(payload, ensure_ascii=False)


def fire_routine(url: str, token: str, payload_text: str) -> tuple[bool, str]:
    """Send routine fire API request."""
    if not url or not token:
        return False, "CLAUDE_ROUTINE_URL or CLAUDE_ROUTINE_TOKEN is not set"

    body = json.dumps({"text": payload_text}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "anthropic-beta": "experimental-cc-routine-2026-04-01",
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            resp_data = json.loads(resp.read().decode("utf-8", "replace"))
            session_url = resp_data.get("claude_code_session_url") or resp_data.get("session_url", "")
            return True, session_url
    except urllib.error.HTTPError as e:
        body_err = e.read().decode("utf-8", "replace")[:300]
        return False, f"HTTPError {e.code}: {body_err}"
    except Exception as e:
        return False, str(e)


def record_trigger_event(
    log_dir: Path,
    kst_now: datetime,
    fp: str,
    run_id: str,
    outcome: str,
    session_url: str = "",
    reason: str = "",
    fire_id: str = "",
) -> None:
    """Append trigger event to logs/claude-triggers-YYYY-MM.jsonl."""
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        hist_file = log_dir / f"claude-triggers-{kst_now:%Y-%m}.jsonl"
        entry = {
            "ts": kst_now.isoformat(timespec="seconds"),
            "fp": fp,
            "run_id": run_id,
            "outcome": outcome,
            "session_url": session_url,
        }
        if fire_id:
            entry["fire_id"] = fire_id
        if reason:
            entry["reason"] = reason
        with open(hist_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"[claude_trigger] 로그 기록 실패: {e}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description="Claude Cloud Routine Trigger for DocAuto")
    parser.add_argument("--results", nargs="*", default=[], help="결과 JSON 파일 경로들")
    parser.add_argument("--outcome", nargs="*", default=[], help="스텝 상태 목록 (step=outcome)")
    parser.add_argument("--dry-run", action="store_true", help="API 호출 없이 페이로드만 출력")
    parser.add_argument("--log-dir", default=str(DEFAULT_LOGS_DIR), help="로그 디렉토리")
    parser.add_argument("--workflow", default=os.environ.get("GITHUB_WORKFLOW", ""), help="워크플로우 이름")
    parser.add_argument("--run-id", default=os.environ.get("GITHUB_RUN_ID", ""), help="GitHub Run ID")
    parser.add_argument("--server-url", default=os.environ.get("GITHUB_SERVER_URL", "https://github.com"))
    parser.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY", "econ-blog/DocAuto"))
    args = parser.parse_args()

    kst_now = datetime.now(KST)
    kst_date = kst_now.strftime("%Y-%m-%d")
    log_dir = Path(args.log_dir)

    # Parse outcomes
    failed_steps = []
    for o in args.outcome:
        if "=" in o:
            step, outcome = o.split("=", 1)
            if outcome.strip().lower() in ("failure", "timed_out"):
                failed_steps.append(step.strip())

    # Resolve result paths (support wildcard)
    result_paths = []
    for r in args.results:
        matched = glob.glob(r)
        if matched:
            result_paths.extend(Path(p) for p in matched)
        else:
            result_paths.append(Path(r))

    # Collect items
    items = collect_actionable_items(result_paths)
    errors = collect_recent_errors(log_dir, kst_now, args.run_id)

    # Step failure with no results JSON
    if failed_steps and not items:
        for s in failed_steps:
            items.append({
                "script": "workflow",
                "account": "",
                "task": f"step.{s}",
                "path": f"step:{s}",
                "status": "failed",
                "target": "step_failure",
                "node": {"status": "failed", "message": f"워크플로우 스텝 '{s}' 실패 (결과 JSON 없음)"},
            })

    if not items and not errors and not failed_steps:
        print("[claude_trigger] 트리거 대상 없음 (모두 정상이거나 quiet).")
        return

    history = read_trigger_history(log_dir, kst_now)
    to_fire, deferred = evaluate_items(items, history, kst_date)

    # 보류 기록 — 재시도성 1회차만 파일에 남긴다(PERSISTED_DEFERRALS 주석 참조).
    for it in deferred:
        reason = it.get("reason", "")
        if reason in PERSISTED_DEFERRALS:
            record_trigger_event(
                log_dir, kst_now, it.get("fp", ""), args.run_id, "deferred", reason=reason
            )
        print(f"[claude_trigger] 항목 보류 ({reason}): {it.get('fp')}")

    if not to_fire:
        print("[claude_trigger] 발사할 신규 항목 없음.")
        return

    # 실패 스크린샷만 싣는다. 표 PNG(daily-table-*, seminar-table-*)는 진단과 무관하다.
    screenshots = [
        p.name for p in sorted((SCRIPT_DIR / "logs").glob("*.png"))
        if not p.name.startswith(("daily-table-", "seminar-table-"))
    ]

    run_url = f"{args.server_url}/{args.repository}/actions/runs/{args.run_id}" if args.run_id else ""
    payload_text = build_payload(
        args.workflow, run_url, kst_now.isoformat(timespec="seconds"),
        to_fire, errors, failed_steps, screenshots
    )

    if args.dry_run:
        print("[claude_trigger] [DRY RUN] 생성된 페이로드:")
        print(payload_text)
        print(f"[claude_trigger] [DRY RUN] 대상 지문 ({len(to_fire)}개): {[it['fp'] for it in to_fire]}")
        return

    routine_url = os.environ.get("CLAUDE_ROUTINE_URL", "")
    routine_token = os.environ.get("CLAUDE_ROUTINE_TOKEN", "")
    if not routine_url or not routine_token:
        # routine을 아직 안 만든 상태다. 설정 부재는 발사 실패가 아니므로 이력에
        # 남기지 않는다 — 남기면 설정 전까지 커밋되는 로그에 매 런 쌓이기만 한다.
        print(f"[claude_trigger] routine 미설정(secrets 없음) — 발사 생략. "
              f"대상 지문 {len(to_fire)}개: {[it['fp'] for it in to_fire]}")
        return

    # 이번 발사(=세션 1개)의 식별자. 상한은 이 값의 개수로 센다.
    fire_id = f"{args.run_id or 'local'}:{kst_now:%H%M%S}"

    ok, res = fire_routine(routine_url, routine_token, payload_text)
    if ok:
        print(f"[claude_trigger] 클라우드 루틴 발사 성공: {res}")
        for it in to_fire:
            record_trigger_event(log_dir, kst_now, it["fp"], args.run_id, "fired",
                                 session_url=res, fire_id=fire_id)
    else:
        print(f"[claude_trigger] 클라우드 루틴 발사 실패: {res}", file=sys.stderr)
        for it in to_fire:
            record_trigger_event(log_dir, kst_now, it["fp"], args.run_id, "http_error",
                                 reason=res, fire_id=fire_id)


if __name__ == "__main__":
    main()
