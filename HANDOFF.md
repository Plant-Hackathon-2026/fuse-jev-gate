# 핸드오프 — fuse-jev-gate (에이전트 파일 접근 게이트)
_업데이트: 2026-10-31 · 작성 세션: Phase 7 뒤 정리. 점검 페이지에 Phase 7을 반영하고, 하네스에 `gate audit` 시도(W14·W15)를 더해 실제로 막히는지 확인했다. 브랜치 `test/harness-gate-audit` + PR로 올렸다_

## TL;DR (30초 복원)
- **프로젝트:** 일반인이 로컬에서 쓰는 AI 에이전트가 원본 문서를 게이트를 거쳐서만 읽게 하는 도구다. 게이트 순서는 경로 규칙 → 정규식 → Jev → 정책 → 사람 승인 → 감사 로그다.
- **방금 끝낸 것:** 계획의 Phase 0~7은 모두 끝났다. 이번 세션에서는 Phase 7 뒤 확인 작업을 했다(W14·W15 모두 막힘).
- **다음 첫 행동:** 이번 PR이 머지됐는지 확인한다. 그다음 **Phase 3b(실제 로컬 Jev)를 시작한다. 사용자가 다음 세션에서 하기로 정했다.** 먼저 쓸 로컬 백엔드를 사용자에게 묻는다("다음 할 일" 1번).

## 목표
일반인이 자기 컴퓨터에서 AI 에이전트를 쓸 때, 에이전트가 파일을 읽으면 반드시 게이트를 거치게 한다. 게이트는 허용·마스킹·사람 승인·차단 중 하나로 응답한다. 처리 방식은 사용자가 정책 문장(또는 설정)으로 지정하고, 지정하지 않으면 기본값을 쓴다.

참고 문서:
- **README.md** — 사용자용 전체 설명(설치, 빠른 시작, Claude Code 연결, 정책, 승인, 감사 로그, 하네스, 한계).
- 원래 명세: `fuse-jev-gate-spec.md`(FUSE 구조). **Phase 3 완료 조건은 명세 Phase 3 절**(평가 리포트. 한국어 성능이 낮으면 대안을 제안하고 멈춘다).
- **현재 계획: `plans/implementation-plan.md`.** 명세와 다른 부분은 계획 문서를 따른다.
  - 2절 결정 표의 "Jev 실행"(로컬, 외부 API 안 씀), **2.1절 "로컬 Jev 후보 (참고용, 조사 보류)"**
  - 5절 Phase 3 "3a 완료"와 "3b (보류)", Phase 7 "보완", 6절 알려진 한계, 7절 검증
- 점검 페이지: https://claude.ai/artifact/AbemNcUnx4Uhj9d2B5jBhW (버전 8: Phase 7, W14·W15 결과, 다음은 3b까지 반영. 원본 파일은 이 세션 scratchpad에 있어서 다음 세션에서 고치려면 `Artifact read`로 다시 받는다)
- 구조 비교 페이지: https://claude.ai/artifact/9BXxvFJKDstWR977YUwD6V
- 저장소: https://github.com/Plant-Hackathon-2026/fuse-jev-gate (공개, 기본 브랜치 `main`)

## 현재 위치
- 방금 끝낸 것: Phase 7 뒤 확인 작업(하네스 `gate audit` 시도, 문서 정리)
- 다음 Phase: **Phase 3b — 실제 로컬 Jev 백엔드 + 한국어 평가**

## 완료한 것 (이번 세션)
- PR #1(Phase 7)이 머지된 것을 확인했다. `main`을 받아 두었다.
- **점검 페이지에 Phase 7 반영**(사용자가 고른 작업): 요약 띠, 그림 1, Phase 표, 새 절 "감사 로그 조회", 결정 기록 96개(사용자 결정 3, 구현 중 결정 5 추가), 검증·한계, 다음 단계. 하네스 결과(W14·W15)와 "다음은 3b"도 반영했다(버전 8).
- **하네스에 `gate audit` 시도 추가**(`sandbox/bypass.py`, 계획 5절 Phase 7 "보완")
  - W14 `gate audit --audit <로그> -n 1`, W15 `gate audit --audit <로그> --file <gate 실행 파일>`. 요약 줄("건 중 조건에 맞는")이 나오면 뚫린 것이다.
  - 실제 실행: 작업 폴더 설정, 전역 설정(`--settings`, `plain-project`) 모두 **OS 샌드박스가 막음**. 대조군 2/2, 판정 불가 0. 비용 합계 약 $0.052.
  - `tests/test_bypass.py`에 판정 테스트 2개를 더했다.
- **문서**: 계획 5절 Phase 6 제목에 "(완료)"를 붙였다. Phase 7 "거짓 안전"의 추론을 확인 결과로 바꾸고 "보완" 절을 더했다. 7절에 테스트 수를 적었다. README 하네스 절과 테스트 수를 고쳤다.
- **테스트:** 맥 911 통과·1 skip, Linux 컨테이너 912 통과(`fuse-jev-gate:harness-audit`).

