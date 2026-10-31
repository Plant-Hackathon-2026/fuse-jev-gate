# 핸드오프 — fuse-jev-gate (에이전트 파일 접근 게이트)
_업데이트: 2026-10-31 · 작성 세션: Phase 3b. 판단 모델을 Jev 대신 팀원이 파인튜닝한 Laya로 정해 `laya` 백엔드를 붙이고 실제 한국어 평가를 돌렸다. 브랜치 `feat/phase3b-laya` + PR_

## TL;DR (30초 복원)
- **프로젝트:** 일반인이 로컬에서 쓰는 AI 에이전트가 원본 문서를 게이트를 거쳐서만 읽게 하는 도구다. 게이트 순서는 경로 규칙 → 정규식 → 판단 모델(Jev 자리) → 정책 → 사람 승인 → 감사 로그다.
- **방금 끝낸 것:** Phase 3b. 팀원 Laya를 로컬 `laya-serve`로 띄워 `jev_backend: laya`로 연결했다. 평가 결과는 미탐 0/24, **오탐 6/12**(상담 외 문서 형식에서 많음)이다.
- **사용자 결정:** 팀원이 추가 학습 중이다. 그래서 지금 모델로 연결을 끝내고, 재학습한 체크포인트가 오면 폴더만 바꿔 다시 평가한다.
- **다음 첫 행동:** PR 머지를 확인한다. 그다음 사용자에게 다음 작업을 묻는다. 후보는 (a) 재학습 체크포인트 교체·재평가, (b) NER 단계다("다음 할 일" 1번).

## 목표
일반인이 자기 컴퓨터에서 AI 에이전트를 쓸 때, 에이전트가 파일을 읽으면 반드시 게이트를 거치게 한다. 게이트는 허용·마스킹·사람 승인·차단 중 하나로 응답한다. 처리 방식은 사용자가 정책 문장(또는 설정)으로 지정하고, 지정하지 않으면 기본값을 쓴다.

참고 문서:
- **README.md:** 사용자용 설명이다. 새 절 "판단 모델 연결 (Laya)"이 있다.
- **현재 계획: `plans/implementation-plan.md`.** 명세와 다른 부분은 계획 문서를 따른다.
  - 2절 결정 표: "판단 모델 (3b)", "Laya 실행 방식 (3b)", "NER (3b 다음)" 행
  - 5절 Phase 3: "3b (완료)" 절에 구현·평가·교체 절차가 있다
  - 6절 한계, 7절 검증
- **평가 문서: `eval/README.md`** "Laya 평가 결과 (Phase 3b)"
- **평가 보고서(아티팩트, 버전 2):** https://claude.ai/artifact/4WaSWgekRir4frkRCq1a2L
  - 그림, 샘플별 표, 팀원 모델 분석, 대안 세 가지, 재학습 요청 사항이 있다.
  - 원본 HTML은 이전 세션 scratchpad에 있다. 고치려면 `Artifact read`로 다시 받는다.
  - 사용자가 "링크가 있는 누구나"로 공유했다. 고치면 보는 사람에게 바로 반영된다.
  - 페이지 수치는 문서 전체를 한 번에 물은 결과(오탐 7/12)다. 저장소 공식 결과(오탐 6/12)와의 차이를 설명하는 문단을 버전 2에 넣었다.
- 점검 페이지: https://claude.ai/artifact/AbemNcUnx4Uhj9d2B5jBhW (버전 8). **3b는 아직 반영하지 않았다.**
- 원래 명세: `fuse-jev-gate-spec.md`. 저장소: https://github.com/Plant-Hackathon-2026/fuse-jev-gate

## 현재 위치
- 방금 끝낸 Phase: Phase 3b — 실제 판단 모델(팀원 Laya) 연결 + 한국어 평가
- 다음: 사용자가 정한다. 재학습 체크포인트 교체·재평가, 또는 NER 단계(정규식 다음·판단 앞에서 이름·주소 등을 가림)

## 완료한 것 (이번 세션)
- **PR #2 머지.** 사용자 요청으로 머지했다(머지 커밋 방식, 원격 브랜치는 남겨 둠).
- **팀원 결과물 분석**
  - 공유 Drive `models/`를 사용자가 내려받았고, 저장소 밖 `hackathon/models/`로 옮겼다.
    - `laya/`: mmBERT-base 322M, 예/아니오(noul) 질문 하나, 보정 온도 3.21
    - `ner/`: xlm-roberta-large, 32종 BIO
    - `app/guard.py`: NER + gitleaks 규칙 + Laya + 정책
  - 중복으로 받은 `~/Downloads/model-001.safetensors`(ner 가중치와 같은 SHA-256)는 휴지통으로 보냈다.
- **laya-serve 설치:** 저장소 밖 `hackathon/laya-serve/.venv`, laya[serve]==0.4.18
- **`LayaJevClient`** (`gate/jev_client.py`)
  - 환경변수 `JEV_BASE_URL`·`JEV_MODEL`·`JEV_MODEL_DIR`·`JEV_API_KEY`로 설정한다.
  - 450자 조각으로 나눠 batch로 묻고, 가장 큰 p를 쓴다.
  - 잘린 응답, 형식 오류, 연결 실패는 모두 차단한다.
  - 이 컴퓨터 주소만 받고, 프록시·리디렉트는 쓰지 않는다.
