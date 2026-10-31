"""원본 폴더(마운트) 보기·추가·빼기의 계산·검사·쓰기 (Phase 9). `gate mount`(gate.mount_cli)와 설정 화면(gate ui)이
같은 함수를 쓴다. 계획: plans/mount-ui-plan.md (4절 설계, 9절 인터페이스).

이 모듈은 묻지 않는다. plan_*가 바꿈(MountChange)을 계산하고 검사하면, 묻는 쪽이 보여 주고 확인받은 뒤 apply로 쓴다.
  - gate mount: 터미널에서 y/N으로 묻는다. 느슨해지는 바꿈(add·unprotect)은 한 번 더 묻는다.
  - gate ui: 화면에서 확인 상자를 보여 주고, 사람이 누르면 MountChange.confirm_token을 실어 다시 보낸다.

파일 (위치는 정책 파일 옆으로 고정. MountContext)
  - mounts.yaml      추가한 폴더 목록. 데몬이 요청마다 바뀌었는지 보고 다시 읽는다. connected: false는 "보호만 남음"
  - workspaces.yaml  `gate workspace init`이 등록한 작업 폴더. 폴더를 추가·보호 해제하면 등록된 작업 폴더마다
                     .claude/settings.json을 지금 목록으로 다시 쓴다
  둘 다 0600, 원자적으로 쓴다. 작업 폴더 설정·전역 설치(gate setup)가 둘을 Read·Edit 거부 + 샌드박스 denyRead로 막는다.

순서 (하나라도 빠지면 구멍. 4.3절)
  - add:       ① 등록된 작업 폴더 설정 모두 다시 쓰기(막기) → ② mounts.yaml에 connected: true로 적기(게이트에 열기).
               ①이 하나라도 실패하면 ②를 하지 않고, 이미 다시 쓴 작업 폴더 설정은 원래대로 돌린다.
  - remove:    mounts.yaml에서 connected: false로만 바꾼다. 작업 폴더 설정은 그대로(직접 읽기 막기 남김, 사용자 결정).
  - unprotect: ① mounts.yaml에서 항목을 지운다 → ② 작업 폴더 설정을 다시 쓴다. 느슨해지는 바꿈이다.
  - 작업 폴더 설정이 손으로 바뀌어 있으면(지금 파일 해시가 마지막으로 쓴 해시와 다르고, 새로 만들 내용과도 다름)
    add·unprotect를 하지 않는다(MountError: `gate workspace init --force`로 다시 만들라고 알림).

이 파일의 공개 이름(클래스·필드·함수 이름과 인자)은 9-1(게이트·CLI)과 9-2(화면)가 같이 쓰는 약속이다.
바꿔야 하면 메인과 먼저 맞춘다.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

MOUNTS_FILE_NAME = "mounts.yaml"
WORKSPACES_FILE_NAME = "workspaces.yaml"
MAX_MOUNTS = 20            # mounts.yaml 항목 수(보호만 남은 것 포함) 최대
MAX_COUNT_FILES = 10_000   # 파일 수는 여기까지만 센다(넘으면 file_count_capped)

AddedBy = Literal["ui", "terminal"]
Op = Literal["add", "remove", "unprotect"]
# 쓴 뒤 알려 줄 점. 글은 보여 주는 쪽(gate mount, gate ui)이 따로 쓴다.
#   restart_session: 작업 폴더 설정을 다시 썼다. 이미 켜진 에이전트 세션은 다시 시작해야 반영될 수 있다(사용자 결정: 항상 안내)
#   rerun_setup:     전역 설치 기록(gate setup)이 있다. 전역 설정에는 반영되지 않았으니 `gate setup`을 다시 실행하라
#   no_workspace:    등록된 작업 폴더가 없다. 어느 작업 폴더에서도 이 폴더 직접 읽기가 막히지 않았다
Note = Literal["restart_session", "rerun_setup", "no_workspace"]


class MountError(Exception):
    """할 수 없다(추가할 수 없는 폴더, 이름 없음, 목록·등록 파일 문제, 손으로 바뀐 작업 폴더 설정, 쓰기 실패,
    plan 뒤에 상태가 바뀜). 메시지는 사용자에게 그대로 보여 주는 해요체 한두 문장이다."""


@dataclass(frozen=True)
class MountContext:
    """폴더 관리에 필요한 경로. 정책 파일 경로 하나로 목록 파일과 등록 파일 위치가 정해진다."""

    policy: Path
    socket: Path | None = None
    audit: Path | None = None

    @property
    def mounts_file(self) -> Path:
        return self.policy.parent / MOUNTS_FILE_NAME

    @property
    def workspaces_file(self) -> Path:
        return self.policy.parent / WORKSPACES_FILE_NAME

    @classmethod
    def from_policy(cls, policy: str | Path, socket: str | Path | None = None,
                    audit: str | Path | None = None) -> MountContext:
        """경로를 expanduser + absolute로 맞춘다(resolve는 하지 않는다. 정책 파일이 아직 없을 수 있다)."""
        raise NotImplementedError


@dataclass(frozen=True)
class WorkspaceStatus:
    """등록된 작업 폴더 하나.

    state
      ok:      설정 파일이 지금 목록으로 만든 내용과 같다
      stale:   마지막으로 쓴 내용 그대로인데 지금 목록과 다르다(다음 add·unprotect 때 자동으로 다시 쓴다)
      edited:  손으로 바뀌었다(자동으로 덮지 않는다. `gate workspace init --force` 안내)
      missing: 작업 폴더나 설정 파일이 없다(다시 쓰기에서 건너뛴다)
    """

    path: str
    state: Literal["ok", "stale", "edited", "missing"]


@dataclass(frozen=True)
class FolderRow:
    """화면·`gate mount list`의 한 줄.

    kind=base는 기본 폴더(--source)다. 등록된 작업 폴더에 적힌 원본 경로에서 알아낸다(여럿이면 줄도 여럿).
    base는 name이 None, vpath는 "/", connected는 True, 뺄 수 없다.
    """

    kind: Literal["base", "mount"]
    name: str | None            # 게이트 안 이름(NFC). base는 None
    vpath: str                  # "/" 또는 "/가계부"
    path: str                   # 실제 경로(심볼릭 링크를 따라간 것)
    connected: bool             # 게이트로 보임. False면 "보호만 남음"(뺐지만 직접 읽기 막기는 남김)
    exists: bool                # 실제 폴더가 지금 있음(외장 디스크를 빼거나 지우면 False → "찾을 수 없음")
    file_count: int | None      # 일반 파일 수(MAX_COUNT_FILES까지). 셀 수 없거나 count_files=False면 None
    file_count_capped: bool     # MAX_COUNT_FILES를 넘음("1만 개 넘음")
    added_at: str | None        # ISO 8601. base는 None
    added_by: AddedBy | None    # base는 None
    unprotected_workspaces: tuple[str, ...]  # 이 폴더 직접 읽기가 아직 막히지 않은 등록 작업 폴더(stale·edited)


@dataclass(frozen=True)
class MountsView:
    folders: tuple[FolderRow, ...]          # base 먼저, 그다음 mounts.yaml 순서
    workspaces: tuple[WorkspaceStatus, ...]
    global_setup: bool                      # 전역 설치 기록(gate.claude_setup.record_path)이 있음
    error: str | None                       # mounts.yaml·workspaces.yaml이 잘못됨(그동안 게이트는 내용 읽기를 모두 막음)
    limit: int = MAX_MOUNTS


@dataclass(frozen=True)
class MountChange:
    """계산한 바꿈 하나. apply 전에 보여 주는 내용이다."""

    op: Op
    name: str                    # 게이트 안 이름(add는 겹치면 뒤에 " 2", " 3"을 붙여 정한 이름)
    path: str                    # 실제 경로
    file_count: int | None
    file_count_capped: bool
    workspaces: tuple[str, ...]  # 다시 쓸 작업 폴더(remove는 비어 있음)
    loosening: bool              # 에이전트에게 더 보여 주는 바꿈(add·unprotect). 한 번 더 확인받는다
    reconnect: bool = False      # add인데 "보호만 남음" 항목을 다시 연결하는 것

    @property
    def confirm_token(self) -> str:
        """확인 값. 위 필드로 만든 해시(16진 24자 정도). 화면은 이 값을 실어 다시 보내고, 서버는 다시 계산한 값과 비교한다."""
        raise NotImplementedError


@dataclass(frozen=True)
class MountResult:
    change: MountChange
    rewritten: tuple[str, ...]   # 다시 쓴 작업 폴더 설정
    notes: tuple[Note, ...]


def list_mounts(ctx: MountContext, *, count_files: bool = True) -> MountsView:
    """지금 상태. 목록·등록 파일이 잘못되어 있어도 던지지 않고 error에 담는다(그때 folders는 읽을 수 있는 만큼)."""
    raise NotImplementedError


def plan_add(ctx: MountContext, folder: str | Path, *, added_by: AddedBy) -> MountChange:
    """폴더 추가(또는 보호만 남은 항목 다시 연결)를 계산·검사한다. 4.5절 거부 목록, 개수 제한, 이름 정하기,
    손으로 바뀐 작업 폴더 설정 검사를 여기서 한다. 못 하면 MountError."""
    raise NotImplementedError


def plan_remove(ctx: MountContext, name: str) -> MountChange:
    """게이트에서 빼기(connected: false). 없는 이름이거나 이미 뺀 것이면 MountError."""
    raise NotImplementedError


def plan_unprotect(ctx: MountContext, name: str) -> MountChange:
    """보호도 풀기(항목을 지우고 작업 폴더 설정을 다시 씀). connected인 항목은 먼저 빼야 한다(MountError)."""
    raise NotImplementedError


def apply(ctx: MountContext, change: MountChange) -> MountResult:
    """다시 계산해 change와 같을 때만 쓴다(같은 op·이름·경로로 plan을 다시 해 confirm_token 비교). 다르면 MountError.
    쓰기 순서와 실패 시 되돌림은 맨 위 설명대로."""
    raise NotImplementedError
