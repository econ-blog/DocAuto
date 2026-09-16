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
| 14 | 장애·미기입 시 Claude 세션 발사 | `claude_trigger.py` | 운영 |
| — | 인터엠디 오늘의 퀴즈 | `intermd.py` | **수동 전용**(러너 IP 403) |

라이브러리 모듈(`seminar_state`·`seminar_applied`·`quiz_bank`·`survey_window`·`survey_bank`·`survey_detail`)은 파일 맵 참조.

---

## 실행 아키텍처

| 워크플로우 | 트리거 | 실행 순서 |
|---|---|---|
| `daily.yml` | cron-job.org 00:15 KST(주) + GitHub cron `0 7 * * *`(16:00 KST 백스톱) | ① inbox fetch → ② 닥터빌(출석·퀴즈·**세미나 신청**) → ③ 키메디 → ④ HMP(캡슐·룰렛·댓글·글쓰기) → ⑤ 익일 퀴즈 사전 확인 (`daily_runner.py`) → ⑥ 정답 커밋 → ⑦ Claude Cloud Routine 트리거(`claude_trigger.py`) |
| `seminar_block.yml` | cron-job.org → `workflow_dispatch` (11:00~14:30, 17:00~21:30 KST 30분 간격) | ① inbox fetch(11:00 런만) → ② 라이브 입장 → ③ 설문 → ④ 세미나 결과 표 전송 → ⑤ Claude Cloud Routine 트리거(`claude_trigger.py`). **신청은 하지 않는다** |
| `manual.yml` | `workflow_dispatch` 전용 | `task` 드롭다운 중 **하나만**, 항상 `--account all` → Claude Cloud Routine 트리거(`claude_trigger.py`). `seminar_block`과 같은 concurrency group |

- 각 스크립트는 결과 JSON 1건을 stdout 및 `scripts/logs/results-*.json`에 atomic하게 출력.
- **텔레그램 알림은 결과 표 PNG 2종(일일 자동화 표, 세미나 블록 표)만 전송.** 텍스트 요약 발송 및 `NOTIFY_LEVEL`은 전면 폐지됨.
- 표 렌더·전송 실패 시 `<pre>` 텍스트 표로 폴백.
- 서브프로세스는 `sys.executable`로 호출. **venv 절대경로 하드코딩 금지.**
- 실패 1건이라도 있으면 exit 1. CI는 `xvfb-run -a ... --headed`(헤드리스 실패 이력).
- **pytest 게이트는 비차단이다**(`daily`·`seminar_block` 공통). 테스트가 깨져도 자동화는 돌고,
  `scripts/warn_tests.py`가 텔레그램으로 ⚠️ 경고를 보낸다. 테스트 버그 하나가 출석 연속일을
  끊는 것을 막기 위한 구조다(2026-09-16 00:15 런 전면 중단).
- 장애 발생 시 `claude_trigger.py`가 Claude Cloud Routine 세션을 트리거하여 자동 복구/족보 입력을 수행.

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
| `scripts/common.py` | 자격증명(`load_credentials`)·계정 목록·스크린샷·`goto_with_retry`·`log_error` 등 공통 유틸 |
| `scripts/notify.py` | severity 판정(`severity_of`·`NEEDS_EVIDENCE`) + 텔레그램 전송 유틸 |
| `scripts/recon.py` | 정찰 스크립트 (CLI R3·R4·R5, `RECON=1` R1/R2) |
| `scripts/daily_runner.py` | daily 오케스트레이터 |
| `scripts/runlog.py` | 실행 로그 적재(`logs/`) + 표 데이터 구성 + `log_seminar` 통합 로깅 |
| `scripts/tablepng.py` | 표 HTML을 Playwright로 렌더해 PNG 저장 |
| `scripts/seminar_report.py` | 세미나 블록 결과 표 렌더·전송 |
| `scripts/seminar_state.py` | 세미나 입장·설문 상태 v2 로드·저장·병합 (`state/seminar_entered.json`) |
| `scripts/seminar_applied.py` | 세미나 신청 이력 로드·저장·prune (`seminar_applied.json`) |
| `scripts/quiz_bank.py` | 닥터빌 퀴즈 족보 조회·정규화·기록·삭제 (Playwright 없음) |
| `scripts/survey_window.py` | 세미나 설문 창·마감 평가 순수 로직 (Playwright 없음) |
| `scripts/survey_bank.py` | 세미나 설문 문제은행 정규화·canonical 조회·승격 (Playwright 없음) |
| `scripts/survey_detail.py` | 세미나 상세 페이지 프로브 및 설문 완료 마커 확인 |
| `scripts/claude_trigger.py` | Claude Cloud Routine webhook 발사 및 디듀플리케이션 |
| `scripts/warn_tests.py` | pytest 실패를 텔레그램 ⚠️ 경고로 알린다(비차단 게이트의 유일한 신호) |
| `scripts/bank_pending.py` | 족보 미기입 항목 조회 (`--bank`, `--json`, Playwright 없이 실행) |
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
| `logs/errors-YYYY-MM.jsonl` | **영구 오류 로그**(append-only, prune 대상 아님). 예외 클래스·메시지·트레이스백·스크린샷·GH run |
| `logs/claude-triggers-YYYY-MM.jsonl` | **영구 Routine 트리거 로그**(append-only, prune 대상 아님). 세션 발사 이력 및 URL |

