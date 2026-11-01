"""gate/mount_change.py: 폴더 보기·추가·빼기·보호 풀기의 계산·검사·쓰기 순서와 되돌림 (Phase 9).

실제 전역 설정(~/.claude)과 실제 데모 폴더를 건드리지 않게 HOME과 전역 설치 기록 위치를 임시 폴더로 바꾼다.
"""

import dataclasses
import errno
import fcntl
import hashlib
import json
import os
import re
import stat
import unicodedata
from pathlib import Path
from types import SimpleNamespace

import pytest

from gate import mount_change as mc
from gate.mount_change import (
    MountContext,
    MountError,
    WorkspaceStatus,
    apply,
    list_mounts,
    plan_add,
    plan_remove,
    plan_unprotect,
)
from gate.mounts import MountedStore, MountEntry, MountsSource, read_mounts, read_workspaces, write_mounts
from gate.protocol import GateError
from gate.workspace import init_workspace, resolve_paths

STAMP = "2026-11-01T10:00:00+09:00"


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    global_settings = tmp_path / "global" / "settings.json"
    monkeypatch.setattr(mc, "global_settings_file", lambda: global_settings)
    return global_settings


def make_gate_data(root: Path, name: str = "gate-data", workspace: bool = True):
    data = root / name
    (data / "source" / "상담").mkdir(parents=True)
    (data / "source" / "상담" / "a.txt").write_text("상담 기록\n", encoding="utf-8")
    paths = resolve_paths(data / "source", data / "run" / "gate.sock", data / "policy.yaml",
                          data / "audit" / "audit.jsonl", root / "bin" / "gate")
    ws = data / "agent-workspace"
    if workspace:
        init_workspace(ws, paths)
    ctx = MountContext.from_policy(data / "policy.yaml", data / "run" / "gate.sock", data / "audit" / "audit.jsonl")
    return SimpleNamespace(data=data, paths=paths, ws=ws, settings=ws / ".claude" / "settings.json", ctx=ctx)


@pytest.fixture
def env(tmp_path):
    root = tmp_path.resolve()
    g = make_gate_data(root)
    book = root / "docs" / "가계부"
    (book / "2026").mkdir(parents=True)
    (book / "2026" / "1월.txt").write_text("1월\n", encoding="utf-8")
    (book / "2026" / "2월.txt").write_text("2월\n", encoding="utf-8")
    (book / "메모.txt").write_text("메모\n", encoding="utf-8")
    (book / "link.txt").symlink_to(book / "메모.txt")         # 세지 않는다
    (book / "outside").symlink_to(root / "gate-data" / "source")  # 따라가지 않는다
    g.root, g.book = root, book
    return g


def protects(settings: Path, folder: Path) -> bool:
    data = json.loads(settings.read_text(encoding="utf-8"))
    rule = "/" + str(folder)
    deny = data["permissions"]["deny"]
    return (all(r in deny for r in (f"Read({rule})", f"Read({rule}/**)", f"Edit({rule})", f"Edit({rule}/**)"))
            and str(folder) in data["sandbox"]["filesystem"]["denyRead"])


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def add(env, folder=None, added_by="terminal"):
    return apply(env.ctx, plan_add(env.ctx, folder or env.book, added_by=added_by))


def snapshot(*paths: Path) -> dict:
    return {p: (p.read_bytes() if p.exists() else None) for p in paths}


# --- 경로·보기 ---

def test_context_paths(tmp_path, monkeypatch):
    ctx = MountContext.from_policy("~/gd/policy.yaml")
    assert ctx.policy == Path.home() / "gd" / "policy.yaml" and ctx.socket is None and ctx.audit is None
    assert ctx.mounts_file == Path.home() / "gd" / "mounts.yaml"
    assert ctx.workspaces_file == Path.home() / "gd" / "workspaces.yaml"
    monkeypatch.chdir(tmp_path)
    assert MountContext.from_policy("p.yaml", "run/gate.sock").socket == Path.cwd() / "run" / "gate.sock"