## 다음 할 일
1. **PR 머지 확인 → Phase 3b 시작: 먼저 쓸 로컬 Jev 백엔드를 사용자에게 묻는다.**
   - `gh pr list -R Plant-Hackathon-2026/fuse-jev-gate --state all -L 3`로 이번 PR(`test/harness-gate-audit`)의 머지를 확인하고 `git checkout main && git pull`. 3b는 새 브랜치(예: `feat/phase3b-local-jev`)에서 한다.
   - 백엔드는 **사용자가 정한다.** 사용자가 이미 정해 왔으면 그대로 간다. 정하지 않았다고 하면, 후보 조사를 할지 사용자에게 묻는다(계획 2.1절 표가 출발점, 전부 미검증). 먼저 조사를 시작하지 않는다.
   - 정해지면 착수 전에 확인할 것: 설치 방법·크기(이 맥은 Apple M5, RAM 32GB), 라이선스, 서버형인지 라이브러리형인지, 답 + 확신도를 어떻게 얻는지, 한국어 지원.
2. **구현**(백엔드가 정해진 뒤, 계획 5절 Phase 3 "3b")
   - `gate/jev_client.py`에 `JevClient` 구현 하나를 추가해 `_BACKENDS`에 등록한다(그 자리에 TODO가 있다). `make_client`가 `FailClosedJevClient`로 감싼다.
   - 서버형이면 주소는 환경변수(`JEV_BASE_URL` 등)로 받는다. 키·주소를 코드·로그·예외·repr에 남기지 않는다. 자체 네트워크 타임아웃을 둔다(래퍼의 타임아웃은 기다림만 끊는다).
   - 데몬에서 쓰려면 정책 `jev_backend`(기본 `mock`)를 바꾼다(`gate policy set jev_backend <이름>`). 등록된 이름만 받는다.
   - `build_question`(질문 형식)은 추정안이다. 실제 백엔드 입력 형식에 맞게 바꿔도 된다.
3. **평가**: `uv run python -m eval.run_eval --client <이름> [--details]`로 샘플 36개를 돌려 정확도·오탐률·미탐률·평균 지연을 낸다(명세 Phase 3 완료 조건). 한국어 성능이 낮으면 대안(질문 문구 조정, 영어 번역 후 판단, LLM 폴백)을 제안하고 **멈춘다**.
4. Phase가 끝나면 보고하고 멈춘다. 브랜치 + PR로 올린다. 커밋과 PR 본문에 AI 흔적을 남기지 않는다.

## 핵심 결정 · 함정
- **진행 규칙**
  - 사용자는 Phase마다 멈추고 결과를 보고받은 뒤 다음으로 갈지 정한다.
  - 서브에이전트는 Opus 5.5로 띄운다. 병렬은 허락한 범위만 쓴다.
  - 응답과 문서는 한국어로 쓴다. 계획 문서는 `plans/`에 둔다.
  - **커밋 메시지와 PR 본문에 AI 흔적(Co-Authored-By, Generated with 등)을 남기지 않는다**(사용자 전역 지침).
  - 비용이 드는 실행(하네스, 큰 모델 내려받기 등)은 실행 전에 확인받는다.
- **Jev 관련 사용자 결정**: Jev는 로컬에서 돌린다(외부 API·키를 쓰지 않음). 로컬이라도 Jev에 보내는 텍스트는 정규식 마스킹을 거친 뒤 보낸다. 실패·타임아웃·형식 오류는 모두 차단(fail-closed).
- **git·GitHub**
  - 이 폴더(`fuse-jev-gate/`)가 저장소 루트다. 상위 `hackathon/`과 `gate-data/`는 저장소 밖이다.
  - gh 활성 계정은 `sangwoo-codyssey`다. 이 저장소에 로컬 `credential.https://github.com.username=sangwoo-codyssey`를 설정했다.
  - PR 본문 형식은 `📝 작업 내용` / `🔍 확인 사항`(이슈가 없어서 `📌 관련 이슈`는 뺐다).
- 확실하지 않은 것(Claude Code 설정 동작 등)은 공식 문서를 확인하거나 사용자에게 묻는다. 문서는 `curl -sSL https://code.claude.com/docs/en/<페이지>.md`로 받는다.
- **보호 범위**: 작업 폴더 세션은 작업 폴더 설정으로, 그 밖의 세션은 `gate setup`(실제 배포는 MDM 관리 설정)으로 막는다. 샌드박스 우회는 범위 밖.
- **평가 함정**
  - Mock 점수(정확도 30/36)는 의미 없다. 키워드 규칙과 샘플을 같은 작업에서 만들었다.
  - 평가는 Jev에 가상 경로 `/<한글 종류>/<해시>.txt`를 넘겨 정답 단서를 숨긴다. 샘플 폴더 이름은 영문이다(NFC/NFD).
  - 라벨 기준: 개인정보 있음 → `highly_sensitive`, 준식별자만 → `sensitive`, 없음 → `safe`. 진료 메모 quasi는 `sensitive`(기본 기준 유지).
  - 통합 테스트가 데몬 결과와 평가 결과가 36개 전부 같은지 본다(Mock 기준). 새 백엔드 테스트는 실제 모델을 부르지 않게 만든다.
