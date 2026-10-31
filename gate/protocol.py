"""게이트 소켓의 요청·응답 형식. 한 줄에 JSON 하나씩 주고받는다.

요청: {"op": "cat", "path": "/상담/2026-03.txt", "offset": 0, "size": null, "purpose": "..."}
성공: {"ok": true, ...연산별 결과}
실패: {"ok": false, "errno": "ENOENT", "message": "..."}

연산별 결과
  ls      {"entries": [{"name", "type"}]}
  stat    {"stat": {"type", "size", "mode", "mtime_ns"}}          (크기는 원본 그대로. 정규식이 가린 부분은 길이가 같지만
          NER이 `[이름]`처럼 가리면 cat 내용 길이가 달라진다(3c). 끝까지 읽으려면 size 없이 읽거나 빈 응답이 올 때까지 읽는다)
  cat     {"data_b64", "decision": "allow" | "mask", "masked": {"종류": 건수}, "labeled": {"종류": 건수}}
          masked는 가린 종류별 건수(종류는 번호 종류 또는 NER 묶음)이고, labeled는 그중 규칙·NER이 `[이름]`처럼 종류 이름으로
          가린 것이다(나머지는 정규식이 같은 길이 `*`로 가렸다). 둘 다 mask일 때만 온다. labeled는 나중에 더했다(2026-11-01).
          NER이 정규식이 놓친 전화번호·이메일도 같은 칸(mobile·phone·email)으로 세서 종류만으로는 가린 방식을 알 수 없어서다.
          labeled가 없는 예전 데몬 응답은 받는 쪽(gate.cli)이 번호 종류는 `*`, 그 밖은 종류 이름으로 가렸다고 본다.
          차단(정책·Jev·사람 거부·승인 시간 초과)은 EACCES다. 사람 승인이 필요하면 데몬이 답을 기다린 뒤 응답한다.
  search  요청 {"path", "pattern", "ignore_case": false, "purpose", "max_results": 100}
          결과 {"matches": [{"path", "line", "text"}], "skipped": [{"path", "errno", "message"}],
                "files_searched", "truncated"}
          검사를 통과한 내용(허용·마스킹)에서만 글자 그대로 찾는다. 사람 승인이 필요한 파일은 묻지 않고 건너뛴다.

승인 소켓(사용자 터미널 전용, gate.daemon.ApprovalServer)
  pending  {"pending": [{"id", "path", "folder", "reason", "exe", "pid", "purpose", "findings",
                         "remaining_seconds", "session_minutes"}]}
  answer   요청 {"id", "answer": "once" | "session" | "deny"}  결과 {"answered": id}
           기다리는 요청이 없으면 ENOENT
"""

import errno
import json
import os

READ_OPS = frozenset({"ls", "stat", "cat", "search"})
# 1차 버전은 읽기 전용이라 쓰기 계열은 전부 EROFS로 거부한다.
WRITE_OPS = frozenset({
    "write", "create", "unlink", "rename", "truncate", "mkdir", "rmdir",
    "chmod", "chown", "symlink", "link", "setxattr", "removexattr",
})
MAX_LINE_BYTES = 64 * 1024


class GateError(Exception):
    """errno 하나로 표현되는 게이트 오류."""

    def __init__(self, err: int, message: str | None = None) -> None:
        self.errno = err
        self.message = message or os.strerror(err)
        super().__init__(self.message)

    @property
    def name(self) -> str:
        return errno.errorcode.get(self.errno, str(self.errno))

    def to_response(self) -> dict:
        return {"ok": False, "errno": self.name, "message": self.message}

    @classmethod
    def from_response(cls, response: dict) -> "GateError":
        err = getattr(errno, response.get("errno", ""), errno.EIO)
        return cls(err, response.get("message"))


def encode(message: dict) -> bytes:
    return json.dumps(message, ensure_ascii=False).encode("utf-8") + b"\n"


def decode(line: bytes) -> dict:
    try:
        message = json.loads(line)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GateError(errno.EINVAL, f"요청 형식이 올바르지 않습니다: {exc}") from exc
    if not isinstance(message, dict):
        raise GateError(errno.EINVAL, "요청은 JSON 객체여야 합니다")
    return message