def test_list_with_nothing_added(env):
    view = list_mounts(env.ctx)
    [base] = view.folders
    assert (base.kind, base.name, base.vpath, base.path, base.connected, base.exists) == (
        "base", None, "/", str(env.paths.source), True, True)
    assert (base.file_count, base.file_count_capped, base.added_at, base.added_by) == (1, False, None, None)
    assert view.workspaces == (WorkspaceStatus(str(env.ws), "ok"),)
    assert (view.global_setup, view.error, view.limit) == (False, None, 20)


def test_list_without_registered_workspace_has_no_base_row(tmp_path):
    g = make_gate_data(tmp_path.resolve(), workspace=False)
    view = list_mounts(g.ctx)
    assert view.folders == () and view.workspaces == () and view.error is None


def test_list_counts_files_without_following_symlinks(env, monkeypatch):
    add(env)
    row = list_mounts(env.ctx).folders[1]
    assert (row.file_count, row.file_count_capped) == (3, False)
    assert list_mounts(env.ctx, count_files=False).folders[1].file_count is None
    monkeypatch.setattr(mc, "MAX_COUNT_FILES", 2)
    row = list_mounts(env.ctx).folders[1]
    assert (row.file_count, row.file_count_capped) == (2, True)
    monkeypatch.setattr(mc, "COUNT_SECONDS", -1.0)  # 너무 오래 걸리면 멈춘다
    assert list_mounts(env.ctx).folders[1].file_count is None


def test_list_reports_broken_files_without_raising(env):
    add(env)
    env.ctx.mounts_file.write_text("version: 1\nmounts: [ {\n", encoding="utf-8")
    view = list_mounts(env.ctx)
    assert "폴더 목록 파일" in view.error and "막아요" in view.error
    assert [r.kind for r in view.folders] == ["base"]
    env.ctx.mounts_file.unlink()
    env.ctx.workspaces_file.write_text("version: 9\n", encoding="utf-8")
    view = list_mounts(env.ctx)
    assert "작업 폴더 등록 파일" in view.error and view.folders == () and view.workspaces == ()


def test_plans_refuse_while_files_are_broken(env):
    env.ctx.mounts_file.write_text("version: 2\n", encoding="utf-8")
    for call in (lambda: plan_add(env.ctx, env.book, added_by="terminal"), lambda: plan_remove(env.ctx, "x"),
                 lambda: plan_unprotect(env.ctx, "x")):
        with pytest.raises(MountError, match="폴더 목록 파일"):
            call()


# --- 추가 ---

def test_add_blocks_the_workspace_before_opening_the_gate(env, monkeypatch):
    change = plan_add(env.ctx, env.book, added_by="terminal")
    assert (change.op, change.name, change.path, change.file_count, change.file_count_capped) == (
        "add", "가계부", str(env.book), 3, False)
    assert (change.workspaces, change.loosening, change.reconnect) == ((str(env.ws),), True, False)
    assert not env.ctx.mounts_file.exists() and not protects(env.settings, env.book)  # 계산만 했다
    order = []
    real_write_list = mc._write_list

    def watch(ctx, entries):
        order.append(protects(env.settings, env.book))  # 목록을 쓸 때 이미 막혀 있어야 한다
        real_write_list(ctx, entries)
    monkeypatch.setattr(mc, "_write_list", watch)
    result = apply(env.ctx, change)
    assert order == [True]
    assert result.rewritten == (str(env.ws),) and result.notes == ("restart_session",)
    [entry] = read_mounts(env.ctx.mounts_file)
    assert (entry.name, entry.path, entry.added_by, entry.connected) == ("가계부", str(env.book), "terminal", True)
    assert stat.S_IMODE(env.ctx.mounts_file.stat().st_mode) == 0o600
    assert read_workspaces(env.ctx.workspaces_file)[0].settings_sha256 == sha(env.settings)
    view = list_mounts(env.ctx)
    assert view.workspaces == (WorkspaceStatus(str(env.ws), "ok"),)
    row = view.folders[1]
    assert (row.kind, row.name, row.vpath, row.connected, row.exists, row.unprotected_workspaces) == (
        "mount", "가계부", "/가계부", True, True, ())
    assert row.added_at and row.added_by == "terminal"


def test_settings_file_mode_is_kept(env):
    env.settings.chmod(0o640)
    add(env)
    assert stat.S_IMODE(env.settings.stat().st_mode) == 0o640


