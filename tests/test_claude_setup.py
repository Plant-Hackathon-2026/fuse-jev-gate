"""gate/claude_setup.py: 설치 명령(사용자 설정에 원본 보호 합치기·되돌리기)."""

import json
import os
from pathlib import Path

import pytest

from gate import cli
from gate import claude_setup as s
from gate.workspace import CLAUDE_CONFIG_EDIT_DENY, GatePaths, agent_settings, protection_settings, resolve_paths

EXAMPLE_ROOT = Path("/path/to/hackathon/gate-data")
EXAMPLE = GatePaths(source=EXAMPLE_ROOT / "source", socket=EXAMPLE_ROOT / "run" / "gate.sock",
                    policy=EXAMPLE_ROOT / "policy.yaml", audit=EXAMPLE_ROOT / "audit" / "audit.jsonl",
                    gate_bin=Path("/path/to/hackathon/fuse-jev-gate/.venv/bin/plant"))
FRAGMENT = protection_settings(EXAMPLE)
YES = lambda _: True  # noqa: E731
NO = lambda _: False  # noqa: E731

USER = {
    "model": "opus",
    "permissions": {"allow": ["Bash(npm test)"], "deny": ["Read(./.env)", FRAGMENT["permissions"]["deny"][0]]},
    "sandbox": {"enabled": False, "network": {"allowedDomains": ["example.com"]}},
    "env": {"FOO": "1"},
}


def write(path: Path, data: object) -> Path:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def backups(path: Path) -> list[Path]:
    return sorted(path.parent.glob(path.name + ".gate-backup-*"))


# --- 조각 ---

def test_fragment_is_the_workspace_protection_without_claude_config_rules():
    assert not set(CLAUDE_CONFIG_EDIT_DENY) & set(FRAGMENT["permissions"]["deny"])
    assert "disableBypassPermissionsMode" not in FRAGMENT["permissions"]
    agent = agent_settings(EXAMPLE)
    assert agent["permissions"]["deny"] == FRAGMENT["permissions"]["deny"] + list(CLAUDE_CONFIG_EDIT_DENY)
    assert {k: v for k, v in agent.items() if k != "permissions"} == {k: v for k, v in FRAGMENT.items()
                                                                      if k != "permissions"}


# --- 합치기 ---

def test_merge_into_empty_is_the_fragment_and_second_merge_changes_nothing():
    current: dict = {}
    changes = s.merge(current, FRAGMENT)
    assert current == FRAGMENT
    assert {c.op for c in changes} == {"create", "add", "set"}
    assert s.merge(current, FRAGMENT) == []


def test_merge_keeps_user_values_and_appends_only_missing_items():
    current = json.loads(json.dumps(USER))
    changes = s.merge(current, FRAGMENT)
    deny = current["permissions"]["deny"]
    assert deny[:2] == USER["permissions"]["deny"]  # 사용자 항목이 앞에 그대로
    assert deny[2:] == FRAGMENT["permissions"]["deny"][1:]  # 이미 있던 항목은 다시 넣지 않는다
    assert current["permissions"]["allow"] == ["Bash(npm test)"] and current["model"] == "opus"
    assert current["sandbox"]["network"] == {"allowedDomains": ["example.com"],
                                            "allowUnixSockets": FRAGMENT["sandbox"]["network"]["allowUnixSockets"]}
    assert current["env"] == {"FOO": "1", "GATE_SOCKET": FRAGMENT["env"]["GATE_SOCKET"]}
    enabled = next(c for c in changes if c.path == ("sandbox", "enabled"))
    assert (enabled.op, enabled.before, enabled.had, enabled.value) == ("set", False, True, True)
    assert enabled.describe() == "sandbox.enabled: false → true"


def test_merge_stops_when_a_value_has_the_wrong_kind():
    with pytest.raises(s.SetupError, match="sandbox가 객체가 아닙니다"):
        s.merge({"sandbox": "on"}, FRAGMENT)
    with pytest.raises(s.SetupError, match="permissions.deny가 목록이 아닙니다"):
        s.merge({"permissions": {"deny": "Read(x)"}}, FRAGMENT)


