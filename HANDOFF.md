# 핸드오프 — fuse-jev-gate (에이전트 파일 접근 게이트)
_업데이트: 2026-10-31 · 작성 세션: Phase 7을 마쳤다(`gate audit` 조회 명령, README, 평가 문서 정리). 브랜치 + PR #1로 올렸다_

## TL;DR (30초 복원)
- **프로젝트:** 일반인이 로컬에서 쓰는 AI 에이전트가 원본 문서를 게이트를 거쳐서만 읽게 하는 도구다. 게이트 순서는 경로 규칙 → 정규식 → Jev → 정책 → 사람 승인 → 감사 로그다.
- **방금 끝낸 것:** Phase 7(감사 로그 + 정리). 계획의 Phase 0~7이 모두 끝났다. Phase 3b(실제 로컬 Jev)만 보류다.
- **다음 첫 행동:** PR #1(https://github.com/Plant-Hackathon-2026/fuse-jev-gate/pull/1)이 머지됐는지 확인하고, 다음에 무엇을 할지 사용자에게 묻는다("다음 할 일" 1번).

## 목표
일반인이 자기 컴퓨터에서 AI 에이전트를 쓸 때, 에이전트가 파일을 읽으면 반드시 게이트를 거치게 한다. 게이트는 허용·마스킹·사람 승인·차단 중 하나로 응답한다. 처리 방식은 사용자가 정책 문장(또는 설정)으로 지정하고, 지정하지 않으면 기본값을 쓴다.

참고 문서:
- **README.md** — 사용자용 전체 설명(설치, 빠른 시작, Claude Code 연결, 정책, 승인, 감사 로그, 하네스, 한계). 이번에 새로 썼다.
- 원래 명세: `fuse-jev-gate-spec.md`(FUSE 구조).
- **현재 계획: `plans/implementation-plan.md`.** 명세와 다른 부분은 계획 문서를 따른다.
  - 2절 결정 표: Phase 7 결정은 "README (Phase 7)", "감사 로그 조회 (Phase 7)", "Phase 7 올리는 방식"
  - 5절 Phase 7 "결과"와 "거짓 안전 (Phase 7)", 6절 알려진 한계, 7절 검증
- 점검 페이지: https://claude.ai/artifact/AbemNcUnx4Uhj9d2B5jBhW (**Phase 6까지만 반영. Phase 7은 아직 넣지 않았다**)
- 구조 비교 페이지: https://claude.ai/artifact/9BXxvFJKDstWR977YUwD6V
- 저장소: https://github.com/Plant-Hackathon-2026/fuse-jev-gate (공개, 기본 브랜치 `main`)

## 현재 위치
- 방금 끝낸 Phase: Phase 7 — 감사 로그 조회 + README + 정리
- 다음: 계획에 남은 Phase는 없다. 보류 중인 Phase 3b와 확장 후보 중 무엇을 할지는 사용자가 정한다(Phase마다 멈추고 보고).

## 완료한 것 (이번 세션)
- **사용자 결정**(계획 2절): README는 한국어 하나, `gate audit`을 만든다, README에 구조 그림·빠른 시작·하네스·감사 로그 형식을 더한다, 브랜치 + PR로 올린다.
- **`gate audit`**(`gate/audit_cli.py`, `tests/test_audit_cli.py` 28개)
  - `-n`, `--blocked`, `--decision`, `--path`, `--since`, `--file`(content_hmac), `--json`. 읽기만 한다.
  - `gate/audit.py`에 `load_key`(읽기만, 없으면 만들지 않음)를 나눴다. `load_or_create_key`가 이를 쓴다.
  - `tests/test_integration.py::test_human_approval_end_to_end`에 실제 `gate audit --blocked --file` 확인을 더했다.
  - 실제 `gate-data/audit/audit.jsonl`(57건)에서도 돌려 봤다.
