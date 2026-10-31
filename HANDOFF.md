# 핸드오프 — fuse-jev-gate (에이전트 파일 접근 게이트)
_업데이트: 2026-11-01 · 작성 세션: 3e 마무리(실제 모델 재측정, 설정 화면 확인, 안내 문구·조사 조각·승인 문구 수정, 계획·평가 문서, 아티팩트, PR)_

## TL;DR (30초 복원)
- **프로젝트:** 일반인이 로컬에서 쓰는 AI 에이전트가 원본 문서를 게이트를 거쳐서만 읽게 하는 도구다.
  - 게이트 순서: 경로 규칙 → 정규식 → IP·내부 주소 규칙 → NER → 판단 모델(Laya, 코드 이름은 Jev) → 정책 → 사람 승인 → 감사 로그
- **방금 끝낸 것:** 3e 마무리.
  - 3e PR(`feat/ner-v2` → `main`)을 열었다. 머지는 사용자가 한다.
  - 실제 모델 재측정 결과가 교체 전 조합 비교와 같다.
  - 맥 테스트 1555 통과·1 skip.
- **다음 첫 행동:** 3e PR 머지 여부를 사용자에게 확인한다. 계획 Phase와 3e가 모두 끝났으니 다음 작업은 사용자에게 묻는다. 모델 관련 작업은 후보로 내지 않는다.

## 목표
일반인이 자기 컴퓨터에서 AI 에이전트를 쓸 때, 에이전트가 파일을 읽으면 반드시 게이트를 거치게 한다. 게이트는 허용·가림·사람 승인·차단 중 하나로 답한다. 처리 방식은 사용자가 정책(문장, 설정 화면, `gate policy`)으로 정하고, 정하지 않으면 기본값을 쓴다.

참고 문서:
- 전체 계획: `plans/implementation-plan.md`. 3e까지 반영했다(2절 결정 표, 5절 "3e", 6절 한계, 7절 테스트 기준). 명세 `fuse-jev-gate-spec.md`와 다르면 계획을 따른다.
- 3e 계획: `plans/ner-v2-plan.md`
- 평가: `eval/README.md` "NER 재학습판 조합 비교"와 그 아래 "교체 뒤 다시 잰 결과 (3e 코드, 2026-11-01)"
- 팀원용 평가 보고서(링크가 있는 누구나 볼 수 있음): https://claude.ai/artifact/4WaSWgekRir4frkRCq1a2L
- 점검 페이지: https://claude.ai/artifact/AbemNcUnx4Uhj9d2B5jBhW

## 현재 위치
- 현재 상태: 3e(NER 교체 + IP·내부 주소 규칙 + 날짜·사건 묶음 빼기 + NER 기본 켬)와 마무리 수정까지 `feat/ner-v2`에 있다. PR이 열려 있다. PR #8은 이번 세션에 머지했다(`34dce69`).
- 다음: PR 머지(사용자) → 다음 작업을 사용자와 정한다.

## 완료한 것 (이번 세션)
- **PR #8 머지**(사용자 요청, 머지 커밋, 원격 브랜치 남김).
- **병렬 에이전트 세 개**(같은 작업 트리, 파일을 나눠 맡김, 커밋은 메인이 검토 뒤에 함)
  - 재측정: 실제 v3 + 새 NER로 36개를 쟀다. 합계는 기대값과 같다(민감 가림 14·승인 10, 안전 허용 7·승인 5, 승인 창 15, 승인 없이 남은 식별자 1 `월매`, 승인하면 0).
    - 상담 quasi_1은 가림 → 승인이 됐다. 가림 표시가 `[소속]`으로 바뀌어 p 0.4996 → 0.62.
    - 이력서 quasi_2는 승인 → 가림이 됐다. `출생 연도: 1987년`이 이제 생년월일로 가려져 p 0.97 → 0.41.
    - 원인은 `eval/README.md`에 적었다.
  - 계획 문서: `plans/implementation-plan.md`에 3e를 반영했다.
  - 안내 문구: `gate cat` 안내와 작업 폴더 안내를 실제 가림 방식대로 고쳤다. cat 응답에 `labeled` 필드를 더했다(아래 결정 참고).
