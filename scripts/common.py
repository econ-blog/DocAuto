#!/usr/bin/env python3
"""출석 자동화 스크립트 공용 유틸.

keymedi.py / hmp.py / doctorville.py가 공유하는 credentials 로딩·스크린샷·
폼 로그인 헬퍼. 사이트별 셀렉터와 완료 판정 로직은 각 스크립트에 남긴다.
"""

import json
import os
import re
import tempfile
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError, Error as PlaywrightError

KST = timezone(timedelta(hours=9))
RESERVED_KEYS = {"telegram"}

# 미등록 문항을 족보에 넣을 때, 값 자리에 보기 전부를 이 표시와 함께 깔아둔다.
# 사람이 정답만 남기고 나머지 줄(이 표시 포함)을 지우면 그대로 정답이 된다.
# 표시가 남아 있는 동안은 "아직 안 채운 것"이므로 절대 제출에 쓰이지 않는다.
# 설문 족보(seminar_survey)와 퀴즈 족보(doctorville)가 같은 문구를 쓴다 — 사람이
# 두 파일을 같은 방식으로 편집하므로 문구가 갈라지면 안 된다.
ANSWER_PLACEHOLDER_MARKER = "※ 정답만 남기고 나머지 줄(이 줄 포함)을 지우세요"

SCRIPT_DIR = Path(__file__).resolve().parent
LOG_DIR = SCRIPT_DIR / "logs"
DEFAULT_CREDENTIALS = SCRIPT_DIR.parent / "credentials.json"


def list_accounts(creds: dict, site: str | None = None) -> list[str]:
    """credentials.json에서 계정 리스트를 추출한다. RESERVED_KEYS 제외.

    site 지정 시 해당 사이트 설정 블록이 있는 계정만 필터링한다.
    """
    accounts = []
    for k, v in creds.items():
        if k in RESERVED_KEYS or not isinstance(v, dict):
            continue
        if site is None or site in v:
            accounts.append(k)
    return accounts


def account_label(creds: dict, account: str) -> str:
    """계정의 라벨(예: '승진')을 반환하며, 라벨이 없으면 계정명 자체를 반환한다."""
    if account in creds and isinstance(creds[account], dict):
        return creds[account].get("label", account)
    return account


def is_recon_enabled() -> bool:
    """환경변수 RECON="1" 설정 여부를 반환한다."""
    return os.environ.get("RECON") == "1"



def read_credentials(path: Path | str) -> dict:
    """credentials.json 전체를 dict로 읽어 반환한다."""
    return read_json(path, default={})


def read_json(path: Path | str, default=None) -> dict | list:
    """JSON 파일을 안전하게 읽어 dict/list로 반환. 후행 쉼표(trailing comma) 등 허용."""
    p = Path(path)
    if not p.exists():
        return {} if default is None else default
    try:
        with open(p, "r", encoding="utf-8") as f:
            content = f.read()
        if not content.strip():
            return {} if default is None else default
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            # 후행 쉼표(trailing comma) 보정 (예: {"a": 1,} or [1, 2,])
            relaxed = re.sub(r",\s*([\]}])", r"\1", content)
            data = json.loads(relaxed)
        return data if data is not None else ({} if default is None else default)
    except Exception:
        return {} if default is None else default


def write_json_atomic(path: Path | str, data: dict | list, indent: int = 2, sort_keys: bool = False) -> None:
    """임시 파일 작성 -> flush -> fsync -> os.replace로 원자적 파일 교체를 보장하며 적절한 권한을 유지한다."""
    filepath = Path(path)
    filepath.parent.mkdir(parents=True, exist_ok=True)

    mode = None
    if filepath.exists():
        try:
            mode = filepath.stat().st_mode & 0o777
        except OSError:
            mode = None

    temp_file = tempfile.NamedTemporaryFile("w", dir=filepath.parent, delete=False, encoding="utf-8")
    try:
        json.dump(data, temp_file, ensure_ascii=False, indent=indent, sort_keys=sort_keys)
        temp_file.write("\n")
        temp_file.flush()
        os.fsync(temp_file.fileno())
        temp_file.close()

        if mode is not None:
            try:
                os.chmod(temp_file.name, mode)
            except OSError:
                pass
        else:
            try:
                os.chmod(temp_file.name, 0o644)
            except OSError:
                pass

        os.replace(temp_file.name, filepath)
    except Exception:
        if os.path.exists(temp_file.name):
            try:
                os.remove(temp_file.name)
            except OSError:
                pass
        raise


