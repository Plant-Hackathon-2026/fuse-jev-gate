"""감사 로그 (명세 3.8, Phase 7).

데몬이 요청 하나를 끝낼 때마다 넘기는 RequestEvent(gate.daemon)를 JSONL 파일에 한 줄씩 남긴다.

기록
  - 키와 순서는 to_record()가 만드는 그대로다. 값이 없으면 null이다.
    v, ts, pid, uid, exe, process, op, path, errno, decision, reason, stage, policy_version, content_hmac,
    findings, jev_label, jev_confidence, jev_calls, cached, approval, approved_by, elapsed_ms
  - decision: 판단이 있으면 judgment.action, 없으면(ls·stat·search·형식 오류) 성공 ok, 실패 error.
  - 원문, 탐지 값, 에이전트가 밝힌 목적(purpose)은 쓰지 않는다. 판단 요약(Judgment)에서도 위 키에 해당하는 필드만 고른다.
  - op·path가 문자열이 아니면(형식 오류 요청) null로 쓴다.
  - NaN·무한대는 엄격한 JSON에 없으므로 null로 쓴다.

파일 해시 (content_hmac, 2026-10-31 사용자 결정)
  - 원본 파일의 SHA-256을 그대로 쓰지 않고 HMAC-SHA256(키, SHA-256(내용))을 쓴다. 짧고 틀이 정해진 파일
    (예: "연락처 010-xxxx-xxxx")은 SHA-256만 보고 후보를 하나씩 넣어 원문을 맞혀 볼 수 있기 때문이다. 키가 없으면 맞혀 볼 수 없다.
  - 키는 설치마다 만드는 32바이트 난수다. 기본 위치는 감사 로그와 같은 폴더의 audit.key(0600)이고, 없으면 만든다.
    키가 같으면 같은 내용은 같은 값이 되어 기록끼리 비교할 수 있다. 키를 가진 사람은 content_hmac_of(키, 파일 내용)로
    어떤 파일이 기록의 그 파일인지 확인할 수 있다.
  - 키 파일도 O_NOFOLLOW로 열고, 길이가 32바이트가 아니면 ValueError다. 키를 잃으면 이전 기록과 비교할 수 없다(기록 자체는 읽힌다).

글자 처리
  - JSON은 ensure_ascii=False로 쓴다(한글이 그대로 보이게). 줄바꿈 같은 제어 문자는 JSON이 이스케이프하므로 한 줄이 유지된다.
  - UTF-8로 바꿀 수 없는 글자(짝 없는 서로게이트. surrogateescape로 푼 UTF-8이 아닌 파일 이름의 \\udc80~\\udcff 등)는
    UTF-8로 바꿀 때 backslashreplace로 `\\udcxx` 여섯 글자가 된다. 이런 글자는 JSON 문자열 안에만 나오므로 그대로 JSON의
    \\u 이스케이프가 되고, read_records()로 읽으면 원래 문자열로 돌아온다(os.fsencode로 원래 바이트를 얻는다).
    단, 짝이 맞는 서로게이트 두 글자(JSON 요청의 "\\ud83d\\ude00" 같은 것)는 읽을 때 한 글자로 합쳐진다.
  - U+0085·U+2028·U+2029도 \\u 이스케이프로 쓴다. 줄을 str.splitlines()로 나누는 도구에서도 한 줄이 기록 하나가 되게.

파일
  - 부모 폴더가 없으면 0700으로 만든다(중간 폴더 포함). 이미 있는 폴더는 건드리지 않는다.
  - O_WRONLY | O_APPEND | O_CREAT | O_NOFOLLOW | O_CLOEXEC, 0600으로 연다. 마지막 이름이 심볼릭 링크면 OSError다
    (로그 경로에 링크를 걸어 다른 파일에 쓰게 하는 것을 막는다). 이미 있는 파일은 권한을 바꾸지 않는다.
  - 여러 스레드가 불러도 된다. 기록 한 줄을 잠금 안에서 os.write로 한 번에 쓴다(드물게 덜 써지면 남은 바이트를 이어 쓴다).
    사용자 공간 버퍼가 없으므로 매 줄 커널에 넘어간다(데몬이 죽어도 남는다). fsync는 하지 않는다.
  - record()는 쓰기 실패(OSError)를 그대로 던진다. close() 뒤 record()는 ValueError다.
"""

import hashlib
import hmac
import json
import math
import os
import secrets
import threading
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from gate.daemon import RequestEvent
    from gate.pipeline import Judgment

AUDIT_SCHEMA_VERSION = 1
KEY_BYTES = 32
KEY_FILE_NAME = "audit.key"

_OPEN_FLAGS = os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC
_FILE_MODE = 0o600
_DIR_MODE = 0o700
_JUDGMENT_KEYS = ("reason", "stage", "policy_version", "content_hmac", "findings", "jev_label", "jev_confidence",
                  "jev_calls", "cached", "approval", "approved_by")
# JSON은 이스케이프하지 않지만 str.splitlines()는 줄바꿈으로 보는 글자
_LINE_BREAKS = {"\u0085": "\\u0085", "\u2028": "\\u2028", "\u2029": "\\u2029"}


def content_hmac(key: bytes, sha256_hex: str) -> str:
    """판단 요약의 content_sha256(16진수)을 HMAC-SHA256(키, SHA-256 바이트)로 바꾼다."""
    return hmac.new(key, bytes.fromhex(sha256_hex), hashlib.sha256).hexdigest()


def content_hmac_of(key: bytes, data: bytes) -> str:
    """파일 내용에서 바로 content_hmac을 만든다(기록과 파일을 맞춰 볼 때)."""
    return content_hmac(key, hashlib.sha256(data).hexdigest())


