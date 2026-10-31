# Phase 5 병렬 작업 지시서

> 2026-10-31. Phase 5(사람 승인)를 5-1(공통, 메인) → 5-2(메인 ∥ 서브에이전트 B) → 5-3(통합·검증, 메인)으로 나눈다.
> 감사 로그(원래 Phase 7 일부)를 사용자 결정으로 B가 병렬로 만든다. 연결(`gate serve --audit`)은 5-3에서 메인이 한다.
> 이 폴더는 git 저장소가 아니라서 worktree로 분리할 수 없다. 대신 손대는 파일을 아래처럼 나눈다.

> **결과(2026-10-31): 5-1·5-2·5-3 모두 완료.** 맥 790 통과·1 skip, Linux 791 통과. B는 지정된 파일 2개만 만들었다(테스트 36개).
> 메인이 B 코드를 읽고 고친 것은 한 가지다. 보이지 않는 글자(U+2028·U+2029)가 소스에 그대로 들어 있어 `\u` 표기로 바꿨다.
> B의 제안 중 "파일 해시로 원문을 맞혀 볼 수 있음"은 사용자 결정으로 HMAC(설치 키 `audit.key`)으로 바꿨다(메인). "FIFO 로그 경로"는 알려진 한계로 적었다.
> 실제 맥 대화상자 버튼은 사용자가 직접 눌러 확인했다(이번만 허용·거부·이 폴더 허용, 폴더 승인 기억).
> 결과와 거짓 안전은 `plans/implementation-plan.md` 5절 Phase 5에 있다.

## 사용자 확인 사항 (2026-10-31)

| 항목 | 결정 |
|---|---|
| 승인 방법 | 둘 다. 맥 대화상자(`osascript display dialog`, 버튼: 거부·이번만 허용·이 폴더 허용, 기본 버튼은 거부)와 사용자 터미널의 `gate approve <id>`·`gate deny <id>`. 먼저 온 답을 쓴다. `gate approve`는 별도 승인 소켓(`approve.sock`)을 쓴다 |
| 승인 대기 시간 | 기본 30초(`approval.timeout_seconds`). 넘으면 차단 |
| 세션 단위 승인 | 데몬 실행 중 + 최대 1시간(`approval.session_minutes`). 단위는 (폴더, 요청 프로그램, 정책 버전) |
| 감사 로그 | Phase 5와 병렬로(서브에이전트 B) |
| 서브에이전트 모델 | Opus 5.5 (Phase 4와 같음) |

기본으로 정한 것(계획 문서 2절): 승인해도 번호는 가린다. `search`는 묻지 않고 건너뛴다(이미 받은 폴더 승인은 씀).

## 5-1 (메인): 공통 약속

- `gate/approval.py`: `Approvals`(대기 요청, 먼저 온 답, 시간 초과, 같은 요청 합치기), `SessionApprovals`, `DialogChannel`(osascript), `ApprovalRequest`·`ApprovalResult`·`Pending`. 규칙은 모듈 docstring에 있다.
- `gate/pipeline.py`
  - `Judgment`에 `approval`(`once`·`session`·`remembered`·`deny`·`timeout`)과 `approved_by`(`dialog`·`terminal`)를 추가했다. 결정이 사람 승인에서 났으면 `stage`는 `human`이다.
  - `Inspector(approvals=...)`, `inspect(..., ask=True)`. 경로 `ask_human`도 검사를 먼저 하고 묻는다.
- `gate/policy.py`: 설정 `approval.timeout_seconds`(기본 30.0), `approval.session_minutes`(기본 60). `config/policy.yaml`도 같이 고쳤다.

## 파일 분담 (5-2)

| | 메인 | 서브에이전트 B |
|---|---|---|
| 만들거나 고치는 파일 | `gate/approval.py`, `gate/pipeline.py`, `gate/daemon.py`, `gate/protocol.py`, `gate/client.py`, `gate/cli.py`, `gate/policy.py`, `gate/policy_cli.py`, `config/policy.yaml`, `tests/test_approval.py`, `tests/test_pipeline.py`, `tests/test_daemon.py`, `tests/test_policy.py`, `tests/test_policy_cli.py` | `gate/audit.py`, `tests/test_audit.py` |
| 읽기만 | | `gate/daemon.py`(`RequestEvent`), `gate/pipeline.py`(`Judgment`), `gate/peer.py`(`Peer`), `fuse-jev-gate-spec.md` 3.8, `plans/implementation-plan.md` 5절 Phase 7 |
| 공통 금지 | B의 파일 | 위 표에 없는 파일, 특히 `gate/cli.py`, `gate/daemon.py`, `pyproject.toml`, `uv.lock`, `HANDOFF.md`, `plans/`, `config/`. git init·커밋. 새 의존성 추가 |

