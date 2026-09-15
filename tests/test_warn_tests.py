"""테스트 게이트가 비차단 + 경고로 동작하는지 고정한다.

2026-09-16 00:15 daily 런이 테스트 1건 때문에 통째로 죽었다. 게이트를
비차단으로 바꾼 이상, 실패를 알리는 유일한 수단은 텔레그램 경고다 —
그 배선이 끊기면 아무도 모른다.
"""

from pathlib import Path

import warn_tests

REPO = Path(__file__).resolve().parent.parent


def _workflow(name):
    return (REPO / f".github/workflows/{name}").read_text("utf-8")


def test_gates_are_non_blocking_and_warn():
    for name in ("daily.yml", "seminar_block.yml"):
        content = _workflow(name)
        gate = content.split("- name: 단위 테스트 실행")[1].split("- name: credentials.json")[0]
        assert "id: tests" in gate
        assert "continue-on-error: true" in gate
        # tee 없이 파이프만 쓰면 경고에 실패 내용이 안 실린다.
        assert "tee pytest-out.txt" in gate
        # pipefail이 없으면 tee의 0이 pytest 실패를 덮어 경고가 아예 안 뜬다.
        assert "set -o pipefail" in gate
        assert "steps.tests.outcome == 'failure'" in gate
        assert "scripts/warn_tests.py" in gate


def test_summarize_returns_tail(tmp_path):
    f = tmp_path / "out.txt"
    f.write_text("\n".join(f"line{i}" for i in range(100)), encoding="utf-8")
    out = warn_tests.summarize(str(f), tail_lines=5)
    assert out.splitlines() == ["line95", "line96", "line97", "line98", "line99"]


def test_summarize_missing_file_is_explicit(tmp_path):
    out = warn_tests.summarize(str(tmp_path / "nope.txt"))
    assert "없음" in out


def test_summarize_drops_blank_lines(tmp_path):
    f = tmp_path / "out.txt"
    f.write_text("a\n\n\nb\n", encoding="utf-8")
    assert warn_tests.summarize(str(f)) == "a\nb"


def test_message_carries_failure_and_run_url(tmp_path):
    f = tmp_path / "out.txt"
    f.write_text("FAILED tests/test_x.py::test_y - assert 0 == 4\n", encoding="utf-8")
    msg = warn_tests.build_message("daily", str(f), "https://example/run/1")
    assert "⚠️" in msg
    assert "daily" in msg
    assert "test_y" in msg
    assert "https://example/run/1" in msg
    # 자동화가 돌았다는 사실이 메시지에 있어야 오해가 없다.
    assert "자동화는 계속 실행" in msg


def test_main_never_fails_the_run(tmp_path, capsys):
    f = tmp_path / "out.txt"
    f.write_text("FAILED tests/test_x.py::test_y\n", encoding="utf-8")
    rc = warn_tests.main(["--output", str(f), "--workflow", "daily", "--no-telegram"])
    assert rc == 0
    assert "::warning" in capsys.readouterr().out
