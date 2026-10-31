"""gate ui (gate/ui.py, gate/ui_api.py): 설정 화면 서버의 보안 검사, API, gate policy와 같은 결과.

실제 브라우저·실제 NER·Laya 서버는 쓰지 않는다. 서버는 127.0.0.1의 빈 포트에 띄우고 http.client로 Host·Origin을 직접 넣는다.
"""

import http.client
import io
import json
import os
import re
import shutil
import socket
import stat
import tempfile
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from gate import detectors, policy_change, ui
from gate.detectors import PATTERNS, VISIBLE_CHOICES, mask
from gate.policy import Entry, build_policy, read_entries, write_entries
from gate.policy_change import history_dir
from gate.ui_api import EXAMPLE_SENTENCES, UiApp, UiConfig, UiError, audit_row, effect_text, fingerprint, loopback_base
from tests.test_policy_cli import gate_policy

FIXED_NOW = "2026-10-31T14:00:00+09:00"
CHANGE_APIS = ("interpret", "save", "set", "remove", "undo", "ner")


# --- 가짜 서버 ---

class FakeHealth:
    """/health만 답하는 가짜 서버(NER·Laya). health를 바꾸면 다음 요청부터 그 답을 한다."""

    def __init__(self, health=None, status=200):
        self.health = {"status": "ok"} if health is None else health
        self.status = status
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                data = json.dumps(outer.health).encode()
                self.send_response(outer.status if self.path == "/health" else 404)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def closed_port_url() -> str:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return f"http://127.0.0.1:{s.getsockname()[1]}"


@pytest.fixture
def health():
    servers = []

    def start(**kwargs):
        s = FakeHealth(**kwargs)
        servers.append(s)
        return s

    yield start
    for s in servers:
        s.close()


# --- 화면 서버 ---

