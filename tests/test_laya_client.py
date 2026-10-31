"""gate.jev_client.LayaJevClient: 가짜 laya-serve로 시험한다. 실제 모델은 부르지 않는다. 모든 값은 가짜 예시다."""

import json
import math
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from gate.jev_client import (
    LAYA_DEFAULT_MODEL,
    LAYA_DEFAULT_QUESTIONS,
    LAYA_MAX_BATCH,
    FailClosedJevClient,
    JevError,
    LayaJevClient,
    RequestContext,
    cache_tag,
    make_client,
    split_windows,
)

CTX = RequestContext("/usr/local/bin/claude", 4321, "/상담/비밀-경로.txt", "요약용 목적 문장")


class FakeLaya:
    """laya-serve의 POST /v1/systemone/batch 흉내. score(조각) → 민감 확률. reply를 주면 그 응답을 그대로 돌려준다."""

    def __init__(self, score=lambda state: 0.01):
        self.score = score
        self.reply: tuple[int, bytes, dict] | None = None
        self.truncated = False
        self.delay = 0.0
        self.requests: list[tuple[str, dict, dict]] = []  # (경로, 헤더, 본문)
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                body = json.loads(raw)
                fake.requests.append((self.path, dict(self.headers), body))
                if fake.delay:
                    time.sleep(fake.delay)
                if fake.reply is not None:
                    status, data, headers = fake.reply
                else:
                    qid = next(iter(body["questions"]))
                    results = [{"model": "laya-rl-agent", "answers": {qid: {"type": "noul", "noul": fake.score(s)}},
                                "usage": {"truncated": fake.truncated}} for s in body["states"]]
                    status, data, headers = 200, json.dumps({"results": results}).encode(), {}
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                for name, value in headers.items():
                    self.send_header(name, value)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        # 끌 때 기다리는 시간을 줄인다(기본 0.5초면 테스트마다 0.5초씩 늘어난다)
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def fake():
    server = FakeLaya()
    yield server
    server.close()


def client(fake, **kwargs):
    return LayaJevClient(base_url=fake.url, env={}, **kwargs)


def write_model_dir(path, question="민감한 정보가 들어 있는가?", qid="q1", weights=b"weights"):
    path.mkdir(exist_ok=True)
    (path / "questions.json").write_text(
        json.dumps({qid: {"type": "noul", "instructions": question}}, ensure_ascii=False), encoding="utf-8")
    (path / "model.safetensors").write_bytes(weights)
    return path


# --- 설정 ---

def test_requires_base_url():
    with pytest.raises(JevError, match="JEV_BASE_URL"):
        LayaJevClient(env={})
    with pytest.raises(JevError, match="JEV_BASE_URL"):
        make_client("laya", env={})


@pytest.mark.parametrize("url", ["http://127.0.0.1:8000", "http://localhost:8000/", "http://[::1]:8000",
                                 "https://127.0.0.1:8443/laya/", "http://127.0.0.2:9000"])
def test_accepts_loopback_urls(url):
    LayaJevClient(base_url=url, env={})


@pytest.mark.parametrize("url", ["http://example.com:8000", "http://10.0.0.5:8000", "http://192.168.0.2",
                                 "ftp://127.0.0.1", "http://user:pw@127.0.0.1:8000", "http://127.0.0.1:8000/?x=1",
                                 "127.0.0.1:8000", "http://127.0.0.1:99999", "http://localhost.example.com"])
def test_rejects_addresses_outside_this_computer(url):
    with pytest.raises(JevError) as info:
        LayaJevClient(base_url=url, env={})
    assert "example" not in str(info.value) and "10.0.0.5" not in str(info.value)


def test_reads_settings_from_env(fake):
    c = LayaJevClient(env={"JEV_BASE_URL": fake.url, "JEV_MODEL": "my-laya", "JEV_API_KEY": "not-a-real-key"})
    c.judge("본문", CTX)
    path, headers, body = fake.requests[0]
    assert path == "/v1/systemone/batch"
    assert body["model"] == "my-laya"
    assert headers["Authorization"] == "Bearer not-a-real-key"