def test_added_by_ui_is_recorded(env):
    add(env, added_by="ui")
    assert read_mounts(env.ctx.mounts_file)[0].added_by == "ui"


def test_name_collisions_get_a_number(env):
    first = env.root / "a" / "상담"
    second = env.root / "b" / "상담"
    third = env.root / "c" / "가계부"
    for folder in (first, second, third):
        folder.mkdir(parents=True)
    assert plan_add(env.ctx, first, added_by="terminal").name == "상담 2"  # 기본 폴더의 맨 위 항목과 겹친다
    add(env, first)
    assert plan_add(env.ctx, second, added_by="terminal").name == "상담 3"
    add(env)
    assert plan_add(env.ctx, third, added_by="terminal").name == "가계부 2"


@pytest.mark.parametrize("folder, message", [
    ("gate-data/audit", "감사 로그 폴더"),
    ("gate-data/run", "소켓 폴더"),
    ("gate-data/agent-workspace", "에이전트 작업 폴더는"),
    ("gate-data/agent-workspace/.claude", "에이전트 작업 폴더 안의"),
    ("gate-data", "작업 폴더를 감싸는 폴더는 연결할 수 없어요"),
    ("gate-data/source", "기본 폴더는 이미"),
    ("gate-data/source/상담", "기본 폴더 안의"),
    ("home", "Claude 설정"),
    ("home/.claude", "Claude 설정 폴더"),
    ("bin", "PLANT 프로그램 폴더"),
    ("없는 폴더", "찾을 수 없어요"),
    ("docs/가계부/메모.txt", "폴더가 아니에요"),
])
def test_add_refuses_forbidden_folders(env, folder, message):
    for d in ("gate-data/audit", "gate-data/run", "bin"):
        (env.root / d).mkdir(parents=True, exist_ok=True)
    before = snapshot(env.ctx.mounts_file, env.settings, env.ctx.workspaces_file)
    with pytest.raises(MountError) as exc:
        plan_add(env.ctx, env.root / folder, added_by="terminal")
    assert message in str(exc.value)
    assert snapshot(env.ctx.mounts_file, env.settings, env.ctx.workspaces_file) == before


def test_add_refuses_already_connected_and_overlapping_folders(env):
    add(env)
    with pytest.raises(MountError, match="이미 연결된 폴더예요"):
        plan_add(env.ctx, env.book, added_by="terminal")
    with pytest.raises(MountError, match="이미 연결된 폴더 '가계부' 안의"):
        plan_add(env.ctx, env.book / "2026", added_by="terminal")
    with pytest.raises(MountError, match="'가계부'을\\(를\\) 감싸는"):
        plan_add(env.ctx, env.root / "docs", added_by="terminal")


@pytest.mark.skipif(not hasattr(fcntl, "F_GETPATH"), reason="F_GETPATH는 맥에만 있다")
def test_add_records_and_blocks_the_on_disk_spelling(env):
    """`~/documents/가계부`처럼 대소문자·NFC가 디스크와 다르게 입력해도 디스크 표기로 적고 막는다."""
    folder = env.root / "Shared" / unicodedata.normalize("NFD", "사진")
    folder.mkdir(parents=True)
    typed = env.root / "shared" / "사진"
    if not typed.exists():
        pytest.skip("대소문자를 가리는 디스크")
    change = plan_add(env.ctx, typed, added_by="terminal")
    assert change.path == str(folder) and change.name == "사진"  # 이름은 NFC
    apply(env.ctx, change)
    assert protects(env.settings, folder)
    assert read_mounts(env.ctx.mounts_file)[0].path == str(folder)
    assert list_mounts(env.ctx).folders[1].exists


def test_add_through_a_symlink_records_the_real_path(env):
    link = env.root / "바로가기"
    link.symlink_to(env.book)
    change = plan_add(env.ctx, link, added_by="terminal")
    assert (change.name, change.path) == ("가계부", str(env.book))


