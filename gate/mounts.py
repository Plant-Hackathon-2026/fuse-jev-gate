"""원본 폴더 여러 개(Phase 9): 폴더 목록 파일·작업 폴더 등록 파일, 추가할 수 없는 폴더 검사, 이름 정하기,
데몬 쪽 변경 감지(MountsSource)와 저장소(MountedStore). 계획: plans/mount-ui-plan.md 4절.

파일 (정책 파일과 같은 폴더에 고정. `--mounts`·`GATE_MOUNTS`는 없다. 데몬·화면·작업 폴더 설정이 같은 파일을 보게)
  mounts.yaml       version: 1, mounts: [{name, path, added_at, added_by: ui|terminal, connected: bool}]
                    path는 resolve한 실제 경로. connected: false는 "보호만 남음"(게이트에는 안 보이고 직접 읽기 막기는 남음)
  workspaces.yaml   version: 1, workspaces: [{path, source, socket, policy, audit, gate_bin, settings_sha256, registered_at}]
                    `gate workspace init`이 적는다. 폴더를 추가·보호 해제하면 이 경로들로 작업 폴더 설정을 다시 만든다
  - 엄격하게 읽는다. 모르는 키, 이름 겹침(대소문자 무시·NFC), 경로 겹침(같음·안에 있음·감쌈), 개수 초과(MAX_MOUNTS),
    형식 오류는 MountsFileError다. 메시지에는 경로·이름을 넣지 않는다(데몬이 차단 이유로 에이전트에게 그대로 돌려준다).
  - 쓰기는 정책 파일과 같다. 같은 폴더의 임시 파일 → fsync → chmod 0600 → os.replace(심볼릭 링크를 따라 쓰지 않는다).
    쓰기 전에 읽기와 같은 검사를 한다(데몬이 거절할 파일을 쓰지 않게).

추가할 수 없는 폴더 (refusal, 계획 4.5. plan_add와 데몬 목록 읽기가 같이 쓴다)
  겹침 = 같음·안에 있음·감쌈. 경로 비교는 NFC + 대소문자 무시다(맥 기본 디스크는 대소문자를 가리지 않는다.
  다르게 쓴 경로로 검사를 피하지 못하게 한다. 대소문자를 가리는 디스크에서는 더 많이 거절할 뿐이다).
  - `/`. DEEP_SYSTEM은 그 안도, SHALLOW_SYSTEM(/private, /Volumes, /Users)은 그 자체와 감싸는 폴더만
  - 등록된 작업 폴더(감싸는 폴더도: 막으면 에이전트가 자기 작업 폴더를 못 읽는다)
  - 게이트 데이터: 정책 파일·정책 기록 폴더·감사 로그 폴더·소켓 폴더·mounts.yaml·workspaces.yaml
  - gate 프로그램 폴더: 저장소(소스에서 import했으면)·sys.prefix·sys.base_prefix·등록에 적힌 gate_bin의 venv
  - ~/.claude, ~/.claude.json, CLAUDE_CONFIG_DIR
  - 기본 폴더, 이미 있는 항목
  - require_dir면 폴더가 아니거나 읽을 수 없는 것. 경로에 `*?[]`·제어 문자가 있는 것
  사용자 이유(message)는 해요체 한 줄, 데몬 경고에 쓰는 겹친 곳(brief)은 명사구다.

데몬 (MountsSource → MountedStore)
  - 요청마다 두 파일의 (dev, ino, size, mtime_ns)를 보고 바뀌었으면 다시 읽는다(PolicySource와 같다).
  - 다시 읽을 때 refusal을 다시 한다(목록 파일은 화면을 거치지 않고도 바뀔 수 있다). 걸리는 항목은 빼고 stderr에
    한 줄 남긴다. 경고는 내용이 바뀔 때만 다시 쓴다.
  - 파일이 잘못되었으면 내용 읽기를 모두 EACCES로 막는다. 목록·stat은 기본 폴더만 보여 준다. 고쳐지면 다음 요청부터 정상.
  - MountedStore: 기본 폴더는 `/`, 연결된 항목은 `/<이름>/`. 첫 칸이 연결된 이름(대소문자 무시·NFC)이면 그 폴더의
    PlainStore로, 아니면 기본 폴더로 보낸다. 심볼릭 링크는 각 폴더 안에서만 따라간다.
    `/` 목록은 합치고, 같은 이름이면 추가 폴더가 이긴다(기본 쪽은 가려지고, 다른 길로 그 항목에 닿아도 ENOENT).
    사라진 폴더(또는 기록한 경로가 지금 다른 곳을 가리키는 폴더)는 `/` 목록에 없고 읽으면 ENOENT다.
    canonical은 `/<이름>/…`을 돌려준다. 그래서 경로 규칙·폴더 승인·감사 기록이 게이트 안 경로로 동작한다.
    실제 경로는 응답·감사 기록에 들어가지 않는다.
"""

