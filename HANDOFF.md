# 핸드오프 — fuse-jev-gate (에이전트 파일 접근 게이트)
_업데이트: 2026-11-01 · 작성 세션: NER 재학습판 조합 평가 → 3e(NER 교체 + IP·내부 주소 규칙 + NER 기본 켬) 구현·맥 테스트·실제 승인 창 데모_

## TL;DR (30초 복원)
- **프로젝트:** 일반인이 로컬에서 쓰는 AI 에이전트가 원본 문서를 게이트를 거쳐서만 읽게 하는 도구다.
  - 게이트 순서: 경로 규칙 → 정규식 → IP·내부 주소 규칙 → NER → 판단 모델(Laya, 코드 이름은 Jev) → 정책 → 사람 승인 → 감사 로그
- **방금 끝낸 것:** **3e 구현**(브랜치 `feat/ner-v2`, 원격에 올림·PR 아직 없음). 맥 `uv run pytest` 1541 통과·1 skip. 실제 모델(v3 + 새 NER)로 데몬을 띄워 가림·허용·**맥 승인 창**까지 사용자가 직접 확인했다.
- **다음 첫 행동:** 실제 모델로 샘플 36개를 다시 잰다(아래 "실행 · 검증"의 서버 셋 + `eval.run_eval --client laya --ner server --details`). 결과를 `eval/README.md` 조합 비교 표의 "v3 + 새 NER"과 비교한다.

## 목표
일반인이 자기 컴퓨터에서 AI 에이전트를 쓸 때, 에이전트가 파일을 읽으면 반드시 게이트를 거치게 한다. 게이트는 허용·가림·사람 승인·차단 중 하나로 답한다. 처리 방식은 사용자가 정책(문장, 설정 화면, `gate policy`)으로 정하고, 정하지 않으면 기본값을 쓴다.

참고 문서:
- **이번 작업 계획: `plans/ner-v2-plan.md`**(3e). 2절 결정은 사용자가 정한 것이다.
- 전체 계획: `plans/implementation-plan.md`(명세 `fuse-jev-gate-spec.md`와 다르면 계획을 따른다). **3e는 아직 반영하지 않았다.**
- 평가: `eval/README.md` "NER 재학습판 조합 비교 (2026-11-01)". **교체 뒤 다시 잰 결과는 아직 없다.**
- 팀원용 평가 보고서: https://claude.ai/artifact/4WaSWgekRir4frkRCq1a2L — **버전 4**(맨 앞에 조합 비교 절). "링크가 있는 누구나" 공유 중. 사용자 요청: 교체 뒤 다시 잰 결과도 넣는다.
- 점검 페이지: https://claude.ai/artifact/AbemNcUnx4Uhj9d2B5jBhW — 버전 13(Phase 8까지). 3e 미반영.

## 현재 위치
- 현재 상태: 3e 코드·테스트·README는 `feat/ner-v2`에 있다. 실제 모델 재측정, 화면 확인, 문서(평가 결과·계획·보고서), PR이 남았다.
- 다음: 재측정 → 화면 확인 → 문서 → PR.

