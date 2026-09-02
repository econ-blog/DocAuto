#!/usr/bin/env python3
"""
일일 자동화 통합 실행 스크립트.

실행 순서:
  1. 닥터빌 (계정별 출석+퀴즈+세미나)
  2. 키메디 출석
  3. HMP (캡슐+룰렛+댓글+글쓰기)
  4. 내일 닥터빌 퀴즈 사전 점검
  5. 세미나 신청 이력(seminar_applied.json)에서 날짜 지난 항목 정리
  6. 결과를 notify 게이트를 거쳐 텔레그램 bot으로 전송

용법:
    python3 scripts/daily_runner.py
    python3 scripts/daily_runner.py --headed
    python3 scripts/daily_runner.py --no-telegram
    python3 scripts/daily_runner.py --notify-level actionable
"""

import argparse
import json
import os
import signal
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import common
import doctorville
import notify
import runlog

SCRIPT_DIR = Path(__file__).resolve().parent
PYTHON = sys.executable


def _extract_json(text: str) -> dict | None:
    """텍스트에서 가장 마지막 또는 유효한 JSON 블록을 추출해 파싱한다."""
    text = text.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 역방향 줄 단위 단일 라인 JSON 탐색
    for line in reversed(text.splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                pass

    # 멀티라인 JSON 블록({ ... }) 탐색
    first_brace = text.find("{")
    last_brace = text.rfind("}")
    if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
        candidate = text[first_brace:last_brace+1]
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass

    return None


def run_script(script_name: str, extra_args: list[str] = None, timeout: int = 120) -> dict:
    """서브프로세스로 스크립트를 실행하고 stdout JSON을 파싱해 반환한다."""
    script_path = SCRIPT_DIR / script_name
    cmd = [PYTHON, str(script_path)] + (extra_args or [])
    kwargs = {}
    if os.name != "nt":
        kwargs["start_new_session"] = True

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            **kwargs,
        )
        stdout = proc.stdout.strip()
        parsed = _extract_json(stdout)
        if parsed is not None:
            return parsed
        common.log_error(
            script_name, f"JSON 파싱 실패. stdout: {stdout[:1000]}",
            task=" ".join(extra_args or []),
            extra={"stderr": proc.stderr[-2000:], "returncode": proc.returncode},
        )
        return {
            "status": "failed",
            "message": f"JSON 파싱 실패. stdout: {stdout[:300]}",
            "stderr": proc.stderr[:200],
        }
    except subprocess.TimeoutExpired as e:
        if os.name != "nt" and hasattr(e, "pid") and e.pid:
            try:
                os.killpg(e.pid, signal.SIGKILL)
            except Exception:
                pass
        common.log_error(script_name, e, task=" ".join(extra_args or []), status="timeout",
                         extra={"timeout_sec": timeout})
        return {"status": "failed", "message": f"{script_name} 타임아웃 ({timeout}초)."}
    except Exception as e:
        common.log_error(script_name, e, task=" ".join(extra_args or []))
        return {"status": "failed", "message": f"실행 예외: {e}"}


FAIL_STATUSES = {"failed", "unverified", "blocked"}


def evaluate_exit_code(results: dict) -> int:
    """results 내에 failed, unverified, blocked 상태가 있으면 1, 없으면 0을 반환한다."""
    def _is_failed(val):
        if isinstance(val, dict):
            if val.get("status") in FAIL_STATUSES:
                return True
            return any(_is_failed(v) for v in val.values())
        elif isinstance(val, list):
            return any(_is_failed(item) for item in val)
        return False

    return 1 if _is_failed(results) else 0


def build_execution_plan(creds: dict) -> dict:
    """credentials dict에서 실행할 스크립트 플랜을 동적으로 생성한다."""
    plan = {}
    for acc in common.list_accounts(creds, site="doctorville"):
        plan[f"doctorville_{acc}"] = {
            "script": "doctorville.py",
            "args": ["--account", acc],
            "timeout": 240,
        }
    plan["keymedi"] = {
        "script": "keymedi.py",
        "args": [],
    }
    plan["hmp"] = {
        "script": "hmp.py",
        "args": [],
    }
    plan["precheck_quiz"] = {
        "script": "doctorville.py",
        "args": ["--task", "precheck_quiz"],
    }
    return plan


