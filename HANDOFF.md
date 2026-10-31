# 핸드오프 — fuse-jev-gate (에이전트 파일 접근 게이트)
_업데이트: 2026-10-31 · 작성 세션: Phase 6을 마쳤다(하네스 전체 실행, 설치 명령 `gate setup`, 사용자 직접 데모, Linux 테스트, 문서). 팀 조직에 공개 저장소로 올렸다_

## TL;DR (30초 복원)
- **프로젝트:** 일반인이 로컬에서 쓰는 AI 에이전트가 원본 문서를 게이트를 거쳐서만 읽게 하는 도구다. 게이트 순서는 경로 규칙 → 정규식 → Jev → 정책 → 사람 승인이다.
- **방금 끝낸 것:** Phase 6(에이전트 격리 + 우회 테스트). 우회 시험에서 뚫린 것은 없다.
- **다음 첫 행동:** Phase 7(README + 정리). 사용자에게 진행을 확인받고 시작한다("다음 할 일" 1번).

## 목표
일반인이 자기 컴퓨터에서 AI 에이전트를 쓸 때, 에이전트가 파일을 읽으면 반드시 게이트를 거치게 한다. 게이트는 허용·마스킹·사람 승인·차단 중 하나로 응답한다. 처리 방식은 사용자가 정책 문장(또는 설정)으로 지정하고, 지정하지 않으면 기본값을 쓴다.

참고 문서:
- 원래 명세: `fuse-jev-gate-spec.md`(FUSE 구조).
- **현재 계획: `plans/implementation-plan.md`.** 명세와 다른 부분은 계획 문서를 따른다.
  - 2절 결정 표: Phase 6 결정은 "Phase 6 데모 방식"부터 "작업 폴더 설정 형식"까지. 이번에 "설치 명령 범위" 행을 더했다
  - 5절 Phase 6: "설정 형식 확인"과 **"6-3 결과"**(막은 층, 하네스 수정, 거짓 안전)
  - 6절 알려진 한계, 7절 검증(하네스 실행 명령 포함)
- Phase 6 진행 방식·하네스 지시서: `plans/phase6-parallel.md`
- 점검 페이지(Phase 6 완료 반영): https://claude.ai/artifact/AbemNcUnx4Uhj9d2B5jBhW
- 구조 비교 페이지: https://claude.ai/artifact/9BXxvFJKDstWR977YUwD6V
- **저장소: https://github.com/Plant-Hackathon-2026/fuse-jev-gate** (공개, 기본 브랜치 `main`)

## 현재 위치
- 방금 끝낸 Phase: Phase 6 — 에이전트 격리 + 우회 테스트
- 다음 Phase: Phase 7 — README + 정리. 사용자가 진행을 정한다(Phase마다 멈추고 보고).

## 완료한 것 (이번 세션)
- **하네스 판정 고침**(`sandbox/bypass.py`)
  - 세션에 없는 도구(2.1.316의 Grep·Glob)를 부르면 invalid("이 버전에는 도구가 없음")로 센다.
  - Claude Code가 `script`를 "실행 전에 검사할 수 없다"며 사람에게 묻다 헤드리스라 거부한 경우도 invalid로 센다. 처음 전체 실행에서 W3·A2를 blocked로 잘못 셌다.
  - A2는 `gate approve`를 바로 부른다. W3는 python `pty`로 가짜 터미널을 만든다.
  - 근거 줄은 막은 이유가 드러나는 줄을 고른다(zsh `nice(5)` 경고는 건너뜀).
  - auto 분류기 거부는 "auto 분류기(우리 설정 아님)" 층으로 적는다.
  - `--settings` 옵션을 더했다(전역 설정 시험).
- **하네스 실행.** 결과는 계획 5절 "6-3 결과". 실행 7번, 합계 약 $0.57.
  - 작업 폴더: default 40가지, auto 쓰기 5가지, 고친 시도 다시 실행. 뚫린 것 0.
  - 전역 설정: `gate-data/plain-project`(빈 폴더)에서 `--settings`로 29가지. 뚫린 것 0.
- **설치 명령 `gate setup`**(`gate/claude_setup.py`, `tests/test_claude_setup.py` 17개)
  - 공통 막기 규칙 `gate.workspace.protection_settings`를 사용자 설정에 합친다.
  - 바뀔 내용을 보여 주고 y/N으로 확인한다. 백업과 기록 파일을 남기고, `--undo`로 되돌리고, 다시 실행해도 결과가 같다.
  - `--print`는 MDM용 JSON만 낸다. `--settings-file`로 대상을 바꾼다.
  - 이 맥의 `~/.claude/settings.json`은 바꾸지 않았다.
