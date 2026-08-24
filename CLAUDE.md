# CLAUDE.md

의료 포털(닥터빌·키메디·HMP·인터엠디) 일일 자동화. Playwright(Chromium) + GitHub Actions 무인 실행 + 텔레그램 요약.

상세 지식(셀렉터, 버그 이력, 설계 근거, 외부 cron 설정)은 전부 [MEMORY.md](MEMORY.md)에 있다. 이 파일은 규칙과 포인터만 유지한다.

---

## 모듈 목록

| # | 모듈 | 스크립트 | 상태 |
|---|---|---|---|
| 1 | 익일 닥터빌 퀴즈 사전 확인 | `doctorville.py --task precheck_quiz` | 운영 |
| 2 | 닥터빌 퀴즈 답변 입력 | `doctorville.py --task quiz` | 운영 |
| 3 | 닥터빌 출석 | `doctorville.py --task attend` | 운영 |
| 4 | 닥터빌 세미나 신청 | `doctorville.py --task seminar` | 운영 |
| 5 | 키메디 출석 | `keymedi.py` | 운영 |
| 6 | HMP 캡슐 출석 | `hmp.py` | 운영 |
| 7 | HMP 룰렛(연속 10·20·30일에만 활성) | `hmp.py` 내장 | 운영 |
| 8 | HMP 지식커뮤니티 댓글 | `hmp.py` 내장 | 운영 |
| 9 | HMP 지식커뮤니티 글쓰기 | `hmp.py` 내장 | 운영 |
| 10 | 닥터빌 세미나 입장(방송 중) | `seminar_live.py` | 운영 |
| 11 | 닥터빌 세미나 설문(종료 후) | `seminar_survey.py` | 운영 |
| 12 | 텔레그램 정답 수신·반영 | `telegram_inbox.py` | 운영 |
| — | 인터엠디 오늘의 퀴즈 | `intermd.py` | **수동 전용**(러너 IP 403) |

---

## 실행 아키텍처

| 워크플로우 | 트리거 | 실행 순서 |
|---|---|---|
| `daily.yml` | cron-job.org 00:15 KST (주) + GitHub cron `0 7 * * *` (16:00 KST 백스톱) | ① inbox fetch → ② 닥터빌(출석·퀴즈) → ③ 키메디 → ④ HMP(캡슐·룰렛·댓글·글쓰기) → ⑤ 익일 퀴즈 사전 확인 (`daily_runner.py`) → 정답 커밋 |
| `seminar_block.yml` | cron-job.org → `workflow_dispatch` (11:00~14:30, 17:00~21:30 KST 30분 간격) | ① inbox fetch (11:00 KST 런만) → ② 닥터빌 세미나 신청 (`doctorville.py --task seminar`) → ③ 라이브 세미나 입장 (`seminar_live.py`) → ④ 세미나 설문 (`seminar_survey.py`) |

- 중앙 알림 게이트(`scripts/notify.py`)가 `NOTIFY_LEVEL` 환경변수 (미설정/빈값 시 `"all"` 기본값 / `"actionable"`)에 따라 알림 여부를 결정한다.
- 각 스크립트는 결과 JSON 1건을 stdout에 출력, `daily_runner.py` 및 알림 게이트가 파싱·취합·전송.
- 서브프로세스는 `sys.executable`로 호출. **venv 절대경로 하드코딩 금지.**
- 실패 1건이라도 있으면 exit 1.
- CI는 `xvfb-run -a ... --headed`로 실행(헤드리스 실패 이력).

---

## 계정 범위

| 계정 | 닥터빌 | 키메디 | HMP | 인터엠디 |
|---|---|---|---|---|
| `bjh7790@gmail.com` (백승진) | 출석·퀴즈·세미나·설문 | 출석 | 캡슐·룰렛·댓글·글쓰기 | 퀴즈(수동) |
| `wonju1119@naver.com` (정원주) | 출석·퀴즈·세미나·설문 | ❌ | ❌ | ❌ |

---

## 파일 맵

