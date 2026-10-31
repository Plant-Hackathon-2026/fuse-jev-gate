import hashlib
import json
import stat
from pathlib import Path

import pytest

from gate import cli
from gate.mounts import MountEntry, read_workspaces, write_mounts
from gate.workspace import GatePaths, WorkspaceError, agent_guide, agent_settings, init_workspace, resolve_paths

EXAMPLE_ROOT = Path("/path/to/hackathon/gate-data")
EXAMPLE = GatePaths(
    source=EXAMPLE_ROOT / "source",
    socket=EXAMPLE_ROOT / "run" / "gate.sock",
    policy=EXAMPLE_ROOT / "policy.yaml",
    audit=EXAMPLE_ROOT / "audit" / "audit.jsonl",
    gate_bin=Path("/path/to/hackathon/fuse-jev-gate/.venv/bin/gate"),
)


@pytest.fixture
def data(tmp_path):
    root = tmp_path / "gate-data"
    (root / "source").mkdir(parents=True)
    paths = resolve_paths(root / "source", root / "run" / "gate.sock", root / "policy.yaml",
                          root / "audit" / "audit.jsonl", tmp_path / "bin" / "gate")
    return root.resolve(), paths


@pytest.mark.xfail(strict=True, reason="Phase 9-1에서 mounts.yaml·workspaces.yaml 막기가 더해졌다. sandbox/는 9-1이 "
                   "건드리지 않아 예시를 아직 다시 만들지 않았다. 다시 만들면(생성기 결과로 덮으면) 이 표시를 지운다")
def test_example_template_matches_generator():
    """sandbox/agent-settings.json은 생성기 결과의 예시다. 둘이 어긋나지 않게 한다."""
    template = Path(__file__).parent.parent / "sandbox" / "agent-settings.json"
    assert json.loads(template.read_text(encoding="utf-8")) == agent_settings(EXAMPLE)


def test_permission_rules_use_double_slash_absolute_paths():
    deny = agent_settings(EXAMPLE)["permissions"]["deny"]
    root = "//path/to/hackathon/gate-data"
    for target in ("source", "policy-history", "audit"):
        for tool in ("Read", "Edit"):
            assert f"{tool}({root}/{target})" in deny
            assert f"{tool}({root}/{target}/**)" in deny
    for name in ("policy.yaml", "mounts.yaml", "workspaces.yaml"):  # 파일은 패턴 하나
        assert f"Read({root}/{name})" in deny and f"Edit({root}/{name})" in deny
        assert f"Read({root}/{name}/**)" not in deny
    assert f"Edit({root}/run/**)" in deny
    assert not any(rule.startswith("Read(") and "/run" in rule for rule in deny)  # 게이트 소켓 연결은 막지 않는다
    for rule in deny:
        if "(/" in rule and not rule.startswith(("Edit(~", "Read(~")):
            assert "(//" in rule, rule  # 한 슬래시는 작업 폴더 기준 상대 경로가 된다


def test_claude_config_is_not_editable():
    deny = agent_settings(EXAMPLE)["permissions"]["deny"]
    for rule in ("Edit(.claude/**)", "Edit(.mcp.json)", "Edit(~/.claude/**)", "Edit(~/.claude.json)"):
        assert rule in deny
    assert agent_settings(EXAMPLE)["permissions"]["disableBypassPermissionsMode"] == "disable"


def test_sandbox_settings():
    s = agent_settings(EXAMPLE)["sandbox"]
    assert s["enabled"] is True and s["failIfUnavailable"] is True and s["allowUnsandboxedCommands"] is False
    assert "excludedCommands" not in s
    assert s["network"] == {"allowUnixSockets": ["/path/to/hackathon/gate-data/run/gate.sock"]}  # 승인 소켓은 없음
    assert s["filesystem"]["denyRead"] == [
        "/path/to/hackathon/gate-data/source",
        "/path/to/hackathon/gate-data/policy.yaml",
        "/path/to/hackathon/gate-data/mounts.yaml",
        "/path/to/hackathon/gate-data/workspaces.yaml",
        "/path/to/hackathon/gate-data/policy-history",
        "/path/to/hackathon/gate-data/audit",
    ]
    assert agent_settings(EXAMPLE)["env"] == {"GATE_SOCKET": "/path/to/hackathon/gate-data/run/gate.sock"}


def test_init_writes_settings_and_guide(data):
    root, paths = data
    written = init_workspace(root / "agent-workspace", paths)
    settings = root / "agent-workspace" / ".claude" / "settings.json"
    guide = root / "agent-workspace" / "CLAUDE.md"
    assert written == {settings: "씀", guide: "씀"}
    assert json.loads(settings.read_text(encoding="utf-8")) == agent_settings(paths)
    assert str(paths.gate_bin) in guide.read_text(encoding="utf-8")
    assert init_workspace(root / "agent-workspace", paths) == {settings: "그대로", guide: "그대로"}


