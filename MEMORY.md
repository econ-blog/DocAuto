# MEMORY.md

DocAuto의 상세 지식 저장소. 셀렉터·파일 포맷·설계 근거·버그 이력·교훈. 운영 규칙은 [CLAUDE.md](CLAUDE.md).
새로 알게 된 내용은 여기에 추가한다. **날짜별 파일·별도 md 생성 금지.** 일일 실행 결과는 텔레그램 히스토리에 남으므로 여기에 로그를 쌓지 않는다.

---

## credentials 스키마

```json
{
  "telegram": { "bot_token": "...", "chat_id": "..." },
  "bjh7790": {
    "email": "bjh7790@gmail.com",
    "doctorville": { "password": "..." },
    "keymedi":     { "id": "bjh7790", "password": "..." },
    "hmp":         { "password": "..." },
    "intermd":     { "id": "bjh7790", "password": "..." }
  },
  "wonju": { "email": "wonju1119@naver.com", "doctorville": { "password": "..." } }
}
```

- 닥터빌: 로그인 id = 계정의 `email`.
- 키메디: `keymedi.id`(이메일 아님) 필수.
- HMP·인터엠디: `id` 생략 시 **계정 키 자체**("bjh7790")를 id로 사용.
- 계정/필드 추가 시 GitHub `CREDENTIALS_JSON` secret도 갱신해야 CI에 반영된다(미갱신으로 인한 실패 이력 있음).

---

## 셀렉터

각 스크립트 상단 docstring이 1차 근거. 아래는 요약.

### 닥터빌 (`doctorville.py`)
- 로그인: `/intro` → `a[href*="mims-account.shop.co.kr"][href*="/login"]` → mims(`input[name="identifier"]`, `input[type="password"]`, `button[type="submit"]:has-text("로그인")`) → `wait_for_url("*doctorville.co.kr*")`.
- 출석(`/event/attend`) — **R2 수집 완료(2026-08-10, 2계정 동일)**:
  - **페이지 진입만으로 출석이 처리된다.** 버튼 클릭은 필요 없다.
  - 출석 버튼 2개가 **항상 DOM에 공존**하고 `style="display:none"`으로 토글된다. 따라서 "버튼이 없다"는 판정은 성립하지 않는다 — **visible 여부로만 갈린다.**
    - 미출석: `button.btn.point_down` = `"8월 10일 출석하기"` visible
    - 출석완료: `button.btn.complete` = `"8월 10일 출석완료"` visible
  - **날짜 확정 증거(권장 판정)**: 달력 `td[data-date="YYYY-MM-DD"] > div.point.complete`. 미래 날짜는 `div.point`만 있고 `.complete`가 없다. 텍스트 휴리스틱 불필요.
  - 보너스 포인트도 읽힌다: 셀 안 `img[alt]`가 `100`(평일) / `500`(10일차 등 보너스).
  - 완료 팝업 문구(`div.tit`="오늘도 출석 완료", `div.txt`="적립완료")는 **숨김 상태로도 DOM에 상주**한다. `text=출석 완료` 류 셀렉터가 오탐하기 쉬운 이유.
  - 원본 덤프: `scripts/recon.py --item R2` → `scripts/logs/recon_R2_*.json`(커밋 금지).
- 퀴즈 진입: `/product/main` → `.quiz_calender`에서 오늘 날짜 다음 줄 제품명 + `td.today` 내 hidden input `.pIdCls`의 pId → `/product/productView?pId=XXX`.
- 퀴즈 레이어: `#quizLayerPop`(오버레이 `.layer_quiz`) / 문항 `.question_area` 반복(`.txt_question`, `ul.question_choice li input[name="an_N"][value="V"]` + `label`) / 문항수 `#questionCnt` / 제출 `.btn_answer` / 정답 `:text('정답입니다')` / 오답 `:text('오답입니다')` / 이미 완료 `:text('축하드립니다')` / 닫기 `.btn_cancel`.
- 세미나 목록: `span.ico_apply` → `closest('a.list_detail')`의 `seminarId`. **제목도 여기서 긁는다**(`aEl.querySelector('.tit, dt, .title, strong')`, 없으면 innerText 줄 필터).
- **세미나 제목은 상세가 아니라 목록에서 얻는다 (2026-08-28).** `_seminar_detail_meta`는 `document` 전역을 뒤져서 사이트 공통 요소를 집어왔다 — `seminar_applied.json` 108건의 제목이 전부 `엠서클 통합회원`(100) / `라이브세미나`(8)로, **단 한 번도 진짜 제목을 얻은 적이 없다.** 목록 추출은 `a.list_detail` 안으로 스코프가 한정돼 그 오염이 구조적으로 불가능하다. 상세 제목은 폴백으로만 남겼고, 오염된 값은 `runlog.clean_title`(`JUNK_TITLES`)이 걸러 표에서 세미나 번호로 대체한다.
  - 이미 신청한 세미나는 상세를 열지 않아 제목을 새로 알 길이 없다. 그래서 **오늘 방송분에 한해** 목록 제목을 표 로그에 채워 넣는다(페이지 로드 없음). 이력 파일의 오염된 제목은 그대로 남지만 표에는 쓰이지 않는다.
- 세미나 신청: `/seminar/seminarDetail?seminarId=X` → `a.btn_bn`("신청하기") → `button.btn_confirm`(동의) → 텍스트가 "신청취소"로 바뀌면 완료.
- 라이브 입장: 목록 마커 `span.ico_enter` → 상세 `a.btn_bn.btn_enter`("입장하기", `onclick="playOnPopup(...)"` → `window.open`) → Playwright `expect_popup()`.
- 설문: `/seminar/broadcastSeminarPopup?viewType=2&seminarId=X` → `a#surveyEnter` → `button.btn_answer:has-text("설문하기")` → `survey.villeway.com` 새 창.
- 설문 폼: `form[id^="surveyForm"]`, 문항 `li[data-question-number]`, 문항 텍스트 = `label > div` 첫 줄(`[퀴즈]` 배지·후행 `*` 포함), 보기 = `ol li label` 내 `input[type=radio|checkbox]` + `span.col-start-2`, 제출 `input[type=submit][value="제출하기"]`.
- **설문 완료 표식 = 상세 페이지의 버튼 (2026-08-28 사용자 실측, m.doctorville.co.kr):** 세미나가 끝난 뒤 `/seminar/seminarDetail?seminarId=X`에 재접속하면
  - 설문까지 마쳤으면 **`설문 참여 완료` + `세미나 종료`** 두 버튼(둘 다 회색·비활성)
  - 입장을 못 했거나 제한 시간 내에 답을 못 냈으면 **`세미나 종료`** 한 버튼
  두 문구는 상호 배타가 아니므로 **완료 표시를 먼저 본다**(`detect_survey_marker`). 버튼 텍스트는 `DETAIL_BUTTON_JS`가 `a.btn_bn`·`.btn_area`·`[class*=btn]` 등을 훑어 모으고, 거기서 못 찾으면 본문 전체로 한 번 더 대조한다.
  - **www(데스크톱)는 문구가 다르다 (2026-08-31 확정).** 자동화는 www로 도는데 그 상세엔 `설문 참여 완료`도 `세미나 종료`도 없다. 세미나 5633(두 계정, run 33355665944)의 `detail_buttons`에 실려 온 실측 버튼은 `… 응답완료, 취소, 동의합니다., 동의하지 않습니다., 완료, 설문하기, …`였다. 즉 **완료 = `응답완료`, 미참여 = `설문하기`**. 그래서 설문을 정상 제출하고도 매번 `unverified`로 떨어졌다.
    - 완료 표식은 `SURVEY_DONE_MARKERS`(`설문 참여 완료`/`응답완료`/`설문 응답 완료`/`설문 완료`), 미참여 표식은 `SURVEY_PENDING_MARKERS`(`세미나 종료`/`설문하기`)로 넓혔다. 완료가 미참여를 이긴다.
    - 상세에는 **안 보이는 팝업·템플릿 버튼**이 잔뜩 있고 그 안에 `설문하기`와 `응답완료`가 같이 들어 있다. 그래서 `DETAIL_BUTTON_JS`가 `getClientRects()`+`getComputedStyle`로 가시성을 같이 실어 오고, 판정은 **보이는 버튼만** 쓴다. 보이는 게 하나도 없을 때(읽기 실패)만 숨은 것까지 본다.
    - `verified_by`에는 실제로 걸린 문구를 싣는다(`detail_button: 응답완료`). 판정 실패 시 결과 JSON에 `detail_buttons`(보이는 것) + `detail_buttons_hidden`(숨은 것)이 남는다.
  - **www 상세는 판정에 쓸 수 없다 (2026-08-31 확정, run 33360722818·33361117021의 `detail_probe`).** www 숨은 버튼에는 `응답완료`와 `설문하기`가 **항상 함께** 들어 있고(설문을 마친 5602에서도, 5633에서도 동일), 보이는 버튼에는 둘 다 없다. 08-31 낮에 "www 완료 표시는 `응답완료`"라고 적었던 것은 **틀렸다** — 숨은 템플릿을 본 것이었다.
  - **m 상세 주소는 `/cme/vod/{seminarId}`다 (2026-08-31 실측).** `m…/seminar/seminarDetail?seminarId=X`는 존재하지 않는 경로이고 `뒤로 가기 / 닥터빌로 이동하기`만 있는 안내 페이지로 떨어진다. 위 55번 줄의 08-28 메모는 이 점에서 부정확했다.
  - **판정은 m(모바일) 상세를 먼저 본다 (2026-08-31 사용자 지시).** www의 `응답완료`는 숨은 템플릿 버튼과 섞여 있어 사람이 눈으로 대조하기 어렵다. m은 사용자가 보는 화면 그대로 `설문 참여 완료`/`세미나 종료`가 뜬다.
    - 순서: `m.doctorville.co.kr/cme/vod/{seminarId}` → 판정 불가면 `www…`(사실상 판정 못 냄). m 조회에만 `MOBILE_UA`를 `set_extra_http_headers`로 씌우고 곧바로 원상복구한다(같은 컨텍스트·같은 쿠키를 쓰므로 재로그인은 없다).
    - **m 판정을 믿는 조건:** 최종 URL이 `m.`이고 안내 페이지(`닥터빌로 이동하기`)가 아닐 것. 완료 표시는 로그아웃 화면에 뜰 수 없으므로 그대로 믿고, 반대로 `미참여`는 로그아웃 화면에서도 똑같이 보이므로 로그인 증거(`로그아웃`/`마이페이지`)가 있을 때만 채택한다.
    - **m은 뼈대를 먼저 그린다.** 같은 세미나·같은 시각인데 한 계정은 `설문 참여 완료`를 읽고 다른 계정은 `뒤로 가기` 하나만 잡힌 일이 있었다(5602). 표식이 잡힐 때까지 최대 8초(0.5초 폴링) 더 기다린다.
    - **m VOD 상세의 버튼 구성 (2026-08-31 실측):** 미참여 → `뒤로 가기 / 관심 추가 / 공유 / 세미나 종료`, 참여 완료 → 거기에 `설문 참여 완료`가 붙는다. 화면에 `로그아웃`·`마이페이지`가 없어서 **미참여(not_done) 판정은 로그인 증거 조건을 못 넘어 실질적으로 보류된다** — 완료 판정만 쓰인다(안전 쪽). 미참여까지 확정하려면 m VOD의 로그인 표식을 찾아 `MOBILE_LOGIN_MARKERS`에 추가할 것.
    - **쿠키는 서브도메인으로 넘어간다 (검증됨).** www에서 로그인한 컨텍스트로 m 상세를 열어 `설문 참여 완료`를 읽었다(2026-08-31 wonju/5602). m 전용 컨텍스트는 필요 없다.
    - m이 `not_done`, www가 판정 불가면 m의 `not_done`을 살린다. 뒤집는 것은 www가 `done`일 때뿐이다.

