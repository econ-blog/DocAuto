"""Centralized Notification Gate module for DocAuto.

Provides pure severity evaluation and Telegram notification dispatch (tables/photos/text).
"""

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_CREDENTIALS = Path(__file__).resolve().parent.parent / "credentials.json"

SEVERITY = {
    "success": "ok",
    "unverified": "alert",
    "already_done": "quiet",
    "skipped": "quiet",
    "no_target": "quiet",
    "not_ready": "quiet",
    "closed": "quiet",
    "no_answer": "action",
    "incomplete_bank": "action",
    "failed": "alert",
    "blocked": "alert",
}

# Severity hierarchy: alert (3) > action (2) > ok (1) > quiet (0)
SEVERITY_ORDER = {"quiet": 0, "ok": 1, "action": 2, "alert": 3}


# 긍정 증거(verified_by)가 없으면 unverified로 강등되는 상태들.
# already_done이 여기 들어간 이유: quiet이라 알림도 표도 조용히 넘어가는데,
# 사이트가 완료 표식 셀렉터를 바꾸면 "이미 완료"로 오판하고도 며칠 모른다
# (keymedi 2026-07-12~16 오판 재발 이력). 근거를 못 대면 완료로 치지 않는다.
NEEDS_EVIDENCE = ("success", "already_done")


def _node_sev(node: dict) -> str:
    if "status" in node:
        st = node["status"]
        if st in NEEDS_EVIDENCE and not node.get("verified_by"):
            return "alert"
        return SEVERITY.get(st, "alert")
    return "quiet"


def severity_of(val) -> str:
    """Traverse a dictionary or list structure recursively to calculate maximum severity."""
    if isinstance(val, dict):
        max_sev = _node_sev(val)
        for k, v in val.items():
            if k == "status":
                continue
            sub_sev = severity_of(v)
            if SEVERITY_ORDER.get(sub_sev, 0) > SEVERITY_ORDER.get(max_sev, 0):
                max_sev = sub_sev
        return max_sev
    elif isinstance(val, list):
        max_sev = "quiet"
        for item in val:
            sub_sev = severity_of(item)
            if SEVERITY_ORDER.get(sub_sev, 0) > SEVERITY_ORDER.get(max_sev, 0):
                max_sev = sub_sev
        return max_sev
    return "quiet"

TELEGRAM_MAX_LEN = 4096


def resolve_credentials(
    bot_token: str = "", chat_id: str = "", credentials_path=None
) -> tuple[str, str]:
    """텔레그램 토큰/chat_id를 인자 → 환경변수 → credentials.json 순으로 해석한다."""
    token = bot_token or os.environ.get("TELEGRAM_BOT_TOKEN", "")
    cid = chat_id or os.environ.get("TELEGRAM_CHAT_ID", "")
    if token and cid:
        return token, cid

    cpath = Path(credentials_path) if credentials_path else DEFAULT_CREDENTIALS
    if cpath.exists():
        try:
            with open(cpath, "r", encoding="utf-8") as f:
                creds = json.load(f)
            t_block = creds.get("telegram", {})
            if isinstance(t_block, dict):
                token = token or t_block.get("bot_token", "")
                cid = cid or t_block.get("chat_id", "")
        except Exception as e:
            print(f"[telegram] credentials 로드 실패: {e}", file=sys.stderr)

    return token, cid


def send_telegram(
    text: str, bot_token: str = "", chat_id: str = "", credentials_path=None,
    parse_mode: str = ""
) -> bool:
    """Send Telegram message via Telegram Bot API."""
    # 보낼 내용이 없으면 자격증명 유무와 무관하게 no-op 성공
    if not text:
        return True

    token, cid = resolve_credentials(bot_token, chat_id, credentials_path)
    if not token or not cid:
        print("[telegram] 토큰/chat_id 없음", file=sys.stderr)
        return False

    if len(text) > TELEGRAM_MAX_LEN:
        text = text[: TELEGRAM_MAX_LEN - 20] + "\n…(생략)"

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    body = {"chat_id": cid, "text": text}
    if parse_mode:
        body["parse_mode"] = parse_mode
    payload = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url, data=payload, headers={"Content-Type": "application/json"}, method="POST"
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status == 200
    except urllib.error.HTTPError as e:
        # 400의 실제 사유(description)는 응답 본문에만 있다
        body = e.read().decode("utf-8", "replace")[:300]
        print(f"[telegram] 전송 실패: {e} / {body}", file=sys.stderr)
        return False
    except Exception as e:
        print(f"[telegram] 전송 실패: {e}", file=sys.stderr)
        return False


def _multipart(fields: dict, filename: str, file_bytes: bytes,
               file_field: str = "photo") -> tuple[bytes, str]:
    """sendPhoto용 multipart/form-data 본문을 만든다.

    requests를 쓰지 않는 프로젝트라(표준 라이브러리만 사용) 직접 조립한다.
    """
    boundary = "----DocAutoBoundary" + os.urandom(8).hex()
    sep = f"--{boundary}\r\n".encode()
    out = bytearray()
    for key, value in fields.items():
        out += sep
        out += f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode()
        out += f"{value}\r\n".encode("utf-8")
    out += sep
    out += (
        f'Content-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'
        "Content-Type: image/png\r\n\r\n"
    ).encode()
    out += file_bytes + b"\r\n"
    out += f"--{boundary}--\r\n".encode()
    return bytes(out), f"multipart/form-data; boundary={boundary}"


TELEGRAM_CAPTION_MAX_LEN = 1024


def send_photo(
    photo_path, caption: str = "", bot_token: str = "", chat_id: str = "",
    credentials_path=None, parse_mode: str = ""
) -> bool:
    """PNG 1장을 sendPhoto로 전송한다. 실패 시 False(호출부가 텍스트로 폴백)."""
    path = Path(photo_path)
    if not path.exists():
        print(f"[telegram] 사진 없음: {path}", file=sys.stderr)
        return False

    token, cid = resolve_credentials(bot_token, chat_id, credentials_path)
    if not token or not cid:
        print("[telegram] 토큰/chat_id 없음", file=sys.stderr)
        return False

    if len(caption) > TELEGRAM_CAPTION_MAX_LEN:
        caption = caption[: TELEGRAM_CAPTION_MAX_LEN - 20] + "\n…(생략)"

    fields = {"chat_id": cid}
    if caption:
        fields["caption"] = caption
    if parse_mode:
        fields["parse_mode"] = parse_mode

    try:
        body, content_type = _multipart(fields, path.name, path.read_bytes())
    except OSError as e:
        print(f"[telegram] 사진 읽기 실패: {e}", file=sys.stderr)
        return False

    url = f"https://api.telegram.org/bot{token}/sendPhoto"
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": content_type}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status == 200
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        print(f"[telegram] 사진 전송 실패: {e} / {detail}", file=sys.stderr)
        return False
    except Exception as e:
        print(f"[telegram] 사진 전송 실패: {e}", file=sys.stderr)
        return False
