# Phase 2 ∥ Phase 3 병렬 작업 지시서

> 새 세션의 메인이 서브에이전트 두 개(일반 목적 에이전트)를 동시에 띄울 때 그대로 넘기는 지시서다. 메인은 두 결과를 확인하고 전체 테스트를 돌린 뒤 **사용자에게 보고하고 멈춘다.**
> 이 폴더는 git 저장소가 아니라서 worktree로 분리할 수 없다. 그래서 두 에이전트가 손대는 파일을 아래처럼 나눈다.

## 파일 분담

| | Phase 2 에이전트 | Phase 3 에이전트 |
|---|---|---|
| 만드는 파일 | `gate/detectors.py`, `tests/test_detectors.py` | `gate/jev_client.py`, `tests/test_jev_client.py`, `samples/`, `eval/`, `tests/test_eval.py` |
| `pyproject.toml`, `uv.lock` | 건드리지 않음 (표준 라이브러리 `re`만) | 꼭 필요할 때만 `uv add`로 변경 (가능하면 표준 라이브러리 `urllib`) |
| 공통 금지 | 기존 파일, 상대 에이전트 파일, `HANDOFF.md`, `plans/` 수정. git init·커밋 | 같음 |

## 두 작업을 잇는 인터페이스 (Phase 2가 만들고, Phase 3 평가와 Phase 4가 쓴다)

```python
# gate/detectors.py
@dataclass(frozen=True)
class Finding:
    kind: str   # "rrn" | "foreigner_rrn" | "mobile" | "phone" | "email" | "card" | "account"
    start: int  # 문자(str 인덱스) 단위
    end: int

@dataclass(frozen=True)
class MaskResult:
    text: str
    findings: tuple[Finding, ...]

def detect(text: str) -> list[Finding]
def mask(text: str) -> MaskResult                      # len(결과.encode("utf-8")) == len(text.encode("utf-8"))
def mask_bytes(data: bytes) -> tuple[bytes, tuple[Finding, ...]]   # 바이트 길이 동일, UTF-8이 아닌 바이트도 보존
```

## 공통 맥락 (두 지시서 앞에 붙여서 넘긴다)

- 경로: `/Users/sangwoo/work/personal/hackathon/fuse-jev-gate`. Python 3.12, uv. 테스트는 `uv run pytest` (Phase 1 기준 43 통과, 1 skip).
- LLM 에이전트가 원본 문서를 게이트를 거쳐서만 읽게 하는 도구다. 판단 흐름은 경로 규칙 → 캐시 → 1차 정규식(같은 길이 마스킹) → 2차 Jev(맥락형 개인정보 판단) → 정책(확신도 임계값으로 허용/차단/사람 승인). Phase 1(Unix 소켓 데몬, 검사 없이 전달)까지 끝났다.
- 먼저 읽을 것: `fuse-jev-gate-spec.md`(원래 명세), `plans/implementation-plan.md`(현재 계획, 명세와 다르면 이쪽이 우선).
- 코드 스타일: `gate/protocol.py`, `gate/paths.py`, `gate/store.py`를 따른다. 한국어 모듈 docstring, 주석은 적게, 타입 힌트, dataclass.
- 보고와 코드 주석은 한국어. 테스트 데이터는 전부 가짜, 실존 인물(유명인 포함) 정보 금지. 번호는 `900101-1234567`, `010-1234-5678`, 테스트 카드 `4111 1111 1111 1111`, `hong@example.com`처럼 누가 봐도 예시인 값을 쓴다.
- 끝나면 `uv run pytest`로 전체를 돌린다. 상대 에이전트가 만드는 중인 파일 때문에 실패하면 고치지 말고 보고만 한다.

---

## 지시서 A: Phase 2 정규식 탐지 + 같은 길이 마스킹

명세의 "3.2 같은 길이 마스킹"과 "Phase 2"를 구현한다.

