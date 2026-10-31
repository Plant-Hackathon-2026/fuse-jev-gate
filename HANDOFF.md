# 핸드오프 — fuse-jev-gate (에이전트 파일 접근 게이트)
_업데이트: 2026-10-31 · 작성 세션: 3d 완료(PR #5 머지) + 점검 페이지 갱신 + 설정 화면 UI(Phase 8) 디자인 확정_

## TL;DR (30초 복원)
- **프로젝트:** 일반인이 로컬에서 쓰는 AI 에이전트가 원본 문서를 게이트를 거쳐서만 읽게 하는 도구다.
  - 게이트 순서: 경로 규칙 → 정규식 → NER(선택) → 판단 모델(Laya, 코드 이름은 Jev) → 정책 → 사람 승인 → 감사 로그
- **방금 끝낸 것**
  - 3d(정책 문장으로 NER 묶음 바꾸기)를 PR #5로 머지했다. PR #4도 머지했다.
  - 점검 페이지를 갱신했다.
  - **설정 화면 UI 디자인을 확정했다.** 계획은 `plans/ui-plan.md`에 있다.
- **사용자 결정:** 모델 관련 작업은 제외한다. "전화번호" 해석과 승인 창 줄이기도 모델 재학습으로 넘긴다(하지 않음).
- **이 문서는 브랜치 `docs/ui-plan`, PR #6**(https://github.com/Plant-Hackathon-2026/fuse-jev-gate/pull/6)에 있다. 다음 세션은 PR #6 머지 여부부터 확인하고, 머지됐으면 `git checkout main && git pull`로 시작한다.
- **다음 첫 행동:** 팀원용 평가 보고서(4WaSWgekRir4frkRCq1a2L)를 갱신한다(사용자 결정, 다음 세션 첫 작업). 그다음 Phase 8 UI 구현에 들어간다. 구현 전에 `plans/ui-plan.md` 7절의 미결 5개를 묻는다.

## 목표
일반인이 자기 컴퓨터에서 AI 에이전트를 쓸 때, 에이전트가 파일을 읽으면 반드시 게이트를 거치게 한다. 게이트는 허용·가림·사람 승인·차단 중 하나로 답한다. 처리 방식은 사용자가 정책으로 정하고, 정하지 않으면 기본값을 쓴다. 이제 일반인이 터미널 없이 정책을 보고 바꿀 수 있는 설정 화면을 만든다.

참고 문서:
- **현재 계획: `plans/implementation-plan.md`.** 명세와 다른 부분은 계획 문서를 따른다.
  - 2절 결정 표: "정책 문장 → NER 묶음 (3d)", "다음 작업 (3d 뒤)" 행
  - 5절 "3d"와 "Phase 8"
  - 6절 한계
- **UI 계획: `plans/ui-plan.md`.** 결정, 디자인 기준(색·글꼴·모양·부품), 화면 구성, 보안 조건, 구현 순서, 미결이 다 있다.
- **디자인 시안(캔버스):** https://claude.ai/artifact/USfowzn1bGWNa6v5nY4ZHc
  - 브랜드 시트, 설정 화면, 감사 기록 화면 세 장(버전 8)이다. "링크가 있는 누구나"로 공유되어 있다.
  - 목록에는 처음 만들 때 잘못 붙인 이름("가리개 게이트 설정 화면 시안")이 남아 있을 수 있다. 캔버스 안 제목은 "Plant 게이트 설정 화면 디자인"이다.
- `eval/README.md`: "정책 문장 해석 평가 (3d)", "게이트 흐름 평가 (3c)", "Laya v3 평가 결과"
- **점검 페이지:** https://claude.ai/artifact/AbemNcUnx4Uhj9d2B5jBhW — **버전 11**. 3d와 UI 결정(Phase 8 행, 결정 기록 147개, 남은 일 = 팀원용 보고서 → Phase 8)까지 반영했다. 공유 중이라 고치면 바로 보인다.
- 3b 평가 보고서(팀원 공유용): https://claude.ai/artifact/4WaSWgekRir4frkRCq1a2L — 3b 체크포인트 기준이라 갱신해야 한다.
- 원래 명세: `fuse-jev-gate-spec.md`. 저장소: https://github.com/Plant-Hackathon-2026/fuse-jev-gate

## 현재 위치
- 방금 끝낸 것: Phase 0~7, 3a~3d 구현 완료. Phase 8(설정 화면 UI)은 디자인과 계획까지 끝났다.
- 다음: ① 팀원용 평가 보고서 갱신 → ② Phase 8 UI 구현

## 완료한 것 (이번 세션)
- **PR #4 머지**(3c), **3d 구현 + PR #5 머지**(머지 커밋 `55e0227`).
  - 3d 요약: 해석기에 entity → entity_action 단계를 더했다.
  - 평가 세트 2개(블라인드 120 + 검증 50)를 만들었다.
  - 결과: Mock은 검증 세트 64%, Laya v3 zero-shot은 실패(모두 `unknown`). 그래서 기본 해석기는 Mock 그대로다.
  - 테스트: 맥 1266 통과·1 skip, Linux 1267 통과.
  - 자세한 것은 계획 5절 3d와 `eval/README.md`.
- **점검 페이지 갱신**(서브에이전트): v10에 3d를, v11에 UI 결정을 반영했다. 결정 기록은 147개(사용자 52)이고, 남은 일은 팀원용 보고서와 Phase 8이다. "전화번호"와 승인 창은 "하지 않기로 한 것"으로 옮겼다. 흐름도(SVG)는 고치지 않았다.
- **사용자 결정(2026-10-31)**
  - 모델 관련 작업 제외: NER 미탐 보완, 해석기를 실제 모델로, 재학습·내려받기
  - "전화번호" 해석과 승인 창 줄이기는 하지 않음(모델 재학습으로 풀림)
  - 다음은 UI
- **UI 결정(사용자, `plans/ui-plan.md` 2절)**
  - 로컬 웹 페이지 `gate ui`
  - 문장 입력이 중심이고 직접 고르기도 둔다
  - 화면은 설정·감사 기록
  - 팀 Plant 로고 기준 디자인
  - 연결 상태(게이트·Laya·NER) 표시
  - 번호 줄에 "찾는 규칙"(정규식) 펼치기
  - 고정 공개 범위는 "안전 규칙으로 고정" 자물쇠
- **디자인 시안 캔버스:** 브랜드 시트, 설정 화면, 감사 기록 화면. 사용자 댓글로 브랜드 시트의 상태·고정값 절은 뺐다.
- **로고 파일:** 저장소 밖 `hackathon/design-assets/`에 원본 `Plant.png`와 마크만 자른 `plant-mark.png`를 두었다.
- 문서: `plans/ui-plan.md`(새 파일), `plans/implementation-plan.md`(결정 표 한 줄, Phase 8 항목).

## 다음 할 일
1. **팀원용 평가 보고서 갱신** (사용자 결정, 다음 세션 첫 작업)
   - 대상: https://claude.ai/artifact/4WaSWgekRir4frkRCq1a2L. 비공개로 만들었다는 기록이 있다. 공유 상태는 읽을 때 확인한다.
   - 먼저 Artifact `read`로 읽고 그 파일을 기준으로 고친다. 새 아티팩트를 만들지 않는다.
   - 더할 것(출처는 저장소 문서의 사실만):
     - Laya v3 결과: `eval/README.md` "Laya v3 평가 결과" — 오탐 6/12 → 2/12, 미탐 0/24, has_pii를 고른 이유
     - 게이트 흐름(NER 포함): `eval/README.md` "게이트 흐름 평가 (3c)" — ①/②/NER 끔 표, NER이 놓친 것
     - **팀원 재학습에 참고할 점:** v3가 나이·연도("30대", "2016년")도 개인정보로 봐서 ②에서 승인이 많다(민감 24개 중 19개 승인). "전화번호"와 승인 창 문제를 모델 재학습으로 넘겼으니 이 부분을 분명히 쓴다.
     - (선택) 3d에서 v3에 정책 문장 해석을 zero-shot으로 물었더니 모두 `unknown`이었다는 점
   - 팀원 공유용이므로 개인정보(이메일, 사용자 이름이 든 경로)는 넣지 않는다.
2. **Phase 8 설정 화면 UI 구현** (`plans/ui-plan.md` 6절)
   - 착수 전에 7절 미결 5개를 사용자에게 묻는다: 제품 이름, 로고 파일을 공개 저장소에 넣을지, 글꼴(번들·시스템), NER [켜기] 버튼이 할 일, 기관 정책 기능(범위 밖 확인).
   - `main`에서 새 브랜치(예: `feat/ui-settings`)를 만든다.
   - 순서: 8-1 서버(토큰·Host·Origin) → 8-2 API → 8-3 설정 화면 → 8-4 감사 기록 화면 → 테스트·하네스.
   - 디자인 소스는 캔버스의 `project/*.dc.html`을 Artifact `read`로 읽어 참고한다. 캔버스 전용 문법이라 그대로 옮기지 않는다.
3. Phase를 끝내면 보고하고 멈춘다. 브랜치 + PR로 올린다. 커밋과 PR 본문에 AI 흔적을 남기지 않는다.

## 핵심 결정 · 함정
- **진행 규칙**
  - Phase마다 멈추고 보고한다.
  - 서브에이전트는 Opus로 띄운다. 파일을 나눠 맡기는 지시서 방식이 잘 맞는다. 점검 페이지 갱신은 서브에이전트에 맡겨 왔다.
  - 평가 세트는 블라인드로 만들고, 고친 뒤에는 처음 보는 검증 세트로 다시 잰다.
  - 응답·문서는 한국어로 쓰고, 계획 문서는 `plans/`에 둔다.
  - 사용자는 설명이 어려우면 "풀어서 말해 달라"고 한다. 실제 예로 단계별로 설명하면 통한다.
  - 비용·용량이 드는 실행(모델 내려받기, 설치)은 실행 전에 확인받는다.
  - **모델 관련 작업은 후보로 내지 않는다**(사용자 결정).
- **UI 보안 조건** (`plans/ui-plan.md` 5절)
  - 정책은 사람만 바꾼다.
  - `gate ui`는 127.0.0.1에만 바인드하고, 실행마다 새 토큰을 만들고(파일로 남기지 않음), Host와 Origin을 검사한다.
  - 게이트 소켓에는 정책 변경 요청을 두지 않는다.
  - 에이전트 샌드박스에서 UI 포트에 닿는지 하네스로 확인한다.
- **UI 구현 함정**
  - `gate/policy_cli.py`의 `_commit`·`_undo`·`_remove`·`_set`은 터미널 질문(`_Prompt`)에 묶여 있다. 화면에서 쓰려면 "바꿈 계산·검사"와 "묻기"를 나눈다. 기록 쌓기, 완화 확인, 0600 쓰기는 같은 코드로 한다.
  - 화면의 정규식은 `gate.detectors`의 패턴에서 읽는다. 문자열을 따로 적으면 코드와 어긋난다.
  - 시안은 Google Fonts를 불러 쓴다. 로컬 화면에서는 외부 요청이 생기므로 번들 또는 시스템 글꼴로 정한다(미결).
  - 고정된 공개 범위(계좌번호·비밀값)는 제품 규칙이다. "기관에서 정함"으로 표시하면 사실과 다르다.
- **3d 해석기 결정**
  - 문장 하나는 바꿈 하나다. 묶음이 둘이거나, 묶음 일부만 바꾸거나, 묶음 부분 공개거나, 넓은 말이면 다시 묻는다.
  - "보여주지 마"는 정보가 주어면 mask, 파일이 주어면 deny로 읽는다.
  - 주제 낱말("병원", "회사")만으로는 묶음으로 보지 않는다.
  - Laya v3 zero-shot은 쓸 수 없다. 첫 질문이 언제나 `unknown`이라 잘못 저장이 0인 것은 우연이다.
- **② 흐름**
  - NER이 켜져 있고 가린 것이 있으면, Laya가 가린 결과를 본다. safe면 가림, 아니면 승인이다.
  - Laya 오류는 언제나 차단이다.
  - NER을 끈 채 ②를 쓰면 안 된다.
- **서버 함정**
  - laya-serve 기본 주소는 0.0.0.0이다. `LAYA_HOST=127.0.0.1`을 꼭 준다.
  - `LAYA_MODELS`를 빼면 공식 체크포인트를 내려받는다. 꼭 준다.
  - nerserve는 127.0.0.1(IP 리터럴)로 띄운다.
  - NER 서버가 꺼진 채 `ner_backend: server`면 모든 읽기가 차단된다(그래서 기본 none).
  - laya-serve는 `pkill -f`로 안 잡힐 수 있다. `lsof -nP -iTCP:8000 -sTCP:LISTEN`으로 PID를 보고 끈다.
- **캔버스(Design 아티팩트) 함정**
  - 파일은 `project/canvas.json`(배치)와 `project/*.dc.html`(화면)이다. 고치기 전에 `read`로 지금 버전을 읽는다. 사용자가 캔버스에서 직접 고칠 수 있다.
  - 사용자가 캔버스에 댓글을 달면 `ArtifactComments`로 읽고 답한 뒤 해결 처리한다.
- **거짓 안전**
  - NER·Laya 테스트는 가짜 서버다. 실제 동작은 `eval.run_eval --ner server`, `eval.run_policy_text --interpreter laya`로만 확인했다.
  - 첫 평가 세트의 Mock 98.3%는 그 세트를 보고 고친 값이다. 실력은 검증 세트 64%다.
- **환경 함정**
  - 긴 한글 파이썬 스크립트를 heredoc으로 넘기면 바이트가 깨질 수 있다. 파일로 써서 실행한다.
  - zsh에서 `echo =====`는 오류가 난다. 따옴표로 감싼다.

## 관련 파일
- `plans/ui-plan.md`: UI 계획(이번 세션 새 파일)
- `plans/implementation-plan.md`: 2절 결정 표, 5절 3d·Phase 8, 6절 한계
- `gate/policy_cli.py`: 정책 쓰기·기록·되돌리기·완화 확인. UI가 재사용하도록 나눌 대상
- `gate/policy_text.py`: 문장 해석기(`interpret`, `make_interpreter`). UI의 [해석하기]가 부른다
- `gate/policy.py`: 설정 키·선택지(`setting_choices`), `describe_setting`, `loosened`, 기본값
- `gate/detectors.py`: 번호·비밀값 정규식과 `VISIBLE_CHOICES`. UI의 "찾는 규칙"이 읽는다
- `gate/audit.py` `read_records`, `gate/audit_cli.py`: 감사 기록 화면의 자료
- `gate/jev_client.py`, `gate/ner_client.py`: 서버 주소·health. 연결 상태 카드가 쓴다
- `gate/cli.py`: `gate ui` 하위 명령을 더할 곳
- 저장소 밖
  - `hackathon/design-assets/`: `Plant.png`(팀 로고 원본), `plant-mark.png`(마크)
  - `hackathon/models/`: `laya-v3`(지금 판단 모델), `ner`, `laya`(3b), `app`(팀원 코드, 저장소에 넣지 않음)
  - `hackathon/laya-serve/`: `.venv`(laya 0.4.18, NER 서버도 이것으로 돌림), `logs/`

## 실행 · 검증
- **laya-serve (v3)** — `hackathon/laya-serve`에서 백그라운드로 띄운다.
  ```
  LAYA_HOST=127.0.0.1 LAYA_PORT=8000 LAYA_MODELS=gate-laya-v3 LAYA_DEFAULT_MODEL=gate-laya-v3 \
  LAYA_EXTRA_MODELS='{"gate-laya-v3": "/Users/sangwoo/work/personal/hackathon/models/laya-v3"}' \
  HF_HUB_OFFLINE=1 HF_HOME=$PWD/hf-cache USE_TF=0 .venv/bin/laya-serve > logs/serve-v3.log 2>&1
  ```
  - 준비 확인: `curl -s http://127.0.0.1:8000/health`
- **NER 서버** — `fuse-jev-gate`에서 띄운다.
  ```
  HF_HUB_OFFLINE=1 ../laya-serve/.venv/bin/python -m nerserve --model-dir ../models/ner --host 127.0.0.1 --port 8001 --device auto > ../laya-serve/logs/nerserve.log 2>&1
  ```
- **평가**
  - 정책 문장: `uv run python -m eval.run_policy_text --interpreter mock [--sentences eval/policy_sentences_holdout.csv] --details`
  - 게이트: `NER_BASE_URL=… JEV_BASE_URL=… JEV_MODEL=gate-laya-v3 JEV_MODEL_DIR=… uv run python -m eval.run_eval --client laya --ner server --details`
- **테스트(맥):** `uv run pytest` — **1266 통과, 1 skip**, 약 30초(이 세션 main 기준)
- **테스트(Linux):** `docker build -f sandbox/gate.Dockerfile -t fuse-jev-gate:phase3d . && docker run --rm --device /dev/fuse --cap-add SYS_ADMIN fuse-jev-gate:phase3d pytest -q` — **1267 통과**
- **인프라·런타임 상태**
  - laya-serve, NER 서버, 게이트 데몬은 모두 꺼져 있다.
  - `gate-data/policy.yaml`은 없다(기본 정책).
  - Docker Desktop은 켜져 있다.

## 미해결 · 블로커
1. **점검 페이지:** v11까지 반영했다. 렌더링 화면은 사람이 보지 않았다. 새 표가 폰 폭에서 가로로 스크롤되는지 한 번 보면 좋다.
2. **UI 미결 5개:** `plans/ui-plan.md` 7절. Phase 8 착수 전에 묻는다.
3. **팀원 학습 데이터의 출처·라이선스:** 확인하지 못했다. 가중치는 저장소에 넣지 않으므로 지금은 막히지 않는다.
