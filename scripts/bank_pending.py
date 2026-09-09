#!/usr/bin/env python3
"""문제은행·설문 족보에서 아직 답이 안 채워진 항목만 뽑아 보여준다.

    python3 scripts/bank_pending.py            # 전부
    python3 scripts/bank_pending.py --bank text
    python3 scripts/bank_pending.py --json     # 다른 도구에 물릴 때

"미기입" 판정은 각 모듈이 실제 제출에 쓰는 함수를 그대로 불러 쓴다
(`doctorville.coerce_bank_answer`, `seminar_survey._coerce_answer`). 여기서
판정 규칙을 다시 구현하면 스크립트만 "다 찼다"고 하고 런은 계속 막히는 어긋남이
생긴다 — 두 곳의 규칙이 실제로 다르다(닥터빌은 보기 한 줄만 남아야 정답,
설문은 복수 선택이라 여러 줄도 정답).

종료 코드: 미기입이 있으면 1, 없으면 0.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import common  # noqa: E402
import doctorville  # noqa: E402
import seminar_survey as survey  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

# 이름 → (파일, 중첩 여부, 미기입 판정 함수)
BANKS = {
    "quiz": ("quiz_answers.json", True, doctorville.coerce_bank_answer),
    "survey_quiz": ("survey_quiz_answers.json", False, survey._coerce_answer),
    "survey_text": ("survey_text_answers.json", False, survey._coerce_answer),
}


def pending(name: str) -> list[dict]:
    """족보 하나에서 미기입 항목을 뽑는다."""
    filename, nested, coerce = BANKS[name]
    bank = common.read_json(ROOT / filename, default={})
    rows = []

    def _check(question, value, product=None):
        if coerce(value) is not None:
            return
        # 남아 있는 보기 목록(표시줄 제외). 주관식은 빈 목록이다.
        options = [
            str(v).strip() for v in value
            if isinstance(value, list) and str(v).strip() != common.ANSWER_PLACEHOLDER_MARKER
        ] if isinstance(value, list) else []
        rows.append({
            "bank": name, "file": filename, "product": product,
            "question": question, "options": options,
        })

    for key, value in bank.items():
        if nested:
            for question, v in (value or {}).items():
                _check(question, v, product=key)
        else:
            _check(key, value)
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="족보의 미기입 항목 조회")
    parser.add_argument("--bank", choices=sorted(BANKS), action="append",
                        help="조회할 족보(반복 지정 가능). 기본은 전부")
    parser.add_argument("--json", action="store_true", help="JSON으로 출력")
    args = parser.parse_args()

    rows = [r for name in (args.bank or sorted(BANKS)) for r in pending(name)]

    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 1 if rows else 0

    if not rows:
        print("미기입 없음")
        return 0

    for name in sorted({r["bank"] for r in rows}):
        group = [r for r in rows if r["bank"] == name]
        print(f"\n[{name}] {BANKS[name][0]} — {len(group)}건")
        for r in group:
            head = f"  - {r['product']} / {r['question']}" if r["product"] else f"  - {r['question']}"
            print(head)
            for i, o in enumerate(r["options"], 1):
                print(f"      {i}. {o}")
            if not r["options"]:
                print("      (주관식 — 답변 문장을 직접 적는다)")
    print(f"\n합계 {len(rows)}건")
    return 1


if __name__ == "__main__":
    sys.exit(main())
