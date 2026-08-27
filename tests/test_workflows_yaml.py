from pathlib import Path

def test_workflow_files_exist():
    repo_root = Path(__file__).resolve().parent.parent
    assert (repo_root / ".github/workflows/seminar_block.yml").exists()
    assert not (repo_root / ".github/workflows/seminar_live.yml").exists()
    assert (repo_root / ".github/workflows/daily.yml").exists()

def test_daily_workflow_schedule_string():
    repo_root = Path(__file__).resolve().parent.parent
    daily_content = (repo_root / ".github/workflows/daily.yml").read_text("utf-8")
    assert "0 7 * * *" in daily_content
    assert "NOTIFY_LEVEL" in daily_content

def test_seminar_block_inbox_filter_and_dynamic_accounts():
    repo_root = Path(__file__).resolve().parent.parent
    block_content = (repo_root / ".github/workflows/seminar_block.yml").read_text("utf-8")
    assert "11" in block_content
    assert "NOTIFY_LEVEL" in block_content
    assert "scripts/doctorville.py --account all --task seminar" in block_content
    assert "scripts/seminar_live.py --account all" in block_content
    assert "scripts/seminar_survey.py --account all" in block_content

def test_seminar_block_failures_reach_run_conclusion():
    """continue-on-error가 스텝 실패를 초록으로 덮으므로 집계 게이트가 있어야 한다."""
    repo_root = Path(__file__).resolve().parent.parent
    block_content = (repo_root / ".github/workflows/seminar_block.yml").read_text("utf-8")
    for step_id in ("apply", "live", "survey"):
        assert f"id: {step_id}" in block_content
        assert f"steps.{step_id}.outcome" in block_content
    assert "실패 집계" in block_content


def test_seminar_block_no_dead_account_input():
    repo_root = Path(__file__).resolve().parent.parent
    block_content = (repo_root / ".github/workflows/seminar_block.yml").read_text("utf-8")
    assert "account:" not in block_content
    assert "inputs.account" not in block_content



# --- 표 리포트 / 수동 실행 워크플로우 -------------------------------------

def _workflow(name):
    repo_root = Path(__file__).resolve().parent.parent
    return (repo_root / f".github/workflows/{name}").read_text("utf-8")


def test_manual_workflow_exposes_four_tasks():
    content = _workflow("manual.yml")
    for task in ("닥터빌 퀴즈", "세미나 신청", "세미나 입장", "세미나 설문"):
        assert f"'{task}'" in content or f'"{task}"' in content


def test_manual_workflow_runs_both_accounts():
    """수동 실행도 두 계정을 함께 돌린다(사용자 요구: 계정 분리 없음)."""
    content = _workflow("manual.yml")
    assert "--account all --task quiz" in content
    assert "--account all --task seminar" in content
    assert "scripts/seminar_live.py --account all" in content
    assert "scripts/seminar_survey.py --account all" in content
    assert "inputs.account" not in content


def test_manual_workflow_shares_seminar_concurrency_group():
    """세미나 상태 파일을 공유하므로 seminar_block과 동시에 돌면 안 된다."""
    assert "group: seminar-block" in _workflow("manual.yml")
    assert "group: seminar-block" in _workflow("seminar_block.yml")


def test_seminar_block_sends_table_at_end_of_every_run():
    content = _workflow("seminar_block.yml")
    assert "scripts/seminar_report.py" in content
    # 앞 스텝이 죽어도 표는 나가야 한다.
    idx = content.index("scripts/seminar_report.py")
    assert "if: always()" in content[idx - 400:idx]


def test_table_workflows_install_fonts_for_png():
    """PNG 표의 한글·컬러 이모지 렌더링에 필요한 폰트."""
    for name in ("daily.yml", "seminar_block.yml", "manual.yml"):
        content = _workflow(name)
        assert "fonts-nanum" in content, name
        assert "fonts-noto-color-emoji" in content, name


def test_run_logs_are_committed_for_persistence():
    """러너는 런마다 새 체크아웃이라, 커밋하지 않으면 run2 이후 append가 안 된다."""
    for name in ("daily.yml", "seminar_block.yml", "manual.yml"):
        assert "logs" in _workflow(name), name
