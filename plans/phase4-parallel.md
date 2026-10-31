# Phase 4 병렬 작업 지시서

> 2026-10-31. Phase 4를 4-1(공통, 메인) → 4-2(메인 ∥ 서브에이전트 B) → 4-3(통합·검증, 메인)으로 나눈다.
> MCP 서버는 사용자 결정으로 뺐다(2026-10-31). 에이전트는 Bash로 `gate` CLI만 쓴다. 그래서 서브에이전트는 B 하나다.
> 이 폴더는 git 저장소가 아니라서 worktree로 분리할 수 없다. 대신 손대는 파일을 아래처럼 나눈다.

> **결과(2026-10-31): 4-1·4-2·4-3 모두 완료.** 맥 615 통과·1 skip, Linux 616 통과. B는 지정된 파일 4개만 만들었고 다른 파일은 고치지 않았다(테스트 159개). 결과와 결정은 `plans/implementation-plan.md` 5절 Phase 4에 있다. B의 제안 두 가지(설정 이름 한국어 표시, 맥락 규칙은 더 막는 쪽만 받는다는 원칙)는 메인이 `gate/policy.py`에 반영했다.
> 이어서 사용자 요청으로 비밀값 보완을 메인이 혼자 했다(서브에이전트 없음). 이 보완을 마친 뒤 결과는 맥 690 통과·1 skip, Linux 691 통과다.

## 사용자 확인 사항 (2026-10-31)

| 항목 | 결정 |
|---|---|
| 정책 파일 위치 | `hackathon/gate-data/policy.yaml`. `--policy` 또는 `GATE_POLICY`로 지정한다. 에이전트 작업 폴더 밖이다 |
| `gate policy` 명령 | `show`, `add "문장"`, `remove <번호>`, `undo`, `set <키> <값>` |
| 데몬 반영 | 요청마다 파일 변경을 감지해 다시 읽는다(`gate.policy.PolicySource`). 재시작이 필요 없다 |
| 번호 기본 공개 범위 | 모든 종류를 기본으로 전부 가린다. 종류별로 공개를 켤 수 있다(`numbers.<종류>.visible`) |
| MCP 서버 | 만들지 않는다 |
| 서브에이전트 모델 | Opus 5.5 |

## 4-1 (완료, 메인): 공통 약속

- `gate/detectors.py`: `mask(text, *, visible=None)`. 종류별 공개 범위는 `VISIBLE_CHOICES`, 기본은 전부 가림이다. `rrn_visible`은 없앴다.
- `gate/jev_client.py`: `RequestContext.user_policy`(맥락 규칙)를 추가했다. `build_question`에 "[사용자 정책]" 칸이 생겼다.
- `gate/policy.py`: 기본값, 정책 파일 읽기·쓰기, `PolicySource`, 판단 함수(`path_action`, `number_action`, `jev_action`, `strictest`), 완화 확인(`loosened`). 형식과 규칙은 모듈 docstring에 있다.
- `config/policy.yaml`: 기본값 설명 파일이다(게이트는 읽지 않는다). 코드와 같은지 테스트가 확인한다.
- 의존성: PyYAML.

## 파일 분담 (4-2)

| | 메인 | 서브에이전트 B |
|---|---|---|
| 만들거나 고치는 파일 | `gate/cache.py`, `gate/pipeline.py`, `gate/daemon.py`, `gate/protocol.py`, `gate/client.py`, `gate/store.py`, `tests/test_cache.py`, `tests/test_pipeline.py`, `tests/test_daemon.py` | `gate/policy_text.py`, `gate/policy_cli.py`, `tests/test_policy_text.py`, `tests/test_policy_cli.py` |
| 읽기만 | | `gate/policy.py`, `gate/detectors.py`, `gate/jev_client.py` |
| 공통 금지 | B의 파일 | 위 표에 없는 파일, 특히 `gate/policy.py`, `gate/cli.py`, `pyproject.toml`, `uv.lock`, `HANDOFF.md`, `plans/`, `config/`. git init·커밋 |

- `gate/cli.py` 연결(`gate policy`, `gate serve --policy`, `gate search`)은 4-3에서 메인이 한다.
- B가 `gate/policy.py`에 없는 기능이 필요하면 자기 파일 안에서 구현한다. 그래도 policy.py를 고쳐야 하면 고치지 말고 보고한다.

