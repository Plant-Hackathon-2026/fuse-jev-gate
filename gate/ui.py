"""`gate ui`: 설정 화면. 이 컴퓨터의 브라우저에서 정책을 보고 바꾸고, 감사 기록을 본다 (Phase 8).

사람이 자기 터미널에서 실행한다. 표준 라이브러리 http.server만 쓴다. 자료와 바꾸기는 gate.ui_api, 정책 쓰기는
gate.policy_change(`gate policy`와 같은 함수)다. 게이트 데몬·소켓과는 별개 프로세스이고, 사용자 권한으로 정책 파일을 쓴다.
에이전트용 게이트 소켓에는 정책을 바꾸는 요청이 없다(그대로).

보안 (plans/ui-plan.md 5절)
  - 127.0.0.1(IP 리터럴)에만 바인드한다. 포트는 기본 0(빈 포트를 고름), --port로 정한다.
  - 여는 주소: 실행할 때마다 secrets로 만든 **한 번 쓰는 열쇠(launch)**가 든 `http://127.0.0.1:<포트>/?launch=…`.
    터미널에 찍고 브라우저로 연다. 파일에 남기지 않는다. 열쇠는 처음 한 번만 통하고, 그때 그 창의 **세션 토큰**을 새로 만든다.
    브라우저 방문 기록(디스크의 파일)에 주소가 남아도, 그 열쇠는 이미 썼으므로 다시 쓸 수 없다.
  - 세션 토큰은 첫 페이지 안에만 실린다. 화면 스크립트는 이를 메모리에만 두고 API에는 X-Gate-Token 헤더로,
    정적 파일(CSS·JS·글꼴·로고)에는 경로 `/s/<토큰>/…`로 보낸다. 쿠키·sessionStorage·localStorage에는 두지 않는다
    (브라우저는 이것들을 디스크에 남기고, 샌드박스 안의 에이전트도 그 파일은 읽을 수 있다). 그래서 새로 고치면 다시 열어야 한다.
    이 터미널에서 Enter를 누르면 새 열쇠로 다시 연다.
  - 모든 요청: Host 헤더가 정확히 `127.0.0.1:<포트>`여야 한다(DNS rebinding 방지). 첫 페이지는 열쇠, 나머지는 세션 토큰이 맞아야 한다.
  - 바꾸는 요청은 POST만 받고, Origin이 정확히 `http://127.0.0.1:<포트>`여야 한다(CSRF 방지). 본문은 JSON(64KB 이하)만 받는다.
    읽기 API에 POST를 보내거나 바꾸는 API에 GET을 보내면 405다.
  - 모든 응답: CSP(default-src 'none', 필요한 것만 'self'), Referrer-Policy: no-referrer, Cache-Control: no-store,
    X-Frame-Options: DENY, X-Content-Type-Options: nosniff. 화면은 외부로 나가는 요청이 없다(글꼴·로고도 이 서버가 준다).
  - 요청 줄(경로·열쇠)은 로그에 남기지 않는다. 거부한 요청만 이유와 함께 stderr에 한 줄 남긴다.
  - 세션 토큰이 없으면 정책·감사 기록 API를 쓸 수 없다. 에이전트가 샌드박스 안에서 127.0.0.1 TCP로 이 포트에 닿더라도
    토큰이 없으면 막힌다(sandbox/bypass.py W16·W17로 확인한다).

API (gate.ui_api)
  GET  /api/state, /api/status, /api/audit?period=today|7d|all&category=all|allow|mask|ask|deny&q=…
  POST /api/interpret {sentence} · /api/save {ticket, confirm?} · /api/set {key, value, confirm?}
       /api/remove {number, fingerprint, confirm?} · /api/undo {confirm?} · /api/ner {enable, confirm?}
  실패는 {"ok": false, "error": 짧은 이름, "message": 사용자에게 보여 줄 말}.
"""

import argparse
import hmac
import json
import os
import secrets
import signal
import sys
import threading
import webbrowser
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from typing import TextIO
from urllib.parse import parse_qs, urlsplit

from gate.policy_change import POLICY_ENV
from gate.ui_api import AUDIT_LIMIT, UiApp, UiConfig, UiError

# 제품 이름은 여기 한 곳에만 둔다(머리글·창 제목·터미널 안내). 사용자 결정(2026-11-01): "게이트 · PLANT".
PRODUCT_NAME = "게이트"
PRODUCT_TAG = "PLANT"

HOST = "127.0.0.1"
DEFAULT_PORT = 0
TOKEN_HEADER = "X-Gate-Token"
MAX_BODY_BYTES = 64 * 1024
MAX_SESSIONS = 8
REQUEST_TIMEOUT_SECONDS = 15

CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; font-src 'self'; "
       "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