| 파일 | 역할 |
|---|---|
| `scripts/common.py` | `read_credentials` / `list_accounts` / `account_label` / `is_recon_enabled` / `save_screenshot` / `goto_with_retry` / `reload_with_retry` |
| `scripts/notify.py` | 중앙 알림 게이트 (severity 판정, messaging, Telegram 전송) |
| `scripts/recon.py` | 정찰 스크립트 (CLI R3/R4, RECON=1 환경변수 R1/R2) |
| `scripts/daily_runner.py` | daily 워크플로우 오케스트레이터 + 알림 필터링 |
| `quiz_answers.json` | 닥터빌 퀴즈 문제은행 `{제품명: {문항텍스트: 정답보기텍스트}}`. 미등록 문항은 값이 `[표시줄, 보기…]` 리스트로 깔린다(정답만 남기고 지우면 등록) |
| `quiz_answers_legacy.json` | 구형식 폴백 `{제품명: "111"}` (보기 번호 시퀀스 문자열) |
| `intermd_answer.json` | 인터엠디 최신 정답 1건 `{answer, updated_at}` (덮어쓰기) |
| `seminar_applied.json` | 세미나 신청 이력 `{계정: {seminarId: {applied_at, title, start}}}`. 목록에 없는 **새 세미나만** 상세 조회 |
| `survey_quiz_answers.json` | 설문 **퀴즈** 족보 `{문항텍스트: 답변}` (`[퀴즈]` 배지 문항) |
| `survey_text_answers.json` | 설문 **주관식** 족보 `{문항텍스트: 답변}` (입력란 문항) |
| `survey_answers_legacy.json` | 3분류 도입 전 단일 족보. 폴백 전용이며 조회될 때마다 위 두 족보로 **승격·제거**된다(최종 삭제 목표) |
| `scripts/state/seminar_entered.json` | 세미나 입장·설문 이력 (State v2 schema, Actions cache 유지) |
| `credentials.json` | 로컬 전용(gitignore). CI는 `CREDENTIALS_JSON` secret |
| `scripts/logs/` | 실패 스크린샷 (artifact 7일 보관) |

Secrets: `CREDENTIALS_JSON`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.
credentials 스키마·계정별 id 규칙 → [MEMORY.md](MEMORY.md) "credentials 스키마".

---

## Claude의 역할

무인 실행이므로 일상 개입 없음. 개입 조건은 텔레그램 알림뿐이다.

1. `no_answer` 알림 → `quiz_answers.json`의 해당 제품에 **키와 보기가 이미 깔려 있다**(아래 2번과 같은 방식). 정답만 남기고 나머지 줄을 지운다. 깔아둔 문항 수는 결과 JSON의 `bank_seeded`.
2. `incomplete_bank` 알림 → 알림의 각 문항에 붙은 `bank` 값(`quiz`/`text`)이 가리키는 족보(`survey_quiz_answers.json` / `survey_text_answers.json`)를 채운다. 키는 스크립트가 이미 만들어 두며, **값 자리엔 표시줄(`※ 정답만 남기고…`) + 보기 전부가 깔려 있다** — 정답만 남기고 나머지 줄(표시줄 포함)을 지우면 끝이다. 표시줄이 남아 있으면 미등록으로 취급되어 제출에 쓰이지 않는다. 주관식은 보기가 없으므로 빈 문자열이다.
   일반 문항은 항상 2번으로 자동 제출되므로 여기 올라오지 않는다. `bank: null`이면 보기가 2개 미만인 DOM 이상이니 스크린샷을 본다.
3. `failed` / `unverified` 알림 → 텔레그램 메시지보다 **Actions artifact 스크린샷을 먼저** 본다(`gh run download <run-id>`).
4. 연속 출석일이 10의 배수에 근접하면 룰렛 안내.
5. 사용자가 채팅으로 텔레그램 인박스 형식 그대로 `제품명 시퀀스`(예: `징코샷 O24`)를 주면 → 절차는 [MEMORY.md](MEMORY.md) "채팅으로 시퀀스 답 받기" 참고. 반영 후 **별도 확인 없이 바로** `main`에 merge·push한다(2026-08-24부터 사용자 지시로 기본값 변경 — 예전엔 명시적 요청 시에만 반영했음).

### 금지
- 정답을 추측해 제출하지 않는다. 미등록이면 미시도(`no_answer` / `incomplete_bank`).
- 자동화가 막힌 항목을 "완료"로 표기하지 않는다.
- 비밀번호·토큰을 코드나 문서에 남기지 않는다.

---

## 상태값 및 Severity (JSON status)

| Severity | status | 의미 | 텔레그램 (`actionable`) |
|---|---|---|---|
| `alert` | `failed`, `blocked`, `unverified` | 오류 / 긍정 증거 미비 강등 | ❌ / ⚠️ |
| `action` | `no_answer`, `incomplete_bank` | 정답/설문 미등록 (사용자 개입 필요) | ❓ |
| `ok` | `success` (verified) | 성공 및 긍정 증거 확인 완료 | 전송 안 함 |
| `quiet` | `already_done`, `skipped`, `no_target`, `not_ready`, `closed` | 완료/건너뜀/대상없음/마감 | 전송 안 함 |

---

