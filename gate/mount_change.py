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

구현에서 정한 것 (9-1)
  - 작업 폴더 설정 상태(WorkspaceStatus.state)는 등록에 적힌 경로(원본·소켓·정책·감사 로그·gate) + 목록(mounts.yaml의
    모든 항목, 보호만 남은 것 포함)으로 만든 글(gate.workspace.settings_text)과 비교한다.
    stale은 "등록 해시와 같음" 또는 "바꾸기 전 목록으로 만든 글과 같음"이다(gate가 만든 글이면 손으로 바꾼 것이 아니다).
    설정 파일이 심볼릭 링크이거나 읽을 수 없으면 edited로 본다(따라가 덮지 않는다).
  - 다시 쓰는 작업 폴더(MountChange.workspaces)는 stale인 것만이다. ok는 이미 같고, missing은 건너뛴다.
  - 쓰다가 실패하면 add·unprotect 모두 이미 쓴 것(작업 폴더 설정, 등록 파일, 목록 파일)을 원래대로 돌리고 MountError.
  - confirm_token은 op·name·path·workspaces·loosening·reconnect로 만든다. file_count는 넣지 않는다(확인하는 사이 파일이
    하나 늘었다고 다시 묻지 않게. apply는 파일을 다시 세지 않는다).
  - added_by는 MountChange.added_by에 담는다(add만, 나머지는 None). apply는 이 값으로 다시 계산한다.
  - 알림(notes): 다시 쓴 작업 폴더가 있으면 restart_session. add이고 전역 설치 기록이 있으면 rerun_setup(unprotect는
    `gate setup`을 다시 해도 규칙이 빠지지 않아 붙이지 않는다). add·unprotect인데 등록된 작업 폴더 중 설정 파일이 있는
    것(ok·stale)이 없으면 no_workspace.
  - 여러 프로세스(화면·터미널)가 같이 apply하지 않게 정책 파일 옆 `.mounts.lock`을 flock으로 잡는다.
