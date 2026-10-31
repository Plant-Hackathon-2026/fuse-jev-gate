"""nerserve: HTTP 계약을 가짜 토큰 예측으로 시험한다. torch·실제 모델은 쓰지 않는다. 이름·기관은 지어낸 예시다."""

import http.client
import json
import os
import random
import re
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from nerserve import __main__ as ner_main
from nerserve import model as ner_model
from nerserve.server import MAX_BODY_BYTES, MAX_TEXTS, NerService, ServiceInfo, check_loopback, make_server

ROOT = Path(__file__).resolve().parent.parent
MODEL_ID = "ner:0123456789abcdef"
KINDS = ("NAME", "COUNSELOR", "SCHOOL", "DEPARTMENT", "PLACE")
WORDS = {"홍길동": "NAME", "김철수": "COUNSELOR", "가온대학교": "SCHOOL", "체육교육과": "DEPARTMENT", "안동": "PLACE"}
SECRET = "비밀표식성춘향"  # 오류 본문·로그에 나오면 안 되는 글자


def fake_tokens(texts, score=0.9):
    """WORDS에 있는 낱말을 글자 단위 B/I 토큰으로 표시하고, 문서를 겹치는 두 창으로 나눈다(창 경계에서 개체가 잘린다)."""
    out = []
    for text in texts:
        tokens = [(i, i + 1, "B-NAME", 0.01) for i in range(len(text))]
        for word, kind in WORDS.items():
            for m in re.finditer(re.escape(word), text):
                for k in range(m.start(), m.end()):
                    tokens[k] = (k, k + 1, ("B-" if k == m.start() else "I-") + kind, score)
        half = len(tokens) // 2
        out.append([[(0, 0, "B-NAME", 0.99)] + tokens[:half + 3], tokens[max(0, half - 3):]])
    return out


class Running:
    def __init__(self, predict_tokens):
        self.service = NerService(predict_tokens, ServiceInfo(MODEL_ID, "cpu", KINDS))
        self.httpd = make_server("127.0.0.1", 0, self.service)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            conn.request(method, path, body=body, headers=headers or {})
            resp = conn.getresponse()
            return resp.status, json.loads(resp.read())
        finally:
            conn.close()

    def ner(self, payload):
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        return self.request("POST", "/v1/ner", body, {"Content-Type": "application/json"})

    def raw(self, head: bytes) -> tuple[int, dict]:
        """헤더만 직접 보낸다(Content-Length 없음·거대 등)."""
        with socket.create_connection(("127.0.0.1", self.port), timeout=10) as sock:
            sock.sendall(head)
            data = b""
            while chunk := sock.recv(65536):
                data += chunk
        status = int(data.split(b" ", 2)[1])
        return status, json.loads(data.split(b"\r\n\r\n", 1)[1])

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def serve():
    running = []

    def start(predict_tokens=fake_tokens):
        r = Running(predict_tokens)
        running.append(r)
        return r

    yield start
    for r in running:
        r.close()


def surface(text, result):
    return [(e["label"], text[e["start"]:e["end"]]) for e in result]


def assert_contract(texts, results):
    assert len(results) == len(texts)
    for text, ents in zip(texts, results):
        prev_end = 0
        for e in ents:
            assert set(e) == {"start", "end", "label", "score"}
            assert prev_end <= e["start"] < e["end"] <= len(text)
            assert 0.0 <= e["score"] <= 1.0
            assert not e["label"].startswith(("B-", "I-"))
            prev_end = e["end"]


# --- 정상 ---

def test_health(serve):
    status, body = serve().request("GET", "/health")
    assert status == 200
    assert body == {"status": "ok", "model": MODEL_ID, "device": "cpu", "labels": list(KINDS)}


def test_ner_returns_refined_entities_per_text(serve):
    seen = []

    def recording(texts):
        seen.append(list(texts))
        return fake_tokens(texts)

    texts = ["저는 홍길동입니다. 가온대학교 체육교육과에서 상담사 김철수와 만났다.", "", "아무 개체도 없는 문장"]
    status, body = serve(recording).ner({"texts": texts})
    assert status == 200
    assert body["model"] == MODEL_ID
    assert_contract(texts, body["results"])
    assert surface(texts[0], body["results"][0]) == [
        ("NAME", "홍길동"), ("SCHOOL", "가온대학교"), ("DEPARTMENT", "체육교육과"), ("COUNSELOR", "김철수")]
    assert body["results"][0][0]["score"] == pytest.approx(0.9)
    assert body["results"][1:] == [[], []]
    assert seen == [[texts[0], texts[2]]]  # 빈 문자열은 모델에 보내지 않는다


def test_empty_texts_list_and_all_empty_strings(serve):
    calls = []
    s = serve(lambda texts: calls.append(texts) or fake_tokens(texts))
    assert s.ner({"texts": []}) == (200, {"model": MODEL_ID, "results": []})
    assert s.ner({"texts": ["", ""]}) == (200, {"model": MODEL_ID, "results": [[], []]})
    assert calls == []


