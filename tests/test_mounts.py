"""gate/mounts.py: 폴더 목록·등록 파일, 추가할 수 없는 폴더, 이름 정하기, MountsSource·MountedStore (Phase 9).

실제 전역 설정(~/.claude)을 건드리지 않게 HOME을 임시 폴더로 바꾼다.
"""

import errno
import fcntl
import json
import os
import shutil
import stat
import sys
import tempfile
import threading
import time
import unicodedata
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from gate.approval import Approvals
from gate.audit import AuditLog
from gate.client import GateClient
from gate.daemon import GateServer
from gate.jev_client import MockJevClient
from gate.mounts import (
    MAX_MOUNTS,
    Guard,
    MountedStore,
    MountEntry,
    MountsFileError,
    MountsSource,
    WorkspaceRecord,
    build_guard,
    choose_name,
    disk_path,
    name_problem,
    read_mounts,
    read_workspaces,
    refusal,
    write_mounts,
    write_workspaces,
)
from gate.pipeline import Inspector
from gate.policy import Entry, PathRule, PolicySource, write_entries
from gate.protocol import GateError

PROJECT_ROOT = Path(__file__).resolve().parents[1]
STAMP = "2026-11-01T10:00:00+09:00"


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    return home.resolve()


def entry(name, path, connected=True, added_by="terminal"):
    return MountEntry(unicodedata.normalize("NFC", name), str(path), STAMP, added_by, connected)


def record(workspace, data, gate_bin, source=None):
    return WorkspaceRecord(path=str(workspace), source=str(source or data / "source"),
                           socket=str(data / "run" / "gate.sock"), policy=str(data / "policy.yaml"),
                           audit=str(data / "audit" / "audit.jsonl"), gate_bin=str(gate_bin),
                           settings_sha256="0" * 64, registered_at=STAMP)


def errno_of(call):
    with pytest.raises(GateError) as exc:
        call()
    return exc.value.errno


# --- 목록 파일 ---

GOOD = {"name": "가계부", "path": "/x/가계부", "added_at": STAMP, "added_by": "ui", "connected": True}


def dump(items, version=1, key="mounts"):
    return yaml.safe_dump({"version": version, key: items}, allow_unicode=True)


def test_round_trip_is_private_and_atomic(tmp_path):
    path = tmp_path / "gate-data" / "mounts.yaml"
    entries = [entry("가계부", tmp_path / "a"), entry("옛 폴더", tmp_path / "b", connected=False, added_by="ui")]
    write_mounts(path, entries)
    assert read_mounts(path) == tuple(entries)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert [p.name for p in path.parent.iterdir()] == ["mounts.yaml"]  # 임시 파일이 남지 않는다
    assert read_mounts(tmp_path / "없음.yaml") == ()
    (tmp_path / "empty.yaml").write_text("", encoding="utf-8")
    assert read_mounts(tmp_path / "empty.yaml") == ()


def test_write_replaces_a_symlink_instead_of_writing_through_it(tmp_path):
    target = tmp_path / "target.yaml"
    target.write_text("keep\n", encoding="utf-8")
    link = tmp_path / "mounts.yaml"
    link.symlink_to(target)
    write_mounts(link, [entry("가계부", tmp_path / "a")])
    assert target.read_text(encoding="utf-8") == "keep\n" and not link.is_symlink()


def test_hand_written_time_without_quotes_is_read(tmp_path):
    path = tmp_path / "mounts.yaml"
    path.write_text("version: 1\nmounts:\n  - name: 가계부\n    path: /x/가계부\n"
                    "    added_at: 2026-11-02T10:00:00+09:00\n    added_by: ui\n    connected: true\n", encoding="utf-8")
    [e] = read_mounts(path)
    assert e.added_at.startswith("2026-11-02T10:00:00")


def test_names_are_read_as_nfc(tmp_path):
    path = tmp_path / "mounts.yaml"
    path.write_text(dump([{**GOOD, "name": unicodedata.normalize("NFD", "가계부")}]), encoding="utf-8")
    assert read_mounts(path)[0].name == "가계부"


