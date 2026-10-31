# 핸드오프 — fuse-jev-gate (에이전트 파일 접근 게이트)
_업데이트: 2026-11-01 · 작성 세션: Phase 8 머지 반영(문서 정리) + 팀원 NER 재학습판 조합 평가_

## TL;DR (30초 복원)
- **프로젝트:** 일반인이 로컬에서 쓰는 AI 에이전트가 원본 문서를 게이트를 거쳐서만 읽게 하는 도구다.
  - 게이트 순서: 경로 규칙 → 정규식 → NER(선택) → 판단 모델(Laya, 코드 이름은 Jev) → 정책 → 사람 승인 → 감사 로그
- **방금 끝낸 것:** 계획 문서의 Phase 8을 머지된 상태로 고쳤다. 팀원이 새로 학습한 NER(`xlmr-pii-ner`, 6종)로 **판단 모델 2 × NER 4 조합**을 평가했고, 추천은 **v3 + 새 NER(단독)**이다. 결과는 `eval/README.md` "NER 재학습판 조합 비교"에 있다. 브랜치는 `docs/ner-v2-eval`이고 PR을 올렸다(머지는 사용자).
- **다음 첫 행동:** 사용자에게 새 NER을 게이트에 넣을지 묻는다. 넣기로 하면 아래 "다음 할 일" 1번의 결정 세 가지를 먼저 받고 구현한다. **저장소 코드는 아직 기존 NER(32종) 기준이다.**

## 목표
일반인이 자기 컴퓨터에서 AI 에이전트를 쓸 때, 에이전트가 파일을 읽으면 반드시 게이트를 거치게 한다. 게이트는 허용·가림·사람 승인·차단 중 하나로 답한다. 처리 방식은 사용자가 정책(문장, 설정 화면, `gate policy`)으로 정하고, 정하지 않으면 기본값을 쓴다.

참고 문서:
- **현재 계획: `plans/implementation-plan.md`.** 명세(`fuse-jev-gate-spec.md`)와 다른 부분은 계획 문서를 따른다. 2절 결정 표, 5절 Phase, 6절 한계. Phase 0~8, 3a~3d 모두 끝났다.
- **평가: `eval/README.md`.** 이번 세션 결과는 "NER 재학습판 조합 비교 (2026-11-01)" 절.
- UI 계획·결과: `plans/ui-plan.md`
- 점검 페이지: https://claude.ai/artifact/AbemNcUnx4Uhj9d2B5jBhW — 버전 13(Phase 8까지). 이번 조합 평가는 반영하지 않았다.
- 팀원용 평가 보고서: https://claude.ai/artifact/4WaSWgekRir4frkRCq1a2L — 버전 3. "링크가 있는 누구나"로 공유 중. 이번 조합 평가는 반영하지 않았다.

## 현재 위치
- 현재 상태: 계획의 Phase는 모두 끝났다. 새 NER 체크포인트를 평가만 했다(저장소에 넣지 않음).
- 다음: 새 NER을 넣을지 사용자 결정 → 넣으면 라벨 지원 구현 + 테스트 + 저장소 평가 다시 실행.

## 완료한 것 (이번 세션)
- PR #7 머지 확인(`14e8a92`), 브랜치 `docs/ner-v2-eval` 생성. `plans/implementation-plan.md` Phase 8 제목을 "완료, PR #7 머지"로 고쳤다.
- 새 NER 체크포인트를 `hackathon/models/ner-xlmr-pii/`로 복제했다(원본은 `~/Downloads/handoff_ner/xlmr-pii-ner`, config 해시 같음). 함께 온 것: 학습·마스킹 스크립트, `data/{train,eval}_with_meta.jsonl`(v3 데이터), `hard_test/hard_test{,_2}.jsonl`.
- **조합 평가**(임시 스크립트, 저장소 밖 — 아래 "관련 파일"):
  - 게이트 흐름(샘플 36개, ② 흐름, 기본 정책): v3 + 새 NER이 민감 가림 14·승인 10, 안전 허용 7·승인 5, 승인 없이 남은 식별자 1(`월매`), 승인하면 남는 것 0, 승인 창 15. 기존 NER은 가림 5·승인 19, 승인하면 남는 것 15줄, 승인 창 24. 둘 합침은 누출이 새 NER과 같고 오탐·지연·메모리만 늘었다. 3b는 어느 조합이든 승인 창 29~32.
  - NER 단독: hard_test 37 → 53/54, hard_test_2 34 → 61/62, eval_with_meta 개체 재현율 72.6% → 98.3%(이 세트는 새 모델 선택에 쓰여 유리함).
  - 겹침 확인: 샘플 36개 줄·기존 NER이 놓친 단어·hard_test 문장 모두 팀원 학습 데이터에 없다.
  - 기술 문서 10문장(직접 지음): 새 NER은 IP·비밀값 종류가 없지만 연락처·식별번호로 보고 가렸다(전부 가림 12/18, 기존 10/18). 종류 이름은 틀리고 `redis`·`com` 같은 오탐이 있다.
- 기존 NER 결과(v3 + 기존 NER)는 3c 기록과 똑같이 재현됐다.