def test_request_shape_and_context_is_not_sent(fake):
    client(fake).judge("회의는 화요일입니다.", CTX)
    _, headers, body = fake.requests[0]
    assert body == {"model": LAYA_DEFAULT_MODEL, "states": ["회의는 화요일입니다."], "questions": LAYA_DEFAULT_QUESTIONS}
    assert "Authorization" not in headers
    raw = json.dumps(body, ensure_ascii=False)
    assert "비밀-경로" not in raw and "요약용 목적" not in raw and "claude" not in raw


def test_repr_and_errors_hide_key_and_address(fake):
    c = client(fake, api_key="not-a-real-key")
    assert "not-a-real-key" not in repr(c) and "127.0.0.1" not in repr(c)
    fake.reply = (500, b'{"detail": "inference failed"}', {})
    with pytest.raises(JevError) as info:
        c.judge("본문", CTX)
    message = str(info.value)
    assert "500" in message and "not-a-real-key" not in message and "127.0.0.1" not in message


# --- 답 옮기기 ---

@pytest.mark.parametrize("p, label, confidence", [
    (0.0, "safe", 1.0), (0.03, "safe", 0.97), (0.4999, "safe", 0.5001), (0.5, "sensitive", 0.5),
    (0.97, "sensitive", 0.97), (1, "sensitive", 1.0),
])
def test_probability_becomes_label_and_confidence(fake, p, label, confidence):
    fake.score = lambda state: p
    decision = client(fake).judge("본문", CTX)
    assert (decision.label, decision.confidence) == (label, pytest.approx(confidence))
    assert decision.raw == {"backend": "laya", "p_sensitive": round(p, 4), "windows": 1}


def test_empty_text_is_safe_without_request(fake):
    decision = client(fake).judge("  \n", CTX)
    assert (decision.label, decision.confidence) == ("safe", 1.0)
    assert fake.requests == []


# --- 긴 문서 ---

def test_long_text_is_split_and_highest_probability_wins(fake):
    lines = [f"{i}번째 줄은 회의 안건입니다." for i in range(200)]
    lines[150] = "민감한 줄"
    text = "\n".join(lines)
    fake.score = lambda state: 0.99 if "민감한 줄" in state else 0.02
    decision = client(fake, window_chars=20).judge(text, CTX)
    states = [s for _, _, body in fake.requests for s in body["states"]]
    assert "".join(states) == text
    assert all(len(s) <= 20 for s in states)
    assert len(states) > LAYA_MAX_BATCH
    assert len(fake.requests) == math.ceil(len(states) / LAYA_MAX_BATCH)
    assert all(len(body["states"]) <= LAYA_MAX_BATCH for _, _, body in fake.requests)
    assert decision.label == "sensitive" and decision.raw["windows"] == len(states)


@pytest.mark.parametrize("text, size", [
    ("", 5), ("짧다", 5), ("가" * 1000, 450), ("한 줄\n" * 300, 450), ("a b c " * 500, 37),
    ("x" * 451, 450), ("가나다\n" * 151, 450), ("ab\n" * 10, 1), ("x" * 449 + "\n" + "y" * 2, 450),
])
def test_split_windows_keeps_text_without_short_pieces(text, size):
    pieces = split_windows(text, size)
    assert "".join(pieces) == text
    assert all(len(p) <= size for p in pieces)
    if len(text) > size:
        assert min(len(p) for p in pieces) >= max(1, int(size * 0.3)) - 1


def test_split_windows_prefers_line_breaks():
    text = ("가" * 89 + "\n") * 10  # 줄 길이 90, 전체 900. 450에 줄 경계가 있다
    assert split_windows(text, 450) == [("가" * 89 + "\n") * 5] * 2
    text = ("가" * 99 + "\n") * 9  # 줄 길이 100, 전체 900. 줄 경계에서는 두 조각으로 못 나눈다
    pieces = split_windows(text, 450)
    assert all(p.endswith("\n") for p in pieces) and len(pieces) == 3


def test_truncated_window_is_error(fake):
    fake.truncated = True
    with pytest.raises(JevError, match="일부만"):
        client(fake).judge("본문", CTX)


# --- 실패는 모두 JevError ---

