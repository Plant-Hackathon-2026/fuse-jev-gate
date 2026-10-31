"""게이트 데몬: Unix 소켓으로 요청을 받아 원본 저장소에서 결과를 돌려준다.

- ls·stat은 검사하지 않는다(명세 1절: 목록 조회는 막지 않고 내용 읽기만 검사한다).
- cat·search는 판단 파이프라인(gate.pipeline.Inspector)을 거친다. cat은 판단 결과(원본 또는 가린 내용)를 통째로 만든 뒤
  offset·size만큼 잘라 준다. 파일을 나눠 판단하면 경계에 걸친 번호를 놓치기 때문이다.
- 검사기를 주지 않으면 기본 정책(정책 파일 없음)과 기본 Jev 백엔드로 검사한다. 검사 없이 전달하는 경로는 없다.
- 사람 승인(Phase 5)은 cat을 처리하는 스레드에서 답을 기다린다(gate.approval). 사용자는 맥 대화상자나 승인 소켓으로 답한다.

승인 소켓(ApprovalServer)
  - 사용자 터미널의 gate approve / gate deny가 쓴다. 에이전트가 쓰는 게이트 소켓과 따로 둔다.
    게이트 소켓에 승인 요청이 있으면 에이전트가 스스로 승인할 수 있다. Phase 6 샌드박스는 게이트 소켓만 연다.
  - 요청: {"op": "pending"} → {"pending": [대기 요청 요약]}, {"op": "answer", "id", "answer": once|session|deny} → {"answered": id}
  - 같은 UID만 받는다(게이트 소켓과 같다).
"""

import base64
import errno
import os
import socket
import socketserver
import stat
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

from gate.approval import ANSWERS, Approvals, pending_summary
from gate.peer import Peer, peer_of
from gate.pipeline import Blocked, Inspector, Judgment, search
from gate.protocol import MAX_LINE_BYTES, READ_OPS, WRITE_OPS, GateError, decode, encode
from gate.store import Store

# sockaddr_un.sun_path 길이 제한 (macOS 104, Linux 108, 끝의 NUL 포함)
_MAX_SOCKET_PATH_BYTES = 103


@dataclass(frozen=True)
class RequestEvent:
    """요청 하나의 처리 결과. 원문은 담지 않는다 (감사 로그는 Phase 7)."""
    peer: Peer
    op: str | None
    path: str | None
    errno: str | None  # 성공이면 None
    elapsed_ms: float
    judgment: Judgment | None = None  # cat의 판단 요약 (원문·탐지 값 없음)


def _int_field(request: dict, name: str, default: int | None) -> int | None:
    value = request.get(name, default)
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise GateError(errno.EINVAL, f"{name}은 0 이상의 정수여야 합니다")
    return value


def _purpose(request: dict) -> str | None:
    purpose = request.get("purpose")
    if purpose is not None and not isinstance(purpose, str):
        raise GateError(errno.EINVAL, "purpose는 문자열이어야 합니다")
    return purpose


def handle_request(store: Store, inspector: Inspector, peer: Peer | None, request: dict) -> tuple[dict, Judgment | None]:
    """요청 하나를 처리해 (응답, 판단 요약)을 돌려준다. 차단은 Blocked(EACCES)로 던진다."""
    op = request.get("op")
    if op in WRITE_OPS:
        raise GateError(errno.EROFS)
    if op not in READ_OPS:
        raise GateError(errno.ENOSYS, f"알 수 없는 요청: {op}")
    path = request.get("path", "/")

    if op == "ls":
        return {"entries": [asdict(e) for e in store.list_dir(path)]}, None
    if op == "stat":
        return {"stat": asdict(store.stat(path))}, None
    if op == "search":
        result = search(
            inspector, peer, path, request.get("pattern"),
            ignore_case=bool(request.get("ignore_case", False)),
            purpose=_purpose(request),
            max_results=_int_field(request, "max_results", 100),
        )
        return result, None

    offset = _int_field(request, "offset", 0)
    size = _int_field(request, "size", None)
    verdict = inspector.inspect(peer, path, _purpose(request))
    if not verdict.readable:
        raise Blocked(verdict.judgment)
    data = verdict.data[offset:] if size is None else verdict.data[offset:offset + size]
    response = {"data_b64": base64.b64encode(data).decode("ascii"), "decision": verdict.judgment.action}
    if verdict.judgment.action == "mask":
        response["masked"] = dict(verdict.judgment.findings)
        response["labeled"] = dict(verdict.judgment.labeled)
    return response, verdict.judgment


class _Handler(socketserver.StreamRequestHandler):
    server: "GateServer"

    def handle(self) -> None:
        try:
            peer = peer_of(self.request)
        except OSError:
            return  # 연결하자마자 끊은 상대(예: 이미 실행 중인지 확인하는 두 번째 데몬)
        while line := self.rfile.readline(MAX_LINE_BYTES + 1):
            started = time.monotonic()
            request: dict = {}
            judgment: Judgment | None = None
            try:
                if len(line) > MAX_LINE_BYTES:
                    raise GateError(errno.E2BIG, "요청이 너무 깁니다")
                if peer.uid != self.server.allowed_uid:
                    raise GateError(errno.EACCES, "게이트 소유자와 다른 사용자입니다")
                request = decode(line)
                result, judgment = handle_request(self.server.store, self.server.inspector, peer, request)
                response = {"ok": True, **result}
                failed = None
            except GateError as exc:
                response = exc.to_response()
                failed = exc.name
                judgment = getattr(exc, "judgment", None)
            self.wfile.write(encode(response))
            self.server.notify(RequestEvent(
                peer, request.get("op"), request.get("path"), failed,
                (time.monotonic() - started) * 1000, judgment,
            ))
            if len(line) > MAX_LINE_BYTES:
                break