- 대상: 주민등록번호, 외국인등록번호, 휴대폰·일반 전화번호, 이메일, 카드번호(Luhn 검증), 계좌번호.
- **같은 길이는 문자 수가 아니라 UTF-8 바이트 수 기준이다.** 숫자·영문은 `*`. 멀티바이트 문자가 가릴 범위에 들어가면 같은 바이트 길이가 되게 치환한다(예: 한글 1자 = `***`). 방식을 docstring에 적고 테스트로 검증한다.
- 마스킹 모양: 명세 예시는 `900101-1234567` → `900101-1******`. 나머지 종류는 구분자(`-`, 공백, `@`, `.`)를 남겨 형식은 보이되 식별 부분은 가린다. 종류별 규칙을 docstring에 표로 적는다.
- 오탐 줄이기:
  - 주민·외국인등록번호: 앞 6자리 날짜(YYMMDD) 유효성, 7번째 자리(주민 1~4·9·0, 외국인 5~8). 체크섬은 필수 조건으로 쓰지 않는다(최근 발급분은 맞지 않는 것으로 알려짐. 확인이 안 되면 쓰지 않는 쪽으로).
  - 더 긴 숫자열의 일부와 매칭되지 않게 앞뒤 경계를 둔다.
  - 전화: 휴대폰 010/011/016/017/018/019, 지역번호(02, 031~064), 070, 구분자는 선택. 대표번호(1588-xxxx 등)를 제외할지 정하고 이유를 적는다.
  - 카드: 13~19자리, 공백·하이픈 그룹 허용, Luhn 통과 필수.
  - 계좌: 형식이 은행마다 달라 주문번호·금액·날짜와 헷갈리기 쉽다. 근처 키워드(계좌, 입금, 은행명 등)를 요구하는 식으로 오탐을 줄이고 트레이드오프를 적는다.
  - 패턴이 겹치면(카드와 계좌 등) 결정적으로 하나를 고르는 규칙을 정한다.
- 한글로 쓴 숫자("공일공 일이삼사")는 범위 밖. TODO 주석으로 남긴다.
- 테스트: 종류별 탐지·비탐지(주문번호, 날짜, 금액, 대표번호 등), 마스킹 결과 예시, **마스킹 전후 바이트 길이 동일**(한글 문장, 이모지, 섞인 텍스트, UTF-8이 아닌 바이트가 든 `mask_bytes`).
- 보고: 만든 파일 / 종류별 규칙 표 / 오탐 결정과 트레이드오프 / 테스트 결과 / 남은 TODO.

---

## 지시서 B: Phase 3 Jev 클라이언트 + 한국어 평가 준비

명세의 "3.3 Jev 연동", "3.4 정책", "Phase 3"을 구현한다. 요청자 정보는 `gate/peer.py`의 `Peer(pid, uid, exe)`, 소켓 요청에는 `purpose` 필드가 있다(`gate/protocol.py`, `gate/daemon.py`).

### 1. 인터페이스와 Mock
```python
@dataclass(frozen=True)
class RequestContext:
    process: str | None   # 요청 프로세스 실행 파일 경로 (Peer.exe)
    pid: int | None
    path: str             # 가상 경로
    purpose: str | None

@dataclass(frozen=True)
class JevDecision:
    label: Literal["safe", "sensitive", "highly_sensitive"]
    confidence: float     # 0.0 ~ 1.0
    raw: dict

class JevClient(Protocol):
    def judge(self, text: str, context: RequestContext) -> JevDecision: ...
```
- **실패하면 차단이 기본이다.** 호출 실패, 타임아웃(기본 3초, 설정 가능), 응답 형식 오류, 예외는 모두 `JevError`를 던진다. 절대 safe를 돌려주지 않는다.
- 질문 구성 함수: 내용만 보내지 말고 요청 프로세스, 가상 경로, 목적을 함께 넣어 "이 요청에 이 내용을 보여줘도 되는가"를 묻는다. 맥락이 들어가는지 테스트한다.
- `MockJevClient`: 결정적. 테스트용 고정 응답 방식과 시연용 단순 키워드 방식. **Mock은 Jev 성능을 대변하지 않는다**는 점을 docstring과 평가 출력에 명시한다.

