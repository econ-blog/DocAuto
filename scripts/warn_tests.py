#!/usr/bin/env python3
"""pytest 게이트 실패를 **차단이 아니라 경고**로 바꾼다.

배경: 2026-09-16 00:15 KST daily 런이 테스트 1건(하드코딩된 날짜로 만든
타임밤) 때문에 통째로 중단돼 출석·퀴즈·HMP가 하나도 돌지 않았다. 테스트
버그가 출석 연속일을 끊는 구조였다. 사용자 지시로 게이트를 비차단으로
바꾸고, 대신 실패 사실을 텔레그램으로 반드시 알린다.

주의: 이 경로는 런을 초록으로 남긴다. 실패를 아는 수단은 이 메시지뿐이므로
전송 실패는 stderr + Actions 경고 주석으로 이중 표시한다.
"""

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import notify  # noqa: E402

# 텔레그램 한 건에 담을 pytest 출력 꼬리. 실패 요약(short test summary info)은
# 항상 마지막에 오므로 꼬리만 있으면 어느 테스트가 깨졌는지 알 수 있다.
TAIL_LINES = 25


def summarize(output_path: str, tail_lines: int = TAIL_LINES) -> str:
    p = Path(output_path)
    if not p.exists():
        return "(pytest 출력 파일 없음 — 테스트 실행 자체가 실패했을 수 있다)"
    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    # 진행 표시(....F...) 줄은 정보가 없어 꼬리 예산만 먹는다.
    meaningful = [ln for ln in lines if ln.strip()]
    return "\n".join(meaningful[-tail_lines:]) or "(출력 없음)"


def build_message(workflow: str, output_path: str, run_url: str = "") -> str:
    parts = [
        f"⚠️ 단위 테스트 실패 ({workflow})",
        "",
        "자동화는 계속 실행됐다. 테스트가 깨진 채 도는 중이니 확인이 필요하다.",
        "",
        summarize(output_path),
    ]
    if run_url:
        parts += ["", run_url]
    return "\n".join(parts)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", default="pytest-out.txt", help="pytest 출력 파일")
    ap.add_argument("--workflow", default="", help="워크플로우 이름")
    ap.add_argument("--no-telegram", action="store_true")
    args = ap.parse_args(argv)

    msg = build_message(args.workflow, args.output, os.environ.get("RUN_URL", ""))
    print(f"::warning title=단위 테스트 실패::{args.workflow} 워크플로우에서 pytest가 실패했다 (자동화는 계속 실행됨)")
    print(msg)

    if args.no_telegram:
        return 0
    if not notify.send_telegram(msg):
        print("[warn_tests] 텔레그램 경고 전송 실패 — 이 실패를 알릴 다른 경로가 없다", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
