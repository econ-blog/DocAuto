"""'재입장하기'를 진행 중으로 읽어 놓친 설문을 조용히 덮던 버그.

2026-09-17 세미나 5666(O.M.T Web Symposium, 19:37 런 35210903336) 실측이다.
같은 런에서 wonju는 설문 창을 열었는데 bjh7790만 팝업을 못 열었고, 그 판정이
`unverified`(alert)가 아니라 quiet `not_ready`로 떨어졌다. 계정 간 엇갈림
승격(`escalate_divergent_surveys`)이 뒤늦게 올려 주지 않았다면 그대로 묻혔다.

그 시각 상세의 실측 버튼:
  - m(`/cme/vod/5666`)   : 뒤로 가기 / 관심 추가 / 공유 / 설문참여 / 세미나 종료
  - www(`?seminarId=5666`): … / 재입장하기 / 목록 / …
m은 '세미나 종료'를 띄우고 있었는데, 그 조회는 로그인 증거가 없다는 이유로
통째로 버려졌다. 남은 www의 '재입장하기'에 '입장하기'가 들어 있어 "아직 안
끝났다"로 읽혔고, 진행 중이면 설문이 안 열리는 게 정상이라 quiet이 됐다.
"""

from datetime import datetime
from unittest.mock import MagicMock

from common import KST
import seminar_survey
import survey_detail


# 2026-09-17 19:37 실측 그대로.
M_VISIBLE = ["뒤로 가기", "관심 추가", "공유", "설문참여", "세미나 종료"]
WWW_VISIBLE = [
    "전체 메뉴 열기", "관심", "재입장하기", "목록",
    "닥터빌 라이브세미나를 통해 선생님의 노하우를 공유해보세요!", "커뮤니티",
    "FAMILY SITE 목록 펼치기",
]
WWW_HIDDEN = ["로그아웃", "응답완료", "설문하기", "확인", "취소"]


def _page(url):
    page = MagicMock()
    page.url = url
    return page


def _detail(monkeypatch, visible, hidden, body=""):
    monkeypatch.setattr(survey_detail.common, "goto_with_retry", lambda *a, **k: None)
    monkeypatch.setattr(survey_detail, "read_detail_buttons", lambda p: (visible, hidden))
    monkeypatch.setattr(survey_detail, "body_text", lambda p: body)


# ---------------------------------------------------------------------------
# ① '재입장하기'는 진행 중 표식이 아니다
# ---------------------------------------------------------------------------

def test_reenter_button_is_not_a_running_marker():
    """다시보기 입장 버튼은 끝난 세미나에도 남는다."""
    assert not seminar_survey.seminar_running(["재입장하기"])
    # 진짜 입장 버튼은 그대로 진행 중이다.
    assert seminar_survey.seminar_running(["입장하기"])
    assert seminar_survey.seminar_running(["재입장하기", "입장하기"])


def test_promo_sentences_are_not_running_markers():
    """www 상세는 홍보 문구까지 버튼으로 실어 온다 — 거기 '라이브'가 들어 있다."""
    promo = "닥터빌 라이브세미나를 통해 선생님의 노하우를 공유해보세요!"
    assert not seminar_survey.seminar_running([promo])
    assert not seminar_survey.seminar_running(WWW_VISIBLE)
    # 버튼 라벨로서의 '라이브'·'방송중'은 그대로 진행 중이다.
    assert seminar_survey.seminar_running(["라이브"])
    assert seminar_survey.seminar_running(["방송중"])
    assert seminar_survey.seminar_running(WWW_VISIBLE + ["방송중"])


def test_www_detail_with_reenter_is_not_evidence_of_a_running_seminar(monkeypatch):
    """www 상세만으로 "아직 안 끝났다"가 되면 안 된다."""
    survey_detail.LAST_DETAIL_PROBE.clear()
    _detail(monkeypatch, WWW_VISIBLE, WWW_HIDDEN)

    verdict, _, err = survey_detail.read_detail_verdict(
        _page("https://www.doctorville.co.kr/seminar/seminarDetail?seminarId=5666"),
        5666, mobile=False,
    )

    assert (verdict, err) == ("unknown", "")
    assert survey_detail.LAST_DETAIL_PROBE["www"]["usable"]
    assert not survey_detail.LAST_DETAIL_PROBE["www"]["running"]
    assert not seminar_survey.probe_saw_running_seminar()
    survey_detail.LAST_DETAIL_PROBE.clear()