"""

from __future__ import annotations

import contextlib
import errno
import fcntl
import hashlib
import json
import os
import stat
import threading
import time
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

from gate import claude_setup
from gate.approval import clean_text
from gate.mounts import (  # noqa: F401  (MOUNTS_FILE_NAME·WORKSPACES_FILE_NAME·MAX_MOUNTS는 이 모듈 이름으로도 쓴다)
    MAX_MOUNTS,
    MOUNTS_FILE_NAME,
    WORKSPACES_FILE_NAME,
    MountEntry,
    MountsFileError,
    WorkspaceRecord,
    build_guard,
    choose_name,
    disk_path,
    has_control,
    name_key,
    name_problem,
    now_iso,
    path_key,
    read_mounts,
    read_workspaces,
    refusal,
    sha256_text,
    write_mounts,
    write_private,
    write_workspaces,
)
from gate.workspace import GatePaths, settings_text

MAX_COUNT_FILES = 10_000   # 파일 수는 여기까지만 센다(넘으면 file_count_capped)
COUNT_SECONDS = 2.0        # 폴더 하나를 세는 데 쓰는 시간. 넘으면 file_count는 None
LIST_COUNT_SECONDS = 5.0   # list_mounts가 모든 폴더를 세는 데 쓰는 시간
LOCK_FILE_NAME = ".mounts.lock"

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
        def absolute(p: str | Path | None) -> Path | None:
            return None if p is None else Path(p).expanduser().absolute()
        return cls(absolute(policy), absolute(socket), absolute(audit))


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
    added_by: AddedBy | None = None  # add만: 목록에 남길 추가한 곳(ui | terminal). 확인 값에는 넣지 않는다

    @property
    def confirm_token(self) -> str:
        """확인 값. 위 필드로 만든 해시(16진 24자 정도). 화면은 이 값을 실어 다시 보내고, 서버는 다시 계산한 값과 비교한다.
        file_count·file_count_capped는 넣지 않는다(모듈 설명 "구현에서 정한 것")."""
        fields = {"op": self.op, "name": self.name, "path": self.path, "workspaces": list(self.workspaces),
                  "loosening": self.loosening, "reconnect": self.reconnect}
        return hashlib.sha256(json.dumps(fields, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:24]


@dataclass(frozen=True)
class MountResult:
    change: MountChange
    rewritten: tuple[str, ...]   # 다시 쓴 작업 폴더 설정
    notes: tuple[Note, ...]


# --- 상태 읽기 ---

def global_settings_file() -> Path:
    """전역 설치(gate setup) 기본 대상 설정 파일. 테스트가 바꿔 끼운다(실제 ~/.claude를 보지 않게)."""
    return claude_setup.default_settings_file()


def _global_setup() -> bool:
    try:
        settings = global_settings_file()
        return claude_setup.record_path(settings).exists() or claude_setup.record_path(settings.resolve()).exists()
    except (OSError, RuntimeError):
        return False


def _mounts_problem(ctx: MountContext, exc: Exception) -> str:
    return (f"폴더 목록 파일({ctx.mounts_file})에 문제가 있어요: {exc}. "
            "고칠 때까지 게이트는 내용 읽기를 모두 막아요. 파일을 고치거나 지우세요.")


def _records_problem(ctx: MountContext, exc: Exception) -> str:
    return (f"작업 폴더 등록 파일({ctx.workspaces_file})에 문제가 있어요: {exc}. "
            "고칠 때까지 게이트는 내용 읽기를 모두 막아요. 파일을 고치거나 지운 뒤 `gate workspace init --force`로 다시 등록하세요.")


def _load(ctx: MountContext) -> tuple[tuple[MountEntry, ...], tuple[WorkspaceRecord, ...]]:
    try:
        entries = read_mounts(ctx.mounts_file)
    except MountsFileError as exc:
        raise MountError(_mounts_problem(ctx, exc)) from exc
    try:
        records = read_workspaces(ctx.workspaces_file)
    except MountsFileError as exc:
        raise MountError(_records_problem(ctx, exc)) from exc
    return entries, records


def _gate_paths(record: WorkspaceRecord, entries: Iterable[MountEntry]) -> GatePaths:
    """등록에 적힌 경로 + 목록. 추가 폴더는 gate workspace init(with_listed_mounts)과 같게 disk_path로 맞춘다."""
    mounts = tuple(dict.fromkeys(disk_path(e.path) for e in entries))
    return GatePaths(Path(record.source), Path(record.socket), Path(record.policy), Path(record.audit),
                     Path(record.gate_bin), mounts)


def _settings_file(record: WorkspaceRecord) -> Path:
    return Path(record.path) / ".claude" / "settings.json"


@dataclass(frozen=True)
class _Workspace:
    record: WorkspaceRecord
    settings: Path
    state: str               # ok | stale | edited | missing
    text: str | None         # 지금 설정 파일 내용(없거나 읽지 못하면 None)
    new_text: str            # 바꾼 뒤 목록으로 만든 설정 파일 내용


def _inspect(record: WorkspaceRecord, target: Sequence[MountEntry],
             previous: Sequence[MountEntry] | None = None) -> _Workspace:
    """target 목록 기준 상태. previous(바꾸기 전 목록)로 만든 글과 같아도 gate가 쓴 것으로 본다(stale)."""
    settings = _settings_file(record)
    new_text = settings_text(_gate_paths(record, target))
    if not Path(record.path).is_dir() or (not settings.exists() and not settings.is_symlink()):
        return _Workspace(record, settings, "missing", None, new_text)
    if settings.is_symlink():
        return _Workspace(record, settings, "edited", None, new_text)
    try:
        text = settings.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return _Workspace(record, settings, "edited", None, new_text)
    if text == new_text:
        state = "ok"
    elif sha256_text(text) == record.settings_sha256:
        state = "stale"
    elif previous is not None and text == settings_text(_gate_paths(record, previous)):
        state = "stale"
    else:
        state = "edited"
    return _Workspace(record, settings, state, text, new_text)


def _protects(text: str | None, path: str) -> bool:
    """설정 글이 이 폴더를 막는지(권한 규칙 Read·Edit 둘 다 + 샌드박스 denyRead)."""
    if text is None:
        return False
    try:
        data = json.loads(text)
    except ValueError:
        return False
    real = str(disk_path(path))
    rule = "/" + real
    permissions = data.get("permissions") if isinstance(data, dict) else None
    sandbox = data.get("sandbox") if isinstance(data, dict) else None
    deny = permissions.get("deny") if isinstance(permissions, dict) else None
    filesystem = sandbox.get("filesystem") if isinstance(sandbox, dict) else None
    deny_read = filesystem.get("denyRead") if isinstance(filesystem, dict) else None
    if not isinstance(deny, list) or not isinstance(deny_read, list):
        return False
    rules = (f"Read({rule})", f"Read({rule}/**)", f"Edit({rule})", f"Edit({rule}/**)")
    return all(r in deny for r in rules) and real in deny_read


def _live(path: str) -> bool:
    """기록한 실제 경로에 지금 폴더가 있다(다른 곳을 가리키는 심볼릭 링크로 바뀌었으면 아님)."""
    try:
        return os.path.realpath(path) == path and os.path.isdir(path)
    except OSError:
        return False


def _count_files(path: str | Path, deadline: float) -> tuple[int | None, bool]:
    """일반 파일 수. 심볼릭 링크는 따라가지 않는다. MAX_COUNT_FILES를 넘으면 (MAX_COUNT_FILES, True),
    deadline을 넘기거나 맨 위 폴더를 읽지 못하면 (None, False)."""
    count = 0
    stack = [os.fspath(path)]
    first = True
    seen = 0
    while stack:
        folder = stack.pop()
        try:
            with os.scandir(folder) as it:
                for entry in it:
                    seen += 1
                    if seen % 256 == 0 and time.monotonic() > deadline:
                        return None, False
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(entry.path)
                        elif entry.is_file(follow_symlinks=False):
                            count += 1
                            if count > MAX_COUNT_FILES:
                                return MAX_COUNT_FILES, True
                    except OSError:
                        continue
        except OSError:
            if first:
                return None, False
        first = False
        if time.monotonic() > deadline:
            return None, False
    return count, False


def _base_names(records: Iterable[WorkspaceRecord]) -> list[str]:
    """기본 폴더(등록된 작업 폴더의 원본) 맨 위 항목 이름."""
    names: list[str] = []
    for source in dict.fromkeys(r.source for r in records):
        try:
            names += os.listdir(source)
        except OSError:
            continue
    return names


def list_mounts(ctx: MountContext, *, count_files: bool = True) -> MountsView:
    """지금 상태. 목록·등록 파일이 잘못되어 있어도 던지지 않고 error에 담는다(그때 folders는 읽을 수 있는 만큼)."""
    try:
        return _list(ctx, count_files)
    except Exception as exc:  # 화면이 깨지지 않게. 종류만 남긴다
        return MountsView((), (), False, f"폴더 목록을 읽지 못했어요: {type(exc).__name__}")


def _list(ctx: MountContext, count_files: bool) -> MountsView:
    errors: list[str] = []
    try:
        entries = read_mounts(ctx.mounts_file)
    except MountsFileError as exc:
        entries = ()
        errors.append(_mounts_problem(ctx, exc))
    try:
        records = read_workspaces(ctx.workspaces_file)
    except MountsFileError as exc:
        records = ()
        errors.append(_records_problem(ctx, exc))
    workspaces = [_inspect(r, entries) for r in records]
    deadline = time.monotonic() + LIST_COUNT_SECONDS

    def count(path: str, exists: bool) -> tuple[int | None, bool]:
        if not count_files or not exists:
            return None, False
        return _count_files(path, min(deadline, time.monotonic() + COUNT_SECONDS))

    rows: list[FolderRow] = []
    for source in dict.fromkeys(r.source for r in records):
        exists = os.path.isdir(source)
        unprotected = tuple(w.record.path for w in workspaces
                            if w.record.source == source and w.state == "edited" and not _protects(w.text, source))
        rows.append(FolderRow("base", None, "/", source, True, exists, *count(source, exists), None, None, unprotected))
    for e in entries:
        exists = _live(e.path)
        unprotected = tuple(w.record.path for w in workspaces
                            if w.state in ("stale", "edited") and not _protects(w.text, e.path))
        rows.append(FolderRow("mount", e.name, "/" + e.name, e.path, e.connected, exists, *count(e.path, exists),
                              e.added_at, e.added_by, unprotected))  # type: ignore[arg-type]
    statuses = tuple(WorkspaceStatus(w.record.path, w.state) for w in workspaces)  # type: ignore[arg-type]
    return MountsView(tuple(rows), statuses, _global_setup(), " ".join(errors) or None)


# --- 계산 ---

@dataclass(frozen=True)
class _Plan:
    change: MountChange
    before: tuple[MountEntry, ...]
    after: tuple[MountEntry, ...]
    records: tuple[WorkspaceRecord, ...]
    workspaces: tuple[_Workspace, ...]

    @property
    def rewrites(self) -> tuple[_Workspace, ...]:
        return tuple(w for w in self.workspaces if w.state == "stale")


def _find(entries: Sequence[MountEntry], name: object) -> MountEntry | None:
    if not isinstance(name, str):
        return None
    if name.startswith("/") and "/" not in name[1:]:  # `gate mount remove /가계부`도 받는다
        name = name[1:]
    key = name_key(name)
    return next((e for e in entries if name_key(e.name) == key), None)


def _unknown_name(name: object) -> MountError:
    return MountError(f"'{clean_text(str(name))}'(이)라는 폴더가 목록에 없어요. 이름을 확인해 주세요.")


def _refuse_edited(workspaces: Sequence[_Workspace], op: Op) -> None:
    for w in workspaces:
        if w.state == "edited":
            action, again = ("연결하지", "다시 연결하세요") if op == "add" else ("보호를 풀지", "다시 풀어 주세요")
            raise MountError(f"작업 폴더 설정이 손으로 바뀌어 있어 {action} 않았어요: {w.settings}. "
                             f"`gate workspace init --force`로 다시 만든 뒤 {again}.")


def _counted(path: str, count: bool) -> tuple[int | None, bool]:
    if not count or not _live(path):
        return None, False
    return _count_files(path, time.monotonic() + COUNT_SECONDS)


def _plan_add(ctx: MountContext, folder: str | Path, added_by: AddedBy, *, count: bool = True) -> _Plan:
    if added_by not in ("ui", "terminal"):
        raise MountError("추가한 곳(added_by)은 ui나 terminal이어야 해요.")
    entries, records = _load(ctx)
    raw = Path(folder).expanduser()
    shown = clean_text(str(raw))
    if has_control(str(raw)):
        raise MountError("경로에 제어 문자가 든 폴더는 연결할 수 없어요.")
    try:
        real = disk_path(raw, strict=True)  # 디스크에 적힌 표기로(맥: 대소문자·NFC/NFD). 규칙은 글자로 맞춘다
    except FileNotFoundError:
        raise MountError(f"폴더를 찾을 수 없어요: {shown}") from None
    except (OSError, RuntimeError):
        raise MountError(f"폴더 경로를 확인할 수 없어요: {shown}") from None
    existing = next((e for e in entries if path_key(e.path) == path_key(real)), None)
    if existing is not None and existing.connected:
        raise MountError(f"이미 연결된 폴더예요: /{existing.name}")
    guard = build_guard(policy=ctx.policy, sockets=[ctx.socket], audits=[ctx.audit], records=records,
                        mounts=[e for e in entries if e is not existing])
    why = refusal(real, guard)
    if why is not None:
        raise MountError(why.message)
    stamp = now_iso()
    if existing is not None:
        name, path = existing.name, existing.path
        after = tuple(MountEntry(name, path, stamp, added_by, True) if e is existing else e for e in entries)
    else:
        if len(entries) >= MAX_MOUNTS:
            raise MountError(f"폴더는 {MAX_MOUNTS}개까지 둘 수 있어요(보호만 남은 것 포함). "
                             "쓰지 않는 폴더의 보호를 풀고 다시 해 주세요.")
        name, path = choose_name(real.name, [*_base_names(records), *(e.name for e in entries)]), str(real)
        problem = name_problem(name)
        if problem is not None:
            raise MountError(f"이 폴더 이름은 게이트 안 이름으로 쓸 수 없어요({problem}).")
        after = (*entries, MountEntry(name, path, stamp, added_by, True))
    workspaces = tuple(_inspect(r, after, entries) for r in records)
    _refuse_edited(workspaces, "add")
    change = MountChange("add", name, path, *_counted(path, count),
                         tuple(w.record.path for w in workspaces if w.state == "stale"),
                         loosening=True, reconnect=existing is not None, added_by=added_by)
    return _Plan(change, entries, after, records, workspaces)


def _plan_remove(ctx: MountContext, name: str, *, count: bool = True) -> _Plan:
    entries, records = _load(ctx)
    entry = _find(entries, name)
    if entry is None:
        raise _unknown_name(name)
    if not entry.connected:
        raise MountError(f"'{entry.name}'은(는) 이미 게이트에서 뺀 폴더예요(보호만 남음).")
    after = tuple(replace(e, connected=False) if e is entry else e for e in entries)
    workspaces = tuple(_inspect(r, after, entries) for r in records)
    change = MountChange("remove", entry.name, entry.path, *_counted(entry.path, count), (), loosening=False)
    return _Plan(change, entries, after, records, workspaces)


def _plan_unprotect(ctx: MountContext, name: str, *, count: bool = True) -> _Plan:
    entries, records = _load(ctx)
    entry = _find(entries, name)
    if entry is None:
        raise _unknown_name(name)
    if entry.connected:
        raise MountError(f"'{entry.name}'은(는) 게이트에 연결된 폴더예요. 먼저 게이트에서 뺀 뒤 보호를 풀 수 있어요.")
    after = tuple(e for e in entries if e is not entry)
    workspaces = tuple(_inspect(r, after, entries) for r in records)
    _refuse_edited(workspaces, "unprotect")
    change = MountChange("unprotect", entry.name, entry.path, *_counted(entry.path, count),
                         tuple(w.record.path for w in workspaces if w.state == "stale"), loosening=True)
    return _Plan(change, entries, after, records, workspaces)


def plan_add(ctx: MountContext, folder: str | Path, *, added_by: AddedBy) -> MountChange:
    """폴더 추가(또는 보호만 남은 항목 다시 연결)를 계산·검사한다. 4.5절 거부 목록, 개수 제한, 이름 정하기,
    손으로 바뀐 작업 폴더 설정 검사를 여기서 한다. 못 하면 MountError."""
    return _plan_add(ctx, folder, added_by).change


def plan_remove(ctx: MountContext, name: str) -> MountChange:
    """게이트에서 빼기(connected: false). 없는 이름이거나 이미 뺀 것이면 MountError."""
    return _plan_remove(ctx, name).change


def plan_unprotect(ctx: MountContext, name: str) -> MountChange:
    """보호도 풀기(항목을 지우고 작업 폴더 설정을 다시 씀). connected인 항목은 먼저 빼야 한다(MountError)."""
    return _plan_unprotect(ctx, name).change


# --- 쓰기 ---

# 테스트가 바꿔 끼워 실패를 만든다
def _write_settings(path: Path, text: str, mode: int) -> None:
    write_private(path, text.encode("utf-8"), mode)


def _write_list(ctx: MountContext, entries: Sequence[MountEntry]) -> None:
    write_mounts(ctx.mounts_file, entries)


def _write_records(ctx: MountContext, records: Sequence[WorkspaceRecord]) -> None:
    write_workspaces(ctx.workspaces_file, records)


_APPLY_LOCK = threading.Lock()


@contextlib.contextmanager
def _locked(ctx: MountContext) -> Iterator[None]:
    """같은 컴퓨터의 다른 gate 프로세스(화면·터미널)와 동시에 쓰지 않게 잡는다."""
    try:
        ctx.policy.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(ctx.policy.parent / LOCK_FILE_NAME, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
    except OSError as exc:
        raise MountError(f"폴더 목록을 잠그지 못했어요: {_reason(exc)}") from exc
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def _reason(exc: BaseException) -> str:
    if isinstance(exc, OSError):
        name = errno.errorcode.get(exc.errno, str(exc.errno)) if exc.errno else type(exc).__name__
        return f"{name} ({exc.filename})" if exc.filename else name
    return str(exc)


def _replan(ctx: MountContext, change: MountChange) -> _Plan:
    if change.op == "add":
        return _plan_add(ctx, change.path, change.added_by or "terminal", count=False)
    if change.op == "remove":
        return _plan_remove(ctx, change.name, count=False)
    if change.op == "unprotect":
        return _plan_unprotect(ctx, change.name, count=False)
    raise MountError("알 수 없는 바꿈이에요.")


def _rewrite(plan: _Plan, written: list[tuple[Path, bytes, int]]) -> tuple[WorkspaceRecord, ...]:
    """stale인 작업 폴더 설정을 다시 쓰고(원래 내용을 written에 쌓는다) 해시를 고친 등록 목록을 돌려준다."""
    hashes: dict[str, str] = {}
    for w in plan.rewrites:
        original = w.settings.read_bytes()
        if w.settings.is_symlink() or original.decode("utf-8", "replace") != w.text:
            raise MountsFileError("작업 폴더 설정 파일이 그사이 바뀌었습니다")  # 확인한 뒤 바뀐 내용을 덮지 않는다
        mode = stat.S_IMODE(w.settings.stat().st_mode)
        _write_settings(w.settings, w.new_text, mode)
        written.append((w.settings, original, mode))
        hashes[w.record.path] = sha256_text(w.new_text)
    return tuple(replace(r, settings_sha256=hashes[r.path]) if r.path in hashes else r for r in plan.records)


def _rollback(ctx: MountContext, written: list[tuple[Path, bytes, int]], records: tuple[WorkspaceRecord, ...] | None,
              entries: tuple[MountEntry, ...] | None) -> list[str]:
    """이미 쓴 것을 되돌린다. 되돌리지 못한 파일 목록을 돌려준다."""
    failed: list[str] = []
    for path, original, mode in reversed(written):
        try:
            write_private(path, original, mode)
        except OSError:
            failed.append(str(path))
    if records is not None:
        try:
            write_workspaces(ctx.workspaces_file, records)
        except (OSError, MountsFileError):
            failed.append(str(ctx.workspaces_file))
    if entries is not None:
        try:
            write_mounts(ctx.mounts_file, entries)
        except (OSError, MountsFileError):
            failed.append(str(ctx.mounts_file))
    return failed


def _failure(head: str, exc: BaseException, failed: list[str]) -> MountError:
    text = f"{head}. 쓰는 중 문제가 생겨 바꾼 것을 되돌렸어요: {_reason(exc)}"
    if failed:
        text += f". 되돌리지 못한 파일이 있어요: {', '.join(failed)}"
    return MountError(text)


def _apply_add(ctx: MountContext, plan: _Plan) -> tuple[str, ...]:
    written: list[tuple[Path, bytes, int]] = []
    records_written = False
    try:
        records = _rewrite(plan, written)                 # ① 작업 폴더 설정(막기)
        if written:
            _write_records(ctx, records)
            records_written = True
        _write_list(ctx, plan.after)                      # ② 목록(게이트에 열기)
    except (OSError, MountsFileError) as exc:
        failed = _rollback(ctx, written, plan.records if records_written else None, None)
        raise _failure("연결하지 않았어요", exc, failed) from exc
    return tuple(w.record.path for w in plan.rewrites)


def _apply_unprotect(ctx: MountContext, plan: _Plan) -> tuple[str, ...]:
    written: list[tuple[Path, bytes, int]] = []
    list_written = records_written = False
    try:
        _write_list(ctx, plan.after)                      # ① 목록에서 지우기
        list_written = True
        records = _rewrite(plan, written)                 # ② 작업 폴더 설정
        if written:
            _write_records(ctx, records)
            records_written = True
    except (OSError, MountsFileError) as exc:
        failed = _rollback(ctx, written, plan.records if records_written else None,
                           plan.before if list_written else None)
        raise _failure("보호를 풀지 않았어요", exc, failed) from exc
    return tuple(w.record.path for w in plan.rewrites)


def apply(ctx: MountContext, change: MountChange) -> MountResult:
    """다시 계산해 change와 같을 때만 쓴다(같은 op·이름·경로로 plan을 다시 해 confirm_token 비교). 다르면 MountError.
    쓰기 순서와 실패 시 되돌림은 맨 위 설명대로."""
    with _APPLY_LOCK, _locked(ctx):
        plan = _replan(ctx, change)
        if plan.change.confirm_token != change.confirm_token:
            raise MountError("확인하는 사이 폴더 목록이나 작업 폴더 설정이 바뀌었어요. 다시 확인해 주세요.")
        if change.op == "add":
            rewritten = _apply_add(ctx, plan)
        elif change.op == "remove":
            try:
                _write_list(ctx, plan.after)
            except (OSError, MountsFileError) as exc:
                raise MountError(f"게이트에서 빼지 못했어요: {_reason(exc)}") from exc
            rewritten = ()
        else:
            rewritten = _apply_unprotect(ctx, plan)
    notes: list[Note] = []
    if rewritten:
        notes.append("restart_session")
    if change.op == "add" and _global_setup():
        notes.append("rerun_setup")
    if change.op in ("add", "unprotect") and not any(w.state in ("ok", "stale") for w in plan.workspaces):
        notes.append("no_workspace")
    return MountResult(change, rewritten, tuple(notes))
