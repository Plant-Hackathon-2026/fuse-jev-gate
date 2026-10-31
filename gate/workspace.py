"""`gate workspace init`: 에이전트 전용 작업 폴더의 Claude Code 설정(Phase 6).

작업 폴더에 `.claude/settings.json`(권한 규칙 + 샌드박스)과 `CLAUDE.md`(gate CLI 쓰는 법)를 만든다.
이 폴더에서 띄운 Claude Code는 원본 폴더를 직접 읽지 못하고 게이트 소켓으로만 읽는다.
형식은 공식 문서로 확인했다(plans/implementation-plan.md 5절 Phase 6 "설정 형식 확인", Claude Code 2.1.314).

막는 것
  | 대상                                          | Read | Edit | 이유                                        |
  |-----------------------------------------------|------|------|---------------------------------------------|
  | 원본 폴더                                      | deny | deny | 게이트를 거치지 않는 읽기                     |
  | 정책 파일, 정책 기록 폴더                        | deny | deny | 정책 바꾸기·undo로 바꿔 넣기                  |
  | 감사 로그 폴더(audit.key 포함)                   | deny | deny | 키가 새면 파일 해시로 원문을 맞혀 볼 수 있다   |
  | 소켓 폴더                                      |  -   | deny | 소켓 바꿔치기. 연결은 allowUnixSockets로 정한다 |
  | `.claude/`·`.mcp.json`(깊이 무관), `~/.claude/`, `~/.claude.json` | - | deny | 훅·MCP 서버·스킬 훅은 샌드박스 밖에서 돈다 |
  - Read·Edit deny 규칙은 샌드박스 denyRead·denyWrite에도 합쳐진다. 원본·정책·기록·감사 로그 폴더는 denyRead에 한 번 더
    적는다(권한 규칙 표기를 잘못 써도 샌드박스에는 남게).
  - 샌드박스: 켬, 시작 못 하면 Claude Code도 시작하지 않음, 샌드박스 밖 재실행 금지, excludedCommands 없음.
    Unix 소켓은 게이트 소켓만 연다(승인 소켓은 막힘). 이 목록은 macOS에서만 쓰인다. Linux는 전부 열기·전부 막기뿐이다.
  - disableBypassPermissionsMode: bypass 모드에서는 Edit 도구의 보호 경로 검사가 꺼진다.
  - env.GATE_SOCKET: 샌드박스 안의 gate CLI가 바로 쓰게.

경로
  - 권한 규칙의 절대 경로는 `//`로 시작한다(`/path`는 작업 폴더 기준). 샌드박스 경로는 `/`로 시작하는 보통 절대 경로다.
  - 모든 경로는 실제 경로(resolve)로 적는다. `*`, `?`, `[`, `]`가 든 경로는 규칙에서 패턴으로 읽히므로 받지 않는다.
  - 작업 폴더는 막는 경로를 담거나 그 안에 있으면 안 된다. 작업 폴더는 에이전트가 쓸 수 있어서, 안쪽 폴더 이름을 바꾸는
    식으로 경로 기준 차단을 피할 수 있다.
  - 프로젝트 설정은 세션을 띄운 폴더의 `.claude/`에서만 읽는다. 작업 폴더의 하위 폴더에서 띄우면 보호되지 않는다.

이미 있는 파일은 내용이 같으면 그대로 두고, 다르면 --force 없이는 덮어쓰지 않는다.
"""

import argparse
import json
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from gate.policy_cli import history_dir

PATTERN_CHARS = set("*?[]")
CLAUDE_CONFIG_EDIT_DENY = ("Edit(.claude/**)", "Edit(.mcp.json)", "Edit(~/.claude/**)", "Edit(~/.claude.json)")


class WorkspaceError(Exception):
    pass


