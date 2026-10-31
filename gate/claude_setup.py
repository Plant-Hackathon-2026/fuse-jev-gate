"""`gate setup`: 게이트를 설치할 때 Claude Code 사용자 설정(~/.claude/settings.json)에 원본 보호 설정을 합친다(Phase 6).

작업 폴더가 아닌 곳에서 띄운 Claude Code 세션도 원본을 직접 읽지 못하게 한다. 합치는 조각은
gate.workspace.protection_settings다(작업 폴더 설정과 같은 막기 규칙).
  - 원본·정책 파일·정책 기록·감사 로그 폴더: Read/Edit deny + 샌드박스 denyRead. 소켓 폴더: Edit deny.
  - 샌드박스: 켬, 시작 못 하면 Claude Code도 시작하지 않음, 샌드박스 밖 재실행 금지. Unix 소켓은 게이트 소켓만 연다.
  - env.GATE_SOCKET

범위(2026-10-31 사용자 결정)
  - 설정 파일 바꾸기, bypass 모드 같은 샌드박스 우회는 다루지 않는다. 실제 배포에서는 이 조각을 MDM 관리 설정으로
    내려보낸다고 가정한다. 관리 설정은 사용자·프로젝트·로컬·--settings 설정이 덮어쓰지 못한다. `--print`가 그 JSON을 낸다.
  - 그래서 작업 폴더 설정에 있는 Claude 설정 파일 Edit deny와 disableBypassPermissionsMode는 넣지 않는다.

합치기
  - 목록은 빠진 항목만 뒤에 덧붙이고, 단일 값은 덮어쓰되 이전 값을 기록한다. 다시 실행하면 바뀌는 것이 없다.
  - 들어갈 자리에 다른 종류의 값이 있으면(예: 객체 자리에 문자열) 고치지 않고 멈춘다. JSON이 아닌 파일도 고치지 않는다.
  - 바뀔 내용을 보여 주고 y/N으로 확인한다(--yes로 건너뜀). 원래 파일은 <파일>.gate-backup-<시각>으로 복사해 둔다.
  - 바꾼 내용은 <파일>.gate-setup.json에 남긴다. --undo는 그 기록대로 덧붙인 항목을 빼고, 단일 값을 되돌리고,
    새로 만든 빈 객체·목록을 지운다. 설치 뒤 사용자가 바꾼 단일 값은 건드리지 않는다.
  - 쓰기는 같은 폴더의 임시 파일에 쓰고 교체한다(os.replace). 원래 파일 권한을 유지한다.
  - --settings-file로 대상을 바꾼다. 개발 맥에서는 전역 설정을 바꾸지 않고 시험용 파일에 써서 `claude --settings`로 시험한다.
"""

import argparse
import json
import os
import shutil
import sys
import tempfile
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from gate import workspace
from gate.workspace import GatePaths, WorkspaceError, protection_settings, resolve_paths

RECORD_VERSION = 1


class SetupError(Exception):
    pass


@dataclass(frozen=True)
class Change:
    """설정에 가한 변경 하나. op: create(빈 객체·목록을 만듦), add(목록에 항목 덧붙임), set(단일 값 바꿈)."""

    op: str
    path: tuple[str, ...]
    value: object = None   # add: 덧붙인 항목, set: 새 값, create: {} 또는 []
    before: object = None  # set: 이전 값
    had: bool = False      # set: 이전 값이 있었나

    def describe(self) -> str:
        where = ".".join(self.path)
        if self.op == "add":
            return f"{where}에 추가: {_show(self.value)}"
        if self.op == "set":
            return f"{where}: {_show(self.before) if self.had else '없음'} → {_show(self.value)}"
        return f"{where} 새로 만듦"


def _show(value: object) -> str:
    return json.dumps(value, ensure_ascii=False)


def default_settings_file() -> Path:
    """Claude Code 사용자 설정 파일. CLAUDE_CONFIG_DIR가 있으면 그 폴더의 settings.json이다."""
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude").expanduser() / "settings.json"


def record_path(settings_file: Path) -> Path:
    return settings_file.with_name(settings_file.name + ".gate-setup.json")


def merge(current: dict, fragment: dict, path: tuple[str, ...] = ()) -> list[Change]:
    """fragment를 current에 합친다(current를 바꾼다). 바꾼 것 목록을 돌려준다."""
    changes: list[Change] = []
    for key, want in fragment.items():
        here = (*path, key)
        where = ".".join(here)
        if isinstance(want, (dict, list)):
            kind = type(want)
            if key not in current:
                current[key] = kind()
                changes.append(Change("create", here, kind()))
            elif not isinstance(current[key], kind):
                raise SetupError(f"설정의 {where}가 {'객체가' if kind is dict else '목록이'} 아닙니다"
                                 f"({_show(current[key])}). 직접 확인하고 고친 뒤 다시 실행하세요")
            if kind is dict:
                changes += merge(current[key], want, here)
            else:
                for item in want:
                    if item not in current[key]:
                        current[key].append(item)
                        changes.append(Change("add", here, item))
        elif key not in current or current[key] != want:
            changes.append(Change("set", here, want, current.get(key), key in current))
            current[key] = want
    return changes


