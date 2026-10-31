# 핸드오프 — fuse-jev-gate (에이전트 파일 접근 게이트)
_업데이트: 2026-10-31 · 작성 세션: 3c. NER 가림 단계를 넣고, 판단 모델을 팀원 Laya v3로 바꾸고, 판단 흐름을 ②(가린 결과를 Laya가 다시 판단)로 정했다. 브랜치 `feat/ner-masking` + PR #4_

## TL;DR (30초 복원)
- **프로젝트:** 일반인이 로컬에서 쓰는 AI 에이전트가 원본 문서를 게이트를 거쳐서만 읽게 하는 도구다.
  - 게이트 순서: 경로 규칙 → 정규식 → **NER(선택)** → 판단 모델(Laya, 코드 이름은 Jev) → 정책 → 사람 승인 → 감사 로그
- **방금 끝낸 것:** 3c
  - NER 서버 `nerserve`
  - `entities.*` 정책
  - ② 흐름(가린 것이 있으면 가린 결과를 Laya에 다시 물어 안전하면 보여 주고, 아니면 사람 승인)
  - Laya v3(`has_pii`) 교체
- **PR #4는 열려 있다.** 머지는 사용자가 한다.
- **다음 첫 행동:** PR #4 머지 여부를 확인하고, 사용자에게 다음 작업을 고르게 한다("다음 할 일" 1번). 먼저 하나를 밀지 않는다.

## 목표
일반인이 자기 컴퓨터에서 AI 에이전트를 쓸 때, 에이전트가 파일을 읽으면 반드시 게이트를 거치게 한다. 게이트는 허용·가림·사람 승인·차단 중 하나로 답한다. 처리 방식은 사용자가 정책으로 정하고, 정하지 않으면 기본값을 쓴다.

참고 문서:
- **현재 계획: `plans/implementation-plan.md`.** 명세와 다른 부분은 계획 문서를 따른다.
  - 2절 결정 표: "NER (3c)", "판단 모델 교체 (v3)" 행
  - 5절 Phase 3의 "3c NER 가림 (완료)": 흐름·묶음 표·구현 결과·평가 결과
  - 6절 한계(끝부분에 3c 항목)
- `plans/ner-parallel.md`: 3c 병렬 지시서. NER 서버 HTTP 계약이 여기 있다.
- `eval/README.md`: "Laya v3 평가 결과", "게이트 흐름 평가 (3c, NER 포함)"(①/②/NER 끔 비교 표)
- `README.md`: "판단 모델 연결 (Laya)"(v3), "이름·주소 가리기 (NER)", "알려진 한계"
- **점검 페이지:** https://claude.ai/artifact/AbemNcUnx4Uhj9d2B5jBhW
  - 이번 세션 끝에 서브에이전트가 3b·3c를 반영하는 중이다. 결과는 아래 "미해결" 참고.
  - "링크가 있는 누구나"로 공유되어 있어서, 고치면 보는 사람에게 바로 보인다.
- 3b 평가 보고서(팀원 공유용): https://claude.ai/artifact/4WaSWgekRir4frkRCq1a2L
  - 3b 체크포인트 기준이다. v3 결과는 들어 있지 않다.
- 원래 명세: `fuse-jev-gate-spec.md`. 저장소: https://github.com/Plant-Hackathon-2026/fuse-jev-gate

## 현재 위치
- 방금 끝낸 Phase: **3c**. NER 가림, Laya v3 교체, ② 흐름을 했다. 계획의 Phase 0~7과 3a·3b는 모두 끝났다.
- 다음: 정해진 Phase는 없다. 아래 후보 중 사용자가 고른다.

## 완료한 것 (이번 세션)
- **PR #3 머지:** 사용자 요청으로 머지 커밋 방식으로 머지했다. 원격 브랜치는 남겨 두었다. 이어서 `main`에서 `feat/ner-masking` 브랜치를 만들었다.
- **NER 설계 결정** (사용자, 2026-10-31):
  - 별도 로컬 NER 서버로 띄운다.
  - NER이 찾은 것은 `[이름]`처럼 종류 이름으로 가린다.
  - 정책 키는 `entities.<묶음>.action`이고 묶음은 11개다. 기본은 mask이고, plain(시간·연도·나이·직함·과목)만 allow다. 전화·이메일·비밀값은 `numbers.*` 칸을 따른다.
