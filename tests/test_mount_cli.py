"""gate/mount_cli.py: `gate mount` (터미널 확인·출력)과 `gate setup`·`gate workspace init`이 폴더 목록을 읽는지 (Phase 9).

실제 전역 설정(~/.claude)과 실제 데모 폴더를 건드리지 않게 HOME과 전역 설치 기록 위치를 임시 폴더로 바꾼다.
"""

import argparse
import io
import json
from types import SimpleNamespace

import pytest

from gate import cli, mount_cli
from gate import mount_change as mc
from gate.mount_change import MountContext, apply, plan_add, plan_remove
from gate.mounts import MountedStore, read_mounts, write_mounts
from gate.protocol import GateError
from gate.store import PlainStore
from gate.workspace import init_workspace, resolve_paths


class Tty(io.StringIO):
    def isatty(self):
        return True


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    for name in ("GATE_POLICY", "GATE_AUDIT", "GATE_SOCKET"):
        monkeypatch.delenv(name, raising=False)
    global_settings = tmp_path / "global" / "settings.json"
    monkeypatch.setattr(mc, "global_settings_file", lambda: global_settings)
    return global_settings


@pytest.fixture
def env(tmp_path):
    root = tmp_path.resolve()
    data = root / "gate-data"
    (data / "source" / "상담").mkdir(parents=True)
    paths = resolve_paths(data / "source", data / "run" / "gate.sock", data / "policy.yaml",
                          data / "audit" / "audit.jsonl", root / "bin" / "gate")
    ws = data / "agent-workspace"
    init_workspace(ws, paths)
    book = root / "docs" / "가계부"
    (book / "2026").mkdir(parents=True)
    for name in ("2026/1월.txt", "2026/2월.txt", "메모.txt"):
        (book / name).write_text("내용\n", encoding="utf-8")
    ctx = MountContext.from_policy(data / "policy.yaml", data / "run" / "gate.sock", data / "audit" / "audit.jsonl")
    return SimpleNamespace(root=root, data=data, paths=paths, ws=ws, settings=ws / ".claude" / "settings.json",
                           book=book, ctx=ctx)


def parse(env, *argv):
    parser = argparse.ArgumentParser()
    parser.add_argument("--socket", default=None)
    sub = parser.add_subparsers(dest="command")
    mount_cli.register(sub)
    return parser.parse_args(["--socket", str(env.paths.socket), "mount", "--policy", str(env.paths.policy),
                              "--audit", str(env.paths.audit), *argv])


def run(env, *argv, answers=None, tty=True):
    out = io.StringIO()
    stdin = (Tty if tty else io.StringIO)(answers or "")
    code = mount_cli.run(parse(env, *argv), stdin=stdin, stdout=out)
    return code, out.getvalue()


# --- 터미널에서만 ---

@pytest.mark.parametrize("argv", [("add", "{book}"), ("remove", "가계부"), ("unprotect", "가계부")])
def test_change_commands_need_a_terminal(env, argv):
    argv = [a.format(book=env.book) for a in argv]
    code, out = run(env, *argv, answers="y\ny\n", tty=False)
    assert code == 1
    assert "사람이 터미널에서 직접 실행해야 해요(입력이 터미널이 아님)" in out and "gate mount list" in out
    assert not env.ctx.mounts_file.exists()


def test_cli_main_refuses_changes_without_a_terminal(env, capsys):
    """pytest의 stdin은 터미널이 아니다(에이전트의 Bash 실행과 같다)."""
    code = cli.main(["mount", "--policy", str(env.paths.policy), "add", str(env.book)])
    assert code == 1 and "터미널에서 직접" in capsys.readouterr().out
    assert not env.ctx.mounts_file.exists()


def test_policy_path_is_required(env, monkeypatch):
    args = argparse.Namespace(mount_command="list", policy_path=None, audit_path=None, socket=None)
    out = io.StringIO()
    assert mount_cli.run(args, stdin=Tty(""), stdout=out) == 1
    assert "--policy 또는 GATE_POLICY" in out.getvalue()
    monkeypatch.setenv("GATE_POLICY", str(env.paths.policy))
    assert mount_cli.run(args, stdin=Tty(""), stdout=io.StringIO()) == 0


def test_options_work_before_or_after_the_subcommand(env, capsys):
    for argv in (["mount", "--policy", str(env.paths.policy), "list"], ["mount", "list", "--policy", str(env.paths.policy)]):
        assert cli.main(argv) == 0
        assert "작업 폴더 (등록 1개)" in capsys.readouterr().out


# --- 보기 ---