- **README.md**: 빠른 시작 출력은 scratchpad에 따로 띄운 데몬(Mock keyword, 기본 정책)으로 실제로 확인했다. 정책 문장 예시 4개도 해석기가 알아듣는지 확인했다. 그 데몬은 껐다.
- **정리**: `eval/README.md`의 옛 옵션(`--threshold`)과 "정책 결과 미리보기" 설명을 고쳤다. `pyproject.toml`에 설명과 README를 넣었다(`uv.lock` 변화 없음).
- **테스트:** 맥 909 통과·1 skip, Linux 컨테이너 910 통과(`fuse-jev-gate:phase7`).
- **git:** 브랜치 `feat/phase7-audit-readme`에 커밋 4개(기능, README, 계획 문서, 이 핸드오프). PR #1을 열었다. 머지는 사용자가 한다.

## 다음 할 일
1. **PR #1 상태 확인 후 다음 작업을 사용자에게 묻는다.**
   - `gh pr view 1 -R Plant-Hackathon-2026/fuse-jev-gate`로 머지 여부를 본다. 머지됐으면 `git checkout main && git pull`.
   - 후보(사용자가 고른다. 먼저 하나를 밀지 않는다):
     - 점검 페이지(artifact)에 Phase 7 반영
     - 하네스에 `gate audit` 시도를 하나 더해 에이전트 세션에서 막히는지 직접 확인(비용 약 $0.02, 실행 전 확인)
     - Phase 3b(실제 로컬 Jev) — 사용자가 백엔드를 정할 때까지 보류. **후보 조사를 먼저 제안하지 않는다.**
     - OS 수준 분리(원본을 별도 계정 소유로 두거나 암호화) — 6절 "다음 확장 후보"
2. 새 작업도 브랜치 + PR로 올린다(Phase 7 결정). 커밋과 PR 본문에 AI 흔적을 남기지 않는다.

## 핵심 결정 · 함정
- **진행 규칙**
  - 사용자는 Phase마다 멈추고 결과를 보고받은 뒤 다음으로 갈지 정한다.
  - 서브에이전트는 Opus 5.5로 띄운다. 병렬은 허락한 범위만 쓴다.
  - 응답과 문서는 한국어로 쓴다. 계획 문서는 `plans/`에 둔다.
  - **커밋 메시지와 PR 본문에 AI 흔적(Co-Authored-By, Generated with 등)을 남기지 않는다**(사용자 전역 지침).
- **git·GitHub**
  - 이 폴더(`fuse-jev-gate/`)가 저장소 루트다. 상위 `hackathon/`과 `gate-data/`는 저장소 밖이다.
  - gh 활성 계정은 `sangwoo-codyssey`다. 이 저장소에 로컬 `credential.https://github.com.username=sangwoo-codyssey`를 설정했다.
  - PR 본문 형식은 `📝 작업 내용` / `🔍 확인 사항`(이슈가 없어서 `📌 관련 이슈`는 뺐다).
- 확실하지 않은 것(Claude Code 설정 동작 등)은 공식 문서를 확인하거나 사용자에게 묻는다. 문서는 `curl -sSL https://code.claude.com/docs/en/<페이지>.md`로 받는다.
- **보호 범위**: 작업 폴더 세션은 작업 폴더 설정으로, 그 밖의 세션은 `gate setup`(실제 배포는 MDM 관리 설정)으로 막는다. 샌드박스 우회는 범위 밖.
- **`gate audit` 설계**
  - 사용자 터미널 전용 명령이다. 에이전트 세션은 감사 로그 폴더를 경로로 막아서 못 쓴다(추론, 아래 거짓 안전).
  - 깨진 줄은 건너뛰고 알린다. 엄격하게 읽는 `read_records`는 그대로 둔다(테스트용).
  - 표의 결정 열은 한글 폭(2칸)을 계산해 맞춘다(`_width`, `_pad`).
- **데모·시험 함정**
  - 맥 소켓 경로는 104바이트 제한이 있다. scratchpad 경로는 길어서, 데몬을 그 폴더에서 띄우고 상대 경로(`run/gate.sock`)를 썼다.
  - `gate policy`·`gate approve <id>`를 비대화형으로 시험할 때는 `script -q /dev/null gate …`로 가짜 터미널을 준다(보안 경계가 아니라는 점은 계획 4·5절).
  - Mock keyword는 가린 값(`***`)도 단서로 센다. 그래서 번호가 든 파일은 가림이 아니라 승인·차단으로 끝나고, 승인한 뒤에야 가린 내용이 보인다(README에 적었다).
  - 하네스 함정(Haiku 거절, `~/.local/bin/claude`, 동시 실행 금지 등)은 Phase 6 그대로다. 계획 5절 "6-3 결과".