---

## 지시서 B: `gate policy` 명령 + 정책 문장 해석기 (Mock)

### 맥락

- 경로: `/Users/sangwoo/work/personal/hackathon/fuse-jev-gate`. Python 3.12, uv. 테스트는 `uv run pytest`(4-1 기준 387 통과, 1 skip).
- 일반인이 자기 컴퓨터에서 AI 에이전트(Claude Code)를 쓸 때, 에이전트가 원본 문서를 게이트 데몬을 거쳐서만 읽게 하는 도구다. 게이트는 경로 규칙 → 정규식 마스킹 → Jev(판단 전용 모델, 답 + 확신도) → 정책 순서로 판단한다. Jev는 아직 Mock이다.
- 정책 원칙(사용자 결정): **사용자가 지정하면 그 값, 지정하지 않으면 기본값.** 일반 사용자는 YAML을 고치지 않는다. 문장(또는 `set`)으로 지정한다.
- 정책은 **사람(사용자)만 자기 터미널에서** 바꾼다. 에이전트가 정책을 바꿀 수 있으면 게이트를 우회하게 된다. 그래서 다음을 지킨다.
  - 소켓(데몬)에는 정책 변경 요청이 없다. `gate policy`는 정책 파일을 직접 쓴다.
  - 진짜 경계는 파일 권한이다. 정책 파일은 에이전트 작업 폴더 밖에 있고, Phase 6에서 샌드박스로 쓰기를 막는다.
- 먼저 읽을 것:
  - `gate/policy.py`의 모듈 docstring과 공개 API
  - `plans/implementation-plan.md` 5절 Phase 4의 "사용자 정책"
  - `gate/jev_client.py`: Jev 방식(답 + 확신도)과 Mock 패턴을 그대로 따른다
- 코드 스타일은 `gate/policy.py`, `gate/jev_client.py`를 따른다. 한국어 docstring, 주석은 적게, 타입 힌트, dataclass. 보고와 주석은 한국어로 쓴다.

### 쓸 수 있는 API (`gate/policy.py`, 고치지 않는다)

```python
Entry(settings=None, context=None, path_rule=None, said=None, at=None)  # 셋 중 하나만. 파일 형식은 docstring
PathRule(pattern, action)                     # action: allow | ask_human | deny
build_policy(entries) -> Policy               # 기본값 + 항목 차례로 적용
read_entries(path) -> tuple[Entry, ...]       # 파일 없으면 ()
write_entries(path, entries) -> Policy        # 검사 후 원자적 쓰기(0600)
loosened(before: Policy, after: Policy) -> list[str]   # 느슨해진 점 설명. 비면 같거나 엄격
describe_setting(key, value) -> str           # "주민등록번호: 생년월일(앞 6자리)까지 공개"
setting_keys(), setting_choices(key), validate_setting(key, value), default_settings()
KIND_NAMES, ACTION_NAMES, VISIBLE_NAMES, DEFAULT_POLICY, DEFAULT_PATH_RULES, PolicyError
Policy.settings / .path_rules / .context_rules / .entries / .version
```

### 1. `gate/policy_text.py`: 문장 해석기

- **Jev 방식이다.** 해석기는 글을 만들지 않고 **정해진 선택지 중 하나와 확신도**만 돌려준다.
  ```python
  @dataclass(frozen=True)
  class Choice:
      answer: str        # options 중 하나
      confidence: float  # 0.0 ~ 1.0

  class PolicyInterpreter(Protocol):
      def choose(self, sentence: str, question: str, options: Sequence[str]) -> Choice: ...
  ```
- `interpret(sentence, interpreter) -> Interpretation`이 질문을 차례로 던져 문장을 해석한다. 질문 구성은 정해서 docstring에 적는다. 예:
  1. 문장 종류: 번호 설정 / 경로 규칙 / 맥락 규칙 / 알 수 없음
  2. 번호 설정이면 어떤 번호인지(`KINDS`), 무엇을 바꾸는지(동작 `mask|ask_human|deny` 또는 그 종류의 공개 범위)
