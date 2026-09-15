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
    """Traverse result JSONs and extract items with severity >= action."""
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

        def _walk(obj, prefix: str, current_acc: str):
            if isinstance(obj, dict):
                # Status node check
                if "status" in obj:
                    sev = notify.severity_of(obj)
                    if notify.SEVERITY_ORDER.get(sev, 0) >= notify.SEVERITY_ORDER["action"]:
                        account = obj.get("account") or current_acc or ""
                        task_name = prefix.split(" > ")[-1] if prefix else ""
                        target = _extract_target(obj)
                        items.append({
                            "script": script_guess,
                            "account": account,
                            "task": task_name,
                            "path": prefix,
                            "status": obj.get("status", ""),
                            "target": target,
                            "node": obj,
                        })
                for k, v in obj.items():
                    if k in ("status", "verified_by", "questions", "options"):
                        continue
                    new_acc = current_acc
                    if k in ("bjh7790", "wonju") or k.startswith("doctorville_"):
                        new_acc = k.replace("doctorville_", "")
                    sub_prefix = f"{prefix} > {k}" if prefix else k
                    _walk(v, sub_prefix, new_acc)
            elif isinstance(obj, list):
                for idx, elem in enumerate(obj):
                    sub_prefix = f"{prefix}[{idx}]"
                    _walk(elem, sub_prefix, current_acc)

        _walk(data, prefix=script_guess, current_acc="")
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


def evaluate_items(
    items: list[dict],
    history: list[dict],
    kst_date: str,
) -> tuple[list[dict], list[dict]]:
    """Determine which items should fire vs be deferred based on deduplication rules.

    Returns: (items_to_fire, items_deferred)
    """
    today_fired_count = sum(
        1 for h in history
        if str(h.get("ts", "")).startswith(kst_date) and h.get("outcome") == "fired"
    )

    fired_fps_today = {
        h.get("fp") for h in history
        if str(h.get("ts", "")).startswith(kst_date) and h.get("outcome") == "fired"
    }

    seen_fps_today = {
        h.get("fp") for h in history
        if str(h.get("ts", "")).startswith(kst_date)
    }

    to_fire = []
    deferred = []

    # Track fingerprints already chosen in this evaluation run to avoid internal duplicates
    chosen_fps_this_run = set()

    for it in items:
        fp = compute_fingerprint(kst_date, it["script"], it["task"], it["status"], it["target"])
        it["fp"] = fp

        if fp in chosen_fps_this_run:
            continue

        # Rule 1: Already fired today -> defer
        if fp in fired_fps_today:
            deferred.append({**it, "reason": "already_fired_today"})
            continue

        # Rule 2: Daily cap reached -> defer
        if today_fired_count + len(to_fire) >= MAX_FIRES_PER_DAY:
            deferred.append({**it, "reason": "daily_cap_reached"})
            continue

        # Rule 3: Retryable error check -> defer on first occurrence today
        msg = str(it.get("node", {}).get("message", ""))
        is_retryable = common.is_retryable_error(msg)
        if it.get("status") == "failed" and is_retryable:
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

    # Truncation step 3: Limit counts
    payload["errors"] = payload["errors"][-3:]
    payload["items"] = payload["items"][:10]
    return json.dumps(payload, ensure_ascii=False)[:MAX_PAYLOAD_CHARS]


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
                "script": s,
                "account": "",
                "task": s,
                "path": s,
                "status": "failed",
                "target": "step_failure",
                "node": {"status": "failed", "message": f"Step '{s}' failed in workflow"},
            })

    if not items and not errors and not failed_steps:
        print("[claude_trigger] 트리거 대상 없음 (모두 정상이거나 quiet).")
        return

    history = read_trigger_history(log_dir, kst_now)
    to_fire, deferred = evaluate_items(items, history, kst_date)

    # Record deferred items
    for it in deferred:
        record_trigger_event(
            log_dir, kst_now, it.get("fp", ""), args.run_id, "deferred", reason=it.get("reason", "")
        )
        print(f"[claude_trigger] 항목 보류 ({it.get('reason')}): {it.get('fp')}")

    if not to_fire:
        print("[claude_trigger] 발사할 신규 항목 없음.")
        return

    # Find screenshots
    screenshots = []
    for p in (SCRIPT_DIR / "logs").glob("*.png"):
        screenshots.append(p.name)

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

    ok, res = fire_routine(routine_url, routine_token, payload_text)
    if ok:
        print(f"[claude_trigger] 클라우드 루틴 발사 성공: {res}")
        for it in to_fire:
            record_trigger_event(log_dir, kst_now, it["fp"], args.run_id, "fired", session_url=res)
    else:
        print(f"[claude_trigger] 클라우드 루틴 발사 실패: {res}", file=sys.stderr)
        for it in to_fire:
            record_trigger_event(log_dir, kst_now, it["fp"], args.run_id, "http_error", reason=res)


if __name__ == "__main__":
    main()