### 키메디 (`keymedi.py`)
- 로그인: `input[name="uid"]`, `input[name="password"]`, `button:has-text("로그인")`.
- 출석: "출석체크하기"(미출석) / "출석완료"(완료). **"출석체크하기"를 먼저 확인**하고 최대 3초(500ms×6) 폴링 후 판단.
- 광고 팝업 "광고보고 출석하기" 클릭 필수(안 누르면 미지급, 새 탭 가능).
- 완료 모달: "출석체크가 완료되었습니다" + "확인".

### HMP (`hmp.py`)
- 로그인: `input[name="memId"]`, `input[name="passwd"]`, `button.btn_login:has-text("로그인")`.
- 캡슐: 신 UI "오늘의 캡슐 받기" 텍스트 / 구 UI `#capsuleBtn`·`#capsuleBtnComplete` 폴백 — **가시성으로 판단**.
- 완료 팝업: `[id="10rewardPopup"]` 내 "확인" (숫자 시작 id라 속성 셀렉터 필수).
- 룰렛: "룰렛 참여하기"(`onclick="roueletteAttendYnPopup(N)"`) → 확인 팝업 `.pop.cont` 처리 → `#startAbled` → `POST /ajax/event/rouelettePercentage.hm` → 결과 팝업 이미지 alt(`[마일리지] X 캡슐 적립 완료`).
- 댓글: `a[onclick*="goDetail"]` 전체에서 boardSeq 수집 → **내림차순 상위 8개 순회** → `knowCommBoardDetail.hm?boardSeq=X` → "댓글" 토글 클릭 → `#cmtDiv` 바깥의 빈 `form.cmtForm textarea[name="cmtCntnt"]`에 "감사합니다" → `button[onclick*="saveCmt"]` → confirm → alert "저장 완료". 내 닉네임은 `form.cmtForm span` 첫 요소.
- 글쓰기: `button.btnWrite` → `#writePopupDiv` → `#_topicNm` → `label:has-text("여행/취미")`(= `input[name="topicGbn"][value="TOPIC_13"]`) → `#title` → `iframe#innoditor_0` body + `#innoditorSource_0` → `#tag` "화이팅" Enter → `.botSubmit button[onclick*="saveBoard"]` → confirm → alert. AJAX: `POST /ajax/knowcomm/insertKnowCommBoard.hm`, `rtn_code==100` 성공.
- 글쓰기 중복 방지(2026-08-03 추가): `knowCommMyInfoPopup.hm?schGbn=BOARD` = "나의 작성 글" 목록. `knowCommMyInfo.hm`의 `$KnowCommMyInfo.openMyPopup('BOARD')`이 `window.open` 하는 URL이며 직접 GET으로도 열린다. 표 컬럼 `카테고리/협진과/제목/조회수/답변/좋아요/등록일자`, 마지막 `td`가 등록일자(`2026.08.03` 형식, 최신순). 오늘 날짜(KST)가 있으면 `already_done`(`verified_by: my_post_list_date_match`). 목록을 못 읽으면 fail-open으로 글쓰기 진행.
  - 배경: 체크가 없어서 daily CI와 로컬 실행이 겹치면 같은 글이 하루 3건까지 올라갔다(2026.08.03 실측: boardSeq 2525548·2525532·2525001).
  - 대안이었던 `POST /ajax/knowcomm/getKnowCommMyInfoMonthChart.hm`도 `thisMonthActList[].monthDt`(`20260803`) + `boardCnt`로 같은 판정이 가능하다. 목록 표가 사람이 검증하기 쉬워 그쪽을 택했다.
- 댓글은 이 중복 방지 대상이 **아니다**. 매일 다른 게시물에 1건 다는 것이 의도된 동작이라 날짜 기반 skip을 넣으면 하루치 지식내공 적립이 사라진다.

### 인터엠디 (`intermd.py`)
- 로그인: `#memberId`, `#memberPw`, `button.loginForm__btn--login` → `/home.do`.
- 퀴즈: `a#quizBtn` → 문항 `h2.pollSurvey__title`, 보기 `div.pollSurvey__body span.inputbox__radio label > input[type=radio]` + `span.text`.
- 제출 `button#saveBtn` / 정답 `[data-cont="state2"]`·`[data-cont="state3"]`(선물상자) / 오답 `[data-cont="state4"]` / 이미 참여 `p.quizOverlap[data-cont="over"]`.
- 캡차 `#captchaText`는 평소 부모 `div.fail`이 display:none. 노출되면 **풀지 않고 즉시 `failed`**.

---

## 데이터 파일 포맷

### `quiz_answers.json` (닥터빌 문제은행)
`{제품명: {문항텍스트: 정답보기텍스트}}`. 위치·번호 미사용, 실행 시점 렌더링 텍스트를 공백 정규화 후 매칭.
- **제품명 조회는 `normalize_product` 기준**(공백·구두점·대소문자 제거, 2026-08-12). 사이트가 같은 제품을 날마다 다르게 렌더해 중복 키가 실제로 쌓였다: `프리스타일리브레`(0개) / `프리스타일 리브레`(3개), `더-스피로킷` / `더스피로킷`. 한 표기로 배운 답이 다른 표기에서 안 보여 `no_answer`로 떨어지던 문제.
- **접미사가 다른 이름은 합치지 않는다.** `아림시스`와 `아림시스주`는 서로 다른 제품이고 정답도 다르다(legacy 112 vs 121). 그래서 부분 포함 매칭은 조회에 쓰지 않는다 — `match_quiz_bank`(precheck의 "아는 제품인가" 불리언)만 부분 포함을 쓴다. 같은 이유로 `시너지아`/`시너지아정`, `리토바`/`리토바젯`도 별개로 남는다.
- `consolidate_products`가 저장 시 표기 중복 키를 합친다(답이 많은 키가 대표). 2026-08-12 적용으로 19 → 18키.
- 제품명이 상세페이지 표기와 어긋나면 여전히 no_answer다("대웅징코샷" vs "대웅징코샷정240mg" 이력, 2026-08-23 신형식 "대웅징코샷정240mg"으로 등록 완료 — 아래 "채팅으로 시퀀스 답 받기" 참고) — 정규화는 표기 흔들림만 흡수하지 이름이 다른 건 못 잡는다.
- 미매칭 문항이 하나라도 있으면 통째 `no_answer` + 텔레그램에 오늘 문항·보기 JSON 전문 포함.
- O/X도 화면 라벨 텍스트 그대로 저장.
- 제출 성공 시 화면의 `{문항: 정답}`을 자동 학습·커밋(`chore: update quiz answers bank and legacy eviction from run [skip ci]`).

### `quiz_answers_legacy.json` (구형식 폴백)
`{제품명: "111"}` 문자열. 리스트 형식 사용 안 함. 문제은행 매칭 실패 시에만 사용.
처리 순서: `quiz_answers.json` → `quiz_answers_legacy.json` → `no_answer`.

