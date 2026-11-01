"""gate 명령: 게이트 데몬 실행(serve), 조회(ls, stat, cat, search), 사용자 정책(policy), 사람 승인(approve, deny),
에이전트 작업 폴더 설정(workspace init), 설치 때 Claude Code 사용자 설정에 원본 보호 합치기(setup), 감사 로그 보기(audit),
설정 화면(ui), 원본 폴더 연결 보기·추가·빼기(mount).

원본 폴더 (Phase 9, 2026-11-01 기본 폴더 없앰: plans/mount-ui-plan.md 11절)
  - serve의 `/`는 빈 읽기 전용 폴더다. 정책 파일(--policy·GATE_POLICY) 옆 mounts.yaml의 연결된 폴더만 `/<이름>/`으로
    보인다(gate.mounts.MountedStore). 목록 파일은 요청마다 바뀌었는지 보고 다시 읽는다. 폴더를 하나도 연결하지 않았거나
    정책 파일 없이 띄우면 아무 문서도 읽을 수 없다(`plant ls /`는 비어 있고 다른 경로는 ENOENT).
  - --source는 e2e·하네스·테스트용 숨은 옵션이다(도움말에 없음). 주면 그 폴더를 `/`에 보여 준다.
  - mount는 소켓을 쓰지 않고 목록 파일과 작업 폴더 설정을 직접 쓴다(gate.mount_cli → gate.mount_change).

소켓 경로는 --socket 또는 GATE_SOCKET, 정책 파일은 --policy 또는 GATE_POLICY로 지정한다.
정책 파일이 없으면 기본 정책으로 동작하고, 데몬은 요청마다 정책 파일이 바뀌었는지 보고 다시 읽는다.
policy는 소켓을 쓰지 않고 정책 파일을 직접 다룬다(gate.policy_cli). workspace init은 설정 파일만 만든다(gate.workspace).
setup은 사용자 설정 파일만 고친다(gate.claude_setup). audit은 감사 로그를 읽기만 한다(gate.audit_cli).
ui는 127.0.0.1에만 여는 로컬 웹 화면이다(gate.ui). 정책 쓰기는 gate policy와 같은 함수(gate.policy_change)로 한다.
--socket(GATE_SOCKET)을 주면 화면에 게이트 데몬이 켜져 있는지 보여 준다(연결만 해 본다).

사람 승인(Phase 5)
  - serve는 맥이면 대화상자로 묻고(--no-dialog로 끔), 승인 소켓도 연다. 승인 소켓은 --approve-socket 또는
    GATE_APPROVE_SOCKET이고, 지정하지 않으면 게이트 소켓 옆의 approve.sock이다.
  - approve(목록), approve <id> [--folder], deny <id>는 사용자 터미널에서 쓴다. 답하는 명령은 실제 터미널에서만 받는다
    (gate policy와 같다. 보안 경계는 아니고, 진짜 경계는 Phase 6 샌드박스가 승인 소켓을 열지 않는 것이다).
  - 터미널에 보이는 경로·목적은 제어 문자를 공백으로 바꾼다(파일 이름에 넣은 터미널 제어 문자로 화면을 꾸미지 못하게).

감사 로그
  - serve --audit 또는 GATE_AUDIT로 지정하면 요청마다 JSONL 한 줄을 남긴다(gate.audit). 지정하지 않으면 남기지 않는다.
    파일 해시는 같은 폴더의 audit.key로 만든 HMAC이다(없으면 만든다).
  - 기록에 실패해도 데몬은 계속 돈다. 응답은 이미 보낸 뒤라서 요청 결과에는 영향이 없고, stderr에 경고한다
    (실패가 이어지는 동안은 한 번만, 다시 기록되면 알린다).
  - gate audit(--audit 또는 GATE_AUDIT)으로 사용자 터미널에서 본다. 최근·막힌 요청, 결정·경로·시각, 같은 내용의 파일로 거른다.
"""

import argparse
import json
import os
import signal
import sys
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import TextIO

from gate import audit_cli, claude_setup, mount_cli, policy_cli, ui, workspace
from gate.approval import Approvals, DialogChannel, Pending, clean_text, kinds_text
from gate.audit import AuditLog
from gate.client import ApprovalClient, GateClient
from gate.daemon import ApprovalServer, GateServer, RequestEvent
from gate.detectors import KINDS as NUMBER_KINDS
from gate.peer import Peer
from gate.ner_client import DEFAULT_BASE_URL as DEFAULT_NER_URL
from gate.pipeline import Inspector
from gate.policy import PolicyError, PolicySource
from gate.protocol import GateError
from gate.mounts import MountedStore, MountsSource
from gate.store import EmptyStore, PlainStore, Store