- **NER 서버** (작업자 A): `nerserve/`, `gate/ner_spans.py`
  - 127.0.0.1(IP 리터럴)만 받는다. `POST /v1/ner`, `GET /health`.
  - 256토큰 창을 32토큰 겹쳐 읽는다. 문서당 약 50ms(mps), 모델 로드 약 4초.
- **데몬 쪽** (메인):
  - `gate/ner_client.py`: 응답 검사, `policy_slot`, `outside`, `MASK_TEXT`
  - `gate/detectors.py`: `mask(replacements=)`
  - `gate/policy.py`: `entities.*`, `ner_backend`(none·server), `ner_timeout_seconds`, `slot_action`
  - `gate/pipeline.py`: NER 단계, ② 재판단, NER 결과 캐시, 판단 캐시 키에 NER 모델 식별값
  - `gate policy show`에 묶음 표시
  - 승인 창·CLI 문구를 "가린 것"으로 바꿈
  - `gate/protocol.py` 문서: NER로 가리면 길이가 달라진다
- **Laya v3** (사용자가 `~/Downloads/export_v3`로 줌):
  - 체크포인트를 `hackathon/models/laya-v3`로 복사했다(해시 같음).
  - 질문이 9개라 `LayaJevClient`가 `has_pii`를 고르게 했다(`JEV_QUESTION`).
  - 결과: 오탐 6/12 → **2/12**, 미탐 0/24.
  - 팀원 방식(종류 질문 6개 중 최대)은 미탐 1이라 고르지 않았다.
- **흐름 ①→②:** ①로 게이트 흐름을 평가하니 NER이 놓친 식별자 8줄·준식별자 8줄이 그대로 나갔다. 사용자가 ②를 골랐다.
- **평가:** `eval/run_eval.py --ner server`
  - 실제 `Inspector`로 36개를 돌리고, `eval/must_hide.csv`(작업자 B, 118줄)로 누출을 센다.
  - ② 결과:
    - 민감 24개: 가림 5 · 승인 19
    - 안전 12개: 허용 5 · 가림 2 · 승인 5
    - 승인 없이 남은 식별자 1(`월매`), 준식별자 0
- **테스트:** 맥 **1172 통과·1 skip**, Linux 컨테이너(`fuse-jev-gate:phase3c`) **1173 통과**
- **커밋 5개 + PR #4:** https://github.com/Plant-Hackathon-2026/fuse-jev-gate/pull/4
  - 커밋·PR 본문에 AI 흔적 없음
- 메모리(`fuse-jev-gate-project.md`)에 결정과 결과를 적었다.

## 다음 할 일
1. **PR #4 머지 확인 → 사용자에게 다음 작업을 고르게 한다.**
   - 확인 명령: `gh pr list -R Plant-Hackathon-2026/fuse-jev-gate --state all -L 3`
   - 머지됐으면 `git checkout main && git pull` 뒤 새 브랜치에서 한다.
   - 후보 (하나를 먼저 밀지 않는다):
     - **(a) 나이·연도 때문에 생기는 승인 줄이기.**
       - 원인: v3는 나이·연도("30대", "2016년")도 개인정보로 배웠다. 그래서 ②에서 잘 가린 준식별자 문서도 승인으로 간다(민감 24개 중 19개 승인).
       - 방법 1: 정책 plain 묶음 기본값을 바꾼다(나이·연도도 가림).
       - 방법 2: 팀원에게 "가려야 할 것만 묻는" 질문으로 재학습을 요청한다.
       - 방법 3: 두 번째 판단에서 `has_name`·`has_contact` 같은 종류 질문을 쓴다.
       - 어느 쪽이든 36개로 다시 평가한다.
     - **(b) 정책 문장으로 NER 묶음 바꾸기.**
       - `gate/policy_text.py`의 질문 단계에 `entities`를 더한다.
       - 지금은 "이름은 가려줘"가 맥락 규칙으로 저장되고, Laya는 맥락 규칙을 읽지 않아 효과가 없다.
     - **(c) NER 미탐 보완.** 성 없는 이름(`월매`), `1990.01.01` 형식 생년월일, 도로명 번지, 지명(강릉·영주).
       - NER threshold를 0.3으로 낮추면 일부를 더 잡는다(`홍길순`·`순천` 두 번째, `가나다내과의원`). 안전 문서 오탐은 3 그대로다.
       - 또는 정규식 보완, 팀원 NER 재학습.
     - **(d) 3b 평가 보고서(4WaSWgekRir4frkRCq1a2L)에 v3·게이트 흐름 결과 반영.** 팀원 공유용이다.