- **사용자 결정**(계획 2절 "설치 명령 범위")
  - bypass 금지와 설정 파일 Edit 금지는 넣지 않는다.
  - 샌드박스 우회는 다루지 않는다. 실제 배포는 MDM 관리 설정으로 한다고 가정한다(관리 설정은 아래 설정이 덮어쓰지 못함, 공식 문서 확인).
- **사용자 직접 데모**
  - 작업 폴더에서 `claude`를 띄워 `/sandbox`(1번 auto-allow, 샌드박스 켜짐)를 확인했다.
  - `gate`로 상담 기록을 읽었다. 데몬 로그에 `ls`·`cat`이 남았다.
  - 직접 읽기 시도를 했는지는 사용자가 따로 말하지 않았다.
- **테스트:** 맥 881 통과·1 skip, Linux 컨테이너 882 통과(`fuse-jev-gate:phase6`).
- **문서:** 계획 2·5·6·7절, `plans/phase6-parallel.md`, 점검 페이지, 메모리를 갱신했다.
- **저장소:** `git init` 뒤 첫 커밋 하나로 `Plant-Hackathon-2026/fuse-jev-gate`(공개)에 올렸다.

## 다음 할 일
1. **Phase 7 시작 전 사용자 확인.** 계획 5절 Phase 7을 보여 주고 범위를 묻는다.
   - README 범위: 설치(`uv`), `gate setup`, 데몬 실행, 정책(`gate policy`), 작업 폴더 설정(`gate workspace init`), 하네스, 알려진 한계.
   - 감사 로그 조회 명령을 만들지.
   - 공개 저장소라서 README를 영어로도 쓸지, 한국어만 쓸지.
2. Phase 7을 진행하고 보고한 뒤 멈춘다. 커밋은 브랜치 + PR로 할지 사용자에게 묻는다. 팀 조직 저장소라 가리개처럼 GitHub Flow일 수 있다.
3. Phase 3b(실제 로컬 Jev)는 사용자가 백엔드를 정할 때까지 보류다. 후보 조사를 먼저 제안하지 않는다.

## 핵심 결정 · 함정
- **진행 규칙**
  - 사용자는 Phase마다 멈추고 결과를 보고받은 뒤 다음으로 갈지 정한다.
  - 서브에이전트는 Opus 5.5로 띄운다. 병렬은 허락한 범위만 쓴다.
  - 응답과 문서는 한국어로 쓴다. 계획 문서는 `plans/`에 둔다.
  - **커밋 메시지에 AI 흔적(Co-Authored-By 등)을 남기지 않는다**(사용자 전역 지침).
- **git·GitHub**
  - 이 폴더(`fuse-jev-gate/`)가 저장소 루트다. 상위 `hackathon/`과 `gate-data/`는 저장소 밖이다.
  - gh 활성 계정은 `sangwoo-codyssey`다. 전역 `.gitconfig`는 다른 계정을 쓰게 되어 있어서, 이 저장소에 로컬 `credential.https://github.com.username=sangwoo-codyssey`를 설정했다(가리개와 같은 방식).
- 확실하지 않은 것(Claude Code 설정 동작 등)은 공식 문서를 확인하거나 사용자에게 묻는다. 문서는 `curl -sSL https://code.claude.com/docs/en/<페이지>.md`로 받는다. 쓴 페이지: `sandboxing`, `permissions`, `settings`, `settings-reference`, `managed-settings`, `env-vars`.
- **보호 범위**
  - 작업 폴더 세션은 작업 폴더 설정으로 막는다. 그 밖의 세션은 `gate setup`으로 합친 사용자 설정(실제 배포는 MDM 관리 설정)으로 막는다.
  - 다른 프로그램을 모두 막으려면 OS 수준 분리(별도 계정·암호화)가 필요하다. 다음 확장 후보다.
- **하네스 함정**
  - 모델(Haiku)이 우회 명령을 거절하곤 한다. 그러면 `Attempt.why`에 목적을 적는다(R13·R15에 있음).
  - `claude`는 cmux 래퍼가 먼저 잡힌다. 하네스는 `~/.local/bin/claude`를 쓴다.
  - 하네스는 두 개를 동시에 돌리지 않는다(`bypass-tmp-*`와 정리가 겹친다).
  - 실행 중에 `gate/`를 고치면 시도 안의 `gate` CLI가 영향받는다.
  - Claude Code가 자동 업데이트되면 도구 목록이 바뀔 수 있다. 보고서에 버전이 남는다.