def test_guide_tells_agent_both_masking_forms():
    """정규식은 같은 길이 `*`, 규칙·NER은 `[이름]`처럼 종류 이름으로 가린다. 에이전트가 둘 다 원래 값이 아님을 알게 한다."""
    guide = agent_guide(EXAMPLE)
    line = next(line for line in guide.splitlines() if "가려져 나옵니다" in line)
    for form in ("같은 길이의 `*`", "`[이름]`", "`[IP]`"):
        assert form in line, form
    assert "원래 값이 아닙니다" in line


def test_init_does_not_overwrite_different_file_without_force(data):
    root, paths = data
    guide = root / "agent-workspace" / "CLAUDE.md"
    guide.parent.mkdir()
    guide.write_text("사용자가 고친 안내\n", encoding="utf-8")
    with pytest.raises(WorkspaceError):
        init_workspace(root / "agent-workspace", paths)
    assert guide.read_text(encoding="utf-8") == "사용자가 고친 안내\n"
    assert init_workspace(root / "agent-workspace", paths, force=True)[guide] == "씀"


@pytest.mark.parametrize("folder", [
    "source/agent",          # 원본 안
    ".",                     # 원본·정책·감사 로그를 담음
    "audit",
    "run",
    "policy-history/ws",
])
def test_workspace_must_not_overlap_protected_paths(data, folder):
    root, paths = data
    with pytest.raises(WorkspaceError):
        init_workspace(root / folder, paths)


def test_pattern_characters_are_rejected(tmp_path):
    (tmp_path / "src[1]").mkdir()
    with pytest.raises(WorkspaceError):
        resolve_paths(tmp_path / "src[1]", tmp_path / "run" / "gate.sock", tmp_path / "policy.yaml",
                      tmp_path / "audit" / "audit.jsonl", tmp_path / "gate")


def test_missing_source_is_rejected(tmp_path):
    with pytest.raises(WorkspaceError):
        resolve_paths(tmp_path / "없음", tmp_path / "run" / "gate.sock", tmp_path / "policy.yaml",
                      tmp_path / "audit" / "audit.jsonl", tmp_path / "gate")


def test_cli_init(data, capsys):
    root, paths = data
    code = cli.main(["--socket", str(paths.socket), "workspace", "init", str(root / "agent-workspace"),
                     "--source", str(paths.source), "--policy", str(paths.policy), "--audit", str(paths.audit),
                     "--gate-bin", str(paths.gate_bin)])
    assert code == 0
    assert (root / "agent-workspace" / ".claude" / "settings.json").is_file()
    assert "하위 폴더에서 띄우면" in capsys.readouterr().out


def test_cli_init_requires_every_protected_path(data, capsys, monkeypatch):
    root, paths = data
    monkeypatch.delenv("GATE_AUDIT", raising=False)
    code = cli.main(["--socket", str(paths.socket), "workspace", "init", str(root / "agent-workspace"),
                     "--source", str(paths.source), "--policy", str(paths.policy), "--gate-bin", str(paths.gate_bin)])
    assert code == 1
    assert "--audit(GATE_AUDIT)" in capsys.readouterr().err
    assert not (root / "agent-workspace").exists()


# --- Phase 9: 추가 폴더·작업 폴더 등록 ---

def _entry(name, path, connected=True):
    return MountEntry(name, str(path), "2026-11-01T10:00:00+09:00", "terminal", connected)


def test_mounts_are_protected_like_the_source():
    paths = GatePaths(**{**EXAMPLE.__dict__, "mounts": (Path("/Volumes/외장/가계부"), Path("/Users/me/Documents/옛"))})
    settings = agent_settings(paths)
    deny = settings["permissions"]["deny"]
    for folder in ("/Volumes/외장/가계부", "/Users/me/Documents/옛"):
        for tool in ("Read", "Edit"):
            assert f"{tool}(/{folder})" in deny and f"{tool}(/{folder}/**)" in deny
        assert folder in settings["sandbox"]["filesystem"]["denyRead"]
    assert settings["sandbox"]["filesystem"]["denyRead"][:3] == [
        "/path/to/hackathon/gate-data/source", "/Volumes/외장/가계부", "/Users/me/Documents/옛"]