**정답 시 승격·제거 (2026-08-12):** legacy로 맞히면 `_record_answers`로 `{문항텍스트: 보기텍스트}`를 `quiz_answers.json`에 옮기고 **legacy 키를 지운다**. 전에는 복사만 하고 남겨 legacy가 영영 줄지 않았다(`더-스피로킷`이 bank 3/3인데 legacy에도 342로 남아 있던 이유). 제거는 `normalize_product` 기준이라 표기 변형 키가 함께 빠진다.

**표기 충돌은 미시도:** `더-스피로킷`=342 / `더스피로킷`=324처럼 같은 제품의 시퀀스가 서로 다르면 `lookup_legacy_seq`가 None을 반환한다. 어느 쪽이 맞는지 판별할 수 없으므로 찍지 않는다.

**아직 지우면 안 되는 이유 (2026-08-12 대조, 2026-08-23 갱신):** `루피어데포`(314) · `뮤코트라`(232)는 `quiz_answers.json`에 항목이 비어 있어 답이 legacy에만 있다. `시너지아`(144) · `리토바`(411)는 접미사 차이로 합쳐지지 않는 고아 키다. `대웅징코샷`(214)은 2026-08-23 신형식(`대웅징코샷정240mg`)으로 3문항 모두 등록되며 legacy 키 삭제 완료 — 신형식 등록이 끝난 고아 키는 이렇게 바로 지운다. 나머지가 다 빠진 뒤 파일 자체를 삭제할 것.

### 채팅으로 정답 받기 (2026-08-23 도입, 2026-08-27 범위 확장)

사용자가 채팅에서 직접 정답을 주는 경우가 있다. 이건 봇으로 보낸 게 아니라 **Claude에게 직접 준 지시**이므로 `telegram_inbox.py`가 처리하지 않는다 — Claude가 수동으로 반영한다. 두 가지 입력 형식이 있다.

**A. 텔레그램 인박스 형식 그대로의 시퀀스** — `제품명 시퀀스`(예: `징코샷 O24`):
1. `quiz_answers.json`에서 제품명을 찾는다(부분 일치도 허용 — "징코샷"→"대웅징코샷정240mg"). 그 제품의 **리스트 값(미등록 표시줄+보기)이 깔린 문항들**을 JSON에 등장하는 순서 그대로 나열한다.
2. 시퀀스를 한 글자씩 그 순서에 매칭한다: 문자가 `o`/`O`/`x`/`X`면 그 문항이 O/X 문항이라는 뜻이고 해당 보기 텍스트, 숫자면 그 문항 보기 리스트의 1-based 인덱스.
3. 각 문항 값을 표시줄·나머지 보기 없이 **정답 텍스트 단일 문자열**로 교체한다(no_answer 해소 방식과 동일).

**B. `제품명 정답` 형태의 직접 지시**(예: `시너지아정 시트룰린`, 2026-08-27 사례): 문항이 하나뿐이거나 정답 텍스트를 사용자가 직접 짚어준 경우. 해당 문항의 리스트 값에서 사용자가 말한 정답과 일치하는 보기 텍스트만 남기고 표시줄·나머지 보기를 지운다(부분 일치 허용 — "시트룰린"→"시트룰린 말산염 (Citrulline Malate)"). 문항이 여러 개면 어느 문항을 가리키는지 문맥으로 판단하고, 애매하면 사용자에게 확인한다.

공통:
4. 같은 제품의 구식 키가 `quiz_answers_legacy.json`에 남아 있으면(표기가 정확히 안 맞아도 같은 제품이 확실하면) 같이 지운다 — 신형식으로 옮겨졌으니 legacy는 죽은 데이터.
5. 커밋 후 **`main`에 반영해야** 다음 데일리/세미나 실행이 이 답을 쓴다. **2026-08-24부터 기본값, 2026-08-27부터 형식 A·B 모두에 적용:** 지정 브랜치에 커밋한 뒤 별도 확인 없이 바로 feature 브랜치를 `main`에 merge(fast-forward)하고 push한다 — 사용자의 명시적 지시 없이도 정답 입력·수정 작업은 즉시 반영이 기본이다.

**오답 eviction:** `:text('오답입니다')` 감지 시 해당 키를 **두 파일 모두에서 삭제**. 사후 커밋 스텝이 두 파일을 함께 `git add` 해야 `git pull --rebase`가 미커밋 변경으로 exit 128 나는 것을 막는다.

### `seminar_applied.json` (세미나 신청 이력)
`{계정: {seminarId: {"applied_at": ISO, "title": ..., "start": "2026-08-10(월) 13:00 ~ 14:00"}}}`

**왜 필요한가 (2026-08-12 측정):** 목록의 `span.ico_apply` 배지는 "신청 가능 기간"을 뜻하지 "내가 아직 신청 안 함"이 아니다. 그래서 이미 신청한 세미나도 매 런 목록에 다시 나오고, 신청 여부는 상세 페이지를 열어야만 알 수 있었다. 30분 간격 × 하루 18런 × 2계정 = 같은 상세를 하루 36번 재방문. `seminar_block` 신청 스텝이 신규 신청 0건인 런에서도 100초씩 걸리던 원인이다.

- `filter_new_seminars`가 이력에 없는 seminarId만 상세로 보낸다. 결과 JSON에 `skipped_known` 건수가 실린다.
- **기록 시점은 두 곳**: ① 신청 클릭 후 상세 재확인에서 `신청취소` 확인 ② 상세 진입 시 이미 `신청취소`(=이미 신청됨). ②가 지금 낭비의 대부분이다.
- **마감·정원초과는 기록하지 않는다.** 신청한 게 아니기 때문. 이 부류는 여전히 매 런 상세를 연다 — 남은 낭비다.
  단 2026-08-28부터 **결과 표에는 `closed`로 올린다**(`logs/seminar-*.json`). 이력에 없어서 표에서 통째로 빠지던 문제(세미나 5498)를 막기 위한 것이고, 이력 자체는 그대로 비워 둬 재시도를 유지한다.
- **캐시가 아니라 커밋되는 파일인 이유:** Actions 캐시 restore-key가 `seminar-state-${KST_DATE}-`로 날짜 단위라 하루가 지나면 사라진다. 신청 이력은 방송일까지 며칠~몇 주 살아야 한다. `daily.yml`에는 상태 캐시 자체가 없다.
- **정리(prune)는 daily가 하루 1회.** `daily_runner`가 `prune_applied_file()`을 부른다(브라우저 불필요). `start`가 파싱되면 방송 종료 시각 지난 건을 버리고, `start`가 없거나 파싱 실패면 `applied_at` + 60일을 백스톱으로 쓴다. 30분마다 도는 block에서 정리하면 파일이 계속 바뀌어 커밋만 늘고, 지난 세미나가 남아 있어도 "상세를 안 연다"는 동작은 옳다.
- 잘못 지워도 자기 치유된다 — 다음 런에서 상세를 한 번 열고 `신청취소`를 확인하면 다시 기록된다.
- **한계:** 신청 후 취소하면(수동·서버측) 이력이 남아 재신청하지 않는다. 그 경우 이 파일에서 해당 id를 지워야 한다.
- `prune_applied_file`의 `status`: 제거 건수 0이면 `skipped`(quiet), 1건 이상이면 `success` + `verified_by`. 증거 없는 `success`는 notify가 `unverified`(alert)로 강등해 런이 빨개진다.

### `intermd_answer.json`
`{"answer": "...", "updated_at": "..."}`. 최신 1건만 덮어쓴다.
매칭: 숫자만이면 **1-based 보기 번호**, 아니면 공백 정규화 후 **부분 포함 + 유일 매칭**(완전 일치 1건이면 우선). 0건·2건 이상이면 `no_answer`. 하루 1문항 전제 — 2문항 이상 감지 시 미시도.
> 최신 1건 구조라, 실행 시각(14:00 KST) 이후 도착한 정답은 다음 날 엉뚱한 문항에 대조된다(무해하나 하루 손실).

### 설문 족보 3분류 (2026-08-12)

문항을 3가지로 나눠 처리한다 (`classify_question`):

| 종류 | 판정 | 처리 | 족보 |
|---|---|---|---|
| `quiz` | 화면 텍스트가 `[퀴즈]`로 시작 **또는** 퀴즈 족보에 키가 이미 있음 | 족보 대조, 없으면 `incomplete_bank` | `survey_quiz_answers.json` |
| `text` | `kind == "input"` (textarea / input[type=text]) | 족보 대조, 없으면 `incomplete_bank` | `survey_text_answers.json` |
| `general` | 나머지 선택형 | **항상 2번 보기**. 족보를 보지 않고 미등록도 되지 않음 | 없음 |

- 조회 순서: 종류별 족보 → `survey_answers_legacy.json` → `incomplete_bank`.
- **legacy는 읽기 전용.** 3분류 도입 전 단일 족보(130키)로, 값의 대부분이 general 문항의 `"2"`라 여기에 새 키를 쓰면 퀴즈/주관식 족보가 오염된다. 스크립트는 절대 쓰지 않고 워크플로우 커밋 스텝에도 없다.
- **배지 유실 대비:** 같은 문항이 세미나에 따라 `[퀴즈]` 배지 없이 렌더되는 사례가 실측돼 있다. 그래서 배지가 없어도 퀴즈 족보에 키가 있으면(값이 빈 문자열이어도) quiz로 분류한다 — 정답 있는 문항에 "2번"을 찍는 쪽보다 `incomplete_bank`로 막히는 쪽이 안전하다. 처음 보는 배지 없는 퀴즈 문항은 여전히 2번으로 새며, 이건 남은 위험이다.
- general 문항의 보기가 2개 미만이면 `bank: null`인 미등록으로 보고한다(DOM 이상, 어느 족보에도 쓰지 않음).
- `missing` 항목 스키마: `{question, options, bank}` — `bank`가 채워 넣을 파일을 가리킨다.