@pytest.mark.parametrize("text, problem", [
    ("[]\n", "version과 mounts만"),
    ("version: 1\nextra: 1\n", "version과 mounts만"),
    ("version: 2\nmounts: []\n", "version은 1"),
    ("version: 1\nmounts: {}\n", "목록"),
    ("version: 1\nmounts: [ {\n", "형식"),
    (dump(["가계부"]), "객체가 아닙니다"),
    (dump([{**GOOD, "extra": 1}]), "모르는 키"),
    (dump([{k: v for k, v in GOOD.items() if k != "connected"}]), "connected이(가) 없습니다"),
    (dump([{**GOOD, "name": 2024}]), "name이 문자열이 아닙니다"),
    (dump([{**GOOD, "name": "a/b"}]), "name을 쓸 수 없습니다"),
    (dump([{**GOOD, "name": ".."}]), "name을 쓸 수 없습니다"),
    (dump([{**GOOD, "name": " "}]), "name을 쓸 수 없습니다"),
    (dump([{**GOOD, "name": "a\nb"}]), "name을 쓸 수 없습니다"),
    (dump([{**GOOD, "path": "x/가계부"}]), "path를 쓸 수 없습니다"),
    (dump([{**GOOD, "path": "/x/../가계부"}]), "path를 쓸 수 없습니다"),
    (dump([{**GOOD, "path": "/x/가계부/"}]), "path를 쓸 수 없습니다"),
    (dump([{**GOOD, "path": "//x/가계부"}]), "path를 쓸 수 없습니다"),
    (dump([{**GOOD, "path": "/x/가계*"}]), "path를 쓸 수 없습니다"),
    (dump([{**GOOD, "path": "/x/가\x07계부"}]), "path를 쓸 수 없습니다"),
    (dump([{**GOOD, "added_by": "agent"}]), "added_by"),
    (dump([{**GOOD, "connected": 1}]), "connected"),
    (dump([{**GOOD, "added_at": ""}]), "added_at"),
    (dump([GOOD, {**GOOD, "path": "/y/가계부"}]), "이름이 겹칩니다"),
    (dump([GOOD, {**GOOD, "name": unicodedata.normalize("NFD", "가계부"), "path": "/y"}]), "이름이 겹칩니다"),
    (dump([{**GOOD, "name": "Docs"}, {**GOOD, "name": "docs", "path": "/y"}]), "이름이 겹칩니다"),
    (dump([GOOD, {**GOOD, "name": "b"}]), "경로가 겹칩니다"),
    (dump([GOOD, {**GOOD, "name": "b", "path": "/x/가계부/2026"}]), "경로가 겹칩니다"),
    (dump([GOOD, {**GOOD, "name": "b", "path": "/x"}]), "경로가 겹칩니다"),
    (dump([GOOD, {**GOOD, "name": "b", "path": "/X/가계부"}]), "경로가 겹칩니다"),
    (dump([{**GOOD, "name": f"f{i}", "path": f"/x/f{i}"} for i in range(MAX_MOUNTS + 1)]), f"{MAX_MOUNTS}개까지"),
])
def test_list_file_is_read_strictly(tmp_path, text, problem):
    path = tmp_path / "mounts.yaml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(MountsFileError, match=problem.replace("(", r"\(").replace(")", r"\)")):
        read_mounts(path)


def test_error_messages_do_not_carry_paths_or_names(tmp_path):
    """데몬은 이 메시지를 차단 이유로 에이전트에게 돌려준다."""
    path = tmp_path / "mounts.yaml"
    path.write_text(dump([GOOD, {**GOOD, "path": "/y/비밀"}]), encoding="utf-8")
    with pytest.raises(MountsFileError) as exc:
        read_mounts(path)
    assert "가계부" not in str(exc.value) and "/x" not in str(exc.value) and "비밀" not in str(exc.value)


def test_unreadable_and_oversized_files(tmp_path):
    path = tmp_path / "mounts.yaml"
    path.write_bytes(b"version: 1\nmounts: []\n# \xff\xfe\n")
    with pytest.raises(MountsFileError, match="형식"):
        read_mounts(path)
    path.write_bytes(b"#" * (300 * 1024))
    with pytest.raises(MountsFileError, match="너무 큽니다"):
        read_mounts(path)
    path.unlink()
    path.mkdir()
    with pytest.raises(MountsFileError, match="읽을 수 없습니다"):
        read_mounts(path)