- **메인**
  - 설정 화면 Chrome 확인(데스크톱 폭): NER 9줄, host 안내, 응답 없음 배너, 예전 항목 태그, "저장할 수 없어요" 상자, NER 꺼짐 태그를 봤다. 콘솔 오류는 없다.
  - 사용자 요청으로 고친 것(4건)
    - 태그 크기 통일(22px).
    - "저장할 수 없어요"·뺀 키 안내에서 내부 날짜와 "NER 교체" 표현을 뺐다.
    - 조사가 이어 붙은 조각(`으로도`)을 버린다. 고친 뒤 다시 재도 합계·누출은 같다.
    - 승인 창·`gate policy show`·README·판단 프롬프트에 NER 가림을 반영했다.
  - 아티팩트 둘(점검 페이지, 팀원용 보고서)을 에이전트로 갱신했다.
- 커밋: `94dfe48`·`d5daa3e`·`52574b2`·`d8446cd`·`713ba45`·`4ba937f`와 이 HANDOFF. AI 흔적은 없다.

## 다음 할 일
1. **3e PR 머지 여부를 사용자에게 확인한다.** 사용자가 머지를 요청하면 지금까지처럼 머지 커밋으로 하고 원격 브랜치는 남긴다. 그다음 로컬 `main`을 최신화한다.
2. **다음 작업을 사용자에게 묻는다.** 모델 관련(NER 미탐, 해석기 실제 모델, 재학습)은 후보로 내지 않는다. 남은 작은 후보는 아래 "미해결"에 있다. 하나를 먼저 밀지 않는다.
3. 다음 실제 데모 전: `hackathon/gate-data/agent-workspace/`의 안내문이 옛 문구다. `gate workspace init <폴더> --force`로 다시 만든다. 이번에는 사용자 결정으로 그대로 두었다. 덮어쓰기 전에 사용자에게 확인한다.

## 핵심 결정 · 함정
- **진행 규칙**
  - 작업마다 멈추고 보고한다. 서브에이전트는 Opus를 쓴다.
  - 병렬 에이전트는 맡는 파일을 겹치지 않게 나누고, 커밋은 메인이 검토 뒤에 한다. 이번에 잘 맞았다.
  - 비용·시간이 드는 실행(하네스, 모델 내려받기, Linux 컨테이너)은 먼저 묻는다.
  - 사용자는 직접 보는 것을 좋아하고, 어려우면 "풀어서 말해 달라"고 한다.
- **cat 응답 `labeled`(이번 세션)**
  - NER도 정규식이 놓친 전화·이메일을 같은 칸(`mobile`·`phone`·`email`)으로 센다. 그래서 종류 이름만으로는 `*`로 가렸는지 `[전화번호]`로 가렸는지 알 수 없다. 그래서 `Judgment.labeled`(규칙·NER이 종류 이름으로 가린 건수)를 두고 cat 응답에 실었다.
  - 예전 데몬 응답(labeled 없음)은 번호 종류는 `*`, 그 밖은 종류 이름으로 본다.
  - 사람 승인 뒤 `replace`로도 유지된다. 판단 캐시는 Jev 결과만 저장하므로 영향이 없다.
- **조사 조각**: `outside`는 잘린 조각이 JOSA 하나나 둘로만 이뤄졌으면 버린다(`_josa_only`). 두 글자 조각이 우연히 조사 둘(예: `이도`)이면 이름이어도 버린다. 정규식·규칙 범위와 겹쳐 잘린 개체에만 적용되므로 드물다.
- **판단 프롬프트**: `build_question`은 Mock이 기록용으로만 쓴다. Laya 백엔드는 `questions.json`(has_pii)을 쓰므로 프롬프트를 바꿔도 측정이 달라지지 않는다.
- **NER 기본 켬의 함정**
  - NER 서버(127.0.0.1:8001)가 없으면 모든 내용 읽기가 차단된다. 끄는 법: `gate policy set ner_backend none`.
  - 하네스 대조군 C1·C2를 통과시키려면 nerserve를 띄우거나 NER을 끈 정책을 쓴다.
  - 처음 32종 모델(`models/ner`)은 받지 않는다.
- **테스트 포트**: 저장소 테스트는 닫힌 포트·가짜 서버를 쓴다. 실제 laya-serve·nerserve를 8000·8001에 띄워 둔 채 `uv run pytest`를 돌려도 된다.
- **설정 화면 자동화**: `gate policy` 바꾸기 명령은 tty가 아니면 거부한다. 시험 정책은 `gate.policy.write_entries`로 scratchpad에 쓴다. `NER_BASE_URL`을 닫힌 포트로 주면 "응답 없음" 상태를 볼 수 있다.
- **이전부터**
  - ② 흐름: NER 켜짐 + 가린 것 있음 → Laya가 가린 결과를 보고 safe면 가림, 아니면 승인.
  - Laya·NER 오류는 차단한다.
  - laya-serve에는 `LAYA_HOST=127.0.0.1`과 `LAYA_MODELS`를 꼭 준다.
  - 긴 한글 파이썬은 파일로 써서 `python3 -I`로 돌린다.
