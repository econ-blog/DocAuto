import sys
from pathlib import Path

import pytest

# Add scripts directory to sys.path for flat imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))


@pytest.fixture(autouse=True)
def isolate_run_logs(tmp_path, monkeypatch):
    """실행 로그를 tmp로 돌린다.

    doctorville.task_seminar / seminar_live / seminar_survey는 내부에서 runlog에
    직접 쓴다. 격리하지 않으면 테스트를 돌릴 때마다 레포의 logs/에 가짜 세미나가
    쌓인다(실제로 한 번 커밋될 뻔했다).
    """
    monkeypatch.setenv("DOCAUTO_LOG_DIR", str(tmp_path / "runlogs"))