ANSWER_NAMES = {"once": "이번만 허용", "session": "이 폴더 허용", "deny": "거부"}


def _err(text: str) -> None:
    print(f"plant: {text}", file=sys.stderr, flush=True)


def _log(event: RequestEvent) -> None:
    """요청 한 줄 기록. 원문과 탐지 값은 쓰지 않는다."""
    j = event.judgment
    result = event.errno or "ok"
    if j is not None:
        result = j.action + (f" ({kinds_text(j.findings)})" if j.findings else "") + (" 캐시" if j.cached else "")
        if j.approval is not None:
            result += f" [승인: {j.approval}" + (f", {j.approved_by}" if j.approved_by else "") + "]"
    exe = os.path.basename(event.peer.exe or "?")
    path = clean_text(event.path if isinstance(event.path, str) else str(event.path))
    _err(f"{event.op} {path} → {result} [pid {event.peer.pid} {exe}] {event.elapsed_ms:.1f}ms")


def _announce(pending: Pending) -> None:
    r = pending.request
    _err(f"승인 대기 [{pending.id}] {clean_text(r.path)} — {clean_text(r.reason)} ({r.timeout_seconds:g}초). "
         f"터미널에서 plant approve {pending.id} 또는 plant deny {pending.id}")


def _log_answer(peer: Peer, id: str, answer: str) -> None:
    _err(f"승인 [{id}] 터미널(pid {peer.pid})에서 {ANSWER_NAMES.get(answer, answer)}")


class _Recorder:
    """요청마다 stderr 한 줄, 그리고 감사 로그(있으면)에 한 줄."""

    def __init__(self, audit: AuditLog | None) -> None:
        self.audit = audit
        self._failing = False

    def __call__(self, event: RequestEvent) -> None:
        _log(event)
        if self.audit is None:
            return
        try:
            self.audit.record(event)
        except (OSError, ValueError) as exc:
            if not self._failing:
                _err(f"감사 로그를 쓰지 못했습니다. 기록이 빠지고 있습니다: {exc}")
            self._failing = True
        else:
            if self._failing:
                _err("감사 로그 기록이 다시 됩니다")
            self._failing = False


def approve_socket_path(gate_socket: str | None, explicit: str | None) -> str | None:
    """승인 소켓 경로. 지정하지 않으면 게이트 소켓 옆의 approve.sock."""
    if explicit:
        return explicit
    return str(Path(gate_socket).with_name("approve.sock")) if gate_socket else None


def _make_store(source: str | None, policy_path: str | None, socket_path: str | None,
                audit_path: str | None) -> tuple[Store, MountsSource | None]:
    """`/`는 빈 폴더(source를 주면 그 폴더)다. 정책 파일이 있으면 그 옆 폴더 목록(mounts.yaml)의 연결한 폴더를
    `/<이름>/`으로 함께 보여 주는 MountedStore, 없으면 `/`만(source도 없으면 아무것도 보이지 않는다)."""
    base: PlainStore | EmptyStore = PlainStore(source) if source else EmptyStore()
    if not policy_path:
        return base, None
    mounts = MountsSource(policy_path, source=base.root, socket=socket_path, audit=audit_path, warn=_err)
    return MountedStore(base, mounts), mounts