- **판단 캐시 키에 모델 식별값 추가** (`gate/cache.py`, `gate/pipeline.py`의 `cache_tag`)
- **평가 보고서에 "안전 여부 정확도" 줄 추가**
- **테스트**
  - 가짜 laya-serve로 시험하는 `tests/test_laya_client.py` 62개
  - pipeline 2개
  - 맥 975 통과·1 skip, Linux 컨테이너 976 통과(`fuse-jev-gate:phase3b`)
- **실제 확인**
  - `eval.run_eval --client laya`: 미탐 0/24, 오탐 6/12, 안전 여부 정확도 30/36, 평균 52ms
  - 시험용 데몬(scratchpad, `jev_backend: laya`): 허용·차단·캐시·승인 시간 초과·감사 로그를 확인했다.

## 다음 할 일
1. **PR 머지 확인 → 다음 작업을 사용자에게 묻는다.** 하나를 먼저 밀지 않는다.
   - `gh pr list -R Plant-Hackathon-2026/fuse-jev-gate --state all -L 3`로 머지를 확인하고 `git checkout main && git pull`.
   - **(a) 재학습 체크포인트 교체·재평가**(팀원 체크포인트가 왔을 때)
     1. 새 폴더를 `hackathon/models/laya`(또는 새 경로)에 둔다.
     2. laya-serve를 다시 띄운다(아래 "실행 · 검증").
     3. `JEV_MODEL_DIR=… uv run python -m eval.run_eval --client laya --details`로 36개를 다시 돌린다.
     4. 지금 결과(오탐 6/12, 미탐 0/24)와 비교해 보고한다.
     - 질문이 예/아니오가 아니면 `LayaJevClient`의 답 옮기기를 고쳐야 한다(지금은 차단으로 끝난다).
   - **(b) NER 단계**(2절 "NER (3b 다음)"). 착수 전에 사용자에게 물을 것:
     - 실행 방식: laya-serve는 NER을 띄우지 않으므로 transformers 서버를 따로 띄울지
     - 가림 형식: 지금 같은 길이 `*`를 유지할지, 팀원처럼 `[이름]`으로 할지
     - 종류별 동작 정책 키: 32종을 지금 번호 종류처럼 둘지
     - Laya와의 결합: 팀원 비교에서 AND가 오탐 0.94%. 우리 36개로 미탐 0이 유지되는지 확인해야 한다.
2. 점검 페이지(AbemNcUnx4Uhj9d2B5jBhW)에 3b를 반영할지 사용자에게 묻는다.
3. Phase가 끝나면 보고하고 멈춘다. 브랜치 + PR로 올린다. 커밋과 PR 본문에 AI 흔적을 남기지 않는다.

## 핵심 결정 · 함정
- **진행 규칙**
  - 사용자는 Phase마다 멈추고 결과를 보고받은 뒤 다음으로 갈지 정한다.
  - 서브에이전트는 Opus 5.5로 띄운다. 응답과 문서는 한국어로 쓴다. 계획 문서는 `plans/`에 둔다.
  - **커밋 메시지와 PR 본문에 AI 흔적(Co-Authored-By, Generated with 등)을 남기지 않는다**(사용자 전역 지침).
  - 비용·용량이 드는 실행(하네스, 큰 모델 내려받기, 설치)은 실행 전에 확인받는다.
- **Jev 자리 결정**
  - 로컬에서 돌린다. 판단 모델은 Jev 대신 팀원 Laya다. 코드 이름(Jev, `jev_backend`)은 그대로 둔다.
  - 가중치와 팀원 코드는 공개 저장소에 넣지 않는다.
  - 판단 모델에 보내는 텍스트는 정규식으로 가린 뒤 보낸다. 원문으로 보내도 결과가 같았으므로 규칙을 유지한다.
- **laya-serve 함정**
  - 기본 주소가 **0.0.0.0**이다. `LAYA_HOST=127.0.0.1`을 꼭 준다.
  - `LAYA_MODELS`를 빼면 **공식 체크포인트를 모두 내려받는다.** `LAYA_MODELS=gate-laya`와 `HF_HUB_OFFLINE=1`을 준다.
  - 요청에는 항상 `model: gate-laya`를 넣는다. 넣지 않으면 영어 문장이 공식 english 체크포인트로 갈 수 있다.
- **평가 함정**
  - 3종 정확도(18/36)는 의미가 적다. 예/아니오 모델이라 개인정보와 준식별자를 나누지 못한다. "안전 여부 정확도"를 본다.
  - 짧은 조각은 p가 높게 나온다("오늘 회의는 3시입니다." 0.72). 그래서 조각을 고르게 나눈다(어느 조각도 창의 30%보다 짧지 않게).
  - 조각으로 나누면 문서 전체를 물을 때와 결과가 다를 수 있다. 전체로 물으면 오탐 7/12였다.
  - 팀원 평가 수치(0.97)는 평가와 학습이 겹친다(6,000 중 1,120).
  - 안전 샘플이 12개뿐이라 오탐률은 한 건에 약 8%p 움직인다.
