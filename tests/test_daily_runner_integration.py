from daily_runner import build_execution_plan, evaluate_exit_code
from notify import should_send


def test_build_execution_plan():
    creds = {
        "telegram": {},
        "bjh7790": {"doctorville": {}, "hmp": {}, "keymedi": {}},
        "wonju": {"doctorville": {}},
    }
    plan = build_execution_plan(creds)
    keys = list(plan.keys())

    # Doctorville accounts first, then keymedi, then hmp, then precheck_quiz
    assert keys[0] == "doctorville_bjh7790"
    assert keys[1] == "doctorville_wonju"
    assert keys[2] == "keymedi"
    assert keys[3] == "hmp"
    assert keys[4] == "precheck_quiz"

    # Verify script and args detail
    assert plan["keymedi"]["script"] == "keymedi.py"
    assert plan["doctorville_bjh7790"]["script"] == "doctorville.py"
    assert "--account" in plan["doctorville_bjh7790"]["args"]
    assert "bjh7790" in plan["doctorville_bjh7790"]["args"]
    assert plan["doctorville_wonju"]["script"] == "doctorville.py"
    assert "wonju" in plan["doctorville_wonju"]["args"]
    assert plan["hmp"]["script"] == "hmp.py"
    assert plan["precheck_quiz"]["script"] == "doctorville.py"
    assert "--task" in plan["precheck_quiz"]["args"]
    assert "precheck_quiz" in plan["precheck_quiz"]["args"]


def test_exit_code_evaluation():
    # Success / quiet statuses return 0
    success_results = {
        "keymedi": {"status": "already_done", "verified_by": "evidence"},
        "hmp": {"status": "success", "roulette": [{"status": "no_target"}]},
        "doctorville_bjh7790": {"attend": {"status": "success"}, "quiz": {"status": "already_done", "verified_by": "evidence"}},
        "precheck_quiz": {"status": "no_answer"},
    }
    assert evaluate_exit_code(success_results) == 0

    # failed status returns 1
    failed_results = {"keymedi": {"status": "failed", "message": "error"}}
    assert evaluate_exit_code(failed_results) == 1

    # unverified status returns 1
    unverified_results = {"doctorville_bjh7790": {"attend": {"status": "unverified", "message": "button missing"}}}
    assert evaluate_exit_code(unverified_results) == 1

    # blocked status returns 1
    blocked_results = {"hmp": {"status": "success", "roulette": [{"status": "blocked", "message": "captchas"}]}}
    assert evaluate_exit_code(blocked_results) == 1


def test_should_send_actionable_zero_calls():
    quiet_results = {
        "keymedi": {"status": "already_done", "verified_by": "evidence"},
        "hmp": {
            "status": "already_done",
            "verified_by": "evidence",
            "roulette": [{"status": "already_done", "verified_by": "evidence"}],
        },
        "doctorville_bjh7790": {"attend": {"status": "already_done", "verified_by": "evidence"}, "quiz": {"status": "already_done", "verified_by": "evidence"}},
        "precheck_quiz": {"status": "already_done", "verified_by": "evidence"},
    }
    assert should_send(quiet_results, "actionable") is False