def _serve(socket_path: str, approve_socket: str, source: str | None, policy_path: str | None, dialog: bool,
           audit_path: str | None = None) -> int:
    store, mounts = _make_store(source, policy_path, socket_path, audit_path)
    base_root = getattr(store, "root", None)
    try:
        audit = AuditLog(audit_path) if audit_path else None
    except (OSError, ValueError) as exc:
        _err(f"감사 로그를 열 수 없습니다: {exc}")
        return 1
    policy_source = PolicySource(policy_path)
    channels = [DialogChannel()] if dialog and DialogChannel.available() else []
    approvals = Approvals(channels, on_pending=_announce, on_error=lambda m: _err(f"승인 창구 오류: {clean_text(m)}"))
    inspector = Inspector(store, policy_source, approvals=approvals)
    try:
        server = GateServer(socket_path, store, on_request=_Recorder(audit), inspector=inspector)
    except BaseException:
        if audit is not None:
            audit.close()
        raise
    try:
        approval_server = ApprovalServer(approve_socket, approvals, on_answer=_log_answer)
    except BaseException:
        server.server_close()
        if audit is not None:
            audit.close()
        raise
    threading.Thread(target=approval_server.serve_forever, daemon=True).start()
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    print(f"plant: {socket_path} 에서 대기 중", file=sys.stderr)
    _announce_mounts(base_root, mounts)
    print(f"plant: 사람 승인: {'맥 대화상자 + ' if channels else ''}터미널(plant approve, 승인 소켓 {approve_socket})",
          file=sys.stderr)
    audit_note = f"{audit_path} (파일 해시 키 {audit.key_path})" if audit else "남기지 않음 (--audit 또는 GATE_AUDIT로 지정)"
    print(f"plant: 감사 로그: {audit_note}", file=sys.stderr)
    try:
        policy = policy_source.current()
        where = policy_path or "지정 안 함"
        print(f"plant: 정책 {where} (버전 {policy.version}, 사용자 항목 {len(policy.entries)}개)", file=sys.stderr)
        if policy.ner_backend != "none":  # 기본은 켜짐(3e). 서버가 없으면 모든 읽기가 막히므로 시작할 때 알린다
            print(f"plant: NER 켜짐. NER 서버(NER_BASE_URL, 없으면 {DEFAULT_NER_URL})가 응답하지 않으면 내용 읽기를 "
                  "모두 차단합니다. 끄려면: plant policy set ner_backend none", file=sys.stderr)
    except PolicyError as exc:
        print(f"plant: 정책 파일 오류. 고칠 때까지 내용 읽기를 모두 차단합니다: {exc}", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        approval_server.shutdown()
        approval_server.server_close()
        server.server_close()
        if audit is not None:
            audit.close()
    return 0


HOW_TO_CONNECT = "폴더를 연결하려면 설정 화면(plant ui)이나 터미널에서 plant mount add <폴더>를 실행하세요"


def _announce_mounts(base_root: Path | None, mounts: MountsSource | None) -> None:
    """시작할 때 연결한 폴더를 알린다. 목록 파일 오류·뺀 항목 경고는 MountsSource가 먼저 stderr에 쓴다.
    base_root는 숨은 개발용 --source(보통 None)."""
    if base_root is not None:
        _err(f"개발용 원본 {base_root}를 / 에 보여 줍니다 (숨은 옵션 --source)")
    nothing = "" if base_root is not None else " 지금은 에이전트가 아무 문서도 읽을 수 없습니다."
    if mounts is None:
        _err("정책 파일(--policy 또는 GATE_POLICY)을 지정하지 않아 폴더 목록을 읽지 않습니다. "
             f"연결한 폴더가 없습니다.{nothing}")
        if base_root is None:
            _err(f"{HOW_TO_CONNECT}. 정책 파일을 지정해 다시 띄워야 연결한 폴더가 보입니다.")
        return
    state = mounts.current()
    if state.error is not None:
        _err(f"폴더 목록 {mounts.mounts_file}이 잘못되어 연결한 폴더를 보여 주지 않습니다")
        return
    if not state.mounts:
        _err(f"아직 연결한 폴더가 없습니다.{nothing}")
        _err(f"{HOW_TO_CONNECT}. 연결하면 다시 띄우지 않아도 다음 요청부터 보입니다 (폴더 목록 {mounts.mounts_file})")
        return
    names = ", ".join(f"/{m.name}" for m in state.mounts)
    _err(f"연결한 폴더 {len(state.mounts)}개: {names} · 폴더 목록 {mounts.mounts_file} "
         "(바뀌면 다시 띄우지 않아도 다음 요청부터 씁니다)")


def build_parser() -> argparse.ArgumentParser:
    # 실행 이름은 plant로 고정한다. gate(예전 이름)는 pyproject의 별칭일 뿐이라 그 이름으로 실행해도 도움말·오류에는 plant가 보인다
    parser = argparse.ArgumentParser(prog="plant", description="PLANT — AI 에이전트의 파일 읽기를 지키는 도구")
    parser.add_argument("--socket", default=os.environ.get("GATE_SOCKET"), help="PLANT 소켓 경로 (기본: $GATE_SOCKET)")
    parser.add_argument("--approve-socket", default=os.environ.get("GATE_APPROVE_SOCKET"),
                        help="승인 소켓 경로 (기본: $GATE_APPROVE_SOCKET, 없으면 PLANT 소켓 옆의 approve.sock)")
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="PLANT 데몬을 실행한다 (연결한 폴더만 보여 준다. 연결은 plant mount·plant ui)")
    # 개발·시험용 숨은 옵션(e2e·하네스·테스트): 이 폴더를 / 에 보여 준다. 도움말·README·화면에는 나오지 않는다
    serve.add_argument("--source", default=None, help=argparse.SUPPRESS)
    serve.add_argument("--policy", default=os.environ.get("GATE_POLICY"),
                       help="정책 파일 (기본: $GATE_POLICY, 없으면 기본 정책)")
    serve.add_argument("--no-dialog", action="store_true", help="맥 대화상자를 띄우지 않는다 (터미널 승인만)")
    serve.add_argument("--audit", default=os.environ.get("GATE_AUDIT"),
                       help="감사 로그 파일 (JSONL, 기본: $GATE_AUDIT, 없으면 남기지 않음)")

    ls = sub.add_parser("ls", help="폴더 목록")
    ls.add_argument("path", nargs="?", default="/")

    st = sub.add_parser("stat", help="파일 정보 (JSON)")
    st.add_argument("path")

    cat = sub.add_parser("cat", help="파일 내용")
    cat.add_argument("path")
    cat.add_argument("--purpose", help="읽는 목적 (판단에 쓰임)")

    se = sub.add_parser("search", help="검사를 통과한 내용에서 글자 그대로 찾기 (grep -rnF와 비슷)")
    se.add_argument("pattern")
    se.add_argument("path", nargs="?", default="/")
    se.add_argument("-i", "--ignore-case", action="store_true")
    se.add_argument("--purpose", help="읽는 목적 (판단에 쓰임)")
    se.add_argument("--max", type=int, default=100, dest="max_results", help="최대 결과 수 (기본 100)")

    ap = sub.add_parser("approve", help="사람 승인: id 없이 쓰면 기다리는 요청 목록, id를 주면 허용 (사용자 터미널 전용)")
    ap.add_argument("id", nargs="?")
    ap.add_argument("--folder", action="store_true", help="이 폴더를 정해진 시간 동안 다시 묻지 않는다")
    de = sub.add_parser("deny", help="사람 승인: 거부 (사용자 터미널 전용)")
    de.add_argument("id")

    policy_cli.register(sub)
    workspace.register(sub)
    claude_setup.register(sub)
    audit_cli.register(sub)
    ui.register(sub)
    mount_cli.register(sub)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "policy":
        return policy_cli.run(args)
    if args.command == "mount":
        return mount_cli.run(args)
    if args.command == "workspace":
        return workspace.run(args)
    if args.command == "setup":
        return claude_setup.run(args)
    if args.command == "audit":
        return audit_cli.run(args)
    if args.command == "ui":
        return ui.run(args)
    if args.command in ("approve", "deny"):
        approve_socket = approve_socket_path(args.socket, args.approve_socket)
        if not approve_socket:
            parser.error("--socket(GATE_SOCKET) 또는 --approve-socket(GATE_APPROVE_SOCKET)을 지정하세요")
        return _approval(ApprovalClient(approve_socket), args)
    if not args.socket:
        parser.error("--socket 또는 GATE_SOCKET 환경변수로 PLANT 소켓 경로를 지정하세요")

    if args.command == "serve":
        return _serve(args.socket, approve_socket_path(args.socket, args.approve_socket), args.source, args.policy,
                      dialog=not args.no_dialog, audit_path=args.audit)

    client = GateClient(args.socket)
    try:
        if args.command == "ls":
            for entry in client.ls(args.path):
                print(entry["name"] + ("/" if entry["type"] == "dir" else ""))
        elif args.command == "stat":
            print(json.dumps(client.stat(args.path), ensure_ascii=False))
        elif args.command == "search":
            return _search(client, args)
        else:
            result = client.read(args.path, purpose=args.purpose)
            sys.stdout.buffer.write(result.data)
            sys.stdout.buffer.flush()
            if result.masked:
                print(mask_notice(result.masked, result.labeled), file=sys.stderr)
    except GateError as exc:
        print(f"plant: {getattr(args, 'path', '')}: {exc.message} ({exc.name})", file=sys.stderr)
        return 1
    return 0


