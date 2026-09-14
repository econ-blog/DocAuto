"""족보 파일에 canonical 중복 키가 남아 있지 않은지 검사한다.

`seminar_survey.lookup_answer`는 ① 정규화 키 완전 일치 → ② canonical 키 일치
(공백·대소문자·구두점·안내문구 무시) 순으로 대조한다. 그래서 표기만 다른 키가
둘로 갈려 있어도 런타임은 하나로 본다 — 파일만 불어난다.

실제로 그렇게 불어난 키가 8건 쌓여 있었다(`(non-dipper)` 앞 공백, `30 mg`/`30mg`,
`(복수 선택 가능)` 유무 등). 파일 상태를 런타임 판정과 일치시켜 두면, 사람이
족보를 열었을 때 "같은 문항이 왜 둘이지"를 다시 묻지 않는다.

새로 깔리는 키는 `add_missing_to_bank`가 canonical로 거르므로 이 검사는
과거 잔재를 다시 들이지 않는 회귀 방지용이다.
"""

import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import bank_pending  # noqa: E402,F401  (playwright 없이도 임포트되게 한다)
import seminar_survey as survey  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

# doctorville은 문항 단위 canonical 정규화를 쓰지 않는다(제품명만 정규화한다).
# 따라서 이 규칙은 seminar_survey가 읽는 세 족보에만 적용된다.
SURVEY_BANKS = (
    "survey_quiz_answers.json",
    "survey_text_answers.json",
    "survey_answers_legacy.json",
)


def _collisions(bank: dict) -> dict:
    groups = defaultdict(list)
    for key in bank:
        ck = survey.canonical_question(key)
        if ck:
            groups[ck].append(key)
    return {ck: keys for ck, keys in groups.items() if len(keys) > 1}


def test_survey_banks_have_no_canonical_duplicates():
    for filename in SURVEY_BANKS:
        bank = json.loads((ROOT / filename).read_text(encoding="utf-8"))
        dupes = _collisions(bank)
        assert not dupes, (
            f"{filename}: 표기만 다른 중복 키 {len(dupes)}쌍 — 하나만 남겨라.\n"
            + "\n".join(f"  {keys}" for keys in dupes.values())
        )


def test_collision_detector_catches_spacing_only_difference():
    """검사 자체가 동작하는지 — 공백 하나 차이를 실제로 잡아내야 한다."""
    bank = {"어떤 경우 입니까?": "답", "어떤 경우입니까?": "답"}
    assert len(_collisions(bank)) == 1
