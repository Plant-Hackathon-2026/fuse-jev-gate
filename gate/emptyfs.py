"""Phase 0 확인용: 루트 디렉터리 하나만 있는 빈 읽기 전용 FUSE 파일시스템."""

import argparse
import errno
import os
import stat
import time

from mfusepy import FUSE, FuseOSError, Operations


class EmptyFS(Operations):
    use_ns = True

    def __init__(self) -> None:
        now_ns = time.time_ns()
        self._root_attr = {
            "st_mode": stat.S_IFDIR | 0o555,
            "st_nlink": 2,
            "st_uid": os.getuid(),
            "st_gid": os.getgid(),
            "st_atime": now_ns,
            "st_mtime": now_ns,
            "st_ctime": now_ns,
        }

    def getattr(self, path, fh=None):
        if path != "/":
            raise FuseOSError(errno.ENOENT)
        return self._root_attr

    def readdir(self, path, fh):
        if path != "/":
            raise FuseOSError(errno.ENOENT)
        return [".", ".."]


def main() -> None:
    parser = argparse.ArgumentParser(description="빈 FUSE 파일시스템을 마운트한다.")
    parser.add_argument("mountpoint")
    args = parser.parse_args()
    FUSE(EmptyFS(), args.mountpoint, foreground=True, ro=True, fsname="emptyfs")


if __name__ == "__main__":
    main()