def test_threshold_is_applied(serve):
    s = serve(lambda texts: fake_tokens(texts, score=0.6))
    text = "홍길동 왔다"
    assert surface(text, s.ner({"texts": [text]})[1]["results"][0]) == [("NAME", "홍길동")]          # 기본 0.5
    assert s.ner({"texts": [text], "threshold": 0.7})[1]["results"] == [[]]
    assert surface(text, s.ner({"texts": [text], "threshold": 0.55})[1]["results"][0]) == [("NAME", "홍길동")]


def test_emoji_offsets_are_code_points(serve):
    text = "👩‍💻😀 홍길동😀은 안동에서 왔다"
    status, body = serve().ner({"texts": [text]})
    assert status == 200
    assert surface(text, body["results"][0]) == [("NAME", "홍길동"), ("PLACE", "안동")]


def test_lone_surrogate_is_replaced_keeping_offsets(serve):
    seen = []

    def recording(texts):
        seen.extend(texts)
        return fake_tokens(texts)

    body = b'{"texts": ["\\ud800 \\ud83d\\ude00 \xed\x99\x8d\xea\xb8\xb8\xeb\x8f\x99"]}'  # 짝 없는 대리 문자 + 이모지 + 홍길동
    status, data = serve(recording).ner(body)
    assert status == 200
    assert seen == ["� 😀 홍길동"]
    assert data["results"] == [[{"start": 4, "end": 7, "label": "NAME", "score": 0.9}]]


def test_messy_predictions_still_give_disjoint_in_range_results(serve):
    """창이 겹치고 offset이 문서 밖으로 나가도 응답은 계약(start 순, 겹치지 않음, 범위 안)을 지킨다."""
    rng = random.Random(7)
    labels = [f"{p}-{k}" for k in KINDS for p in "BI"]

    def messy(texts):
        out = []
        for text in texts:
            windows = []
            for _ in range(3):
                toks = sorted((a, a + rng.randint(-1, 5), rng.choice(labels), rng.random())
                              for a in (rng.randint(-3, len(text) + 3) for _ in range(40)))
                windows.append(toks)
            out.append(windows)
        return out

    texts = ["".join(rng.choice("홍길동가온 은는에서😀ab1.\n") for _ in range(rng.randint(0, 80))) for _ in range(MAX_TEXTS)]
    status, body = serve(messy).ner({"texts": texts, "threshold": 0.3})
    assert status == 200
    assert_contract(texts, body["results"])


# --- 잘못된 요청 ---

@pytest.mark.parametrize("body", [
    b"not json",
    b"\xff\xfe{}",
    b"[]",
    b"{}",
    b'{"texts": "' + SECRET.encode() + b'"}',
    b'{"texts": ["' + SECRET.encode() + b'", 3]}',
    b'{"texts": [null]}',
    b'{"texts": ["a"], "threshold": 0}',
    b'{"texts": ["a"], "threshold": 1}',
    b'{"texts": ["a"], "threshold": -0.1}',
    b'{"texts": ["a"], "threshold": 1.5}',
    b'{"texts": ["a"], "threshold": "0.5"}',
    b'{"texts": ["a"], "threshold": true}',
    b'{"texts": ["a"], "threshold": null}',
    b'{"texts": ["a"], "threshold": NaN}',
    b'{"texts": ["' + SECRET.encode() + b'"',
])
def test_bad_request_is_400_without_original_text(serve, body):
    status, data = serve().ner(body)
    assert status == 400
    assert set(data) == {"error"}
    assert SECRET not in json.dumps(data, ensure_ascii=False)


def test_too_many_texts_is_413(serve):
    status, data = serve().ner({"texts": [SECRET] * (MAX_TEXTS + 1)})
    assert status == 413
    assert set(data) == {"error"} and SECRET not in data["error"]


def test_too_large_body_is_413_without_reading_it(serve):
    head = (f"POST /v1/ner HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Type: application/json\r\n"
            f"Content-Length: {MAX_BODY_BYTES + 1}\r\n\r\n").encode()
    status, data = serve().raw(head)
    assert status == 413 and set(data) == {"error"}


@pytest.mark.parametrize("length_header", [b"", b"Content-Length: abc\r\n", b"Transfer-Encoding: chunked\r\n"])
def test_missing_or_bad_content_length_is_400(serve, length_header):
    head = b"POST /v1/ner HTTP/1.1\r\nHost: 127.0.0.1\r\n" + length_header + b"\r\n"
    status, data = serve().raw(head)
    assert status == 400 and set(data) == {"error"}


def test_unknown_path_and_method(serve):
    s = serve()
    assert s.request("GET", "/nope")[0] == 404
    assert s.request("POST", "/health")[0] == 404  # 본문을 읽지 않고 닫으므로 본문 없이 보낸다
    assert s.request("GET", "/v1/ner")[0] == 405
    assert s.request("PUT", "/v1/ner")[0] == 405


