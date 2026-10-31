# 핸드오프 — fuse-jev-gate (에이전트 파일 접근 게이트)
_업데이트: 2026-11-01 · 작성 세션: 팀원용 평가 보고서 갱신 + Phase 8 설정 화면(`gate ui`) 구현·검증, PR #7_

## TL;DR (30초 복원)
- **프로젝트:** 일반인이 로컬에서 쓰는 AI 에이전트가 원본 문서를 게이트를 거쳐서만 읽게 하는 도구다.
  - 게이트 순서: 경로 규칙 → 정규식 → NER(선택) → 판단 모델(Laya, 코드 이름은 Jev) → 정책 → 사람 승인 → 감사 로그
- **방금 끝낸 것:** 계획의 마지막 Phase인 **Phase 8 설정 화면 `gate ui`**를 구현했다. **PR #7**(https://github.com/Plant-Hackathon-2026/fuse-jev-gate/pull/7, 브랜치 `feat/ui-settings`)에 올렸고, 머지는 사용자가 한다.
- **다음 첫 행동:** PR #7이 머지됐는지 확인한다. 머지됐으면 `git checkout main && git pull`로 시작한다. 계획의 Phase는 모두 끝났으므로, 다음 작업은 사용자에게 묻는다(먼저 하나를 밀지 않는다).

## 목표
일반인이 자기 컴퓨터에서 AI 에이전트를 쓸 때, 에이전트가 파일을 읽으면 반드시 게이트를 거치게 한다. 게이트는 허용·가림·사람 승인·차단 중 하나로 답한다. 처리 방식은 사용자가 정책(문장, 설정 화면, `gate policy`)으로 정하고, 정하지 않으면 기본값을 쓴다.

참고 문서:
- **현재 계획: `plans/implementation-plan.md`.** 명세(`fuse-jev-gate-spec.md`)와 다른 부분은 계획 문서를 따른다. 2절 결정 표, 5절 Phase 8, 6절 한계.
- **UI 계획·결과: `plans/ui-plan.md`.** 2절 결정(새로 고침 행 포함), 7절 확정한 결정, 8절 구현 결과(보안·테스트·하네스·시안과 달라진 점·남은 문제).
- `README.md` "설정 화면 (gate ui)", "우회 시험 하네스"
- 디자인 캔버스: https://claude.ai/artifact/USfowzn1bGWNa6v5nY4ZHc
- 점검 페이지: https://claude.ai/artifact/AbemNcUnx4Uhj9d2B5jBhW — **버전 13**. Phase 8 결과(하네스, 새로 고침 유지, 테스트 1375/1376)까지 반영했다. 공유 중이라 고치면 바로 보인다.
- 팀원용 평가 보고서: https://claude.ai/artifact/4WaSWgekRir4frkRCq1a2L — **버전 3**(v3·NER 게이트 흐름·재학습 참고 절). "링크가 있는 누구나"로 공유 중이다(이전 핸드오프의 "비공개"는 틀린 기록이었다).

## 현재 위치
- 방금 끝낸 것: Phase 0~8, 3a~3d 모두 구현 완료. Phase 8은 PR #7 머지만 남았다.
- 다음: 사용자가 정한다. 계획 문서에 남은 Phase는 없다.

## 완료한 것 (이번 세션)
- **PR #6 머지**(UI 계획, 사용자 요청, 머지 커밋 `1246824`).
- **팀원용 평가 보고서 갱신**(버전 3): v3 결과(오탐 6/12 → 2/12, 미탐 0/24), NER을 붙인 게이트 흐름(①/②/NER 끔 비교, NER이 놓친 것), **재학습 때 참고할 점**(나이·연도와 가림 표시를 has_pii가 어떻게 볼지, 성 없는 이름, 안전 문서 확신도, 정책 문장 해석 질문 단계와 "전화번호"는 다시 묻기, 평가 데이터 분리·학습 데이터 출처 요청). 3b 내용은 "처음 판" 아래에 그대로 두었다.
- **UI 미결 5개 사용자 결정(2026-10-31):** 이름 "게이트 · PLANT", 로고는 마크만 저장소에, 글꼴은 Outfit·JetBrains Mono latin만 번들(한글은 시스템 글꼴, 외부 요청 0), NER [켜기]는 설정만 바꾸고 NER 서버 `/health`가 응답할 때만, 기관 정책 기능은 범위 밖.
- **Phase 8 구현**(사용자 요청으로 별도 에이전트가 worktree에서 구현 → 메인이 검토·테스트·수정):
  - `gate/ui.py`(서버·보안), `gate/ui_api.py`(자료·바꾸기), `gate/ui_static/`(화면), `gate/policy_change.py`(CLI와 화면이 같이 쓰는 정책 쓰기), `gate/detectors.py` `PATTERNS`, 하네스 W16·W17.
  - 메인이 고친 것: 결정 날짜 바로잡기, 바꾼 뒤·탭으로 돌아올 때 연결 상태 다시 불러오기, **새로 고쳐도 유지**(아래 결정).
