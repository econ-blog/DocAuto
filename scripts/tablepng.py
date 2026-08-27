#!/usr/bin/env python3
"""표를 PNG로 렌더링한다.

matplotlib/Pillow 대신 Playwright(이미 이 프로젝트의 의존성)로 HTML 표를
스크린샷한다. 새 pip 패키지가 필요 없고, 한글과 컬러 이모지 렌더링이
Chromium 쪽이 훨씬 안정적이다. CI에는 apt 폰트만 추가하면 된다
(fonts-nanum, fonts-noto-color-emoji).

렌더가 실패하면 None을 반환한다 — 호출부는 텍스트 표로 폴백한다.
"""

import html as html_mod
from pathlib import Path

# 이 프로젝트 다른 스크립트와 달리 브라우저는 항상 headless로 띄운다.
# 로컬 HTML 스냅샷이라 의료 포털에서 겪은 headless 차단과 무관하다.
RENDER_TIMEOUT_MS = 20000

CSS = """
* { margin: 0; padding: 0; box-sizing: border-box; }
body {
  font-family: 'Noto Sans KR', 'NanumGothic', 'Nanum Gothic', 'Malgun Gothic', sans-serif;
  background: #ffffff;
  padding: 20px;
  display: inline-block;
}
h1 {
  font-size: 19px;
  font-weight: 700;
  color: #1a1a1a;
  margin-bottom: 12px;
  white-space: nowrap;
}
table { border-collapse: collapse; }
th, td {
  border: 1px solid #d8dde3;
  padding: 8px 12px;
  font-size: 15px;
  text-align: center;
  white-space: nowrap;
  color: #1a1a1a;
}
th {
  background: #eef2f7;
  font-weight: 700;
  font-size: 13px;
  line-height: 1.35;
  color: #38424e;
}
td.label, th.label {
  text-align: left;
  font-weight: 600;
  background: #f7f9fb;
  max-width: 320px;
  overflow: hidden;
  text-overflow: ellipsis;
}
td.cell { font-size: 17px; }
tbody tr:nth-child(even) td:not(.label) { background: #fafbfc; }
p.legend {
  margin-top: 10px;
  font-size: 12px;
  color: #6b7684;
  white-space: nowrap;
}
"""


def build_html(title: str, headers: list[str], rows: list[list[str]], legend: str = "") -> str:
    """헤더의 개행(\\n)은 <br>로 바꿔 2줄 헤더를 만든다."""
    def esc(text):
        return html_mod.escape(str(text)).replace("\n", "<br>")

    head_cells = "".join(
        f'<th class="{"label" if i == 0 else ""}">{esc(h)}</th>' for i, h in enumerate(headers)
    )
    body = ""
    for row in rows:
        cells = "".join(
            f'<td class="{"label" if i == 0 else "cell"}">{esc(c)}</td>'
            for i, c in enumerate(row)
        )
        body += f"<tr>{cells}</tr>"

    legend_html = f'<p class="legend">{esc(legend)}</p>' if legend else ""
    return (
        "<!doctype html><html lang='ko'><head><meta charset='utf-8'>"
        f"<style>{CSS}</style></head><body>"
        f"<h1>{esc(title)}</h1>"
        f"<table><thead><tr>{head_cells}</tr></thead><tbody>{body}</tbody></table>"
        f"{legend_html}</body></html>"
    )


def render_png(title: str, headers: list[str], rows: list[list[str]],
               out_path: Path | str, legend: str = "") -> Path | None:
    """표를 PNG로 저장하고 경로를 반환한다. 실패 시 None."""
    if not rows:
        return None

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    markup = build_html(title, headers, rows, legend)

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page(device_scale_factor=2)
                page.set_default_timeout(RENDER_TIMEOUT_MS)
                page.set_content(markup, wait_until="load")
                # body가 inline-block이라 요소 스크린샷이 표 크기에 딱 맞는다.
                page.locator("body").screenshot(path=str(out_path))
            finally:
                browser.close()
    except Exception as e:  # 렌더 실패가 런 전체를 죽이면 안 된다
        print(f"[tablepng] PNG 렌더 실패: {e}")
        return None

    return out_path if out_path.exists() else None