2. **팀원이 체크포인트를 다시 주면:**
   1. 저장소 밖 `hackathon/models/`에 둔다.
   2. 아래 "실행 · 검증" 명령으로 서버를 다시 띄운다.
   3. `eval.run_eval --client laya [--ner server] --details`로 다시 평가한다.
   - 질문 구성이 다르면 `JEV_QUESTION`을 정하거나 `_check_questions`를 확인한다.
3. Phase를 끝내면 보고하고 멈춘다. 브랜치 + PR로 올린다. 커밋과 PR 본문에 AI 흔적을 남기지 않는다.

## 핵심 결정 · 함정
- **진행 규칙**
  - Phase마다 멈추고 보고한다.
  - 서브에이전트는 Opus 5.5로 띄운다. 파일을 나눠 맡기는 지시서 방식이 잘 맞는다.
  - 응답·문서는 한국어로 쓰고, 계획 문서는 `plans/`에 둔다.
  - 사용자는 설명이 어려우면 "풀어서 말해 달라"고 한다. 실제 샘플 문서를 예로 단계별로 설명하면 통한다.
  - 비용·용량이 드는 실행(모델 내려받기, 설치)은 실행 전에 확인받는다.
- **② 흐름의 뜻** (`gate/pipeline.py` `inspect`)
  - NER이 켜져 있고 가린 것(정규식·NER)이 있으면, Laya는 **가린 결과(보여 줄 내용)**를 본다.
    - label이 safe면 가림이고, 아니면 ask_human이다. 확신도 임계값은 쓰지 않는다.
  - 가린 것이 없거나 NER이 꺼져 있으면, 3b처럼 정규식 결과를 묻고 임계값(0.95) 두 개로 정한다.
  - Laya 오류는 언제나 차단이다.
  - NER을 끈 채 ②를 쓰면 안 된다(정규식만 가리고 이름이 남은 채 보여 주게 된다). 코드도 NER이 켜졌을 때만 적용한다.
- **값 이름 함정:** `ner_backend` 값은 `none`/`server`다. YAML이 `off`를 false로 읽어서 `none`으로 정했다.
- **NER 쪽 결정**
  - 정규식 범위와 겹치는 NER 부분은 뺀다(정규식 우선). 남은 조각에 글자가 없으면 버린다(`)`만 남는 경우).
  - NER의 PRIVATE_KEY 라벨은 쓰지 않는다(팀원 검증 F1 0.03).
  - 조사 떼기는 낱말로 넓히며 붙은 글자에서만 한다. 그렇지 않으면 `체육교육과`의 `과`가 떨어진다.
- **Laya·NER 서버 함정**
  - laya-serve 기본 주소는 **0.0.0.0**이다. `LAYA_HOST=127.0.0.1`을 꼭 준다.
  - `LAYA_MODELS`를 빼면 공식 체크포인트를 내려받는다. 꼭 준다.
  - 요청에는 모델 이름을 넣는다(`JEV_MODEL`).
  - nerserve는 `localhost`가 아니라 `127.0.0.1`로 띄운다(IP 리터럴만 받음).
  - NER 서버가 꺼진 채 `ner_backend: server`면 모든 읽기가 차단된다. 그래서 기본값이 none이다.
- **평가 함정**
  - 샘플 36개는 작다. 안전 12개라 오탐률은 한 건에 약 8%p 움직인다.
  - `must_hide.csv`는 원문만 보고 정했다. 시·도 이름, 나이·연도, 직함은 넣지 않았다.
  - 부분 문자열 검사라 "1990년 1월 1일"이 일부만 가려지면 누출로 잡히지 않는다(연도는 정책상 보여 줌).
  - 팀원 v3 학습 데이터는 받지 않아서, 우리 샘플과 겹치는지 확인하지 못했다. 함께 받은 평가 데이터(`data_v3`, `hard_test`)에는 우리 샘플 문장이 없다.
- **거짓 안전**
  - `tests/test_ner_pipeline.py`·`test_ner_client.py`는 가짜 NER·가짜 서버다. 실제 모델 동작은 `eval.run_eval --ner server`로만 확인했다.
  - `test_nerserve.py`는 가짜 예측 함수를 쓴다(torch 없이).