def test_resolve_paths_accepts_missing_mounts_but_not_pattern_characters(tmp_path):
    (tmp_path / "src").mkdir()
    args = (tmp_path / "src", tmp_path / "run" / "gate.sock", tmp_path / "policy.yaml",
            tmp_path / "audit" / "audit.jsonl", tmp_path / "gate")
    paths = resolve_paths(*args, mounts=[tmp_path / "없는 폴더"])
    assert paths.mounts == ((tmp_path / "없는 폴더").resolve(),)
    with pytest.raises(WorkspaceError, match="패턴"):
        resolve_paths(*args, mounts=[tmp_path / "a[1]"])


def test_symlinked_policy_file_is_rejected(tmp_path):
    """목록·기록 파일은 정책 파일(링크) 옆에서 찾는데, 막는 경로는 링크를 따라간 곳이 되어 어긋난다."""
    (tmp_path / "src").mkdir()
    (tmp_path / "elsewhere").mkdir()
    (tmp_path / "elsewhere" / "policy.yaml").write_text("", encoding="utf-8")
    (tmp_path / "policy.yaml").symlink_to(tmp_path / "elsewhere" / "policy.yaml")
    with pytest.raises(WorkspaceError, match="심볼릭 링크"):
        resolve_paths(tmp_path / "src", tmp_path / "run" / "gate.sock", tmp_path / "policy.yaml",
                      tmp_path / "audit" / "audit.jsonl", tmp_path / "gate")


def test_init_protects_every_listed_mount_and_registers_the_workspace(data):
    root, paths = data
    outside = root.parent / "가계부"
    write_mounts(paths.mounts_file, [_entry("가계부", outside), _entry("옛 폴더", root.parent / "없음", False)])
    init_workspace(root / "agent-workspace", paths)
    settings_file = root / "agent-workspace" / ".claude" / "settings.json"
    text = settings_file.read_text(encoding="utf-8")
    deny_read = json.loads(text)["sandbox"]["filesystem"]["denyRead"]
    assert str(outside) in deny_read and str(root.parent / "없음") in deny_read  # 보호만 남은 것·없는 폴더도
    [record] = read_workspaces(paths.workspaces_file)
    assert record.path == str(root / "agent-workspace")
    assert (record.source, record.socket, record.policy, record.audit, record.gate_bin) == tuple(
        str(p) for p in (paths.source, paths.socket, paths.policy, paths.audit, paths.gate_bin))
    assert record.settings_sha256 == hashlib.sha256(text.encode("utf-8")).hexdigest()
    assert stat.S_IMODE(paths.workspaces_file.stat().st_mode) == 0o600


def test_init_again_updates_the_same_record(data):
    root, paths = data
    init_workspace(root / "agent-workspace", paths)
    init_workspace(root.parent / "second", paths)
    write_mounts(paths.mounts_file, [_entry("가계부", root.parent / "가계부")])
    init_workspace(root / "agent-workspace", paths, force=True)
    records = read_workspaces(paths.workspaces_file)
    assert [r.path for r in records] == [str(root / "agent-workspace"), str(root.parent / "second")]
    text = (root / "agent-workspace" / ".claude" / "settings.json").read_text(encoding="utf-8")
    assert records[0].settings_sha256 == hashlib.sha256(text.encode("utf-8")).hexdigest()


@pytest.mark.parametrize("which", ["mounts", "workspaces"])
def test_init_writes_nothing_when_list_files_are_broken(data, which):
    root, paths = data
    broken = paths.mounts_file if which == "mounts" else paths.workspaces_file
    broken.write_text("version: 1\nmounts: [ {name: x}\n" if which == "mounts" else "version: 2\n", encoding="utf-8")
    with pytest.raises(WorkspaceError, match="문제"):
        init_workspace(root / "agent-workspace", paths)
    assert not (root / "agent-workspace").exists()


def test_workspace_must_not_overlap_a_mount(data):
    root, paths = data
    write_mounts(paths.mounts_file, [_entry("가계부", root.parent / "가계부")])
    with pytest.raises(WorkspaceError, match="겹칩니다"):
        init_workspace(root.parent / "가계부" / "agent", paths)
    with pytest.raises(WorkspaceError, match="겹칩니다"):  # 대소문자만 다른 경로도(맥 기본 디스크)
        init_workspace(root / "Policy-History" / "ws", paths)


def test_cli_init_reports_registration(data, capsys):
    root, paths = data
    code = cli.main(["--socket", str(paths.socket), "workspace", "init", str(root.parent / "ws"),
                     "--source", str(paths.source), "--policy", str(paths.policy), "--audit", str(paths.audit),
                     "--gate-bin", str(paths.gate_bin)])
    assert code == 0
    assert "작업 폴더를 등록했습니다" in capsys.readouterr().out
    assert [r.path for r in read_workspaces(paths.workspaces_file)] == [str(root.parent / "ws")]
