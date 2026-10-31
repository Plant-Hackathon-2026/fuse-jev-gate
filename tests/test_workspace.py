import json
from pathlib import Path

import pytest

from gate import cli
from gate.workspace import GatePaths, WorkspaceError, agent_settings, init_workspace, resolve_paths

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
    assert f"Read({root}/policy.yaml)" in deny and f"Edit({root}/policy.yaml)" in deny
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