def test_hand_edited_workspace_settings_block_add(env):
    data = json.loads(env.settings.read_text(encoding="utf-8"))
    data["permissions"]["deny"].append("Read(//mine)")
    env.settings.write_text(json.dumps(data), encoding="utf-8")
    assert list_mounts(env.ctx).workspaces == (WorkspaceStatus(str(env.ws), "edited"),)
    before = snapshot(env.ctx.mounts_file, env.settings, env.ctx.workspaces_file)
    with pytest.raises(MountError) as exc:
        plan_add(env.ctx, env.book, added_by="terminal")
    assert "손으로 바뀌어" in str(exc.value) and "plant workspace init --force" in str(exc.value)
    assert snapshot(env.ctx.mounts_file, env.settings, env.ctx.workspaces_file) == before


def test_symlinked_workspace_settings_count_as_edited(env):
    real = env.root / "elsewhere.json"
    real.write_bytes(env.settings.read_bytes())
    env.settings.unlink()
    env.settings.symlink_to(real)
    with pytest.raises(MountError, match="손으로 바뀌어"):
        plan_add(env.ctx, env.book, added_by="terminal")


def test_failed_settings_write_rolls_back_and_leaves_the_list_alone(env, monkeypatch):
    init_workspace(env.root / "second-ws", env.paths)
    second_settings = env.root / "second-ws" / ".claude" / "settings.json"
    before = snapshot(env.settings, second_settings, env.ctx.workspaces_file, env.ctx.mounts_file)
    calls = []
    real = mc._write_settings

    def flaky(path, text, mode):
        calls.append(path)
        if len(calls) == 2:
            raise OSError(errno.ENOSPC, "디스크가 가득 참", str(path))
        real(path, text, mode)
    monkeypatch.setattr(mc, "_write_settings", flaky)
    change = plan_add(env.ctx, env.book, added_by="terminal")
    assert change.workspaces == (str(env.ws), str(env.root / "second-ws"))
    with pytest.raises(MountError) as exc:
        apply(env.ctx, change)
    assert "연결하지 않았어요" in str(exc.value) and "되돌렸어요" in str(exc.value) and "ENOSPC" in str(exc.value)
    assert snapshot(env.settings, second_settings, env.ctx.workspaces_file, env.ctx.mounts_file) == before
    assert not env.ctx.mounts_file.exists()
    assert calls == [env.settings, second_settings]  # 두 번째에서 실패. 첫 번째는 되돌렸다(위 snapshot)


def test_failed_list_write_rolls_back_the_workspace_settings(env, monkeypatch):
    before = snapshot(env.settings, env.ctx.workspaces_file)

    def broken(ctx, entries):
        raise OSError(errno.EACCES, "권한 없음", str(ctx.mounts_file))
    monkeypatch.setattr(mc, "_write_list", broken)
    with pytest.raises(MountError, match="되돌렸어요"):
        add(env)
    assert snapshot(env.settings, env.ctx.workspaces_file) == before
    assert not env.ctx.mounts_file.exists()


def test_apply_refuses_when_things_changed_after_plan(env):
    change = plan_add(env.ctx, env.book, added_by="terminal")
    init_workspace(env.root / "late-ws", env.paths)  # 확인하는 사이 작업 폴더가 하나 더 등록됨
    with pytest.raises(MountError, match="확인하는 사이"):
        apply(env.ctx, change)
    assert not env.ctx.mounts_file.exists()
    change = plan_add(env.ctx, env.book, added_by="terminal")
    with pytest.raises(MountError, match="확인하는 사이"):
        apply(env.ctx, dataclasses.replace(change, name="다른 이름"))


def test_settings_edited_after_plan_are_not_overwritten(env):
    change = plan_add(env.ctx, env.book, added_by="terminal")
    env.settings.write_text(env.settings.read_text(encoding="utf-8") + " ", encoding="utf-8")
    edited = env.settings.read_bytes()
    with pytest.raises(MountError, match="손으로 바뀌어"):
        apply(env.ctx, change)
    assert env.settings.read_bytes() == edited and not env.ctx.mounts_file.exists()


def test_confirm_token(env):
    a = plan_add(env.ctx, env.book, added_by="terminal")
    b = plan_add(env.ctx, env.book, added_by="ui")
    assert a.confirm_token == b.confirm_token and re.fullmatch(r"[0-9a-f]{24}", a.confirm_token)
    assert dataclasses.replace(a, file_count=99).confirm_token == a.confirm_token  # 파일 수는 넣지 않는다
    for field, value in (("op", "remove"), ("name", "x"), ("path", "/x"), ("workspaces", ()), ("loosening", False),
                         ("reconnect", True)):
        assert dataclasses.replace(a, **{field: value}).confirm_token != a.confirm_token, field


