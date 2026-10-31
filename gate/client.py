"""게이트 데몬 클라이언트. gate CLI가 쓴다. ApprovalClient는 사용자 터미널의 gate approve / gate deny용이다."""

import base64
import errno
import os
import socket
from dataclasses import dataclass, field

from gate.protocol import GateError, decode, encode


@dataclass(frozen=True)
class ReadResult:
    data: bytes
    decision: str                                       # allow | mask
    masked: dict[str, int] = field(default_factory=dict)  # 가린 종류별 건수


class _SocketClient:
    def __init__(self, socket_path: str | os.PathLike) -> None:
        self.socket_path = os.fspath(socket_path)

    def request(self, op: str, **fields) -> dict:
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                sock.connect(self.socket_path)
                sock.sendall(encode({"op": op, **fields}))
                with sock.makefile("rb") as reader:
                    line = reader.readline()
        except (FileNotFoundError, ConnectionRefusedError) as exc:
            raise GateError(errno.ECONNREFUSED, f"게이트 데몬에 연결할 수 없습니다: {self.socket_path}") from exc
        if not line:
            raise GateError(errno.EIO, "게이트 데몬이 응답 없이 연결을 끊었습니다")
        response = decode(line)
        if not response.get("ok"):
            raise GateError.from_response(response)
        return response


class GateClient(_SocketClient):
    def ls(self, path: str = "/") -> list[dict]:
        return self.request("ls", path=path)["entries"]

    def stat(self, path: str) -> dict:
        return self.request("stat", path=path)["stat"]

    def read(self, path: str, offset: int = 0, size: int | None = None, purpose: str | None = None) -> ReadResult:
        response = self.request("cat", path=path, offset=offset, size=size, purpose=purpose)
        return ReadResult(base64.b64decode(response["data_b64"]), response["decision"], response.get("masked", {}))

    def cat(self, path: str, offset: int = 0, size: int | None = None, purpose: str | None = None) -> bytes:
        return self.read(path, offset, size, purpose).data

    def search(self, path: str, pattern: str, *, ignore_case: bool = False, purpose: str | None = None,
               max_results: int = 100) -> dict:
        response = self.request("search", path=path, pattern=pattern, ignore_case=ignore_case, purpose=purpose,
                                max_results=max_results)
        return {k: response[k] for k in ("matches", "skipped", "files_searched", "truncated")}


class ApprovalClient(_SocketClient):
    def pending(self) -> list[dict]:
        return self.request("pending")["pending"]

    def answer(self, id: str, answer: str) -> None:
        self.request("answer", id=id, answer=answer)