## 완료한 것 (이번 세션)
- **문서 정리:** 계획 Phase 8 제목을 머지된 상태로(브랜치 `docs/ner-v2-eval`, **PR #8 열림**, 머지는 사용자).
- **조합 평가**(PR #8에 기록): Laya {3b, v3} × NER {끔, 처음 32종, 새 6종, 합침}. 추천 **v3 + 새 NER 단독** — 36개에서 민감 가림 14·승인 10, 안전 허용 7·승인 5, 승인 없이 남은 식별자 1(`월매`), 승인하면 남는 것 0(처음 NER 15줄), 승인 창 15(처음 24). hard_test 53/54·61/62. 겹침 확인 0.
- **사용자 결정(3e, 2026-11-01):** 새 NER 단독, ID_NUMBER → `student_id` 묶음(이름 "식별번호(학번·사번·여권번호 등)", 키 유지), AGE_BIRTH는 값 + 앞 10자로 생년월일/나이, IP·내부 주소는 규칙(NER 꺼도 적용, `host` 묶음), **날짜·사건 묶음 빼기**(예전 키는 읽고 무시·쓰기 거부, `plain`은 "나이"), **NER 기본 켬**.
- **3e 구현**(worktree 에이전트 → 메인 검토·합침, 커밋 7개 `ad78186`~`edcc185`, AI 흔적 없음):
  - `gate/ner_client.py`(6종만 받음, `split_kind`, 기본 주소 127.0.0.1:8001), `gate/host_rules.py`(새), `gate/pipeline.py`(정규식 > 규칙 > NER), `gate/policy.py`·`policy_change.py`·`policy_cli.py`(예전 키, 기본 켬), `gate/policy_text.py`(빼낸 묶음은 unsupported), `gate/ui_api.py`·`app.js`(NER 줄 9개, host 안내), `gate/cli.py`(serve 시작 안내), README, `eval/policy_sentences*.csv`(해당 행만 unsupported로).
  - 정책 문장 Mock: 첫 세트 118 → 119/120, 검증 세트 32/50 그대로(새 잘못 저장 없음).
  - 맥 테스트 1541 통과·1 skip. **Linux 컨테이너는 사용자 요청으로 돌리지 않았다.**
- **실제 데모**(scratchpad 정책·감사 로그, 실제 `gate-data`는 안 건드림): 상담기록 → 가림(`[이름]`, `***-****-****`), 빈 이력서 양식 → 허용, 진료메모 → 맥 승인 창 → 사용자가 "이번만 허용" → 가린 본(`[소속]`·`[장소]`). 서버는 모두 끔.
- 팀원용 보고서 버전 4(조합 비교·NER 재학습 참고 절).

## 다음 할 일
1. **실제 모델로 36개 다시 재기.** 아래 서버 셋을 띄우고 `eval.run_eval --client laya --ner server --details`. 기대값은 조합 비교 표의 "v3 + 새 NER"(민감 가림 14·승인 10, 안전 허용 7·승인 5, 승인 없이 남은 식별자 1, 승인하면 0). 가림 표시가 평가 때(`[학교]` 등)와 달라져(`[소속]`) v3 확신도가 조금 다를 수 있다. 다르면 샘플별로 원인을 본다.
2. **설정 화면 브라우저 확인**(에이전트가 화면으로는 보지 않았다, `node --check`만): NER 카드 9줄, host 줄 "규칙으로 찾아요" 안내, NER 껐을 때 host 뺀 8줄의 "NER 꺼짐" 태그(26px)와 "내가 바꿈"(22px)이 한 줄에서 어색하지 않은지, NER 응답 없음 배너, 예전 날짜·사건 항목의 "지금은 찾지 않는 정보" 태그, "날짜는 가려줘" 해석 시 "저장할 수 없어요" 상자. 띄우기: `gate --socket <소켓> ui --policy <scratchpad 정책> --audit <scratchpad 감사 로그>`.
3. **Linux 컨테이너 테스트를 돌릴지 사용자에게 묻는다**(이번엔 하지 않기로 함). README의 Linux 수는 "맥 + 1"로 적혀 있다.
4. **문서:** `eval/README.md`(교체 뒤 결과 절), `plans/implementation-plan.md`(2절 결정 표에 3e, 5절 3e 항목, 6절 한계: NER 기본 켜짐·서버 없으면 차단, IP 규칙 한계, 날짜·사건 뺌), 팀원용 보고서(교체 뒤 결과, "남은 한계"의 "NER은 기본으로 꺼 두었습니다"와 "다시 돌리는 법"의 `models/ner`를 새 모델로), 점검 페이지(사용자에게 먼저 묻기), HANDOFF.
5. **PR:** `feat/ner-v2` → `main`. PR #8 커밋이 함께 들어 있다(#8이 먼저 머지되면 차이가 줄어든다). 머지는 사용자.
6. 데모에서 찾은 작은 문제(사용자에게 고칠지 묻기): `gate cat` 끝 안내가 NER로 가렸어도 "개인정보 일부를 같은 길이의 *로 가렸습니다"라고 한다(원래 있던 문구).

## 핵심 결정 · 함정
- **진행 규칙**
  - 작업마다 멈추고 보고한다. 서브에이전트는 Opus. 큰 구현은 worktree 에이전트 → 메인 검토·전체 테스트·실제 실행 확인 → PR이 잘 맞았다. 에이전트 worktree는 합친 뒤 지웠다.
  - 비용·시간이 드는 실행(하네스, 모델 내려받기, 이번엔 Linux 컨테이너도)은 먼저 묻는다.
  - 사용자는 설명이 어려우면 "풀어서 말해 달라"고 한다. 실제 예로 단계별로 설명하면 통한다. 직접 보는 것(승인 창 데모)을 좋아한다.
- **3e에서 계획과 달라진 점(에이전트 판단, 메인이 검토함)**
  - `outside`: 잘린 개체에 조사 하나만 남으면 버린다(`hong@example.com으로`의 `으로`가 `[주소]`가 되지 않게).
  - Jev는 NER이 꺼져 있어도 규칙으로 가린 내용(`[IP]` 포함)을 본다(전에는 정규식만 가린 내용).
  - IPv6: `::`가 있어도 값 칸이 2개 미만(`::1`)이면 안 잡는다(코드 `a[::2]`와 구별).
  - 내부 주소: 내부 단어가 칸과 정확히 같고, 그 칸이 마지막이거나 마지막 칸이 흔한 TLD일 때만. `threading.local()` 같은 호출은 뺀다. 한계: 네 칸 버전 번호(`2.0.1.4`)는 IP로 가리고, 내부 단어 없는 사내 도메인은 놓치고, `corp.example.com`은 가린다.
  - `split_kind`: 전화 70%는 공백 뺀 글자 기준, "개월"은 나이, 붙여 쓴 6·8자리 숫자는 생년월일. 해석기 birth 단서에 `출생 연도`·`태어난 해`를 더했다.
- **NER 기본 켬의 함정**
  - NER 서버(기본 127.0.0.1:8001)가 없으면 **모든 내용 읽기가 차단**된다. 데몬 시작 때 안내가 나온다. 끄려면 `gate policy set ner_backend none`(완화라 한 번 더 묻는다).
  - 우회 시험 하네스(`sandbox/bypass.py`)는 고치지 않았다. 대조군 C1·C2가 통과하려면 nerserve를 띄우거나 NER을 끈 정책을 쓴다.
  - 처음 32종 NER 체크포인트(`models/ner`)는 이제 모르는 라벨이라 차단된다. nerserve에는 새 모델 폴더를 준다.
- **평가 함정**: `eval_with_meta`는 새 NER 체크포인트 선택에 쓴 세트라 유리하다. hard_test도 팀원 세트다. 독립적인 것은 샘플 36개다. 학습 데이터 출처·라이선스는 아직 답이 없다(`meta.source`가 실제 상담 데이터로 보임). 가중치·팀원 데이터는 저장소에 넣지 않는다.
- **설정 화면 보안**(`plans/ui-plan.md`): 127.0.0.1, 일회용 열쇠, Host·Origin 검사, CSP, 새로 고침 유지 쿠키. 정책은 사람만 바꾼다. `gate policy` 바꾸기 명령은 tty가 아니면 거부한다 → 자동화로 시험 정책을 만들 때는 `gate.policy.write_entries`로 scratchpad 파일에 썼다(실제 `gate-data/policy.yaml`은 안 건드림).
- **이전부터**: ② 흐름(NER 켜짐 + 가린 것 있음 → Laya가 가린 결과를 보고 safe면 가림, 아니면 승인), Laya·NER 오류는 차단. laya-serve는 `LAYA_HOST=127.0.0.1`과 `LAYA_MODELS`를 꼭 준다. 긴 한글 파이썬은 파일로 써서 `python3 -I`. zsh에서 `echo =====`는 따옴표로.
- **거짓 안전**
  - 저장소 NER·Laya 테스트는 가짜 서버다. 3e의 실제 동작은 이번 데모(파일 3개)로만 봤다. 36개 재측정 전에는 조합 비교 수치를 3e 코드의 결과로 믿지 않는다.
  - 화면 변화는 브라우저로 보지 않았다.
  - Linux 컨테이너 테스트를 돌리지 않았다.

## 관련 파일
- `plans/ner-v2-plan.md`: 3e 계획·결정
- `gate/ner_client.py`: 서버 라벨 6종, `split_kind`, 묶음 9개, `RETIRED_ENTITY_GROUPS`, `RULE_GROUPS`, `DEFAULT_BASE_URL`
- `gate/host_rules.py`, `tests/test_host_rules.py`: IP·내부 주소 규칙
- `gate/pipeline.py`: 정규식 > 규칙 > NER 합치기, Jev가 보는 내용
- `gate/policy.py`(`RETIRED_SETTING_KEYS`, `is_retired`, 기본 `ner_backend: server`), `gate/policy_change.py`(`needs_ner`, 예전 키 쓰기 거부)
- `gate/policy_text.py`, `eval/policy_sentences*.csv`, `eval/policy_sentences_notes.md`
- `gate/ui_api.py`, `gate/ui_static/app.js`: NER 줄·host 안내
- `eval/README.md`: 조합 비교 절(교체 전 임시 스크립트 결과)
- 저장소 밖
  - `hackathon/models/ner-xlmr-pii/`(새 NER), `models/laya-v3/`(판단 모델), `models/ner/`(처음 NER, 이제 안 씀)
  - `~/Downloads/handoff_ner/`: 팀원 원본(스크립트·데이터·hard_test)
  - `hackathon/gate-data/`(실제 데모 폴더, 이번에 바꾸지 않음), `hackathon/laya-serve/`

## 실행 · 검증
- **서버 셋** (`H=<hackathon>`, 저장소 루트에서):
  - laya-serve: `cd $H/laya-serve && LAYA_HOST=127.0.0.1 LAYA_PORT=8000 LAYA_MODELS=gate-laya-v3 LAYA_DEFAULT_MODEL=gate-laya-v3 LAYA_EXTRA_MODELS='{"gate-laya-v3": "'$H'/models/laya-v3"}' HF_HUB_OFFLINE=1 USE_TF=0 .venv/bin/laya-serve`
  - nerserve: `HF_HUB_OFFLINE=1 ../laya-serve/.venv/bin/python -m nerserve --model-dir $H/models/ner-xlmr-pii --port 8001`
  - 둘 다 `/health`가 200인지 본다(모델 로드 수 초).
- **36개 재측정:** `NER_BASE_URL=http://127.0.0.1:8001 JEV_BASE_URL=http://127.0.0.1:8000 JEV_MODEL=gate-laya-v3 JEV_MODEL_DIR=$H/models/laya-v3 uv run python -m eval.run_eval --client laya --ner server --details`
- **데모 데몬(이번 세션 방식):** 시험 정책을 scratchpad에 `write_entries`로 만든다(`jev_backend: laya`, `approval.timeout_seconds: 120`). `JEV_BASE_URL=… JEV_MODEL=gate-laya-v3 JEV_MODEL_DIR=… NER_BASE_URL=http://127.0.0.1:8001 .venv/bin/gate --socket $H/gate-data/run/demo.sock serve --source <샘플 복사본 폴더> --policy <시험 정책> --audit <시험 감사 로그>` → `gate --socket … cat /파일`. 소켓은 경로 길이 때문에 scratchpad가 아니라 `gate-data/run/`에 둔다. 승인 창은 맥 대화상자로 뜬다.
- **테스트(맥):** `uv run pytest` — **1541 통과, 1 skip**(`feat/ner-v2` `edcc185`)
- **테스트(Linux):** `docker build -f sandbox/gate.Dockerfile -t fuse-jev-gate:ner-v2 . && docker run --rm --device /dev/fuse --cap-add SYS_ADMIN fuse-jev-gate:ner-v2 pytest -q` — **미실행**(돌리기 전에 사용자에게 묻는다)
- **인프라·런타임 상태:** laya-serve, nerserve, 게이트 데몬, 설정 화면 모두 꺼져 있다. `gate-data/policy.yaml`은 없다(기본 정책 = 이제 NER 켜짐). 이번 데모 파일·정책·감사 로그는 이 세션 scratchpad에만 있다.

## 미해결 · 블로커
1. **실제 모델 재측정 전**이라 3e가 조합 비교 수치를 재현하는지 모른다(다음 할 일 1번).
2. **PR #8 머지**(사용자), 3e PR은 아직 없음.
3. **Linux 컨테이너 테스트** 미실행 — 돌릴지 사용자 결정.
4. `gate cat` 안내 문구("같은 길이의 *") 고칠지 사용자 결정.
5. 팀원 학습 데이터 출처·라이선스 답 없음(가중치는 저장소에 넣지 않아 막히지는 않음).