- 결과(`Interpretation`)는 다음 중 하나다.
  - **번호 설정:** `Entry(settings=..., said=문장)`
  - **맥락 규칙:** `Entry(context=문장, said=문장)`. 맥락 문장은 해석하지 않고 그대로 Jev 질문에 들어간다.
  - **경로 규칙:** 경로 패턴은 선택지로 고를 수 없다. 이때는 `gate policy set path <패턴> <동작>`을 안내한다(저장하지 않음).
  - **알 수 없음 / 확신도 낮음:** 임계값(예: 0.7) 미만인 답이 하나라도 있으면 다시 말해 달라고 한다. 추측해서 저장하지 않는다.
- 사용자에게 보여 줄 "이렇게 이해했어요" 문장도 만든다(`describe_setting`을 쓴다).
- `MockPolicyInterpreter`: 결정적인 키워드 규칙이다. docstring에 **"Jev의 실제 성능을 대변하지 않는다"**고 적는다.
  - 테스트용 `fixed` 방식(질문별 답 지정)도 둔다. `calls`에 받은 질문을 남긴다. `error`와 `delay`로 실패 경로를 흉내 낼 수 있게 한다.
  - 다음 문장은 맞게 해석해야 한다.
    - "주민번호는 생년월일까지만 보여줘" → rrn visible birth
    - "카드번호 있는 파일은 거부" → card action deny
    - "이메일은 물어보고 보여줘" → email action ask_human
    - "휴대폰 번호는 앞자리만 보여줘" → mobile visible prefix
    - "병원 기록은 보여주지 마" → 맥락 규칙
    - "공개 폴더는 그냥 보여줘" → 경로 규칙 안내
    - "아무거나" → 알 수 없음
- `make_interpreter(name="mock", **options)`: `jev_client.make_client`처럼 이름으로 고른다. 실제 백엔드는 Phase 3b에서 붙인다. 해석기 오류(예외, 타임아웃, 선택지 밖의 답)는 저장하지 않는 쪽으로 처리한다(fail-closed와 같은 뜻).

### 2. `gate/policy_cli.py`: `gate policy` 하위 명령

- `register(subparsers)`로 `policy` 하위 명령을 붙이고, `run(args, *, stdin=None, stdout=None, interpreter=None, require_tty=True) -> int`로 실행한다. `gate/cli.py` 연결은 메인이 4-3에서 한다.
- 정책 파일 경로는 `--policy` 또는 `GATE_POLICY`에서 받는다. 둘 다 없으면 안내 후 exit 1.
- 명령:

  | 명령 | 동작 |
  |---|---|
  | `show` | 사용자 항목을 번호와 함께 보여 준다(문장, 해석). 현재 적용 중인 정책은 번호 종류별 처리, 임계값, 경로 규칙, 맥락 규칙으로 나눠 보여 준다. 기본값과 지정값을 구분한다. 일반 사용자가 읽을 수 있는 한국어로 쓴다 |
  | `add "문장"` | 해석 → "이렇게 이해했어요" → y/N 확인 → 저장 |
  | `remove <번호>` | `show`의 번호로 항목을 지운다 |
  | `undo` | 직전 변경을 되돌린다. 여러 번 되돌릴 수 있게 변경 전 파일을 정책 파일 옆 기록 폴더(예: `policy.yaml` → `policy-history/`, 폴더 0700, 파일 0600)에 쌓는다. 개수 제한을 둔다 |
  | `set <키> <값>` | 해석 없이 바로 지정한다(Mock 해석기가 못 알아들을 때, 테스트용). 예: `set numbers.rrn.visible birth`, `set thresholds.allow_if_safe_confidence_gte 0.97`. 경로 규칙은 `set path <패턴> <allow\|ask_human\|deny>`. 값은 `validate_setting`으로 검사하고, 숫자는 문자열에서 바꾼다 |

- 확인 규칙(계획 문서 5절):
  - **완화하는 변경은 한 번 더 확인한다.** 바꾸기 전과 후의 정책을 `loosened(before, after)`로 비교해서, 비어 있지 않으면 무엇이 느슨해지는지 보여 주고 한 번 더 y/N을 묻는다. `add`, `remove`, `undo`, `set`에 모두 적용한다.
  - `add`는 엄격해지는 변경이라도 "이렇게 이해했어요" 확인을 한 번 받는다(해석이 틀릴 수 있어서).
  - `set`과 `remove`는 엄격해지는 변경이면 확인 없이 바로 저장한다.
  - 기본 답은 N이다. 입력이 끝나면(EOF) N으로 본다.
