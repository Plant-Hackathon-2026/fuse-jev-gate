# 핸드오프 — fuse-jev-gate (에이전트 파일 접근 게이트)
_업데이트: 2026-10-31 · 작성 세션: 3d. 정책 문장으로 NER 묶음 바꾸기(해석기 entity 단계 + 평가 세트 2개 + Laya zero-shot 실험). 브랜치 `feat/policy-text-entities` + PR #5_

## TL;DR (30초 복원)
- **프로젝트:** 일반인이 로컬에서 쓰는 AI 에이전트가 원본 문서를 게이트를 거쳐서만 읽게 하는 도구다.
  - 게이트 순서: 경로 규칙 → 정규식 → NER(선택) → 판단 모델(Laya, 코드 이름은 Jev) → 정책 → 사람 승인 → 감사 로그
- **방금 끝낸 것:** 3d
  - `gate policy add "이름은 가려줘"` → `entities.name.action = mask`
  - 평가 세트 2개(첫 세트 120, 검증 세트 50)와 실행기 `eval/run_policy_text.py`
  - Laya v3 zero-shot 해석기 실험: 실패(모든 문장을 "모름"으로 답함). 기본 해석기는 키워드 Mock 그대로다.
- **PR #4는 머지했다**(사용자 요청, 머지 커밋 `cdc9671`). **PR #5는 열려 있다.** 머지는 사용자가 한다.
- **다음 첫 행동:** PR #5 머지 여부를 확인하고, 다음 작업을 사용자에게 고르게 한다("다음 할 일" 1번).

## 목표
일반인이 자기 컴퓨터에서 AI 에이전트를 쓸 때, 에이전트가 파일을 읽으면 반드시 게이트를 거치게 한다. 게이트는 허용·가림·사람 승인·차단 중 하나로 답한다. 처리 방식은 사용자가 정책으로 정하고, 정하지 않으면 기본값을 쓴다.

참고 문서:
- **현재 계획: `plans/implementation-plan.md`.** 명세와 다른 부분은 계획 문서를 따른다.
  - 2절 결정 표: "NER (3c)", "판단 모델 교체 (v3)", "정책 문장 → NER 묶음 (3d)" 행
  - 5절 Phase 3의 "3c NER 가림", "3d 정책 문장으로 NER 묶음 바꾸기"(설계·구현·평가 결과)
  - 6절 한계(끝부분에 3c·3d 항목)
- `eval/README.md`: "정책 문장 해석 평가 (3d)"(Mock·Laya 표, Laya 진단), "게이트 흐름 평가 (3c)"
- `eval/policy_sentences_notes.md`: 평가 세트 정답 원칙(정보가 주어인 "보여주지 마" = 가림, 파일이 주어면 거부 등)
- `README.md`: "정책 바꾸기", "알려진 한계"
- **점검 페이지:** https://claude.ai/artifact/AbemNcUnx4Uhj9d2B5jBhW
  - 버전 9(3b·3c까지)다. **3d는 아직 반영하지 않았다.**
  - "링크가 있는 누구나"로 공유되어 있어서, 고치면 보는 사람에게 바로 보인다.
- 3b 평가 보고서(팀원 공유용): https://claude.ai/artifact/4WaSWgekRir4frkRCq1a2L — 3b 체크포인트 기준이다.
- 원래 명세: `fuse-jev-gate-spec.md`. 저장소: https://github.com/Plant-Hackathon-2026/fuse-jev-gate

## 현재 위치
- 방금 끝낸 Phase: **3d**. 계획의 Phase 0~7과 3a·3b·3c·3d는 모두 끝났다.
- 다음: 정해진 Phase는 없다. 아래 후보 중 사용자가 고른다.