**legacy 승격 (`apply_promotions`) — legacy 삭제가 최종 목표**

legacy에서 답을 찾으면 그 자리에서 종류별 족보로 **옮긴다**(복사가 아니라 이동). 복사만 하면 legacy가 영영 줄지 않아 지울 수 없다.

- 선택형은 보기 **번호를 보기 텍스트로 바꿔** 승격한다. 번호는 위치 기반이라 세미나마다 보기 순서가 바뀌면 오답이 되고, 텍스트는 순서에 무관하다.
- **왕복 검증**: 승격할 텍스트로 `match_option`을 다시 돌려 같은 인덱스가 유일하게 나올 때만 승격한다. 같은 텍스트의 보기가 둘 이상이면 승격을 건너뛰고 legacy 값을 남긴다.
- legacy 제거는 canonical 키 기준이라 표기 변형 키(`"Edoxaban 30 mg"` / `"edoxaban 30mg"`)도 함께 빠진다.
- 승격 건수는 결과 JSON의 `promoted: {quiz: n, text: n}`에 실린다.
- 워크플로우 커밋 스텝이 **세 파일을 한 커밋에** 넣어야 승격이 런 사이에 유실되지 않는다.
- legacy 130키 중 대부분(약 90건)은 general 문항의 `"2"`라 앞으로 조회되지 않는다 — 즉 자연 배수로는 안 빠진다. 퀴즈·주관식이 다 빠진 시점에 남은 잔여를 한 번에 버리면 된다.

**보기 텍스트 매칭 (`match_option`)**: 표기 그대로(완전 일치 → 부분 포함) → canonical(완전 일치 → 부분 포함) 순. 각 단계마다 유일 매칭일 때만 인정. canonical 단계 덕분에 하이픈 종류(`–`/`-`), 괄호 앞뒤 공백, `+` 앞뒤 공백, 대소문자 차이로 매칭이 깨지지 않는다. 보기 텍스트를 답으로 적는 것이 기본 형식이라 필요한 관용이다.

### 설문 족보 파일 형식 (세 파일 공통)
`{정규화된 문항텍스트: 값}`. 세미나 무관 단일 파일. 키는 `[퀴즈]` 배지·후행 `*` 제거 + 공백 정규화.
- 선택형: 숫자만 → 1-based 보기 번호. 아니면 보기 텍스트 부분 포함 + 유일 매칭.
- 복수 선택: `"1,3"` 또는 `["1","3"]`. **쉼표 분리는 모든 조각이 숫자일 때만** — 쉼표 포함 보기 텍스트를 그대로 써도 안전.
- 주관식: 입력할 문장 그대로. 빈 문자열은 항상 "미등록".
- **주관식 답변 작성 톤 (2026-08-24):** 100~150자 이내. 임상 경험은 있지만 설문에 성의 들일 여유는 없는 사람처럼 — 구어체 반말 종결(`-임`/`-함`/`-음`), 문어체·격식체 금지, 미사여구·과도한 디테일 없이 짧고 무심하게.
- 척도형 5점 = 매우 만족 / 만족 / 보통 / 불만족 / 매우 불만족.
> 번호 방식은 위치 기반이라 다른 세미나에서 보기 순서가 바뀌면 오답이 된다. 흔들릴 수 있는 문항은 텍스트로 적는 편이 안전하다.

### `scripts/state/seminar_entered.json` (State v2)
`{"version": 2, "date": "YYYY-MM-DD", "accounts": {"bjh7790": {"entered": [{"id": 5473, "title": "...", "start": "2026-08-10(월) 13:00 ~ 14:00", "entered_at": "ISO시간"}], "survey": {"5473": "done"}, "blocks": {"lunch": [], "evening": [], "manual": []}}}}`
- `version: 2` 스키마 적용 (v1 파일 로드 시 자동 마이그레이션).
- `parse_dd_date`: `"2026-08-10(월) 13:00 ~ 14:00"` 형식 텍스트를 KST 타임존 파싱.
- 마감 계산 (`evaluate_survey_cutoff`): 마감 시각 = 세미나 종료 + 90분 (폴백: 시작/입장 시각 + 3시간).
- 마감 시각 전 = `not_ready` (`quiet`), 마감 시각 후 = `closed` (`quiet`).
- **설문 팝업 대기는 8초** (`SURVEY_POPUP_TIMEOUT_MS`, 2026-08-12). 설문 창은 열릴 때 즉시 열리므로 30초는 "안 열림"을 확인하는 비용일 뿐이었다. 아직 안 열린 세미나는 done/closed가 찍힐 때까지 30분마다 재시도되어, 이 대기가 설문 스텝 247초 중 180초를 먹고 있었다(세미나 3건 × 계정 2개 × 30초).
- gitignore 대상이며 `actions/cache`로 런 간 유지.

---

## 텔레그램 인박스 설계 (`telegram_inbox.py`)

봇은 알림 봇과 동일(`TELEGRAM_BOT_TOKEN` 재사용, 새 시크릿 없음). `getUpdates` 폴링.

흐름: `getUpdates`(offset 없이) → **chat_id 필터** → 줄 단위 파싱 → 파일 갱신 → 답장 → **워크플로우가 커밋·푸시** → `--confirm-offset`으로 확정.

- **offset 확정을 맨 마지막에 하는 이유:** `getUpdates?offset=N` 호출 순간 이전 업데이트가 서버에서 영구 삭제된다. 커밋 성공 후 확정해야 중간에 죽어도 다음 실행에 다시 읽힌다(중복 처리는 같은 값 덮어쓰기라 멱등).
- **chat_id 인증은 필수.** 없으면 봇 이름을 아는 누구나 저장소에 쓸 수 있다. 불일치 메시지는 답장 없이 버리고 offset만 확정.
- 파싱 순서: **인터엠디(`인터엠디:X` / `인터엠디 X`)를 먼저** 판정 → 그 다음 닥터빌 legacy(`<제품명> <시퀀스>`). 그래서 `인터엠디 4`가 legacy 제품명으로 오인되지 않는다.
- legacy 문법: `line.rsplit(None, 1)`(제품명에 공백 허용), 시퀀스 `^[0-9oOxX]+$` 길이 1~10, `o`/`x` 소문자 정규화, 기존 키 덮어쓰기.
- 문제은행에 없는 제품명도 거부하지 않고 저장 + 경고 답장(`⚠️ … 오타 확인`).
- 피기백 실행: 두 워크플로우 **맨 앞** 스텝, `continue-on-error: true`. 최대 공백 18:30→다음날 14:00(getUpdates 24시간 보존 한도 내). 두 워크플로우 모두 `permissions: contents: write` 필요.
- **채택 안 함 — 텔레그램 → GitHub 직접 트리거:** `setWebhook`은 URL과 `secret_token`만 설정 가능하고 커스텀 `Authorization` 헤더를 못 붙인다. GitHub API는 `Bearer <PAT>`를 요구하므로 릴레이(Worker 등) 없이는 불가능.
- **채택 안 함 — 오답 피드백 기반 정답 탐색:** 오답 문구가 틀린 문항 번호를 알려줘 3~5회면 전 문항 확보 가능하나, 하루 기회가 3회뿐.

---

## 외부 cron (cron-job.org → workflow_dispatch)

GitHub `schedule`은 지연(최대 80분)·누락이 잦아 external cron (cron-job.org)을 주 트리거로 사용한다. PAT 만료 시 401로 실패하므로 cron-job.org 실패 알림 및 PAT 만료일을 관리할 것.

**PAT (fine-grained):** repository access = `econ-blog/DocAuto`만, permissions = **Actions: Read and write** + Metadata(자동).

**1) DocAuto seminar block (`seminar_block.yml`):**
- URL: `https://api.github.com/repos/econ-blog/DocAuto/actions/workflows/seminar_block.yml/dispatches`
- Method / Body: `POST` / `{"ref":"main"}`
- Headers: `Accept: application/vnd.github+json`, `Authorization: Bearer <PAT>`, `X-GitHub-Api-Version: 2022-11-28`, `Content-Type: application/json`
- Timezone: `Asia/Seoul`
- Schedule: Minutes `0,30` / Hours `11,12,13,14,17,18,19,20,21` (하루 18회)