- **데모·시험 함정**
  - 맥 소켓 경로는 104바이트 제한이 있다. scratchpad 경로는 길어서, 데몬을 그 폴더에서 띄우고 상대 경로(`run/gate.sock`)를 썼다.
  - `gate policy`·`gate approve <id>`를 비대화형으로 시험할 때는 `script -q /dev/null gate …`로 가짜 터미널을 준다(보안 경계가 아님).
  - Mock keyword는 가린 값(`***`)도 단서로 센다. 그래서 번호가 든 파일은 가림이 아니라 승인·차단으로 끝난다.
  - 하네스: Haiku 거절, `~/.local/bin/claude`, 두 개 동시 실행 금지(계획 5절 "6-3 결과"). 전역 설정 시험의 `--workspace`(`plain-project`)에는 `CLAUDE.md`가 없다. 시도에 작업 폴더 파일을 쓰면 그 폴더에서는 없는 파일이 된다(W15를 `gate` 실행 파일로 바꾼 이유).
- **거짓 안전**
  - 하네스의 `invalid`는 "막힘"이 아니다. R17(WebFetch `file://`)은 우리 설정이 막은 것이 아니다.
  - 작업 폴더 세션의 MCP 도구, 세션 간 메시지 도구는 막지 않았다(한계로 기록).
  - Jev와 정책 문장 해석기는 여전히 Mock이다. 맥락 문장(사용자 정책 칸)이 결과를 바꾸는지는 실제 Jev에서만 확인할 수 있다.
- **테스트 함정:** 데몬 이벤트는 `tests/test_daemon.py`의 `settled()`로 기다린다. 사람 승인 테스트 때문에 전체가 약 20초 걸린다.

## 관련 파일
- `gate/jev_client.py` — `JevClient`, `JevDecision`, `RequestContext`, `MockJevClient`, `FailClosedJevClient`, `build_question`, `_BACKENDS`(3b TODO), `make_client`
- `eval/run_eval.py`, `eval/labels.csv`, `eval/README.md` — 평가 스크립트(`--client`), 라벨, 기준
- `samples/` — 합성 한국어 문서 36개(4종류 × pii·quasi·none × 3)
- `tests/test_jev_client.py`, `tests/test_eval.py`, `tests/test_integration.py`
- `gate/policy.py` — `jev_backend`, `jev_timeout_seconds`, `max_inspect_bytes` 설정, `jev_action`(임계값 0.95)
- `gate/pipeline.py` — 마스킹 → Jev → 정책 → 승인 흐름(큰 파일은 나눠서 묻기)
- `plans/implementation-plan.md` — 2.1절 후보, 5절 Phase 3, 7절 검증
- `sandbox/bypass.py`, `tests/test_bypass.py` — 이번에 W14·W15를 더함

## 실행 · 검증
- **데몬**(`hackathon/`에서):
  ```
  GATE_SOCKET=$PWD/gate-data/run/gate.sock GATE_POLICY=$PWD/gate-data/policy.yaml GATE_AUDIT=$PWD/gate-data/audit/audit.jsonl \
    fuse-jev-gate/.venv/bin/gate serve --source gate-data/source [--no-dialog]
  ```
- 떠 있는지 확인: `GATE_SOCKET=…/gate-data/run/gate.sock fuse-jev-gate/.venv/bin/gate ls /`
- 감사 로그 보기: `GATE_AUDIT=$PWD/gate-data/audit/audit.jsonl fuse-jev-gate/.venv/bin/gate audit [--blocked]`
- 평가(Mock): `cd fuse-jev-gate && uv run python -m eval.run_eval --client mock [--details]`
- 테스트(맥): `cd fuse-jev-gate && uv run pytest` — **911 통과, 1 skip(FUSE)**, 약 20초
- 테스트(Linux): `docker build -f sandbox/gate.Dockerfile -t fuse-jev-gate:harness-audit . && docker run --rm --device /dev/fuse --cap-add SYS_ADMIN fuse-jev-gate:harness-audit pytest -q` — **912 통과**
- 하네스: 계획 문서 7절 또는 README "우회 시험 하네스"(비용이 든다)
- 인프라·런타임 상태
  - 데몬(PID 63433, 대화상자 켬, 감사 로그 켬)이 이 세션 끝까지 떠 있었다. 새 세션에서 확인하고 필요하면 다시 띄운다.
  - Docker Desktop이 켜져 있다(Linux 테스트용).
  - `gate-data/`에는 `source/`, `run/`, `audit/`, `agent-workspace/`, `plain-project/`가 있다. `policy.yaml`은 없다(기본 정책, `jev_backend: mock`).
  - Claude Code 2.1.316. 로컬 Jev 런타임은 아직 없다.

## 미해결 · 블로커
1. **이번 PR 머지:** 사용자가 리뷰하고 머지한다.
2. **Jev 백엔드 선택:** Phase 3b의 블로커다. 사용자가 정한다.
3. **애매한 라벨:** 진료 메모 quasi는 `sensitive`로, pii는 마스킹 뒤 이름만 남는 것으로 둔다(기본 기준 유지).