def mask_notice(masked: Mapping[str, int], labeled: Mapping[str, int] | None) -> str:
    """gate cat이 가린 내용을 돌려줄 때 stderr에 쓰는 안내. 실제로 가린 방식만 말한다.

    정규식은 같은 길이 `*`로, 규칙(IP·내부 주소)과 NER은 `[이름]`처럼 종류 이름으로 가린다(gate.pipeline).
    labeled는 masked 중 종류 이름으로 가린 것이다. None이면 그 정보를 주지 않는 예전 데몬이라 번호 종류(정규식 종류)는
    `*`, 그 밖은 종류 이름으로 가렸다고 본다(이때는 NER이 찾은 전화번호·이메일도 `*`로 안내된다).
    """
    if labeled is None:
        labeled = {kind: n for kind, n in masked.items() if kind not in NUMBER_KINDS}
    stars = {kind: n - labeled.get(kind, 0) for kind, n in masked.items() if n > labeled.get(kind, 0)}
    labels = {kind: n for kind, n in labeled.items() if n > 0}
    if stars and labels:
        return (f"plant: 개인정보 일부를 같은 길이의 *로 ({kinds_text(stars)}), "
                f"[이름]처럼 종류 이름으로 ({kinds_text(labels)}) 가렸습니다")
    if labels:
        return f"plant: 개인정보 일부를 [이름]처럼 종류 이름으로 가렸습니다 ({kinds_text(labels)})"
    return f"plant: 개인정보 일부를 같은 길이의 *로 가렸습니다 ({kinds_text(stars)})"