def test_write_checks_before_writing(tmp_path):
    path = tmp_path / "mounts.yaml"
    write_mounts(path, [entry("가계부", tmp_path / "a")])
    before = path.read_bytes()
    for bad in ([entry("가계부", tmp_path / "a"), entry("가계부", tmp_path / "b")],
                [entry("a/b", tmp_path / "c")], [entry("x", "relative")]):
        with pytest.raises(MountsFileError):
            write_mounts(path, bad)
        assert path.read_bytes() == before


def test_workspaces_file_round_trip_and_errors(tmp_path):
    data = tmp_path / "gate-data"
    path = data / "workspaces.yaml"
    records = [record(tmp_path / "ws1", data, tmp_path / "bin" / "gate"), record(tmp_path / "ws2", data, "/usr/bin/gate")]
    write_workspaces(path, records)
    assert read_workspaces(path) == tuple(records)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    item = records[0].to_dict()
    for bad, problem in (([{**item, "settings_sha256": "abc"}], "settings_sha256"),
                         ([{**item, "path": "ws"}], "path를"),
                         ([{**item, "extra": 1}], "모르는 키"),
                         ([item, item], "작업 폴더가 같습니다"),
                         ([{**item, "gate_bin": "/x/[g]"}], "gate_bin")):
        path.write_text(dump(bad, key="workspaces"), encoding="utf-8")
        with pytest.raises(MountsFileError, match=problem):
            read_workspaces(path)


# --- 이름 ---

def test_choose_name_appends_a_number_when_taken():
    assert choose_name("가계부", ["상담", "안내문.txt"]) == "가계부"
    assert choose_name("상담", ["상담", "안내문.txt"]) == "상담 2"
    assert choose_name("상담", ["상담", "상담 2"]) == "상담 3"
    assert choose_name("Docs", ["docs"]) == "Docs 2"  # 대소문자 무시
    assert choose_name(unicodedata.normalize("NFD", "가계부"), ["가계부"]) == "가계부 2"  # NFC로 비교
    assert choose_name(unicodedata.normalize("NFD", "가계부"), []) == "가계부"  # NFC로 맞춘다


@pytest.mark.parametrize("bad", ["", "  ", ".", "..", "a/b", "a\nb", "a\x00b", "a‮b", "a b"])
def test_name_problems(bad):
    assert name_problem(bad)


@pytest.mark.parametrize("good", ["가계부", "가계부 2", "Photos 2024", ".hidden", "a.b"])
def test_good_names(good):
    assert name_problem(good) is None


# --- 디스크 표기 ---

needs_getpath = pytest.mark.skipif(not hasattr(fcntl, "F_GETPATH"), reason="F_GETPATH는 맥에만 있다")


@needs_getpath
def test_disk_path_uses_the_on_disk_spelling(tmp_path):
    root = tmp_path.resolve()
    nfd = unicodedata.normalize("NFD", "가계부")
    (root / nfd).mkdir()
    (root / "MixedCase" / "Sub").mkdir(parents=True)
    (root / "MixedCase" / "policy.yaml").write_text("", encoding="utf-8")
    if not (root / "mixedcase").exists():
        pytest.skip("대소문자를 가리는 디스크")
    assert str(disk_path(root / "가계부")) == str(root / nfd)  # 입력은 NFC, 디스크는 NFD
    assert disk_path(root / "mixedcase" / "sub") == root / "MixedCase" / "Sub"
    assert disk_path(root / "mixedcase" / "POLICY.yaml") == root / "MixedCase" / "policy.yaml"
    assert disk_path(root / "mixedcase" / "없음" / "gate.sock") == root / "MixedCase" / "없음" / "gate.sock"
    assert disk_path("/tmp") == Path("/private/tmp")
    with pytest.raises(FileNotFoundError):
        disk_path(root / "없음", strict=True)


# --- 추가할 수 없는 폴더 (4.5) ---