## 다음 할 일
1. **새 NER을 게이트에 넣을지 사용자에게 묻는다.** 넣기로 하면 구현 전에 다음 세 가지를 정한다.
   - (a) ID_NUMBER를 어느 묶음으로 둘지. 지금 묶음은 `student_id`(학번)뿐이다. 이름을 "식별번호"로 넓힐지, 새 묶음을 만들지 정한다.
   - (b) AGE_BIRTH를 어떻게 나눌지. 평가에서는 값에 `생`·월일·날짜 형식이 있으면 생년월일(가림), 아니면 나이(plain, 기본 공개)로 나눴다. `출생 연도: 1987년`처럼 앞말을 봐야 하는 경우를 넣을지 정한다.
   - (c) 기존 모델의 기술 종류(IP·내부 주소·비밀값)를 버릴지. 버리면 정규식과 새 모델의 우연한 가림에 기댄다.
   - 구현할 곳: `gate/ner_client.py`(`NER_LABELS`·`_GROUP_OF`·`MASK_TEXT`·`policy_slot`, 라벨 검사), `gate/ner_spans.py`(`EXACT_KINDS`·`_KEEP_ENDINGS`를 새 종류에 맞추기), 설정 화면 NER 줄(`gate/ui_api.py`), 관련 테스트, `eval/README.md`·`README.md`의 모델 설명. 그 뒤 `eval.run_eval --ner server`로 다시 재서 위 표와 맞는지 본다. `ner_backend` 기본값(지금 none)을 바꿀지도 사용자에게 묻는다.
   - 평가에 쓴 바꾸기 규칙은 scratchpad `combo_common.py`의 `map_new_label`에 있다(세션이 끝나면 사라질 수 있다. 규칙은 `eval/README.md`에도 적어 두었다).
2. 원하면 팀원용 보고서(4WaSWgekRir4frkRCq1a2L)와 점검 페이지에 이번 결과를 넣는다(사용자에게 먼저 묻는다).
3. 작업을 끝내면 보고하고 멈춘다. 새 작업은 브랜치 + PR, 커밋과 PR 본문에 AI 흔적을 남기지 않는다.

## 핵심 결정 · 함정
- **진행 규칙**
  - Phase나 작업마다 멈추고 보고한다. 서브에이전트는 Opus로 띄운다. 큰 구현은 worktree 에이전트에 맡기고 메인이 검토·전체 테스트·실제 실행 확인을 한 뒤 PR로 올리는 방식이 잘 맞았다.
  - 비용이 드는 실행(하네스, 모델 내려받기)은 실행 전에 확인받는다.
  - 사용자는 설명이 어려우면 "풀어서 말해 달라"고 한다. 실제 예로 단계별로 설명하면 통한다.
  - 사용자가 직접 꺼낸 모델 작업(이번처럼 재학습 체크포인트 평가)은 한다. 후보를 낼 때 모델 작업을 먼저 제안하지는 않는다.
- **이번 평가의 함정**
  - 게이트 NER 클라이언트는 모르는 라벨이 오면 `NerError`로 막는다(→ 모든 읽기 차단). 새 모델을 nerserve로 그대로 띄우면 안 된다. 라벨 지원을 먼저 넣는다.
  - 새 모델은 기존과 같은 nerserve(`nerserve.model.load`, 256토큰 창)로 읽힌다. 팀원 `mask_pii.py`는 argmax·512토큰이라 수치가 조금 다를 수 있다.
  - `eval_with_meta`는 새 모델 체크포인트 선택에 쓴 세트다. hard_test는 팀원이 만들어 학습 데이터 설계가 이를 보고 정해졌을 수 있다. 두 모델 모두에 독립적인 것은 샘플 36개다.
  - 학습 데이터 `meta.source`가 `1.데이터/Training/01.원천데이터/…`(실제 상담 대화 2,212행 포함)라 출처·라이선스 확인이 여전히 필요하다. 가중치·팀원 데이터는 저장소에 넣지 않는다.
- **설정 화면** (`plans/ui-plan.md` 5·8절)
  - 127.0.0.1에만 바인드, 실행마다 한 번 쓰는 열쇠, Host 정확히 일치, 바꾸는 요청은 POST + Origin, CSP `default-src 'none'`. 새로 고쳐도 유지(HttpOnly·SameSite=Strict 쿠키 `gate_ui_<포트>`, API는 `X-Gate-Token` 헤더).
  - 정책은 사람만 바꾼다. `gate policy` 바꾸기 명령은 입력이 터미널이 아니면 거부한다.
  - 게이트 카드가 "확인 못 함"이면 `gate --socket <소켓> ui`로 띄우지 않은 것이다. 상태 카드의 Laya·NER 주소는 화면 프로세스의 `JEV_BASE_URL`·`NER_BASE_URL`이다.