### 2. 백엔드 교체 구조 (조사·설치 없음)
- **사용자 결정(2026-10-31): Jev는 로컬에서 돌리되, 실제 백엔드는 나중에 정한다.** 지금은 Jev 방식(판단 전용, 답 + 확신도)으로 인터페이스와 Mock만 구현한다. 실제 백엔드는 나중에 `JevClient` 구현 하나를 추가해 바꿔 끼운다.
- **하지 않는 것:** 로컬 후보 조사, 공식 API 조사, HTTP 클라이언트 구현, 모델 다운로드, 런타임 설치, 외부 API 호출, 키 생성, 계정 설정 변경.
- 백엔드를 이름으로 고르는 함수를 둔다. 예: `make_client(name: str, **options) -> JevClient`. 지금은 `"mock"`만 등록하고, 모르는 이름이면 `JevError`를 던진다. Phase 4에서 `policy.yaml`의 `jev_backend: mock` 같은 설정으로 넘길 예정이다.
- 실제 백엔드 자리에 TODO 주석으로 남길 것: 서버 주소는 환경변수(`JEV_BASE_URL` 등)로 받는다. 키가 필요한 백엔드라면 키는 환경변수에서만 읽고 로그·예외·repr에 나오지 않게 한다.
- Jev에 보내는 텍스트는 정규식으로 마스킹한 뒤여야 한다. 로컬이라도 설계 일관성을 위해서이고, 나중에 외부 API로 바꿔도 안전하다.

### 3. samples/: 합성 한국어 문서 최소 30개
- 종류: 자기소개서, 고객 상담 채팅, 진료 메모, 이력서
- 경우: 개인정보 있음(이름 + 연락처·주민번호 등), 없음, 준식별자만(이름 없이 학교 + 연도 + 지역 + 직업 조합)
- 4 × 3 × 3 = 36개 정도. 예: `samples/<종류>/<경우>_<n>.txt`. 자연스러운 한국어로 쓴다.

### 4. eval/: 라벨과 평가 스크립트
- 라벨 파일(CSV 또는 JSONL): 파일 경로, 기대 라벨, 문서 종류, 경우. 라벨 기준(예: 있음 → highly_sensitive, 준식별자만 → sensitive, 없음 → safe)을 `eval/README.md`에 적는다.
- `uv run python -m eval.run_eval --client mock` → 정확도, 오탐률(safe를 safe 아님으로), 미탐률(safe 아님을 safe로), 평균 지연, 혼동행렬, 종류별 결과. 임계값 0.95 기준 정책 결과(허용·차단·승인) 분포도 보여 주면 좋다.
- **마스킹:** 평가 함수는 마스킹 함수를 인자로 받는다(`masker=`). CLI 기본값은 함수 안에서 `gate.detectors.mask`를 import한다. import에 실패하면 중단한다(마스킹 없이 외부로 보내지 않음). 테스트는 가짜 마스킹 함수를 주입해 `gate.detectors` 없이도 통과해야 한다.
- `tests/test_eval.py`: 지표 계산(작은 고정 데이터), 라벨 파일이 모든 샘플을 덮는지, 샘플 30개 이상.
- `gate/detectors.py`가 생겨 있으면 `uv run python -m eval.run_eval --client mock`도 실행해 출력을 보고에 붙인다.
- 보고: 만든 파일 / 백엔드 교체 구조 설명 / 샘플·라벨 요약 / Mock 평가 출력 / 테스트 결과 / 사용자 확인이 필요한 것.

---

## 메인이 할 일 (두 에이전트가 끝난 뒤)
1. 두 보고를 읽고, 만든 파일을 직접 훑어본다(특히 마스킹 길이 보존, JevError 처리, 키 노출).
2. `uv run pytest` 전체 실행, Linux 컨테이너에서도 실행(`HANDOFF.md`의 검증 명령).
3. `uv run python -m eval.run_eval --client mock`으로 두 결과가 맞물리는지 확인한다.
4. `HANDOFF.md`와 `plans/implementation-plan.md`에 결과를 반영하고, 사용자에게 Phase 2와 Phase 3a를 보고한 뒤 멈춘다.
5. Phase 3b(실제 로컬 백엔드 + 실제 한국어 평가)는 보류다. 사용자가 백엔드를 정하면 그때 진행한다. 다음 순서는 사용자 확인 후 Phase 4이고, Jev는 Mock으로 진행한다.