## 완료한 것 (이번 세션)
- **PR #4 머지**(사용자 요청, 머지 커밋 방식, 원격 브랜치 남김). `main`에서 `feat/policy-text-entities`를 만들었다.
- **진행 방식**(사용자 결정, 2026-10-31): 질문 단계와 평가 세트를 먼저 만들고 Mock으로 통과시킨다. 이어서 같은 세트로 Laya zero-shot을 실험한다.
- **해석기**(`gate/policy_text.py`)
  - type 선택지에 `entity`를 더했다. entity → `entity`(묶음 11개) → `entity_action`(deny·ask_human·mask·allow).
  - 안내 문구: 동작별 효과, plain 묶음은 다섯 가지가 함께 바뀐다는 안내.
  - Mock 단서: `_ENTITY_CUES`, `_ENTITY_ACTION_STEPS`(단계 순서로 찾고 지움), `_BROAD_CUES`, `_FILE_NAME`, `_PARTIAL_CUES`.
  - `LayaPolicyInterpreter`(`make_interpreter("laya")`): 질문을 `type: choice`로 보내고, 고른 선택지의 확률을 확신도로 쓴다.
- `gate/jev_client.py`: `laya_endpoint`·`laya_opener`·`laya_post`로 HTTP 부분을 뺐다(판단 모델과 해석기가 같이 씀).
- `gate/policy_cli.py`: `entities.*`를 저장할 때 NER이 꺼져 있으면 효과가 없다고 알린다.
- **평가 세트**(작업자 B 서브에이전트가 해석기 키워드 표를 보지 않고 작성)
  - `eval/policy_sentences.csv` 120문장, `eval/policy_sentences_holdout.csv` 50문장, `eval/policy_sentences_notes.md`
- **평가 결과**
  - Mock 첫 세트: 고치기 전 79.2%(잘못 저장 12, 느슨해짐 4) → 규칙 결함을 고친 뒤 98.3%(낙관적인 값)
  - **Mock 검증 세트(블라인드): 64.0%, 안전한 실패 14, 잘못 저장 4(느슨해짐 1)** → 과거형 "가렸" 등을 보태 잘못 저장 3(느슨해짐 0)
  - Laya v3 zero-shot: 두 세트 모두 24%. 문장 종류는 언제나 `unknown`(0.99~1.00), 묶음은 `etc`, 동작은 확신 있게 틀린다.
- **테스트:** 맥 **1266 통과·1 skip**, Linux 컨테이너(`fuse-jev-gate:phase3d`) **1267 통과**
- **커밋 3개 + PR #5:** https://github.com/Plant-Hackathon-2026/fuse-jev-gate/pull/5 (커밋·PR 본문에 AI 흔적 없음)

## 다음 할 일
1. **PR #5 머지 확인 → 다음 작업을 사용자에게 고르게 한다**
   - 확인 명령: `gh pr list -R Plant-Hackathon-2026/fuse-jev-gate --state all -L 3`
   - 머지됐으면 `git checkout main && git pull` 뒤 새 브랜치에서 한다.
   - 후보
     - **(a) 나이·연도 때문에 생기는 승인 줄이기.** v3는 "30대", "2016년"도 개인정보로 배워서, ②에서 잘 가린 준식별자 문서도 승인으로 간다(민감 24개 중 19개).
       - 방법 1: plain 묶음 기본값을 바꾼다(나이·연도도 가림).
       - 방법 2: 팀원에게 "가려야 할 것만 묻는" 질문으로 재학습을 요청한다.
       - 방법 3: 두 번째 판단에서 `has_name`·`has_contact` 같은 종류 질문을 쓴다.
       - 어느 쪽이든 36개로 다시 평가한다.
     - **(c) NER 미탐 보완.** 성 없는 이름(`월매`), `1990.01.01` 형식 생년월일, 도로명 번지, 지명(강릉·영주). threshold 0.3, 정규식 보완, 팀원 재학습 중 하나.
     - **(d) 3b 평가 보고서(4WaSWgekRir4frkRCq1a2L)에 v3·게이트 흐름 결과 반영.** 팀원 공유용이다.
     - **(e) 해석기를 실제 모델로 바꾸기**(3d 후속)
       - 공식 laya-multilingual 기반 체크포인트로 zero-shot을 다시 잰다. **내려받기가 필요하므로 먼저 사용자 확인을 받는다**(`LAYA_MODELS`에 넣으면 내려받는다. `POLICY_LAYA_MODEL`로 해석기만 그 모델을 쓰게 할 수 있다).
       - 또는 팀원에게 해석용 질문(type·entity·entity_action 등) 학습을 부탁한다. **평가 세트 두 개는 학습에 쓰지 말고 평가로만 쓴다.**
     - **(f) 점검 페이지(AbemNcUnx4Uhj9d2B5jBhW)에 3d 반영**(버전 10). 공유 중이라 고치면 바로 보인다.
