"""Centralized Notification Gate module for DocAuto.

Provides pure severity evaluation, message formatting (all / actionable modes),
and Telegram notification dispatch.
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


def _node_sev(node: dict) -> str:
    if "status" in node:
        st = node["status"]
        if st == "success" and not node.get("verified_by"):
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


LEVELS = ("all", "actionable")


def resolve_level(level: str) -> str:
    """알림 레벨 정규화. 빈 값·미지원 값은 'all'."""
    lvl = (level or "all").strip().lower()
    return lvl if lvl in LEVELS else "all"


def should_send(results: dict, level: str) -> bool:
    """Determine whether notification should be sent based on level setting."""
    if resolve_level(level) == "all":
        return True
    sev = severity_of(results)
    return SEVERITY_ORDER.get(sev, 0) >= SEVERITY_ORDER["action"]


STATUS_EMOJIS = {
    "success": "✅",
    "already_done": "☑️",
    "skipped": "⏭️",
    "no_answer": "❓",
    "incomplete_bank": "❓",
    "failed": "❌",
    "unverified": "⚠️",
    "blocked": "🚫",
    "not_ready": "⏳",
    "closed": "🔒",
    "no_target": "⏭️",
}


def format_status_emoji(status: str) -> str:
    return STATUS_EMOJIS.get(status, "❓")


def shorten(text: str, limit: int = 200) -> str:
    text = (text or "").strip()
    first_line = text.splitlines()[0] if text else text
    if len(first_line) > limit:
        first_line = first_line[:limit] + "…"
    return first_line


IGNORED_RENDER_KEYS = {
    "status", "points", "message", "verified_by", "product", "quiz_id",
    "missing", "site", "account", "screenshot", "error", "count",
    "skipped_known", "applied", "remaining", "pruned", "accounts",
}


def _format_all_summary(results: dict, date_str: str) -> str:
    lines = [f"📋 *일일 자동화 결과* ({date_str})", ""]

    def _render_dict(d: dict, indent: int = 0):
        prefix = "  " * indent
        for k, v in d.items():
            if k in IGNORED_RENDER_KEYS or k == "seminar_applied_prune":
                continue
            if isinstance(v, dict):
                if "status" in v:
                    st = v["status"]
                    emoji = format_status_emoji(st)
                    pts = f" +{v['points']}P" if v.get("points") else ""
                    prod = f" — {v['product']}" if v.get("product") else ""
                    lines.append(f"{prefix}{k}: {emoji} {st}{prod}{pts}")
                    if v.get("message") and st not in ("success", "already_done"):
                        lines.append(f"{prefix}  └ {shorten(v['message'])}")
                else:
                    lines.append(f"{prefix}{k}:")
                _render_dict(v, indent + 1)
            elif isinstance(v, list):
                if k == "surveys":
                    for item in v:
                        if isinstance(item, dict):
                            sid = item.get("seminarId", "")
                            st = item.get("status", "unknown")
                            semoji = format_status_emoji(st)
                            smsg = item.get("message", "")
                            title = f" — {item['title']}" if item.get("title") else ""
                            lines.append(f"{prefix}세미나 {sid}{title}: {semoji} {st}")
                            if smsg and st not in ("success", "already_done"):
                                lines.append(f"{prefix}  └ {shorten(smsg)}")
                    continue
                lines.append(f"{prefix}{k}:")
                for idx, item in enumerate(v):
                    if isinstance(item, dict):
                        _render_dict({f"[{idx}]": item}, indent + 1)
                    else:
                        lines.append(f"{prefix}  {idx + 1}. {shorten(str(item))}")
            elif v is not None and v != "":
                lines.append(f"{prefix}{k}: {shorten(str(v))}")

    _render_dict(results, indent=0)
    return "\n".join(lines)


def _format_actionable_summary(results: dict, date_str: str) -> str:
    lines = [f"❗ DocAuto ({date_str})", ""]
    has_items = False

    def _traverse(prefix: str, data):
        nonlocal has_items
        if isinstance(data, dict):
            if "status" in data:
                sev = _node_sev(data)
                if SEVERITY_ORDER.get(sev, 0) >= SEVERITY_ORDER["action"]:
                    has_items = True
                    status = data["status"]
                    msg = shorten(data.get("message", ""))
                    prod = f" — {data['product']}" if data.get("product") else ""
                    emoji = format_status_emoji(status)
                    header_line = f"{prefix}: {emoji} {status}{prod}"
                    if msg:
                        header_line += f" ({msg})"
                    lines.append(header_line.strip())

                    if status == "no_answer" and data.get("product"):
                        lines.append(f"  → quiz_answers.json에 {data['product']} 정답 추가 (또는 텔레그램 답장: {data['product']} [번호])")
                    if status == "incomplete_bank":
                        lines.append("  → survey_quiz_answers.json / survey_text_answers.json 빈 값 추가")

                    if "questions" in data:
                        lines.append(json.dumps(data["questions"], ensure_ascii=False, indent=2))
                    if "options" in data:
                        lines.append(json.dumps(data["options"], ensure_ascii=False, indent=2))

            for k, v in data.items():
                if k in IGNORED_RENDER_KEYS or k in ("questions", "options"):
                    continue
                if isinstance(v, (dict, list)):
                    sub_prefix = f"{prefix} > {k}" if prefix else k
                    _traverse(sub_prefix, v)
        elif isinstance(data, list):
            for idx, item in enumerate(data):
                sub_prefix = f"{prefix}[{idx}]"
                _traverse(sub_prefix, item)

    _traverse("", results)
    return "\n".join(lines) if has_items else ""


def build_message(results: dict, level: str, date_str: str) -> str:
    """Build summary message in specified level format."""
    if resolve_level(level) == "actionable":
        return _format_actionable_summary(results, date_str)
    return _format_all_summary(results, date_str)


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