## 로컬 실행 (디버깅)

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt && playwright install chromium && deactivate
```

```bash
venv/bin/pytest
venv/bin/python3 scripts/daily_runner.py --no-telegram --headed
venv/bin/python3 scripts/doctorville.py --account bjh7790 --task quiz --headed
venv/bin/python3 scripts/recon.py --item R3
```

---

## 정책

- **개인정보 활용 동의: 항상 동의**(`button.btn_confirm`). 사용자 사전 승인 완료(제3자 제공, 12개월 보유).
- **텔레그램 정답 마감:** daily 주 실행이 **00:15 KST**라, 낮에 보낸 정답은 **그날 자정 런**에 반영된다(실질 마감 24:00). `precheck_quiz`가 익일 퀴즈를 미리 알려주므로 하루 여유가 있다.
- **CI는 워킹트리가 아니라 HEAD를 돌린다.** 동작이 "옛날 코드" 같으면 `git show HEAD:<파일>`부터 확인.
- **성공의 양성 증거 (`verified_by`):** `status: "success"`에는 항상 `verified_by`가 동반되어야 하며, 없으면 `unverified`(`alert`)로 강등된다.

---

## 다음 런에서 확인할 것 (2026-08-12 변경분)

오늘 바꾼 것들은 **실제 런에서 아직 검증되지 않았다.** 다음 `seminar_block` / `daily` 결과에서 아래를 본다.

| 확인 대상 | 어디서 | 기대값 | 아니면 |
|---|---|---|---|
| 신청 이력이 상세 조회를 줄였나 | `seminar` 노드의 `skipped_known` | 2번째 런부터 0보다 큼, 신청 스텝 111s → 크게 감소 | 0에 가까우면 마감·정원초과 세미나가 목록에 계속 남는다는 뜻 |
| 설문 팝업 대기 단축 | 설문 스텝 지속시간 | 247s → ~90s | 안 줄면 타임아웃이 아닌 다른 지연 |
| 설문 3분류 | `incomplete_bank` 알림 | 퀴즈·주관식만 올라옴, 일반 문항은 자동 2번 | 일반 문항이 올라오면 분류 오판 |
| legacy 승격 | 결과 JSON의 `promoted` | `survey_answers_legacy.json`이 줄어듦 | 안 줄면 왕복 검증에서 막히는 중 |
| 제품명 정규화 | 퀴즈 `no_answer` 빈도 | 표기 흔들림으로 인한 `no_answer` 소멸 | — |
| `ERR_CONNECTION_CLOSED` | 실패 런 수 | 부하 감축으로 감소 | 그대로면 `goto_with_retry` 지수 백오프 적용 |

첫 런은 신청 이력이 비어 있어 평소대로 전부 훑는다. **절감은 두 번째 런부터다.**

알려진 한계 (사용자 확인함, 2026-08-12): 마감·정원초과 세미나는 신청 이력에 기록되지 않아 매 런 상세를 다시 연다. 그런 세미나가 많지 않다고 판단해 그대로 둔다.

### 2026-08-24 변경분 — **미확정**

설문에서 응답 컨트롤이 없는 `<p>` 기반 항목을 건너뛰도록 바꿨다(세미나 5587). **그 항목이 무엇인지는 아직 확정되지 않았고, 실제 런에서 검증되지 않았다.** 배경·롤백 지점·이어서 볼 것은 전부 [MEMORY.md](MEMORY.md) "설문 `<p>` 기반 항목"에 있다. 다음 설문 런에서 결과 JSON의 `static_items`(건너뛴 항목 수)와 제출 성공 여부를 확인할 것.

---

## 미결 항목 (2026-08-01 배포 시점)

### ~~① 설문 롤업 강등~~ — 2026-08-02 해결

`seminar_survey.rollup_verified_by()`가 계정 레벨 `verified_by`를 만든다. 성공한 설문이 **전부** 개별 `verified_by`를 가질 때만 생성하므로, 증거 없는 성공은 여전히 `unverified`로 강등된다.

### ② 정찰 R1 미수집 — 설문 증거 판정이 아직 휴리스틱

`RECON=1` 환경변수로 실행해야 덤프된다. 기본 실행에는 영향 없다.

| ID | 대상 | 현재 판정 방식 |
|---|---|---|
| R1 | 설문 완료 화면 문구 | `완료`/`감사`/`제출`/`참여`/`응답` 키워드 부분 일치 |
| ~~R2~~ | ~~닥터빌 출석 완료 표식~~ | **2026-08-10 수집 완료** → `td[data-date="{today}"] div.point.complete` (`scripts/recon.py --item R2`). 상세는 [MEMORY.md](MEMORY.md) "닥터빌 셀렉터" |

R1은 **실제 설문 제출이 일어나야** 수집되므로 며칠 걸린다. 수집 전까지 설문이 `unverified`를 내는 것은 예상된 동작이다. 산출물은 `scripts/logs/recon_*`에 남고 artifact로만 올라간다 — **커밋 금지**(설문 페이지에 이름·소속 포함).

### ③ 첫 몇 주 Actions 빨간 런은 정상

`daily_runner.evaluate_exit_code`가 `failed`·`unverified`·`blocked`를 모두 exit 1로 잡는다. 양성 증거를 못 잡는 모듈이 워크플로우를 실패로 만든다.

- 커밋·아티팩트 스텝은 `if: always()`라 **데이터 유실은 없다.**
- 빨간 런 목록 = 증거 셀렉터를 다듬을 작업 목록이다.
- 실패를 없애려고 `verified_by`를 무조건 붙이지 않는다. 증거가 없으면 `unverified`가 정답이다.