- `require_tty=True`이면 정책을 바꾸는 명령(`add`, `remove`, `undo`, `set`)은 stdin이 터미널일 때만 받는다. 에이전트의 Bash 실행처럼 터미널이 없는 곳에서 `y`를 흘려 넣는 것을 한 겹 더 막는다. **보안 경계는 아니다**(진짜 경계는 파일 쓰기 차단, Phase 6). docstring에 그렇게 적는다. `show`는 터미널이 없어도 된다.
- 저장은 `write_entries`만 쓴다. 저장하면 실행 중인 데몬이 다음 요청에서 자동으로 반영한다는 것도 안내한다.
- 출력 끝에 현재 정책 버전(`Policy.version`)을 짧게 보여 주면 데몬 로그와 맞춰 보기 쉽다(선택).

### 3. 테스트

- `tests/test_policy_text.py`: 위 예시 문장, 확신도가 낮을 때 다시 묻기, 해석기 오류 시 저장하지 않음, Mock `fixed` 방식과 `calls`.
- `tests/test_policy_cli.py`: `tmp_path`의 정책 파일로 각 명령을 시험한다. stdin·stdout은 `io.StringIO`로 넣는다.
  - `show`: 기본값과 지정값을 구분해서 보여 준다.
  - `add`: 승인, 거부, EOF 각각.
  - 완화 변경: 두 번째 확인이 나오고, 거부하면 파일이 바뀌지 않는다.
  - 엄격 변경: `set`은 확인 없이 바로 저장된다.
  - `remove`: 없는 번호를 주면 exit 1.
  - `undo`: 여러 번 되돌리기, 기록이 없을 때.
  - 터미널이 아니면 변경 명령을 거부한다(`require_tty=True`).
  - 정책 경로가 없으면 exit 1.
  - 잘못된 정책 파일이면 PolicyError를 사람이 읽을 수 있게 알리고 exit 1.
  - 파일 권한이 0600이다.
- 테스트 데이터는 전부 가짜 값이다. 실존 인물 정보는 쓰지 않는다.

### 끝나면

- `uv run pytest`로 전체를 돌린다. 메인이 동시에 고치는 파일(`daemon.py` 등) 때문에 실패하면 고치지 말고 보고만 한다.
- 보고(한국어)에 담을 것:
  - 만든 파일과 공개 API
  - 해석 질문 구성
  - 확인 규칙 표
  - `undo` 기록 방식
  - 테스트 수와 결과
  - 결정한 것과 그 이유
  - 확인하지 못한 것(거짓 안전: Mock 해석이 실제 Jev와 다를 수 있는 점 등)
  - policy.py에 바라는 변경(있으면)

---

## 메인 (4-2): 캐시 + 판단 파이프라인 + 데몬 연결 + search

- `gate/pipeline.py`의 `Inspector.inspect(peer, vpath, purpose) -> Verdict`:
  1. 정책 가져오기: `PolicySource.current()`. `PolicyError`면 차단.
  2. 경로 규칙: 요청 경로와 실제 경로(심볼릭 링크를 따라간 것) 둘 다로 판단한다.
  3. 파일 전체 읽기 → `mask_bytes(visible=policy.visible)` → `number_action`.
  4. 캐시 조회. 키는 `sha256(내용) + policy.version + 요청 맥락 요약(요청 프로세스, purpose)`.
  5. Jev 호출. `max_inspect_bytes`를 넘으면 나눠 묻고 가장 엄격한 답을 쓴다. `JevError`면 deny.
  6. `strictest`로 합친다.
- `ask_human`은 Phase 5 전까지 차단(`EACCES`, "사람 승인 필요")이다.
- `cat`은 판단 결과 버퍼(원본 또는 마스킹본)에서 offset·size만큼 잘라 준다.
- `search` 연산: 허용·마스킹된 내용만 대상으로 한다. 패턴은 글자 그대로(정규식 아님, ReDoS 방지). 차단된 파일은 `skipped`에 경로와 이유만 넣는다.
- 완료 조건 테스트:
  - 다시 읽으면 Jev를 호출하지 않는다(`MockJevClient.calls`).
  - 정책을 바꾸면 캐시가 무효가 된다.
  - 정책 파일이 잘못되면 차단한다.
