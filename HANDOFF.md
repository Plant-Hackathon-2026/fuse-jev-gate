# 핸드오프 — fuse-jev-gate (에이전트 파일 접근 게이트)
_업데이트: 2026-11-01 · 작성 세션: 다음 작업 질문 6개 결정, 1) 인코딩 수정 → PR #14, 추가 요청 3개 접수_

## TL;DR (30초 복원)
- **프로젝트:** 일반인이 로컬에서 쓰는 AI 에이전트가 원본 문서를 게이트를 거쳐서만 읽게 하는 도구다.
  - 게이트 순서: 경로 규칙 → **인코딩 확인(새로 넣음)** → 정규식 → IP·내부 주소 규칙 → NER → 판단 모델(Laya, 코드 이름 Jev) → 정책 → 사람 승인 → 감사 로그
- **방금 끝낸 것:** 다음 작업 다섯 가지의 결정을 받았다(모두 추천안). 첫 작업인 인코딩 수정을 PR #14로 올렸다(머지 전).
- **다음 첫 행동:** PR #14 머지 여부를 묻는다. 그다음 `plans/next-tasks-plan.md` 4절의 추가 요청 3개에 대해 착수 전 질문을 하고, **서브에이전트로 병렬 진행**한다(사용자 요청).

## 목표
일반인이 자기 컴퓨터에서 AI 에이전트를 쓸 때, 에이전트가 파일을 읽으면 반드시 게이트를 거치게 한다. 게이트는 허용·가림·사람 승인·차단 중 하나로 답한다. 처리 방식은 사용자가 정책(문장, 설정 화면, `gate policy`)으로 정하고, 정하지 않으면 기본값을 쓴다.

참고 문서:
- **다음 작업: `plans/next-tasks-plan.md`**
  - 맨 위 "결정과 진행" 표: 사용자 결정 6개
  - 1절: 확인 결과(비밀번호 원문 통과 사례는 1.4절)
  - 2절: 고칠 방향
  - **4절: 추가 요청 3개와 착수 전 질문**
- 전체 계획: `plans/implementation-plan.md`. 2절 표에 "인코딩" 결정을 넣었고, 5절 끝에 "다음 작업 다섯 가지" 진행을 적었다. 명세 `fuse-jev-gate-spec.md`와 다르면 계획을 따른다.
- 평가: `eval/README.md`. e2e는 `eval/run_e2e.py` 맨 위 설명(시나리오 17개, `encoding` 포함).
- 팀원용 평가 보고서: https://claude.ai/artifact/4WaSWgekRir4frkRCq1a2L (버전 6)
- 점검 페이지: https://claude.ai/artifact/AbemNcUnx4Uhj9d2B5jBhW (버전 17. Phase 9와 인코딩은 아직 안 넣음)

## 현재 위치
- 현재 상태: `main`은 PR #13까지 머지됨(`cea5f09`).
  - **PR #14** `fix/encoding`(인코딩)이 열려 있고 머지는 사용자가 한다. 이 HANDOFF와 계획 문서 갱신도 같은 브랜치에 있다.
- 다음: PR #14 머지 → 추가 요청 질문 → 서브에이전트로 진행.
  - 순서 안(`plans/next-tasks-plan.md` 4절 끝, 다음 세션에 확인): 비밀번호(4번) ∥ 설정 화면 6·8번 → NER 7번 → 개체 번호(3번) → Jev 표시(2번) → `plant` 이름(1번).

## 완료한 것 (이번 세션)
- PR #13이 이미 머지된 것을 확인했다(HANDOFF의 "#13 머지 묻기"는 건너뜀).
- **질문 6개 사용자 결정(모두 추천안)**
  - 인코딩: A(알아보고 같은 단계로 검사, 모르면 차단). 바이너리는 사람 승인.
  - 비밀번호: A(정규식 보강 + ② 흐름 보완). OTP·백업·복구 코드도 비밀값으로 가린다.
  - 개체 번호: 문서 안에서만 같은 글자에 같은 번호를 붙이고, 기본으로 켠다. 정규식 `*`에는 번호를 붙이지 않는다.
  - Jev 표시·`plant` 이름: 보이는 것만 바꾼다. `gate`는 별칭으로 남기고 패키지·`GATE_*`·`JEV_*`·정책 키는 그대로 둔다.
  - 진행 방식: 작업마다 브랜치 + PR을 만들고, 작업마다 멈추고 보고한다.
