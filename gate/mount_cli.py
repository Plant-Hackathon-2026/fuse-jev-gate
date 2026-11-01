"""`gate mount` 명령: 게이트에 연결한 원본 폴더 보기·추가·빼기 (Phase 9, plans/mount-ui-plan.md 4.4).

설정 화면(gate ui)과 같은 함수(gate.mount_change)를 쓴다. 이 모듈은 터미널에서 묻고 결과를 글로 보여 주기만 한다.
데몬 소켓을 거치지 않고 정책 파일 옆 mounts.yaml·workspaces.yaml과 등록된 작업 폴더 설정을 직접 쓴다. 실행 중인 데몬은
다음 요청에서 목록 파일이 바뀐 것을 보고 다시 읽는다(다시 띄우지 않아도 된다).

명령
  list               이름, 게이트 안 경로, 실제 경로, 상태(연결됨·보호만 남음·찾을 수 없음), 작업 폴더 반영 여부.
                     연결한 폴더만 보인다(2026-11-01 기본 폴더 없앰. 연결하지 않으면 에이전트는 아무 문서도 읽지 못한다)
  add <폴더>          확인 뒤 연결. 에이전트에게 더 보여 주는 바꿈이라 한 번 더 묻는다
  remove <이름>       확인 뒤 게이트에서 빼기. 작업 폴더 설정의 직접 읽기 막기는 남는다("보호만 남음")
  unprotect <이름>    보호도 풀기(목록에서 지우고 작업 폴더 설정을 다시 씀). 느슨해지는 바꿈이라 한 번 더 묻는다

경로
  --policy(기본 $GATE_POLICY)가 필요하다. 목록·등록 파일은 그 옆에 있다. --socket은 gate 전역 옵션(GATE_SOCKET),
  --audit은 GATE_AUDIT를 쓴다. 둘은 추가할 수 없는 폴더(소켓 폴더·감사 로그 폴더)를 가려내는 데만 쓴다.

require_tty
  True이면 바꾸는 명령(add, remove, unprotect)은 stdin이 터미널일 때만 받는다(`gate policy`와 같다).
  에이전트의 Bash 실행처럼 터미널이 없는 곳에서 `y`를 흘려 넣는 것을 한 겹 더 막을 뿐 보안 경계는 아니다.
  진짜 경계는 목록·등록 파일 쓰기를 샌드박스로 막는 것이다(작업 폴더 설정의 Edit deny + denyWrite).
  기본 답은 N이고 입력이 끝나면(EOF) N으로 본다. 바꾸지 않으면 exit 1.

출력은 모두 stdout으로 낸다.
"""

import argparse
import os
import sys
from typing import TextIO

from gate.approval import clean_text
from gate.mount_change import (
    MAX_COUNT_FILES,
    FolderRow,
    MountChange,
    MountContext,
    MountError,
    MountResult,
    MountsView,
    Note,
    apply,
    list_mounts,
    plan_add,
    plan_remove,
    plan_unprotect,
)
from gate.policy_change import POLICY_ENV

CHANGE_COMMANDS = ("add", "remove", "unprotect")
AUDIT_ENV = "GATE_AUDIT"
_YES = frozenset({"y", "yes", "예", "네", "ㅛ"})  # ㅛ: 한글 자판 상태에서 y를 누른 경우

_STATE_TEXT = {
    "ok": "지금 목록대로 막고 있어요",
    "stale": "예전 목록대로예요(다음에 폴더를 연결하거나 보호를 풀 때 자동으로 다시 써요)",
    "edited": "손으로 바뀌었어요(자동으로 덮지 않아요. `plant workspace init --force`로 다시 만드세요)",
    "missing": "작업 폴더나 설정 파일이 없어요(건너뛰어요)",
}


class _Stop(Exception):
    """명령을 멈추고 exit 1. 메시지는 사용자에게 보여 준다."""


def register(subparsers) -> argparse.ArgumentParser:
    """gate 명령의 subparsers에 mount 하위 명령을 붙인다. --policy·--audit은 mount 바로 뒤나 하위 명령 뒤 어디에 써도 된다."""
    policy_help = f"정책 파일 (기본: ${POLICY_ENV}). 폴더 목록·작업 폴더 등록 파일은 그 옆에 있다"
    audit_help = f"감사 로그 파일 (기본: ${AUDIT_ENV}). 그 폴더는 연결할 수 없다"
    parser = subparsers.add_parser("mount", help="원본 폴더 연결 보기·추가·빼기 (바꾸기는 사람만 터미널에서)")
    parser.add_argument("--policy", dest="policy_path", default=None, help=policy_help)
    parser.add_argument("--audit", dest="audit_path", default=None, help=audit_help)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--policy", dest="policy_path", default=argparse.SUPPRESS, help=policy_help)
    common.add_argument("--audit", dest="audit_path", default=argparse.SUPPRESS, help=audit_help)
    sub = parser.add_subparsers(dest="mount_command", required=True)
    sub.add_parser("list", parents=[common], help="연결한 폴더와 작업 폴더 설정 상태")
    add = sub.add_parser("add", parents=[common], help="폴더 연결 (확인 뒤)")
    add.add_argument("folder")
    remove = sub.add_parser("remove", parents=[common], help="PLANT에서 빼기 (직접 읽기 막기는 남음)")
    remove.add_argument("name")
    unprotect = sub.add_parser("unprotect", parents=[common], help="보호도 풀기 (PLANT에서 뺀 폴더만)")
    unprotect.add_argument("name")
    return parser


