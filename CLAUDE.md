# CLAUDE.md

의료 포털(닥터빌·키메디·HMP·인터엠디) 일일 자동화. Playwright(Chromium) + GitHub Actions 무인 실행 + 텔레그램 요약.

- 상세 지식(셀렉터, 버그 이력, 설계 근거, 외부 cron, 날짜별 검증 체크리스트) → [MEMORY.md](MEMORY.md)
- 정답·족보 채우기 절차 → `.claude/skills/answer-bank/SKILL.md`
- 이 파일은 규칙과 포인터만 유지한다. 서술형 경위 설명·검증 로그는 넣지 않는다.

---

## 작업 규칙 (토큰·시간 절약)

1. 이미 읽은 파일은 다시 읽지 않는다.
2. 불필요한 도구 호출을 하지 않는다.
3. 의존성 없는 도구 호출은 한 번에 병렬로 실행한다.
4. 20줄 이상의 불필요한 출력이 예상되는 조사·검색은 서브에이전트에 위임한다.
5. 사용자가 이미 설명한 내용을 다시 설명하지 않는다.

---

## 모듈 목록

| # | 모듈 | 스크립트 | 상태 |
|---|---|---|---|
| 1 | 익일 닥터빌 퀴즈 사전 확인 | `doctorville.py --task precheck_quiz` | 운영 |
| 2 | 닥터빌 퀴즈 답변 입력 | `doctorville.py --task quiz` | 운영 |
| 3 | 닥터빌 출석 | `doctorville.py --task attend` | 운영 |
| 4 | 닥터빌 세미나 신청 | `doctorville.py --task seminar` | 운영 (daily 1회 + `manual.yml` 온디맨드) |
| 5 | 키메디 출석 | `keymedi.py` | 운영 |
| 6 | HMP 캡슐 출석 | `hmp.py` | 운영 |
| 7 | HMP 룰렛(연속 10·20·30일에만 활성) | `hmp.py` 내장 | 운영 |
| 8 | HMP 지식커뮤니티 댓글 | `hmp.py` 내장 | 운영 |
| 9 | HMP 지식커뮤니티 글쓰기 | `hmp.py` 내장 | 운영 |
| 10 | 닥터빌 세미나 입장(방송 중) | `seminar_live.py` | 운영 |
| 11 | 닥터빌 세미나 설문(종료 후) | `seminar_survey.py` | 운영 |
| 12 | 텔레그램 정답 수신·반영 | `telegram_inbox.py` | 운영 |
| 13 | 세미나 블록 결과 표 전송 | `seminar_report.py` | 운영 |
| — | 인터엠디 오늘의 퀴즈 | `intermd.py` | **수동 전용**(러너 IP 403) |

---

## 실행 아키텍처

| 워크플로우 | 트리거 | 실행 순서 |
|---|---|---|
| `daily.yml` | cron-job.org 00:15 KST(주) + GitHub cron `0 7 * * *`(16:00 KST 백스톱) | ① inbox fetch → ② 닥터빌(출석·퀴즈·**세미나 신청**) → ③ 키메디 → ④ HMP(캡슐·룰렛·댓글·글쓰기) → ⑤ 익일 퀴즈 사전 확인 (`daily_runner.py`) → 정답 커밋 |
| `seminar_block.yml` | cron-job.org → `workflow_dispatch` (11:00~14:30, 17:00~21:30 KST 30분 간격) | ① inbox fetch(11:00 런만) → ② 라이브 입장 → ③ 설문 → ④ 결과 표 전송. **신청은 하지 않는다** |
| `manual.yml` | `workflow_dispatch` 전용 | `task` 드롭다운 중 **하나만**, 항상 `--account all`. `seminar_block`과 같은 concurrency group |

- 각 스크립트는 결과 JSON 1건을 stdout에 출력 → `daily_runner.py`·알림 게이트가 파싱·취합·전송.
- 알림 여부는 `scripts/notify.py`가 `NOTIFY_LEVEL`(기본 `all` / `actionable`)로 판정.
- 결과 표 PNG는 `NOTIFY_LEVEL`과 무관하게 항상 전송. 렌더·전송 실패 시 `<pre>` 텍스트 표로 폴백.
- 서브프로세스는 `sys.executable`로 호출. **venv 절대경로 하드코딩 금지.**
- 실패 1건이라도 있으면 exit 1. CI는 `xvfb-run -a ... --headed`(헤드리스 실패 이력).

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
| `scripts/common.py` | 자격증명·계정 목록·스크린샷·`goto_with_retry`·`log_error` 등 공통 유틸 |
| `scripts/notify.py` | 중앙 알림 게이트 (severity 판정·메시지·Telegram 전송) |
| `scripts/recon.py` | 정찰 스크립트 (CLI R3/R4, `RECON=1` R1/R2) |
| `scripts/daily_runner.py` | daily 오케스트레이터 + 알림 필터링 |
| `scripts/runlog.py` | 실행 로그 적재(`logs/`) + 표 데이터 구성 + 표 전송 |
| `scripts/tablepng.py` | 표 HTML을 Playwright로 렌더해 PNG 저장 |
| `scripts/seminar_report.py` | 세미나 블록 결과 표 렌더·전송 |
| `quiz_answers.json` | 닥터빌 퀴즈 족보 `{제품명: {문항: 정답}}` |
| `quiz_answers_legacy.json` | 구형식 폴백 `{제품명: "111"}` |
| `intermd_answer.json` | 인터엠디 최신 정답 1건(덮어쓰기, 없으면 미생성) |
| `seminar_applied.json` | 세미나 신청 이력. 목록에 없는 새 세미나만 상세 조회 |
| `survey_quiz_answers.json` / `survey_text_answers.json` | 설문 퀴즈·주관식 족보 |
| `survey_answers_legacy.json` | 구 단일 족보. 조회 시 위 두 족보로 승격·제거 |
| `scripts/state/seminar_entered.json` | 세미나 입장·설문 이력 (State v2, Actions cache) |
| `credentials.json` | 로컬 전용(gitignore). CI는 `CREDENTIALS_JSON` secret |
| `scripts/logs/` | 실패 스크린샷·표 PNG (gitignore, artifact 7일) |
| `logs/daily-YYYY-MM-DD.json` | daily 실행 로그. 런마다 `run{N}` append. 행=run, 열=모듈 |
| `logs/seminar-YYYY-MM-DD.json` | 세미나 실행 로그. 행=세미나 |
| `logs/errors-YYYY-MM.jsonl` | **영구 오류 로그**(append-only, prune 대상 아님). 예외 클래스·메시지·트레이스백·스크린샷·GH run. **에이전트 진단용 — 텔레그램으로 보내지 않는다** |