- **하네스**: worktree에서는 `--data-root <hackathon>/gate-data`. W17은 `--mode auto`. 데몬이 떠 있어야 한다. 실제 `~/.claude/settings.json`은 건드리지 않는다.
- **이전부터의 결정·함정** (자세한 것은 계획 2절·6절)
  - ② 흐름: NER이 켜져 있고 가린 것이 있으면 Laya가 가린 결과를 본다. safe면 가림, 아니면 승인. Laya·NER 오류는 언제나 차단.
  - laya-serve는 `LAYA_HOST=127.0.0.1`과 `LAYA_MODELS`를 꼭 준다. `LAYA_MODELS=gate-laya,gate-laya-v3`처럼 여러 판을 한 서버에 띄우고 `JEV_MODEL`·`JEV_MODEL_DIR`로 고를 수 있다.
  - 긴 한글 파이썬 스크립트는 heredoc 대신 파일로 써서 `python3 -I`로 돌린다. zsh에서 `echo =====`는 따옴표로 감싼다.
- **거짓 안전**
  - NER·Laya 저장소 테스트는 가짜 서버다. 실제 동작은 `eval.run_eval --ner server`와 이번 임시 스크립트로만 확인했다.
  - 이번 조합 평가의 새 NER 수치는 평가용 바꾸기 규칙(`map_new_label`)에 기댄다. 저장소에 넣을 때 규칙이 달라지면 다시 잰다.
  - 화면 스크립트(`app.js`)의 흐름은 저장소 테스트가 없고 Chrome에서 손으로 확인했다.

## 관련 파일
- `eval/README.md` "NER 재학습판 조합 비교": 이번 결과 표·바꾸기 규칙·주의점
- `gate/ner_client.py`, `gate/ner_spans.py`, `nerserve/`: 새 NER을 넣을 때 고칠 곳
- `plans/implementation-plan.md`, `plans/ui-plan.md`: 결정·결과·한계
- 이 세션 scratchpad(세션이 끝나면 사라질 수 있음. 남아 있으면 `find /private/tmp/claude-501 -name combo_common.py`로 찾는다): `combo_common.py`(라벨 바꾸기), `ner_adapter.py`(평가용 NER 서버 8001 기존·8002 새·8003 합침), `combo_gate.py`·`combo_report.py`(게이트 흐름), `combo_ner.py`(hard_test·eval_with_meta), `combo_tech.py`(기술 문서), 결과 `combo_gate.json`·`combo_ner.json`
- 저장소 밖
  - `hackathon/models/ner-xlmr-pii/`: 새 NER(6종). `hackathon/models/ner/`: 기존 NER(32종). `hackathon/models/laya`, `laya-v3`: 판단 모델
  - `~/Downloads/handoff_ner/`: 팀원이 준 원본(스크립트·데이터·hard_test)
  - `hackathon/gate-data/`, `hackathon/design-assets/`, `hackathon/laya-serve/`

## 실행 · 검증
- **데몬:** `D=<hackathon>/gate-data; .venv/bin/gate --socket $D/run/gate.sock serve --source $D/source --policy $D/policy.yaml --audit $D/audit/audit.jsonl`
- **설정 화면:** `.venv/bin/gate --socket $D/run/gate.sock ui --policy $D/policy.yaml --audit $D/audit/audit.jsonl`
- **laya-serve(두 판):** `cd <hackathon>/laya-serve && LAYA_HOST=127.0.0.1 LAYA_PORT=8000 LAYA_MODELS=gate-laya,gate-laya-v3 LAYA_DEFAULT_MODEL=gate-laya-v3 LAYA_EXTRA_MODELS='{"gate-laya": "<models>/laya", "gate-laya-v3": "<models>/laya-v3"}' HF_HUB_OFFLINE=1 USE_TF=0 .venv/bin/laya-serve`
- **조합 평가 다시 돌리기(scratchpad가 남아 있으면, 저장소 루트에서):** `../laya-serve/.venv/bin/python -I <scratchpad>/ner_adapter.py &` → `.venv/bin/python -I <scratchpad>/combo_gate.py` → `python3 -I <scratchpad>/combo_report.py`. NER 단독은 adapter를 끄고 `../laya-serve/.venv/bin/python -I <scratchpad>/combo_ner.py`(약 4분).
- **테스트(맥):** `uv run pytest` — 1375 통과, 1 skip(이번 세션은 코드를 바꾸지 않아 다시 돌리지 않음)
- **테스트(Linux):** `docker build -f sandbox/gate.Dockerfile -t fuse-jev-gate:phase8 . && docker run --rm --device /dev/fuse --cap-add SYS_ADMIN fuse-jev-gate:phase8 pytest -q` — 1376 통과(Phase 8 기준)
- **인프라·런타임 상태:** 게이트 데몬, 설정 화면, laya-serve, NER 서버 모두 꺼져 있다. `gate-data/policy.yaml`은 없다(기본 정책).

## 미해결 · 블로커
1. **새 NER 도입 여부와 결정 세 가지**(위 다음 할 일 1번). 사용자 결정.
2. **팀원 학습 데이터의 출처·라이선스**: 보고서에서 요청했고 아직 답이 없다. 새 NER도 같은 데이터로 학습했다. 가중치는 저장소에 넣지 않아 지금은 막히지 않는다.
3. 점검 페이지·팀원 보고서에 이번 결과 미반영.