# --- 설치·되돌리기 ---

def test_install_backs_up_records_and_undo_restores(tmp_path):
    settings = write(tmp_path / "settings.json", USER)
    original = settings.read_text(encoding="utf-8")
    out = s.install(settings, FRAGMENT, YES)
    assert out.applied and out.backup == backups(settings)[0]
    assert out.backup.read_text(encoding="utf-8") == original
    assert s.merge(load(settings), FRAGMENT) == []  # 조각이 다 들어갔다
    record = load(s.record_path(settings))
    assert record["created_file"] is False and record["backups"] == [str(out.backup)]
    undone = s.undo(settings, YES)
    assert undone.applied and undone.kept == ()
    assert load(settings) == USER
    assert not s.record_path(settings).exists() and out.backup.exists()  # 백업은 남긴다


def test_install_again_changes_nothing(tmp_path):
    settings = write(tmp_path / "settings.json", USER)
    s.install(settings, FRAGMENT, YES)
    text = settings.read_text(encoding="utf-8")
    out = s.install(settings, FRAGMENT, YES)
    assert out.changes == [] and not out.applied
    assert settings.read_text(encoding="utf-8") == text and len(backups(settings)) == 1


def test_declined_install_changes_nothing(tmp_path):
    settings = write(tmp_path / "settings.json", USER)
    out = s.install(settings, FRAGMENT, NO)
    assert out.changes and not out.applied
    assert load(settings) == USER and backups(settings) == [] and not s.record_path(settings).exists()


def test_invalid_or_conflicting_file_is_not_touched(tmp_path):
    for text in ("{ not json", "[]", json.dumps({"sandbox": "on"})):
        settings = tmp_path / "settings.json"
        settings.write_text(text, encoding="utf-8")
        with pytest.raises(s.SetupError):
            s.install(settings, FRAGMENT, YES)
        assert settings.read_text(encoding="utf-8") == text
        assert backups(settings) == [] and not s.record_path(settings).exists()


def test_undo_leaves_what_the_user_changed_after_install(tmp_path):
    settings = write(tmp_path / "settings.json", {"sandbox": {"enabled": False}})
    s.install(settings, FRAGMENT, YES)
    data = load(settings)
    data["sandbox"]["failIfUnavailable"] = False
    data["permissions"]["deny"].append("Read(//mine)")
    write(settings, data)
    out = s.undo(settings, YES)
    assert out.kept == ("sandbox.failIfUnavailable: 설치 뒤 바뀐 값이라 그대로 둠(false)",)
    assert load(settings) == {"sandbox": {"enabled": False, "failIfUnavailable": False},
                              "permissions": {"deny": ["Read(//mine)"]}}


def test_file_created_by_install_is_removed_by_undo(tmp_path):
    settings = tmp_path / "new" / "settings.json"
    out = s.install(settings, FRAGMENT, YES)
    assert out.applied and out.backup is None and load(settings) == FRAGMENT
    s.undo(settings, YES)
    assert not settings.exists() and not s.record_path(settings).exists()


def test_declined_undo_and_missing_record(tmp_path):
    settings = write(tmp_path / "settings.json", USER)
    with pytest.raises(s.SetupError, match="설치 기록이 없습니다"):
        s.undo(settings, YES)
    s.install(settings, FRAGMENT, YES)
    assert not s.undo(settings, NO).applied
    assert s.record_path(settings).exists() and s.merge(load(settings), FRAGMENT) == []


def test_file_mode_is_kept(tmp_path):
    settings = write(tmp_path / "settings.json", USER)
    settings.chmod(0o600)
    s.install(settings, FRAGMENT, YES)
    assert settings.stat().st_mode & 0o777 == 0o600


def test_default_settings_file_follows_claude_config_dir(monkeypatch, tmp_path):
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    assert s.default_settings_file() == Path.home() / ".claude" / "settings.json"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cfg"))
    assert s.default_settings_file() == tmp_path / "cfg" / "settings.json"


# --- gate setup ---

