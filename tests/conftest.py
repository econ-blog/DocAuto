import sys
from pathlib import Path

import pytest

# Add scripts directory to sys.path for flat imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))


import hashlib

PROTECTED_PATHS = [
    "quiz_answers.json",
    "quiz_answers_legacy.json",
    "survey_quiz_answers.json",
    "survey_text_answers.json",
    "survey_answers_legacy.json",
    "seminar_applied.json",
    "logs",
]


def _hash_target(path: Path) -> str:
    if not path.exists():
        return "__missing__"
    if path.is_file():
        return hashlib.sha256(path.read_bytes()).hexdigest()
    h = hashlib.sha256()
    for p in sorted(path.rglob("*")):
        if p.name == ".gitkeep" or p.is_dir():
            continue
        rel = str(p.relative_to(path))
        h.update(rel.encode("utf-8"))
        h.update(p.read_bytes())
    return h.hexdigest()


@pytest.fixture(scope="session", autouse=True)
def guard_repository_data_integrity():
    """테스트 세션 전후로 레포지토리 내 실제 데이터 파일의 변조를 감시한다."""
    repo_root = Path(__file__).resolve().parent.parent
    before = {name: _hash_target(repo_root / name) for name in PROTECTED_PATHS}

    yield

    mutated = []
    for name in PROTECTED_PATHS:
        after = _hash_target(repo_root / name)
        if after != before[name]:
            mutated.append(name)

    if mutated:
        raise AssertionError(
            f"테스트 세션 실행 중 실제 데이터 파일이 오염되었습니다: {mutated}. "
            "테스트는 실제 레포 파일에 쓰지 말고 monkeypatch/tmp_path로 격리해야 합니다."
        )


@pytest.fixture(autouse=True)
def isolate_run_logs(tmp_path, monkeypatch):
    """실행 로그 및 영구 오류 로그를 tmp로 돌린다.

    common.get_error_log_dir() 및 runlog는 DOCAUTO_LOG_DIR을 참조하므로
    환경변수 설정만으로 영구 로그와 실행 로그가 모두 격리된다.
    """
    log_dir = tmp_path / "runlogs"
    monkeypatch.setenv("DOCAUTO_LOG_DIR", str(log_dir))