class Ui:
    """띄운 화면 서버와 요청 도우미."""

    def __init__(self, server: ui.UiServer):
        self.server = server
        self.port = server.port
        self.host = f"127.0.0.1:{self.port}"
        self.origin = f"http://127.0.0.1:{self.port}"
        self.log = server.log

    @property
    def policy(self) -> Path:
        return self.server.app.config.policy

    def raw(self, method, path, headers=(), body: bytes | None = None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
        for name, value in headers:
            conn.putheader(name, value)
        if body is not None:
            conn.putheader("Content-Length", str(len(body)))
        conn.endheaders(body)
        response = conn.getresponse()
        data = response.read()
        conn.close()
        return response.status, {k.lower(): v for k, v in response.getheaders()}, data

    def launch(self) -> str:
        url = self.server.new_launch_url()
        status, _, body = self.raw("GET", url[len(self.origin):], [("Host", self.host)])
        assert status == 200
        return re.search(r'name="gate-session" content="([^"]+)"', body.decode()).group(1)

    def get(self, name, token, *, host=None, query=""):
        host = self.host if host is None else host
        status, headers, body = self.raw("GET", f"/api/{name}{query}", [("Host", host), ("X-Gate-Token", token)])
        return status, json.loads(body)

    def post(self, name, body, token, *, origin="same", host=None, content_type="application/json", extra=()):
        headers = [("Host", self.host if host is None else host), ("Content-Type", content_type)]
        if token is not None:
            headers.append(("X-Gate-Token", token))
        if origin == "same":
            headers.append(("Origin", self.origin))
        elif origin is not None:
            headers.append(("Origin", origin))
        headers += list(extra)
        raw = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
        status, _, data = self.raw("POST", f"/api/{name}", headers, raw)
        return status, json.loads(data)


@pytest.fixture
def make_ui(tmp_path):
    servers = []

    def start(policy=None, *, audit=None, socket_path=None, ner_url=None, jev_url=None):
        policy = policy or tmp_path / "gate-data" / "policy.yaml"
        config = UiConfig(policy, audit, socket_path, jev_url or closed_port_url(), ner_url or closed_port_url(),
                          health_timeout=2.0)
        server = ui.UiServer(UiApp(config), 0, log=io.StringIO())
        threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        servers.append(server)
        return Ui(server)

    yield start
    for server in servers:
        server.shutdown()
        server.server_close()


@pytest.fixture
def fixed_now(monkeypatch):
    monkeypatch.setattr(policy_change, "now", lambda: FIXED_NOW)


# --- 바인드·열쇠·토큰 ---

def test_binds_only_to_loopback_ip(make_ui):
    u = make_ui()
    assert u.server.server_address[0] == "127.0.0.1"
    assert u.server.new_launch_url().startswith(f"http://127.0.0.1:{u.port}/?launch=")


def test_launch_key_works_once_and_new_key_replaces_old(make_ui):
    u = make_ui()
    url = u.server.new_launch_url()
    path = url[len(u.origin):]
    assert u.raw("GET", "/?launch=wrong", [("Host", u.host)])[0] == 403
    assert u.raw("GET", "/", [("Host", u.host)])[0] == 403
    status, headers, body = u.raw("GET", path, [("Host", u.host)])
    assert status == 200 and headers["content-type"].startswith("text/html")
    assert 'name="gate-session"' in body.decode() and "__SESSION__" not in body.decode()
    assert u.raw("GET", path, [("Host", u.host)])[0] == 403  # 한 번만
    first = u.server.new_launch_url()
    second = u.server.new_launch_url()
    assert u.raw("GET", first[len(u.origin):], [("Host", u.host)])[0] == 403  # 새 열쇠가 옛 열쇠를 바꾼다
    assert u.raw("GET", second[len(u.origin):], [("Host", u.host)])[0] == 200
    assert "bad-launch" in u.log.getvalue() and "launch=" not in u.log.getvalue()  # 거부만 남기고 주소는 남기지 않는다


def test_api_requires_session_token(make_ui):
    u = make_ui()
    session = u.launch()
    launch = u.server.new_launch_url().split("launch=")[1]
    for token in ("", "wrong", launch, session + "x"):
        status, data = u.get("state", token)
        assert (status, data["error"]) == (403, "bad-token"), token
    status, _, _ = u.raw("GET", "/api/state", [("Host", u.host)])  # 헤더 없음
    assert status == 403
    status, data = u.post("set", {"key": "numbers.card.action", "value": "deny"}, None)
    assert (status, data["error"]) == (403, "bad-token")
    assert u.get("state", session)[0] == 200
    assert not u.policy.exists()


@pytest.mark.parametrize("host", ["localhost:{port}", "127.0.0.1:1", "127.0.0.1", "evil.example", "[::1]:{port}",
                                  "127.0.0.1:{port}.evil.example", ""])
def test_wrong_host_is_rejected_even_with_token(make_ui, host):
    u = make_ui()
    session = u.launch()
    host = host.format(port=u.port)
    status, data = u.get("state", session, host=host)
    assert (status, data["error"]) == (403, "bad-host")
    status, data = u.post("set", {"key": "numbers.card.action", "value": "deny"}, session, host=host)
    assert (status, data["error"]) == (403, "bad-host")
    assert u.raw("GET", u.server.new_launch_url()[len(u.origin):], [("Host", host)])[0] == 403
    assert not u.policy.exists()


def test_missing_or_double_host_is_rejected(make_ui):
    u = make_ui()
    session = u.launch()
    assert u.raw("GET", "/api/state", [("X-Gate-Token", session)])[0] == 403
    assert u.raw("GET", "/api/state", [("Host", u.host), ("Host", u.host), ("X-Gate-Token", session)])[0] == 403


@pytest.mark.parametrize("origin", [None, "http://localhost:{port}", "http://evil.example", "null",
                                    "https://127.0.0.1:{port}", "http://127.0.0.1:1"])
def test_change_request_needs_same_origin(make_ui, origin):
    u = make_ui()
    session = u.launch()
    origin = origin.format(port=u.port) if origin else None
    status, data = u.post("set", {"key": "numbers.card.action", "value": "deny"}, session, origin=origin)
    assert (status, data["error"]) == (403, "bad-origin")
    assert not u.policy.exists()


def test_cross_site_fetch_metadata_is_rejected(make_ui):
    u = make_ui()
    session = u.launch()
    status, data = u.post("set", {"key": "numbers.card.action", "value": "deny"}, session,
                          extra=[("Sec-Fetch-Site", "cross-site")])
    assert (status, data["error"]) == (403, "bad-origin")
    status, data = u.post("set", {"key": "numbers.card.action", "value": "deny"}, session,
                          extra=[("Sec-Fetch-Site", "same-origin")])
    assert (status, data["status"]) == (200, "saved")


@pytest.mark.parametrize("name", CHANGE_APIS)
def test_change_apis_reject_get(make_ui, name):
    u = make_ui()
    session = u.launch()
    status, data = u.get(name, session, query="?key=numbers.card.action&value=deny&enable=true")
    assert (status, data["error"]) == (405, "method-not-allowed")
    assert not u.policy.exists()


def test_read_apis_reject_post_and_other_methods(make_ui):
    u = make_ui()
    session = u.launch()
    assert u.post("state", {}, session)[0] == 405
    for method in ("PUT", "DELETE", "PATCH", "OPTIONS"):
        status, _, _ = u.raw(method, "/api/set", [("Host", u.host), ("X-Gate-Token", session), ("Origin", u.origin)])
        assert status == 405, method


def test_body_must_be_small_json_object(make_ui):
    u = make_ui()
    session = u.launch()
    assert u.post("set", b"key=numbers.card.action", session, content_type="application/x-www-form-urlencoded")[0] == 415
    assert u.post("set", b"x" * (ui.MAX_BODY_BYTES + 1), session)[0] == 413
    assert u.post("set", b"{not json", session)[0] == 400
    assert u.post("set", b"[1, 2]", session)[0] == 400
    assert not u.policy.exists()


def test_every_response_has_security_headers(make_ui):
    u = make_ui()
    session = u.launch()
    responses = [
        u.raw("GET", u.server.new_launch_url()[len(u.origin):], [("Host", u.host)]),
        u.raw("GET", "/?launch=wrong", [("Host", u.host)]),
        u.raw("GET", f"/s/{session}/app.js", [("Host", u.host)]),
        u.raw("GET", "/api/state", [("Host", u.host), ("X-Gate-Token", session)]),
        u.raw("GET", "/api/state", [("Host", "evil.example")]),
    ]
    for status, headers, _ in responses:
        csp = headers["content-security-policy"]
        assert "default-src 'none'" in csp and "connect-src 'self'" in csp and "frame-ancestors 'none'" in csp
        assert "http" not in csp  # 외부 출처를 허용하지 않는다
        assert headers["referrer-policy"] == "no-referrer"
        assert headers["cache-control"] == "no-store"
        assert headers["x-frame-options"] == "DENY" and headers["x-content-type-options"] == "nosniff"
        assert "set-cookie" not in headers


def test_static_files_need_session_and_allowlist(make_ui):
    u = make_ui()
    session = u.launch()
    status, headers, body = u.raw("GET", f"/s/{session}/app.js", [("Host", u.host)])
    assert status == 200 and headers["content-type"].startswith("text/javascript") and b"X-Gate-Token" in body
    for name, kind in (("app.css", "text/css"), ("plant-mark.png", "image/png"),
                       ("fonts/outfit-latin-700-normal.woff2", "font/woff2")):
        status, headers, _ = u.raw("GET", f"/s/{session}/{name}", [("Host", u.host)])
        assert status == 200 and headers["content-type"].startswith(kind), name
    assert u.raw("GET", "/s/wrong/app.js", [("Host", u.host)])[0] == 403
    for name in ("../ui.py", "fonts/OFL-Outfit.txt", "index.html", "%2e%2e/ui.py", ""):
        assert u.raw("GET", f"/s/{session}/{name}", [("Host", u.host)])[0] == 404, name


def test_request_log_never_contains_tokens(make_ui):
    u = make_ui()
    session = u.launch()
    u.get("state", "wrong")
    u.get("state", session, host="evil.example")
    log = u.log.getvalue()
    assert "bad-token" in log and "bad-host" in log
    assert session not in log and "/api/" not in log


# --- 외부 요청 0 ---

URL = re.compile(r"https?://[A-Za-z0-9.\-\[\]]+")


def test_static_files_reference_no_external_url():
    files = {p.name: p.read_text(encoding="utf-8") for p in ui.static_files() if p.suffix in (".html", ".css", ".js")}
    assert set(files) == {"index.html", "app.css", "app.js"}
    for name, text in files.items():
        for match in URL.findall(text):
            assert match in ("http://127.0.0.1",), (name, match)  # 상태 안내의 이 컴퓨터 주소뿐
        assert "fonts.googleapis" not in text and "cdn." not in text, name
    index = files["index.html"]
    for ref in re.findall(r'(?:src|href)="([^"]+)"', index):
        assert ref.startswith("/s/__SESSION__/") or ref.startswith("#"), ref
    assert "@import" not in files["app.css"]
    for ref in re.findall(r"url\(([^)]+)\)", files["app.css"]):
        assert ref.startswith("fonts/") and ref.endswith(".woff2"), ref


def test_static_folder_has_only_expected_files_and_licenses():
    names = {str(p.relative_to(Path(str(ui.static_root())))) for p in ui.static_files()}
    expected = {"index.html", "app.css", "app.js", "plant-mark.png", "fonts/OFL-Outfit.txt", "fonts/OFL-JetBrainsMono.txt",
                *(f"fonts/{f}.woff2" for f in ui.FONTS)}
    assert names == expected
    root = Path(str(ui.static_root()))
    for license_file in ("OFL-Outfit.txt", "OFL-JetBrainsMono.txt"):
        assert "SIL OPEN FONT LICENSE Version 1.1" in (root / "fonts" / license_file).read_text(encoding="utf-8")
    for font in ui.FONTS:
        assert (root / "fonts" / f"{font}.woff2").read_bytes()[:4] == b"wOF2"
    assert (root / "plant-mark.png").read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_api_responses_contain_no_external_url(make_ui, health):
    ner = health()
    u = make_ui(ner_url=ner.url)
    session = u.launch()
    for name in ("state", "status", "audit"):
        status, data = u.get(name, session)
        text = json.dumps(data, ensure_ascii=False)
        for match in URL.findall(text):
            assert match == "http://127.0.0.1", (name, match)


# --- 정규식 정보 ---

def test_regex_info_comes_from_detectors(make_ui):
    u = make_ui()
    _, data = u.get("state", u.launch())
    assert [row["kind"] for row in data["numbers"]] == list(detectors.KINDS)
    for row in data["numbers"]:
        kind = row["kind"]
        assert row["rule"]["patterns"] == [{"name": n, "pattern": p.pattern} for n, p in PATTERNS[kind]]
        for name, pattern in PATTERNS[kind]:
            assert getattr(detectors, name) is pattern  # 이름은 detectors의 실제 변수
        example = row["rule"]["example"]
        assert [f.kind for f in mask(example).findings] == [kind], example
        assert row["rule"]["example_masked"] == mask(example, visible={kind: row["visible"]["value"]}).text


def test_patterns_cover_every_kind():
    assert tuple(PATTERNS) == detectors.KINDS
    assert all(PATTERNS[kind] for kind in detectors.KINDS)


def test_fixed_visible_is_shown_as_safety_rule_not_organisation(make_ui):
    u = make_ui()
    _, data = u.get("state", u.launch())
    for row in data["numbers"]:
        locked = VISIBLE_CHOICES[row["kind"]] == ("none",)
        assert row["visible"]["locked"] is locked
        assert ("안전 규칙" in row["rule"]["lock_reason"]) is locked
    assert {row["kind"] for row in data["numbers"] if row["visible"]["locked"]} == {"account", "secret"}
    texts = [p.read_text(encoding="utf-8") for p in ui.static_files() if p.suffix in (".html", ".js")]
    assert all("기관에서 정함" not in t for t in texts)
    assert any("안전 규칙으로 고정" in t for t in texts)


def test_masked_example_follows_current_visible(make_ui):
    u = make_ui()
    write_entries(u.policy, [Entry(settings={"numbers.rrn.visible": "birth"})])
    _, data = u.get("state", u.launch())
    rrn = next(row for row in data["numbers"] if row["kind"] == "rrn")
    assert rrn["rule"]["example_masked"] == "900101-*******" and rrn["visible"]["mine"]


# --- 완화 확인 ---

def test_loosening_set_needs_matching_confirm(make_ui, fixed_now):
    u = make_ui()
    session = u.launch()
    body = {"key": "numbers.rrn.visible", "value": "birth"}
    status, first = u.post("set", body, session)
    assert (status, first["status"]) == (200, "confirm")
    assert first["loosening"] == ["주민등록번호: 전부 가림 → 주민등록번호: 생년월일(앞 6자리)까지 공개"]
    assert not u.policy.exists()
    status, wrong = u.post("set", {**body, "confirm": "0" * 32}, session)
    assert wrong["status"] == "confirm" and not u.policy.exists()
    status, done = u.post("set", {**body, "confirm": first["confirm"]}, session)
    assert done["status"] == "saved"
    assert read_entries(u.policy) == (Entry(settings={"numbers.rrn.visible": "birth"}, at=FIXED_NOW),)


def test_confirm_is_asked_again_if_policy_changed_in_between(make_ui):
    u = make_ui()
    session = u.launch()
    body = {"key": "numbers.rrn.visible", "value": "birth"}
    _, first = u.post("set", body, session)
    write_entries(u.policy, [Entry(settings={"numbers.card.action": "deny"})])  # 그사이 다른 곳에서 바뀜
    _, second = u.post("set", {**body, "confirm": first["confirm"]}, session)
    assert second["status"] == "confirm"
    assert read_entries(u.policy) == (Entry(settings={"numbers.card.action": "deny"}),)


def test_stricter_set_saves_without_confirm_and_same_value_is_unchanged(make_ui):
    u = make_ui()
    session = u.launch()
    _, data = u.post("set", {"key": "entities.name.action", "value": "deny"}, session)
    assert data["status"] == "saved"
    _, data = u.post("set", {"key": "entities.name.action", "value": "deny"}, session)
    assert data["status"] == "unchanged"
    assert len(read_entries(u.policy)) == 1


@pytest.mark.parametrize("key, value", [
    ("ner_backend", "server"), ("jev_backend", "mock"), ("thresholds.allow_if_safe_confidence_gte", "0.5"),
    ("numbers.rrn", "birth"), ("numbers.rrn.visible", "last4"), ("numbers.card.action", "allow"),
    ("entities.nobody.action", "mask"), (None, "x"), ("numbers.card.action", 3),
])
def test_set_only_takes_number_and_entity_choices(make_ui, key, value):
    u = make_ui()
    status, data = u.post("set", {"key": key, "value": value}, u.launch())
    assert status == 400 and data["ok"] is False
    assert not u.policy.exists()


def test_save_loosening_sentence_needs_confirm(make_ui, fixed_now):
    u = make_ui()
    session = u.launch()
    _, result = u.post("interpret", {"sentence": "주민번호는 생년월일까지만 보여줘"}, session)
    assert result["saveable"] and result["loosening"]
    _, first = u.post("save", {"ticket": result["ticket"]}, session)
    assert first["status"] == "confirm" and not u.policy.exists()
    _, done = u.post("save", {"ticket": result["ticket"], "confirm": first["confirm"]}, session)
    assert done["status"] == "saved"
    _, again = u.post("save", {"ticket": result["ticket"]}, session)
    assert again["error"] == "ticket-expired"  # 저장한 표는 다시 쓰지 않는다


def test_interpret_does_not_save(make_ui):
    u = make_ui()
    session = u.launch()
    _, result = u.post("interpret", {"sentence": "카드번호 있는 파일은 거부"}, session)
    assert result["saveable"] and result["preview"]["before"] == "mask" and result["preview"]["value"] == "deny"
    assert result["preview"]["effect"] == "카드번호가 있는 파일은 읽지 못하게 해요."
    assert not u.policy.exists()


@pytest.mark.parametrize("sentence, outcome", [
    ("아무거나", "unclear"), ("공개 폴더는 그냥 보여줘", "path"), ("병원 기록은 보여줘도 돼", "unsupported"), ("", "unclear"),
])
def test_unsaveable_interpretation_has_message_and_no_ticket(make_ui, sentence, outcome):
    u = make_ui()
    status, result = u.post("interpret", {"sentence": sentence}, u.launch())
    assert status == 200 and result["outcome"] == outcome and not result["saveable"]
    assert "ticket" not in result and result["message"]


def test_context_sentence_is_marked_without_effect(make_ui):
    u = make_ui()
    session = u.launch()
    _, result = u.post("interpret", {"sentence": "병원 기록은 보여주지 마"}, session)
    assert result["preview"]["type"] == "context" and result["notes"] == ["context_unused"]
    u.post("save", {"ticket": result["ticket"]}, session)
    _, data = u.get("state", session)
    assert data["entries"][0]["no_effect"] == "지금은 효과 없음"


def test_example_sentences_are_understood():
    app = UiApp(UiConfig(Path(tempfile.mkdtemp()) / "policy.yaml"))
    for sentence in EXAMPLE_SENTENCES:
        assert app.interpret(sentence)["saveable"], sentence


def test_ner_entity_note(make_ui):
    u = make_ui()
    _, result = u.post("interpret", {"sentence": "이름은 가려줘"}, u.launch())
    assert result["notes"] == ["ner_off", "no_change"]


# --- gate policy와 같은 결과 ---

def snapshot(policy: Path) -> dict:
    """정책 파일과 기록 폴더의 내용·권한."""
    out = {}
    for path in [policy, *sorted(history_dir(policy).glob("*"))]:
        if path.exists():
            out[str(path.relative_to(policy.parent))] = (path.read_bytes(), stat.S_IMODE(os.stat(path).st_mode))
    if history_dir(policy).exists():
        out["history-dir-mode"] = stat.S_IMODE(os.stat(history_dir(policy)).st_mode)
    return out


def test_ui_changes_write_the_same_files_as_gate_policy(make_ui, tmp_path, fixed_now):
    cli_policy = tmp_path / "cli" / "policy.yaml"
    u = make_ui(tmp_path / "ui" / "policy.yaml")
    session = u.launch()

    def via_ui(name, body):
        _, result = u.post(name, body, session)
        if result.get("status") == "confirm":
            _, result = u.post(name, {**body, "confirm": result["confirm"]}, session)
        assert result["status"] == "saved", result

    def via_sentence(sentence):
        _, result = u.post("interpret", {"sentence": sentence}, session)
        via_ui("save", {"ticket": result["ticket"]})

    def entry_fp(number):
        _, data = u.get("state", session)
        return data["entries"][number - 1]["fingerprint"]

    steps = [
        (("set", "numbers.card.action", "deny"), lambda: via_ui("set", {"key": "numbers.card.action", "value": "deny"})),
        (("add", "주민번호는 생년월일까지만 보여줘"), lambda: via_sentence("주민번호는 생년월일까지만 보여줘")),
        (("add", "병원 기록은 보여주지 마"), lambda: via_sentence("병원 기록은 보여주지 마")),
        (("set", "entities.student_id.action", "ask_human"),
         lambda: via_ui("set", {"key": "entities.student_id.action", "value": "ask_human"})),
        (("set", "numbers.mobile.visible", "prefix"), lambda: via_ui("set", {"key": "numbers.mobile.visible", "value": "prefix"})),
        (("remove", "1"), lambda: via_ui("remove", {"number": 1, "fingerprint": entry_fp(1)})),
        (("undo",), lambda: via_ui("undo", {})),
        (("undo",), lambda: via_ui("undo", {})),
        (("remove", "2"), lambda: via_ui("remove", {"number": 2, "fingerprint": entry_fp(2)})),
    ]
    for argv, ui_step in steps:
        code, out = gate_policy(cli_policy, *argv, answers="y\ny\n")
        assert code == 0, out
        ui_step()
        assert snapshot(u.policy) == snapshot(cli_policy), argv
    assert snapshot(u.policy)["history-dir-mode"] == 0o700


def test_ner_toggle_writes_the_same_as_gate_policy_set(make_ui, tmp_path, health, fixed_now):
    ner = health()
    cli_policy = tmp_path / "cli" / "policy.yaml"
    u = make_ui(tmp_path / "ui" / "policy.yaml", ner_url=ner.url)
    session = u.launch()
    assert gate_policy(cli_policy, "set", "ner_backend", "server")[0] == 0
    assert u.post("ner", {"enable": True}, session)[1]["status"] == "saved"
    assert snapshot(u.policy) == snapshot(cli_policy)
    assert gate_policy(cli_policy, "set", "ner_backend", "none", answers="y\n")[0] == 0
    _, first = u.post("ner", {"enable": False}, session)
    assert first["status"] == "confirm"  # 끄기는 느슨해지는 바꿈
    assert u.post("ner", {"enable": False, "confirm": first["confirm"]}, session)[1]["status"] == "saved"
    assert snapshot(u.policy) == snapshot(cli_policy)


# --- NER 켜기 ---

@pytest.mark.parametrize("kind", ["closed", "loading", "error"])
def test_ner_enable_is_refused_when_health_fails(make_ui, health, kind):
    if kind == "closed":
        url = closed_port_url()
    else:
        server = health(health={"status": "loading"}) if kind == "loading" else health(status=500)
        url = server.url
    u = make_ui(ner_url=url)
    session = u.launch()
    status, data = u.post("ner", {"enable": True}, session)
    assert (status, data["error"]) == (409, "ner-down")
    assert not u.policy.exists()
    _, st = u.get("status", session)
    assert st["ner"]["responding"] is False and st["ner"]["state"] == "unused"


def test_ner_enable_refuses_non_ip_address(make_ui, health):
    server = health()
    u = make_ui(ner_url=server.url.replace("127.0.0.1", "localhost"))
    status, data = u.post("ner", {"enable": True}, u.launch())
    assert (status, data["error"]) == (409, "ner-down") and "IP" in data["message"]


def test_ner_enable_then_server_down_shows_down_and_disable_needs_no_health(make_ui, health):
    server = health()
    u = make_ui(ner_url=server.url)
    session = u.launch()
    assert u.post("ner", {"enable": True}, session)[1]["status"] == "saved"
    _, st = u.get("status", session)
    assert st["ner"]["state"] == "connected" and st["ner"]["enabled"]
    server.health = {"status": "loading"}
    _, st = u.get("status", session)
    assert st["ner"]["state"] == "down"
    _, first = u.post("ner", {"enable": False}, session)
    _, done = u.post("ner", {"enable": False, "confirm": first["confirm"]}, session)
    assert done["status"] == "saved" and build_policy(read_entries(u.policy)).ner_backend == "none"


def test_ner_enable_value_must_be_bool(make_ui):
    u = make_ui()
    assert u.post("ner", {"enable": "true"}, u.launch())[0] == 400


# --- 지우기·되돌리기 ---

def test_remove_refuses_stale_list(make_ui):
    u = make_ui()
    session = u.launch()
    write_entries(u.policy, [Entry(settings={"numbers.card.action": "deny"}), Entry(settings={"numbers.email.action": "deny"})])
    _, data = u.get("state", session)
    second = data["entries"][1]["fingerprint"]
    write_entries(u.policy, [Entry(settings={"numbers.card.action": "mask"}), Entry(settings={"numbers.mobile.action": "deny"})])
    status, result = u.post("remove", {"number": 2, "fingerprint": second}, session)
    assert (status, result["error"]) == (409, "stale")  # 같은 번호여도 다른 항목이면 지우지 않는다
    status, result = u.post("remove", {"number": 3, "fingerprint": second}, session)
    assert (status, result["error"]) == (409, "stale")
    assert u.post("remove", {"number": "1", "fingerprint": second}, session)[0] == 400
    assert len(read_entries(u.policy)) == 2


def test_undo_without_history(make_ui):
    u = make_ui()
    status, data = u.post("undo", {}, u.launch())
    assert (status, data["error"]) == (409, "cannot-undo") and "되돌릴 기록이 없습니다" in data["message"]


def test_invalid_policy_file_state_and_undo(make_ui, fixed_now):
    u = make_ui()
    session = u.launch()
    u.post("set", {"key": "numbers.card.action", "value": "deny"}, session)
    u.policy.write_text("version: 1\nentries: [\n", encoding="utf-8")
    _, data = u.get("state", session)
    assert data["invalid"] and data["policy_file"]["error"] and data["history"] == 1
    status, result = u.post("set", {"key": "numbers.email.action", "value": "deny"}, session)
    assert (status, result["error"]) == (409, "policy-invalid")
    _, first = u.post("undo", {}, session)
    assert first["status"] == "confirm" and first["problem"]
    _, done = u.post("undo", {"confirm": first["confirm"]}, session)
    assert done["status"] == "saved" and read_entries(u.policy) == ()


# --- 상태 ---

@pytest.fixture
def short_dir():
    path = Path(tempfile.mkdtemp(prefix="gui-"))
    yield path
    shutil.rmtree(path, ignore_errors=True)


def test_status_of_gate_daemon_socket(make_ui, short_dir, tmp_path):
    sock_path = short_dir / "gate.sock"
    audit = tmp_path / "audit.jsonl"
    audit.write_text(json.dumps({"ts": "2026-10-31T14:22:00.000+09:00", "decision": "allow"}) + "\n", encoding="utf-8")
    u = make_ui(socket_path=sock_path, audit=audit)
    session = u.launch()
    assert u.get("status", session)[1]["gate"]["state"] == "off"
    accepted = []
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
        listener.bind(str(sock_path))
        listener.listen(1)
        _, st = u.get("status", session)
        listener.settimeout(1)
        conn, _ = listener.accept()
        accepted.append(conn.recv(10))
        conn.close()
    assert st["gate"]["state"] == "on" and st["gate"]["last_request"] == "2026-10-31T14:22:00.000+09:00"
    assert accepted == [b""]  # 연결만 하고 요청은 보내지 않는다(감사 로그에 남지 않는다)


def test_status_with_unusable_socket_path_is_unknown_with_reason(make_ui, tmp_path):
    long_path = tmp_path / ("x" * 120) / "gate.sock"  # AF_UNIX 경로 길이 제한(맥 104, Linux 108바이트)을 넘는다
    u = make_ui(socket_path=long_path)
    _, st = u.get("status", u.launch())
    assert st["gate"]["state"] == "unknown" and st["gate"]["reason"] and st["gate"]["socket"] == str(long_path)


def test_status_without_socket_is_unknown(make_ui):
    u = make_ui()
    _, st = u.get("status", u.launch())
    assert st["gate"]["state"] == "unknown" and st["gate"]["socket"] is None and st["gate"]["last_request"] is None
    assert st["laya"]["state"] == "unused" and st["laya"]["backend"] == "mock"


def test_status_of_laya_when_used(make_ui, health):
    laya = health()
    u = make_ui(jev_url=laya.url)
    write_entries(u.policy, [Entry(settings={"jev_backend": "laya"})])
    session = u.launch()
    _, st = u.get("status", session)
    assert st["laya"]["state"] == "connected" and st["laya"]["address"] == laya.url.removeprefix("http://")
    laya.status = 503
    assert u.get("status", session)[1]["laya"]["state"] == "down"


@pytest.mark.parametrize("url, ok", [
    ("http://127.0.0.1:8000", True), ("http://[::1]:8000", True), ("http://127.0.0.2:1", True),
    ("http://localhost:8000", False), ("http://10.0.0.1:8000", False), ("ftp://127.0.0.1:1", False),
    ("http://user:pw@127.0.0.1:1", False), ("http://example.com", False),
])
def test_status_checks_only_loopback_ip(url, ok):
    if ok:
        assert loopback_base(url)[0].startswith(url.split("/")[0])
    else:
        with pytest.raises(ValueError):
            loopback_base(url)


# --- 감사 기록 ---

def record(ts, decision, path="/상담/a.txt", **extra):
    return {"v": 1, "ts": ts, "pid": 1, "process": "python3", "op": "cat", "path": path, "errno": None,
            "decision": decision, "reason": extra.pop("reason", "이유"), "findings": extra.pop("findings", {}),
            "approval": extra.pop("approval", None), **extra}


def test_audit_filters(tmp_path):
    log = tmp_path / "audit.jsonl"
    lines = [
        record("2026-10-20T10:00:00.000+09:00", "allow", "/메모/old.txt"),
        record("2026-10-31T09:00:00.000+09:00", "mask", findings={"rrn": 1}),
        record("2026-10-31T10:00:00.000+09:00", "mask", "/상담/b.txt", approval="once", reason="사용자가 승인했습니다"),
        record("2026-10-31T11:00:00.000+09:00", "deny", "/설정/.env", reason="경로 규칙에 따라 차단했습니다"),
        {**record("2026-10-31T11:30:00.000+09:00", "ok"), "op": "ls"},
        {**record("2026-10-31T11:40:00.000+09:00", "error", "/../밖"), "errno": "EACCES", "reason": None},
    ]
    log.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in lines) + "깨진 줄\n", encoding="utf-8")
    app = UiApp(UiConfig(tmp_path / "policy.yaml", log))
    moment = datetime.fromisoformat("2026-10-31T12:00:00+09:00")
    today = app.audit("today", moment=moment)
    assert today["counts"] == {"allow": 0, "mask": 1, "ask": 1, "deny": 2}
    assert today["hidden"] == 1 and today["broken"] == 1 and today["total"] == 6
    assert [r["path"] for r in today["records"]] == ["/../밖", "/설정/.env", "/상담/b.txt", "/상담/a.txt"]  # 최근 것부터
    asked = app.audit("today", "ask", moment=moment)["records"]
    assert [(r["category"], r["after"]) for r in asked] == [("ask", "이번만 허용")]
    assert app.audit("all", moment=moment)["counts"]["allow"] == 1
    assert [r["path"] for r in app.audit("all", query="상담", moment=moment)["records"]] == ["/상담/b.txt", "/상담/a.txt"]
    assert today["records"][-1]["found"] == "주민등록번호 1건"
    with pytest.raises(UiError):
        app.audit("year")