# --- 내부 오류·로그 ---

def test_predictor_failure_is_500_without_text(serve, caplog):
    def broken(texts):
        raise RuntimeError(f"토크나이저가 {texts[0]}에서 멈춤")

    caplog.set_level("INFO", logger="nerserve")
    status, data = serve(broken).ner({"texts": [SECRET]})
    assert (status, data) == (500, {"error": "내부 오류"})
    assert SECRET not in caplog.text
    assert "RuntimeError" in caplog.text


def test_wrong_number_of_predictions_is_500(serve):
    status, data = serve(lambda texts: []).ner({"texts": ["홍길동"]})
    assert (status, data) == (500, {"error": "내부 오류"})


def test_logs_have_counts_but_no_text_or_entities(serve, caplog):
    caplog.set_level("INFO", logger="nerserve")
    s = serve()
    text = f"{SECRET} 홍길동 가온대학교"
    assert s.ner({"texts": [text, "안동"]})[0] == 200
    assert s.ner(b"{bad " + SECRET.encode())[0] == 400
    assert "texts=2" in caplog.text and f"chars={len(text) + 2}" in caplog.text
    for word in (SECRET, "홍길동", "가온대학교", "안동"):
        assert word not in caplog.text


def test_model_calls_are_serialized(serve):
    active, peak = 0, 0
    guard = threading.Lock()

    def slow(texts):
        nonlocal active, peak
        with guard:
            active += 1
            peak = max(peak, active)
        time.sleep(0.03)
        with guard:
            active -= 1
        return fake_tokens(texts)

    s = serve(slow)
    statuses = []
    threads = [threading.Thread(target=lambda: statuses.append(s.ner({"texts": ["홍길동"]})[0])) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert statuses == [200] * 6
    assert peak == 1


# --- 바인드 주소 ---

@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.0.10", "::", "localhost", "example.com", ""])
def test_non_loopback_host_is_rejected(host):
    with pytest.raises(ValueError):
        make_server(host, 0, NerService(fake_tokens, ServiceInfo(MODEL_ID, "cpu", KINDS)))


@pytest.mark.parametrize("host", ["127.0.0.1", "127.0.0.2", "::1"])
def test_loopback_hosts_are_accepted(host):
    assert check_loopback(host).is_loopback


def test_ipv6_loopback_binds_if_available():
    try:
        httpd = make_server("::1", 0, NerService(fake_tokens, ServiceInfo(MODEL_ID, "cpu", KINDS)))
    except OSError:
        pytest.skip("IPv6 루프백을 쓸 수 없는 환경")
    try:
        assert httpd.server_address[0] == "::1"
    finally:
        httpd.server_close()


def test_main_refuses_non_loopback_before_loading_model(monkeypatch, tmp_path):
    def no_load(*args, **kwargs):
        raise AssertionError("모델을 읽으면 안 된다")

    monkeypatch.setattr(ner_model, "load", no_load)
    assert ner_main.main(["--model-dir", str(tmp_path), "--host", "0.0.0.0"]) == 2


# --- 체크포인트 정보 (torch 없음) ---

def make_checkpoint(path: Path, id2label: dict) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    (path / "config.json").write_text(json.dumps({"id2label": id2label}), encoding="utf-8")
    (path / "tokenizer.json").write_text("{}", encoding="utf-8")
    (path / "model.safetensors").write_bytes(b"\0" * 16)
    return path


def test_read_labels_and_model_id(tmp_path):
    id2label = {"0": "O", "1": "B-NAME", "2": "I-NAME", "3": "B-SCHOOL", "4": "I-SCHOOL"}
    ckpt = make_checkpoint(tmp_path / "ner", id2label)
    assert ner_model.read_labels(ckpt) == ["NAME", "SCHOOL"]

    first = ner_model.model_id(ckpt)
    assert re.fullmatch(r"ner:[0-9a-f]{16}", first)
    assert ner_model.model_id(ckpt) == first  # 바뀐 것이 없으면 같다

    (ckpt / "model.safetensors").write_bytes(b"\0" * 17)  # 가중치 크기
    second = ner_model.model_id(ckpt)
    assert second != first

    st = (ckpt / "tokenizer.json").stat()
    os.utime(ckpt / "tokenizer.json", ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))  # 토크나이저 수정 시각
    third = ner_model.model_id(ckpt)
    assert third != second

    id2label["5"] = "B-PLACE"
    (ckpt / "config.json").write_text(json.dumps({"id2label": id2label}), encoding="utf-8")  # config 내용
    assert ner_model.model_id(ckpt) not in (first, second, third)


def test_read_labels_rejects_missing_id2label(tmp_path):
    (tmp_path / "config.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError):
        ner_model.read_labels(tmp_path)


def test_http_side_imports_without_torch():
    code = ("import sys, nerserve.server, nerserve.model, nerserve.__main__, gate.ner_spans; "
            "print(sorted(m for m in ('torch', 'transformers') if m in sys.modules))")
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "[]"