- **거짓 안전**
  - 하네스의 `invalid`는 "막힘"이 아니다.
  - W3는 정책 파일 쓰기까지 가지 못했다. 샌드박스 안에서 pty를 못 만든다(정책 파일 쓰기 차단은 W1이 보인다).
  - R17(WebFetch `file://`)은 우리 설정이 아니라 사람 확인이나 auto 분류기가 막는다.
  - 원본 폴더 `stat`은 샌드박스 안에서도 보인다.
  - 작업 폴더 세션의 MCP 도구, 세션 간 메시지 도구, `RemoteTrigger`·`CronCreate`는 막지 않았다(한계로 기록).
  - Linux는 Unix 소켓을 경로별로 열 수 없다.
  - Jev는 여전히 Mock이다.
- **테스트 함정:** 데몬 이벤트는 `tests/test_daemon.py`의 `settled()`로 기다린다. 맥 소켓 경로는 104바이트 제한이 있다. 사람 승인 테스트 때문에 전체가 약 20초 걸린다.

## 관련 파일
- `plans/implementation-plan.md` — 현재 계획. 2절 결정 표, 5절 Phase 6 "6-3 결과", 6절 한계, 7절 검증
- `gate/claude_setup.py` — 설치 명령(`merge`, `revert`, `install`, `undo`, CLI `run`)
- `gate/workspace.py` — `protection_settings`(공통 막기 규칙), `agent_settings`(작업 폴더 전용 규칙 추가), `init_workspace`
- `gate/cli.py` — `setup`·`workspace` 연결
- `sandbox/bypass.py` — 우회 시험 하네스(`ATTEMPTS`, `judge`, `_designated`, `preflight`, `--settings`)
- `tests/test_claude_setup.py`, `tests/test_bypass.py`, `tests/test_workspace.py`
- `hackathon/gate-data/agent-workspace/` — 실제 작업 폴더(생성기로 만듦. 손으로 고치지 말고 `gate workspace init --force`)
- `hackathon/gate-data/plain-project/` — 전역 설정 시험용 빈 폴더(프로젝트 설정을 두지 않는다)

## 실행 · 검증
- **데몬**(`hackathon/`에서). 하네스는 `--no-dialog`로 돌렸고, 사용자 데모는 대화상자를 켠 상태로 했다.
  ```
  GATE_SOCKET=$PWD/gate-data/run/gate.sock GATE_POLICY=$PWD/gate-data/policy.yaml GATE_AUDIT=$PWD/gate-data/audit/audit.jsonl \
    fuse-jev-gate/.venv/bin/gate serve --source gate-data/source [--no-dialog]
  ```
- 떠 있는지 확인: `GATE_SOCKET=…/gate-data/run/gate.sock fuse-jev-gate/.venv/bin/gate ls /`
- 설치 명령 시험(이 맥의 전역 설정은 바꾸지 않는다. `hackathon/`에서, 위 환경변수와 함께):
  `fuse-jev-gate/.venv/bin/gate setup --source gate-data/source --settings-file <시험 파일> --yes` (되돌리기 `--undo --yes`, MDM용 `--print`)
- 하네스: 계획 문서 7절의 명령(작업 폴더 default·auto, 전역 설정 `--settings`)
- 테스트(맥): `cd fuse-jev-gate && uv run pytest` — **881 통과, 1 skip(FUSE)**, 약 20초
- 테스트(Linux): `docker build -f sandbox/gate.Dockerfile -t fuse-jev-gate:phase6 . && docker run --rm --device /dev/fuse --cap-add SYS_ADMIN fuse-jev-gate:phase6 pytest -q` — **882 통과**
- 인프라·런타임 상태
  - 데몬은 이 세션이 백그라운드로 띄웠다(대화상자 켬, 감사 로그 켬). 세션이 끝나면 꺼질 수 있으니 새 세션에서 확인하고 다시 띄운다.
  - Docker Desktop은 Linux 테스트를 위해 이 세션에서 켰다.
  - `gate-data/`에는 `source/`, `run/`, `audit/`, `agent-workspace/`, `plain-project/`가 있다. `policy.yaml`은 없다(기본 정책).
  - Claude Code 2.1.316.

## 미해결 · 블로커
1. **Phase 7 범위:** README 언어, 감사 로그 조회 명령을 만들지, 커밋 방식(main 직접 또는 PR). 시작 전에 사용자에게 묻는다.
2. **Jev 백엔드:** 로컬 실행으로 정했다(사용자 결정). 실제 백엔드는 나중에 정하고, 그때까지 Phase 3b는 보류다.
3. **애매한 라벨:** 진료 메모 quasi는 `sensitive`로, pii는 마스킹 뒤 이름만 남는 것으로 둔다(기본 기준 유지). Mock이라 사용자 맥락 규칙은 아직 효과가 없다.