@pytest.mark.parametrize("path", [
    "/", "/System", "/System/Volumes/Data/Users/me", "/Library", "/Library/Application Support/x", "/usr",
    "/usr/local/data", "/bin", "/sbin", "/etc", "/private/etc", "/private/etc/ssh", "/Applications",
    "/Applications/Foo.app", "/private", "/Volumes", "/Users", "/users", "/PRIVATE",
])
def test_system_folders_are_refused(path):
    assert refusal(Path(path), Guard(), require_dir=False) is not None


@pytest.mark.parametrize("path", ["/private/tmp/x", "/private/var/folders/ab/T/x", "/Volumes/외장/가계부",
                                  "/Users/me/Documents/가계부", "/Users/Shared/x", "/opt/data"])
def test_inside_shallow_system_folders_is_fine(path):
    assert refusal(Path(path), Guard(), require_dir=False) is None


def test_etc_is_refused_after_resolve():
    assert refusal(Path("/etc").resolve(), Guard(), require_dir=False) is not None


@pytest.fixture
def layout(tmp_path):
    """실제 배치와 같다: gate-data/{source, policy.yaml, policy-history, audit, run, agent-workspace}."""
    root = tmp_path.resolve()
    data = root / "gate-data"
    for d in ("source/상담", "policy-history", "audit", "run", "agent-workspace/sub"):
        (data / d).mkdir(parents=True)
    (root / "venv" / "bin").mkdir(parents=True)
    (root / "venv" / "lib").mkdir()
    (root / "venv" / "pyvenv.cfg").write_text("", encoding="utf-8")
    for d in ("가계부/2026", "옛/sub", "free"):
        (root / d).mkdir(parents=True)
    guard = build_guard(policy=data / "policy.yaml",
                        records=[record(data / "agent-workspace", data, root / "venv" / "bin" / "gate")],
                        mounts=[entry("가계부", root / "가계부"), entry("옛", root / "옛", connected=False)])
    return root, guard


@pytest.mark.parametrize("folder, message", [
    ("gate-data/agent-workspace", "에이전트 작업 폴더는 연결할 수 없어요"),
    ("gate-data/agent-workspace/sub", "에이전트 작업 폴더 안의 폴더는"),
    ("gate-data", "작업 폴더를 감싸는 폴더는 연결할 수 없어요. 에이전트가 자기 작업 폴더를 못 읽게 돼요."),
    ("gate-data/audit", "PLANT 데이터(감사 로그 폴더)"),
    ("GATE-DATA/Audit", "PLANT 데이터(감사 로그 폴더)"),  # 대소문자만 다르게 써도
    ("gate-data/run", "PLANT 데이터(소켓 폴더)"),
    ("gate-data/policy-history", "PLANT 데이터(정책 기록 폴더)"),
    ("venv", "PLANT 프로그램 폴더는"),
    ("venv/lib", "PLANT 프로그램 폴더는"),
    ("gate-data/source", "기본 폴더는 이미 PLANT에 연결되어 있어요"),
    ("gate-data/source/상담", "기본 폴더 안의 폴더는"),
    ("가계부", "이미 연결된 폴더예요."),
    ("가계부/2026", "이미 연결된 폴더 '가계부' 안의 폴더예요"),
    ("옛", "보호만 남은 폴더 '옛'"),
    ("옛/sub", "보호만 남은 폴더 '옛' 안의 폴더예요"),
])
def test_guard_refuses_overlaps(layout, folder, message):
    root, guard = layout
    why = refusal(root / folder, guard, require_dir=False)
    assert why is not None and message in why.message
    assert "\n" not in why.message  # 한 줄


def test_guard_allows_unrelated_folders(layout):
    root, guard = layout
    assert refusal(root / "free", guard) is None


def test_folders_that_contain_gate_data_or_the_base_are_refused(tmp_path):
    root = tmp_path.resolve()
    guard = build_guard(policy=root / "data" / "policy.yaml", sources=[root / "src" / "inner"],
                        audits=[root / "logs" / "audit.jsonl"], sockets=[root / "sock" / "gate.sock"])
    assert "PLANT 데이터(정책 파일)를 담은" in refusal(root / "data", guard, require_dir=False).message
    assert "감사 로그 폴더" in refusal(root / "logs", guard, require_dir=False).message
    assert "소켓 폴더" in refusal(root / "sock" / "x", guard, require_dir=False).message
    assert "기본 폴더를 감싸는" in refusal(root / "src", guard, require_dir=False).message