def _parent(settings: dict, path: tuple[str, ...]) -> dict | None:
    node: object = settings
    for key in path[:-1]:
        node = node.get(key) if isinstance(node, dict) else None
    return node if isinstance(node, dict) else None


def revert(settings: dict, changes: list[Change]) -> list[str]:
    """changes를 거꾸로 되돌린다(settings를 바꾼다). 되돌리지 않고 둔 것의 설명 목록을 돌려준다."""
    kept: list[str] = []
    for c in reversed(changes):
        parent = _parent(settings, c.path)
        key = c.path[-1]
        where = ".".join(c.path)
        if parent is None or key not in parent:
            continue
        if c.op == "add":
            if isinstance(parent[key], list) and c.value in parent[key]:
                parent[key].remove(c.value)
        elif c.op == "set":
            if parent[key] != c.value:
                kept.append(f"{where}: 설치 뒤 바뀐 값이라 그대로 둠({_show(parent[key])})")
            elif c.had:
                parent[key] = c.before
            else:
                del parent[key]
        elif parent[key] in ({}, []):
            del parent[key]
    return kept


def read_settings(settings_file: Path) -> dict:
    if not settings_file.exists():
        return {}
    try:
        data = json.loads(settings_file.read_text(encoding="utf-8") or "{}")
    except (UnicodeDecodeError, ValueError) as exc:
        raise SetupError(f"{settings_file}가 JSON이 아니라 고치지 않습니다: {exc}") from None
    if not isinstance(data, dict):
        raise SetupError(f"{settings_file}의 최상위가 객체가 아니라 고치지 않습니다")
    return data