SECURITY_HEADERS = (
    ("Content-Security-Policy", CSP),
    ("Referrer-Policy", "no-referrer"),
    ("Cache-Control", "no-store"),
    ("X-Content-Type-Options", "nosniff"),
    ("X-Frame-Options", "DENY"),
    ("Cross-Origin-Opener-Policy", "same-origin"),
    ("Cross-Origin-Resource-Policy", "same-origin"),
    ("Permissions-Policy", "camera=(), microphone=(), geolocation=()"),
)

# 정적 파일: 이 목록에 있는 것만 준다(경로를 파일 시스템으로 풀지 않는다).
FONTS = ("outfit-latin-400-normal", "outfit-latin-500-normal", "outfit-latin-600-normal", "outfit-latin-700-normal",
         "jetbrains-mono-latin-400-normal", "jetbrains-mono-latin-500-normal")
STATIC_TYPES: dict[str, str] = {
    "app.css": "text/css; charset=utf-8",
    "app.js": "text/javascript; charset=utf-8",
    "plant-mark.png": "image/png",
    **{f"fonts/{name}.woff2": "font/woff2" for name in FONTS},
}
GET_API = frozenset({"state", "status", "audit"})
POST_API = frozenset({"interpret", "save", "set", "remove", "undo", "ner"})

_DENIED_PAGE = """<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title></head>
<body><main><h1>이 주소로는 열 수 없어요</h1>
<p>설정 화면 주소는 한 번만 열 수 있어요. 창을 새로 고쳤거나 다른 창에서 열었다면,
<code>gate ui</code>를 실행한 터미널에서 Enter를 누르면 새 주소로 다시 열어요.</p>
<p>터미널을 닫았다면 <code>gate ui</code>를 다시 실행해 주세요.</p></main></body></html>
"""


def static_root():
    return resources.files("gate").joinpath("ui_static")


def load_static() -> dict[str, bytes]:
    root = static_root()
    files = {name: root.joinpath(name).read_bytes() for name in STATIC_TYPES}
    files["index.html"] = root.joinpath("index.html").read_bytes()
    return files


class UiServer(ThreadingHTTPServer):
    """127.0.0.1에만 여는 설정 화면 서버. 열쇠(launch)·세션 토큰을 들고 있다."""

    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, app: UiApp, port: int = DEFAULT_PORT, *, log: TextIO | None = None) -> None:
        super().__init__((HOST, port), _Handler)
        self.app = app
        self.port = self.server_address[1]
        self.host_header = f"{HOST}:{self.port}"
        self.origin = f"http://{HOST}:{self.port}"
        self.files = load_static()
        self.log = log if log is not None else sys.stderr
        self._lock = threading.Lock()
        self._launch: str | None = None
        self._sessions: list[str] = []

    def new_launch_url(self) -> str:
        """새 열쇠를 만들어 여는 주소를 돌려준다. 전에 만든 열쇠(아직 안 썼어도)는 더 통하지 않는다."""
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._launch = token
        return f"{self.origin}/?launch={token}"

    def use_launch(self, candidate: str) -> str | None:
        """열쇠가 맞으면 지우고 새 세션 토큰을 돌려준다. 틀리면 None."""
        with self._lock:
            if self._launch is None or not hmac.compare_digest(self._launch, candidate):
                return None
            self._launch = None
            session = secrets.token_urlsafe(32)
            self._sessions.append(session)
            del self._sessions[:-MAX_SESSIONS]
            return session

    def has_session(self, candidate: str) -> bool:
        with self._lock:
            sessions = list(self._sessions)
        return any(hmac.compare_digest(s, candidate) for s in sessions)

    def note(self, text: str) -> None:
        print(f"gate ui: {text}", file=self.log, flush=True)