def test_gate_program_folders_are_refused():
    guard = build_guard(policy="/nowhere/gate-data/policy.yaml")
    for folder in (PROJECT_ROOT, PROJECT_ROOT / "gate", Path(sys.prefix).resolve(), Path(sys.base_prefix).resolve()):
        assert "PLANT 프로그램 폴더" in refusal(folder, guard, require_dir=False).message
    assert "PLANT 프로그램 폴더를 담은" in refusal(PROJECT_ROOT.parent, guard, require_dir=False).message


def test_claude_config_is_refused(home, tmp_path, monkeypatch):
    guard = build_guard(policy=tmp_path / "gate-data" / "policy.yaml")
    assert "Claude 설정 폴더" in refusal(home / ".claude", guard, require_dir=False).message
    assert "Claude 설정 폴더" in refusal(home / ".claude" / "projects", guard, require_dir=False).message
    assert "Claude 설정(~/.claude)을 담은" in refusal(home, guard, require_dir=False).message
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cfg"))
    guard = build_guard(policy=tmp_path / "gate-data" / "policy.yaml")
    assert refusal((tmp_path / "cfg").resolve(), guard, require_dir=False) is not None


def test_folder_checks(tmp_path):
    root = tmp_path.resolve()
    guard = Guard()
    assert "찾을 수 없어요" in refusal(root / "없음", guard).message
    (root / "file.txt").write_text("x", encoding="utf-8")
    assert "폴더가 아니에요" in refusal(root / "file.txt", guard).message
    for name in ("a[1]", "b*", "c?"):
        (root / name).mkdir()
        assert "*, ?, [, ]" in refusal(root / name, guard).message
    (root / "d\ne").mkdir()
    assert "제어 문자" in refusal(root / "d\ne", guard).message


@pytest.mark.skipif(os.geteuid() == 0, reason="root는 권한을 무시한다")
def test_unreadable_folder_is_refused(tmp_path):
    folder = tmp_path.resolve() / "locked"
    folder.mkdir()
    folder.chmod(0o000)
    try:
        assert "읽을 수 없어요" in refusal(folder, Guard()).message
    finally:
        folder.chmod(0o700)


# --- MountsSource·MountedStore ---

@pytest.fixture
def world(tmp_path):
    root = tmp_path.resolve()
    data = root / "gate-data"
    source = data / "source"
    (source / "상담").mkdir(parents=True)
    (source / "상담" / "a.txt").write_text("기본 상담\n", encoding="utf-8")
    (source / "가계부").mkdir()  # 추가 폴더가 가릴 같은 이름
    (source / "가계부" / "secret.txt").write_text("기본 쪽 가려진 파일\n", encoding="utf-8")
    (source / "안내문.txt").write_text("안내\n", encoding="utf-8")
    book = root / "docs" / "가계부"
    (book / "2026").mkdir(parents=True)
    (book / "2026" / "1월.txt").write_text("1월 가계부\n", encoding="utf-8")
    (book / "2026" / "2월.txt").write_text("2월 가계부\n", encoding="utf-8")
    (book / ".env").write_text("TOKEN=abc\n", encoding="utf-8")
    other = root / "other" / "사진"
    other.mkdir(parents=True)
    (other / "p.txt").write_text("사진 설명\n", encoding="utf-8")
    warnings: list[str] = []
    mounts = MountsSource(data / "policy.yaml", source=source, socket=data / "run" / "gate.sock",
                          audit=data / "audit" / "audit.jsonl", warn=warnings.append)
    return SimpleNamespace(root=root, data=data, source=source, book=book, other=other, warnings=warnings,
                           mounts=mounts, store=MountedStore(source, mounts), mounts_file=data / "mounts.yaml")


def names(entries):
    return [(e.name, e.type) for e in entries]


def connect(w, *entries):
    write_mounts(w.mounts_file, list(entries))


def test_without_list_file_only_the_base_is_shown(world):
    assert names(world.store.list_dir("/")) == [("가계부", "dir"), ("상담", "dir"), ("안내문.txt", "file")]
    assert world.store.read("/가계부/secret.txt") == "기본 쪽 가려진 파일\n".encode()
    assert world.warnings == []