**2) DocAuto daily (`daily.yml`):**
- URL: `https://api.github.com/repos/econ-blog/DocAuto/actions/workflows/daily.yml/dispatches`
- Method / Body: `POST` / `{"ref":"main"}`
- Headers: `Accept: application/vnd.github+json`, `Authorization: Bearer <PAT>`, `X-GitHub-Api-Version: 2022-11-28`, `Content-Type: application/json`
- Timezone: `Asia/Seoul`
- Schedule: Minutes `15` / Hours `0` (**00:15 KST 주 실행**)
- 백스톱: GitHub schedule `0 7 * * *` (16:00 KST) 남김
- **자정 직후로 잡은 이유(2026-08-10 확정):** 닥터빌·키메디·HMP 모두 00:00 KST에 리셋된다. 리셋 직후 실행해야 실패 시 그날 하루 전체가 재시도 여유로 남는다(15:00 실행이면 16:00 백스톱 1회가 전부). 부수 효과로 텔레그램 정답 실질 마감도 24:00까지 늘어난다 — `precheck_quiz`가 익일 퀴즈를 미리 알려주므로 낮에 보낸 답을 그날 자정 런이 소비한다.
- 문서에 한동안 "15:00 KST"로 적혀 있었으나 실제 dispatch는 00:15 KST였다. 시각이 아니라 **문서가 틀렸던 것**이다.

---

## 요청 부하 프로파일 및 `net::ERR_CONNECTION_CLOSED` (2026-08-12 측정)

### 측정된 스텝 지속시간 (`seminar_block` 런 7건, 2계정 순차 포함)

| ① 신청 | ② 입장 | ③ 설문 | 합계 |
|---|---|---|---|
| 평균 111s (98~157) | 평균 126s (60~200) | 평균 247s (114~386) | 평균 485s |

`seminar_block` 18런 × 485s ≈ 8,730s/day vs `daily` 1런 × 273s. **닥터빌 트래픽의 약 97%가 `seminar_block`이다.**

### 계정당 네비게이션 수 (page.goto / reload / 팝업창)

| | 구성 | 런당 |
|---|---|---|
| ① 신청 | attend 1 + mims 로그인 1 + 목록 1 + 신청가능 N × 상세 1 + 신규 A × 검증 1 | `3 + N + A` |
| ② 입장 | attend 1 + 로그인 1 + 목록 1 + 방송중 M × (상세 1 + 팝업 1) | `3 + 2M` |
| ③ 설문 | attend 1 + 로그인 1 + pending P × 방송팝업 1 + 열린 건 × (설문창 1 + 페이지수) | `2 + P + …` |

> 네비게이션 1회 ≠ HTTP 요청 1회. 페이지당 서브리소스 배수(30~80×)는 측정하지 않았다. 스텝 간 상대 비중은 배수가 일정하므로 순위는 안 바뀐다.

**load bearing이 질문에 따라 다르다:** 요청 수는 ① 신청(하루 ~830 navigations)이 지배하고, 시간은 ③ 설문이 지배했다(그중 180초가 팝업 타임아웃). 둘 다 2026-08-12에 손봤다 → `seminar_applied.json`, `SURVEY_POPUP_TIMEOUT_MS`.

### `net::ERR_CONNECTION_CLOSED` 진단

최근 40런 로그 대조 결과:

- **발생 사이트 100% doctorville.co.kr.** 같은 런·같은 IP인데 keymedi·hmp·intermd는 0건 → 러너 네트워크 일반 불안정이 아니다.
- 성공 런 5건 샘플: 0건.
- 발생 위치는 **후반 스텝**. `seminar_block`은 3번째(`seminar_survey`)의 첫 네비게이션, `daily`는 `precheck_quiz` / attend `reload`. 1번째 스텝에서는 거의 안 난다.
- 결정적 사례(런 `31482558022`): 신청 10:31:52 → 입장 10:33:38 → 설문 10:35:26 시작, **10:35:43 실패**. 같은 IP로 3분 30초 연속 요청한 직후 새 브라우저의 첫 요청이 끊겼고, `goto_with_retry` 3회 시도가 17초 안에 전부 실패했다.

**판정: 요청량 기반 연결 차단으로 보인다.** 랜덤 플레이키였다면 첫 스텝에도 균등하게 나와야 하는데 그렇지 않다.

**분리 못 한 대안 가설:** Azure 러너 IP가 해당 사이트 WAF에서 평판 페널티를 받고 있을 가능성. 로그만으로는 요청량과 IP평판을 구분할 수 없다. 구분 실험 = 같은 시각 집 IP에서 동일 스크립트 실행.

**적용된 대응책 (2026-08-15):**
1. `common.reload_with_retry()` 추가 및 `doctorville.py` / `recon.py`의 `page.reload()` 교체.
2. `goto_with_retry` / `reload_with_retry`에 지수 백오프(`3.0, 7.0, 15.0`초) 적용으로 WAF 일시 차단 창 회피.
3. `doctorville.py:run()` 내 태스크(`attend`, `quiz`, `seminar`, `precheck_quiz`) 개별 `try-except` 격리로 단일 태스크 오류 시 잔여 태스크 보호.


---

## 중앙 알림 게이트 및 Severity (`scripts/notify.py`)

알림 게이트는 `NOTIFY_LEVEL` 환경변수(미설정/빈값 시 `"all"` [default] 또는 `"actionable"`)에 따라 텔레그램 메시지 발송 여부를 정한다.

- **Severity 계층:** `alert` (3) > `action` (2) > `ok` (1) > `quiet` (0).
- `actionable` 모드: 전체 severity가 `action` (2) 이상일 때만 전송 (개입 필요 항목 및 오류만 추출).
- `all` 모드: 모드와 무관하게 모든 실행 결과 요약 전송.
- **성공의 양성 증거 (`verified_by`):** `status: "success"`는 `verified_by` 필드가 동반되어야 `ok` (1)로 평가되며, 미비 시 `unverified` (`alert`, 3)로 강등된다.


---

## 버그·수정 이력

### doctorville.py
| 버그 | 원인 | 수정 |
|---|---|---|
| `networkidle` 타임아웃 | 백그라운드 요청으로 미도달 | `wait_until="load"` + 타임아웃 30s |
| mims 로그인 감지 실패 | `wait_for_load_state("load")`가 SSO 전환보다 먼저 끝남 | `wait_for_url("*doctorville.co.kr*")` |
| 퀴즈 레이어 ID 오류 | `#applyInfo`가 아니라 `#quizLayerPop` | 전면 교체 |
| 결과 팝업 셀렉터 | 쉼표 다중 셀렉터 + `text=` 혼용 불가 | `:text('정답입니다')` 단일 |
| 퀴즈 already_done 미인식 | 제출 완료 시 "축하드립니다" 뷰라 `.btn_answer` 없음 | `:text('축하드립니다')` → `already_done` |
| pId 조회 실패 | `/product/medicineList` 검색은 의약품 전용 — 모비케어 등 의료기기 미등록 | 캘린더 `td.today .pIdCls`에서 직접 추출(medicineList는 폴백만) |
| 출석 매일 `unverified` → daily 백스톱 런이 매일 빨간불 (2026-08-05~10) | 출석은 **페이지 진입만으로 처리**되는데, 두 버튼이 DOM에 공존하며 `display`로만 토글된다. `button:has-text("출석하기")`는 매칭되지만 hidden이라 `wait_for(state="visible")`가 타임아웃 → "출석 버튼 없음(날짜 미확인)" | 버튼 대기 **이전에** `td[data-date="{today}"] div.point.complete` 확인 → `already_done` + `verified_by`. 클릭 후 폴백도 같은 표식으로 교체(기존 `#attend_btn, .btn_attend`는 실존하지 않는 셀렉터라 0개 매칭 시 예외). 포인트는 셀 `img[alt]`에서 읽음(100/보너스 500) |
| 세미나 신청 전 계정 `failed` — `Page.evaluate: SyntaxError: Invalid or unexpected token` (2026-08-28) | 목록에서 제목을 긁는 JS를 **일반** 삼중따옴표 문자열에 넣었다. 그 안의 `\n`을 파이썬이 진짜 줄바꿈으로 바꿔 `split('` 에서 JS 문자열 리터럴이 끊겼다. `seminar_live`의 같은 JS는 `\\n`으로 이스케이프돼 있어 멀쩡했는데, 옮겨 심으면서 한 단계를 흘렸다 | JS를 모듈 상수 `SEMINAR_LIST_JS`로 빼고 **r-문자열**로 만들어 이스케이프 단계를 없앰. 회귀 테스트 `tests/test_embedded_js.py`가 evaluate JS의 줄별 따옴표 짝을 검사(브라우저 없이 검출) |
| 출석 `failed` — "출석 버튼 클릭 후 완료 확인 실패" (2026-08-14) | 오늘 셀 확인이 `locator().count()` **즉시 읽기**였다. 달력은 `domcontentloaded` 시점에 아직 안 붙어 있어, 출석이 처리됐는데도 0 → 클릭 분기로 새고 → `reload()` 후에도 또 즉시 `count()` → 실패. 키메디 `already_done` 오판과 **같은 함정**(마운트 전 count) | `_attend_marked()`로 `wait_for_selector("td[data-date]")` → 오늘 셀 순서로 대기(8초). 버튼을 누르기 전에 **재접속 1회**를 넣어 진입=출석 경로를 정상 경로로 삼음(`success`, `verified_by`=오늘 셀). 클릭 후 폴백도 같은 대기로 교체 |

### keymedi.py
- 첫 성공 2026-07-06. 수정 순서: venv 전환 → 로그인 URL 매칭 대신 폼 가시성 → 클릭 후 폼 hidden 대기.
- **already_done 오판 4회 반복.** 달력에 과거 "출석완료"가 여러 개 존재. 결정타는 `wait_for_selector('A, B')` OR 매칭이 과거 버튼만 붙어도 즉시 리턴해 오늘 버튼 마운트 전에 `count()`를 읽은 것. 수정: 3초 폴링 + already_done 분기에도 스크린샷 저장(이전엔 이 분기가 스샷을 안 남겨 사후 검증 불가였다).
- **미출석 상태에서의 폴링 로직은 아직 미검증.** 재발 시 `logs/keymedi_*_already_done_*.png`로 셀렉터 변경 여부부터 확인.