- **하네스 W16·W17 실행**(사용자 요청, 2026-11-01): 작업 폴더 설정·전역 설정 모두 **OS 샌드박스가 127.0.0.1 연결 자체를 막음**(화면 서버에 요청이 닿지 않음). 대조군 통과, 약 $0.070.
- **실제 Chrome 확인:** 데스크톱 폭과 폰 폭(458px), 해석→저장, 완화 확인 창, 찾는 규칙, 감사 기록, 새로 고침 두 번·새 탭으로 다시 열기. 콘솔 오류 없음. 캡처는 이 세션 scratchpad에만 있다(저장소에 넣지 않음).
- 저장 확인 때 실제 `gate-data/policy.yaml`에 들어간 시험 항목은 사용자 확인 뒤 휴지통(`~/.Trash/gate-data-policy-test-20261101-0051/`)으로 옮겼다. 지금 `gate-data`에는 정책 파일이 없다(전부 기본값).

## 다음 할 일
1. **PR #7 머지 여부를 확인한다.** 머지됐으면 `git checkout main && git pull`. 아직이면 사용자에게 머지를 부탁하거나, 요청이 있으면 머지 커밋 방식으로 머지한다(원격 브랜치는 지우지 않아 왔다).
2. **다음 작업을 사용자에게 묻는다.** 계획의 Phase는 모두 끝났다. 하나를 먼저 밀지 않는다. **모델 관련 작업(NER 보완, 해석기 실제 모델, 재학습·내려받기)은 후보로 내지 않는다**(사용자 결정). 팀원이 재학습 체크포인트를 주면 그때 교체·재평가한다(`eval/README.md` "체크포인트를 바꿀 때").
3. Phase나 작업을 끝내면 보고하고 멈춘다. 새 작업은 브랜치 + PR, 커밋과 PR 본문에 AI 흔적을 남기지 않는다.

## 핵심 결정 · 함정
- **진행 규칙**
  - Phase마다 멈추고 보고한다. 서브에이전트는 Opus로 띄운다. 큰 구현은 worktree 에이전트에 맡기고 메인이 검토·전체 테스트·실제 실행 확인을 한 뒤 PR로 올리는 방식이 잘 맞았다.
  - 비용이 드는 실행(하네스, 모델 내려받기)은 실행 전에 확인받는다.
  - 사용자는 설명이 어려우면 "풀어서 말해 달라"고 한다. 실제 예로 단계별로 설명하면 통한다.
- **설정 화면 보안** (`plans/ui-plan.md` 5·8절)
  - 127.0.0.1에만 바인드, 실행마다 한 번 쓰는 열쇠, Host 정확히 일치, 바꾸는 요청은 POST + Origin, CSP `default-src 'none'`.
  - **새로 고침(사용자 결정, 2026-11-01): 유지한다.** 열쇠로 처음 연 응답에서 세션 토큰을 쿠키 `gate_ui_<포트>`(HttpOnly, SameSite=Strict, Path=/)로도 준다. 쿠키는 페이지를 다시 그리는 데만 쓰고 API는 `X-Gate-Token` 헤더가 있어야 한다. 처음 구현(토큰을 메모리에만)은 새로 고치면 다시 열어야 해서 사용자가 "실제 유저는 새로 고침한다"고 지적했다.
  - 정책은 사람만 바꾼다. 게이트 소켓(에이전트용)에는 정책 변경 요청이 없다. `gate policy` 바꾸기 명령은 입력이 터미널이 아니면 거부한다(그래서 자동화로 정책을 넣으려면 화면을 쓴다).
- **설정 화면 함정**
  - 게이트 카드가 "확인 못 함"이면 `gate --socket <소켓> ui`로 띄우지 않은 것이다. 감사 로그(`--audit`)를 안 주면 "마지막 요청"을 못 구한다.
  - 상태 카드의 Laya·NER 주소는 화면 프로세스의 `JEV_BASE_URL`·`NER_BASE_URL`(없으면 127.0.0.1:8000·8001)이다. 데몬과 다르면 실제와 다를 수 있다.
  - 백그라운드로 띄운 `gate ui`는 입력이 터미널이 아니라 Enter로 새 주소를 만들 수 없다. 다른 브라우저에서 열어야 하면 다시 띄운다.
  - 페이지는 시작할 때 `<meta name="gate-session">`을 지운다(DOM에 토큰을 남기지 않음). 자동화 확인은 화면이 자료를 불러왔는지로 본다.
