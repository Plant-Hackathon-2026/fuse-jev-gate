"""gate.ner_client (3c, 3e): 로컬 NER 서버 클라이언트, 응답 검사, 종류·정책 칸 나누기. 서버는 가짜다. 모든 값은 가짜 예시다."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from gate.detectors import mask, mask_bytes
from gate.ner_client import (
    DEFAULT_BASE_URL,
    ENTITY_GROUPS,
    ENTITY_KINDS,
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
    split_kind,
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
    s = server(find=lambda t: spans_of(t, {"홍길동": "NAME", "가온대학교": "AFFILIATION"}))
    client = ServerNerClient(base_url=s.url, threshold=0.4)
    result = client.find(text)
    assert result.model == "ner:fake0001"
    assert [(e.label, text[e.start:e.end]) for e in result.entities] == [("NAME", "홍길동"), ("AFFILIATION", "가온대학교")]
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
        ServerNerClient(env={"NER_BASE_URL": "http://10.0.0.5:8001"})


@pytest.mark.parametrize("env", [{}, {"NER_BASE_URL": ""}, {"NER_BASE_URL": "  "}])
def test_base_url_defaults_to_this_computer_port_8001(env):
    """NER_BASE_URL이 없으면 설정 화면 상태 카드와 같은 기본 주소를 쓴다(3e, NER 기본 켜짐)."""
    assert DEFAULT_BASE_URL == "http://127.0.0.1:8001"
    assert ServerNerClient(env=env)._base == DEFAULT_BASE_URL


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
    ([{"start": 0, "end": 2, "label": "SCHOOL", "score": 0.9}], "모르는 종류"),      # 기존 32종 모델의 라벨
    ([{"start": 0, "end": 2, "label": "IP_ADDRESS", "score": 0.9}], "모르는 종류"),  # 규칙 종류는 서버가 주지 않는다
    ([{"start": 0, "end": 2, "label": "EMAIL", "score": 0.9}], "모르는 종류"),       # PLANT 종류도 서버 라벨이 아니다
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
    items = [{"start": 0, "end": 2, "label": "NAME", "score": 1}, {"start": 2, "end": 5, "label": "CONTACT", "score": 0}]
    assert parse_entities(items, 5) == (Entity("NAME", 0, 2, 1.0), Entity("CONTACT", 2, 5, 0.0))


def test_server_labels_are_the_new_six():
    assert NER_LABELS == ("NAME", "CONTACT", "ID_NUMBER", "AFFILIATION", "LOCATION", "AGE_BIRTH")


def test_old_model_labels_are_refused_by_find(server):
    """기존 32종 모델을 띄운 서버는 받지 않는다(모르는 라벨 → NerError → 차단, 2026-11-01 결정)."""
    s = server(find=lambda t: spans_of(t, {"가온대학교": "SCHOOL"}))
    with pytest.raises(NerError, match="모르는 종류"):
        ServerNerClient(base_url=s.url).find("가온대학교 학생")


def test_backends():
    assert NER_BACKENDS == ("server", "none")
    assert make_ner_client("none") is None
    with pytest.raises(NerError, match="알 수 없는"):
        make_ner_client("cloud")
    assert make_ner_client("server", env={})._base == DEFAULT_BASE_URL
    with pytest.raises(NerError, match="NER_BASE_URL"):
        make_ner_client("server", env={"NER_BASE_URL": "http://example.com"})


# --- 정책 칸·가림 표시 ---

def test_every_kind_has_a_slot_and_mask_text():
    for kind in ENTITY_KINDS:
        assert policy_slot(kind, "010-1234-5678") in (*ENTITY_GROUPS, "mobile", "phone", "email")
        assert MASK_TEXT[kind].startswith("[") and MASK_TEXT[kind].endswith("]")
    assert set(MASK_TEXT) == set(ENTITY_KINDS)


@pytest.mark.parametrize("label, value, slot", [
    ("NAME", "홍길동", "name"), ("AFFILIATION", "가온대학교", "org"), ("AFFILIATION", "가나다내과의원", "org"),
    ("ID_NUMBER", "20231234", "student_id"), ("ID_NUMBER", "M12345678", "student_id"), ("LOCATION", "안동", "place"),
    ("ADDRESS", "수원시 예시로 45", "address"), ("MESSENGER", "hong_gd", "etc"), ("BIRTH", "1990.01.01", "birth"),
    ("AGE", "30대", "plain"), ("IP_ADDRESS", "10.0.0.1", "host"), ("INTERNAL_HOST", "db.internal", "host"),
    ("PHONE", "010-1234-5678", "mobile"), ("PHONE", "+82 10 1234 5678", "mobile"), ("PHONE", "02-123-4567", "phone"),
    ("PHONE", "0212345678", "phone"), ("EMAIL", "a@b.kr", "email"),
])
def test_policy_slot(label, value, slot):
    assert policy_slot(label, value) == slot


@pytest.mark.parametrize("label", ["CONTACT", "AGE_BIRTH", "SCHOOL", "PRIVATE_KEY", "DATE"])
def test_policy_slot_refuses_unknown_kinds(label):
    """서버 라벨 그대로나 기존 모델 종류는 게이트 종류가 아니다(받는 쪽은 차단한다)."""
    with pytest.raises(NerError, match="모르는 종류"):
        policy_slot(label, "값")


@pytest.mark.parametrize("value, kind", [
    ("hong@example.com", "EMAIL"), ("메일 hong.gd@example.co.kr", "EMAIL"), ("hong@example.com으로", "EMAIL"),
    ("010-1234-5678", "PHONE"), ("+82 10 1234 5678", "PHONE"), ("(02) 123-4567", "PHONE"), ("01012345678로", "PHONE"),
    ("123-4567", "PHONE"),
    ("서울시 강남구 예시로 45, 3층", "ADDRESS"), ("공일공에 칠칠팔에 일이삼사", "ADDRESS"),  # 한글이 있으면 상세 주소로 본다
    ("hong_gildong", "MESSENGER"), ("@hong.gd", "MESSENGER"), ("kakao-0101", "MESSENGER"),
])
def test_contact_is_split_by_value(value, kind):
    text = f"연락: {value} 끝"
    start = text.index(value)
    assert split_kind("CONTACT", text, start, start + len(value)) == kind


@pytest.mark.parametrize("value, kind", [
    ("abc1234567", "PHONE"),       # 숫자 7자, 10자 중 70%
    ("abcd123456", "MESSENGER"),   # 숫자 6자
    ("abcd1234567", "MESSENGER"),  # 숫자 7자지만 11자 중 64%
    ("12 34 56 7", "PHONE"),       # 공백은 세지 않는다
])
def test_contact_phone_needs_seven_digits_and_seventy_percent(value, kind):
    assert split_kind("CONTACT", value, 0, len(value)) == kind


@pytest.mark.parametrize("value, kind", [
    ("1990년생", "BIRTH"), ("98년생", "BIRTH"), ("3월 21일", "BIRTH"), ("21일", "BIRTH"), ("오월", "BIRTH"),
    ("1990.01.01", "BIRTH"), ("98-03-21", "BIRTH"), ("1998/3/21", "BIRTH"), ("19980321", "BIRTH"), ("980321", "BIRTH"),
    ("30대 초반", "AGE"), ("36세", "AGE"), ("스물한 살", "AGE"), ("초등학교 5학년", "AGE"), ("2016년", "AGE"),
    ("18개월", "AGE"), ("1987년", "AGE"),
])
def test_age_birth_is_split_by_value(value, kind):
    text = f"환자는 {value}이다"
    start = text.index(value)
    assert split_kind("AGE_BIRTH", text, start, start + len(value)) == kind


@pytest.mark.parametrize("text, value, kind", [
    ("출생 연도: 1987년", "1987년", "BIRTH"),               # 값만 보면 연도지만 바로 앞에 '출생'
    ("생년: 1987", "1987", "BIRTH"),
    ("생일은 다음 주 화요일", "다음 주 화요일", "BIRTH"),
    ("태어난 해는 1987년", "1987년", "BIRTH"),
    ("태어난 곳은 경상북도 안동이고 지금은 26세", "26세", "AGE"),  # '태어'가 10자보다 멀다
    ("출생지는 안동이고 나이는 36세입니다", "36세", "AGE"),  # '출생'이 10자보다 멀다
    ("올해 36세", "36세", "AGE"),
])
def test_age_birth_looks_at_words_right_before(text, value, kind):
    start = text.index(value)
    assert split_kind("AGE_BIRTH", text, start, start + len(value)) == kind


def test_other_labels_are_kept_and_unknown_label_is_an_error():
    for label in ("NAME", "ID_NUMBER", "AFFILIATION", "LOCATION"):
        assert split_kind(label, "홍길동", 0, 3) == label
    with pytest.raises(NerError, match="모르는 종류"):
        split_kind("SCHOOL", "가온대학교", 0, 5)


def test_find_splits_server_labels_into_gate_kinds(server):
    text = "홍길동(30대, 20231234, 가온대학교) 010-1234-5678 hong@example.com 카톡 hong_gd 예시로 45 출생 1990년"
    words = {"홍길동": "NAME", "30대": "AGE_BIRTH", "20231234": "ID_NUMBER", "가온대학교": "AFFILIATION",
             "010-1234-5678": "CONTACT", "hong@example.com": "CONTACT", "hong_gd": "CONTACT", "예시로 45": "CONTACT",
             "1990년": "AGE_BIRTH"}
    s = server(find=lambda t: spans_of(t, words))
    result = ServerNerClient(base_url=s.url).find(text)
    assert [(e.label, text[e.start:e.end]) for e in result.entities] == [
        ("NAME", "홍길동"), ("AGE", "30대"), ("ID_NUMBER", "20231234"), ("AFFILIATION", "가온대학교"),
        ("PHONE", "010-1234-5678"), ("EMAIL", "hong@example.com"), ("MESSENGER", "hong_gd"), ("ADDRESS", "예시로 45"),
        ("BIRTH", "1990년")]


def test_outside_removes_regex_ranges_and_keeps_pieces():
    text = "홍길동 010-1234-5678 서울시 강남구"
    #       0   4            17
    entities = [Entity("NAME", 0, 3, 0.9), Entity("PHONE", 4, 17, 0.9), Entity("ADDRESS", 0, len(text), 0.5)]
    kept = outside(entities, [(4, 17)], text)
    assert [(e.label, text[e.start:e.end]) for e in kept] == [
        ("NAME", "홍길동"), ("ADDRESS", "홍길동"), ("ADDRESS", "서울시 강남구")]
    assert outside([Entity("NAME", 3, 5, 0.9)], [(0, 4)], "가나 다-") == []  # 남은 조각이 기호뿐이면 버린다


def test_outside_drops_a_lone_josa_left_after_cutting():
    """낱말 넓히기로 붙은 조사(`hong@example.com으로`의 `으로`)만 남으면 버린다. 잘리지 않은 개체는 그대로 둔다."""
    text = "hong@example.com으로 10.0.0.12로 보내 은"
    ip = text.index("10.")
    entities = [Entity("EMAIL", 0, 18, 0.9), Entity("PHONE", ip, ip + 10, 0.9),
                Entity("NAME", len(text) - 1, len(text), 0.9)]
    kept = outside(entities, [(0, 16), (ip, ip + 9)], text)
    assert [(e.label, text[e.start:e.end]) for e in kept] == [("NAME", "은")]
    kept = outside([Entity("ADDRESS", 0, 18, 0.9)], [(0, 5)], text)   # 잘리고 남은 것이 조사가 아니면 남긴다
    assert [text[e.start:e.end] for e in kept] == ["example.com으로"]


@pytest.mark.parametrize("tail", ["으로도", "까지도", "에게서", "에서도", "로는"])
def test_outside_drops_joined_josa_left_after_cutting(tail):
    """조사가 이어 붙은 조각(`...@example.com으로도`의 `으로도`)도 버린다. 재측정 상담 pii_1에서 `***[이메일]`로 나왔다."""
    text = f"메일은 gilsoon.hong@example.com{tail} 보내 드리겠습니다"
    start, end = text.index("gilsoon"), text.index(tail) + len(tail)
    assert outside([Entity("CONTACT", start, end, 0.9)], [(start, text.index(tail))], text) == []


@pytest.mark.parametrize("tail", ["홍길동", "이도윤", "으로도요"])
def test_outside_keeps_pieces_that_are_not_only_josa(tail):
    """조사 한두 개로 나눠지지 않는 조각은 남긴다(이름, 끝말이 더 붙은 것)."""
    text = f"hong@example.com{tail}"
    kept = outside([Entity("CONTACT", 0, len(text), 0.9)], [(0, 16)], text)
    assert [text[e.start:e.end] for e in kept] == [tail]


# --- 정규식과 함께 바꾸기 (gate.detectors.mask replacements) ---

def test_mask_with_replacements_keeps_regex_stars_and_uses_kind_names():
    text = "홍길동 010-1234-5678 가온대학교"
    result = mask(text, replacements=[(0, 3, "[이름]"), (18, 23, "[소속]")])
    assert result.text == "[이름] ***-****-**** [소속]"
    assert [f.kind for f in result.findings] == ["mobile"]
    assert mask(text, replacements=[(18, 23, "[소속]")]).text == "홍길동 ***-****-**** [소속]"
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
