# 핸드오프 — fuse-jev-gate (에이전트 파일 접근 게이트)
_업데이트: 2026-11-01 · 작성 세션: PR #9 머지 뒤 마무리(데모 안내문, 아티팩트, 말투 통일 PR #10, 실제 모델 e2e PR #11, 사용자 직접 시험) + Phase 9(원본 폴더 관리) 계획_

## TL;DR (30초 복원)
- **프로젝트:** 일반인이 로컬에서 쓰는 AI 에이전트가 원본 문서를 게이트를 거쳐서만 읽게 하는 도구다.
  - 게이트 순서: 경로 규칙 → 정규식 → IP·내부 주소 규칙 → NER → 판단 모델(Laya, 코드 이름 Jev) → 정책 → 사람 승인 → 감사 로그
- **방금 끝낸 것:** 3e(PR #9) 머지. 말투 통일(PR #10)과 실제 모델 e2e(PR #11)를 열었다. Phase 9 계획을 썼다(`plans/mount-ui-plan.md`, 이 HANDOFF와 같은 PR).
- **다음 첫 행동:** 열린 PR(#10·#11·Phase 9 계획 PR) 머지 여부를 묻는다. 그다음 Phase 9 미결 5개(`plans/mount-ui-plan.md` 8절)를 묻고 구현을 시작한다.

## 목표
일반인이 자기 컴퓨터에서 AI 에이전트를 쓸 때, 에이전트가 파일을 읽으면 반드시 게이트를 거치게 한다. 게이트는 허용·가림·사람 승인·차단 중 하나로 답한다. 처리 방식은 사용자가 정책(문장, 설정 화면, `gate policy`)으로 정하고, 정하지 않으면 기본값을 쓴다.

참고 문서:
- 전체 계획: `plans/implementation-plan.md`(5절 Phase 0~8 완료, Phase 9 계획). 명세 `fuse-jev-gate-spec.md`와 다르면 계획을 따른다.
- **Phase 9 계획: `plans/mount-ui-plan.md`**(정한 것 2절, 설계 4·5절, 단계 6절, 검증 7절, **미결 8절**)
- 평가: `eval/README.md`. e2e는 `eval/run_e2e.py` 맨 위 설명(PR #11).
- 팀원용 평가 보고서(링크가 있는 누구나 볼 수 있음): https://claude.ai/artifact/4WaSWgekRir4frkRCq1a2L — 버전 6(링크를 `main`으로 바꿈)
- 점검 페이지: https://claude.ai/artifact/AbemNcUnx4Uhj9d2B5jBhW — 버전 16(3e 머지됨). 이번 세션 끝에 PR #10·#11·Phase 9 계획 반영을 에이전트에 맡겼다(버전 17 예정, 결과는 아래 "완료한 것"에 적음).

## 현재 위치
- 현재 상태: 계획의 Phase 0~8과 3e가 `main`에 있다(머지 커밋 `f92c471`). 열린 PR 세 개:
  - **#10** `fix/policy-text-tone`: 정책 해석·바꿈 안내를 해요체로 통일. 맥 1569 통과·1 skip. README 테스트 수 1569.
  - **#11** `test/e2e-real`: `eval/run_e2e.py`. 실제 모델로 시나리오 15개 모두 통과(약 12초).
  - **Phase 9 계획 PR** `docs/mount-ui-plan`: `plans/mount-ui-plan.md`, `plans/implementation-plan.md` Phase 9 항목, 이 HANDOFF.
- 다음: PR 머지(사용자) → Phase 9 미결 5개 묻기 → 9-1(게이트·보호 설정·CLI) ∥ 9-2(화면) 구현.

## 완료한 것 (이번 세션)
- **PR #9 머지**(사용자 요청, 머지 커밋 `f92c471`, 원격 브랜치 남김). 로컬 `main` 최신.
- **데모 작업 폴더 안내문 다시 만들기**(사용자 요청): `gate workspace init gate-data/agent-workspace --force`. 바뀐 것은 `CLAUDE.md`의 가림 설명 한 줄이다. 설정 파일은 같아서 그대로 뒀다. 옛 파일은 이 세션 scratchpad에 백업했다.
- **아티팩트**(에이전트): 점검 페이지 v16(3e "머지 대기" → "머지됨" 13곳), 팀원용 보고서 v6(`feat/ner-v2` 링크 → `main`).
- **PR #10 말투 통일**(worktree 에이전트 → 메인 검토·테스트·커밋 `d073bdb`·`e1f0ed9`)
  - `gate/policy_text.py`·`gate/policy_change.py`·`gate/policy_cli.py`의 사용자 문구 약 65곳.
  - 출력에 "니다"가 없는지 보는 테스트 2개를 더했다.
  - 남긴 것: `gate/policy.py` `PolicyError`(데몬의 정책 파일 차단과 같이 씀. `gate policy set` 값 오류가 "~합니다"로 나옴), `gate ui` 터미널 안내, `JevError`.
- **PR #11 실제 모델 e2e**(worktree 에이전트 → 메인이 직접 다시 돌려 15/15 확인, 커밋 `13c4846`)
  - 시나리오: ls, allow, mask, number, host, path, approve, deny, timeout, search, policy, cache, ner-down, ui, audit
  - 데이터는 `/tmp/ge2e-*`(소켓 경로 104바이트 제한 때문)에 만들고 지운다. 실제 데모 폴더는 쓰지 않는다. 사람 명령은 pty로 돈다. API 비용은 없다.
  - 참고(고치지 않음): 데몬 stderr 버퍼가 없을 때(`PYTHONUNBUFFERED=1`)만 승인 줄과 요청 줄이 섞인다(`gate/cli.py` `_err`를 두 스레드가 씀).
- **사용자 직접 시험**: 실제 모델 서버 둘(8000·8001)을 띄우고 터미널 3개 가이드를 드렸다. 사용자가 데몬·`gate ui`·에이전트 세션을 직접 띄워 시험했다.
  - 처음에 판단 모델이 "사용 안 함"으로 보였다. 원인은 `gate-data/policy.yaml`이 없어 기본값 Mock이었던 것이다. `gate policy set jev_backend laya`로 켰다.
  - 사용자가 시험하며 정책에 두 항목을 남겼다: `jev_backend: laya`, `entities.name.action: allow`. 그대로 둘지 다음 세션에 묻는다.
- **Phase 9 계획**(사용자 요청 "UI에서 마운트, 지금 폴더 목록과 추가"): 사용자 결정 4개(맥 폴더 선택 창, 보기+추가+빼기, 작업 폴더 설정만 자동, 구현은 새 세션). 문서 `plans/mount-ui-plan.md`.

## 다음 할 일
1. **열린 PR 머지 여부를 묻는다**: #10(말투), #11(e2e), Phase 9 계획 PR. 머지를 요청하면 지금까지처럼 머지 커밋으로 하고 원격 브랜치는 남긴다. #10과 #11은 둘 다 README "테스트" 절을 고치지만 다른 줄이라 충돌이 없을 것이다(확인할 것). 머지 뒤 로컬 `main`을 최신화하고 `uv run pytest`가 1569 통과·1 skip인지 본다.
2. **Phase 9 미결 5개를 묻는다**(`plans/mount-ui-plan.md` 8절, 추천안 있음). 기본 폴더 자리, 뺄 때 보호, 켜진 세션 반영(W20 결과로), 이름 겹침, 작업 폴더 등록 방식이다.
3. Phase 9 구현: 9-1 게이트·보호 설정·CLI ∥ 9-2 화면(파일 나눠 worktree 에이전트, 커밋은 메인) → 9-3 e2e `mount` 시나리오·하네스 W18~W20(비용, 먼저 묻기)·문서.
4. 데모 정책 두 항목(`jev_backend: laya`, `entities.name.action: allow`)을 그대로 둘지 묻는다.

## 핵심 결정 · 함정
- **진행 규칙**
  - 작업마다 멈추고 보고한다. 서브에이전트는 Opus를 쓴다.
  - 병렬 에이전트는 맡는 파일을 나누거나 worktree를 나누고, 커밋은 메인이 검토 뒤에 한다.
  - 비용·시간이 드는 실행(하네스, 모델 내려받기, Linux 컨테이너)은 먼저 묻는다.
  - 사용자는 직접 보는 것을 좋아하고, 어려우면 "풀어서 말해 달라"고 한다.
  - 이번 세션 사용자 결정: 설정 화면 폰 폭 확인과 Linux 컨테이너 테스트는 하지 않는다.
- **Phase 9 핵심 위험**: 폴더를 게이트에만 연결하고 작업 폴더 보호 설정에 넣지 않으면 에이전트가 원본을 직접 읽는다. 추가는 보호 설정 먼저, 목록 나중이다. 손으로 바뀐 작업 폴더 설정이면 추가하지 않는다(계획 4.3).
- **실제 모델로 시험할 때의 함정**
  - `gate-data/policy.yaml`이 없으면 판단 모델은 Mock이다. 데몬에 `JEV_*`를 줘도 `jev_backend: laya`가 있어야 실제 모델을 쓴다. 이 바꿈은 "느슨해짐"으로 보고 한 번 더 묻는다.
  - 설정 화면에는 판단 모델을 켜는 버튼이 없다(터미널만).
- **e2e(PR #11)**
  - 샘플은 확신도가 임계값(0.05·0.5·0.95)과 먼 것만 골랐다. 체크포인트를 바꾸면 다시 골라야 한다.
  - `JEV_MODEL` 기본값은 `gate-laya-v3`(제품 기본 `gate-laya`와 다름), `JEV_MODEL_DIR`는 저장소 위 폴더들에서 `models/laya-v3`를 찾는다.
- **이전부터**
  - ② 흐름: NER 켜짐 + 가린 것 있음 → Laya가 가린 결과를 보고 safe면 가림, 아니면 승인.
  - Laya·NER 오류는 차단한다. NER은 기본 켬이라 nerserve가 없으면 모든 내용 읽기가 막힌다. 서버는 데몬보다 먼저 띄운다.
  - laya-serve에는 `LAYA_HOST=127.0.0.1`과 `LAYA_MODELS`를 꼭 준다.
  - `gate policy` 바꾸기 명령은 tty가 아니면 거부한다(자동화는 pty나 `gate.policy.write_entries`).
  - 긴 한글 파이썬은 파일로 써서 `python3 -I`로 돌린다.
- **거짓 안전**
  - 저장소 NER·Laya 테스트는 가짜 서버로 돈다. 실제 동작은 36개 재측정과 e2e 15개(PR #11, 머지 전)로 본다.
  - 설정 화면은 데스크톱 폭만 봤다.
  - Linux 컨테이너 테스트는 3e 이후 돌리지 않았다(사용자 결정으로 뺌).

## 관련 파일
- `plans/mount-ui-plan.md`(Phase 9), `plans/implementation-plan.md`, `eval/run_e2e.py`(PR #11)
- Phase 9에서 바꿀 곳: `gate/store.py`(`PlainStore`), `gate/cli.py`(`_serve`), `gate/workspace.py`(`GatePaths.protected`·`protection_settings`·`agent_settings`·`check_workspace`), `gate/claude_setup.py`(`record_path`), `gate/policy.py`(`PolicySource` 방식 참고, `match_path`), `gate/policy_change.py`(쓰기 방식 참고), `gate/ui.py`·`gate/ui_api.py`·`gate/ui_static/`, `gate/approval.py`(osascript argv 원칙), `sandbox/bypass.py`
- 저장소 밖
  - `hackathon/models/ner-xlmr-pii/`(NER), `models/laya-v3/`(판단 모델), `hackathon/laya-serve/`
  - `hackathon/gate-data/`: 실제 데모 폴더. 안내문은 새 문구다. `policy.yaml`에 사용자 시험 항목 2개가 있다.

## 실행 · 검증
- **서버 둘** (`H=<hackathon>`)
  - laya-serve: `cd $H/laya-serve && LAYA_HOST=127.0.0.1 LAYA_PORT=8000 LAYA_MODELS=gate-laya-v3 LAYA_DEFAULT_MODEL=gate-laya-v3 LAYA_EXTRA_MODELS='{"gate-laya-v3": "'$H'/models/laya-v3"}' HF_HUB_OFFLINE=1 USE_TF=0 .venv/bin/laya-serve`
  - nerserve: `cd $H/fuse-jev-gate && HF_HUB_OFFLINE=1 ../laya-serve/.venv/bin/python -m nerserve --model-dir $H/models/ner-xlmr-pii --port 8001`
  - 둘 다 `/health`가 200이 되는지 본다.
- **e2e(PR #11 머지 뒤)**: `uv run python -m eval.run_e2e` — 15 통과(약 12초)
- **36개 재측정**: `NER_BASE_URL=http://127.0.0.1:8001 JEV_BASE_URL=http://127.0.0.1:8000 JEV_MODEL=gate-laya-v3 JEV_MODEL_DIR=$H/models/laya-v3 uv run python -m eval.run_eval --client laya --ner server --details`
- **사용자 직접 시험(터미널 3개)**: 모든 터미널에 `export DATA=$H/gate-data GATE_SOCKET=$DATA/run/gate.sock GATE_POLICY=$DATA/policy.yaml GATE_AUDIT=$DATA/audit/audit.jsonl JEV_BASE_URL=http://127.0.0.1:8000 JEV_MODEL=gate-laya-v3 JEV_MODEL_DIR=$H/models/laya-v3`와 `PATH=$H/fuse-jev-gate/.venv/bin:$PATH`를 준다.
  - 터미널 1: `gate serve --source $DATA/source`
  - 터미널 2: `cd $DATA/agent-workspace && claude`
  - 터미널 3: `gate audit`, `gate ui`
- **테스트(맥):** `uv run pytest` — main 1555 통과·1 skip, PR #10 브랜치 1569 통과·1 skip
- **인프라·런타임 상태:** 세션을 끝낼 때 laya-serve·nerserve는 이 세션과 함께 꺼진다. 사용자가 띄운 데몬·`gate ui`는 사용자 터미널에 있다. worktree 두 개(`.claude/worktrees/agent-*`)는 PR 머지 뒤 지운다.

## 미해결 · 블로커
1. PR #10·#11·Phase 9 계획 PR 머지(사용자).
2. Phase 9 미결 5개(`plans/mount-ui-plan.md` 8절).
3. 데모 정책 시험 항목 2개를 둘지.
4. 작은 후보(사용자가 정하면): `PolicyError`·`gate ui` 터미널 안내 말투, 데몬 로그 줄 섞임(`PYTHONUNBUFFERED=1`일 때만).
5. 팀원 학습 데이터 출처·라이선스는 답이 없다. 가중치는 저장소에 넣지 않으므로 막히지는 않는다.