- **흔들리는 테스트:** `tests/test_approval.py::test_dialog_failure_is_reported`(1.5초 제한)는 모델 서버가 돌아 컴퓨터가 바쁠 때 한 번 실패했다. 단독 실행과 재실행에서는 통과했다.
- **환경 함정**
  - 셸 heredoc으로 긴 한글 파이썬 스크립트를 넘기면 가끔 바이트가 깨진다(`SyntaxError: Non-UTF-8 code`). 긴 스크립트는 파일로 써서 실행한다.
  - 이 터미널은 `~/Downloads` 목록은 못 봐도 정확한 하위 경로는 읽을 수 있었다.

## 관련 파일
- `gate/pipeline.py` `Inspector.inspect`·`_entities`·`_judge`: NER 단계, ② 재판단, 캐시
- `gate/ner_client.py`: 묶음·라벨 표, `ServerNerClient`, `parse_entities`, `outside`, `policy_slot`
- `gate/ner_spans.py`, `nerserve/`(`server.py`, `model.py`, `__main__.py`): NER 서버
- `gate/policy.py`: `entities.*`, `ner_backend`, `slot_rule`·`slot_action` / `config/policy.yaml`: 기본값 설명(테스트가 코드와 같은지 확인)
- `gate/jev_client.py` `_check_questions`·`LayaJevClient(question=)`: 여러 질문 체크포인트
- `eval/run_eval.py` `run_gate`·`format_gate_runs`, `eval/must_hide.csv`
- 테스트: `tests/test_ner_client.py`, `test_ner_pipeline.py`, `test_ner_spans.py`, `test_nerserve.py`, `test_eval.py`, `test_laya_client.py`
- 저장소 밖
  - `hackathon/models/`: `laya`(3b), `laya-v3`(지금), `ner`, `app`(팀원 코드, 저장소에 넣지 않음)
  - `hackathon/laya-serve/`: `.venv`(torch 2.15·transformers 5.22.1, NER 서버도 이것으로 돌림), `logs/serve-v3.log`·`nerserve.log`

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
  - 준비 확인: `curl -s http://127.0.0.1:8001/health`
- **평가** — `fuse-jev-gate`에서:
  ```
  NER_BASE_URL=http://127.0.0.1:8001 JEV_BASE_URL=http://127.0.0.1:8000 JEV_MODEL=gate-laya-v3 \
  JEV_MODEL_DIR=/Users/sangwoo/work/personal/hackathon/models/laya-v3 \
  uv run python -m eval.run_eval --client laya --ner server --details [--show]
  ```
- **테스트(맥):** `uv run pytest` — **1172 통과, 1 skip**, 약 30초
- **테스트(Linux):** `docker build -f sandbox/gate.Dockerfile -t fuse-jev-gate:phase3c . && docker run --rm --device /dev/fuse --cap-add SYS_ADMIN fuse-jev-gate:phase3c pytest -q` — **1173 통과**
- **데몬을 v3 + NER로 띄우기:** 위 `JEV_*`·`NER_BASE_URL` 환경변수를 주고 `gate serve`를 띄운다. 이어서 내 터미널에서 다음을 실행한다.
  - `gate policy set jev_backend laya`
  - `gate policy set ner_backend server`
- **인프라·런타임 상태**
  - **laya-serve와 NER 서버는 세션 끝에 껐다.**
  - 게이트 데몬도 꺼져 있다.
  - `gate-data/policy.yaml`은 없다(기본 정책: Mock Jev, NER 끔).
  - Docker Desktop은 켜져 있다.

## 미해결 · 블로커
1. **PR #4 머지:** 사용자가 리뷰하고 머지한다.
2. **점검 페이지 3b·3c 반영:** 세션 끝에 서브에이전트에게 맡겼다. 끝났는지는 이 세션의 마지막 보고에 있다. 새 세션에서는 페이지를 `Artifact read`로 열어 3c 행이 있는지 확인한다.
3. **다음 작업 선택:** 위 후보 (a)~(d) 중 사용자가 고른다.
4. **팀원 학습 데이터의 출처·라이선스:** 확인하지 못했다. 가중치는 저장소에 넣지 않으므로 지금은 막히지 않는다.