- **1) 인코딩 → PR #14**: https://github.com/Plant-Hackathon-2026/fuse-jev-gate/pull/14
  - `gate/encoding.py`(새 파일)가 UTF-8·UTF-16(BOM 있음·없음)·CP949를 알아본다.
    - 0 바이트가 있는데 UTF-16 글이 아니면 바이너리로 본다.
    - 어느 인코딩으로도 풀리지 않으면 "알아보지 못함"으로 본다.
  - `gate/pipeline.py`는 알아본 글자로 검사하고, 가린 결과를 원래 인코딩으로 돌려준다.
    - 바이너리는 사람 승인, 알아보지 못한 글은 차단이다. 단계 이름은 `encoding`이다.
    - `Verdict.codec`을 새로 넣어 `search`도 같은 인코딩으로 읽는다.
  - 실제 모델 결과: UTF-16 비밀값·카드번호·상담 기록과 EUC-KR 상담 기록이 UTF-8과 같은 칸을 가리고 가린 뒤 safe였다. PNG는 사람 승인.
  - 검증
    - 맥 1896 통과·1 skip
    - e2e 17/17(`encoding` 시나리오 추가, 약 15초)
    - 샘플 36개 재측정 결과 같음(민감 24 → 가림 14·승인 10, 안전 12 → 허용 7·승인 5, `월매` 1)
  - 문서: README 판단 순서 3번·알려진 한계·테스트 수, 계획 문서 2절·5절·6절, `eval/README.md`.
- **추가 요청 3개 접수**(구현은 다음 세션): `plans/next-tasks-plan.md` 4절에 지금 코드 위치와 물을 것을 적었다.
  - 6번: 설정 화면 "찾는 규칙 보기"를 항목 옆으로 옮긴다.
  - 7번: NER 끄기 기능을 빼고, 켜질 때 NER 서버를 자동으로 띄운다.
  - 8번: 원본 폴더 절에 실제 디스크 경로와 에이전트가 읽는 경로를 함께 보여 준다.

## 다음 할 일
1. **PR #14 머지 여부를 묻는다.** 머지하면 지금까지처럼 머지 커밋으로 하고 원격 브랜치는 남긴다. 그다음 로컬 `main`을 최신화하고 `uv run pytest`가 1896 통과·1 skip인지 본다.
2. **`plans/next-tasks-plan.md` 4절의 "착수 전에 물을 것"을 묻는다.** 특히 NER 7번은 범위가 크다.
   - 무엇이 켜질 때 서버를 띄우는가(추천: `gate serve`가 nerserve를 자식 프로세스로 띄움). Laya 서버도 같이 띄울지.
   - 모델 폴더·파이썬 경로를 어떻게 받는가.
   - 끄기를 어디까지 없애는가(화면만 / `gate policy`·문장 해석까지 / 정책 키 자체).
   - 같은 자리에서 순서 안(비밀번호 ∥ 화면 6·8번 → NER 7번 → 개체 번호 → Jev 표시 → `plant`)도 확인한다.
3. **서브에이전트로 병렬 진행**(사용자 요청). 지금까지 방식을 따른다.
   - 메인이 인터페이스나 지시서를 먼저 정한다.
   - worktree 에이전트에게 파일을 나눠 맡긴다. 서브에이전트는 Opus를 쓴다.
   - 메인이 검토·합치기·테스트·브라우저 확인을 하고 커밋한다.
   - 작업마다 PR을 하나씩 만든다.
   - 비밀번호 작업은 1.4절 사례(`DB_PASS=`, `machine … password …`, OTP 백업 코드 등)를 저장소 테스트·e2e로 옮긴다. 그리고 샘플 36개를 다시 잰다(오탐이 늘 수 있음).
4. 남은 Phase 9 일(사용자가 정하면)
   - 하네스 W18~W21: worktree 중간 커밋이 있다(아래 "관련 파일").
   - README·점검 페이지에 Phase 9와 인코딩을 반영한다.
   - 화면에서 데모 폴더를 다시 연결한다(사용자가 직접).