def run(args: argparse.Namespace, *, stdin: TextIO | None = None, stdout: TextIO | None = None,
        require_tty: bool = True) -> int:
    """mount 하위 명령을 실행한다. 성공 0, 실패·취소 1."""
    stdin = sys.stdin if stdin is None else stdin
    out = sys.stdout if stdout is None else stdout
    command = args.mount_command
    try:
        ctx = _context(args)
        if command in CHANGE_COMMANDS and require_tty and not _isatty(stdin):
            raise _Stop("폴더 연결을 바꾸는 명령은 사람이 터미널에서 직접 실행해야 해요(입력이 터미널이 아님). "
                        "보기는 `plant mount list`로 할 수 있어요.")
        if command == "list":
            _show(ctx, list_mounts(ctx), out)
            return 0
        io = _Prompt(stdin, out)
        if command == "add":
            return _add(ctx, args.folder, io)
        if command == "remove":
            return _remove(ctx, args.name, io)
        return _unprotect(ctx, args.name, io)
    except (_Stop, MountError) as exc:
        print(f"plant mount: {exc}", file=out)
        return 1


# --- 공통 ---

def _isatty(stream: TextIO) -> bool:
    try:
        return bool(stream.isatty())
    except (AttributeError, ValueError, OSError):
        return False


def _context(args: argparse.Namespace) -> MountContext:
    policy = getattr(args, "policy_path", None) or os.environ.get(POLICY_ENV)
    if not policy:
        raise _Stop(f"정책 파일 경로를 --policy 또는 {POLICY_ENV} 환경변수로 지정하세요 "
                    "(예: --policy <hackathon 폴더>/gate-data/policy.yaml). 폴더 목록은 그 옆에 있어요.")
    audit = getattr(args, "audit_path", None) or os.environ.get(AUDIT_ENV)
    return MountContext.from_policy(policy, getattr(args, "socket", None) or None, audit or None)


class _Prompt:
    def __init__(self, stdin: TextIO, out: TextIO) -> None:
        self.stdin = stdin
        self.out = out

    def say(self, text: str = "") -> None:
        print(text, file=self.out)

    def confirm(self, question: str) -> bool:
        """기본 N. 입력이 끝나면(EOF) N."""
        self.out.write(f"{question} [y/N] ")
        self.out.flush()
        line = self.stdin.readline()
        if not line:
            self.out.write("\n")
            return False
        return line.strip().lower() in _YES


def _count_text(count: int | None, capped: bool) -> str:
    if capped:
        return "파일 1만 개 넘음" if MAX_COUNT_FILES == 10_000 else f"파일 {MAX_COUNT_FILES}개 넘음"
    return "파일 수 모름" if count is None else f"파일 {count}개"


def _p(path: str) -> str:
    return clean_text(path, limit=4096)


# --- list ---

def _row_state(row: FolderRow) -> str:
    state = "연결됨" if row.connected else "보호만 남음(PLANT에서 뺌, 직접 읽기 막기는 남음)"
    return state + ("" if row.exists else " · 찾을 수 없음")


def _show(ctx: MountContext, view: MountsView, out: TextIO) -> None:
    mounts = [r for r in view.folders if r.kind == "mount"]
    lines = [f"폴더 목록: {_p(str(ctx.mounts_file))}"
             + ("" if ctx.mounts_file.exists() else " (아직 없음)") + f" · 폴더 {len(mounts)}/{view.limit}개"]
    if view.error:
        lines += [f"  문제: {view.error}"]
    lines.append("")
    if not any(r.connected for r in mounts):
        lines.append("  아직 연결한 폴더가 없어요. 연결하지 않으면 에이전트는 아무 문서도 읽지 못해요. "
                     "`plant mount add <폴더>`나 설정 화면(`plant ui`)에서 연결하세요.")
    for row in mounts:
        lines.append(f"  {row.vpath}  {row.name}  —  {_row_state(row)}")
        detail = f"     실제 경로: {_p(row.path)}"
        if row.exists:
            detail += f" · {_count_text(row.file_count, row.file_count_capped)}"
        if row.added_at:
            how = {"ui": "설정 화면", "terminal": "터미널"}.get(row.added_by or "", "")
            detail += f" · {row.added_at[:16].replace('T', ' ')} {how}에서 연결"
        lines.append(detail)
        if row.unprotected_workspaces:
            lines.append("     작업 폴더 설정에 반영 안 됨(이 폴더를 직접 읽을 수 있음): "
                         + ", ".join(_p(p) for p in row.unprotected_workspaces))
    lines += ["", f"작업 폴더 (등록 {len(view.workspaces)}개)"]
    if not view.workspaces:
        lines.append("  없음. 폴더를 연결해도 어느 작업 폴더에서도 직접 읽기가 막히지 않아요.")
    for ws in view.workspaces:
        lines.append(f"  {_p(ws.path)}: {_STATE_TEXT[ws.state]}")
    if view.global_setup:
        lines += ["", "전역 설치(plant setup) 기록이 있어요. 새로 연결한 폴더는 `plant setup`을 다시 실행해야 전역 설정에서도 막혀요."]
    print("\n".join(lines), file=out)