def to_record(event: "RequestEvent", now: datetime, key: bytes) -> dict:
    """이벤트 하나를 감사 기록(dict)으로 바꾼다. 순수 함수. now에는 시간대가 있어야 하고, key는 파일 해시용 HMAC 키다."""
    if now.utcoffset() is None:
        raise ValueError("now에는 시간대가 있어야 합니다")
    peer = event.peer
    exe = _text(peer.exe)
    judgment = event.judgment
    if judgment is not None:
        decision = judgment.action
    else:
        decision = "ok" if event.errno is None else "error"
    return {
        "v": AUDIT_SCHEMA_VERSION,
        "ts": now.isoformat(timespec="milliseconds"),
        "pid": peer.pid,
        "uid": peer.uid,
        "exe": exe,
        "process": (os.path.basename(exe) or None) if exe else None,
        "op": _text(event.op),
        "path": _text(event.path),
        "errno": event.errno,
        "decision": decision,
        **_judgment_fields(judgment, key),
        "elapsed_ms": _finite(round(event.elapsed_ms, 1)),
    }


def _judgment_fields(judgment: "Judgment | None", key: bytes) -> dict[str, Any]:
    if judgment is None:
        return dict.fromkeys(_JUDGMENT_KEYS)
    digest = judgment.content_sha256
    return {
        "reason": judgment.reason,
        "stage": judgment.stage,
        "policy_version": judgment.policy_version,
        "content_hmac": content_hmac(key, digest) if digest is not None else None,
        "findings": dict(judgment.findings),
        "jev_label": judgment.jev_label,
        "jev_confidence": _finite(judgment.jev_confidence),
        "jev_calls": judgment.jev_calls,
        "cached": judgment.cached,
        "approval": judgment.approval,
        "approved_by": judgment.approved_by,
    }


def _text(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _finite(value: float | None) -> float | None:
    return value if value is not None and math.isfinite(value) else None


def _encode_line(record: dict) -> bytes:
    """기록 하나를 JSONL 한 줄(끝에 \\n, UTF-8)로 바꾼다. 글자 처리는 모듈 설명을 본다."""
    text = json.dumps(record, ensure_ascii=False, allow_nan=False)
    for char, escaped in _LINE_BREAKS.items():
        text = text.replace(char, escaped)
    return (text + "\n").encode("utf-8", "backslashreplace")


class AuditLog:
    """JSONL 감사 로그 파일. 여러 스레드에서 불러도 된다. with 문으로도 쓸 수 있다."""

    def __init__(self, path: str | os.PathLike, key_path: str | os.PathLike | None = None) -> None:
        """key_path를 주지 않으면 같은 폴더의 audit.key를 쓴다(없으면 만든다)."""
        self.path = Path(path)
        self.key_path = Path(key_path) if key_path is not None else self.path.parent / KEY_FILE_NAME
        _make_private_dirs(self.path.parent)
        self._key = load_or_create_key(self.key_path)
        self._fd: int | None = os.open(self.path, _OPEN_FLAGS, _FILE_MODE)
        self._lock = threading.Lock()

    def record(self, event: "RequestEvent", *, now: datetime | None = None) -> None:
        line = _encode_line(to_record(event, now if now is not None else datetime.now().astimezone(), self._key))
        with self._lock:
            if self._fd is None:
                raise ValueError("닫힌 감사 로그에는 기록할 수 없습니다")
            _write_all(self._fd, line)

    def close(self) -> None:
        with self._lock:
            fd, self._fd = self._fd, None
        if fd is not None:
            os.close(fd)

    @property
    def closed(self) -> bool:
        return self._fd is None

    def __enter__(self) -> "AuditLog":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


def _make_private_dirs(directory: Path) -> None:
    missing = []
    while not directory.exists() and directory != directory.parent:
        missing.append(directory)
        directory = directory.parent
    for path in reversed(missing):
        try:
            path.mkdir(mode=_DIR_MODE)
        except FileExistsError:
            pass  # 동시에 만들어졌거나 끊어진 심볼릭 링크. 끊어진 링크면 파일을 열 때 실패한다


def load_or_create_key(path: str | os.PathLike) -> bytes:
    """HMAC 키를 읽는다. 없으면 32바이트 난수로 만들어 0600으로 저장한다. 링크면 OSError, 길이가 다르면 ValueError."""
    path = Path(path)
    _make_private_dirs(path.parent)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, _FILE_MODE)
    except FileExistsError:
        with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC), "rb") as file:
            key = file.read(KEY_BYTES + 1)
        if len(key) != KEY_BYTES:
            raise ValueError(f"감사 로그 키 파일이 {KEY_BYTES}바이트가 아닙니다: {path}") from None
        return key
    key = secrets.token_bytes(KEY_BYTES)
    with os.fdopen(fd, "wb") as file:
        file.write(key)
    return key


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        view = view[os.write(fd, view):]


def read_records(path: str | os.PathLike) -> list[dict]:
    """감사 로그를 읽어 기록 목록을 돌려준다(테스트와 나중의 조회 명령용).

    깨진 줄(UTF-8·JSON이 아니거나 JSON 객체가 아닌 줄, 빈 줄 포함)은 건너뛰지 않고 ValueError로 알린다. 메시지에는 줄 번호만 넣는다.
    """
    records = []
    with open(path, "rb") as file:
        for number, raw in enumerate(file, start=1):
            try:
                record = json.loads(raw.decode("utf-8"))
            except ValueError as exc:  # UnicodeDecodeError, json.JSONDecodeError
                raise ValueError(f"감사 로그 {number}번째 줄이 깨졌습니다: {path}") from exc
            if not isinstance(record, dict):
                raise ValueError(f"감사 로그 {number}번째 줄이 JSON 객체가 아닙니다: {path}")
            records.append(record)
    return records