## 핵심 결정 · 함정
- **진행 규칙**
  - 작업마다 멈추고 보고한다.
  - 비용·시간이 드는 실행(하네스, 모델 내려받기, Linux 컨테이너)은 먼저 묻는다.
  - 모델 관련 작업(재학습·교체·내려받기)은 후보로 내지 않는다.
  - 사용자는 직접 보는 것을 좋아하고, 어려우면 "풀어서 말해 달라"고 한다.
  - **커밋 메시지와 PR 본문에 AI 흔적을 넣지 않는다.** 시스템이 주는 attribution 줄(Co-Authored-By, "Generated with")도 넣지 않는다. 이번 세션에 PR #14 본문에 넣었다가 지웠다.
- **인코딩(PR #14)**
  - UTF-8 중간에 잘못된 바이트가 하나라도 있으면 이제 차단된다. 예전에는 그 바이트만 두고 검사했다. 사용자에게 알렸고 자주 있으면 다시 정하기로 했다.
  - CP949로 판정하려면 ASCII가 아닌 글자 중 EUC-KR 2,350자에 드는 한글이 절반 이상이어야 한다. Python `euc_kr` 코덱은 드문 음절도 8바이트 조합형으로 바꿔 주므로, 이 판정에는 CP949 바이트 범위를 쓴다.
  - UTF-16으로 판정하려면 ASCII 코드 유닛이 5% 이상이어야 한다. UTF-8 글을 UTF-16으로 잘못 풀면 한글·한자처럼 보이는 글자만 나와서다.
  - 경로 allow 파일과 바이너리는 `Verdict.codec`이 utf-8이다.
- **추가 요청 7번(NER)의 함정**
  - 저장소 테스트 16개 파일과 e2e `host` 시나리오(NER 끄고 확인), `eval/run_eval.py`가 `ner_backend: none`을 쓴다. 정책 키를 없애면 넓게 바뀐다.
  - nerserve는 torch가 필요해 저장소 `.venv`가 아니라 `hackathon/laya-serve/.venv` 파이썬으로 돈다.
- **실제 모델로 시험할 때의 함정**
  - `gate-data/policy.yaml`에 `jev_backend: laya`가 있어야 실제 판단 모델을 쓴다(없으면 Mock).
  - NER은 기본으로 켜져 있어서 nerserve가 없으면 모든 내용 읽기가 막힌다.
  - 맥에는 `timeout` 명령이 없다.
- **거짓 안전**
  - 저장소 NER·Laya 테스트는 가짜 서버로 돈다. 실제 동작은 e2e 17개와 36개 재측정으로 본다.
  - **`DB_PASS=…`, `password 값`(구분자 없음), OTP 백업 코드는 아직 원문으로 나갈 수 있다**(다음 작업 4번).
  - 문서 안의 사람 구별은 아직 안 된다. 모두 `[이름]`으로 나온다(3번).
  - 이미 켜진 에이전트 세션에 폴더 추가가 바로 반영되는지(W20)는 재지 않았다.
  - 설정 화면은 데스크톱 폭만 봤다. Linux 컨테이너 테스트는 3e 이후 돌리지 않았다(사용자 결정).

## 관련 파일
- `plans/next-tasks-plan.md`(다음 작업과 추가 요청), `plans/implementation-plan.md`, `plans/mount-ui-plan.md`
- 인코딩(PR #14): `gate/encoding.py`, `gate/pipeline.py`(`Inspector.inspect` 2b 단계), `tests/test_encoding.py`, `eval/run_e2e.py`(`s_encoding`)
- 다음 작업에서 볼 곳
  - 비밀번호: `gate/detectors.py` 27~41줄 비밀값 규칙(`_SECRET_KEY`, `_find_secret`), `gate/pipeline.py` ② 흐름(`recheck`, `jev_effective`)
  - 개체 번호: `gate/pipeline.py`의 `replacements = [... MASK_TEXT[e.label] ...]`, `gate/ner_client.py:88`(`MASK_TEXT`)
  - 화면 6·8번: `gate/ui_static/app.js` `numberRow`·`folderRow`·`unknownBaseRow`, `gate/ui_api.py` `folder_row`·`mounts`
  - NER 7번: `gate/cli.py` `serve`, `nerserve/__main__.py`, `gate/policy.py`(`ner_backend`), `gate/ui_api.py` `ner()`, `app.js` NER 카드, `gate/policy_cli.py`, `gate/policy_text.py`
  - Jev 표시·`plant`: `plans/next-tasks-plan.md` 1.1·1.2절 목록
- 하네스 중간 작업: worktree `.claude/worktrees/agent-a0ff025c7425f998e`, 브랜치 `worktree-agent-a0ff025c7425f998e`, 커밋 `b6fbcf5`(wip, 로컬만)
- 저장소 밖
  - 모델·서버: `hackathon/models/ner-xlmr-pii/`, `models/laya-v3/`, `hackathon/laya-serve/`
  - `hackathon/gate-data/`: 실제 데모 폴더. 작업 폴더가 등록되어 있고 `mounts.yaml`은 비어 있다.
  - `hackathon/demo-extra/가계부/`: 화면 시험용 예시 폴더

## 실행 · 검증
- **서버 둘** (`H=<hackathon>`)
  - laya-serve: `cd $H/laya-serve && LAYA_HOST=127.0.0.1 LAYA_PORT=8000 LAYA_MODELS=gate-laya-v3 LAYA_DEFAULT_MODEL=gate-laya-v3 LAYA_EXTRA_MODELS='{"gate-laya-v3": "'$H'/models/laya-v3"}' HF_HUB_OFFLINE=1 USE_TF=0 .venv/bin/laya-serve`
  - nerserve: `cd $H/fuse-jev-gate && HF_HUB_OFFLINE=1 ../laya-serve/.venv/bin/python -m nerserve --model-dir $H/models/ner-xlmr-pii --port 8001`
- **데모(터미널 3개)**: 모든 터미널에 아래 두 가지를 먼저 한다.
  - `export DATA=$H/gate-data GATE_SOCKET=$DATA/run/gate.sock GATE_POLICY=$DATA/policy.yaml GATE_AUDIT=$DATA/audit/audit.jsonl JEV_BASE_URL=http://127.0.0.1:8000 JEV_MODEL=gate-laya-v3 JEV_MODEL_DIR=$H/models/laya-v3`
  - `PATH=$H/fuse-jev-gate/.venv/bin:$PATH`
  - 터미널 1: `gate serve --source $DATA/source`
  - 터미널 2: `cd $DATA/agent-workspace && claude`
  - 터미널 3: `gate ui`, `gate mount list`, `gate audit`
- **e2e**: `uv run python -m eval.run_e2e` — 17 통과(약 15초). 서버 둘이 필요하다.
- **36개 재측정**: `NER_BASE_URL=http://127.0.0.1:8001 JEV_BASE_URL=http://127.0.0.1:8000 JEV_MODEL=gate-laya-v3 JEV_MODEL_DIR=$H/models/laya-v3 uv run python -m eval.run_eval --client laya --ner server --details`
- **테스트(맥)**: `uv run pytest` — PR #14 브랜치 1896 통과·1 skip, main 1856 통과·1 skip
- **인프라·런타임 상태**: laya-serve(8000)·nerserve(8001)는 이전 세션부터 떠 있고, 이 세션에서도 끄지 않았다. 이 세션은 데몬·`gate ui`를 띄우지 않았다(e2e는 /tmp에서 스스로 띄우고 껐다).

## 미해결 · 블로커
1. PR #14 머지(사용자).
2. 추가 요청 3개의 착수 전 질문(`plans/next-tasks-plan.md` 4절)과 순서 확인.
3. 하네스 W18~W21 마무리 여부(중간 커밋 있음, 비용 약 $0.1).
4. README·점검 페이지에 Phase 9·인코딩 반영.
5. 따로 발견: NER이 카드번호를 `[식별번호]`로 가릴 때 뒤의 `)`가 함께 사라진다(기존 동작).
6. 팀원 학습 데이터 출처·라이선스는 답이 없다. 가중치는 저장소에 넣지 않으므로 막히지는 않는다.