- **거짓 안전**
  - 에이전트 세션의 `gate audit` 차단은 하네스로 돌리지 않았다. W5(감사 키 Bash 읽기)가 OS 샌드박스에 막힌 것과 같은 경로라서 막힐 것으로 본다.
  - 하네스의 `invalid`는 "막힘"이 아니다. R17(WebFetch `file://`)은 우리 설정이 막은 것이 아니다.
  - 작업 폴더 세션의 MCP 도구, 세션 간 메시지 도구는 막지 않았다(한계로 기록).
  - Jev와 정책 문장 해석기는 여전히 Mock이다.
- **테스트 함정:** 데몬 이벤트는 `tests/test_daemon.py`의 `settled()`로 기다린다. 사람 승인 테스트 때문에 전체가 약 20초 걸린다.

## 관련 파일
- `README.md` — 사용자용 문서(이번에 새로 씀)
- `plans/implementation-plan.md` — 2절 결정 표, 5절 Phase 7 결과, 6절 한계, 7절 검증
- `gate/audit_cli.py` — `gate audit`(`register`, `run`, `select`, `format_record`, `summary`, `parse_since`, `file_hmac`)
- `gate/audit.py` — 감사 로그 기록, `load_key`·`load_or_create_key`, `read_records`
- `gate/cli.py` — `audit` 연결
- `tests/test_audit_cli.py`, `tests/test_integration.py`
- `eval/README.md` — 평가 문서(옵션 설명을 고침)
- `hackathon/gate-data/agent-workspace/` — 실제 작업 폴더(생성기로 만듦. 손으로 고치지 말고 `gate workspace init --force`)

## 실행 · 검증
- **데몬**(`hackathon/`에서):
  ```
  GATE_SOCKET=$PWD/gate-data/run/gate.sock GATE_POLICY=$PWD/gate-data/policy.yaml GATE_AUDIT=$PWD/gate-data/audit/audit.jsonl \
    fuse-jev-gate/.venv/bin/gate serve --source gate-data/source [--no-dialog]
  ```
- 떠 있는지 확인: `GATE_SOCKET=…/gate-data/run/gate.sock fuse-jev-gate/.venv/bin/gate ls /`
- 감사 로그 보기: `GATE_AUDIT=$PWD/gate-data/audit/audit.jsonl fuse-jev-gate/.venv/bin/gate audit [--blocked]`
- 테스트(맥): `cd fuse-jev-gate && uv run pytest` — **909 통과, 1 skip(FUSE)**, 약 20초
- 테스트(Linux): `docker build -f sandbox/gate.Dockerfile -t fuse-jev-gate:phase7 . && docker run --rm --device /dev/fuse --cap-add SYS_ADMIN fuse-jev-gate:phase7 pytest -q` — **910 통과**
- 하네스: 계획 문서 7절 또는 README "우회 시험 하네스"(비용이 든다)
- 인프라·런타임 상태
  - 이전 세션이 띄운 데몬(PID 63433, 대화상자 켬, 감사 로그 켬)이 이 세션 끝까지 떠 있었다. 새 세션에서 확인하고 필요하면 다시 띄운다.
  - Docker Desktop이 켜져 있다(Linux 테스트용).
  - `gate-data/`에는 `source/`, `run/`, `audit/`, `agent-workspace/`, `plain-project/`가 있다. `policy.yaml`은 없다(기본 정책).
  - Claude Code 2.1.316.

## 미해결 · 블로커
1. **PR #1 머지:** 사용자가 리뷰하고 머지한다.
2. **다음 작업:** 계획의 Phase는 모두 끝났다. 무엇을 할지 사용자가 정한다("다음 할 일" 1번 후보).
3. **Jev 백엔드:** 로컬 실행으로 정했다(사용자 결정). 실제 백엔드는 나중에 정하고, 그때까지 Phase 3b는 보류다.
4. **애매한 라벨:** 진료 메모 quasi는 `sensitive`로, pii는 마스킹 뒤 이름만 남는 것으로 둔다(기본 기준 유지).