### hmp.py
- 캡슐 셀렉터 리뉴얼(2026-07-07): 구 ID 소멸 → 텍스트 기반 + 구 ID 폴백.
- 페이지 진입만으로 자동 출석되는 경우가 있어 클릭→팝업 흐름이 갈린다.
- **댓글 strict mode violation:** 기존 댓글의 수정/답글 폼도 `textarea[name="cmtCntnt"]`를 가져 2개 이상 매칭. `.first`는 위험(기존 댓글 덮어쓰기). → `#cmtDiv` 바깥 + 값이 빈 폼으로 스코프. 추가로 **기본 상태에서 textarea가 `is_visible()=False`** — "댓글" 토글을 먼저 눌러야 펼쳐진다.
- **댓글이 매일 "이미 작성 완료"였던 이유(2026-07-29):** 목록 첫 링크만 확인했는데 상단 3개가 고정 게시물(공지·[지식스폰서], 실측 2518741·2501691·2496228이 최신글 2522297보다 번호가 낮다). 거기 남은 옛 댓글을 완료 신호로 읽어 매일 아무것도 안 했다. → 내림차순 상위 8개 순회로 수정, 2522445에 작성 성공 확인.
- **글쓰기 토픽 선택 실패:** `input[name="topicGbn"]`이 커스텀 스타일링으로 시각적 숨김 → 보이는 `label:has-text("여행/취미")` 우선 클릭, `force=True` 폴백, `is_checked()` 검증 후 재시도.
- **룰렛 확인 팝업 미처리(2026-07-15):** "룰렛 참여하기" 클릭 후 휠이 아니라 `.pop.cont` 확인 팝업이 먼저 뜰 수 있고, 안 닫으면 재시도 때 "intercepts pointer events" 연쇄 실패. `_run_roulette()`에 팝업 확인 단계 추가(운영망 미검증 — 다음 활성화 때 확인).
- **goto 타임아웃(2026-07-21):** 러너의 일시적 네트워크 지연. `common.goto_with_retry()`(2초 대기 후 최대 2회 재시도) 추가.

### daily_runner.py
- **텔레그램 400 Bad Request:** 파싱 오류가 아니라 **길이 초과**였다. Playwright 예외(call log 포함 ~2400자)를 그대로 넣어 4096자 한도 초과. `notify.shorten()`(첫 줄·200자) + 4096자 안전망 + `HTTPError` 응답 body 로깅.
- **닥터빌 120초 타임아웃:** 출석+퀴즈+세미나 순차 + 세미나 건수만큼 반복이라 초과. 닥터빌만 240초.
- 실행 순서: 닥터빌×2 → 키메디 → HMP → 익일 퀴즈 사전확인 (AGENTS.md 및 build_execution_plan 일치).

### seminar_survey.py
- **headlessui 모달이 제출을 막음:** 임시저장 초안이 있으면 "작성 중인 정보를 불러왔습니다" 모달의 backdrop이 포인터 이벤트를 가로채 제출 클릭이 30초 타임아웃(실패한 실행이 초안을 남겨 재시도할수록 재현). `dismiss_alerts()`를 창 오픈 직후·제출 직전·직후에 호출.
  - **함정: 모달 루트는 크기 0이라 `is_visible()`이 False.** 이 프로젝트에서 "가시성으로 판단"이 정석이던 것과 반대로, 여기서는 `count()`로만 판정해야 한다.
- **척도형 보기 텍스트:** 보기 텍스트가 input을 감싼 label이 아니라 `label[for="<input id>"]`에 있어 전부 빈 문자열이었다 → `label[for]` 폴백 추가.
- **제출 후에도 `a#surveyEnter`가 사라진다** → "이미 참여"와 "마감"이 구분되지 않고 둘 다 `no_questions`. **2026-08-28 해결** — 설문 창이 안 열리면 상세를 열어 `설문 참여 완료`를 본다. 있으면 `already_done`(+`verified_by`)로 확정하고 이력에 `done`을 박아 다음 런이 다시 붙들지 않게 한다. 없으면 종전대로 `not_ready`/`closed`.
- **완료 판정을 완료 화면 문구 → 상세 재접속으로 교체 (2026-08-28 사용자 지시):** 옛 판정(`verify_survey_completion_text`)은 본문에 `완료`·`감사`·`제출`·`참여`·`응답` 중 하나만 있으면 성공으로 봤다. `제출`은 제출 버튼에도 있어 오탐이 났고, 제출 후 창이 닫히면 아예 읽을 수도 없어 무조건 `unverified`였다. 상세 재접속 판정에는 두 약점이 다 없다 — **창이 닫힌 경우에도 성공을 확정할 수 있다.**
  - 판정 자리 4곳: ① 제출 후 문항 없음 ② 제출 후 창 닫힘 ③ 같은 페이지 재표시(제출은 됐는지 확인) ④ 설문 창 자체가 안 열림(→ `already_done` 판별).
  - 제출 직후에는 표시 갱신이 늦을 수 있어 `finalize_after_submit`만 상세를 **한 번 더** 연다(`retries=1`).
  - 다중 페이지 설문은 페이지마다 상세를 열지 않는다. 앞 세미나 응답의 잔상으로 중간에 성공으로 끊길 수 있고 왕복 비용도 붙는다.
  - `verified_by`가 `completion_screen_verified` → `detail_button: 설문 참여 완료`로 바뀌었다.
- 설문은 페이지 순차 제출형이라 전체 사전 검증 불가. **페이지 단위 검증이 도달 가능한 최대 안전선**이라 미등록 1건이면 그 페이지를 제출하지 않고 `incomplete_bank`로 중단한다.
- **응답 컨트롤이 없는 `<p>` 기반 항목(2026-08-24, 미확정):** → 아래 "설문 `<p>` 기반 항목" 절 참고. 그 항목이 무엇인지 아직 모른다.

### seminar_live.py (2026-07-20 신규)
- 로컬 sandbox에서 Playwright 시스템 라이브러리 설치 불가(sudo 필요)해 DOM 조사는 Claude in Chrome MCP로 실제 로그인 세션에 붙어 수행했다.
- `playOnPopup` 소스 직접 검사는 도구 필터에 걸려 `usesWindowOpen` 등 구조만 간접 확인.
- 목록에 있어도 방문 시점에 방송 종료/미시작이면 상세에 `a.btn_bn.btn_enter`가 없다 → `skipped` 후 다음 세미나.

---

## 설문 `<p>` 기반 항목 (2026-08-24) — **미확정, 이어서 분석 필요**

세미나 5587(전공의를 위한 응급실 증례강의) 설문이 `incomplete_bank`로 반복 차단됐다.
당일 4회 런으로 좁힌 내용이며 **결론은 확정되지 않았다.** 롤백하거나 이어받을 수 있게 남긴다.

### 관측 (run 32693921094, `blank_probe`)

`li[data-question-number]` 11개 중 1개만 정상 문항, 나머지 10개가 다른 마크업이었다.

| 항목 | `li_inner` | `head_inner` | `hidden` | `height` | `fields` | `kids` |
|---|---|---|---|---|---|---|
| 1 (정상) | 30 | 27 | false | 112 | `input:radio,input:radio` | `span.absolute… \| label.block` |
| 2~11 | 44~83 | **-1** | false | **84** | **없음** | `span.absolute… \| p.block \| p.text-base my-2 md:my-4 break-w` |

- `head_inner: -1` = `label > div`가 없다. 문항 텍스트가 `<p>`에 있다.
- `fields` 비었음 = 라디오·체크박스·textarea·text input이 **하나도 없다**.
- `hidden: false`, 높이 84px, 텍스트 44~83자 = **화면에는 멀쩡히 보인다.**

### 배제된 가설

- **렌더 지연 아님.** 5초 재대기 후에도 동일. 11개 중 1개는 정상이라 "폼 전체 미완"도 아니다.
- **숨겨진 요소라 `innerText`가 빈 값 아님.** `hidden: false`, 높이 84px.
- **셀렉터 스코프 문제 아님.** (중간에 유력 가설로 세웠다가 프로브로 기각됐다.)
  `document` 전체를 훑는 것은 사실이나(`read_questions`), 문제의 10개는 숨은 템플릿이 아니라
  같은 페이지에 실제로 렌더된 항목이다.
- **정답 미등록이 원인 아님.** 유일하게 읽힌 1번 문항이 라디오 2개("전공의인가요?" 예/아니오로 추정)였고,
  10개는 등록 여부와 무관하게 컨트롤이 없어 막혔다.

### 확정된 결함 2개 (수정함)

1. `read_questions`가 문항 텍스트를 **`label > div`에서만** 읽어, `<p>` 기반 항목은 `question: ""`이 됐다.
   화면에 글자가 있는데 스크립트만 못 봤다.
2. `resolve_page`에 "응답 컨트롤이 없는 항목" 분류가 없어, 그런 항목이 `general` → 보기 2개 미만 →
   `_miss(None)`으로 떨어져 **페이지 전체를 막았다**.