- `gate serve --audit` 연결은 5-3에서 메인이 한다.
- B가 다른 파일을 고쳐야 할 것 같으면 고치지 말고 보고한다.

---

## 지시서 B: 감사 로그 (`gate/audit.py`)

### 맥락

- 경로: `/Users/sangwoo/work/personal/hackathon/fuse-jev-gate`. Python 3.12, uv. 테스트는 `uv run pytest`(5-1 기준 691 통과, 1 skip).
- 일반인이 자기 컴퓨터에서 AI 에이전트(Claude Code)를 쓸 때, 에이전트가 원본 문서를 게이트 데몬을 거쳐서만 읽게 하는 도구다. 에이전트는 `gate` CLI로 Unix 소켓에 요청하고, 데몬은 경로 규칙 → 정규식 마스킹 → Jev(Mock) → 정책 → 사람 승인을 거쳐 허용·가림·차단으로 답한다.
- 데몬은 요청 하나를 끝낼 때마다 `RequestEvent`(`gate/daemon.py`)를 `on_request` 콜백으로 넘긴다. 지금은 `gate/cli.py`의 `_log`가 stderr에 한 줄만 쓴다. B는 이 이벤트를 **JSONL 파일에 한 줄씩** 남기는 모듈을 만든다.
- 명세 3.8: 모든 판단을 JSONL로 기록한다. 시각, PID·프로세스 이름, 경로, 파일 해시, 정규식 탐지 건수, Jev 라벨·확신도, 최종 결정, 소요 시간. **파일 원문이나 탐지된 개인정보 값은 기록하지 않는다.**
- 먼저 읽을 것: `gate/daemon.py`의 `RequestEvent`, `gate/pipeline.py`의 `Judgment`(필드 설명 주석), `gate/peer.py`의 `Peer`, `gate/approval.py` 모듈 docstring(승인 결과의 뜻).
- 코드 스타일은 `gate/cache.py`, `gate/pipeline.py`를 따른다. 한국어 docstring, 주석은 적게, 타입 힌트, dataclass. 보고와 주석은 한국어로 쓴다.

### 입력 (고치지 않는다)

```python
# gate/daemon.py
@dataclass(frozen=True)
class RequestEvent:
    peer: Peer                 # pid, uid, exe(실행 파일 경로 또는 None)
    op: str | None             # ls | stat | cat | search | 잘못된 요청이면 None 또는 아무 문자열
    path: str | None           # 요청한 가상 경로
    errno: str | None          # 성공이면 None, 실패면 "EACCES" 같은 이름
    elapsed_ms: float
    judgment: Judgment | None  # cat의 판단 요약(성공·차단 모두). ls·stat·search·형식 오류는 None

# gate/pipeline.py — Judgment 필드
action          allow | mask | ask_human | deny
reason          사람이 읽는 이유(원문·탐지 값 없음)
stage           policy | path | number | jev | empty | human
policy_version  str | None
content_sha256  str | None
findings        Mapping[str, int]  # 종류별 탐지 건수 (rrn, card, secret ...)
jev_label       str | None
jev_confidence  float | None
jev_calls       int
cached          bool
approval        once | session | remembered | deny | timeout | None
approved_by     dialog | terminal | None
```

### 만들 것

`gate/audit.py`

```python
AUDIT_SCHEMA_VERSION = 1

def to_record(event: RequestEvent, now: datetime) -> dict:
    """이벤트 하나를 감사 기록(dict)으로 바꾼다. 순수 함수."""

class AuditLog:
    def __init__(self, path: str | os.PathLike) -> None: ...
    def record(self, event: RequestEvent, *, now: datetime | None = None) -> None: ...
    def close(self) -> None: ...
    # 컨텍스트 매니저(with)로도 쓸 수 있게 한다

def read_records(path: str | os.PathLike) -> list[dict]:
    """테스트와 나중의 조회 명령용. 깨진 줄은 건너뛰지 말고 ValueError로 알린다."""
```

기록 한 줄의 키(이 이름과 순서를 지킨다. 값이 없으면 null):