def test_connected_folder_is_shown_under_its_name_and_hides_the_base_item(world):
    connect(world, entry("가계부", world.book))
    store = world.store
    assert names(store.list_dir("/")) == [("가계부", "dir"), ("상담", "dir"), ("안내문.txt", "file")]
    assert names(store.list_dir("/가계부")) == [(".env", "file"), ("2026", "dir")]
    assert store.read("/가계부/2026/1월.txt") == "1월 가계부\n".encode()
    assert store.canonical("/가계부/./2026/1월.txt") == "/가계부/2026/1월.txt"
    assert store.canonical("/가계부") == "/가계부"
    assert errno_of(lambda: store.read("/가계부/../상담/a.txt")) == errno.EACCES  # '..'은 받지 않는다
    assert store.stat("/가계부").type == "dir" and store.stat("/가계부/2026/1월.txt").type == "file"
    assert errno_of(lambda: store.read("/가계부/secret.txt")) == errno.ENOENT  # 기본 쪽 항목은 가려졌다
    assert store.read("/상담/a.txt") == "기본 상담\n".encode()
    assert any("기본 폴더의 같은 이름 항목을 가립니다" in w for w in world.warnings)


def test_names_are_matched_ignoring_case_and_canonical_uses_the_real_name(world):
    connect(world, entry("Photos", world.other))
    assert world.store.read("/photos/p.txt") == "사진 설명\n".encode()
    assert world.store.canonical("/PHOTOS/p.txt") == "/Photos/p.txt"


def test_base_symlink_into_a_hidden_item_is_not_found(world):
    (world.source / "link.txt").symlink_to(world.source / "가계부" / "secret.txt")
    connect(world, entry("가계부", world.book))
    assert errno_of(lambda: world.store.canonical("/link.txt")) == errno.ENOENT
    assert errno_of(lambda: world.store.read("/link.txt")) == errno.ENOENT


def test_symlinks_do_not_cross_between_folders(world):
    (world.book / "to-base.txt").symlink_to(world.source / "안내문.txt")
    (world.book / "to-other").symlink_to(world.other)
    (world.source / "to-book").symlink_to(world.book)
    (world.book / "inside.txt").symlink_to(world.book / "2026" / "1월.txt")
    connect(world, entry("가계부", world.book), entry("사진", world.other))
    store = world.store
    for path in ("/가계부/to-base.txt", "/가계부/to-other/p.txt", "/to-book/2026/1월.txt"):
        assert errno_of(lambda: store.read(path)) == errno.EACCES, path
    assert store.read("/가계부/inside.txt") == "1월 가계부\n".encode()  # 같은 폴더 안은 된다
    assert store.canonical("/가계부/inside.txt") == "/가계부/2026/1월.txt"
    listed = dict(names(store.list_dir("/가계부")))
    assert listed["to-base.txt"] == "other" and listed["to-other"] == "other"


def test_missing_folder_is_not_listed_and_reads_are_enoent(world):
    connect(world, entry("가계부", world.book))
    moved = world.root / "moved"
    world.book.rename(moved)
    store = world.store
    assert names(store.list_dir("/")) == [("상담", "dir"), ("안내문.txt", "file")]  # 기본 쪽 같은 이름도 그대로 가려짐
    for call in (lambda: store.read("/가계부/2026/1월.txt"), lambda: store.stat("/가계부"),
                 lambda: store.list_dir("/가계부"), lambda: store.read("/가계부/secret.txt")):
        assert errno_of(call) == errno.ENOENT
    moved.rename(world.book)  # 외장 디스크를 다시 꽂음
    assert store.read("/가계부/2026/1월.txt") == "1월 가계부\n".encode()


def test_folder_replaced_by_a_symlink_is_not_served(world):
    connect(world, entry("가계부", world.book))
    shutil.rmtree(world.book)
    world.book.symlink_to(world.data / "audit")
    (world.data / "audit").mkdir()
    (world.data / "audit" / "audit.key").write_bytes(b"k" * 32)
    assert errno_of(lambda: world.store.read("/가계부/audit.key")) in (errno.ENOENT, errno.EACCES)
    assert "가계부" not in [e.name for e in world.store.list_dir("/")]
    assert any("다른 곳을 가리켜" in w for w in world.warnings)