@dataclass(frozen=True)
class GatePaths:
    """작업 폴더 설정에 들어가는 실제 경로. 모두 resolve한 절대 경로다."""

    source: Path
    socket: Path
    policy: Path
    audit: Path      # 감사 로그 파일. 그 폴더(audit.key 포함) 전체를 막는다
    gate_bin: Path

    @property
    def policy_history(self) -> Path:
        return history_dir(self.policy)

    @property
    def audit_dir(self) -> Path:
        return self.audit.parent

    @property
    def run_dir(self) -> Path:
        return self.socket.parent

    def protected(self) -> list[Path]:
        """읽기·쓰기를 모두 막는 경로."""
        return [self.source, self.policy, self.policy_history, self.audit_dir]


def resolve_paths(source: str | os.PathLike, socket: str | os.PathLike, policy: str | os.PathLike,
                  audit: str | os.PathLike, gate_bin: str | os.PathLike) -> GatePaths:
    paths = GatePaths(*(Path(p).expanduser().resolve() for p in (source, socket, policy, audit, gate_bin)))
    for p in (paths.source, paths.socket, paths.policy, paths.audit, paths.gate_bin):
        if PATTERN_CHARS & set(str(p)):
            raise WorkspaceError(f"경로에 *, ?, [, ] 가 있으면 규칙에서 패턴으로 읽혀 지원하지 않습니다: {p}")
    if not paths.source.is_dir():
        raise WorkspaceError(f"원본 폴더가 없습니다: {paths.source}")
    return paths


def _rule_path(path: Path) -> str:
    return "/" + str(path)


def protection_settings(paths: GatePaths) -> dict:
    """원본·정책·기록·감사 로그를 막는 설정(권한 규칙 + 샌드박스 + env). 설치 명령(gate.claude_setup)도 이것을 쓴다."""
    deny = []
    for p in paths.protected():
        # 폴더는 폴더 자체(목록 보기)와 그 안을 둘 다 적는다
        patterns = [_rule_path(p)] if p == paths.policy else [_rule_path(p), _rule_path(p) + "/**"]
        deny += [f"{tool}({pattern})" for tool in ("Read", "Edit") for pattern in patterns]
    deny.append(f"Edit({_rule_path(paths.run_dir)}/**)")
    return {
        "permissions": {"deny": deny},
        "sandbox": {
            "enabled": True,
            "failIfUnavailable": True,
            "allowUnsandboxedCommands": False,
            "filesystem": {"denyRead": [str(p) for p in paths.protected()]},
            "network": {"allowUnixSockets": [str(paths.socket)]},
        },
        "env": {"GATE_SOCKET": str(paths.socket)},
    }


def agent_settings(paths: GatePaths) -> dict:
    """작업 폴더의 .claude/settings.json 내용. protection_settings에 Claude 설정 파일 Edit deny와 bypass 금지를 더한다."""
    settings = protection_settings(paths)
    settings["permissions"]["deny"] += CLAUDE_CONFIG_EDIT_DENY
    settings["permissions"]["disableBypassPermissionsMode"] = "disable"
    return settings


def agent_guide(paths: GatePaths) -> str:
    """작업 폴더의 CLAUDE.md 내용."""
    gate = paths.gate_bin
    return f"""# 이 폴더에서 문서 읽기

원본 문서는 이 컴퓨터의 게이트를 거쳐서만 읽을 수 있습니다. 원본 폴더를 직접 열면 막힙니다.

- 목록: `{gate} ls /` (하위 폴더는 `{gate} ls /폴더`)
- 읽기: `{gate} cat <경로> --purpose "읽는 목적"`
- 찾기: `{gate} search <글자> [경로] [-i]`

- 경로는 `/`로 시작하는 게이트 안 경로입니다.
- 개인정보와 비밀값은 가려져 나옵니다. 번호·비밀값은 같은 길이의 `*`(예: `***-****-****`)로, 이름·주소·소속·장소·IP 등은 `[이름]`·`[주소]`·`[IP]` 같은 표시로 바뀝니다. 둘 다 원래 값이 아닙니다.
- 사용자 승인이 필요한 파일은 사용자가 답할 때까지 잠시 기다립니다.
- `EACCES`는 정책상 읽을 수 없다는 뜻입니다. 다른 방법으로 읽으려 하지 말고 사용자에게 알리세요.
"""


def _overlaps(a: Path, b: Path) -> bool:
    return a == b or a.is_relative_to(b) or b.is_relative_to(a)