Secrets: `CREDENTIALS_JSON`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `CLAUDE_ROUTINE_URL`, `CLAUDE_ROUTINE_TOKEN`.

---

## Claude의 역할

무인 실행이라 일상 개입은 없다. 개입 트리거는 러너 장애 시 `claude_trigger.py`가 호출하는 **Claude Cloud Routine 세션**(`.claude/skills/maintenance/SKILL.md`)이다.

| 트리거 원인 | 대응 |
|---|---|
| `no_answer` | `quiz_answers.json`에 깔린 보기 중 정답만 남긴다 → `.claude/skills/answer-bank/SKILL.md` |
| `incomplete_bank` | payload의 `bank` 값이 가리키는 설문 족보를 채운다 → 같은 스킬 |
| `failed` / `unverified` | `logs/errors-YYYY-MM.jsonl`(트레이스백) → Actions artifact 스크린샷 순으로 보고 결함 수정 브랜치 생성 |
| 채팅으로 받은 정답 | `제품명 시퀀스` / `제품명 정답` 두 형식 모두 처리 → 같은 스킬 |

**정답·족보 반영은 확인 없이 바로 `main`에 merge·push한다** (경로 불문, 사용자 상시 지시).
**코드 결함 수정은 PR을 생성하여 사용자의 머지를 받는다.**

### 금지
- 자동화가 막힌 항목을 "완료"로 표기.
- 비밀번호·토큰을 코드·문서에 기록.
- `verified_by`를 증거 없이 부여해 실패를 감추기.
- 브라우저에 넘기는 JS를 일반 문자열로 작성(`\n` 파손). **r-문자열 필수.**

---

## 상태값 및 Severity

| Severity | status | 의미 | 세션 트리거 (`claude_trigger`) |
|---|---|---|---|
| `alert` | `failed`, `blocked`, `unverified` | 오류 / 긍정 증거 미비 강등 | 발사 (재시도성은 당일 2회차부터, 일일 최대 4회) |
| `action` | `no_answer`, `incomplete_bank` | 족보 미기입 — 세션이 채운다 | 발사 (`answer-bank` 스킬) |
| `ok` | `success` (`verified_by` 동반) | 성공 확정 | 발사 안 함 |
| `quiet` | `already_done`(`verified_by` 동반), `skipped`, `no_target`, `not_ready`, `closed` | 완료·건너뜀·대상없음·마감 | 발사 안 함 |

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
python3 scripts/bank_pending.py          # 미기입 족보 항목 (있으면 exit 1)
```

---

## 정책

- 개인정보 활용 동의: **항상 동의**(`button.btn_confirm`). 사용자 사전 승인 완료.
- 텔레그램 정답 실질 마감 24:00 KST — 낮에 받은 정답은 그날 자정 런에 반영된다.
- **CI는 워킹트리가 아니라 HEAD를 돌린다.** 동작이 옛날 코드 같으면 `git show HEAD:<파일>`부터 확인.
- 실행 로그는 레포에 커밋된다(러너는 런마다 새 체크아웃). `daily`/`seminar` 로그는 종류별 최근 7개만 유지(`runlog.prune`), **`errors-*.jsonl`은 삭제하지 않는다.**
- 정찰 산출물(`scripts/logs/recon_*`)은 개인정보 포함 — **커밋 금지**, artifact로만.