def test_protected_only_entry_is_not_served(world):
    connect(world, entry("가계부", world.book, connected=False))
    assert world.store.read("/가계부/secret.txt") == "기본 쪽 가려진 파일\n".encode()  # 기본 폴더 쪽이 다시 보인다
    assert errno_of(lambda: world.store.read("/가계부/2026/1월.txt")) == errno.ENOENT


def test_broken_list_file_blocks_every_read_but_lists_the_base(world):
    connect(world, entry("가계부", world.book))
    assert world.store.read("/상담/a.txt")
    world.mounts_file.write_text("version: 1\nmounts: [ {\n", encoding="utf-8")
    store = world.store
    with pytest.raises(GateError) as exc:
        store.read("/상담/a.txt")
    assert exc.value.errno == errno.EACCES and "폴더 목록 파일에 문제가 있어 차단했습니다" in exc.value.message
    assert errno_of(lambda: store.read("/가계부/2026/1월.txt")) == errno.EACCES
    assert names(store.list_dir("/")) == [("가계부", "dir"), ("상담", "dir"), ("안내문.txt", "file")]
    assert names(store.list_dir("/가계부")) == [("secret.txt", "file")]  # 기본 폴더만
    assert store.stat("/상담/a.txt").type == "file"
    assert any("폴더 목록 파일 오류" in w for w in world.warnings)
    connect(world, entry("가계부", world.book))  # 고치면 다음 요청부터 정상
    assert store.read("/가계부/2026/1월.txt") == "1월 가계부\n".encode()


def test_broken_workspaces_file_also_blocks(world):
    (world.data / "workspaces.yaml").write_text("version: 7\n", encoding="utf-8")
    assert errno_of(lambda: world.store.read("/상담/a.txt")) == errno.EACCES


def test_changes_are_picked_up_without_restart(world):
    store = world.store
    assert "사진" not in [e.name for e in store.list_dir("/")]
    connect(world, entry("사진", world.other))
    assert "사진" in [e.name for e in store.list_dir("/")]
    connect(world, entry("사진", world.other, connected=False))
    assert "사진" not in [e.name for e in store.list_dir("/")]


@pytest.mark.parametrize("target, brief", [
    ("gate-data/audit", "감사 로그 폴더"),
    ("gate-data/run", "소켓 폴더"),
    ("gate-data/policy-history", "정책 기록 폴더"),
    ("gate-data/source/상담", "기본 폴더"),
    ("gate-data", "정책 파일"),
])
def test_daemon_drops_forbidden_entries_and_warns_once(world, target, brief):
    folder = world.root / target
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "audit.key").write_bytes(b"k" * 32)
    connect(world, entry("열쇠", folder))
    store = world.store
    assert "열쇠" not in [e.name for e in store.list_dir("/")]
    assert errno_of(lambda: store.read("/열쇠/audit.key")) == errno.ENOENT
    dropped = [w for w in world.warnings if "'열쇠'" in w]
    assert len(dropped) == 1 and brief in dropped[0] and "연결하지 않습니다" in dropped[0]
    connect(world, entry("열쇠", folder), entry("사진", world.other))  # 다른 항목이 바뀌어도 같은 경고는 다시 쓰지 않는다
    assert store.read("/사진/p.txt")
    assert len([w for w in world.warnings if "'열쇠'" in w]) == 1


def test_daemon_drops_entries_overlapping_a_registered_workspace(world):
    workspace = world.root / "docs"  # 가계부를 감싸는 작업 폴더
    write_workspaces(world.data / "workspaces.yaml", [record(workspace, world.data, world.root / "bin" / "gate")])
    connect(world, entry("가계부", world.book))
    assert errno_of(lambda: world.store.read("/가계부/2026/1월.txt")) == errno.ENOENT
    assert any("작업 폴더" in w for w in world.warnings)


# --- 게이트 판단까지: 경로 규칙·폴더 승인·감사 기록이 게이트 안 경로로 ---

class AnswerChannel:
    name = "terminal"

    def __init__(self, answer):
        self.answer = answer
        self.asked = []

    def present(self, pending):
        self.asked.append(pending.request)
        pending.answer(self.answer, "terminal")