2. **사용자 확인이 필요한 판단:** P115 "전화번호가 나오면 물어봐줘"
   - 평가 세트는 휴대폰·일반 전화가 다 해당하므로 다시 물어야 한다고 봤다.
   - Mock은 Phase 4부터 "전화번호"를 일반 전화(`numbers.phone`)로 읽는다.
   - 어느 쪽을 따를지 정해지면 Mock(`_KIND_CUES`) 또는 CSV를 고친다.
3. Phase를 끝내면 보고하고 멈춘다. 브랜치 + PR로 올린다. 커밋과 PR 본문에 AI 흔적을 남기지 않는다.

## 핵심 결정 · 함정
- **진행 규칙**
  - Phase마다 멈추고 보고한다.
  - 서브에이전트는 Opus로 띄운다. 파일을 나눠 맡기는 지시서 방식이 잘 맞는다.
  - **평가 세트는 블라인드로 만든다.** 해석기·탐지기를 만든 쪽이 시험 문장까지 쓰면 그 규칙에 맞춘 문장만 나온다. 고친 뒤에는 처음 보는 검증 세트로 다시 잰다(3d에서 98% → 64%).
  - 응답·문서는 한국어로 쓰고, 계획 문서는 `plans/`에 둔다.
  - 사용자는 설명이 어려우면 "풀어서 말해 달라"고 한다. 실제 예로 단계별로 설명하면 통한다.
  - 비용·용량이 드는 실행(모델 내려받기, 설치)은 실행 전에 확인받는다.
- **3d 해석기 결정**
  - 문장 하나는 바꿈 하나다. 묶음이 둘, 묶음 일부만 다르게, 묶음 부분 공개("시·도까지만"), 넓은 말("개인정보", "연락처")은 다시 묻는다.
  - "보여주지 마"는 주어로 가른다. 정보가 주어면 mask, 파일 단위 말("있는 파일", "있으면")이 있으면 deny다.
  - 주제 낱말("병원", "회사", "사건")만으로는 묶음으로 보지 않는다("병원 기록" = 맥락 규칙, "병원 이름" = org).
  - "이메일 주소"는 email, "IP 주소"는 host, "주소록.xlsx"는 경로다. 번호 단서를 먼저 지우고, 파일 이름과 경로 단서를 지운 뒤 묶음 단서를 찾는다.
  - 공개 범위 말("생년월일")은 번호 단서가 있을 때 **처음 한 번만** 지운다(두 번째는 묶음으로 남겨 섞인 문장으로 잡음).
  - 영문 "ip"는 낱말일 때만 "아이피"로 바꾼다("zip"에 걸리지 않게).
- **Mock 단서의 함정**
  - allow 단서("그대로", "보여줘")가 가장 위험하다. 이유를 말하는 부분이나 다른 동작과 섞이면 잘못 allow로 읽을 수 있다. 그래서 단계 순서(이중 부정 → 부정 allow → 물어봄 → 거부 → 보여주지 마 → 가림 → allow)로 지운다.
  - 느슨해지는 저장은 `gate policy add`가 한 번 더 묻는다(마지막 안전망).
- **Laya 해석기 함정**
  - v3는 해석용 질문을 배우지 않아 쓸 수 없다. 첫 질문이 언제나 `unknown`이라 잘못 저장이 0인 것은 **우연**이다. 동작 질문만 따로 물으면 확신 있게 틀린다. 다른 체크포인트를 쓰더라도 평가 세트로 먼저 잰다.
  - `split_question`은 `build_question`의 형식("선택지:" 줄, "- 선택지: 설명")에 기댄다. 질문 형식을 바꾸면 함께 고친다.
- **② 흐름의 뜻** (`gate/pipeline.py` `inspect`)
  - NER이 켜져 있고 가린 것(정규식·NER)이 있으면, Laya는 **가린 결과(보여 줄 내용)**를 본다.
    - label이 safe면 가림이고, 아니면 ask_human이다. 확신도 임계값은 쓰지 않는다.
  - 가린 것이 없거나 NER이 꺼져 있으면, 3b처럼 정규식 결과를 묻고 임계값(0.95) 두 개로 정한다.
  - Laya 오류는 언제나 차단이다.
  - NER을 끈 채 ②를 쓰면 안 된다. 코드도 NER이 켜졌을 때만 적용한다.