Secrets: `CREDENTIALS_JSON`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.

---

## Claude의 역할

무인 실행이라 일상 개입은 없다. 개입 트리거는 텔레그램 알림뿐이다.

| 알림 | 대응 |
|---|---|
| `no_answer` | `quiz_answers.json`에 깔린 보기 중 정답만 남긴다 → `.claude/skills/answer-bank/SKILL.md` |
| `incomplete_bank` | 알림의 `bank` 값이 가리키는 설문 족보를 채운다 → 같은 스킬 |
| `failed` / `unverified` | `logs/errors-YYYY-MM.jsonl`(트레이스백) → Actions artifact 스크린샷 순으로 본다 |
| 연속 출석일이 10의 배수 근접 | 룰렛 수동 참여 안내 |
| 채팅으로 받은 정답 | `제품명 시퀀스` / `제품명 정답` 두 형식 모두 처리 → 같은 스킬 |

**정답·족보 반영은 확인 없이 바로 `main`에 merge·push한다** (경로 불문, 사용자 상시 지시).

### 금지
- 정답 추측 제출. 미등록이면 미시도(`no_answer` / `incomplete_bank`).
- 자동화가 막힌 항목을 "완료"로 표기.
- 비밀번호·토큰을 코드·문서에 기록.
- `verified_by`를 증거 없이 부여해 실패를 감추기.
- 브라우저에 넘기는 JS를 일반 문자열로 작성(`\n` 파손). **r-문자열 필수.**

---

## 상태값 및 Severity

| Severity | status | 의미 | 텔레그램(`actionable`) |
|---|---|---|---|
| `alert` | `failed`, `blocked`, `unverified` | 오류 / 긍정 증거 미비 강등 | ❌ / ⚠️ |
| `action` | `no_answer`, `incomplete_bank` | 사용자 개입 필요 | ❓ |
| `ok` | `success` (`verified_by` 동반) | 성공 확정 | 전송 안 함 |
| `quiet` | `already_done`(`verified_by` 동반), `skipped`, `no_target`, `not_ready`, `closed` | 완료·건너뜀·대상없음·마감 | 전송 안 함 |

**`success`·`already_done`에 `verified_by`가 없으면 `unverified`(`alert`)로 강등된다**
(`notify.NEEDS_EVIDENCE`, `runlog.status_of`가 공유). `already_done`이 포함된 이유:
`quiet`이라 조용히 넘어가는데, 사이트가 완료 표식 셀렉터를 바꾸면 며칠간 오판을 모른다.

`verified_by`의 `cache:` 접두사는 서버가 아니라 로컬 이력에 근거한 판정을 뜻한다
(`cache: seminar_applied.json skipped_known`, `cache: state.entered`). 서버 증거보다 약하다.

---

## 로컬 실행

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt && playwright install chromium && deactivate
venv/bin/pytest
venv/bin/python3 scripts/daily_runner.py --no-telegram --headed
venv/bin/python3 scripts/doctorville.py --account bjh7790 --task quiz --headed
venv/bin/python3 scripts/seminar_report.py --no-telegram
```

---

## 정책

- 개인정보 활용 동의: **항상 동의**(`button.btn_confirm`). 사용자 사전 승인 완료.
- 텔레그램 정답 실질 마감 24:00 KST — 낮에 받은 정답은 그날 자정 런에 반영된다.
- **CI는 워킹트리가 아니라 HEAD를 돌린다.** 동작이 옛날 코드 같으면 `git show HEAD:<파일>`부터 확인.
- 실행 로그는 레포에 커밋된다(러너는 런마다 새 체크아웃). `daily`/`seminar` 로그는 종류별 최근 7개만 유지(`runlog.prune`), **`errors-*.jsonl`은 삭제하지 않는다.**
- 정찰 산출물(`scripts/logs/recon_*`)은 개인정보 포함 — **커밋 금지**, artifact로만.
