# 핸드오프 — fuse-jev-gate (에이전트 파일 접근 게이트)
_업데이트: 2026-11-01 · 작성 세션: PR #10~#12 머지, Phase 9(원본 폴더 보기·추가·빼기) 구현 → PR #13, 다음 작업 다섯 가지 확인(`plans/next-tasks-plan.md`)_

## TL;DR (30초 복원)
- **프로젝트:** 일반인이 로컬에서 쓰는 AI 에이전트가 원본 문서를 게이트를 거쳐서만 읽게 하는 도구다.
  - 게이트 순서: 경로 규칙 → 정규식 → IP·내부 주소 규칙 → NER → 판단 모델(Laya, 코드 이름 Jev) → 정책 → 사람 승인 → 감사 로그
- **방금 끝낸 것:** Phase 9 구현(PR #13, 머지 전). 사용자가 준 다음 작업 다섯 가지를 확인했다.
  - 다섯 가지: `plant` 이름, Jev 표시, 개체 번호, 비밀번호 통과, UTF-16
  - 확인 결과는 `plans/next-tasks-plan.md`에 있다. 비밀번호 일부와 UTF-16 비밀값이 **원문 그대로 나가는 것을 실제 모델로 확인했다.**
- **다음 첫 행동:** PR #13 머지 여부를 묻고, `plans/next-tasks-plan.md` 3절의 질문 6개를 사용자에게 묻는다.

## 목표
일반인이 자기 컴퓨터에서 AI 에이전트를 쓸 때, 에이전트가 파일을 읽으면 반드시 게이트를 거치게 한다. 게이트는 허용·가림·사람 승인·차단 중 하나로 답한다. 처리 방식은 사용자가 정책(문장, 설정 화면, `gate policy`)으로 정하고, 정하지 않으면 기본값을 쓴다.

참고 문서:
- **다음 작업: `plans/next-tasks-plan.md`**(확인 결과 1절, 고칠 방향 2절, 물을 것 3절)
- 전체 계획: `plans/implementation-plan.md`(5절 Phase 0~9). 명세 `fuse-jev-gate-spec.md`와 다르면 계획을 따른다.
- Phase 9: `plans/mount-ui-plan.md`(2절 아래 표에 사용자 결정 5개, 9절 인터페이스)
- 평가: `eval/README.md`. e2e는 `eval/run_e2e.py` 맨 위 설명(시나리오 16개, `mount` 포함).
- 팀원용 평가 보고서: https://claude.ai/artifact/4WaSWgekRir4frkRCq1a2L (버전 6)
- 점검 페이지: https://claude.ai/artifact/AbemNcUnx4Uhj9d2B5jBhW (버전 17, Phase 9 구현은 아직 안 넣음)

## 현재 위치
- 현재 상태: `main`은 PR #12까지 머지됨(`ff07f82`).
  - **PR #13** `feat/mount-ui`(Phase 9)가 열려 있고 머지는 사용자가 한다. 이 HANDOFF와 `plans/next-tasks-plan.md`도 같은 브랜치에 있다.
- 다음: PR #13 머지 → 다음 작업 질문 6개 → 정한 순서대로 작업(추천 순서: UTF-16·인코딩 → 비밀번호 → 개체 번호 → Jev 표시 → `plant` 이름).

## 완료한 것 (이번 세션)
- **PR #10·#11·#12 머지**(사용자 요청, 머지 커밋, 원격 브랜치 남김). main 1569 통과·1 skip.
- **Phase 9 미결 5개 사용자 결정(모두 추천안)**
  - 기본 폴더는 `/`에 그대로 둔다.
  - 빼면 게이트에서만 빼고 보호는 남긴다([보호도 풀기]는 따로).
  - 켜진 세션에는 항상 "다시 시작" 안내를 한다.
  - 이름이 겹치면 숫자를 붙인다.
  - 작업 폴더는 `workspaces.yaml`에 등록한다.
  - 데모 정책 두 항목(`jev_backend: laya`, `entities.name.action: allow`)은 그대로 둔다.
- **Phase 9 구현**: 인터페이스(`gate/mount_change.py`)를 먼저 커밋하고, worktree 에이전트 둘(9-1 게이트·CLI ∥ 9-2 화면)에게 맡긴 뒤 메인이 검토·합치기를 했다.
  - 바꾼 것: `MountChange.added_by` 필드, 작업 폴더 설정 예시, 테스트 격리(`tests/conftest.py`), 실제 함수 통합 테스트.
  - 맥 1856 통과·1 skip.
- **e2e**: `mount` 시나리오를 더했다. PR #10(해요체) 뒤 깨졌던 host·policy 판정 문구(`저장했습니다` → `저장했어요`)도 고쳤다. 실제 모델로 16/16(약 14초).
- **브라우저 확인**(Chrome, 실제 데모 폴더)
  - 폴더 선택 창 → 확인 상자 → 연결, 취소, 빼기("보호만 남음"), 보호도 풀기(빨간 경고)를 확인했다.
  - 게이트로 읽으면 `.env`는 차단되고 연락처·카드번호는 가려졌다.
  - 마지막 다시 연결은 사용자가 나중에 하기로 했다. 그래서 지금 데모에는 추가 폴더가 없다.
- **데모 폴더 변경**: `gate-data/agent-workspace`를 `init --force`로 다시 만들어 등록했다(`gate-data/workspaces.yaml`). 예시 폴더 `hackathon/demo-extra/가계부`를 만들었다.
  - 바뀐 것은 설정에 목록·등록 파일 막기 4줄이 더해진 것이다.
  - 옛 설정은 세션 scratchpad에 백업했다.
- **PR #13 생성**: https://github.com/Plant-Hackathon-2026/fuse-jev-gate/pull/13
- **다음 작업 다섯 가지 확인**: `plans/next-tasks-plan.md`

## 다음 할 일
1. **PR #13 머지 여부를 묻는다.** 머지하면 지금까지처럼 머지 커밋으로 하고 원격 브랜치는 남긴다. 그다음 로컬 `main`을 최신화하고 `uv run pytest`가 1856 통과·1 skip인지 본다.
2. **`plans/next-tasks-plan.md` 3절 질문 6개를 묻는다.** `plant` 범위, Jev 표시 범위, 개체 번호 방식, 비밀번호 보강 범위, 인코딩 처리, 순서·PR 단위다. 추천안은 그 문서 2절에 있다.
3. 정한 순서대로 작업마다 브랜치 + PR.
   - 인코딩·비밀번호는 1.4·1.5절 사례를 저장소 테스트와 e2e 시나리오로 옮기고 샘플 36개를 다시 잰다.
4. 남은 Phase 9 일(사용자가 정하면)
   - 하네스 W18~W21: worktree에 중간 커밋이 있다(아래 "관련 파일").
   - README·점검 페이지에 Phase 9 반영.
   - 화면에서 다시 연결해 두기(사용자가 직접).

## 핵심 결정 · 함정
- **진행 규칙**
  - 작업마다 멈추고 보고한다. 서브에이전트는 Opus를 쓴다.
  - 병렬 에이전트는 파일이나 worktree를 나누고, 커밋은 메인이 검토한 뒤에 한다.
  - 비용·시간이 드는 실행(하네스, 모델 내려받기, Linux 컨테이너)은 먼저 묻는다.
  - 모델 관련 작업(재학습·교체·내려받기)은 후보로 내지 않는다.
  - 사용자는 직접 보는 것을 좋아하고, 어려우면 "풀어서 말해 달라"고 한다.
- **Phase 9**
  - `mounts.yaml`·`workspaces.yaml`은 정책 파일 옆에 고정했다(`--mounts` 없음). 데몬·화면·작업 폴더 설정이 같은 파일을 보게 하려는 것이다.
  - 등록된 작업 폴더가 없으면 화면의 기본 폴더 줄이 "알 수 없음"이고, 추가할 때 `no_workspace` 안내가 나온다.
  - 화면 추가 API는 경로를 받지 않는다. 폴더 선택 창에서 고른 것만 한 번 쓰는 선택 번호로 받는다.
- **실제 모델로 시험할 때의 함정**
  - `gate-data/policy.yaml`에 `jev_backend: laya`가 있어야 실제 판단 모델을 쓴다(없으면 Mock).
  - NER은 기본 켬이라 nerserve가 없으면 모든 내용 읽기가 막힌다. 서버는 데몬보다 먼저 띄운다.
  - 맥에는 `timeout` 명령이 없다.
- **거짓 안전**
  - 저장소 NER·Laya 테스트는 가짜 서버로 돈다. 실제 동작은 e2e 16개와 36개 재측정으로 본다.
  - **UTF-16 파일의 비밀값·카드번호는 지금 허용되어 원문 그대로 나간다**(테스트는 모두 UTF-8이라 green).
  - **`DB_PASS=…`, `password 값`(구분자 없음), OTP 백업 코드도 원문으로 나갈 수 있다.** NER이 일부를 가리면 판단 모델이 "가린 뒤 안전"으로 기우는 것이 원인 중 하나다.
  - 이미 켜진 에이전트 세션에 폴더 추가가 바로 반영되는지(W20)는 재지 않았다. 그래서 안내를 항상 낸다.
  - 설정 화면은 데스크톱 폭만 봤다. Linux 컨테이너 테스트는 3e 이후 돌리지 않았다(사용자 결정).

## 관련 파일
- `plans/next-tasks-plan.md`(다음 작업), `plans/mount-ui-plan.md`(Phase 9), `plans/implementation-plan.md`
- 다음 작업에서 볼 곳
  - 인코딩: `gate/pipeline.py`(`Inspector.inspect`의 `decode("utf-8", "surrogateescape")`), `gate/detectors.py`(`mask_bytes`)
  - 비밀번호: `gate/detectors.py` 27~41줄 비밀값 규칙(`_SECRET_KEY`, `_find_secret`), `gate/pipeline.py` ② 흐름(가린 뒤 판단)
  - 개체 번호: `gate/pipeline.py:253`(`MASK_TEXT[e.label]`), `gate/ner_client.py:88`(`MASK_TEXT`)
  - Jev 표시: `gate/pipeline.py`, `gate/policy.py`(설정 설명), `gate/policy_cli.py`, `gate/policy_text.py`, `gate/jev_client.py`, `gate/audit_cli.py`
  - `plant` 이름: `pyproject.toml`(`[project.scripts]`), `gate/cli.py`(`prog`), `gate/workspace.py`(`agent_guide`), README
- 하네스 중간 작업: worktree `.claude/worktrees/agent-a0ff025c7425f998e`, 브랜치 `worktree-agent-a0ff025c7425f998e`, 커밋 `b6fbcf5`(wip, 로컬만). `sandbox/bypass.py`에 W18~W21과 `tests/test_bypass.py`가 있다. 이어 할 때 검토하고 다시 돌린다(실제 데모 폴더 대신 `/tmp` 데이터를 쓴다).
- 저장소 밖
  - `hackathon/models/ner-xlmr-pii/`, `models/laya-v3/`, `hackathon/laya-serve/`
  - `hackathon/gate-data/`: 실제 데모 폴더. `workspaces.yaml`에 작업 폴더가 등록되어 있다. `mounts.yaml`은 비어 있다.
  - `hackathon/demo-extra/가계부/`: 화면 시험용 예시 폴더(가계부 메모, 카드번호·연락처가 든 10월 기록, `.env`)

## 실행 · 검증
- **서버 둘** (`H=<hackathon>`)
  - laya-serve: `cd $H/laya-serve && LAYA_HOST=127.0.0.1 LAYA_PORT=8000 LAYA_MODELS=gate-laya-v3 LAYA_DEFAULT_MODEL=gate-laya-v3 LAYA_EXTRA_MODELS='{"gate-laya-v3": "'$H'/models/laya-v3"}' HF_HUB_OFFLINE=1 USE_TF=0 .venv/bin/laya-serve`
  - nerserve: `cd $H/fuse-jev-gate && HF_HUB_OFFLINE=1 ../laya-serve/.venv/bin/python -m nerserve --model-dir $H/models/ner-xlmr-pii --port 8001`
- **데모(터미널 3개)**: 모든 터미널에 `export DATA=$H/gate-data GATE_SOCKET=$DATA/run/gate.sock GATE_POLICY=$DATA/policy.yaml GATE_AUDIT=$DATA/audit/audit.jsonl JEV_BASE_URL=http://127.0.0.1:8000 JEV_MODEL=gate-laya-v3 JEV_MODEL_DIR=$H/models/laya-v3`와 `PATH=$H/fuse-jev-gate/.venv/bin:$PATH`
  - 터미널 1: `gate serve --source $DATA/source`
  - 터미널 2: `cd $DATA/agent-workspace && claude`
  - 터미널 3: `gate ui`(원본 폴더 절에서 [폴더 추가]), `gate mount list`, `gate audit`
- **e2e**: `uv run python -m eval.run_e2e` — 16 통과(약 14초). 서버 둘이 필요하다.
- **테스트(맥)**: `uv run pytest` — PR #13 브랜치 1856 통과·1 skip, main 1569 통과·1 skip
- **인프라·런타임 상태**
  - laya-serve(8000)·nerserve(8001)는 이전 세션부터 떠 있었고 이 세션에서 끄지 않았다.
  - 이 세션이 띄운 데모 데몬·`gate ui`는 껐다. 예전 세션의 사용자 데몬·`gate ui`도 사용자 허락을 받고 껐다.

## 미해결 · 블로커
1. PR #13 머지(사용자).
2. `plans/next-tasks-plan.md` 3절 질문 6개.
3. 하네스 W18~W21 마무리 여부(중간 커밋 있음, 비용 약 $0.1).
4. README·점검 페이지에 Phase 9 반영.
5. 따로 발견: NER이 카드번호를 `[식별번호]`로 가릴 때 뒤의 `)`가 함께 사라진다(기존 동작).
6. 팀원 학습 데이터 출처·라이선스는 답이 없다. 가중치는 저장소에 넣지 않으므로 막히지는 않는다.
