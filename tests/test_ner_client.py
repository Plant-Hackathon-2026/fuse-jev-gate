"""gate.ner_client (3c): 로컬 NER 서버 클라이언트, 응답 검사, 정책 칸 나누기. 서버는 가짜다. 모든 값은 가짜 예시다."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from gate.detectors import mask, mask_bytes
from gate.ner_client import (
    ENTITY_GROUPS,
    IGNORED_LABELS,
    MASK_TEXT,
    NER_BACKENDS,
    NER_LABELS,
    Entity,
    NerError,
    ServerNerClient,
    clean_text,
    make_ner_client,
    outside,
    parse_entities,
    policy_slot,
)


class FakeNerServer:
    """계약(plans/ner-parallel.md)대로 답하는 가짜 nerserve. find는 texts 하나마다 개체 목록을 돌려주는 함수다."""

    def __init__(self, find=lambda text: [], model="ner:fake0001", health=None, status=200, raw=None):
        self.find, self.model, self.status, self.raw = find, model, status, raw
        self.health = health if health is not None else {"status": "ok", "model": model, "device": "cpu",
                                                          "labels": list(NER_LABELS)}
        self.requests: list[dict] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def _send(self, status, body):
                data = body if isinstance(body, bytes) else json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                self._send(200, outer.health) if self.path == "/health" else self._send(404, {"error": "없음"})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.requests.append(body)
                if outer.raw is not None:
                    return self._send(outer.status, outer.raw)
                results = [outer.find(t) for t in body["texts"]]
                self._send(outer.status, {"model": outer.model, "results": results})

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def server():
    servers = []

    def start(**kwargs):
        s = FakeNerServer(**kwargs)
        servers.append(s)
        return s

    yield start
    for s in servers:
        s.close()


def spans_of(text, words):
    """words: {글자: 종류}. 글자가 나오는 곳마다 개체 하나."""
    out = []
    for word, label in words.items():
        start = text.find(word)
        while start != -1:
            out.append({"start": start, "end": start + len(word), "label": label, "score": 0.9})
            start = text.find(word, start + 1)
    return sorted(out, key=lambda s: s["start"])


# --- 서버 클라이언트 ---

def test_find_returns_entities_and_sends_whole_text_with_threshold(server):
    text = "상담자 홍길동은 가온대학교 학생입니다."
    s = server(find=lambda t: spans_of(t, {"홍길동": "NAME", "가온대학교": "SCHOOL"}))
    client = ServerNerClient(base_url=s.url, threshold=0.4)
    result = client.find(text)
    assert result.model == "ner:fake0001"
    assert [(e.label, text[e.start:e.end]) for e in result.entities] == [("NAME", "홍길동"), ("SCHOOL", "가온대학교")]
    assert s.requests == [{"texts": [text], "threshold": 0.4}]
    assert client.cache_tag() == "ner:fake0001"


def test_surrogates_are_replaced_with_same_length(server):
    data = b"\xff\xfe \xea\xb9\x80"  # 깨진 바이트 두 개 + 공백 + '김'
    text = data.decode("utf-8", "surrogateescape")
    s = server(find=lambda t: spans_of(t, {"김": "NAME"}))
    result = ServerNerClient(base_url=s.url).find(text)
    assert s.requests[0]["texts"] == ["�� 김"]
    assert clean_text(text) == "�� 김" and len(clean_text(text)) == len(text)
    assert [(e.start, e.end) for e in result.entities] == [(3, 4)]


def test_blank_text_is_not_sent(server):
    s = server()
    assert ServerNerClient(base_url=s.url).find("  \n").entities == ()
    assert s.requests == []


@pytest.mark.parametrize("url", ["", "   ", "http://example.com:8001", "http://10.0.0.5:8001", "ftp://127.0.0.1",
                                 "http://user:pw@127.0.0.1:8001", "http://127.0.0.1:8001?x=1", "http://127.0.0.1:99999"])
def test_only_this_computer_is_accepted(url):
    with pytest.raises(NerError) as info:
        ServerNerClient(base_url=url)
    assert "example.com" not in str(info.value) and "pw" not in str(info.value)


def test_base_url_comes_from_environment():
    client = ServerNerClient(env={"NER_BASE_URL": "http://localhost:8001/"})
    assert "localhost" not in repr(client)
    with pytest.raises(NerError, match="NER_BASE_URL"):
        ServerNerClient(env={})


@pytest.mark.parametrize("kwargs", [{"timeout": 0}, {"timeout": True}, {"threshold": 0}, {"threshold": 1.0},
                                    {"threshold": float("nan")}])
def test_bad_settings_are_rejected(kwargs):
    with pytest.raises(NerError):
        ServerNerClient(base_url="http://127.0.0.1:8001", **kwargs)


def test_unreachable_server_is_an_error():
    client = ServerNerClient(base_url="http://127.0.0.1:9", timeout=1)
    for call in (client.cache_tag, lambda: client.find("홍길동")):
        with pytest.raises(NerError, match="연결하지 못했습니다") as info:
            call()
        assert "127.0.0.1" not in str(info.value)


@pytest.mark.parametrize("status, raw, message", [
    (500, {"error": "boom"}, "HTTP 500"),
    (200, b"not json", "JSON이 아닙니다"),
    (200, {"model": "ner:x", "results": []}, "results"),
    (200, {"model": "ner:x", "results": [[], []]}, "results"),
    (200, {"model": "", "results": [[]]}, "model"),
    (200, {"results": [[]]}, "model"),
    (200, [1, 2], "형식"),
])
def test_bad_responses_are_errors(server, status, raw, message):
    s = server(status=status, raw=raw)
    with pytest.raises(NerError, match=message):
        ServerNerClient(base_url=s.url).find("홍길동")


def test_unhealthy_server_is_an_error(server):
    s = server(health={"status": "loading"})
    with pytest.raises(NerError, match="준비"):
        ServerNerClient(base_url=s.url).cache_tag()


@pytest.mark.parametrize("items, message", [
    ([{"start": 0, "end": 9, "label": "NAME", "score": 0.9}], "문서 밖"),
    ([{"start": 2, "end": 2, "label": "NAME", "score": 0.9}], "문서 밖"),
    ([{"start": -1, "end": 2, "label": "NAME", "score": 0.9}], "문서 밖"),
    ([{"start": True, "end": 2, "label": "NAME", "score": 0.9}], "문서 밖"),
    ([{"start": 0.0, "end": 2, "label": "NAME", "score": 0.9}], "문서 밖"),
    ([{"start": 0, "end": 2, "label": "B-NAME", "score": 0.9}], "모르는 종류"),
    ([{"start": 0, "end": 2, "label": "NAME", "score": 1.5}], "score"),
    ([{"start": 0, "end": 2, "label": "NAME", "score": float("nan")}], "score"),
    ([{"start": 0, "end": 2, "label": "NAME"}], "score"),
    ([{"start": 0, "end": 3, "label": "NAME", "score": 0.9}, {"start": 2, "end": 4, "label": "NAME", "score": 0.9}],
     "겹치거나"),
    ([{"start": 3, "end": 4, "label": "NAME", "score": 0.9}, {"start": 0, "end": 2, "label": "NAME", "score": 0.9}],
     "겹치거나"),
    (["x"], "개체"),
    ({"start": 0}, "results"),
])
def test_parse_entities_rejects_bad_spans(items, message):
    with pytest.raises(NerError, match=message):
        parse_entities(items, 5)


def test_parse_entities_accepts_adjacent_spans():
    items = [{"start": 0, "end": 2, "label": "NAME", "score": 1}, {"start": 2, "end": 5, "label": "DATE", "score": 0}]
    assert parse_entities(items, 5) == (Entity("NAME", 0, 2, 1.0), Entity("DATE", 2, 5, 0.0))


def test_backends():
    assert NER_BACKENDS == ("server", "none")
    assert make_ner_client("none") is None
    with pytest.raises(NerError, match="알 수 없는"):
        make_ner_client("cloud")
    with pytest.raises(NerError, match="NER_BASE_URL"):
        make_ner_client("server", env={})


# --- 정책 칸·가림 표시 ---

def test_every_label_has_a_slot_and_mask_text():
    for label in NER_LABELS:
        slot = policy_slot(label, "010-1234-5678")
        if label in IGNORED_LABELS:
            assert slot is None and label not in MASK_TEXT
        else:
            assert slot in (*ENTITY_GROUPS, "mobile", "phone", "email", "secret")
            assert MASK_TEXT[label].startswith("[") and MASK_TEXT[label].endswith("]")


@pytest.mark.parametrize("label, value, slot", [
    ("NAME", "홍길동", "name"), ("PROFESSOR", "김교수", "name"), ("SCHOOL", "가온대학교", "org"),
    ("HOSPITAL", "가나다내과의원", "org"), ("AGE", "30대", "plain"), ("YEAR", "2016년", "plain"),
    ("IP_ADDRESS", "10.0.0.1", "host"), ("PHONE", "010-1234-5678", "mobile"), ("PHONE", "+82 10 1234 5678", "mobile"),
    ("PHONE", "02-123-4567", "phone"), ("PHONE", "공일공", "phone"), ("EMAIL", "a@b.kr", "email"),
    ("PASSWORD", "hunter2", "secret"), ("DB_URL", "postgres://u:p@h/db", "secret"), ("PRIVATE_KEY", "MIIE", None),
])
def test_policy_slot(label, value, slot):
    assert policy_slot(label, value) == slot


def test_outside_removes_regex_ranges_and_keeps_pieces():
    text = "홍길동 010-1234-5678 서울시 강남구"
    #       0   4            17
    entities = [Entity("NAME", 0, 3, 0.9), Entity("PHONE", 4, 17, 0.9), Entity("ADDRESS", 0, len(text), 0.5)]
    kept = outside(entities, [(4, 17)], text)
    assert [(e.label, text[e.start:e.end]) for e in kept] == [
        ("NAME", "홍길동"), ("ADDRESS", "홍길동"), ("ADDRESS", "서울시 강남구")]
    assert outside([Entity("NAME", 3, 5, 0.9)], [(0, 4)], "가나 다-") == []  # 남은 조각이 기호뿐이면 버린다


# --- 정규식과 함께 바꾸기 (gate.detectors.mask replacements) ---

def test_mask_with_replacements_keeps_regex_stars_and_uses_kind_names():
    text = "홍길동 010-1234-5678 가온대학교"
    result = mask(text, replacements=[(0, 3, "[이름]"), (18, 23, "[학교]")])
    assert result.text == "[이름] ***-****-**** [학교]"
    assert [f.kind for f in result.findings] == ["mobile"]
    assert mask(text, replacements=[(18, 23, "[학교]")]).text == "홍길동 ***-****-**** [학교]"
    assert mask("홍길동", replacements=[(0, 3, "[이름]")]).findings == ()


@pytest.mark.parametrize("replacements", [
    [(0, 5, "[이름]")],              # 정규식 범위와 겹침
    [(0, 2, "a"), (1, 3, "b")],      # 서로 겹침
    [(3, 2, "a")], [(0, 99, "a")], [(-1, 2, "a")], [(True, 2, "a")], [(0, 2, None)],
])
def test_mask_rejects_bad_replacements(replacements):
    with pytest.raises(ValueError):
        mask("010-1234-5678 홍길동", replacements=replacements)


def test_mask_bytes_replacements_keep_non_utf8_bytes():
    data = b"\xff" + "홍길동".encode() + b"\x80"
    out, found = mask_bytes(data, replacements=[(1, 4, "[이름]")])
    assert out == b"\xff" + "[이름]".encode() + b"\x80" and found == ()
