"""Unix 소켓으로 연결한 상대 프로세스의 PID·UID·실행 파일 경로를 얻는다."""

import ctypes
import ctypes.util
import os
import platform
import socket
import struct
from dataclasses import dataclass

_SYSTEM = platform.system()

# macOS <sys/un.h>
_SOL_LOCAL = 0
_LOCAL_PEERCRED = 0x001
_LOCAL_PEERPID = 0x002
_XUCRED_SIZE = 76  # struct xucred { u_int cr_version; uid_t cr_uid; short cr_ngroups; gid_t cr_groups[16]; }
_PROC_PIDPATHINFO_MAXSIZE = 4096


@dataclass(frozen=True)
class Peer:
    pid: int
    uid: int
    exe: str | None


def _exe_darwin(pid: int) -> str | None:
    libproc = ctypes.CDLL(ctypes.util.find_library("proc") or "/usr/lib/libproc.dylib")
    buf = ctypes.create_string_buffer(_PROC_PIDPATHINFO_MAXSIZE)
    if libproc.proc_pidpath(pid, buf, _PROC_PIDPATHINFO_MAXSIZE) <= 0:
        return None
    return os.fsdecode(buf.value)


def _exe_linux(pid: int) -> str | None:
    try:
        return os.readlink(f"/proc/{pid}/exe")
    except OSError:
        return None


def peer_of(sock: socket.socket) -> Peer:
    if _SYSTEM == "Darwin":
        pid = struct.unpack("i", sock.getsockopt(_SOL_LOCAL, _LOCAL_PEERPID, 4))[0]
        _, uid = struct.unpack_from("II", sock.getsockopt(_SOL_LOCAL, _LOCAL_PEERCRED, _XUCRED_SIZE))
        return Peer(pid, uid, _exe_darwin(pid))
    if _SYSTEM == "Linux":
        pid, uid, _ = struct.unpack("3i", sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
        return Peer(pid, uid, _exe_linux(pid))
    raise OSError(f"{_SYSTEM}에서는 소켓 상대 확인을 지원하지 않습니다")