class _Reject(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


class _Handler(BaseHTTPRequestHandler):
    server: UiServer
    server_version = "gate-ui"
    sys_version = ""
    timeout = REQUEST_TIMEOUT_SECONDS

    def log_message(self, format: str, *args) -> None:  # 요청 줄(경로·열쇠)은 남기지 않는다
        pass

    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_POST(self) -> None:
        self._dispatch("POST")

    def do_HEAD(self) -> None:
        self._dispatch("HEAD")

    def do_PUT(self) -> None:
        self._dispatch("PUT")

    def do_DELETE(self) -> None:
        self._dispatch("DELETE")

    def do_PATCH(self) -> None:
        self._dispatch("PATCH")

    def do_OPTIONS(self) -> None:
        self._dispatch("OPTIONS")

    # --- 흐름 ---

    def _dispatch(self, method: str) -> None:
        try:
            self._check_host()
            path = urlsplit(self.path).path
            if path.startswith("/api/"):
                self._api(method, path[len("/api/"):])
            elif method != "GET":
                raise _Reject(405, "method-not-allowed", "이 주소는 GET만 받아요.")
            elif path == "/":
                self._launch_page()
            elif path.startswith("/s/"):
                self._static(path)
            else:
                raise _Reject(404, "not-found", "없는 주소예요.")
        except _Reject as exc:
            if exc.status in (401, 403, 405):
                self.server.note(f"요청을 거부했습니다 ({exc.code}, {method})")
            self._send_json(exc.status, {"ok": False, "error": exc.code, "message": exc.message})
        except UiError as exc:
            self._send_json(exc.status, {"ok": False, "error": exc.code, "message": exc.message})
        except Exception as exc:  # 화면 서버가 죽지 않게. 자세한 내용은 터미널에만
            self.server.note(f"처리 중 오류: {type(exc).__name__}: {exc}")
            self._send_json(500, {"ok": False, "error": "internal", "message": "화면 서버에 오류가 났어요. 터미널을 확인해 주세요."})

    def _check_host(self) -> None:
        hosts = self.headers.get_all("Host") or []
        if len(hosts) != 1 or hosts[0] != self.server.host_header:
            raise _Reject(403, "bad-host", f"이 화면은 {self.server.host_header} 주소로만 열려요.")

    def _check_token(self) -> None:
        tokens = self.headers.get_all(TOKEN_HEADER) or []
        if len(tokens) != 1 or not self.server.has_session(tokens[0]):
            raise _Reject(403, "bad-token", "화면 토큰이 없거나 맞지 않아요. 터미널에서 gate ui로 연 창에서만 쓸 수 있어요.")

    def _check_origin(self) -> None:
        origins = self.headers.get_all("Origin") or []
        if len(origins) != 1 or origins[0] != self.server.origin:
            raise _Reject(403, "bad-origin", "이 화면에서 보낸 요청이 아니에요.")
        site = self.headers.get("Sec-Fetch-Site")
        if site is not None and site != "same-origin":
            raise _Reject(403, "bad-origin", "이 화면에서 보낸 요청이 아니에요.")

    def _launch_page(self) -> None:
        values = parse_qs(urlsplit(self.path).query).get("launch", [])
        session = self.server.use_launch(values[0]) if len(values) == 1 else None
        if session is None:
            body = _DENIED_PAGE.format(title=escape(f"{PRODUCT_NAME} · {PRODUCT_TAG}")).encode("utf-8")
            self.server.note("요청을 거부했습니다 (bad-launch, GET)")
            self._send(403, body, "text/html; charset=utf-8")
            return
        page = self.server.files["index.html"].decode("utf-8")
        page = (page.replace("__SESSION__", session)
                .replace("__PRODUCT_NAME__", escape(PRODUCT_NAME))
                .replace("__PRODUCT_TAG__", escape(PRODUCT_TAG)))
        self._send(200, page.encode("utf-8"), "text/html; charset=utf-8")

    def _static(self, path: str) -> None:
        _, _, rest = path.partition("/s/")
        session, _, name = rest.partition("/")
        if not self.server.has_session(session):
            raise _Reject(403, "bad-token", "화면 토큰이 없거나 맞지 않아요.")
        if name not in STATIC_TYPES:
            raise _Reject(404, "not-found", "없는 파일이에요.")
        self._send(200, self.server.files[name], STATIC_TYPES[name])

    def _api(self, method: str, name: str) -> None:
        if method == "POST":
            self._check_origin()
        self._check_token()
        if name in GET_API:
            if method != "GET":
                raise _Reject(405, "method-not-allowed", "읽기 요청은 GET만 받아요.")
            self._send_json(200, self._get(name))
        elif name in POST_API:
            if method != "POST":
                raise _Reject(405, "method-not-allowed", "바꾸는 요청은 POST만 받아요.")
            self._send_json(200, self._post(name, self._body()))
        else:
            raise _Reject(404, "not-found", "없는 API예요.")

    def _get(self, name: str) -> dict:
        app = self.server.app
        if name == "state":
            return app.state()
        if name == "status":
            return app.status()
        query = parse_qs(urlsplit(self.path).query)

        def one(key: str, default: str) -> str:
            values = query.get(key, [])
            return values[0] if len(values) == 1 else default

        return app.audit(one("period", "7d"), one("category", "all"), one("q", ""), AUDIT_LIMIT)

    def _post(self, name: str, body: dict) -> dict:
        app = self.server.app
        confirm = body.get("confirm")
        if name == "interpret":
            return app.interpret(body.get("sentence"))
        if name == "save":
            return app.save(body.get("ticket"), confirm)
        if name == "set":
            return app.set_choice(body.get("key"), body.get("value"), confirm)
        if name == "remove":
            return app.remove(body.get("number"), body.get("fingerprint"), confirm)
        if name == "undo":
            return app.undo(confirm)
        return app.ner(body.get("enable"), confirm)

    def _body(self) -> dict:
        kind = (self.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
        if kind != "application/json":
            raise _Reject(415, "bad-content-type", "요청 본문은 JSON이어야 해요.")
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            raise _Reject(411, "length-required", "요청 길이가 없어요.") from None
        if not 0 <= length <= MAX_BODY_BYTES:
            raise _Reject(413, "too-large", "요청이 너무 커요.")
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8")) if raw else {}
        except ValueError:
            raise _Reject(400, "bad-json", "요청 본문이 올바른 JSON이 아니에요.") from None
        if not isinstance(data, dict):
            raise _Reject(400, "bad-json", "요청 본문은 JSON 객체여야 해요.")
        return data

    # --- 응답 ---

    def _send_json(self, status: int, payload: dict) -> None:
        self._send(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for name, value in SECURITY_HEADERS:
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)


# --- gate ui 명령 ---

def register(sub: argparse._SubParsersAction) -> None:
    ui = sub.add_parser("ui", help="설정 화면: 이 컴퓨터의 브라우저에서 정책 보기·바꾸기, 감사 기록 보기 (사람만)")
    ui.add_argument("--policy", default=os.environ.get(POLICY_ENV), help=f"정책 파일 (기본: ${POLICY_ENV})")
    ui.add_argument("--audit", default=os.environ.get("GATE_AUDIT"), help="감사 로그 파일 (기본: $GATE_AUDIT)")
    ui.add_argument("--port", type=int, default=DEFAULT_PORT, help="포트 (기본 0: 빈 포트를 고름)")
    ui.add_argument("--no-browser", action="store_true", help="브라우저를 열지 않고 주소만 보여 준다")


def run(args: argparse.Namespace, *, stdin: TextIO | None = None, out: TextIO | None = None) -> int:
    stdin = sys.stdin if stdin is None else stdin
    out = sys.stdout if out is None else out
    if not args.policy:
        print(f"gate ui: 정책 파일 경로를 --policy 또는 {POLICY_ENV} 환경변수로 지정하세요 "
              "(예: --policy <hackathon 폴더>/gate-data/policy.yaml).", file=sys.stderr)
        return 1
    if not 0 <= args.port <= 65535:
        print("gate ui: --port는 0~65535예요.", file=sys.stderr)
        return 1
    config = UiConfig.from_env(args.policy, args.audit, getattr(args, "socket", None))
    try:
        server = UiServer(UiApp(config), args.port)
    except OSError as exc:
        print(f"gate ui: {HOST}:{args.port}에서 열 수 없습니다: {exc.strerror or exc}", file=sys.stderr)
        return 1
    thread = threading.Thread(target=server.serve_forever, name="gate-ui", daemon=True)
    thread.start()
    previous = signal.signal(signal.SIGTERM, _on_term)
    try:
        _announce(server, config, args.no_browser, out, first=True)
        if _isatty(stdin):
            for _ in stdin:  # Enter마다 새 열쇠로 다시 연다
                _announce(server, config, args.no_browser, out, first=False)
        while thread.is_alive():
            thread.join(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        signal.signal(signal.SIGTERM, previous)
        server.shutdown()
        server.server_close()
        print("gate ui: 설정 화면을 껐습니다.", file=out, flush=True)
    return 0


def _on_term(signum, frame) -> None:
    raise KeyboardInterrupt


def _announce(server: UiServer, config: UiConfig, no_browser: bool, out: TextIO, *, first: bool) -> None:
    url = server.new_launch_url()
    print(f"gate ui: {PRODUCT_NAME} · {PRODUCT_TAG} 설정 화면 {url}", file=out, flush=True)
    if first:
        lines = [
            "  이 주소는 한 번만 열 수 있어요. 창을 새로 고쳤거나 닫았으면 여기서 Enter를 누르면 새 주소로 다시 열어요.",
            "  주소는 이 터미널에만 보여요. 다른 사람이나 AI 에이전트에게 알려 주지 마세요. 끄려면 Ctrl-C.",
            f"  정책 파일: {config.policy}",
            f"  감사 로그: {config.audit or '지정 안 함 (--audit 또는 GATE_AUDIT)'}",
            f"  게이트 소켓: {config.socket or '지정 안 함 (--socket 또는 GATE_SOCKET, 없으면 게이트 상태를 확인하지 못함)'}",
        ]
        print("\n".join(lines), file=out, flush=True)
    if not no_browser and not webbrowser.open(url):
        print("  브라우저를 열지 못했어요. 위 주소를 브라우저에 붙여 넣어 주세요.", file=out, flush=True)


def _isatty(stream: TextIO) -> bool:
    try:
        return bool(stream.isatty())
    except (AttributeError, ValueError, OSError):
        return False


def static_files() -> list[Path]:
    """패키지에 든 화면 파일(테스트용)."""
    root = Path(str(static_root()))
    return sorted(p for p in root.rglob("*") if p.is_file())