def _search(client: GateClient, args: argparse.Namespace) -> int:
    """grep처럼 '경로:줄:내용'을 출력한다. 찾으면 0, 못 찾으면 1. 차단된 파일은 stderr에 알린다."""
    result = client.search(args.path, args.pattern, ignore_case=args.ignore_case, purpose=args.purpose,
                           max_results=args.max_results)
    for m in result["matches"]:
        print(f"{m['path']}:{m['line']}:{m['text']}")
    for s in result["skipped"]:
        print(f"plant: 건너뜀 {s['path']}: {s['message']} ({s['errno']})", file=sys.stderr)
    if result["truncated"]:
        print("plant: 결과가 많아 일부만 보여 줍니다 (--max로 늘릴 수 있음)", file=sys.stderr)
    return 0 if result["matches"] else 1


def _isatty(stream: TextIO) -> bool:
    try:
        return bool(stream.isatty())
    except (AttributeError, ValueError):
        return False


def _show_pending(pending: list[dict], out: TextIO) -> None:
    if not pending:
        print("기다리는 승인 요청이 없습니다.", file=out)
        return
    lines = [f"기다리는 승인 요청 {len(pending)}건"]
    for p in pending:
        exe = os.path.basename(p["exe"]) if p.get("exe") else "알 수 없음"
        lines += [
            f"  [{p['id']}] {clean_text(p['path'])}  (남은 시간 {p['remaining_seconds']:.0f}초)",
            f"     이유: {clean_text(p['reason'])}",
            f"     요청 프로그램: {clean_text(exe)} (pid {p.get('pid')})"
            f" · 에이전트가 밝힌 목적(확인되지 않음): {clean_text(p.get('purpose')) or '없음'}",
        ]
        if p.get("findings"):
            lines.append(f"     가린 것: {kinds_text(p['findings'])} (허용해도 가린 채로 보여 줍니다)")
    folder = pending[0]["session_minutes"]
    lines.append("  답하기: plant approve <id> (이번만)"
                 + (f" · plant approve <id> --folder (그 폴더 {folder}분 동안)" if folder > 0 else "")
                 + " · plant deny <id>")
    print("\n".join(lines), file=out)


def _approval(client: ApprovalClient, args: argparse.Namespace, stdin: TextIO | None = None,
              out: TextIO | None = None) -> int:
    stdin = sys.stdin if stdin is None else stdin
    out = sys.stdout if out is None else out
    try:
        if args.command == "approve" and args.id is None:
            _show_pending(client.pending(), out)
            return 0
        if not _isatty(stdin):
            print("plant: 승인과 거부는 사용자 터미널에서만 받습니다", file=sys.stderr)
            return 1
        answer = "deny" if args.command == "deny" else ("session" if args.folder else "once")
        client.answer(args.id, answer)
        print(f"{args.id}: {ANSWER_NAMES[answer]}", file=out)
    except GateError as exc:
        print(f"plant: {exc.message} ({exc.name})", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