- **값 이름 함정:** `ner_backend` 값은 `none`/`server`다. YAML이 `off`를 false로 읽어서 `none`으로 정했다.
- **NER 쪽 결정**
  - 정규식 범위와 겹치는 NER 부분은 뺀다(정규식 우선). 남은 조각에 글자가 없으면 버린다.
  - NER의 PRIVATE_KEY 라벨은 쓰지 않는다(팀원 검증 F1 0.03).
  - 조사 떼기는 낱말로 넓히며 붙은 글자에서만 한다(`체육교육과`의 `과`).
- **Laya·NER 서버 함정**
  - laya-serve 기본 주소는 **0.0.0.0**이다. `LAYA_HOST=127.0.0.1`을 꼭 준다.
  - `LAYA_MODELS`를 빼면 공식 체크포인트를 내려받는다. 꼭 준다.
  - 요청에는 모델 이름을 넣는다(`JEV_MODEL`, 해석기는 `POLICY_LAYA_MODEL`이 먼저).
  - nerserve는 `127.0.0.1`로 띄운다(IP 리터럴만 받음).
  - NER 서버가 꺼진 채 `ner_backend: server`면 모든 읽기가 차단된다. 그래서 기본값이 none이다.
  - laya-serve를 `pkill -f` 패턴으로 끄면 안 잡힐 수 있다. `lsof -nP -iTCP:8000 -sTCP:LISTEN`으로 PID를 보고 끈다.
- **평가 함정**
  - 게이트 샘플 36개는 작다. 안전 12개라 오탐률은 한 건에 약 8%p 움직인다.
  - 정책 문장 세트도 작다(검증 세트 50, 한 건 2%p).
  - `must_hide.csv`는 원문만 보고 정했다. 시·도 이름, 나이·연도, 직함은 넣지 않았다.
  - 팀원 v3 학습 데이터는 받지 않아서, 우리 샘플과 겹치는지 확인하지 못했다.
- **거짓 안전**
  - `tests/test_ner_pipeline.py`·`test_ner_client.py`는 가짜 NER·가짜 서버다. 실제 모델 동작은 `eval.run_eval --ner server`로만 확인했다.
  - `tests/test_policy_text_entities.py`의 Laya 테스트는 가짜 laya-serve다. 실제 Laya의 해석 성능은 `eval.run_policy_text --interpreter laya`로만 확인했다(실패).
  - 첫 평가 세트의 Mock 98.3%는 그 세트를 보고 고친 값이다. 실력은 검증 세트 64%다.
- **흔들리는 테스트:** `tests/test_approval.py::test_dialog_failure_is_reported`(1.5초 제한)는 모델 서버가 돌아 컴퓨터가 바쁠 때 한 번 실패한 적이 있다. 이번 세션에서는 통과했다.
- **환경 함정**
  - 셸 heredoc으로 긴 한글 파이썬 스크립트를 넘기면 가끔 바이트가 깨진다는 기록이 있다. 이번 세션의 짧은 heredoc은 문제없었다. 긴 스크립트는 파일로 써서 실행한다.
  - zsh에서 `echo =====`는 `=` 확장 때문에 오류가 난다. 따옴표로 감싼다.

## 관련 파일
- `gate/policy_text.py`: `interpret`(entity 분기), `_OPTION_GUIDES`, `_ENTITY_CUES`·`_ENTITY_ACTION_STEPS`·`_type_choice`·`_entity_action_choice`, `LayaPolicyInterpreter`·`split_question`
- `gate/policy_cli.py` `_add`: NER 꺼짐 안내
- `gate/jev_client.py`: `laya_endpoint`·`laya_opener`·`laya_post`
- `gate/policy.py`: `entities.*` 선택지(`_entity_settings`), `loosened`
- `eval/run_policy_text.py`, `eval/policy_sentences.csv`, `eval/policy_sentences_holdout.csv`, `eval/policy_sentences_notes.md`
- 테스트: `tests/test_policy_text_entities.py`, `tests/test_eval_policy_text.py`, `tests/test_policy_text.py`, `tests/test_policy_cli.py`
- 3c 쪽: `gate/pipeline.py`, `gate/ner_client.py`, `nerserve/`, `eval/run_eval.py`, `eval/must_hide.csv`
- 저장소 밖
  - `hackathon/models/`: `laya`(3b), `laya-v3`(지금), `ner`, `app`(팀원 코드, 저장소에 넣지 않음)
  - `hackathon/laya-serve/`: `.venv`(laya 0.4.18, NER 서버도 이것으로 돌림), `logs/serve-v3.log`·`nerserve.log`, `hf-cache`(비어 있음: 공식 체크포인트 없음)