# ---------------------------------------------------------------------------
# ② 판정을 보류해도 '세미나 종료' 관측은 남는다
# ---------------------------------------------------------------------------

def test_mobile_login_hold_keeps_the_ended_observation(monkeypatch):
    """m VOD에는 로그아웃·마이페이지가 없어 미참여 판정은 보류된다.

    보류 대상은 설문 참여 여부이지 세미나 종료 여부가 아니다. '세미나 종료'는
    로그인과 무관한 사이트 상태이므로 관측으로는 살아남아야 한다.
    """
    survey_detail.LAST_DETAIL_PROBE.clear()
    _detail(monkeypatch, M_VISIBLE, [])

    verdict, buttons, err = survey_detail.read_detail_verdict(
        _page("https://m.doctorville.co.kr/cme/vod/5666"), 5666, mobile=True
    )

    assert verdict == "unknown"          # 미참여 판정은 그대로 보류
    assert "판정 보류" in err
    assert buttons == M_VISIBLE
    rec = survey_detail.LAST_DETAIL_PROBE["m"]
    assert rec["usable"] and rec["ended"] and not rec["running"]
    assert seminar_survey.probe_saw_ended_seminar()
    assert not seminar_survey.probe_saw_running_seminar()
    survey_detail.LAST_DETAIL_PROBE.clear()


def test_discarded_pages_still_never_count(monkeypatch):
    """신원이 안 맞는 페이지는 여전히 통째로 버린다(2026-09-15 5671·5681)."""
    survey_detail.LAST_DETAIL_PROBE.clear()
    _detail(monkeypatch, ["입장하기"], [])

    verdict, _, err = survey_detail.read_detail_verdict(
        _page("https://m.doctorville.co.kr/cme/vod"), 5666, mobile=True
    )

    assert verdict == "unknown"
    assert "5666" in err
    assert not survey_detail.LAST_DETAIL_PROBE["m"]["usable"]
    assert not seminar_survey.probe_saw_running_seminar()
    survey_detail.LAST_DETAIL_PROBE.clear()


# ---------------------------------------------------------------------------
# ③ 5666 재현 — 못 연 설문은 alert다
# ---------------------------------------------------------------------------

def test_missed_survey_after_the_end_is_an_alert_not_not_ready(monkeypatch):
    """두 상세를 실측대로 읽은 뒤 설문 창을 못 열면 `unverified`여야 한다."""
    survey_detail.LAST_DETAIL_PROBE.clear()
    monkeypatch.setattr(
        seminar_survey, "open_survey",
        lambda page, sid: (None, seminar_survey.SURVEY_POPUP_TIMEOUT_REASON),
    )

    def fake_confirm(page, seminar_id, retries=0):
        """확인 단계는 실제 판정 경로(read_detail_verdict)를 그대로 태운다."""
        survey_detail.LAST_DETAIL_PROBE.clear()
        _detail(monkeypatch, M_VISIBLE, [])
        survey_detail.read_detail_verdict(
            _page("https://m.doctorville.co.kr/cme/vod/5666"), seminar_id, mobile=True
        )
        _detail(monkeypatch, WWW_VISIBLE, WWW_HIDDEN)
        verdict, buttons, _ = survey_detail.read_detail_verdict(
            _page(f"https://www.doctorville.co.kr/seminar/seminarDetail?seminarId={seminar_id}"),
            seminar_id, mobile=False,
        )
        return verdict, buttons

    monkeypatch.setattr(seminar_survey, "confirm_survey_done", fake_confirm)

    now = datetime(2026, 9, 17, 19, 37, tzinfo=KST)
    state = {}
    result = seminar_survey.run_survey(
        MagicMock(),
        {"id": 5666, "title": "O.M.T Web Symposium", "start": "2026-09-17(목) 19:00 ~ 19:30"},
        now_dt=now,
        state=state,
        account="bjh7790",
    )

    assert result["status"] == "unverified"
    # '세미나 종료'를 봤으니 설문 창의 기준점(실제 종료)도 기록된다.
    ended = seminar_survey.get_survey_meta(state, "bjh7790", 5666).get("ended_at")
    assert ended and ended.startswith("2026-09-17T19:37")
    survey_detail.LAST_DETAIL_PROBE.clear()