- **하네스 함정**
  - worktree에서 돌릴 때는 `--data-root <hackathon>/gate-data`를 꼭 준다. 기본값이 저장소 위치 기준이라 worktree 안을 가리킨다.
  - W17은 headless default 모드에서 "승인 필요"로 판정 불가가 나올 수 있다. `--mode auto`로 돌린다.
  - 데몬이 떠 있어야 한다(대조군 C1·C2). 전역 설정 시험 파일은 `gate setup --settings-file <scratchpad 파일> --yes`로 만들고, 실제 `~/.claude/settings.json`은 건드리지 않는다.
- **이전부터의 결정·함정** (자세한 것은 계획 2절·6절)
  - ② 흐름: NER이 켜져 있고 가린 것이 있으면 Laya가 가린 결과를 본다. safe면 가림, 아니면 승인. Laya·NER 오류는 언제나 차단.
  - laya-serve는 `LAYA_HOST=127.0.0.1`과 `LAYA_MODELS`를 꼭 준다. NER 서버가 꺼진 채 `ner_backend: server`면 모든 읽기가 차단된다(기본 none).
  - 긴 한글 파이썬 스크립트는 heredoc 대신 파일로 써서 `python3 -I`로 돌린다. zsh에서 `echo =====`는 따옴표로 감싼다.
- **거짓 안전**
  - NER·Laya 테스트는 가짜 서버다. 실제 동작은 `eval.run_eval --ner server`로만 확인했다.
  - 화면 테스트(`tests/test_ui.py`)는 서버를 직접 부른다. 화면 스크립트(`app.js`)의 흐름은 저장소 테스트가 없고 Chrome에서 손으로 확인했다.

## 관련 파일
- `plans/ui-plan.md`, `plans/implementation-plan.md`: 결정·결과·한계
- `gate/ui.py`: `gate ui` 명령, HTTP 서버, 보안 검사(맨 위 docstring에 정리), 새로 고침 쿠키
- `gate/ui_api.py`: 화면 API(state·status·audit·interpret·save·set·remove·undo·ner), 상태 확인
- `gate/ui_static/`: `index.html`, `app.css`, `app.js`, `plant-mark.png`, `fonts/`(OFL 전문 포함)
- `gate/policy_change.py`: 정책 쓰기(계산·완화 확인·기록·0600 쓰기·undo). `gate/policy_cli.py`는 묻기와 출력만
- `sandbox/bypass.py`: 하네스(W16·W17 포함)
- `tests/test_ui.py`(91), `tests/test_policy_change.py`(8), `tests/test_bypass.py`
- 저장소 밖
  - `hackathon/gate-data/`: 원본(`source`), 작업 폴더(`agent-workspace`), 전역 설정 시험 폴더(`plain-project`), 감사 로그(`audit/`), 소켓(`run/`)
  - `hackathon/design-assets/`: `Plant.png`, `plant-mark.png`(원본 560px)
  - `hackathon/models/`, `hackathon/laya-serve/`: 판단 모델·NER 가중치와 실행 환경(저장소에 넣지 않음)

## 실행 · 검증
- **데몬:** `D=<hackathon>/gate-data; .venv/bin/gate --socket $D/run/gate.sock serve --source $D/source --policy $D/policy.yaml --audit $D/audit/audit.jsonl`(맥 대화상자 없이 하려면 `--no-dialog`)
- **설정 화면:** `.venv/bin/gate --socket $D/run/gate.sock ui --policy $D/policy.yaml --audit $D/audit/audit.jsonl` (주소만 보려면 `--no-browser`. 시험할 때는 `--policy`를 scratchpad 파일로 준다)
- **하네스:** `uv run python -m sandbox.bypass --data-root $D --only W16,W17 --out 보고서.md`, 전역 설정은 `--settings <시험 파일> --workspace $D/plain-project --mode auto`
- **테스트(맥):** `uv run pytest` — **1375 통과, 1 skip**(`feat/ui-settings` 기준)
- **테스트(Linux):** `docker build -f sandbox/gate.Dockerfile -t fuse-jev-gate:phase8 . && docker run --rm --device /dev/fuse --cap-add SYS_ADMIN fuse-jev-gate:phase8 pytest -q` — **1376 통과**
- **인프라·런타임 상태:** 게이트 데몬, 설정 화면, laya-serve, NER 서버 모두 꺼져 있다. `gate-data/policy.yaml`은 없다(기본 정책). Docker Desktop은 켜져 있다.

## 미해결 · 블로커
1. **PR #7 머지**(사용자).
2. **점검 페이지:** 버전 13까지 반영했다(서브에이전트가 v12, 메인이 테스트 수·새로 고침 완료를 고쳐 v13). 렌더링은 사람이 보지 않았다.
3. **팀원 학습 데이터의 출처·라이선스:** 보고서에서 요청했고 아직 답이 없다. 가중치는 저장소에 넣지 않아 지금은 막히지 않는다.
