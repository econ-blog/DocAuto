#!/usr/bin/env python3
"""세미나 블록 실행 결과를 표 1장으로 렌더해 텔레그램으로 보낸다.

seminar_block 워크플로우의 마지막 스텝에서 실행된다. 신청·입장·설문 세 단계가
각자 남긴 로그(logs/seminar-YYYY-MM-DD.json)와 신청 이력(seminar_applied.json)을
합쳐, 그날 예정된 세미나를 행으로 하는 표를 만든다.

용법:
    python3 scripts/seminar_report.py
    python3 scripts/seminar_report.py --no-telegram      # 콘솔에만 출력
    python3 scripts/seminar_report.py --date 2026-08-27
"""

import argparse
import json
import sys
from pathlib import Path

import common
import runlog

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_APPLIED = SCRIPT_DIR.parent / "seminar_applied.json"


def build_report(date_str: str, applied_path: Path, accounts: list[str] = None):
    applied = common.read_json(applied_path, default={}) if Path(applied_path).exists() else {}
    if not isinstance(applied, dict):
        applied = {}
    return runlog.seminar_table(date_str, applied=applied, accounts=accounts)


def main():
    parser = argparse.ArgumentParser(description="세미나 블록 결과 표 전송")
    parser.add_argument("--date", default=None, help="대상 날짜 (기본: 오늘 KST)")
    parser.add_argument("--applied-file", default=str(DEFAULT_APPLIED), help="seminar_applied.json 경로")
    parser.add_argument(
        "--credentials",
        default=str(SCRIPT_DIR.parent / "credentials.json"),
        help="credentials.json 경로",
    )
    parser.add_argument("--no-telegram", action="store_true", help="전송 없이 표만 출력")
    args = parser.parse_args()

    date_str = args.date or runlog.today_str()
    headers, rows = build_report(date_str, Path(args.applied_file))

    print(runlog.render_text_table(headers, rows) if rows else "(표에 넣을 세미나 없음)")

    if not rows:
        result = {"status": "no_target", "message": f"{date_str} 예정 세미나 없음."}
    elif args.no_telegram:
        result = {"status": "skipped", "message": "--no-telegram", "rows": len(rows)}
    else:
        result = runlog.send_table(
            f"📅 {date_str} 세미나 블록",
            headers,
            rows,
            png_name=f"seminar-table-{date_str}.png",
            credentials_path=args.credentials,
        )
        result["rows"] = len(rows)

    # 오래된 로그 정리는 표를 다 만든 뒤에 한다.
    runlog.prune(runlog.KIND_SEMINAR)

    print(json.dumps(result, ensure_ascii=False))
    # 표 전송 실패로 블록 전체를 실패시키지는 않는다 — 신청·입장·설문은 이미 끝났다.
    sys.exit(0)


if __name__ == "__main__":
    main()