## 실행 · 검증
- **laya-serve (v3)** — `hackathon/laya-serve`에서 백그라운드로 띄운다. 시작에 수십 초 걸린다.
  ```
  LAYA_HOST=127.0.0.1 LAYA_PORT=8000 LAYA_MODELS=gate-laya-v3 LAYA_DEFAULT_MODEL=gate-laya-v3 \
  LAYA_EXTRA_MODELS='{"gate-laya-v3": "/Users/sangwoo/work/personal/hackathon/models/laya-v3"}' \
  HF_HUB_OFFLINE=1 HF_HOME=$PWD/hf-cache USE_TF=0 .venv/bin/laya-serve > logs/serve-v3.log 2>&1
  ```
  - 준비 확인: `curl -s http://127.0.0.1:8000/health`
- **NER 서버** — `fuse-jev-gate`에서 백그라운드로 띄운다. 로드 약 4초.
  ```
  HF_HUB_OFFLINE=1 ../laya-serve/.venv/bin/python -m nerserve --model-dir ../models/ner --host 127.0.0.1 --port 8001 --device auto > ../laya-serve/logs/nerserve.log 2>&1
  ```
- **정책 문장 평가** — `fuse-jev-gate`에서:
  ```
  uv run python -m eval.run_policy_text --interpreter mock --details
  uv run python -m eval.run_policy_text --interpreter mock --sentences eval/policy_sentences_holdout.csv --details
  JEV_BASE_URL=http://127.0.0.1:8000 JEV_MODEL=gate-laya-v3 uv run python -m eval.run_policy_text --interpreter laya --details
  ```
- **게이트 평가** — `fuse-jev-gate`에서:
  ```
  NER_BASE_URL=http://127.0.0.1:8001 JEV_BASE_URL=http://127.0.0.1:8000 JEV_MODEL=gate-laya-v3 \
  JEV_MODEL_DIR=/Users/sangwoo/work/personal/hackathon/models/laya-v3 \
  uv run python -m eval.run_eval --client laya --ner server --details [--show]
  ```
- **테스트(맥):** `uv run pytest` — **1266 통과, 1 skip**, 약 30초
- **테스트(Linux):** `docker build -f sandbox/gate.Dockerfile -t fuse-jev-gate:phase3d . && docker run --rm --device /dev/fuse --cap-add SYS_ADMIN fuse-jev-gate:phase3d pytest -q` — **1267 통과**
- **인프라·런타임 상태**
  - **laya-serve와 NER 서버는 꺼져 있다**(laya-serve는 세션 끝에 껐다).
  - 게이트 데몬도 꺼져 있다.
  - `gate-data/policy.yaml`은 없다(기본 정책: Mock Jev, NER 끔).
  - Docker Desktop은 켜져 있다.

## 미해결 · 블로커
1. **PR #5 머지:** 열려 있다. 독립 리뷰는 하지 않았다. 머지 전에 리뷰를 원하는지 사용자에게 묻는다.
2. **P115 "전화번호" 해석:** 위 "다음 할 일" 2번.
3. **점검 페이지:** 3d를 반영하지 않았다(버전 9). 3c 때 SVG 그림 1·2의 겹침은 아직 사람이 보지 않았다.
4. **다음 작업 선택:** 위 후보 (a)·(c)·(d)·(e)·(f) 중 사용자가 고른다.
5. **팀원 학습 데이터의 출처·라이선스:** 확인하지 못했다. 가중치는 저장소에 넣지 않으므로 지금은 막히지 않는다.
