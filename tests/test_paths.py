import errno

import pytest

from gate.paths import resolve, split_vpath
from gate.protocol import GateError


@pytest.fixture
def root(tmp_path):
    source = tmp_path / "source"
    (source / "상담").mkdir(parents=True)
    (source / "상담" / "2026-03.txt").write_text("상담 기록\n", encoding="utf-8")
    (tmp_path / "outside.txt").write_text("원본 폴더 밖\n", encoding="utf-8")
    (source / "inner").symlink_to(source / "상담" / "2026-03.txt")
    (source / "escape").symlink_to(tmp_path / "outside.txt")
    (source / "escape_dir").symlink_to(tmp_path)
    (source / "broken").symlink_to(tmp_path / "없는파일")
    return source.resolve()


@pytest.mark.parametrize("vpath, expected", [
    ("/", ()),
    ("", ()),
    ("/상담/2026-03.txt", ("상담", "2026-03.txt")),
    ("상담//./2026-03.txt", ("상담", "2026-03.txt")),
])
def test_split_vpath(vpath, expected):
    assert split_vpath(vpath) == expected


@pytest.mark.parametrize("vpath", ["/../outside.txt", "상담/../../outside.txt", "/상담/.."])
def test_dotdot_is_rejected(vpath):
    with pytest.raises(GateError) as exc:
        split_vpath(vpath)
    assert exc.value.errno == errno.EACCES


def test_nul_is_rejected():
    with pytest.raises(GateError) as exc:
        split_vpath("/상담\x00/x")
    assert exc.value.errno == errno.EINVAL


def test_resolve_inside(root):
    assert resolve(root, "/") == root
    assert resolve(root, "/상담/2026-03.txt") == root / "상담" / "2026-03.txt"


def test_symlink_inside_root_is_allowed(root):
    assert resolve(root, "/inner") == root / "상담" / "2026-03.txt"


@pytest.mark.parametrize("vpath", ["/escape", "/escape_dir/outside.txt", "/escape_dir"])
def test_symlink_outside_root_is_rejected(root, vpath):
    with pytest.raises(GateError) as exc:
        resolve(root, vpath)
    assert exc.value.errno == errno.EACCES


@pytest.mark.parametrize("vpath", ["/없음.txt", "/broken"])
def test_missing_is_enoent(root, vpath):
    with pytest.raises(GateError) as exc:
        resolve(root, vpath)
    assert exc.value.errno == errno.ENOENT