from __future__ import annotations

import errno
import fcntl
import hashlib
import os
import sys
import tempfile
import threading
import unicodedata
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import yaml

import gate
from gate.paths import split_vpath
from gate.policy_change import history_dir
from gate.protocol import GateError
from gate.store import Entry, FileStat, PlainStore

MOUNTS_FILE_NAME = "mounts.yaml"
WORKSPACES_FILE_NAME = "workspaces.yaml"
MAX_MOUNTS = 20            # mounts.yaml 항목 수(보호만 남은 것 포함) 최대
FILE_VERSION = 1
MAX_LIST_FILE_BYTES = 256 * 1024
PATTERN_CHARS = frozenset("*?[]")
ADDED_BY = ("ui", "terminal")

# 그 안도 거부하는 시스템 폴더. /etc·/tmp·/var는 resolve하면 /private/… 가 된다
DEEP_SYSTEM = tuple(Path(p) for p in ("/System", "/Library", "/usr", "/bin", "/sbin", "/etc", "/private/etc",
                                      "/Applications"))
# 그 자체와 감싸는 폴더만 거부한다(/private/tmp/…, /private/var/folders/…, /Volumes/외장/…, /Users/나/… 는 된다)
SHALLOW_SYSTEM = tuple(Path(p) for p in ("/private", "/Volumes", "/Users"))

_MOUNT_KEYS = ("name", "path", "added_at", "added_by", "connected")
_WORKSPACE_KEYS = ("path", "source", "socket", "policy", "audit", "gate_bin", "settings_sha256", "registered_at")
_BIDI = frozenset("‪‫‬‭‮⁦⁧⁨⁩‎‏")


class MountsFileError(Exception):
    """목록 파일·등록 파일을 읽을 수 없거나 형식이 틀렸다. 메시지는 합쇼체이고 경로·이름을 담지 않는다."""


# --- 기록 ---

@dataclass(frozen=True)
class MountEntry:
    name: str
    path: str
    added_at: str
    added_by: str          # ui | terminal
    connected: bool

    def to_dict(self) -> dict:
        return {"name": self.name, "path": self.path, "added_at": self.added_at, "added_by": self.added_by,
                "connected": self.connected}


@dataclass(frozen=True)
class WorkspaceRecord:
    path: str
    source: str
    socket: str
    policy: str
    audit: str
    gate_bin: str
    settings_sha256: str
    registered_at: str

    def to_dict(self) -> dict:
        return {key: getattr(self, key) for key in _WORKSPACE_KEYS}


def mounts_file_for(policy: str | os.PathLike) -> Path:
    return Path(policy).expanduser().absolute().parent / MOUNTS_FILE_NAME


def workspaces_file_for(policy: str | os.PathLike) -> Path:
    return Path(policy).expanduser().absolute().parent / WORKSPACES_FILE_NAME


def now_iso() -> str:
    """기록에 남기는 시각(ISO 8601, 이 컴퓨터 시간대, 초 단위)."""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# --- 이름·경로 규칙 ---

def nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def name_key(name: str) -> str:
    """이름 비교 값(NFC + 대소문자 무시)."""
    return nfc(name).casefold()


def has_control(text: str) -> bool:
    return any(unicodedata.category(c) in ("Cc", "Zl", "Zp") or c in _BIDI for c in text)


def name_problem(name: str) -> str | None:
    """게이트 안 이름으로 쓸 수 없으면 그 이유(명사구)."""
    if not name or not name.strip():
        return "빈 이름"
    if name in (".", ".."):
        return "'.'이나 '..'"
    if "/" in name:
        return "'/'가 든 이름"
    if has_control(name):
        return "제어 문자가 든 이름"
    return None


def path_problem(text: object) -> str | None:
    """기록에 적힌 경로로 쓸 수 없으면 그 이유(명사구). 있는지는 보지 않는다."""
    if not isinstance(text, str) or not text:
        return "문자열이 아닌 경로"
    if not text.startswith("/") or text.startswith("//") or os.path.normpath(text) != text:
        return "정규화된 절대 경로가 아닌 경로"
    if PATTERN_CHARS & set(text):
        return "*, ?, [, ]가 든 경로"
    if has_control(text):
        return "제어 문자가 든 경로"
    return None


def path_key(path: str | os.PathLike) -> tuple[str, ...]:
    return tuple(nfc(part).casefold() for part in Path(path).parts)