def test_default_path_rules_apply_inside_a_connected_folder(world):
    connect(world, entry("가계부", world.book))
    (world.book / "key.pem").write_text("-----BEGIN-----\n", encoding="utf-8")
    jev = MockJevClient("fixed", label="safe", confidence=0.99)
    inspector = Inspector(world.store, jev_factory=lambda p: jev, ner_factory=lambda p: None)
    for path in ("/가계부/.env", "/가계부/key.pem", "/가계부/KEY.PEM"):
        verdict = inspector.inspect(None, path)
        assert (verdict.judgment.action, verdict.judgment.stage) == ("deny", "path"), path
    allowed = inspector.inspect(None, "/가계부/2026/1월.txt")
    assert allowed.readable and allowed.data == "1월 가계부\n".encode()
    assert jev.calls[-1].context.path == "/가계부/2026/1월.txt"  # Jev에도 게이트 안 경로가 간다


def test_symlink_to_env_file_is_denied_by_its_real_name(world):
    (world.book / "notes.txt").symlink_to(world.book / ".env")
    connect(world, entry("가계부", world.book))
    inspector = Inspector(world.store, jev_factory=lambda p: MockJevClient("fixed", label="safe", confidence=0.99),
                          ner_factory=lambda p: None)
    assert inspector.inspect(None, "/가계부/notes.txt").judgment.action == "deny"


def test_user_path_rule_and_folder_approval_work_in_a_connected_folder(world):
    connect(world, entry("가계부", world.book))
    policy = world.data / "policy.yaml"
    write_entries(policy, [Entry(path_rule=PathRule("가계부/**", "ask_human"))])
    channel = AnswerChannel("session")
    inspector = Inspector(world.store, PolicySource(policy),
                          jev_factory=lambda p: MockJevClient("fixed", label="safe", confidence=0.99),
                          ner_factory=lambda p: None, approvals=Approvals([channel]))
    first = inspector.inspect(None, "/가계부/2026/1월.txt")
    assert first.readable and first.judgment.approval == "session"
    [asked] = channel.asked
    assert (asked.path, asked.folder) == ("/가계부/2026/1월.txt", "/가계부/2026")
    second = inspector.inspect(None, "/가계부/2026/2월.txt")
    assert second.readable and second.judgment.approval == "remembered" and len(channel.asked) == 1
    assert inspector.inspect(None, "/상담/a.txt").judgment.approval is None  # 기본 폴더에는 그 규칙이 없다


@pytest.fixture
def socket_dir():
    path = Path(tempfile.mkdtemp(prefix="gate-"))
    yield path
    shutil.rmtree(path, ignore_errors=True)


def test_running_daemon_serves_new_folders_and_audits_gate_paths(world, socket_dir):
    events = []
    store = world.store
    inspector = Inspector(store, jev_factory=lambda p: MockJevClient("fixed", label="safe", confidence=0.99),
                          ner_factory=lambda p: None)
    server = GateServer(socket_dir / "gate.sock", store, on_request=events.append, inspector=inspector)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    try:
        client = GateClient(server.socket_path)
        assert "사진" not in [e["name"] for e in client.ls("/")]
        connect(world, entry("사진", world.other), entry("가계부", world.book))
        assert "사진" in [e["name"] for e in client.ls("/")]  # 다시 띄우지 않았다
        assert client.cat("/사진/p.txt") == "사진 설명\n".encode()
        with pytest.raises(GateError) as exc:
            client.cat("/가계부/.env")
        assert exc.value.errno == errno.EACCES
        deadline = time.monotonic() + 3
        while len(events) < 4 and time.monotonic() < deadline:
            time.sleep(0.01)
    finally:
        server.shutdown()
        server.server_close()
    log = AuditLog(world.root / "audit-test" / "audit.jsonl")
    for event in events:
        log.record(event)
    log.close()
    text = (world.root / "audit-test" / "audit.jsonl").read_text(encoding="utf-8")
    lines = [json.loads(line) for line in text.splitlines()]
    assert {"/사진/p.txt", "/가계부/.env"} <= {line["path"] for line in lines}
    assert str(world.other) not in text and str(world.book) not in text  # 실제 경로는 남기지 않는다