def test_list_shows_state_paths_counts_and_workspaces(env):
    apply(env.ctx, plan_add(env.ctx, env.book, added_by="terminal"))
    code, out = run(env, "list", tty=False)
    assert code == 0
    assert "추가 폴더 1/20개" in out
    assert "/  (기본)  —  기본 · 띄울 때 정함" in out
    assert "/가계부  가계부  —  연결됨" in out and f"실제 경로: {env.book} · 파일 3개" in out and "터미널에서 연결" in out
    assert f"{env.ws}: 지금 목록대로 막고 있어요" in out
    apply(env.ctx, plan_remove(env.ctx, "가계부"))
    env.book.rename(env.root / "moved")
    code, out = run(env, "list", tty=False)
    assert "보호만 남음" in out and "찾을 수 없음" in out


def test_list_without_workspace_and_with_problems(tmp_path):
    data = tmp_path.resolve() / "other-data"
    data.mkdir()
    (data / "mounts.yaml").write_text("version: 3\n", encoding="utf-8")
    out = io.StringIO()
    args = argparse.Namespace(mount_command="list", policy_path=str(data / "policy.yaml"), audit_path=None, socket=None)
    assert mount_cli.run(args, stdin=io.StringIO(), stdout=out) == 0
    text = out.getvalue()
    assert "기본 폴더: 알 수 없음" in text and "문제: 폴더 목록 파일" in text and "등록 0개" in text


def test_list_shows_workspaces_that_do_not_block_yet(env):
    from gate.mounts import MountEntry
    write_mounts(env.ctx.mounts_file, [MountEntry("가계부", str(env.book), "2026-11-01T10:00:00+09:00", "ui", True)])
    code, out = run(env, "list", tty=False)
    assert "작업 폴더 설정에 반영 안 됨(이 폴더를 직접 읽을 수 있음)" in out and "예전 목록대로예요" in out
    assert "설정 화면에서 연결" in out


# --- 바꾸기 ---

def test_add_asks_twice_and_reports(env):
    code, out = run(env, "add", str(env.book), answers="y\ny\n")
    assert code == 0, out
    assert "이 폴더를 게이트에 연결해요:" in out and "게이트 안 경로: /가계부" in out
    assert f"다시 쓸 작업 폴더 설정: {env.ws}" in out
    assert out.count("[y/N]") == 2
    assert "연결했어요: /가계부" in out and f"작업 폴더 설정을 다시 썼어요: {env.ws}" in out
    assert "에이전트 세션은 다시 시작하세요" in out
    [entry] = read_mounts(env.ctx.mounts_file)
    assert entry.added_by == "terminal" and entry.connected


@pytest.mark.parametrize("answers", ["n\n", "y\nn\n", "", "y\n"])
def test_add_declined_changes_nothing(env, answers):
    before = env.settings.read_bytes()
    code, out = run(env, "add", str(env.book), answers=answers)
    assert code == 1 and "연결하지 않았어요" in out
    assert not env.ctx.mounts_file.exists() and env.settings.read_bytes() == before


def test_refused_folder_is_explained(env):
    (env.data / "audit").mkdir(exist_ok=True)
    code, out = run(env, "add", str(env.data / "audit"), answers="y\ny\n")
    assert code == 1 and out.strip() == "gate mount: 게이트 데이터(감사 로그 폴더)는 연결할 수 없어요."


def test_global_setup_and_missing_workspace_notes(tmp_path, isolate):
    root = tmp_path.resolve()
    data = root / "lonely"
    (data / "source").mkdir(parents=True)
    folder = root / "가계부"
    folder.mkdir()
    isolate.parent.mkdir(parents=True)
    isolate.with_name(isolate.name + ".gate-setup.json").write_text("{}", encoding="utf-8")
    args = argparse.Namespace(mount_command="add", folder=str(folder), policy_path=str(data / "policy.yaml"),
                              audit_path=None, socket=None)
    out = io.StringIO()
    assert mount_cli.run(args, stdin=Tty("y\ny\n"), stdout=out) == 0
    text = out.getvalue()
    assert "주의: 등록된 작업 폴더가 없어요" in text
    assert "`gate setup`을 다시 실행하세요" in text and "`gate workspace init`으로 작업 폴더를 만드세요" in text


def test_remove_then_unprotect(env):
    run(env, "add", str(env.book), answers="y\ny\n")
    code, out = run(env, "remove", "가계부", answers="y\n")
    assert code == 0 and "뺐어요: /가계부" in out and "gate mount unprotect 가계부" in out
    assert out.count("[y/N]") == 1
    assert read_mounts(env.ctx.mounts_file)[0].connected is False
    assert str(env.book) in env.settings.read_text(encoding="utf-8")  # 보호는 남음
    code, out = run(env, "unprotect", "가계부", answers="y\nn\n")  # 두 번째 질문에서 취소
    assert code == 1 and "보호는 그대로예요" in out and read_mounts(env.ctx.mounts_file)
    code, out = run(env, "unprotect", "가계부", answers="y\ny\n")
    assert code == 0 and f"보호를 풀었어요: {env.book}" in out and out.count("[y/N]") == 2
    assert read_mounts(env.ctx.mounts_file) == ()
    assert str(env.book) not in env.settings.read_text(encoding="utf-8")