def parse_dd_date(date_str: str | None) -> tuple[datetime | None, datetime | None]:
    """'2026-08-10(월) 13:00 ~ 14:00' 형태 문자열을 (start_dt, end_dt)로 파싱."""
    if not date_str or not isinstance(date_str, str):
        return None, None
    m = re.search(r"(\d{4}-\d{2}-\d{2})\s*\([^)]+\)\s*(\d{2}:\d{2})\s*~\s*(\d{2}:\d{2})", date_str)
    if not m:
        return None, None
    d_str, s_str, e_str = m.groups()
    try:
        s_dt = datetime.strptime(f"{d_str} {s_str}", "%Y-%m-%d %H:%M").replace(tzinfo=KST)
        e_dt = datetime.strptime(f"{d_str} {e_str}", "%Y-%m-%d %H:%M").replace(tzinfo=KST)
        return s_dt, e_dt
    except ValueError:
        return None, None


ERROR_LOG_DIR = Path(os.environ.get("DOCAUTO_LOG_DIR") or (SCRIPT_DIR.parent / "logs"))


def log_error(
    script: str,
    exc: BaseException | str,
    *,
    account: str = "",
    task: str = "",
    status: str = "failed",
    screenshot: str = "",
    extra: dict | None = None,
) -> str:
    """영구 오류 로그(logs/errors-YYYY-MM.jsonl)에 한 줄 append하고 경로를 반환한다.

    왜 필요한가: 예외의 클래스·트레이스백은 지금까지 어디에도 남지 않았다.
    결과 JSON에는 str(e) 200자만, 스크린샷은 artifact 7일, logs/daily-*.json은
    상태 키워드 한 개뿐이라 사후에 "무엇이 왜 깨졌는지"를 복원할 수 없었다.
    이 파일은 runlog.prune 대상이 아니며 레포에 커밋되어 계속 남는다.

    실패해도 예외를 밖으로 던지지 않는다 — 오류 기록이 본 흐름을 죽이면 안 된다.
    """
    try:
        import traceback

        if isinstance(exc, BaseException):
            exc_type = type(exc).__name__
            message = str(exc)[:1000]
            tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))[-4000:]
        else:
            exc_type = ""
            message = str(exc)[:1000]
            tb = ""

        entry = {
            "ts": datetime.now(KST).isoformat(timespec="seconds"),
            "script": script,
            "account": account,
            "task": task,
            "status": status,
            "exc_type": exc_type,
            "message": message,
            "traceback": tb,
            "screenshot": Path(screenshot).name if screenshot else "",
            "gh_run": os.environ.get("GITHUB_RUN_ID", ""),
            "gh_workflow": os.environ.get("GITHUB_WORKFLOW", ""),
            "extra": extra or {},
        }
        path = ERROR_LOG_DIR / f"errors-{datetime.now(KST):%Y-%m}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return str(path)
    except Exception:
        return ""