def test_limit_counts_protected_only_entries_but_reconnect_is_allowed(env):
    entries = [MountEntry(f"폴더{i}", str(env.root / "many" / str(i)), STAMP, "terminal", i != 0) for i in range(20)]
    (env.root / "many" / "0").mkdir(parents=True)
    write_mounts(env.ctx.mounts_file, entries)
    with pytest.raises(MountError, match="20개까지"):
        plan_add(env.ctx, env.book, added_by="terminal")
    change = plan_add(env.ctx, env.root / "many" / "0", added_by="terminal")
    assert change.reconnect and change.name == "폴더0"


def test_no_registered_workspace_is_noted(tmp_path):
    g = make_gate_data(tmp_path.resolve(), workspace=False)
    folder = tmp_path.resolve() / "가계부"
    folder.mkdir()
    result = apply(g.ctx, plan_add(g.ctx, folder, added_by="terminal"))
    assert result.rewritten == () and result.notes == ("no_workspace",)
    assert read_mounts(g.ctx.mounts_file)[0].connected


def test_missing_workspaces_are_skipped(env):
    init_workspace(env.root / "gone", env.paths)
    for p in sorted((env.root / "gone").rglob("*"), reverse=True):
        p.unlink() if p.is_file() else p.rmdir()
    (env.root / "gone").rmdir()
    assert WorkspaceStatus(str(env.root / "gone"), "missing") in list_mounts(env.ctx).workspaces
    result = add(env)
    assert result.rewritten == (str(env.ws),) and "no_workspace" not in result.notes


def test_global_setup_record_asks_to_rerun_setup(env, isolate):
    record = isolate.with_name(isolate.name + ".gate-setup.json")
    record.parent.mkdir(parents=True)
    record.write_text("{}", encoding="utf-8")
    assert list_mounts(env.ctx).global_setup
    assert add(env).notes == ("restart_session", "rerun_setup")
    apply(env.ctx, plan_remove(env.ctx, "가계부"))
    assert "rerun_setup" not in apply(env.ctx, plan_unprotect(env.ctx, "가계부")).notes


def test_list_shows_workspaces_that_do_not_block_a_folder_yet(env):
    write_mounts(env.ctx.mounts_file, [MountEntry("가계부", str(env.book), STAMP, "terminal", True)])  # 화면을 거치지 않음
    view = list_mounts(env.ctx)
    assert view.workspaces == (WorkspaceStatus(str(env.ws), "stale"),)
    assert view.folders[1].unprotected_workspaces == (str(env.ws),)
    other = env.root / "사진"
    other.mkdir()
    add(env, other)  # 다음 추가 때 stale인 작업 폴더를 지금 목록으로 다시 쓴다
    view = list_mounts(env.ctx)
    assert view.workspaces == (WorkspaceStatus(str(env.ws), "ok"),)
    assert protects(env.settings, env.book) and protects(env.settings, other)
    assert all(r.unprotected_workspaces == () for r in view.folders)


# --- 빼기·보호 풀기 ---

def daemon_store(env):
    return MountedStore(env.paths.source, MountsSource(env.ctx.policy, source=env.paths.source, socket=env.ctx.socket,
                                                       audit=env.ctx.audit))


def test_remove_keeps_the_protection(env):
    add(env)
    store = daemon_store(env)
    assert store.read("/가계부/메모.txt") == "메모\n".encode()
    settings_before = env.settings.read_bytes()
    change = plan_remove(env.ctx, "가계부")
    assert (change.op, change.workspaces, change.loosening, change.reconnect) == ("remove", (), False, False)
    result = apply(env.ctx, change)
    assert result.rewritten == () and result.notes == ()
    assert read_mounts(env.ctx.mounts_file)[0].connected is False
    assert env.settings.read_bytes() == settings_before and protects(env.settings, env.book)  # 직접 읽기 막기는 남음
    row = list_mounts(env.ctx).folders[1]
    assert (row.connected, row.exists, row.unprotected_workspaces) == (False, True, ())
    with pytest.raises(GateError) as exc:
        store.read("/가계부/메모.txt")
    assert exc.value.errno == errno.ENOENT  # 게이트에서는 빠졌다