class _UnixServer(socketserver.ThreadingUnixStreamServer):
    """소켓 파일 준비(폴더 0700, 소켓 0600, 남은 소켓 정리)와 같은 UID 확인을 맡는다."""
    daemon_threads = True

    def __init__(self, socket_path: str | os.PathLike, handler: type, allowed_uid: int | None) -> None:
        self.socket_path = Path(socket_path)
        self.allowed_uid = os.getuid() if allowed_uid is None else allowed_uid
        _prepare_socket_path(self.socket_path)
        super().__init__(str(self.socket_path), handler)
        os.chmod(self.socket_path, 0o600)

    def server_close(self) -> None:
        super().server_close()
        self.socket_path.unlink(missing_ok=True)


class GateServer(_UnixServer):
    def __init__(
        self,
        socket_path: str | os.PathLike,
        store: Store,
        on_request: Callable[[RequestEvent], None] | None = None,
        allowed_uid: int | None = None,
        inspector: Inspector | None = None,
    ) -> None:
        self.store = store
        self.inspector = inspector if inspector is not None else Inspector(store)
        self.on_request = on_request
        super().__init__(socket_path, _Handler, allowed_uid)

    def notify(self, event: RequestEvent) -> None:
        if self.on_request is not None:
            self.on_request(event)


_NO_PENDING = "기다리는 승인 요청이 없습니다 (이미 답했거나 시간이 지났습니다)"


def handle_approval_request(approvals: Approvals, request: dict) -> dict:
    op = request.get("op")
    if op == "pending":
        return {"pending": [pending_summary(p) for p in approvals.pending()]}
    if op != "answer":
        raise GateError(errno.ENOSYS, f"알 수 없는 요청: {op}")
    id, answer = request.get("id"), request.get("answer")
    if not isinstance(id, str) or answer not in ANSWERS:
        raise GateError(errno.EINVAL, f"id는 문자열, answer는 {', '.join(ANSWERS)} 중 하나여야 합니다")
    try:
        if not approvals.answer(id, answer, "terminal"):
            raise GateError(errno.ENOENT, _NO_PENDING)
    except KeyError:
        raise GateError(errno.ENOENT, _NO_PENDING) from None
    except ValueError as exc:
        raise GateError(errno.EINVAL, str(exc)) from None
    return {"answered": id}


class _ApprovalHandler(socketserver.StreamRequestHandler):
    server: "ApprovalServer"

    def handle(self) -> None:
        try:
            peer = peer_of(self.request)
        except OSError:
            return  # 연결하자마자 끊은 상대(예: 이미 실행 중인지 확인하는 두 번째 데몬)
        while line := self.rfile.readline(MAX_LINE_BYTES + 1):
            try:
                if len(line) > MAX_LINE_BYTES:
                    raise GateError(errno.E2BIG, "요청이 너무 깁니다")
                if peer.uid != self.server.allowed_uid:
                    raise GateError(errno.EACCES, "게이트 소유자와 다른 사용자입니다")
                request = decode(line)
                response = {"ok": True, **handle_approval_request(self.server.approvals, request)}
                if request.get("op") == "answer":
                    self.server.notify(peer, request["id"], request["answer"])
            except GateError as exc:
                response = exc.to_response()
            self.wfile.write(encode(response))
            if len(line) > MAX_LINE_BYTES:
                break


class ApprovalServer(_UnixServer):
    """사용자 터미널(gate approve / gate deny)이 쓰는 승인 소켓."""

    def __init__(
        self,
        socket_path: str | os.PathLike,
        approvals: Approvals,
        on_answer: Callable[[Peer, str, str], None] | None = None,
        allowed_uid: int | None = None,
    ) -> None:
        self.approvals = approvals
        self.on_answer = on_answer
        super().__init__(socket_path, _ApprovalHandler, allowed_uid)

    def notify(self, peer: Peer, id: str, answer: str) -> None:
        if self.on_answer is not None:
            self.on_answer(peer, id, answer)


def _prepare_socket_path(path: Path) -> None:
    if len(os.fsencode(path)) > _MAX_SOCKET_PATH_BYTES:
        raise ValueError(f"소켓 경로가 너무 깁니다 ({_MAX_SOCKET_PATH_BYTES}바이트 이하): {path}")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not path.exists():
        return
    if not stat.S_ISSOCK(path.lstat().st_mode):
        raise FileExistsError(f"소켓이 아닌 파일이 이미 있습니다: {path}")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
        try:
            probe.connect(str(path))
        except OSError:
            path.unlink()  # 이전 데몬이 남긴 소켓
            return
    raise RuntimeError(f"게이트 데몬이 이미 실행 중입니다: {path}")
