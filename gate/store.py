"""원본 저장소. 게이트 데몬만 원본을 읽고, 에이전트는 가상 경로로만 요청한다.

지금은 평문 폴더(PlainStore)만 있다. 나중에 암호화 저장소로 바꿔 끼울 수 있게 Store 인터페이스로 분리해 둔다.
EmptyStore는 아무것도 없는 `/`다. 데몬은 보통 이것 위에 사용자가 연결한 폴더만 `/<이름>/`으로 보여 준다
(gate.mounts.MountedStore. 2026-11-01 사용자 결정: 폴더를 연결하지 않으면 아무 문서도 읽을 수 없다).
"""

import errno
import os
import stat
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from gate.paths import resolve, split_vpath
from gate.protocol import GateError

FileType = Literal["file", "dir", "other"]
MAX_READ_BYTES = 16 * 1024 * 1024


@dataclass(frozen=True)
class Entry:
    name: str
    type: FileType


@dataclass(frozen=True)
class FileStat:
    type: FileType
    size: int
    mode: int
    mtime_ns: int


class Store(Protocol):
    def list_dir(self, vpath: str) -> list[Entry]: ...

    def stat(self, vpath: str) -> FileStat: ...

    def read(self, vpath: str, offset: int = 0, size: int | None = None) -> bytes: ...

    def canonical(self, vpath: str) -> str:
        """심볼릭 링크를 따라간 실제 위치의 가상 경로(`/상담/a.txt`). 경로 규칙을 실제 파일 기준으로도 보기 위해 쓴다."""
        ...


def _file_type(mode: int) -> FileType:
    if stat.S_ISREG(mode):
        return "file"
    if stat.S_ISDIR(mode):
        return "dir"
    return "other"


class PlainStore:
    def __init__(self, root: str | os.PathLike) -> None:
        self.root = Path(root).resolve(strict=True)
        if not self.root.is_dir():
            raise NotADirectoryError(self.root)

    def list_dir(self, vpath: str) -> list[Entry]:
        real = resolve(self.root, vpath)
        if not real.is_dir():
            raise GateError(errno.ENOTDIR)
        entries = []
        for child in sorted(real.iterdir(), key=lambda p: p.name):
            try:
                child_type = _file_type(resolve(self.root, str(child.relative_to(self.root))).stat().st_mode)
            except GateError:
                child_type = "other"  # 원본 밖을 가리키거나 깨진 심볼릭 링크
            entries.append(Entry(child.name, child_type))
        return entries

    def stat(self, vpath: str) -> FileStat:
        st = resolve(self.root, vpath).stat()
        return FileStat(_file_type(st.st_mode), st.st_size, stat.S_IMODE(st.st_mode), st.st_mtime_ns)

    def canonical(self, vpath: str) -> str:
        relative = resolve(self.root, vpath).relative_to(self.root).as_posix()
        return "/" if relative == "." else "/" + relative

    def read(self, vpath: str, offset: int = 0, size: int | None = None) -> bytes:
        if offset < 0 or (size is not None and size < 0):
            raise GateError(errno.EINVAL, "offset과 size는 0 이상이어야 합니다")
        real = resolve(self.root, vpath)
        if real.is_dir():
            raise GateError(errno.EISDIR)
        if not real.is_file():
            raise GateError(errno.EACCES, "일반 파일만 읽을 수 있습니다")
        remaining = max(real.stat().st_size - offset, 0)
        length = remaining if size is None else min(size, remaining)
        if length > MAX_READ_BYTES:
            raise GateError(errno.EFBIG, f"한 번에 {MAX_READ_BYTES} 바이트까지 읽을 수 있습니다")
        with open(real, "rb") as f:
            f.seek(offset)
            return f.read(length)


class EmptyStore:
    """아무것도 없는 읽기 전용 `/`. `/` 목록은 비어 있고, 그 밖의 경로는 모두 ENOENT, `/`를 읽으면 EISDIR이다.

    `plant serve`에 기본 폴더(숨은 개발용 --source)를 주지 않으면 이것이 `/`다. 연결한 폴더는 MountedStore가 그 위에 더한다.
    """

    root = None  # 실제 폴더가 없다(PlainStore.root와 같은 자리)

    def __init__(self) -> None:
        self._mtime_ns = time.time_ns()

    @staticmethod
    def _root_only(vpath: str) -> None:
        if split_vpath(vpath):
            raise GateError(errno.ENOENT)

    def list_dir(self, vpath: str) -> list[Entry]:
        self._root_only(vpath)
        return []

    def stat(self, vpath: str) -> FileStat:
        self._root_only(vpath)
        return FileStat("dir", 0, 0o555, self._mtime_ns)

    def canonical(self, vpath: str) -> str:
        self._root_only(vpath)
        return "/"

    def read(self, vpath: str, offset: int = 0, size: int | None = None) -> bytes:
        if offset < 0 or (size is not None and size < 0):
            raise GateError(errno.EINVAL, "offset과 size는 0 이상이어야 합니다")
        self._root_only(vpath)
        raise GateError(errno.EISDIR)