| 키 | 값 |
|---|---|
| `v` | `AUDIT_SCHEMA_VERSION` |
| `ts` | 시각. 시간대가 붙은 ISO 8601, 밀리초까지(`now`가 없으면 현재 시각, 로컬 시간대) |
| `pid`, `uid`, `exe` | `event.peer` 값 |
| `process` | `exe`의 파일 이름(basename). exe가 없으면 null |
| `op`, `path`, `errno` | 이벤트 값. `op`·`path`가 문자열이 아니면(형식 오류 요청) null로 쓴다 |
| `decision` | 판단이 있으면 `judgment.action`, 없으면 성공 `ok`, 실패 `error` |
| `reason`, `stage`, `policy_version`, `content_sha256` | 판단 값 |
| `findings` | 종류별 건수 dict(판단이 없으면 null) |
| `jev_label`, `jev_confidence`, `jev_calls`, `cached` | 판단 값 |
| `approval`, `approved_by` | 판단 값 |
| `elapsed_ms` | 소수 첫째 자리까지 반올림 |

지킬 것:
- **원문과 탐지 값은 절대 쓰지 않는다.** 위 표에 없는 것은 쓰지 않는다. 요청의 `purpose`(에이전트가 밝힌 목적)도 이벤트에 없고 기록하지 않는다.
- JSON은 `ensure_ascii=False`, 한 줄에 하나, 끝에 `\n`. 경로에 줄바꿈이 있어도 JSON 이스케이프로 한 줄이 유지되어야 한다. `path`는 `surrogateescape`로 푼 문자열일 수 있다(UTF-8이 아닌 파일 이름). 이런 경우에도 기록이 실패하지 않게 한다(예: `\udcxx`를 그대로 인코딩하지 못하면 `backslashreplace` 등으로 바꿔 쓴다. 방법은 B가 정하고 docstring에 적는다).
- 데몬의 여러 스레드가 동시에 부른다. 줄이 섞이면 안 된다(잠금 + 한 번의 write).
- 파일 권한: 부모 폴더가 없으면 0700으로 만들고, 파일은 0600으로 만든다. 열 때 `O_APPEND | O_CREAT | O_WRONLY | O_NOFOLLOW`를 쓴다(로그 경로에 심볼릭 링크를 걸어 다른 파일에 쓰게 하는 것을 막는다). 이미 있는 파일은 권한을 바꾸지 않는다.
- 매 줄 flush한다(데몬이 죽어도 남게). fsync는 하지 않는다.
- `record`는 쓰기 실패(`OSError`)를 그대로 던진다. 데몬 쪽에서 어떻게 할지는 메인이 정한다.
- `close` 뒤 `record`는 `ValueError`.

### 테스트 (`tests/test_audit.py`)

최소한 다음을 확인한다. 값은 모두 가짜 예시다.
- cat 허용·가림·차단(`deny`)·승인(`approval="session"`, `approved_by="dialog"`)·시간 초과 판단이 위 표대로 기록된다.
- ls·stat·search 성공은 `decision: ok`, 실패(예: ENOENT)는 `decision: error`.
- 형식 오류 요청(op·path가 None이거나 dict 같은 값)도 기록된다.
- **원문·탐지 값이 기록에 없다**: 가짜 원문(예: `010-1234-5678`, `900101-1234567`)을 담은 파일을 판단했다고 치고 만든 Judgment로 기록한 뒤 파일 전체에 그 값이 없는지 본다(Judgment에는 원래 값이 없으니, reason 등에 값이 섞이지 않았는지와 기록 키가 표와 같은지를 함께 본다).
- 한글 경로, 줄바꿈이 든 경로, `surrogateescape` 경로.
- 여러 스레드가 동시에 기록해도 모든 줄이 올바른 JSON이고 개수가 맞다.
- 파일 0600, 새로 만든 부모 폴더 0700, 로그 경로가 심볼릭 링크면 실패(`OSError`).
- 이어 쓰기(다시 열어도 앞 기록이 남는다), `close` 뒤 `record`는 `ValueError`.
- `ts`가 시간대가 붙은 ISO 8601이고, `now`를 주면 그 값을 쓴다.

### 보고

끝나면 다음을 한국어로 짧게 보고한다.
- 만든 파일과 테스트 수, `uv run pytest` 전체 결과(다른 파일의 테스트가 깨졌으면 그 이름. 메인이 동시에 작업 중이라 B와 무관한 실패가 있을 수 있다)
- 정한 것(예: surrogateescape 처리 방법)과 메인에게 제안할 것
- 다른 파일을 고칠 필요가 있었다면 그 내용(고치지 말고 보고만)
