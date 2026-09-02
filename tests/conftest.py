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
    log_dir = tmp_path / "runlogs"
    monkeypatch.setenv("DOCAUTO_LOG_DIR", str(log_dir))

    # common.ERROR_LOG_DIR은 import 시점에 env를 읽어 굳는다. 영구 오류 로그도
    # 같은 이유로 격리하지 않으면 테스트가 레포의 logs/errors-*.jsonl을 더럽힌다.
    import common

    monkeypatch.setattr(common, "ERROR_LOG_DIR", log_dir)