### 아직 모르는 것 (여기서부터 이어서)

**그 10개 `<p>` 항목이 무엇인지 미확정.** 후보:
- (a) 이미 응답된 문항의 읽기 전용 표시 — 질문 `<p>` + 답변 `<p>` 두 개 구조와 맞는다
- (b) 안내문·섹션 헤더
- (c) 그 외

확인 방법: DOM 덤프에 실제 텍스트가 있다. artifact는 7일 보관이므로 **2026-08-31까지**만 가능하다.

```
gh run download 32693921094 -n seminar-block-logs-12
# survey_5587_dom_*.html / survey_5587_blank_*.png
```

덤프에는 이름·소속이 들어갈 수 있다 — **커밋 금지**(`scripts/logs/`는 gitignore).

만료 후 재수집하려면 같은 마크업이 나오는 세미나에서 `blank_probe`가 다시 찍히길 기다려야 하는데,
아래 수정으로 이제 그 항목들은 텍스트가 읽히므로 `is_blank_question`에 걸리지 않아 **프로브가 안 찍힌다.**
재수집이 필요하면 `dump_survey_dom()`을 임시로 무조건 호출하도록 바꿔야 한다.

### 이번에 바꾼 것 / 롤백 지점

브랜치 `claude/seminar-survey-registration-r2jgfw`의 커밋 4개. 전부 `scripts/seminar_survey.py` + 족보.

| 변경 | 위치 | 되돌릴 때 |
|---|---|---|
| 문항 텍스트 폴백 `label > div, p` | `read_questions` | `label > div` 단독으로 되돌리면 `<p>` 항목이 다시 빈 문자열이 된다 |
| 컨트롤 없는 항목(`kind == "unknown"`) 건너뛰기 | `resolve_page` 루프 선두 | 되돌리면 5587류 설문이 다시 `incomplete_bank`로 막힌다 |
| `is_blank_question` / `dump_survey_dom` / `probe_questions` + 5초 재대기 | `run_survey` 루프 선두 | 진단 전용. 지워도 제출 동작에는 영향 없다 |
| `"전공의인가요?"` / `"전공의 이신가요? (인턴, 레지던트)"` → `"아니오"` | `survey_quiz_answers.json` | legacy의 같은 항목(`"2"`)은 이때 제거했다. 되돌리려면 legacy에 복원 |

**건너뛰기의 안전성 근거:** 건너뛴 항목이 사실은 답해야 하는 필수 문항이었다면 진행 버튼이 먹지 않고,
`seen_pages` 지문 검사가 "같은 문항 재표시"로 잡아 `failed`로 끊는다. 오답이 제출되지는 않는다.
다만 **선택 문항이었다면 조용히 빈 채로 제출된다** — 이건 감수한 트레이드오프다.

### 검증 상태

**실제 런에서 검증되지 않았다.** 5587·5576 모두 설문 마감(15:00 KST)이 지나 당일 확인 불가였고,
사용자가 수동으로 처리했다. 단위 테스트 3건만 추가돼 있다
(`tests/test_seminar_survey.py`의 `test_static_item_*`, `test_option_less_choice_question_is_still_missing`).
다음에 `<p>` 항목이 섞인 설문을 만나면 결과 JSON의 `static_items`(건너뛴 개수)와 제출 성공 여부를 확인할 것.

### 곁다리 관측

- 요가 세미나(5576)는 주관식 `"오늘 경험한 온라인 웨비나 / 9월 예정된 오프라인 클래스와…"`가 빈 값이라
  계속 `incomplete_bank`였다. 사용자 지시로 **의도적으로 미등록 유지** — 설문 안 함.
- 같은 날 bjh7790 계정이 `net::ERR_CONNECTION_CLOSED`로 설문 스텝 전체를 날린 런이 1회 있었다
  (run 32692942892). 계정 레벨 예외라 세미나 2건 모두 미시도.

---

## 인터엠디 차단 (해결 불가 판정)

- 증상: `#memberId` 20초 타임아웃 → 셀렉터 문제로 오해하기 쉬우나, **artifact 스크린샷은 `403 Forbidden` 한 줄짜리 Apache 페이지**였다.
- 1차 대응(헤더): `locale="ko-KR"` + `Accept-Language` 명시, `detect_block()`으로 차단 문구 감지해 `접속 차단됨(...)` 보고.
- **2026-07-29 확정:** 헤더 조치 후에도 7-28·7-29 연속 403. 같은 시각·같은 코드로 로컬(집 IP)은 정상 제출 → **Azure 데이터센터 IP 대역 차단**. 코드로 해결 불가로 판단해 `daily_runner`에서 제외, 수동 실행 전용.
- 되살리려면 셀프호스티드 러너(맥) 또는 한국 residential 프록시가 필요하다.

---

## 세미나 표의 계정 분리 (2026-08-28 구현, A안)

사용자 질문(2026-08-28): "세미나 표도 승진버전과 원주버전이 있어야하지 않아?"

**바꾸기 전:** 두 계정을 한 칸에 합치고 **나쁜 쪽이 이겼다**(`runlog.merge_status`).
승진만 실패하고 원주는 성공한 경우 ❌ 하나만 보이고 어느 계정인지 알 수 없었다.
daily 표는 이미 계정별 컬럼(`출석 승진` / `출석 원주`)이라 **세미나 표만 예외**였다.

**채택:** A안 — 단계 × 계정으로 컬럼을 펼친다. 총 9컬럼
(`세미나` `시작` `종료` `신청 승진` `신청 원주` `입장 승진` `입장 원주` `설문 승진` `설문 원주`).
daily 표와 일관되고 표는 여전히 1장이다. B안(계정별 PNG 2장)은 알림이 2배가 되고,
C안(한 칸에 이모지 2개)은 순서 규칙을 외워야 해서 버렸다.

로그 스키마는 원래부터 계정별이었다(`{"apply": {"bjh7790": "success", "wonju": "failed"}}`)
— **데이터는 그대로 두고 렌더링만 바꿨다.** 옛 로그 파일도 그대로 읽힌다.

| 요소 | 위치 | 내용 |
|---|---|---|
| 컬럼 순서 | `runlog.seminar_accounts()` | credentials 순서를 앞에 놓고, 로그에만 있는 계정을 뒤에 정렬해 붙인다(합집합). 한쪽만 쓰면 계정이 표에서 통째로 사라지거나 칸이 어긋난다 |
| 계정·라벨 공급 | `seminar_report.build_report()` | `common.list_accounts(creds, "doctorville")` + `account_label`. credentials가 없으면(로컬 `--no-telegram`) 로그에서 발견한 계정으로 떨어진다 |
| 계정 없는 옛 기록 | `runlog.ACCOUNTLESS`(`"_"`) | 컬럼을 만들지 않고 **모든 계정 칸에 비친다**. 어느 계정 것인지 모르는 기록을 감추면 실패가 표에서 사라진다 |
| 계정이 하나도 없을 때 | `seminar_table()` | 단계당 한 칸으로 되돌아가 `merge_status`로 합친다(옛 로그 호환) |

`merge_status`는 daily 표(HMP 룰렛·글쓰기)에서 계속 쓰이므로 **지우지 않았다.**

### 한 번 초록이면 계속 초록 (2026-08-28 사용자 지시)

세미나 블록은 30분마다 같은 세미나를 다시 훑는다. 첫 런에서 실제로 신청·입장에
성공(✅)한 뒤, 다음 런은 이력을 보고 `already_done`(☑️)을 적어 **표가 성공을
잃어버렸다.** `already_done`은 새 결과가 아니라 옛 결과의 재확인이므로 칸을 덮을
근거가 없다.

`runlog.NON_OVERWRITING_STATUSES = {"already_done"}` — 이 상태는 **칸이 비어 있을
때만** 쓰이고, 이미 무언가 적혀 있으면 `updated_at`조차 건드리지 않는다.

- 빈 칸은 여전히 채운다. 어제 신청해둔 세미나는 오늘 첫 기록이 `already_done`이다.
- 계정별로 따로 판단한다. 승진의 재확인이 원주 칸을 막지 않는다.
- 다른 상태의 갱신은 그대로다. 정답을 채워 ❓(`incomplete_bank`) → ✅(`success`)로
  올라가는 정상 경로가 막히면 안 된다.
- ❌도 덮지 않는다. 실패를 "이미 완료"로 덧칠하면 알림과 표가 어긋난다.
- daily 표는 런마다 **행이 새로 붙으므로**(run1, run2 …) 해당 없음. 세미나 표만
  같은 칸을 read-modify-write 한다.

**검증 상태:** 실제 세미나 블록 런에서는 아직 안 돌았다. 로컬에서 표 렌더(PNG·텍스트)와
**2026-08-28 실제 로그 재렌더**까지는 확인했다(두 계정이 각자 칸에 찍힘).

---

## 다음 세션에서 이어서 할 일 (2026-08-28 기준)

### ① 목록 제목 추출 — **셀렉터는 검증됨, 신청 스텝 경로만 남음**

2026-08-28 13:00 세미나 블록 런의 실제 로그(`logs/seminar-2026-08-28.json`)에 **진짜 제목이
들어왔다** — `56세, 66세 국가건강검진 폐기능검사로 COPD 진단하고 치료하기`,
`ARB Strategies in Atrial Fibrillation – Clinical value of Olmesartan`. 목록 셀렉터
(`a.list_detail` 안의 `.tit, dt, .title, strong`)가 이 사이트에서 동작한다는 것이 확인됐으니
**`recon`으로 목록 DOM을 뜰 필요는 없다.**