- **거짓 안전**
  - 저장소 NER·Laya 테스트는 가짜 서버로 돈다. 실제 동작은 36개 재측정과 지난 세션 데모 3개로만 봤다.
  - 설정 화면은 데스크톱 폭만 봤다(폰 폭은 못 봄).
  - Linux 컨테이너 테스트는 3e 이후 돌리지 않았다. README의 Linux 수는 "맥 + 1"로 적혀 있다.

## 관련 파일
- `plans/implementation-plan.md`(5절 "3e"), `plans/ner-v2-plan.md`, `eval/README.md`
- `gate/ner_client.py`(`outside`, `_josa_only`, `split_kind`), `gate/host_rules.py`, `gate/pipeline.py`(`Judgment.labeled`)
- `gate/protocol.py`·`gate/daemon.py`·`gate/client.py`·`gate/cli.py`(`mask_notice`): cat 안내
- `gate/approval.py`(승인 창 문구), `gate/workspace.py`(작업 폴더 안내문), `gate/policy_text.py`·`gate/policy_change.py`(저장 불가·뺀 키 안내), `gate/ui_static/app.css`
- 저장소 밖
  - `hackathon/models/ner-xlmr-pii/`(새 NER), `models/laya-v3/`(판단 모델)
  - `hackathon/gate-data/`(실제 데모 폴더, 이번에 바꾸지 않음. `policy.yaml` 없음)
  - `hackathon/laya-serve/`

## 실행 · 검증
- **서버 둘** (`H=<hackathon>`, 저장소 루트에서)
  - laya-serve: `cd $H/laya-serve && LAYA_HOST=127.0.0.1 LAYA_PORT=8000 LAYA_MODELS=gate-laya-v3 LAYA_DEFAULT_MODEL=gate-laya-v3 LAYA_EXTRA_MODELS='{"gate-laya-v3": "'$H'/models/laya-v3"}' HF_HUB_OFFLINE=1 USE_TF=0 .venv/bin/laya-serve`
  - nerserve: `HF_HUB_OFFLINE=1 ../laya-serve/.venv/bin/python -m nerserve --model-dir $H/models/ner-xlmr-pii --port 8001`
  - 둘 다 `/health`가 200이 되는지 본다.
- **36개 재측정:** `NER_BASE_URL=http://127.0.0.1:8001 JEV_BASE_URL=http://127.0.0.1:8000 JEV_MODEL=gate-laya-v3 JEV_MODEL_DIR=$H/models/laya-v3 uv run python -m eval.run_eval --client laya --ner server --details`
- **설정 화면 확인:** `NER_BASE_URL=http://127.0.0.1:8099 .venv/bin/gate --socket $H/gate-data/run/uicheck.sock ui --policy <scratchpad 정책> --audit <scratchpad 감사 로그> --no-browser` → 출력된 일회용 주소를 Chrome으로 연다.
- **테스트(맥):** `uv run pytest`: **1555 통과, 1 skip**(`4ba937f`)
- **테스트(Linux):** `docker build -f sandbox/gate.Dockerfile -t fuse-jev-gate:ner-v2 . && docker run --rm --device /dev/fuse --cap-add SYS_ADMIN fuse-jev-gate:ner-v2 pytest -q`. **미실행**(돌리기 전에 사용자에게 묻는다)
- **인프라·런타임 상태:** laya-serve, nerserve, 게이트 데몬, 설정 화면 모두 꺼져 있다. 시험 정책·재측정 임시 파일은 이번 세션 scratchpad에만 있다.

## 미해결 · 블로커
1. **3e PR 머지**(사용자).
2. Linux 컨테이너 테스트 미실행(사용자 결정으로 뺌).
3. 실제 데모 폴더 안내문이 옛 문구다(다음 데모 전에 다시 만들기, 사용자 확인).
4. 작은 후보(사용자가 정하면)
   - 문장 해석기의 다른 안내도 "~어요. 저장하지 않았습니다."처럼 말투가 섞여 있다. 이번에는 날짜·사건 상자만 고쳤다.
   - 설정 화면 폰 폭은 보지 않았다.
5. 팀원 학습 데이터 출처·라이선스는 답이 없다. 가중치는 저장소에 넣지 않으므로 막히지는 않는다.