def test_audit_unavailable(tmp_path):
    assert UiApp(UiConfig(tmp_path / "p.yaml")).audit()["reason"] == "no-path"
    assert UiApp(UiConfig(tmp_path / "p.yaml", tmp_path / "none.jsonl")).audit()["reason"] == "missing"


def test_audit_row_cleans_agent_text():
    row = audit_row(record("2026-10-31T10:00:00+09:00", "deny", "/a\x1b[31m.txt", reason="이유\n둘", process="cl\x07aude"))
    assert "\x1b" not in row["path"] and "\n" not in row["reason"] and "\x07" not in row["process"]


# --- 상태 화면 문구 ---

def test_effect_text_uses_right_particles():
    assert effect_text("entities.student_id.action", "ask_human") == \
        "학번이 있는 파일은 사람에게 물어본 뒤 보여줘요. 허용해도 학번은 가린 채로 보여줘요."
    assert effect_text("numbers.card.action", "mask") == "카드번호를 가린 뒤 보여줘요."
    assert effect_text("numbers.rrn.visible", "birth") == "주민등록번호를 가릴 때 생년월일(앞 6자리)까지 공개해요."
    assert effect_text("entities.org.action", "deny").startswith("NER이 소속(학교·회사·병원 등)을")
    assert effect_text("entities.plain.action", "mask").endswith("다섯 가지가 함께 바뀌어요.")