def _write_json(path: Path, data: dict) -> None:
    """같은 폴더의 임시 파일에 쓰고 교체한다. 있던 파일의 권한을 유지한다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = path.stat().st_mode & 0o777 if path.exists() else None
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        if mode is not None:
            os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _backup(settings_file: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = settings_file.with_name(f"{settings_file.name}.gate-backup-{stamp}")
    n = 1
    while target.exists():
        n += 1
        target = settings_file.with_name(f"{settings_file.name}.gate-backup-{stamp}-{n}")
    shutil.copy2(settings_file, target)
    return target


def _load_record(settings_file: Path) -> dict | None:
    path = record_path(settings_file)
    if not path.exists():
        return None
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
        changes = [Change(c["op"], tuple(c["path"]), c.get("value"), c.get("before"), bool(c.get("had")))
                   for c in record["changes"]]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise SetupError(f"설치 기록 {path}를 읽을 수 없습니다: {exc}") from None
    return {**record, "changes": changes}


def _save_record(settings_file: Path, record: dict) -> None:
    _write_json(record_path(settings_file), {**record, "changes": [asdict(c) for c in record["changes"]]})


def _summary(changes: list[Change]) -> str:
    return "\n".join(f"  {c.describe()}" for c in changes if c.op != "create")


@dataclass(frozen=True)
class Outcome:
    changes: list[Change]
    applied: bool
    backup: Path | None = None
    kept: tuple[str, ...] = ()


def install(settings_file: Path, fragment: dict, confirm: Callable[[str], bool]) -> Outcome:
    """fragment를 settings_file에 합친다. 바뀔 것이 없으면 쓰지 않는다. confirm이 거절하면 아무것도 바꾸지 않는다."""
    current = read_settings(settings_file)
    changes = merge(current, fragment)
    if not changes:
        return Outcome([], applied=False)
    if not confirm(f"{settings_file}에 다음을 합칩니다:\n{_summary(changes)}"):
        return Outcome(changes, applied=False)
    existed = settings_file.exists()
    record = _load_record(settings_file) or {"version": RECORD_VERSION, "settings_file": str(settings_file),
                                             "created_file": not existed, "backups": [], "changes": []}
    backup = _backup(settings_file) if existed else None
    if backup is not None:
        record["backups"].append(str(backup))
    record["changes"] += changes
    _write_json(settings_file, current)
    _save_record(settings_file, record)
    return Outcome(changes, applied=True, backup=backup)


def undo(settings_file: Path, confirm: Callable[[str], bool]) -> Outcome:
    """설치 기록대로 되돌린다. 설치가 새로 만든 파일이 비면 파일을 지운다. 백업 파일은 남긴다."""
    record = _load_record(settings_file)
    if record is None:
        raise SetupError(f"설치 기록이 없습니다: {record_path(settings_file)}")
    changes: list[Change] = record["changes"]
    if not confirm(f"{settings_file}에서 게이트 설치로 바뀐 것을 되돌립니다:\n{_summary(changes)}"):
        return Outcome(changes, applied=False)
    current = read_settings(settings_file)
    kept = revert(current, changes)
    if record.get("created_file") and not current:
        settings_file.unlink(missing_ok=True)
    else:
        _write_json(settings_file, current)
    record_path(settings_file).unlink()
    return Outcome(changes, applied=True, kept=tuple(kept))


def _ask(question: str) -> bool:
    print(question)
    try:
        return input("진행할까요? [y/N] ").strip().lower() in ("y", "yes")
    except EOFError:
        return False


def register(sub: argparse._SubParsersAction) -> None:
    setup = sub.add_parser("setup", help="게이트 설치: Claude Code 사용자 설정에 원본 보호(샌드박스 + 권한 규칙)를 합친다")
    setup.add_argument("--source", help="원본 폴더 (--undo가 아니면 필요)")
    setup.add_argument("--policy", default=os.environ.get("GATE_POLICY"), help="정책 파일 (기본: $GATE_POLICY)")
    setup.add_argument("--audit", default=os.environ.get("GATE_AUDIT"), help="감사 로그 파일 (기본: $GATE_AUDIT)")
    setup.add_argument("--settings-file", default=None,
                       help="합칠 설정 파일 (기본: ~/.claude/settings.json, CLAUDE_CONFIG_DIR가 있으면 그 폴더)")
    setup.add_argument("--yes", action="store_true", help="확인하지 않고 바로 적용한다")
    setup.add_argument("--print", action="store_true", dest="print_only",
                       help="합칠 설정 조각만 출력한다 (MDM 관리 설정으로 배포할 때). 파일은 바꾸지 않는다")
    setup.add_argument("--undo", action="store_true", help="설치 기록대로 되돌린다")


def _fragment(args: argparse.Namespace) -> dict:
    missing = [name for name, value in (("--source", args.source), ("--socket(GATE_SOCKET)", args.socket),
                                        ("--policy(GATE_POLICY)", args.policy), ("--audit(GATE_AUDIT)", args.audit))
               if not value]
    if missing:
        raise SetupError(f"설치에는 {', '.join(missing)}이 필요합니다 (막을 경로를 정확히 적어야 합니다)")
    # gate_bin은 사용자 설정에 들어가지 않는다. 경로 확인만 같이 하려고 넘긴다
    paths: GatePaths = resolve_paths(args.source, args.socket, args.policy, args.audit,
                                     workspace._default_gate_bin() or sys.argv[0])
    return protection_settings(paths)


def run(args: argparse.Namespace) -> int:
    # 링크면 실제 파일에 쓴다(교체하면 링크가 일반 파일로 바뀌므로)
    settings_file = (Path(args.settings_file).expanduser() if args.settings_file else default_settings_file()).resolve()
    confirm = (lambda _: True) if args.yes else _ask
    try:
        if args.undo:
            if not args.yes and not sys.stdin.isatty():
                raise SetupError("되돌리기 전에 확인이 필요합니다. 터미널에서 실행하거나 --yes를 붙이세요")
            outcome = undo(settings_file, confirm)
            if not outcome.applied:
                print("되돌리지 않았습니다.")
                return 1
            print(f"{settings_file}: 게이트 설치를 되돌렸습니다.")
            for line in outcome.kept:
                print(f"  {line}")
            return 0
        fragment = _fragment(args)
        if args.print_only:
            print(json.dumps(fragment, ensure_ascii=False, indent=2))
            return 0
        if not args.yes and not sys.stdin.isatty():
            raise SetupError("설정을 바꾸기 전에 확인이 필요합니다. 터미널에서 실행하거나 --yes를 붙이세요")
        outcome = install(settings_file, fragment, confirm)
    except (SetupError, WorkspaceError, OSError) as exc:
        print(f"gate: {exc}", file=sys.stderr)
        return 1
    if not outcome.changes:
        print(f"{settings_file}: 이미 적용되어 있습니다.")
        return 0
    if not outcome.applied:
        print("바꾸지 않았습니다.")
        return 1
    print(f"{settings_file}: 적용했습니다." + (f" 원래 파일: {outcome.backup}" if outcome.backup else ""))
    print("이제 Claude Code는 원본을 직접 읽지 못하고 gate로만 읽습니다. 이미 떠 있는 세션은 다시 띄우세요. "
          "되돌리기: gate setup --undo")
    return 0