def relation(a: str | os.PathLike, b: str | os.PathLike) -> str | None:
    """a가 b와 같으면 same, b 안이면 inside, b를 감싸면 contains, 겹치지 않으면 None. NFC + 대소문자 무시로 비교한다."""
    ka, kb = path_key(a), path_key(b)
    if ka == kb:
        return "same"
    if len(ka) > len(kb) and ka[:len(kb)] == kb:
        return "inside"
    if len(kb) > len(ka) and kb[:len(ka)] == ka:
        return "contains"
    return None


def disk_path(path: str | os.PathLike, *, strict: bool = False) -> Path:
    """resolve한 경로를 디스크에 적힌 표기로 바꾼다(맥: 대소문자·NFC/NFD까지, F_GETPATH).

    맥 기본 디스크는 대소문자·NFC/NFD를 가리지 않고 찾아 주지만 resolve()는 입력한 표기를 그대로 돌려준다
    (`~/documents/가계부`를 치면 디스크의 `Documents`·NFD 이름과 글자가 다르다). 샌드박스 denyRead와 권한 규칙은 글자로
    맞추므로 디스크 표기로 적는다. 있는 데까지만 바꾸고(가장 깊은 있는 폴더는 F_GETPATH, 그 바로 아래 한 칸은 목록에서
    대소문자·NFC만 다른 이름), 나머지와 F_GETPATH가 없는 OS는 resolve 결과 그대로다. strict면 없을 때 FileNotFoundError.
    """
    real = Path(path).expanduser().resolve(strict=strict)
    if not hasattr(fcntl, "F_GETPATH"):
        return real
    folder, rest = real, []
    while not folder.is_dir():
        if folder.parent == folder:
            return real
        rest.append(folder.name)
        folder = folder.parent
    try:
        fd = os.open(folder, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        try:
            raw = fcntl.fcntl(fd, fcntl.F_GETPATH, bytes(1024))
        finally:
            os.close(fd)
    except OSError:
        return real
    canonical = Path(os.fsdecode(raw.split(b"\0", 1)[0]))
    if not canonical.is_absolute() or path_key(canonical) != path_key(folder):
        return real  # 다른 곳(펌링크 등)을 돌려주면 쓰지 않는다
    rest.reverse()
    if rest:
        try:
            names = os.listdir(canonical)
        except OSError:
            names = []
        same = [n for n in names if name_key(n) == name_key(rest[0])]
        if rest[0] not in names and len(same) == 1:
            rest[0] = same[0]
    return canonical.joinpath(*rest)


def choose_name(folder_name: str, taken: Iterable[str]) -> str:
    """폴더 이름(NFC). taken(기본 폴더 맨 위 항목·다른 항목 이름)과 겹치면 뒤에 " 2", " 3"을 붙인다."""
    base = nfc(folder_name)
    keys = {name_key(t) for t in taken}
    if name_key(base) not in keys:
        return base
    n = 2
    while name_key(f"{base} {n}") in keys:
        n += 1
    return f"{base} {n}"


# --- 파일 읽기·쓰기 ---

def _load_items(path: Path, what: str, key: str) -> list:
    try:
        with open(path, "rb") as f:
            raw = f.read(MAX_LIST_FILE_BYTES + 1)
    except FileNotFoundError:
        return []
    except OSError as exc:
        raise MountsFileError(f"{what}을 읽을 수 없습니다: {errno.errorcode.get(exc.errno, exc.errno)}") from exc
    if len(raw) > MAX_LIST_FILE_BYTES:
        raise MountsFileError(f"{what}이 너무 큽니다 ({MAX_LIST_FILE_BYTES} 바이트 이하)")
    try:
        data = yaml.safe_load(raw.decode("utf-8"))
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
        raise MountsFileError(f"{what} 형식이 올바르지 않습니다: {type(exc).__name__}") from exc
    if data is None:
        return []
    if not isinstance(data, Mapping) or set(data) - {"version", key}:
        raise MountsFileError(f"{what}에는 version과 {key}만 있어야 합니다")
    if data.get("version") != FILE_VERSION:
        raise MountsFileError(f"{what} version은 {FILE_VERSION}이어야 합니다")
    items = data.get(key)
    if items is None:
        return []
    if not isinstance(items, list):
        raise MountsFileError(f"{what}의 {key}는 목록이어야 합니다")
    return items


def _check_keys(item: object, keys: Sequence[str], where: str) -> Mapping:
    if not isinstance(item, Mapping):
        raise MountsFileError(f"{where}이 객체가 아닙니다")
    unknown = [k for k in item if k not in keys]
    if unknown:
        raise MountsFileError(f"{where}에 모르는 키가 있습니다")
    missing = [k for k in keys if k not in item]
    if missing:
        raise MountsFileError(f"{where}에 {', '.join(missing)}이(가) 없습니다")
    return item


def _time_text(value: object, where: str, key: str) -> str:
    if isinstance(value, (datetime, date)):  # 따옴표 없이 손으로 적은 시각은 YAML이 날짜로 읽는다
        return value.isoformat()
    if not isinstance(value, str) or not value or has_control(value):
        raise MountsFileError(f"{where}의 {key}가 올바르지 않습니다")
    return value


def _mount_from(item: object, i: int) -> MountEntry:
    where = f"mounts {i + 1}번 항목"
    item = _check_keys(item, _MOUNT_KEYS, where)
    name = item["name"]
    if not isinstance(name, str):
        raise MountsFileError(f"{where}의 name이 문자열이 아닙니다")
    name = nfc(name)
    problem = name_problem(name)
    if problem:
        raise MountsFileError(f"{where}의 name을 쓸 수 없습니다({problem})")
    problem = path_problem(item["path"])
    if problem:
        raise MountsFileError(f"{where}의 path를 쓸 수 없습니다({problem})")
    if item["added_by"] not in ADDED_BY:
        raise MountsFileError(f"{where}의 added_by는 {' | '.join(ADDED_BY)} 중 하나여야 합니다")
    if not isinstance(item["connected"], bool):
        raise MountsFileError(f"{where}의 connected는 true나 false여야 합니다")
    return MountEntry(name, item["path"], _time_text(item["added_at"], where, "added_at"), item["added_by"],
                      item["connected"])


def check_mounts(entries: Sequence[MountEntry]) -> None:
    """항목 사이의 규칙(개수, 이름 겹침, 경로 겹침)."""
    if len(entries) > MAX_MOUNTS:
        raise MountsFileError(f"폴더는 {MAX_MOUNTS}개까지 둘 수 있습니다(보호만 남은 것 포함)")
    for i, a in enumerate(entries):
        for j in range(i):
            b = entries[j]
            if name_key(a.name) == name_key(b.name):
                raise MountsFileError(f"mounts {j + 1}번과 {i + 1}번 항목의 이름이 겹칩니다")
            if relation(a.path, b.path) is not None:
                raise MountsFileError(f"mounts {j + 1}번과 {i + 1}번 항목의 경로가 겹칩니다")


def read_mounts(path: str | os.PathLike) -> tuple[MountEntry, ...]:
    """폴더 목록 파일. 없으면 빈 묶음. 잘못되었으면 MountsFileError."""
    items = _load_items(Path(path), "폴더 목록 파일", "mounts")
    entries = tuple(_mount_from(item, i) for i, item in enumerate(items))
    check_mounts(entries)
    return entries


def _workspace_from(item: object, i: int) -> WorkspaceRecord:
    where = f"workspaces {i + 1}번 항목"
    item = _check_keys(item, _WORKSPACE_KEYS, where)
    for key in ("path", "source", "socket", "policy", "audit", "gate_bin"):
        problem = path_problem(item[key])
        if problem:
            raise MountsFileError(f"{where}의 {key}를 쓸 수 없습니다({problem})")
    digest = item["settings_sha256"]
    if not isinstance(digest, str) or len(digest) != 64 or set(digest) - set("0123456789abcdef"):
        raise MountsFileError(f"{where}의 settings_sha256이 올바르지 않습니다")
    values = {key: item[key] for key in _WORKSPACE_KEYS}
    values["registered_at"] = _time_text(item["registered_at"], where, "registered_at")
    return WorkspaceRecord(**values)


def check_workspaces(records: Sequence[WorkspaceRecord]) -> None:
    for i, a in enumerate(records):
        for j in range(i):
            if path_key(a.path) == path_key(records[j].path):
                raise MountsFileError(f"workspaces {j + 1}번과 {i + 1}번 항목의 작업 폴더가 같습니다")


def read_workspaces(path: str | os.PathLike) -> tuple[WorkspaceRecord, ...]:
    """작업 폴더 등록 파일. 없으면 빈 묶음. 잘못되었으면 MountsFileError."""
    items = _load_items(Path(path), "작업 폴더 등록 파일", "workspaces")
    records = tuple(_workspace_from(item, i) for i, item in enumerate(items))
    check_workspaces(records)
    return records


def write_private(path: Path, data: bytes, mode: int = 0o600) -> None:
    """같은 폴더의 임시 파일 → fsync → chmod → os.replace. path가 심볼릭 링크면 링크를 바꾼다(따라 쓰지 않는다)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


_MOUNTS_HEADER = ("# PLANT 폴더 목록. `plant mount`나 설정 화면(plant ui)으로 바꾼다(직접 고치지 않아도 된다).\n"
                  "# connected: false는 PLANT에서 뺐지만 작업 폴더 설정의 직접 읽기 막기는 남은 폴더다.\n")
_WORKSPACES_HEADER = ("# PLANT 작업 폴더 등록. `plant workspace init`이 적는다. 폴더를 연결·보호 해제하면\n"
                      "# 여기 적힌 작업 폴더의 .claude/settings.json을 지금 목록으로 다시 쓴다.\n")


def _dump(header: str, key: str, items: list[dict]) -> bytes:
    body = {"version": FILE_VERSION, key: items}
    return (header + yaml.safe_dump(body, allow_unicode=True, sort_keys=False)).encode("utf-8")


def write_mounts(path: str | os.PathLike, entries: Sequence[MountEntry]) -> None:
    """검사한 뒤 0600으로 원자적으로 쓴다. 검사에 걸리면 MountsFileError(쓰지 않음)."""
    entries = tuple(_mount_from(e.to_dict(), i) for i, e in enumerate(entries))
    check_mounts(entries)
    write_private(Path(path), _dump(_MOUNTS_HEADER, "mounts", [e.to_dict() for e in entries]))


def write_workspaces(path: str | os.PathLike, records: Sequence[WorkspaceRecord]) -> None:
    records = tuple(_workspace_from(r.to_dict(), i) for i, r in enumerate(records))
    check_workspaces(records)
    write_private(Path(path), _dump(_WORKSPACES_HEADER, "workspaces", [r.to_dict() for r in records]))


# --- 추가할 수 없는 폴더 ---

@dataclass(frozen=True)
class Refusal:
    message: str   # 사용자에게 보여 줄 해요체 한 줄
    brief: str     # 데몬 경고에 쓰는 명사구(겹친 곳)


@dataclass(frozen=True)
class Guard:
    """추가할 수 없는 폴더를 정하는 경로들. 모두 resolve(strict=False)한 절대 경로."""

    workspaces: tuple[Path, ...] = ()
    gate_data: tuple[tuple[str, Path], ...] = ()   # (이름, 경로)
    programs: tuple[Path, ...] = ()
    claude: tuple[Path, ...] = ()
    bases: tuple[Path, ...] = ()
    mounts: tuple[MountEntry, ...] = ()


def _real(path: str | os.PathLike) -> Path:
    return Path(path).expanduser().resolve()


def _unique(paths: Iterable[Path]) -> tuple[Path, ...]:
    seen: dict[tuple[str, ...], Path] = {}
    for p in paths:
        seen.setdefault(path_key(p), p)
    return tuple(seen.values())


def venv_of(gate_bin: str | os.PathLike) -> Path:
    """gate 실행 파일이 든 venv(…/bin/gate의 …, pyvenv.cfg가 있을 때). 아니면 실행 파일이 든 폴더."""
    real = _real(gate_bin)
    folder = real.parent
    if folder.name in ("bin", "Scripts") and (folder.parent / "pyvenv.cfg").is_file():
        return folder.parent
    return folder


def program_dirs(gate_bins: Iterable[str | os.PathLike] = ()) -> tuple[Path, ...]:
    """gate 프로그램이 있는 폴더. 막으면 에이전트가 gate를 실행하지 못한다."""
    package = Path(gate.__file__).resolve().parent
    repo = package.parent
    dirs = [repo if (repo / "pyproject.toml").is_file() else package, _real(sys.prefix), _real(sys.base_prefix)]
    dirs += [venv_of(b) for b in gate_bins]
    return _unique(dirs)


def claude_paths() -> tuple[Path, ...]:
    home = Path.home()
    paths = [home / ".claude", home / ".claude.json"]
    if os.environ.get("CLAUDE_CONFIG_DIR"):
        paths.append(Path(os.environ["CLAUDE_CONFIG_DIR"]))
    return _unique(_real(p) for p in paths)


def gate_data_paths(policy: str | os.PathLike, sockets: Iterable[str | os.PathLike | None] = (),
                    audits: Iterable[str | os.PathLike | None] = ()) -> list[tuple[str, Path]]:
    policy = Path(policy).expanduser().absolute()
    found = [("정책 파일", policy), ("정책 기록 폴더", history_dir(policy)),
             ("폴더 목록 파일", policy.parent / MOUNTS_FILE_NAME),
             ("작업 폴더 등록 파일", policy.parent / WORKSPACES_FILE_NAME)]
    found += [("소켓 폴더", Path(s).expanduser().absolute().parent) for s in sockets if s]
    found += [("감사 로그 폴더", Path(a).expanduser().absolute().parent) for a in audits if a]
    return [(what, _real(p)) for what, p in found]


def build_guard(*, policy: str | os.PathLike, sockets: Iterable[str | os.PathLike | None] = (),
                audits: Iterable[str | os.PathLike | None] = (), sources: Iterable[str | os.PathLike] = (),
                records: Sequence[WorkspaceRecord] = (), mounts: Sequence[MountEntry] = ()) -> Guard:
    data = gate_data_paths(policy, sockets, audits)
    for r in records:
        data += gate_data_paths(r.policy, [r.socket], [r.audit])
    unique: dict[tuple[str, ...], tuple[str, Path]] = {}
    for what, p in data:
        unique.setdefault(path_key(p), (what, p))
    return Guard(
        workspaces=_unique(_real(r.path) for r in records),
        gate_data=tuple(unique.values()),
        programs=program_dirs(r.gate_bin for r in records),
        claude=claude_paths(),
        bases=_unique([*(_real(s) for s in sources), *(_real(r.source) for r in records)]),
        mounts=tuple(mounts),
    )


def refusal(path: Path, guard: Guard, *, require_dir: bool = True) -> Refusal | None:
    """path(resolve한 실제 경로)를 연결할 수 없으면 그 이유. 연결할 수 있으면 None."""
    text = str(path)
    if PATTERN_CHARS & set(text):
        return Refusal("경로에 *, ?, [, ]가 든 폴더는 연결할 수 없어요. 보호 규칙에서 패턴으로 읽혀요.",
                       "경로의 패턴 문자(*, ?, [, ])")
    if has_control(text):
        return Refusal("경로에 제어 문자가 든 폴더는 연결할 수 없어요.", "경로의 제어 문자")
    if not path.is_absolute():
        return Refusal("절대 경로가 아니에요.", "절대 경로가 아님")
    if require_dir:
        if not path.exists():
            return Refusal("폴더를 찾을 수 없어요.", "없는 폴더")
        if not path.is_dir():
            return Refusal("폴더가 아니에요. 폴더만 연결할 수 있어요.", "폴더가 아님")
        try:
            with os.scandir(path):
                pass
        except OSError:
            return Refusal("폴더를 읽을 수 없어요. 이 프로그램에 그 폴더를 읽을 권한이 있는지 확인하세요.", "읽을 수 없는 폴더")
    if path_key(path) == path_key("/"):
        return Refusal("디스크 전체(/)는 연결할 수 없어요.", "디스크 전체(/)")
    for system in DEEP_SYSTEM:
        rel = relation(path, system)
        if rel in ("same", "inside"):
            return Refusal(f"시스템 폴더({system})는 연결할 수 없어요.", f"시스템 폴더 {system}")
        if rel == "contains":
            return Refusal(f"시스템 폴더({system})를 담은 폴더는 연결할 수 없어요.", f"시스템 폴더 {system}")
    for system in SHALLOW_SYSTEM:
        if relation(path, system) in ("same", "contains"):
            return Refusal(f"{system} 전체는 연결할 수 없어요. 그 안의 폴더를 골라 주세요.", f"시스템 폴더 {system}")
    for ws in guard.workspaces:
        rel = relation(path, ws)
        if rel == "same":
            return Refusal("에이전트 작업 폴더는 연결할 수 없어요. 에이전트가 직접 바꿀 수 있는 곳이에요.", "작업 폴더")
        if rel == "inside":
            return Refusal("에이전트 작업 폴더 안의 폴더는 연결할 수 없어요. 에이전트가 직접 바꿀 수 있는 곳이에요.",
                           "작업 폴더")
        if rel == "contains":
            return Refusal("작업 폴더를 감싸는 폴더는 연결할 수 없어요. 에이전트가 자기 작업 폴더를 못 읽게 돼요.",
                           "작업 폴더")
    for what, p in guard.gate_data:
        rel = relation(path, p)
        if rel in ("same", "inside"):
            return Refusal(f"PLANT 데이터({what})는 연결할 수 없어요.", what)
        if rel == "contains":
            return Refusal(f"PLANT 데이터({what})를 담은 폴더는 연결할 수 없어요. PLANT가 자기 설정을 내주게 돼요.", what)
    for p in guard.programs:
        rel = relation(path, p)
        if rel in ("same", "inside"):
            return Refusal("PLANT 프로그램 폴더는 연결할 수 없어요. 막으면 에이전트가 plant를 실행하지 못해요.",
                           "PLANT 프로그램 폴더")
        if rel == "contains":
            return Refusal("PLANT 프로그램 폴더를 담은 폴더는 연결할 수 없어요. 막으면 에이전트가 plant를 실행하지 못해요.",
                           "PLANT 프로그램 폴더")
    for p in guard.claude:
        rel = relation(path, p)
        if rel in ("same", "inside"):
            return Refusal("Claude 설정 폴더(~/.claude)는 연결할 수 없어요.", "Claude 설정")
        if rel == "contains":
            return Refusal("Claude 설정(~/.claude)을 담은 폴더는 연결할 수 없어요.", "Claude 설정")
    for base in guard.bases:
        rel = relation(path, base)
        if rel == "same":
            return Refusal("기본 폴더는 이미 PLANT에 연결되어 있어요.", "기본 폴더")
        if rel == "inside":
            return Refusal("기본 폴더 안의 폴더는 이미 PLANT로 보여요.", "기본 폴더")
        if rel == "contains":
            return Refusal("기본 폴더를 감싸는 폴더는 연결할 수 없어요. 연결이 겹쳐요.", "기본 폴더")
    for m in guard.mounts:
        rel = relation(path, m.path)
        state = "이미 연결된 폴더" if m.connected else "보호만 남은 폴더"
        if rel == "same":
            return Refusal("이미 연결된 폴더예요." if m.connected else f"보호만 남은 폴더 '{m.name}'이에요.", state)
        if rel == "inside":
            return Refusal(f"{state} '{m.name}' 안의 폴더예요. 연결이 겹쳐요.", state)
        if rel == "contains":
            return Refusal(f"{state} '{m.name}'을(를) 감싸는 폴더는 연결할 수 없어요. 연결이 겹쳐요.", state)
    return None


# --- 데몬: 변경 감지 ---

@dataclass(frozen=True)
class MountsState:
    """데몬이 지금 쓰는 목록. mounts는 게이트에 보일 항목(connected이고 검사를 통과한 것)."""

    mounts: tuple[MountEntry, ...] = ()
    error: str | None = None
    excluded: tuple[tuple[MountEntry, str], ...] = ()   # (항목, 겹친 곳)

    def find(self, name: str) -> MountEntry | None:
        key = name_key(name)
        return next((m for m in self.mounts if name_key(m.name) == key), None)


def _signature(path: Path) -> tuple | None:
    try:
        st = os.stat(path)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise MountsFileError(f"파일 정보를 읽을 수 없습니다: {errno.errorcode.get(exc.errno, exc.errno)}") from exc
    return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns)


class MountsSource:
    """요청마다 목록 파일·등록 파일이 바뀌었는지 보고, 바뀌었으면 다시 읽고 검사한다. 여러 스레드에서 불러도 된다."""

    def __init__(self, policy: str | os.PathLike, *, source: str | os.PathLike,
                 socket: str | os.PathLike | None = None, audit: str | os.PathLike | None = None,
                 warn: Callable[[str], None] | None = None) -> None:
        self.policy = Path(policy).expanduser().absolute()
        self.mounts_file = self.policy.parent / MOUNTS_FILE_NAME
        self.workspaces_file = self.policy.parent / WORKSPACES_FILE_NAME
        self.source = _real(source)
        self.socket = socket
        self.audit = audit
        self.warn = warn if warn is not None else (lambda _: None)
        self._lock = threading.Lock()
        self._signature: object = object()  # 처음에는 무엇과도 같지 않다
        self._state = MountsState()
        self._warnings: tuple[str, ...] = ()
        self._loaded = False

    def current(self) -> MountsState:
        with self._lock:
            try:
                signature: object = (_signature(self.mounts_file), _signature(self.workspaces_file))
            except MountsFileError as exc:
                signature = ("error", str(exc))
            if signature != self._signature:
                self._signature = signature
                state = MountsState(error=signature[1]) if signature[0] == "error" else self._load()
                self._set(state)
            return self._state

    def _load(self) -> MountsState:
        try:
            entries = read_mounts(self.mounts_file)
            records = read_workspaces(self.workspaces_file)
        except MountsFileError as exc:
            return MountsState(error=str(exc))
        guard = build_guard(policy=self.policy, sockets=[self.socket], audits=[self.audit], sources=[self.source],
                            records=records)
        kept: list[MountEntry] = []
        excluded: list[tuple[MountEntry, str]] = []
        for entry in entries:
            if not entry.connected:
                continue
            why = refusal(Path(entry.path), guard, require_dir=False)
            if why is None:
                kept.append(entry)
            else:
                excluded.append((entry, why.brief))
        return MountsState(tuple(kept), None, tuple(excluded))

    def _set(self, state: MountsState) -> None:
        before = self._state
        self._state = state
        warnings: list[str] = []
        if state.error is not None:
            warnings.append(f"폴더 목록 파일 오류. 고칠 때까지 내용 읽기를 모두 차단합니다: {state.error}")
        for entry, brief in state.excluded:
            warnings.append(f"폴더 목록의 '{entry.name}'({entry.path})은(는) PLANT에 연결하지 않습니다 "
                            f"(연결할 수 없는 폴더: {brief})")
        if state.mounts:
            try:
                base_names = {name_key(n): n for n in os.listdir(self.source)}
            except OSError:
                base_names = {}
            for m in state.mounts:
                if name_key(m.name) in base_names:
                    warnings.append(f"추가 폴더 '{m.name}'이(가) 기본 폴더의 같은 이름 항목을 가립니다")
        if self._loaded and (state.mounts, state.error) != (before.mounts, before.error) and state.error is None:
            names = ", ".join(m.name for m in state.mounts) or "없음"
            self.warn(f"폴더 목록을 다시 읽었습니다. 추가 폴더 {len(state.mounts)}개: {names}")
        self._loaded = True
        if tuple(warnings) != self._warnings:
            for line in warnings:
                self.warn(line)
            self._warnings = tuple(warnings)


# --- 데몬: 저장소 ---

class MountedStore:
    """기본 폴더(`/`)와 추가 폴더(`/<이름>/`)를 한 이름 공간으로 보여 준다(Store 프로토콜)."""

    def __init__(self, base: str | os.PathLike | PlainStore, mounts: MountsSource) -> None:
        self.base = base if isinstance(base, PlainStore) else PlainStore(base)
        self.mounts = mounts
        self._stores: dict[str, PlainStore] = {}
        self._lock = threading.Lock()
        self._redirected: set[str] = set()

    @property
    def root(self) -> Path:
        """기본 폴더(시작 안내용)."""
        return self.base.root

    def _store(self, entry: MountEntry) -> PlainStore | None:
        """실제 폴더가 지금 있고 기록한 경로 그대로면 그 폴더의 PlainStore. 아니면 None(사라진 폴더)."""
        path = Path(entry.path)
        try:
            real = path.resolve(strict=True)
        except (OSError, RuntimeError):
            return None
        if real != path:
            if entry.path not in self._redirected:  # 기록한 뒤 심볼릭 링크로 바뀌었다
                self._redirected.add(entry.path)
                self.mounts.warn(f"추가 폴더 '{entry.name}'의 경로가 다른 곳을 가리켜 연결하지 않습니다")
            return None
        if not real.is_dir():
            return None
        with self._lock:
            store = self._stores.get(entry.path)
            if store is None:
                try:
                    store = PlainStore(path)
                except OSError:
                    return None
                self._stores[entry.path] = store
            return store

    def _route(self, vpath: str) -> tuple[MountsState, tuple[str, ...], MountEntry | None, str]:
        parts = split_vpath(vpath)
        state = self.mounts.current()
        if state.error is None and parts:
            entry = state.find(parts[0])
            if entry is not None:
                return state, parts, entry, "/" + "/".join(parts[1:])
        return state, parts, None, vpath

    def _mount(self, entry: MountEntry) -> PlainStore:
        store = self._store(entry)
        if store is None:
            raise GateError(errno.ENOENT)
        return store

    def _base_canonical(self, state: MountsState, vpath: str) -> str:
        """기본 폴더 쪽 실제 위치. 추가 폴더에 가려진 맨 위 항목으로 가면(심볼릭 링크 등) 없는 것으로 본다."""
        canonical = self.base.canonical(vpath)
        parts = split_vpath(canonical)
        if state.error is None and parts and state.find(parts[0]) is not None:
            raise GateError(errno.ENOENT)
        return canonical

    def list_dir(self, vpath: str) -> list[Entry]:
        state, parts, entry, inner = self._route(vpath)
        if entry is not None:
            return self._mount(entry).list_dir(inner)
        if parts:
            self._base_canonical(state, vpath)
            return self.base.list_dir(vpath)
        entries = self.base.list_dir("/")
        if state.error is not None or not state.mounts:
            return entries
        hidden = {name_key(m.name) for m in state.mounts}
        merged = [e for e in entries if name_key(e.name) not in hidden]
        merged += [Entry(m.name, "dir") for m in state.mounts if self._store(m) is not None]
        return sorted(merged, key=lambda e: e.name)

    def stat(self, vpath: str) -> FileStat:
        state, parts, entry, inner = self._route(vpath)
        if entry is not None:
            return self._mount(entry).stat(inner)
        if parts:
            self._base_canonical(state, vpath)
        return self.base.stat(vpath)

    def canonical(self, vpath: str) -> str:
        state, parts, entry, inner = self._route(vpath)
        if entry is None:
            return self._base_canonical(state, vpath)
        relative = self._mount(entry).canonical(inner)
        return "/" + entry.name + ("" if relative == "/" else relative)

    def read(self, vpath: str, offset: int = 0, size: int | None = None) -> bytes:
        state, parts, entry, inner = self._route(vpath)
        if state.error is not None:
            raise GateError(errno.EACCES, f"폴더 목록 파일에 문제가 있어 차단했습니다: {state.error}")
        if entry is not None:
            return self._mount(entry).read(inner, offset, size)
        self._base_canonical(state, vpath)
        return self.base.read(vpath, offset, size)
