# fuse-jev-gate — AI 에이전트 파일 접근 게이트

내 컴퓨터에서 AI 에이전트(Claude Code)를 쓸 때, 에이전트가 원본 문서를 **게이트를 거쳐서만** 읽게 하는 도구입니다. 게이트는 파일마다 허용·가림·사람 승인·차단 중 하나로 답합니다. 주민등록번호·연락처·비밀번호 같은 값은 같은 길이의 `*`로 가려서 돌려줍니다.

- 대상: 개발자가 아닌 일반 사용자가 자기 컴퓨터에서 에이전트를 쓰는 경우입니다. 정책을 따로 정하지 않아도 기본값으로 안전하게 동작합니다.
- 상태: 해커톤 프로젝트입니다. 맥(Apple Silicon, macOS 26)에서 만들고 시험했습니다. **판단 모델 Jev는 아직 Mock(키워드 규칙)입니다**([알려진 한계](#알려진-한계)).
- 이름의 FUSE는 처음 설계(FUSE 마운트)에서 왔습니다. 지금은 1Password처럼 데몬과 Unix 소켓을 쓰는 구조입니다. 바꾼 이유는 [`plans/implementation-plan.md`](plans/implementation-plan.md) 1절에 있습니다.

## 목차

- [구조](#구조)
- [설치](#설치)
- [빠른 시작](#빠른-시작)
- [Claude Code 연결](#claude-code-연결)
- [정책 바꾸기](#정책-바꾸기)
- [사람 승인](#사람-승인)
- [감사 로그](#감사-로그)
- [우회 시험 하네스](#우회-시험-하네스)
- [테스트](#테스트)
- [알려진 한계](#알려진-한계)
- [폴더 구조와 문서](#폴더-구조와-문서)

## 구조

```
[원본 폴더]  ← 에이전트 세션은 Claude Code 권한 규칙 + 샌드박스로 직접 못 읽음
     ▲ 원본 읽기 (데몬은 샌드박스 밖, 내 계정 권한)
[게이트 데몬]  gate serve
     │  경로 규칙 → 정규식 탐지·가림 → Jev 판단(캐시) → 정책 → 사람 승인 → 감사 로그
     ▲ Unix 소켓 gate.sock (요청자 PID·UID 확인)
     └── gate CLI    Claude Code가 Bash로 실행 (샌드박스 안에서 이 소켓 하나만 열 수 있음)
[Claude Code]  에이전트 세션

[내 터미널]  gate policy → 정책 파일        gate approve / deny → 승인 소켓 approve.sock
             gate audit  → 감사 로그        (에이전트는 셋 다 열 수 없음)
```

에이전트는 `gate cat /상담/기록.txt`처럼 게이트 안 경로로 요청합니다. 데몬은 원본 폴더 밖으로 나가는 경로(`..`, 심볼릭 링크)를 거부합니다. 오류는 `ENOENT`(없음), `EACCES`(차단), `EROFS`(쓰기)로 돌려줍니다.

### 판단 순서

`gate cat` 요청 하나는 다음 순서로 판단합니다. 가장 엄격한 결과를 따릅니다.

1. **정책 파일 확인.** 정책 파일이 잘못되어 있으면 고칠 때까지 내용 읽기를 모두 막습니다.
2. **경로 규칙.** `*.env`, `*.pem`, `*.key`, `id_rsa*`, `.netrc`, `.aws/credentials` 같은 파일은 내용을 보지 않고 거부합니다.
3. **정규식 탐지.** 주민·외국인등록번호, 카드번호, 휴대폰·일반 전화번호, 이메일, 계좌번호, 비밀값(비밀번호·API 키·토큰·개인 키)을 찾아 같은 길이의 `*`로 가립니다. 종류마다 가림·사람 승인·거부를 정할 수 있습니다.
4. **Jev 판단.** 가린 내용을 Jev에 보내 `safe`·`sensitive`·`highly_sensitive`와 확신도를 받습니다. 같은 파일·정책·요청이면 캐시를 씁니다. Jev가 실패하면 차단합니다.
5. **정책.** 기본값은 `safe`이고 확신도 0.95 이상이면 허용, 민감이고 0.95 이상이면 거부, 그 사이면 사람에게 묻습니다.
6. **사람 승인.** 맥 대화상자나 내 터미널에서 답합니다. 30초 안에 답이 없으면 차단합니다.
7. **감사 로그.** 원문 없이 요청 한 줄을 남깁니다.

| 결과 | 에이전트가 받는 것 |
|---|---|
| 허용 | 원본 그대로. 번호나 비밀값이 있으면 가린 내용 |
| 가림 | 번호·비밀값을 같은 길이의 `*`로 가린 내용 |
| 사람 승인 | 사람이 허용하면 가린 내용, 거부하거나 답이 없으면 `EACCES` |
| 차단 | `EACCES`와 이유 한 줄 (값은 넣지 않음) |

## 설치

필요한 것: macOS, Python 3.12, [uv](https://docs.astral.sh/uv/), Claude Code(2.1.316에서 시험). Linux는 Docker 컨테이너에서 테스트만 돌렸습니다.

```bash
git clone https://github.com/Plant-Hackathon-2026/fuse-jev-gate.git
cd fuse-jev-gate
uv sync                    # .venv/bin/gate 가 생깁니다
```

데이터는 저장소와 에이전트 작업 폴더 **밖에** 둡니다. 에이전트가 쓸 수 있는 폴더 안에 두면 폴더 이름을 바꾸는 식으로 경로 기준 차단을 피할 수 있기 때문입니다. 이 저장소에서는 다음 배치를 썼습니다.

```
gate-data/
├── source/            원본 폴더 (에이전트가 읽을 문서)
├── policy.yaml        내 정책 (gate policy가 만듦, 없으면 기본값)
├── policy-history/    정책 되돌리기 기록
├── run/               gate.sock, approve.sock
├── audit/             audit.jsonl, audit.key
└── agent-workspace/   에이전트 전용 작업 폴더 (gate workspace init이 만듦)
```

경로는 환경변수로 넘깁니다. `gate` 명령마다 같은 값을 씁니다.

```bash
export DATA=$HOME/gate-data
export GATE_SOCKET=$DATA/run/gate.sock GATE_POLICY=$DATA/policy.yaml GATE_AUDIT=$DATA/audit/audit.jsonl
```

맥은 소켓 경로가 104바이트를 넘으면 열지 못합니다. 데이터 폴더는 짧은 경로에 둡니다.

## 빠른 시작

저장소의 합성 샘플(`samples/`)로 게이트를 직접 돌려 봅니다. 아래 결과는 실제로 돌려서 확인했습니다(Mock Jev, 기본 정책).

**터미널 1: 데몬 띄우기**

```bash
export DATA=$HOME/gate-demo
mkdir -p $DATA/source
cp -R samples/medical_note $DATA/source/
printf 'DB_PASSWORD=hunter2\n' > $DATA/source/.env
printf '동네 도서관 휴관 안내\n이번 주 토요일은 휴관합니다.\n문의: 010-1234-5678\n' > $DATA/source/안내문.txt

export GATE_SOCKET=$DATA/run/gate.sock GATE_POLICY=$DATA/policy.yaml GATE_AUDIT=$DATA/audit/audit.jsonl
.venv/bin/gate serve --source $DATA/source          # 맥 대화상자 없이 터미널로만 답하려면 --no-dialog
```

데몬은 요청마다 stderr에 한 줄씩 남깁니다.

**터미널 2: 에이전트처럼 읽기** (같은 환경변수, `gate`는 `.venv/bin/gate`)

```console
$ gate ls /
.env
medical_note/
안내문.txt

$ gate cat /medical_note/none_1.txt              # 허용: 개인정보가 없는 운영 메모
[외래 운영 메모] 내과 외래 / 2026-01-15
...

$ gate cat /medical_note/pii_1.txt               # 차단: Jev가 매우 민감하다고 판단
gate: /medical_note/pii_1.txt: Jev 판단(highly_sensitive, 확신도 0.97)에 따라 차단했습니다 (EACCES)

$ gate cat /.env                                 # 차단: 경로 규칙
gate: /.env: 경로 규칙에 따라 차단했습니다 (EACCES)

$ gate search 진료 /medical_note                  # 통과한 파일에서만 찾고, 막힌 파일은 건너뜀
gate: 건너뜀 /medical_note/pii_1.txt: Jev 판단(highly_sensitive, 확신도 0.97)에 따라 차단했습니다 (EACCES)
...
/medical_note/none_2.txt:1:[진료 참고 메모] 성인 고혈압 첫 진료 때 확인할 것 (내부 교육용)

$ gate cat /안내문.txt                            # 사람 승인: 답할 때까지 기다림
```

**사람 승인하기.** 맥이면 대화상자가 뜹니다(거부·이번만 허용·이 폴더 허용). 터미널 3에서 답해도 됩니다.

```console
$ gate approve                                   # 기다리는 요청 목록
기다리는 승인 요청 1건
  [6e5a98] /안내문.txt  (남은 시간 28초)
     이유: Jev 판단(sensitive, 확신도 0.80)이 확실하지 않습니다
  ...
$ gate approve 6e5a98                            # 이번만 허용 (--folder면 그 폴더 60분)
```

허용하면 터미널 2에 번호를 가린 내용이 나옵니다.

```
동네 도서관 휴관 안내
이번 주 토요일은 휴관합니다.
문의: ***-****-****
gate: 개인정보 일부를 같은 길이의 *로 가렸습니다 (휴대폰 번호 1건)
```

**기록 보기.**

```console
$ gate audit --blocked
2026-10-31 17:34:39  cat     차단       /medical_note/pii_1.txt  — Jev 판단(highly_sensitive, 확신도 0.97)에 따라 차단했습니다 · 찾음: 주민등록번호 1건, 휴대폰 번호 1건 · ...
2026-10-31 17:34:40  cat     차단       /.env  — 경로 규칙에 따라 차단했습니다 · python3.12 pid 25259
기록 6건 중 조건에 맞는 2건 · 차단 2
```

지금 Jev는 Mock이라 판단은 키워드 규칙으로 나옵니다. 그래서 번호가 든 문서는 대부분 승인이나 차단으로 끝나고, 가린 내용은 승인한 뒤에 보입니다. 데몬은 `Ctrl-C`로 끕니다.

## Claude Code 연결

게이트만 띄우면 에이전트는 여전히 원본 폴더를 직접 열 수 있습니다. Claude Code 설정으로 원본 쪽을 막아야 게이트가 유일한 통로가 됩니다. 방법은 두 가지입니다.

### 1. 에이전트 전용 작업 폴더 (`gate workspace init`)

```bash
gate workspace init $DATA/agent-workspace --source $DATA/source
cd $DATA/agent-workspace && claude
```

- [설치](#설치)의 환경변수 세 개(소켓·정책·감사 로그 경로)가 있어야 합니다. 막을 경로를 정확히 적기 위해서입니다.
- 폴더에 `.claude/settings.json`(권한 규칙 + 샌드박스)과 `CLAUDE.md`(에이전트에게 `gate` 쓰는 법 안내)를 만듭니다. 내용이 다른 파일이 있으면 멈추고, `--force`로 덮어씁니다. 설정은 손으로 고치지 말고 다시 만듭니다.
- 이 폴더에서 띄운 세션에만 적용됩니다. **하위 폴더에서 띄우면 적용되지 않습니다.** 세션 안에서 `/sandbox`로 샌드박스가 켜졌는지 볼 수 있습니다.
- 만들어지는 설정의 예: [`sandbox/agent-settings.json`](sandbox/agent-settings.json) (경로는 `/path/to/...`로 바꿔 둠)

### 2. 설치: 모든 세션에 적용 (`gate setup`)

```bash
gate setup --source $DATA/source           # 바뀔 내용을 보여 주고 y/N으로 확인
gate setup --undo                          # 설치 기록대로 되돌리기
gate setup --source $DATA/source --print   # 합칠 설정 조각(JSON)만 출력. MDM 관리 설정으로 배포할 때
```

- 사용자 설정(`~/.claude/settings.json`)에 같은 막기 규칙을 합칩니다. 원래 파일은 백업하고, 다시 실행해도 결과가 같습니다. `--settings-file`로 대상 파일을 바꿀 수 있습니다.
- **이 맥의 모든 Claude Code 세션에서 샌드박스가 켜집니다.** 다른 작업(예: Docker를 쓰는 개발)에 영향이 있을 수 있으니, 시험할 때는 `--settings-file 시험.json`으로 만든 뒤 `claude --settings 시험.json`으로 넣어 보세요.
- 실제 배포는 같은 조각을 MDM 관리 설정으로 내려보낸다고 가정합니다. 관리 설정은 사용자·프로젝트 설정이 덮어쓰지 못합니다.

### 막는 것

| 대상 | 방법 |
|---|---|
| 원본 폴더, 정책 파일, 정책 기록, 감사 로그(키 포함) | 내장 도구 `Read`·`Edit` 거부 규칙 + 샌드박스 `denyRead` (Bash의 `cat`·`grep -r`·Python 스크립트도 막힘) |
| 소켓 폴더 | `Edit` 거부 + 샌드박스 쓰기 제한 (가짜 데몬 띄우기 막기) |
| 승인 소켓 | 샌드박스 `allowUnixSockets`에 `gate.sock`만 넣음 (에이전트가 스스로 승인하지 못함) |
| 샌드박스 끄기 | `failIfUnavailable: true`, `allowUnsandboxedCommands: false` |
| Claude 설정 파일·MCP 정의 수정, bypass 모드 | 작업 폴더 설정에만 넣음(`gate setup`에는 없음). MDM이 없을 때를 위한 방어 |

## 정책 바꾸기

정책은 **사람이 자기 터미널에서만** 바꿉니다. 아무것도 정하지 않으면 기본값으로 동작합니다. 데몬은 요청마다 정책 파일이 바뀌었는지 보고 다시 읽으므로 재시작할 필요가 없습니다.

```bash
gate policy show                                   # 내가 정한 항목과 지금 적용 중인 정책
gate policy add "주민번호는 생년월일까지만 보여줘"      # 문장 → "이렇게 이해했어요" → y/N
gate policy add "비밀번호가 있는 파일은 거부해줘"
gate policy add "병원 기록은 보여주지 마"               # 맥락 규칙 (Jev 질문에 그대로 넣음)
gate policy set numbers.email.action deny          # 문장 해석 없이 바로 지정
gate policy set path "가계부/**" deny               # 경로 규칙 (allow | ask_human | deny)
gate policy remove 2                               # show의 번호로 지우기
gate policy undo                                   # 직전 변경 되돌리기 (최근 20개)
```

- 더 엄격하게 바꾸면 바로 저장하고, 완화하면 한 번 더 묻습니다. 맥락 규칙은 더 막는 쪽만 받습니다.
- 번호·비밀값 기본값은 **전부 가림**입니다. 주민번호는 `numbers.rrn.visible`을 `birth`(앞 6자리)나 `birth_gender`(앞 7자리)로, 휴대폰은 앞자리(`prefix`), 카드는 끝 4자리(`last4`) 등을 공개할 수 있습니다.
- 사람 승인 대기 시간(`approval.timeout_seconds`, 기본 30초)과 폴더 승인 기억 시간(`approval.session_minutes`, 기본 60분)도 바꿀 수 있습니다.
- 기본값 전체와 설명: [`config/policy.yaml`](config/policy.yaml)
- 문장 해석기도 지금은 Mock입니다. 예시와 다른 표현은 못 알아들을 수 있습니다. 그때는 `gate policy set`을 씁니다.

## 사람 승인

Jev 확신도가 애매하거나, 내가 경로·번호 종류에 `ask_human`을 정한 파일이면 사람에게 묻습니다.

- **맥 대화상자**: 거부 · 이번만 허용 · 이 폴더 허용. `gate serve --no-dialog`로 끕니다.
- **터미널**: `gate approve`(목록), `gate approve <id> [--folder]`, `gate deny <id>`. 답하는 명령은 실제 터미널에서만 받습니다.
- 먼저 온 답을 씁니다. 30초 안에 답이 없으면 차단합니다. 그동안 요청한 `gate cat`은 기다립니다.
- 허용해도 번호·비밀값은 가린 채로 보여 줍니다. 원본 그대로 보여 주는 길은 경로 규칙 `allow`뿐입니다.
- 대화상자의 "에이전트가 밝힌 목적"은 에이전트가 쓴 글이라 "확인되지 않음"으로 표시합니다.
- 승인 소켓(`approve.sock`)은 게이트 소켓과 따로 있어서, 샌드박스 안의 에이전트는 스스로 승인할 수 없습니다.

## 감사 로그

`gate serve --audit 파일` 또는 `GATE_AUDIT`를 주면 요청마다 JSONL 한 줄을 남깁니다.

```console
$ gate audit                       # 최근 20건 (-n 0이면 전부)
$ gate audit --blocked             # 막힌 요청만 (차단, 승인 필요, 원본 밖 경로 같은 EACCES)
$ gate audit --decision mask       # 결정으로 거르기: allow mask deny ask_human ok error
$ gate audit --path 상담 --since 1h  # 경로에 '상담'이 든 최근 1시간 기록 (30m, 2h, 1d, 2026-10-31T17:00)
$ gate audit --file ~/문서/계약서.pdf  # 이 파일과 내용이 같은 파일을 읽은 기록
$ gate audit --json                # 고른 기록을 JSONL 그대로 (스크립트용)
```

- **남기는 것**: 시각, 요청자 PID·UID·프로그램, 연산, 게이트 안 경로, 오류, 결정과 이유, 결정 단계, 정책 버전, 파일 해시, 종류별 탐지 건수, Jev 라벨·확신도·호출 수, 캐시 여부, 사람 승인 결과와 답한 곳, 걸린 시간. 키 목록과 규칙은 [`gate/audit.py`](gate/audit.py) 맨 위 설명에 있습니다.
- **남기지 않는 것**: 원문, 탐지한 값, 에이전트가 밝힌 목적.
- **파일 해시**는 SHA-256이 아니라 `HMAC-SHA256(audit.key, SHA-256(내용))`입니다. "연락처 010-xxxx-xxxx"처럼 짧고 틀이 정해진 파일은 SHA-256만 보고 후보를 넣어 원문을 맞혀 볼 수 있기 때문입니다. 키는 설치마다 만드는 32바이트 난수입니다. `gate audit --file`은 이 키로 파일을 맞춰 봅니다.
- 파일은 0600, 폴더는 0700이고 심볼릭 링크로 바꿔치기한 경로에는 쓰지 않습니다. 돌려 쓰기(rotation)는 없습니다. `gate audit`은 읽기만 하고, 깨진 줄은 건너뛰고 줄 번호를 알려 줍니다.

## 우회 시험 하네스

[`sandbox/bypass.py`](sandbox/bypass.py)는 에이전트가 원본을 게이트 없이 읽으려는 시도를 실제 `claude -p`(헤드리스, 모델 Haiku)로 하나씩 돌려 보고 판정합니다.

- **시도**: 내장 `Read`·`LSP`, `cat`, `grep -r`, Python `open()`, 심볼릭 링크·하드링크·`../`·대소문자 바꾼 경로, `cp`·`tar`, Spotlight, 두 번째 데몬 띄우기, 정책 파일·정책 기록·감사 로그 쓰기, 승인 소켓 연결·`gate approve`·`osascript`로 스스로 승인하기, 설정 파일 수정, bypass 모드 등.
- **판정**: 실행마다 원본 폴더에 무작위 표식 파일을 두고, 결과에 표식이 나오면 뚫린 것으로 봅니다. 모델이 명령을 거절하거나 다른 명령을 쓰면 "막힘"이 아니라 "판정 불가"로 셉니다. 게이트로는 읽힌다는 대조군 2개가 먼저 돕니다.
- **결과(2026-10-31, Claude Code 2.1.316)**: 작업 폴더 설정 40가지, 전역 설정(`gate setup` 조각) 29가지 모두 **뚫린 것 0**. 어떤 층이 막았는지는 계획 문서 5절 "6-3 결과"에 있습니다.

실행하려면 데몬이 떠 있고 Claude Code에 로그인되어 있어야 합니다. **API 비용이 듭니다**(Phase 6에서 7번 돌려 약 $0.57).

```bash
uv run python -m sandbox.bypass --out 보고서.md                                        # 작업 폴더 설정 (default 모드)
uv run python -m sandbox.bypass --mode auto --only W2,W8,W9,W10,W11 --out 보고서.md     # 쓰기 도구 (auto 모드)
gate setup --source $DATA/source --settings-file 시험.json --yes                       # 전역 설정 시험 파일
uv run python -m sandbox.bypass --settings 시험.json --workspace $DATA/plain-project --only … --out 보고서.md
```

전역 설정 시험의 `--workspace`는 프로젝트 설정(`.claude/`)이 없는 빈 폴더여야 합니다. 시험 범위(뺀 시도)는 계획 문서 5절 "6-3 결과"를 봅니다.

종료 코드는 0(모두 막힘), 1(뚫림 또는 대조군 실패), 2(판정 불가 있음), 3(시작 전 확인 실패)입니다. 두 개를 동시에 돌리지 않습니다.

## 테스트

```bash
uv run pytest                     # 맥: 909 통과, 1 skip(FUSE), 약 20초 (사람 승인 시간 초과를 실제로 기다림)

docker build -f sandbox/gate.Dockerfile -t fuse-jev-gate .
docker run --rm --device /dev/fuse --cap-add SYS_ADMIN fuse-jev-gate pytest -q    # Linux (FUSE 포함)

uv run python -m eval.run_eval --client mock [--policy 정책파일] [--details]        # 샘플 36개 평가
```

평가 방법과 지표는 [`eval/README.md`](eval/README.md)에 있습니다. Mock 점수는 실제 성능이 아닙니다.

## 알려진 한계

- **Jev는 Mock입니다.** 키워드 규칙이라 실제 판단 성능을 보여 주지 않습니다. 가린 값(`***`)도 단서로 세서, 번호가 든 문서는 대부분 승인이나 차단으로 끝납니다. 맥락 규칙("병원 기록은 보여주지 마")도 아직 효과가 없습니다. 실제 로컬 백엔드는 `gate/jev_client.py`에 구현 하나를 추가해 바꿔 끼웁니다.
- **Claude Code 세션만 막습니다.** 다른 에이전트나 프로그램은 원본을 그대로 읽습니다. 모두 막으려면 원본을 별도 계정 소유로 두거나 암호화하는 OS 수준 분리가 필요합니다(다음 확장 후보).
- **샌드박스 우회는 다루지 않습니다.** 설정 파일 바꾸기, bypass 모드, 샌드박스 밖에서 도는 훅은 MDM 관리 설정으로 막는다고 가정합니다. 관리 설정 없이 사용자 설정만 쓰면 프로젝트 설정이 샌드박스를 풀 수 있습니다.
- MCP 도구와 세션 간 메시지 도구는 막지 않습니다. MCP 서버는 샌드박스 밖에서 돌아서, 화면 제어나 브라우저 자동화 같은 로컬 MCP가 있으면 원본을 열 수 있습니다.
- Linux에서는 승인 소켓만 따로 막을 수 없습니다(`allowUnixSockets`는 macOS 전용).
- 정규식은 형식이 정해진 값만 찾습니다. 한글로 쓴 숫자, 키워드 없는 계좌번호, EUC-KR·UTF-16 파일, YAML 블록 값이나 목록에 없는 형식의 비밀값은 놓칩니다.
- 사람이 미리 만들어 둔 하드링크나 디스크 다른 곳의 사본은 지키지 못합니다(에이전트가 하드링크를 만드는 것은 막힘).
- 원본 폴더가 있는지(`stat`)는 샌드박스 안에서도 보입니다. 목록과 내용은 막힙니다.
- WebFetch의 `file://`은 우리 설정이 아니라 Claude Code의 사람 확인이나 auto 분류기가 막습니다.
- 폴더 승인은 데몬 메모리에만 있고, 요청 프로그램 단위라 에이전트 세션을 구분하지 않습니다.

전체 목록과 근거는 [`plans/implementation-plan.md`](plans/implementation-plan.md) 6절에 있습니다.

## 폴더 구조와 문서

```
gate/
  daemon.py, client.py, protocol.py, peer.py   소켓 서버·클라이언트, 요청자 확인
  store.py, paths.py                           원본 저장소, 게이트 안 경로 → 실제 경로(탈출 차단)
  pipeline.py, cache.py                        판단 순서, search, 캐시
  detectors.py                                 정규식 탐지와 같은 길이 가림
  jev_client.py                                Jev 인터페이스(답 + 확신도)와 Mock
  policy.py, policy_text.py, policy_cli.py     정책 기본값·파일, 문장 해석기, gate policy
  approval.py                                  사람 승인(대화상자, 승인 소켓)
  audit.py, audit_cli.py                       감사 로그, gate audit
  workspace.py, claude_setup.py                gate workspace init, gate setup
  cli.py                                       gate 명령
config/policy.yaml     기본 정책 설명
samples/  eval/        합성 한국어 샘플 36개와 평가 스크립트
sandbox/               우회 시험 하네스, 작업 폴더 설정 예시, Linux 테스트용 Dockerfile
tests/                 pytest
plans/                 구현 계획과 Phase별 진행 기록
```

- [`plans/implementation-plan.md`](plans/implementation-plan.md): 확정한 결정(2절), Phase별 결과(5절), 알려진 한계(6절). 명세와 다른 부분은 이 문서를 따릅니다.
- [`fuse-jev-gate-spec.md`](fuse-jev-gate-spec.md): 처음 명세(FUSE 구조)
- [`HANDOFF.md`](HANDOFF.md): 작업 세션 사이의 인수인계 메모