- **환경 함정**
  - 이 터미널은 `~/Downloads` 목록을 못 본다(macOS 개인정보 보호). 파일 찾기는 `mdfind -name`을 쓴다.
  - Drive 커넥터로는 작은 파일만 읽을 수 있다. 큰 파일은 사용자가 브라우저로 받는다. 폴더 zip은 실패했다.
  - Chrome 확장은 `file://`을 못 연다.
  - 맥 소켓 경로 104바이트 제한이 있다. scratchpad에서 데몬을 띄울 때는 그 폴더로 `cd`해 상대 경로 `run/gate.sock`을 쓴다.
- **거짓 안전**
  - `tests/test_laya_client.py`는 가짜 서버다. 실제 모델 동작은 `eval.run_eval --client laya`와 시험 데몬으로만 확인했다.
  - 맥락 문장(사용자 정책 칸)은 Mock도 Laya도 쓰지 않는다.
  - 모델 식별값은 `questions.json`과 가중치의 크기·수정 시각으로 만든다. 같은 크기·같은 시각으로 덮어쓰면 못 알아챈다.
  - 하네스의 `invalid`는 "막힘"이 아니다(이전 세션 내용 그대로).

## 관련 파일
- `gate/jev_client.py`: `LayaJevClient`, `split_windows`, `cache_tag`, `LAYA_*` 상수, `_BACKENDS`(laya, mock), `make_client`(네트워크 백엔드에 타임아웃 전달)
- `gate/pipeline.py` `_judge`, `gate/cache.py` `CacheKey.model`: 모델 식별값이 들어간 캐시
- `eval/run_eval.py` `Stats.safe_correct`, `eval/README.md`: 평가와 결과
- `tests/test_laya_client.py`: 가짜 laya-serve 테스트. `tests/test_pipeline.py` `TaggedJev`.
- `plans/implementation-plan.md`: 2절 결정, 5절 Phase 3 "3b (완료)", 6절 한계, 7절 검증
- 저장소 밖
  - `hackathon/models/`: 팀원 laya·ner·app, README, 학습 로그
  - `hackathon/laya-serve/`: `.venv`, `logs/serve.log`, `hf-cache`(비어 있어야 정상)

## 실행 · 검증
- **laya-serve** (`hackathon/laya-serve`에서, 백그라운드):
  ```
  LAYA_HOST=127.0.0.1 LAYA_PORT=8000 LAYA_MODELS=gate-laya LAYA_DEFAULT_MODEL=gate-laya \
  LAYA_EXTRA_MODELS='{"gate-laya": "/Users/sangwoo/work/personal/hackathon/models/laya"}' \
  HF_HUB_OFFLINE=1 HF_HOME=$PWD/hf-cache USE_TF=0 .venv/bin/laya-serve > logs/serve.log 2>&1
  ```
  - 준비 확인: `curl -s http://127.0.0.1:8000/health`. `loaded: ["gate-laya"]`, `device: mps`가 나와야 한다. 시작에 약 16초 걸린다.
- **평가:** `cd fuse-jev-gate && JEV_BASE_URL=http://127.0.0.1:8000 JEV_MODEL_DIR=/Users/sangwoo/work/personal/hackathon/models/laya uv run python -m eval.run_eval --client laya [--details]`
- **테스트(맥):** `uv run pytest` — **975 통과, 1 skip**, 약 22초
- **테스트(Linux):** `docker build -f sandbox/gate.Dockerfile -t fuse-jev-gate:phase3b . && docker run --rm --device /dev/fuse --cap-add SYS_ADMIN fuse-jev-gate:phase3b pytest -q` — **976 통과**
- **데몬을 laya로 띄우기:** 위 `JEV_*` 환경변수를 주고 `gate serve`를 띄운 뒤, 내 터미널에서 `gate policy set jev_backend laya`
- **인프라·런타임 상태**
  - 사용자 데몬(PID 63433, 대화상자 켬, 감사 로그 켬)은 이전 세션부터 떠 있다. **Mock 백엔드 그대로**다(`gate-data/policy.yaml` 없음). 이번 세션은 건드리지 않았다.
  - laya-serve는 이번 세션의 백그라운드 작업으로 떠 있었다. 세션이 끝나면 꺼질 수 있으니 새 세션에서 health로 확인하고 필요하면 다시 띄운다.
  - Docker Desktop이 켜져 있다.

## 미해결 · 블로커
1. **이번 PR 머지:** 사용자가 리뷰하고 머지한다.
2. **팀원 재학습 체크포인트:** 팀원이 추가 학습 중이다. 재학습 요청 사항은 보고서와 계획 5절에 있다.
3. **팀원 학습 데이터의 출처·라이선스:** 상담 원문의 출처를 모른다. 가중치를 저장소에 넣지 않으므로 지금은 막히지 않는다.
4. **NER 단계의 설계 결정:** 실행 방식, 가림 형식, 정책 키, 결합 방식. 착수 전에 사용자에게 묻는다.
