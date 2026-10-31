"""가상 경로를 원본 폴더 안의 실제 경로로 바꾼다. 원본 폴더 밖으로 나가는 경로는 거부한다."""

import errno
from pathlib import Path

from gate.protocol import GateError


def split_vpath(vpath: str) -> tuple[str, ...]:
    """'/상담/a.txt' 같은 가상 경로를 구성 요소로 나눈다. '..'은 허용하지 않는다."""
    if not isinstance(vpath, str):
        raise GateError(errno.EINVAL, "경로는 문자열이어야 합니다")
    if "\x00" in vpath:
        raise GateError(errno.EINVAL, "경로에 NUL 문자가 있습니다")
    parts = tuple(p for p in vpath.split("/") if p not in ("", "."))
    if ".." in parts:
        raise GateError(errno.EACCES, "경로에 '..'을 쓸 수 없습니다")
    return parts


def resolve(root: Path, vpath: str) -> Path:
    """가상 경로를 실제 경로로 바꾼다. 심볼릭 링크를 따라간 결과가 root 밖이면 EACCES."""
    candidate = root.joinpath(*split_vpath(vpath))
    try:
        real = candidate.resolve(strict=True)
    except FileNotFoundError as exc:
        raise GateError(errno.ENOENT) from exc
    except RuntimeError as exc:  # Python 3.12의 심볼릭 링크 순환
        raise GateError(errno.ELOOP) from exc
    except OSError as exc:
        raise GateError(exc.errno or errno.EIO) from exc
    if real != root and root not in real.parents:
        raise GateError(errno.EACCES, "원본 폴더 밖을 가리킵니다")
    return real