def save_screenshot(page, name_stem: str) -> str:
    """logs/<name_stem>_<타임스탬프>.png 저장 후 경로 반환(실패 시 빈 문자열)."""
    LOG_DIR.mkdir(exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    path = LOG_DIR / f"{name_stem}_{ts}.png"
    try:
        page.screenshot(path=str(path), full_page=True)
        return str(path)
    except Exception as e:
        # 스크린샷 실패는 조용히 삼키면 "증거가 왜 없는지"를 알 수 없다.
        log_error("common.save_screenshot", e, task=name_stem, status="screenshot_failed")
        return ""


RETRYABLE_ERROR_MARKERS = ("net::", "ERR_", "Timeout", "timeout")


def _is_retryable(exc: Exception) -> bool:
    """재시도로 나아질 수 있는 오류인지 판정한다.

    PlaywrightError는 네트워크 오류뿐 아니라 strict mode violation, 닫힌 page,
    target crashed 같은 코드/DOM 문제에도 쓰인다. 그런 오류에 3+7+15초를 태우면
    시간만 버리고 진짜 원인이 백오프 뒤에 묻힌다. 네트워크·타임아웃 신호가 있는
    것만 재시도한다.
    """
    if isinstance(exc, PlaywrightTimeoutError):
        return True
    msg = str(exc)
    return any(m in msg for m in RETRYABLE_ERROR_MARKERS)


def goto_with_retry(
    page,
    url: str,
    *,
    wait_until: str = "load",
    retries: int = 2,
    timeout_ms: int = 15000,
    backoff_delays: tuple[float, ...] = (3.0, 7.0, 15.0),
):
    """page.goto 타임아웃 또는 네트워크 연결 오류(net::ERR_CONNECTION_CLOSED 등) 발생 시 지수 백오프로 재시도한다.

    GitHub Actions 러너 및 로컬 실행에서 간헐적으로 첫 요청만 15초 타임아웃 또는
    순간 소켓 끊김(ERR_CONNECTION_CLOSED, ERR_CONNECTION_RESET)으로 실패하고
    다음 같은 URL 요청은 정상 응답하는 사례가 확인됨. 코드/셀렉터 문제가 아니라
    네트워크 일시 지연/서버 리셋으로 판단, goto 자체에 재시도를 추가해 흡수한다.
    지수 백오프(기본: 3초, 7초, 15초)를 두어 WAF의 일시적 차단 창을 넘기도록 한다.

    마지막 시도에서도 실패하면 발생한 예외(PlaywrightTimeoutError / PlaywrightError)를 그대로 전파한다.
    """
    last_exc = None
    for attempt in range(retries + 1):
        try:
            page.goto(url, wait_until=wait_until, timeout=timeout_ms)
            return
        except (PlaywrightTimeoutError, PlaywrightError) as e:
            last_exc = e
            if attempt < retries and _is_retryable(e):
                delay = backoff_delays[attempt] if attempt < len(backoff_delays) else backoff_delays[-1]
                try:
                    page.wait_for_timeout(int(delay * 1000))
                except Exception:
                    time.sleep(delay)
                continue
            raise last_exc


def reload_with_retry(
    page,
    *,
    wait_until: str = "domcontentloaded",
    retries: int = 2,
    timeout_ms: int = 15000,
    backoff_delays: tuple[float, ...] = (3.0, 7.0, 15.0),
):
    """page.reload() 타임아웃 또는 네트워크 연결 오류(net::ERR_CONNECTION_CLOSED 등) 발생 시 지수 백오프로 재시도한다."""
    last_exc = None
    for attempt in range(retries + 1):
        try:
            page.reload(wait_until=wait_until, timeout=timeout_ms)
            return
        except (PlaywrightTimeoutError, PlaywrightError) as e:
            last_exc = e
            if attempt < retries and _is_retryable(e):
                delay = backoff_delays[attempt] if attempt < len(backoff_delays) else backoff_delays[-1]
                try:
                    page.wait_for_timeout(int(delay * 1000))
                except Exception:
                    time.sleep(delay)
                continue
            raise last_exc



def form_login(
    page,
    id_selector: str,
    pw_selector: str,
    submit_selector: str,
    login_id: str,
    password: str,
    timeout_ms: int,
):
    """로그인 폼이 보이면 채워 제출하고, 폼이 사라질 때까지 기다린다.

    반환값:
        None  — 로그인 폼이 없음 (이미 로그인된 세션)
        True  — 로그인 성공 (폼이 hidden 됨)
        False — 제출 후에도 폼이 남아있음 (로그인 실패)

    keymedi/hmp 공통 패턴. URL 매칭이 아니라 폼 가시성으로 로그인 필요 여부를
    판단하고, 제출 직후 경쟁 상태를 피하려 폼이 hidden 될 때까지 대기한다
    (2026-07-06 keymedi 오판 사례에서 얻은 교훈).
    """
    id_input = page.locator(id_selector)
    try:
        id_input.first.wait_for(state="visible", timeout=5000)
    except PlaywrightTimeoutError:
        return None

    id_input.first.fill(login_id)
    page.fill(pw_selector, password)
    page.click(submit_selector)
    try:
        id_input.first.wait_for(state="hidden", timeout=timeout_ms)
        return True
    except PlaywrightTimeoutError:
        return False