def send_daily_table(date_str: str, credentials_path: Path) -> dict:
    """그날의 daily 로그 전체를 표 1장으로 렌더해 전송한다."""
    try:
        headers, rows = runlog.daily_table(date_str)
        return runlog.send_table(
            f"📋 {date_str} 일일 자동화",
            headers,
            rows,
            png_name=f"daily-table-{date_str}.png",
            credentials_path=str(credentials_path),
        )
    except Exception as e:
        return {"status": "failed", "message": f"표 전송 예외: {e}"}


def main():
    parser = argparse.ArgumentParser(description="일일 자동화 통합 실행")
    parser.add_argument("--headed", action="store_true", help="브라우저 창 표시 (디버깅용)")
    parser.add_argument("--no-telegram", action="store_true", help="텔레그램 전송 건너뜀")
    parser.add_argument(
        "--credentials",
        default=str(SCRIPT_DIR.parent / "credentials.json"),
        help="credentials.json 경로",
    )
    parser.add_argument(
        "--notify-level",
        choices=["all", "actionable"],
        default=None,
        help="텔레그램 알림 레벨 (all, actionable)",
    )
    args = parser.parse_args()

    notify_level = notify.resolve_level(args.notify_level or os.environ.get("NOTIFY_LEVEL"))

    credentials_path = Path(args.credentials)
    creds = {}
    if credentials_path.exists():
        try:
            creds = common.read_credentials(credentials_path)
        except Exception as e:
            print(f"[daily_runner] credentials 로드 실패: {e}", file=sys.stderr)

    extra = []
    if args.headed:
        extra.append("--headed")
    if args.credentials:
        extra += ["--credentials", args.credentials]

    date_str = datetime.now(common.KST).strftime("%Y-%m-%d")
    plan = build_execution_plan(creds)
    results = {}

    total_steps = len(plan)
    for idx, (step_name, task_cfg) in enumerate(plan.items(), 1):
        script = task_cfg["script"]
        script_args = task_cfg.get("args", []) + extra
        timeout = task_cfg.get("timeout", 120)
        print(f"[{idx}/{total_steps}] {step_name}...")
        results[step_name] = run_script(script, script_args, timeout=timeout)
        indent = 2 if "doctorville" in step_name else None
        print(json.dumps(results[step_name], ensure_ascii=False, indent=indent))

    # 세미나 신청 이력 정리 — 브라우저가 필요 없는 파일 작업이라 서브프로세스로
    # 돌리지 않는다. 30분마다 도는 seminar_block이 아니라 여기서 하루 1회만 한다.
    results["seminar_applied_prune"] = doctorville.prune_applied_file()
    print(json.dumps(results["seminar_applied_prune"], ensure_ascii=False))

    print("\n=== 최종 결과 ===")
    print(json.dumps(results, ensure_ascii=False, indent=2))

    # 실행 로그 적재 — 이 런을 run{N} 행으로 append 하고, 오래된 로그를 지운다.
    # 텔레그램 전송보다 먼저 해야 전송이 실패해도 기록은 남는다.
    try:
        runlog.append_daily_run(runlog.daily_cells(results, creds), date_str=date_str)
        runlog.prune(runlog.KIND_DAILY)
    except Exception as e:
        print(f"[daily_runner] 실행 로그 적재 실패: {e}", file=sys.stderr)

    if not args.no_telegram:
        if notify.should_send(results, notify_level):
            msg = notify.build_message(results, notify_level, date_str)
            print(f"\n[telegram] 전송 중... (mode: {notify_level})")
            ok = notify.send_telegram(msg, credentials_path=str(credentials_path))
            print(f"[telegram] {'성공' if ok else '실패'}")
        else:
            print(f"\n[telegram] 메시지 억제됨 ({notify_level} 모드)")

        # 표는 NOTIFY_LEVEL과 무관하게 항상 보낸다. 하루 실행 현황 전체를 한 장으로
        # 보여주는 게 목적이라, 조용한 런이라고 빼면 표가 비어 보인다.
        table_result = send_daily_table(date_str, credentials_path)
        print(f"[telegram] 표: {table_result.get('status')}")
    else:
        print("\n[telegram] 건너뜀 (--no-telegram)")

    failed = evaluate_exit_code(results) != 0
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