단, 그 제목을 넣은 것은 `seminar_live.get_live_seminar_info()`이지 신청 스텝의
`SEMINAR_LIST_JS`가 아니다. 둘은 **같은 셀렉터**를 쓰므로 남은 의문은 셀렉터가 아니라
"신청 스텝이 그 경로를 타느냐"뿐이다.

**남은 것:** 그날 17:00 세미나(5583·5584·5585)는 여전히 표에 `세미나 5583`처럼 번호로
나온다. 로그에 항목 자체가 없고 `seminar_applied.json`의 오염된 제목(`라이브세미나`)만
있기 때문이다. "이미 신청한 세미나도 오늘 방송분이면 목록 제목을 채운다"는 경로가
13:00 런에서는 동작하지 않았다 — **이미 신청한 항목엔 `span.ico_apply` 배지가 안 붙어
목록 수집에서 빠졌을 가능성이 크다**(아래 ②와 같은 원인일 수 있다). 다음 신청 스텝 런의
로그에 `apply` 칸이 생기는지 보면 갈린다.

### ② 마감 세미나가 표에 뜨는지 — 여전히 미검증, 단서는 늘었다

세미나 5498이 표에서 빠진 원인이 "마감이라 신청 못 함"인지 "목록에 아예 없음"인지
**로그로 확정하지 못했다.** 전자면 2026-08-28 수정(`_log_seminar(sid, "closed", ...)`)으로
🔒 행이 뜬다. 후자면 여전히 안 뜨고, 그때는 목록 수집 자체를 `span.ico_apply` 배지
기준에서 전체 `a.list_detail` 기준으로 넓혀야 한다.

**2026-08-28 13:00 런 관측:** 로그에 `apply` 단계 기록이 **한 건도 없다**(`live`·`survey`뿐).
`closed`도 물론 없다. 5498은 입장까지 됐는데(`already_done`) 신청 칸은 `·`다 — 신청
이력에도 없어서 표가 채울 근거가 없다. 즉 신청 스텝이 이 세미나들을 목록에서 아예 못
봤다는 뜻이고, `span.ico_apply` 배지 기준이 좁다는 후자 가설 쪽에 무게가 실린다.

### ③ 텍스트 알림 걷어내기 — 사용자 지시, 표 안정화 후

표가 잘 돌면 기존 텍스트 알림을 없앤다(2026-08-27 지시). 아직 표가 검증 중이라 보류.

---

## 알려진 리스크

1. **구형식 legacy 시도는 하루 3회 기회 중 1회를 태운다.** 위치가 맞은 사례(모비케어 `"123"`, 아림시스 `"112"`)와 어긋난 사례(펙수클루 `"332"`→`"323"` 정정, 커밋 `394d8ec`)가 모두 있다. 안전조건은 방어일 뿐 순서 섞임 자체는 못 막는다.
2. **위치 기반 매칭 전반**(legacy 시퀀스, 설문·인터엠디 번호)은 보기 순서가 섞이면 오답. 퀴즈 레이어에 "커뮤니티 정답 공유 시 패널티" 경고가 있어 사용자별 셔플 가능성이 있다 — 위치 기반 저장을 2026-07-19 폐기한 이유.
3. **PAT 만료 시 401로 조용히 실패.** cron-job.org 실패 알림 필수.
4. **`getUpdates` 24시간 보존.** 인박스 폴링이 하루 넘게 죽으면 그 사이 메시지는 복구 불가.
5. **봇 webhook 충돌.** webhook이 설정되면 `getUpdates`가 409. 현재는 전송 전용.

---

## 교훈

- **`already_done` 판정은 "오늘 것"인지 확인해야 한다.** 날짜 개념 없는 흔적(과거 댓글·과거 출석완료 버튼)을 완료 신호로 쓰면 조용히 아무것도 안 하는 자동화가 된다. HMP 댓글·키메디 출석 둘 다 같은 함정에 빠졌다.
- **타임아웃 메시지보다 스크린샷 artifact를 먼저 볼 것** (`gh run download <run-id>`). 인터엠디 403이 셀렉터 타임아웃으로 위장했다.
- **CI는 워킹트리가 아니라 HEAD를 돌린다.** 응답이 "옛 동작" 같으면 코드 로직보다 `git show HEAD:<파일>`부터.
- **분기마다 스크린샷을 남겨라.** 스샷 없는 분기는 오판이 나도 사후 검증이 불가능하다.
- **가시성 판정은 만능이 아니다.** 대부분은 `is_visible()`이 정답이지만, 크기 0인 모달 루트나 커스텀 스타일로 숨긴 input에는 `count()`/`label` 우회가 필요하다.
- **화면 미반영 ≠ 실패.** 세미나 "신청하기" 후 텍스트가 즉시 안 바뀌어도 성공한 경우가 많다 → 재진입해 "신청취소" 확인.
- **퀴즈 placeholder 오판:** 캘린더가 오늘자 제품명을 "?"로 보여줄 수 있다(SPA 로딩 지연). "?"만 보고 "퀴즈 없음" 단정 금지.
- **세미나 동의 모달 변형:** 대개 `button.btn_confirm` 한 번이지만 일부는 2단계(제3자 제공 + 마케팅 선택). 항상 동의.
- **상태 오기재 금지.** 실제 목표(포인트 적립) 달성 시에만 완료 처리(2026-07-02 사고).
- **브라우저에 넘기는 JS는 r-문자열로 쓴다.** 일반 문자열이면 `\n`·`\d`·`\s`가 파이썬 이스케이프 단계를 한 번 거친다. `\n`은 진짜 줄바꿈이 되어 JS 문자열을 끊고, 나머지는 DeprecationWarning만 내고 우연히 살아남는다 — 그래서 더 위험하다. **JS를 파일 간에 복사할 때 이스케이프 단계 수가 바뀐다**는 것이 2026-08-28 사고의 핵심이다.
- **표에 보이는 것이 곧 데이터의 전부가 아니다.** 세미나 표가 "그날 예정 세미나"를 보여준다고 믿었지만 실제 출처는 "신청 이력"이라, 마감돼 신청 못 한 세미나가 통째로 사라졌다(2026-08-28, 5개 중 4개). 집계·리포트를 만들 땐 **행의 출처가 무엇인지** 먼저 적어 둘 것.
- **합쳐진 칸은 원인을 지운다.** 세미나 표가 두 계정을 한 칸에 합치고 나쁜 쪽을 보여주는 바람에, ❌를 봐도 어느 계정을 손봐야 하는지 알 수 없었다(2026-08-28에 계정별 컬럼으로 분리). 집계는 **행동으로 이어질 만큼** 쪼개져 있어야 한다.
- **오래 안 본 필드는 조용히 썩는다.** `seminar_applied.json`의 `title` 108건이 전부 사이트 헤더 텍스트였다. 아무도 안 봤기 때문에 몇 주간 아무도 몰랐다. 표에 띄우는 순간 드러났다 — **리포트는 그 자체로 데이터 검증 도구다.**

---

## 순수 함수 테스트 지점

Playwright 계층은 라벨 텍스트만 추출해 순수 함수에 넘긴다. `tests/`에 단위 테스트 존재.

| 함수 | 위치 |
|---|---|
| `legacy_to_choice_indices` / `parse_wrong_numbers` / `match_quiz_bank` | `doctorville.py` |
| `parse_inbox_line` / `parse_intermd_line` | `telegram_inbox.py` |
| `merge_state` / `parse_dd_date` / `upgrade_to_v2` | `seminar_live.py` |
| `evaluate_survey_cutoff` | `seminar_survey.py` |
| `severity_of` / `should_send` / `build_message` | `notify.py` |
| `list_accounts` / `account_label` / `is_recon_enabled` | `common.py` |
| `match_choice(saved, choices)` | `intermd.py` |

---

## HMP 연속 출석 이력 (룰렛 참고, 갱신 필요)

| 날짜 | 연속 일수 |
|---|---|
| 2026-06-24 | 10일 (룰렛 → 100캡슐 당첨) |
| 2026-07-03 | 3일 (연속 끊김) |
| 2026-07-06 | 6일 |

---

## 과거 실행 방식 (이력용, 현재 미사용)

GitHub Actions 전환(2026-07-14) 이전의 반자동 실행 기록. 현재 루틴과 무관.

- **Chrome MCP 도메인 차단:** keymedi.com·hmp.co.kr `navigate`가 날마다 다르게 거부됨(07-01 정상 → 07-02 차단 → 07-03 정상). 이 불안정성이 Playwright 전환의 계기.
- **Desktop Commander:** 사용자 Mac에서 스크립트 직접 실행(30초 timeout). → daily_runner + Actions로 대체.
- **Chrome 프로필 판별:** `list_connected_browsers`의 "Browser 1/2" 순서가 연결마다 뒤바뀜 → 로그인 계정명으로 검증 필요. 원주 프로필(`Profile 2`)은 매 세션 사용자가 직접 열어야 했음.
- **JS click 제약:** 퀴즈 제출·로그인 버튼은 `computer` 좌표 클릭 필요. `javascript_tool`의 outerHTML/cookie 반환은 콘텐츠 필터로 `[BLOCKED]`. — 모두 Playwright 전환으로 해소.
