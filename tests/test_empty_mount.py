import os

from conftest import mounted, requires_fuse


@requires_fuse
def test_empty_mount_appears_and_unmounts(tmp_path):
    mountpoint = tmp_path / "mnt"
    mountpoint.mkdir()

    with mounted("gate.emptyfs", mountpoint) as proc:
        assert os.path.ismount(mountpoint)
        assert os.listdir(mountpoint) == []
        assert os.stat(mountpoint).st_mode & 0o777 == 0o555

    assert not os.path.ismount(mountpoint)
    assert proc.returncode == 0