def test_errors_are_reported_with_exit_1(env):
    code, out = run(env, "remove", "없는이름", answers="y\n")
    assert code == 1 and "목록에 없어요" in out
    run(env, "add", str(env.book), answers="y\ny\n")
    code, out = run(env, "unprotect", "가계부", answers="y\ny\n")
    assert code == 1 and "먼저 게이트에서 뺀" in out


# --- gate setup·gate workspace init이 목록을 읽는다 ---

def test_gate_setup_protects_every_listed_folder(env, tmp_path, capsys):
    apply(env.ctx, plan_add(env.ctx, env.book, added_by="terminal"))
    gone = env.root / "gone"
    gone.mkdir()
    apply(env.ctx, plan_add(env.ctx, gone, added_by="terminal"))
    apply(env.ctx, plan_remove(env.ctx, "gone"))  # 보호만 남은 것도 막는다
    settings = tmp_path / "test-settings.json"
    args = ["--socket", str(env.paths.socket), "setup", "--source", str(env.paths.source),
            "--policy", str(env.paths.policy), "--audit", str(env.paths.audit), "--settings-file", str(settings)]
    assert cli.main([*args, "--print"]) == 0
    fragment = json.loads(capsys.readouterr().out)
    deny_read = fragment["sandbox"]["filesystem"]["denyRead"]
    assert str(env.book) in deny_read and str(gone) in deny_read
    assert str(env.ctx.mounts_file) in deny_read and str(env.ctx.workspaces_file) in deny_read
    assert f"Read(/{env.book}/**)" in fragment["permissions"]["deny"]
    assert cli.main([*args, "--yes"]) == 0
    capsys.readouterr()
    new = env.root / "새 폴더"
    new.mkdir()
    apply(env.ctx, plan_add(env.ctx, new, added_by="terminal"))
    assert cli.main([*args, "--yes"]) == 0  # 다시 실행하면 빠진 것만 덧붙인다
    assert "적용했습니다" in capsys.readouterr().out
    assert str(new) in json.loads(settings.read_text(encoding="utf-8"))["sandbox"]["filesystem"]["denyRead"]


def test_gate_setup_stops_when_the_list_is_broken(env, tmp_path, capsys):
    env.ctx.mounts_file.write_text("version: 1\nmounts: oops\n", encoding="utf-8")
    settings = tmp_path / "test-settings.json"
    code = cli.main(["--socket", str(env.paths.socket), "setup", "--source", str(env.paths.source),
                     "--policy", str(env.paths.policy), "--audit", str(env.paths.audit),
                     "--settings-file", str(settings), "--yes"])
    assert code == 1 and "폴더 목록 파일" in capsys.readouterr().err and not settings.exists()


def test_workspace_init_reads_the_list(env):
    apply(env.ctx, plan_add(env.ctx, env.book, added_by="terminal"))
    other = env.root / "second-ws"
    init_workspace(other, env.paths)
    text = (other / ".claude" / "settings.json").read_text(encoding="utf-8")
    assert str(env.book) in json.loads(text)["sandbox"]["filesystem"]["denyRead"]
    assert [ws.path for ws in mc.list_mounts(env.ctx).workspaces] == [str(env.ws), str(other)]
    assert all(ws.state == "ok" for ws in mc.list_mounts(env.ctx).workspaces)


# --- gate serve가 쓰는 저장소 ---

def test_serve_uses_the_list_only_with_a_policy_file(env, capsys):
    apply(env.ctx, plan_add(env.ctx, env.book, added_by="terminal"))
    store, mounts = cli._make_store(str(env.paths.source), None, str(env.paths.socket), None)
    assert isinstance(store, PlainStore) and mounts is None
    cli._announce_mounts(store.root, mounts)
    assert f"원본: 기본 {env.paths.source}만 씁니다" in capsys.readouterr().err
    with pytest.raises(GateError):  # 정책 파일 없이 띄우면 목록을 보지 않는다
        store.read("/가계부/메모.txt")
    store, mounts = cli._make_store(str(env.paths.source), str(env.paths.policy), str(env.paths.socket),
                                    str(env.paths.audit))
    assert isinstance(store, MountedStore) and store.root == env.paths.source
    assert store.read("/가계부/메모.txt") == "내용\n".encode()
    cli._announce_mounts(store.root, mounts)
    err = capsys.readouterr().err
    assert f"원본: 기본 {env.paths.source} + 추가 1개 (/가계부)" in err
    env.ctx.mounts_file.write_text("version: 5\n", encoding="utf-8")
    cli._announce_mounts(store.root, mounts)
    err = capsys.readouterr().err
    assert "폴더 목록 파일 오류. 고칠 때까지 내용 읽기를 모두 차단합니다" in err and "추가 폴더를 보여 주지 않습니다" in err