def check_workspace(workspace: Path, paths: GatePaths) -> None:
    for p in (*paths.protected(), paths.run_dir):
        if _overlaps(workspace, p):
            raise WorkspaceError(f"작업 폴더 {workspace}가 막아야 할 경로 {p}와 겹칩니다. 서로 바깥에 두세요")
    if PATTERN_CHARS & set(str(workspace)):
        raise WorkspaceError(f"작업 폴더 경로에 *, ?, [, ] 가 있으면 지원하지 않습니다: {workspace}")


def _write(path: Path, text: str, force: bool) -> str:
    if path.exists():
        if path.read_text(encoding="utf-8") == text:
            return "그대로"
        if not force:
            raise WorkspaceError(f"{path}가 이미 있고 내용이 다릅니다. 덮어쓰려면 --force")
    path.write_text(text, encoding="utf-8")
    return "씀"


def init_workspace(workspace: str | os.PathLike, paths: GatePaths, *, force: bool = False) -> dict[Path, str]:
    """설정 파일과 안내 파일을 만든다. {파일: "씀" | "그대로"}."""
    root = Path(workspace).expanduser().resolve()
    check_workspace(root, paths)
    (root / ".claude").mkdir(parents=True, exist_ok=True)
    settings = json.dumps(agent_settings(paths), ensure_ascii=False, indent=2) + "\n"
    files = {root / ".claude" / "settings.json": settings, root / "CLAUDE.md": agent_guide(paths)}
    return {path: _write(path, text, force) for path, text in files.items()}


def _default_gate_bin() -> str | None:
    if Path(sys.argv[0]).name == "gate":
        return os.path.abspath(sys.argv[0])
    return shutil.which("gate")


def register(sub: argparse._SubParsersAction) -> None:
    ws = sub.add_parser("workspace", help="에이전트 작업 폴더 설정 (Claude Code 권한 규칙 + 샌드박스)")
    ws_sub = ws.add_subparsers(dest="workspace_command", required=True)
    init = ws_sub.add_parser("init", help="작업 폴더에 .claude/settings.json과 CLAUDE.md를 만든다")
    init.add_argument("folder", help="에이전트 작업 폴더 (없으면 만든다)")
    init.add_argument("--source", required=True, help="원본 폴더")
    init.add_argument("--policy", default=os.environ.get("GATE_POLICY"), help="정책 파일 (기본: $GATE_POLICY)")
    init.add_argument("--audit", default=os.environ.get("GATE_AUDIT"), help="감사 로그 파일 (기본: $GATE_AUDIT)")
    init.add_argument("--gate-bin", default=None, help="gate 실행 파일 (기본: 지금 실행한 gate)")
    init.add_argument("--force", action="store_true", help="내용이 다른 기존 파일을 덮어쓴다")


def run(args: argparse.Namespace) -> int:
    missing = [name for name, value in (("--socket(GATE_SOCKET)", args.socket), ("--policy(GATE_POLICY)", args.policy),
                                        ("--audit(GATE_AUDIT)", args.audit)) if not value]
    gate_bin = args.gate_bin or _default_gate_bin()
    if not gate_bin:
        missing.append("--gate-bin")
    if missing:
        print(f"gate: 작업 폴더 설정에는 {', '.join(missing)}이 필요합니다 (막을 경로를 정확히 적어야 합니다)",
              file=sys.stderr)
        return 1
    try:
        paths = resolve_paths(args.source, args.socket, args.policy, args.audit, gate_bin)
        written = init_workspace(args.folder, paths, force=args.force)
    except (WorkspaceError, OSError) as exc:
        print(f"gate: {exc}", file=sys.stderr)
        return 1
    for path, state in written.items():
        print(f"{path}: {state}")
    folder = next(iter(written)).parent.parent
    print(f"이 폴더에서 Claude Code를 띄우세요: cd {folder} && claude\n"
          "하위 폴더에서 띄우면 이 설정이 적용되지 않습니다. 세션 안에서 /sandbox로 샌드박스가 켜졌는지 볼 수 있습니다.")
    return 0
