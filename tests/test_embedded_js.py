"""브라우저에 넘기는 JS 조각이 파이썬 이스케이프로 깨지지 않는지 검사한다.

2026-08-28: evaluate에 넘기는 JS를 일반 삼중따옴표 문자열로 써서 `\n`이
파이썬 단계에서 진짜 줄바꿈이 됐다. JS 문자열 리터럴이 끊겨
`SyntaxError: Invalid or unexpected token`으로 두 계정 세미나 신청이 전부 죽었다.
브라우저 없이도 잡히는 검사라 단위 테스트로 남긴다.
"""

import re
from pathlib import Path

import pytest

import doctorville

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def _unescaped_quote_count(line: str, quote: str) -> int:
    """이스케이프되지 않은 따옴표 개수. 정규식 리터럴 안의 것은 세지 않는다."""
    count, i = 0, 0
    while i < len(line):
        ch = line[i]
        if ch == "\\":
            i += 2
            continue
        if ch == quote:
            count += 1
        i += 1
    return count


def assert_no_broken_string_literals(js: str, label: str):
    """JS의 각 줄에서 따옴표 짝이 맞아야 한다.

    파이썬이 `\\n`을 줄바꿈으로 바꿔버리면 `split('` 에서 줄이 끊겨 홀수가 된다.
    """
    for lineno, line in enumerate(js.splitlines(), 1):
        code = line.split("//")[0]
        for quote in ("'", '"'):
            assert _unescaped_quote_count(code, quote) % 2 == 0, (
                f"{label} {lineno}번째 줄: {quote} 짝이 안 맞음 — "
                f"파이썬 이스케이프로 JS 문자열이 끊겼을 수 있다: {line!r}"
            )


def test_seminar_list_js_has_no_broken_string_literals():
    assert_no_broken_string_literals(doctorville.SEMINAR_LIST_JS, "SEMINAR_LIST_JS")


def test_seminar_list_js_keeps_newline_as_a_two_char_escape():
    """split('\\n')의 \\n은 JS가 해석해야 한다 — 파이썬이 먹으면 안 된다."""
    assert "\\n" in doctorville.SEMINAR_LIST_JS
    assert "split('\n" not in doctorville.SEMINAR_LIST_JS


def test_seminar_list_js_scopes_lookup_to_the_list_anchor():
    """document 전역 조회는 사이트 헤더를 제목으로 집어온다(2026-08-28)."""
    js = doctorville.SEMINAR_LIST_JS
    assert "aEl.querySelector" in js
    assert "document.querySelector('.tit'" not in js


EVALUATE_BLOCK = re.compile(r'evaluate\(\s*(r?)"""(.*?)"""', re.DOTALL)


@pytest.mark.parametrize("path", sorted(SCRIPTS.glob("*.py")), ids=lambda p: p.name)
def test_inline_evaluate_blocks_have_no_broken_string_literals(path):
    """스크립트에 인라인으로 박힌 evaluate JS도 같은 검사를 받는다."""
    source = path.read_text(encoding="utf-8")
    for idx, (raw_prefix, block) in enumerate(EVALUATE_BLOCK.findall(source)):
        # 일반 문자열이면 파이썬이 이미 이스케이프를 먹은 뒤의 모습으로 검사해야 한다.
        js = block if raw_prefix else block.encode().decode("unicode_escape")
        assert_no_broken_string_literals(js, f"{path.name} evaluate #{idx + 1}")