@pytest.mark.parametrize("reply", [
    (500, b'{"detail": "inference failed"}', {}),
    (422, b'{"detail": "bad question"}', {}),
    (200, b"not json", {}),
    (200, b"[]", {}),
    (200, b'{"results": []}', {}),
    (200, b'{"results": [{"answers": {}}]}', {}),
    (200, b'{"results": [{"answers": {"sensitive": {"noul": 1.5}}}]}', {}),
    (200, b'{"results": [{"answers": {"sensitive": {"noul": true}}}]}', {}),
    (200, b'{"results": [{"answers": {"sensitive": {"noul": "0.1"}}}]}', {}),
])
def test_bad_responses_are_errors(fake, reply):
    fake.reply = reply
    with pytest.raises(JevError):
        client(fake).judge("본문", CTX)
    with pytest.raises(JevError):
        make_client("laya", base_url=fake.url, env={}).judge("본문", CTX)


def test_redirect_is_not_followed(fake):
    other = FakeLaya()
    try:
        fake.reply = (307, b"", {"Location": other.url + "/v1/systemone/batch"})
        with pytest.raises(JevError, match="307"):
            client(fake).judge("본문", CTX)
        assert other.requests == []
    finally:
        other.close()


def test_proxy_settings_are_ignored(fake, monkeypatch):
    for name in ("http_proxy", "HTTP_PROXY", "all_proxy", "ALL_PROXY"):
        monkeypatch.setenv(name, "http://127.0.0.1:9")
    for name in ("no_proxy", "NO_PROXY"):
        monkeypatch.delenv(name, raising=False)
    assert client(fake).judge("본문", CTX).label == "safe"
    assert len(fake.requests) == 1


def test_connection_refused_is_error():
    server = FakeLaya()
    url = server.url
    server.close()
    with pytest.raises(JevError, match="연결하지 못했습니다"):
        LayaJevClient(base_url=url, env={}).judge("본문", CTX)


def test_own_network_timeout(fake):
    fake.delay = 1.0
    started = time.perf_counter()
    with pytest.raises(JevError, match="연결하지 못했습니다"):
        client(fake, timeout=0.2).judge("본문", CTX)
    assert time.perf_counter() - started < 0.9


# --- 모델 폴더와 캐시 식별값 ---

def test_model_dir_questions_are_used_and_reloaded(fake, tmp_path):
    folder = write_model_dir(tmp_path / "laya")
    c = client(fake, model_dir=folder)
    c.judge("본문", CTX)
    assert fake.requests[-1][2]["questions"] == {"q1": {"type": "noul", "instructions": "민감한 정보가 들어 있는가?"}}
    tag = c.cache_tag()
    assert tag == c.cache_tag() and tag.startswith("laya:")
    write_model_dir(folder, question="개인을 알아볼 수 있는 정보가 들어 있는가?", qid="q2")
    assert c.cache_tag() != tag
    c.judge("본문", CTX)
    assert fake.requests[-1][2]["questions"] == {"q2": {"type": "noul", "instructions": "개인을 알아볼 수 있는 정보가 들어 있는가?"}}


def test_cache_tag_changes_when_weights_change(fake, tmp_path):
    folder = write_model_dir(tmp_path / "laya")
    c = client(fake, model_dir=folder)
    tag = c.cache_tag()
    (folder / "model.safetensors").write_bytes(b"retrained weights")
    assert c.cache_tag() != tag


def test_cache_tag_depends_on_model_name(fake):
    assert client(fake).cache_tag() == client(fake).cache_tag()
    assert client(fake).cache_tag() != client(fake, model="other-laya").cache_tag()
    assert cache_tag(make_client("laya", base_url=fake.url, env={})) == client(fake).cache_tag()
    assert cache_tag(make_client("mock")) == ""