@pytest.fixture
def env(tmp_path, monkeypatch):
    root = tmp_path / "gate-data"
    (root / "source").mkdir(parents=True)
    monkeypatch.setenv("GATE_SOCKET", str(root / "run" / "gate.sock"))
    monkeypatch.setenv("GATE_POLICY", str(root / "policy.yaml"))
    monkeypatch.setenv("GATE_AUDIT", str(root / "audit" / "audit.jsonl"))
    paths = resolve_paths(root / "source", root / "run" / "gate.sock", root / "policy.yaml",
                          root / "audit" / "audit.jsonl", tmp_path / "gate")
    return root, protection_settings(paths)


def test_cli_print_outputs_fragment_without_touching_files(env, tmp_path, capsys):
    root, fragment = env
    settings = tmp_path / "settings.json"
    assert cli.main(["setup", "--source", str(root / "source"), "--settings-file", str(settings), "--print"]) == 0
    assert json.loads(capsys.readouterr().out) == fragment
    assert not settings.exists()


def test_cli_install_and_undo(env, tmp_path, capsys):
    root, fragment = env
    settings = write(tmp_path / "settings.json", USER)
    args = ["setup", "--source", str(root / "source"), "--settings-file", str(settings)]
    assert cli.main(args) == 1  # 터미널이 아니면 --yes 없이는 바꾸지 않는다
    assert "확인이 필요합니다" in capsys.readouterr().err and load(settings) == USER
    assert cli.main([*args, "--yes"]) == 0
    assert "적용했습니다" in capsys.readouterr().out and s.merge(load(settings), fragment) == []
    assert cli.main([*args, "--yes"]) == 0
    assert "이미 적용되어 있습니다" in capsys.readouterr().out
    assert cli.main(["setup", "--undo", "--yes", "--settings-file", str(settings)]) == 0
    assert load(settings) == USER


def test_cli_needs_paths(env, tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("GATE_AUDIT")
    assert cli.main(["setup", "--settings-file", str(tmp_path / "x.json"), "--yes"]) == 1
    err = capsys.readouterr().err
    # --source는 숨은 개발용 옵션이라 필요하다고 하지 않는다(2026-11-01 기본 폴더 없앰)
    assert "--source" not in err and "--audit(GATE_AUDIT)" in err and not (tmp_path / "x.json").exists()


def test_cli_without_source_protects_connected_folders_and_data(env, tmp_path, capsys):
    """--source 없이: 연결한 폴더(mounts.yaml)와 PLANT 데이터만 막는다. 도움말에 --source가 없다."""
    root, _ = env
    book = tmp_path / "docs" / "가계부"
    book.mkdir(parents=True)
    write_mounts(root / "policy.yaml", book)
    settings = tmp_path / "settings.json"
    assert cli.main(["setup", "--settings-file", str(settings), "--print"]) == 0
    fragment = json.loads(capsys.readouterr().out)
    deny_read = fragment["sandbox"]["filesystem"]["denyRead"]
    real = str(book.resolve())
    assert real in deny_read and str((root / "policy.yaml").resolve()) in deny_read
    assert not any(p.endswith("/source") for p in deny_read)
    assert f"Read(/{real})" in fragment["permissions"]["deny"]
    assert "--source" not in cli.build_parser()._subparsers._group_actions[0].choices["setup"].format_help()


def write_mounts(policy, folder):
    from gate.mounts import MountEntry, mounts_file_for
    from gate.mounts import write_mounts as write_list
    write_list(mounts_file_for(policy), [MountEntry("가계부", str(folder.resolve()), "2026-11-01T10:00:00+09:00",
                                                    "terminal", True)])


def test_cli_writes_through_a_symlinked_settings_file(env, tmp_path):
    root, fragment = env
    (tmp_path / "dotfiles").mkdir()
    real = write(tmp_path / "dotfiles" / "settings.json", USER)
    link = tmp_path / "settings.json"
    link.symlink_to(real)
    assert cli.main(["setup", "--source", str(root / "source"), "--settings-file", str(link), "--yes"]) == 0
    assert link.is_symlink() and os.readlink(link) == str(real)
    assert s.merge(load(real), fragment) == [] and s.record_path(real).exists()