def test_state_marks_my_values_and_overridden_entries(make_ui):
    u = make_ui()
    write_entries(u.policy, [Entry(settings={"numbers.card.action": "deny"}, said="카드번호 있는 파일은 거부"),
                             Entry(settings={"numbers.card.action": "ask_human"}),
                             Entry(settings={"entities.name.action": "deny"})])
    _, data = u.get("state", u.launch())
    card = next(row for row in data["numbers"] if row["kind"] == "card")
    assert card["action"] == {"key": "numbers.card.action", "value": "ask_human", "default": "mask",
                              "choices": ["deny", "ask_human", "mask"], "mine": True}
    assert [e["no_effect"] for e in data["entries"]] == ["뒤에 정한 것이 덮어씀", "", "NER을 켜야 효과 있음"]
    assert data["entries"][0]["fingerprint"] == fingerprint(read_entries(u.policy)[0])
    name = next(row for row in data["entities"] if row["group"] == "name")
    assert name["value"] == "deny" and name["mine"] and name["choices"] == ["deny", "ask_human", "mask", "allow"]


# --- gate ui 명령 ---

def test_cli_requires_policy_path(monkeypatch, capsys):
    from gate.cli import main
    monkeypatch.delenv("GATE_POLICY", raising=False)
    assert main(["ui", "--no-browser"]) == 1
    assert "GATE_POLICY" in capsys.readouterr().err