def test_remove_accepts_gate_path_and_other_case(env):
    folder = env.root / "Photos"
    folder.mkdir()
    add(env, folder)
    assert plan_remove(env.ctx, "/photos").name == "Photos"
    assert plan_remove(env.ctx, unicodedata.normalize("NFD", "PHOTOS")).name == "Photos"


def test_remove_errors(env):
    with pytest.raises(MountError, match="목록에 없어요"):
        plan_remove(env.ctx, "가계부")
    add(env)
    apply(env.ctx, plan_remove(env.ctx, "가계부"))
    with pytest.raises(MountError, match="이미 PLANT에서 뺀"):
        plan_remove(env.ctx, "가계부")


def test_unprotect_removes_the_entry_and_the_protection(env):
    add(env)
    with pytest.raises(MountError, match="먼저 PLANT에서 뺀"):
        plan_unprotect(env.ctx, "가계부")
    apply(env.ctx, plan_remove(env.ctx, "가계부"))
    change = plan_unprotect(env.ctx, "가계부")
    assert (change.op, change.workspaces, change.loosening) == ("unprotect", (str(env.ws),), True)
    result = apply(env.ctx, change)
    assert result.rewritten == (str(env.ws),) and result.notes == ("restart_session",)
    assert read_mounts(env.ctx.mounts_file) == ()
    assert not protects(env.settings, env.book)  # 보호가 풀렸다
    assert str(env.book) not in env.settings.read_text(encoding="utf-8")
    assert read_workspaces(env.ctx.workspaces_file)[0].settings_sha256 == sha(env.settings)
    assert [r.kind for r in list_mounts(env.ctx).folders] == ["base"]
    with pytest.raises(MountError, match="목록에 없어요"):
        plan_unprotect(env.ctx, "가계부")


def test_unprotect_refuses_hand_edited_settings(env):
    add(env)
    apply(env.ctx, plan_remove(env.ctx, "가계부"))
    env.settings.write_text(env.settings.read_text(encoding="utf-8").replace('"enabled": true', '"enabled": false'),
                            encoding="utf-8")
    before = snapshot(env.ctx.mounts_file, env.settings, env.ctx.workspaces_file)
    with pytest.raises(MountError, match="손으로 바뀌어 있어 보호를 풀지"):
        plan_unprotect(env.ctx, "가계부")
    assert snapshot(env.ctx.mounts_file, env.settings, env.ctx.workspaces_file) == before


def test_failed_unprotect_puts_the_entry_back(env, monkeypatch):
    add(env)
    apply(env.ctx, plan_remove(env.ctx, "가계부"))
    before = snapshot(env.ctx.mounts_file, env.settings, env.ctx.workspaces_file)

    def broken(path, text, mode):
        raise OSError(errno.EIO, "입출력 오류", str(path))
    monkeypatch.setattr(mc, "_write_settings", broken)
    with pytest.raises(MountError, match="보호를 풀지 않았어요"):
        apply(env.ctx, plan_unprotect(env.ctx, "가계부"))
    assert read_mounts(env.ctx.mounts_file)[0].name == "가계부"
    assert snapshot(env.settings, env.ctx.workspaces_file) == {k: v for k, v in before.items()
                                                              if k != env.ctx.mounts_file}
    assert protects(env.settings, env.book)


def test_reconnect_a_protected_only_folder(env):
    add(env)
    apply(env.ctx, plan_remove(env.ctx, "가계부"))
    change = plan_add(env.ctx, env.book, added_by="ui")
    assert change.reconnect and change.name == "가계부" and change.workspaces == ()  # 이미 막혀 있다
    result = apply(env.ctx, change)
    assert result.rewritten == () and result.notes == ()
    [entry] = read_mounts(env.ctx.mounts_file)
    assert entry.connected and entry.added_by == "ui"
    assert daemon_store(env).read("/가계부/메모.txt") == "메모\n".encode()


def test_lock_file_is_private(env):
    add(env)
    lock = env.data / mc.LOCK_FILE_NAME
    assert lock.exists() and stat.S_IMODE(lock.stat().st_mode) == 0o600
    assert os.path.getsize(lock) == 0