# --- 바꾸기 ---

def _describe(change: MountChange, io: _Prompt) -> None:
    io.say(f"  PLANT 안 경로: /{change.name}")
    io.say(f"  실제 경로: {_p(change.path)} · {_count_text(change.file_count, change.file_count_capped)}")
    if change.workspaces:
        io.say("  다시 쓸 작업 폴더 설정: " + ", ".join(_p(p) for p in change.workspaces))


def _commit(ctx: MountContext, change: MountChange, io: _Prompt) -> MountResult:
    try:
        return apply(ctx, change)
    except MountError as exc:
        raise _Stop(str(exc)) from exc


def _notes(result: MountResult, io: _Prompt) -> None:
    for path in result.rewritten:
        io.say(f"  작업 폴더 설정을 다시 썼어요: {_p(path)}")
    text: dict[Note, str] = {
        "restart_session": "  이미 켜진 에이전트 세션은 다시 시작하세요. 그래야 바뀐 작업 폴더 설정이 확실히 적용돼요.",
        "rerun_setup": "  전역 설정에는 아직 반영되지 않았어요. 터미널에서 `plant setup`을 다시 실행하세요.",
        "no_workspace": ("  등록된 작업 폴더가 없어 어느 작업 폴더에서도 이 폴더 직접 읽기가 막히지 않았어요. "
                         "`plant workspace init`으로 작업 폴더를 만드세요." if result.change.op == "add" else
                         "  등록된 작업 폴더가 없어 바꾼 작업 폴더 설정은 없어요."),
    }
    for note in result.notes:
        io.say(text[note])


def _add(ctx: MountContext, folder: str, io: _Prompt) -> int:
    change = plan_add(ctx, folder, added_by="terminal")
    if change.reconnect:
        io.say("보호만 남아 있던 폴더를 PLANT에 다시 연결해요:")
    else:
        io.say("이 폴더를 PLANT에 연결해요:")
    _describe(change, io)
    if not change.workspaces:
        view = list_mounts(ctx, count_files=False)
        if not any(ws.state in ("ok", "stale") for ws in view.workspaces):
            io.say("  주의: 등록된 작업 폴더가 없어요. 어느 작업 폴더에서도 이 폴더 직접 읽기가 막히지 않아요.")
    io.say("연결하면 에이전트가 PLANT를 거쳐 이 폴더 문서를 읽을 수 있어요(정책대로 가리거나 물어봐요). "
           "동시에 작업 폴더 설정에서 이 폴더를 직접 열지 못하게 막아요.")
    if not io.confirm("연결할까요?"):
        io.say("연결하지 않았어요.")
        return 1
    if change.loosening and not io.confirm("에이전트에게 보이는 문서가 늘어나요. 정말 연결할까요?"):
        io.say("취소했어요. 연결하지 않았어요.")
        return 1
    result = _commit(ctx, change, io)
    io.say(f"연결했어요: /{change.name}. 실행 중인 PLANT는 다음 요청부터 이 폴더를 보여 줘요(다시 띄우지 않아도 돼요).")
    _notes(result, io)
    return 0


def _remove(ctx: MountContext, name: str, io: _Prompt) -> int:
    change = plan_remove(ctx, name)
    io.say("이 폴더를 PLANT에서 빼요:")
    _describe(change, io)
    io.say("에이전트는 이 폴더 문서를 더는 PLANT로 읽지 못해요. 작업 폴더 설정의 직접 읽기 막기는 그대로 남아요"
           f"(풀려면 `plant mount unprotect {change.name}`).")
    if not io.confirm("뺄까요?"):
        io.say("빼지 않았어요.")
        return 1
    result = _commit(ctx, change, io)
    io.say(f"뺐어요: /{change.name}. 지금은 보호만 남아 있어요.")
    _notes(result, io)
    return 0


def _unprotect(ctx: MountContext, name: str, io: _Prompt) -> int:
    change = plan_unprotect(ctx, name)
    io.say("이 폴더의 보호를 풀어요(목록에서 지우고 작업 폴더 설정의 직접 읽기 막기를 빼요):")
    _describe(change, io)
    if not io.confirm("보호를 풀까요?"):
        io.say("보호를 풀지 않았어요.")
        return 1
    if change.loosening and not io.confirm("보호를 풀면 에이전트가 이 폴더를 직접 읽을 수 있게 돼요. 정말 풀까요?"):
        io.say("취소했어요. 보호는 그대로예요.")
        return 1
    result = _commit(ctx, change, io)
    io.say(f"보호를 풀었어요: {_p(change.path)}")
    _notes(result, io)
    return 0