def test_announce_prints_url_and_does_not_open_browser_with_no_browser(make_ui, monkeypatch):
    opened = []
    monkeypatch.setattr(ui.webbrowser, "open", lambda url: opened.append(url) or True)
    u = make_ui()
    out = io.StringIO()
    ui._announce(u.server, u.server.app.config, True, out, first=True)
    text = out.getvalue()
    assert re.search(rf"http://127\.0\.0\.1:{u.port}/\?launch=\S+", text) and "Ctrl-C" in text and opened == []
    ui._announce(u.server, u.server.app.config, False, out, first=False)
    assert len(opened) == 1 and opened[0].startswith(u.origin)


def test_product_name_lives_in_one_place(make_ui):
    files = {p.name: p.read_text(encoding="utf-8") for p in ui.static_files() if p.suffix in (".html", ".js", ".css")}
    assert all("PLANT" not in text for text in files.values())  # 화면 파일에는 자리표시만 둔다
    assert "__PRODUCT_NAME__" in files["index.html"] and "__PRODUCT_TAG__" in files["index.html"]
    u = make_ui()
    _, _, body = u.raw("GET", u.server.new_launch_url()[len(u.origin):], [("Host", u.host)])
    page = body.decode()
    assert f"<title>{ui.PRODUCT_NAME} · {ui.PRODUCT_TAG}</title>" in page and "__PRODUCT" not in page