@pytest.mark.parametrize("case", ["missing", "no_weights", "bad_json", "choice", "two_questions", "no_instructions"])
def test_bad_model_dir_is_error(fake, tmp_path, case):
    folder = tmp_path / "laya"
    questions = {
        "no_weights": {"q": {"type": "noul", "instructions": "질문"}},
        "bad_json": None,
        "choice": {"q": {"type": "choice", "instructions": "질문", "criteria": {"a": "b"}}},
        "two_questions": {"a": {"type": "noul", "instructions": "x"}, "b": {"type": "noul", "instructions": "y"}},
        "no_instructions": {"q": {"type": "noul", "instructions": " "}},
    }
    if case != "missing":
        folder.mkdir()
        q = questions[case]
        (folder / "questions.json").write_text("{not json" if q is None else json.dumps(q), encoding="utf-8")
        if case != "no_weights":
            (folder / "model.safetensors").write_bytes(b"w")
    with pytest.raises(JevError):
        client(fake, model_dir=folder)


# --- make_client ---

def test_make_client_laya_is_fail_closed_with_same_timeout(fake):
    c = make_client("laya", timeout=1.5, base_url=fake.url, env={})
    assert isinstance(c, FailClosedJevClient) and isinstance(c.inner, LayaJevClient)
    assert c.timeout == c.inner.timeout == 1.5
    assert c.judge("본문", CTX).label == "safe"


def test_make_client_laya_reads_process_env(fake, monkeypatch):
    monkeypatch.setenv("JEV_BASE_URL", fake.url)
    monkeypatch.delenv("JEV_MODEL_DIR", raising=False)
    assert make_client("laya").judge("본문", CTX).label == "safe"


def test_fail_closed_turns_cache_tag_failure_into_jev_error(fake, tmp_path):
    folder = write_model_dir(tmp_path / "laya")
    c = make_client("laya", base_url=fake.url, model_dir=folder, env={})
    (folder / "model.safetensors").unlink()
    with pytest.raises(JevError):
        c.cache_tag()


# --- 질문이 여러 개인 체크포인트 (팀원 v3) ---

V3_QUESTIONS = {
    "has_pii": {"type": "noul", "instructions": "가려지지 않은 개인 식별 정보가 포함되어 있는가?"},
    "has_name": {"type": "noul", "instructions": "사람의 실제 이름이 포함되어 있는가?"},
    "is_pii": {"type": "noul", "instructions": "`target` 값이 개인정보인가?"},
    "category": {"type": "choice", "instructions": "`target` 값의 종류는?", "criteria": {"name": "이름"}},
}


def write_v3_dir(path, questions=V3_QUESTIONS):
    path.mkdir(exist_ok=True)
    (path / "questions.json").write_text(json.dumps(questions, ensure_ascii=False), encoding="utf-8")
    (path / "model.safetensors").write_bytes(b"v3")
    return path


def test_multi_question_checkpoint_sends_only_has_pii(fake, tmp_path):
    c = client(fake, model_dir=write_v3_dir(tmp_path / "v3"))
    c.judge("본문", CTX)
    assert fake.requests[-1][2]["questions"] == {"has_pii": V3_QUESTIONS["has_pii"]}


def test_jev_question_picks_another_yes_no_question(fake, tmp_path):
    folder = write_v3_dir(tmp_path / "v3")
    c = client(fake, model_dir=folder, question="has_name")
    c.judge("본문", CTX)
    assert fake.requests[-1][2]["questions"] == {"has_name": V3_QUESTIONS["has_name"]}
    assert c.cache_tag() != client(fake, model_dir=folder).cache_tag()  # 질문이 다르면 캐시도 따로
    env_client = LayaJevClient(base_url=fake.url, model_dir=folder, env={"JEV_QUESTION": "has_name"})
    assert env_client.cache_tag() == c.cache_tag()


@pytest.mark.parametrize("question, questions", [
    ("category", V3_QUESTIONS),                                         # 예/아니오가 아님
    ("nope", V3_QUESTIONS),                                             # 없는 질문
    (None, {k: v for k, v in V3_QUESTIONS.items() if k != "has_pii"}),  # 여럿인데 has_pii도 없음
    (None, {}),
])
def test_bad_question_choice_is_error(fake, tmp_path, question, questions):
    with pytest.raises(JevError):
        client(fake, model_dir=write_v3_dir(tmp_path / "v3", questions), question=question)


def test_jev_question_without_model_dir_must_match_default_question(fake):
    assert client(fake, question="sensitive").judge("본문", CTX).label == "safe"
    with pytest.raises(JevError, match="JEV_QUESTION"):
        client(fake, question="has_pii")
