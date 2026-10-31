"""NER 서버 HTTP 처리부 (표준 라이브러리만, torch 없음).

계약 (plans/ner-parallel.md "공통 계약")
- GET /health → {"status": "ok", "model", "device", "labels"}
- POST /v1/ner {"texts": [...], "threshold": 0.5} → {"model", "results": [[{start, end, label, score}, ...], ...]}
  범위는 파이썬 str 인덱스(코드 포인트), end 미포함, start 순, 서로 겹치지 않는다. 빈 문자열은 빈 목록.
- 잘못된 요청 400, 너무 크면 413(본문 32 MiB 초과, texts 64개 초과), 내부 오류 500. 오류 본문은 {"error": "..."}이고
  고정 문구만 쓴다(요청 원문·예외 메시지를 넣지 않는다).
- 로그에는 요청 수·글자 수·걸린 시간만 남긴다. 원문·개체 글자는 남기지 않는다.
- 루프백 주소(127.0.0.0/8, ::1)에만 바인드한다. 다른 주소를 주면 시작하지 않는다.

모델 호출은 잠금 하나로 직렬화한다(GPU·MPS 메모리를 요청마다 따로 잡지 않게).
"""

import ipaddress
import json
import logging
import math
import socket
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from gate.ner_spans import Token, entities_from_windows, is_valid

log = logging.getLogger("nerserve")

MAX_BODY_BYTES = 32 * 1024 * 1024
MAX_TEXTS = 64
DEFAULT_THRESHOLD = 0.5
READ_TIMEOUT_SECONDS = 30.0

# 문서들 → 문서마다 창 목록 → 창마다 토큰 예측. 진짜 구현은 nerserve.model, 테스트는 가짜를 넣는다.
TokenPredictor = Callable[[list[str]], list[list[Sequence[Token]]]]


class RequestError(Exception):
    """클라이언트에 그대로 돌려줄 오류. message는 고정 문구여야 한다(원문을 넣지 않는다)."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


@dataclass(frozen=True)
class ServiceInfo:
    model_id: str
    device: str
    labels: tuple[str, ...]


class NerService:
    """요청 검사 → (잠금 안에서) 토큰 예측 → 범위 다듬기. HTTP와 떨어져 있어 직접 시험할 수도 있다."""

    def __init__(self, predict_tokens: TokenPredictor, info: ServiceInfo):
        self.predict_tokens = predict_tokens
        self.info = info
        self._lock = threading.Lock()

    def health(self) -> dict:
        return {"status": "ok", "model": self.info.model_id, "device": self.info.device,
                "labels": list(self.info.labels)}

    @staticmethod
    def parse_request(body: bytes) -> tuple[list[str], float]:
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise RequestError(400, "본문이 UTF-8 JSON이 아니다") from None
        if not isinstance(payload, dict):
            raise RequestError(400, "본문은 JSON 객체여야 한다")
        texts = payload.get("texts")
        if not isinstance(texts, list):
            raise RequestError(400, "texts는 문자열 목록이어야 한다")
        if len(texts) > MAX_TEXTS:
            raise RequestError(413, f"texts는 {MAX_TEXTS}개까지다")
        if not all(isinstance(t, str) for t in texts):
            raise RequestError(400, "texts는 문자열 목록이어야 한다")
        threshold = payload.get("threshold", DEFAULT_THRESHOLD)
        if (isinstance(threshold, bool) or not isinstance(threshold, (int, float))
                or not math.isfinite(threshold) or not 0.0 < threshold < 1.0):
            raise RequestError(400, "threshold는 0 초과 1 미만의 수여야 한다")
        # JSON은 짝 없는 대리 문자(\ud800 등)를 허용하지만 토크나이저는 못 받는다. 같은 길이의 U+FFFD로 바꿔 범위를 지킨다.
        texts = [_replace_surrogates(t) for t in texts]
        return texts, float(threshold)

    def recognize(self, texts: list[str], threshold: float) -> list[list[dict]]:
        """문서마다 개체 목록. 빈 문자열은 모델에 보내지 않는다."""
        todo = [i for i, t in enumerate(texts) if t]
        windows_per_text: list[list[Sequence[Token]]] = []
        if todo:
            with self._lock:
                windows_per_text = self.predict_tokens([texts[i] for i in todo])
            if len(windows_per_text) != len(todo):
                raise RuntimeError("예측 결과 수가 요청과 다르다")
        results: list[list[dict]] = [[] for _ in texts]
        for i, windows in zip(todo, windows_per_text):
            entities = entities_from_windows(texts[i], windows, threshold)
            if not is_valid(texts[i], entities):  # 다듬기가 보장하지만 계약을 깨는 응답은 내보내지 않는다
                raise RuntimeError("개체 범위가 계약을 어긴다")
            results[i] = [e.as_dict() for e in entities]
        return results


def _replace_surrogates(text: str) -> str:
    if not any("\ud800" <= ch <= "\udfff" for ch in text):
        return text
    return "".join("�" if "\ud800" <= ch <= "\udfff" else ch for ch in text)


def check_loopback(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    """루프백 IP 리터럴만 받는다. 이름(localhost 등)은 해석 결과가 바뀔 수 있어 받지 않는다."""
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        raise ValueError("호스트는 루프백 IP 주소(127.0.0.1 또는 ::1)여야 한다") from None
    if not addr.is_loopback:
        raise ValueError("루프백이 아닌 주소에는 바인드하지 않는다(127.0.0.1 또는 ::1)")
    return addr


class _Handler(BaseHTTPRequestHandler):
    server: "NerHTTPServer"
    timeout = READ_TIMEOUT_SECONDS  # 본문을 보내다 멈춘 연결이 스레드를 붙잡지 않게
    server_version = "nerserve"
    sys_version = ""

    def log_message(self, format, *args):  # noqa: A002 - 기본 로그(요청 줄)는 끄고 아래 _log만 쓴다
        pass

    def _send(self, status: int, payload: dict) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _log(self, route: str, status: int, started: float, texts: int = 0, chars: int = 0) -> None:
        ms = (time.perf_counter() - started) * 1000
        log.info("%s %s %d texts=%d chars=%d ms=%.1f", self.command, route, status, texts, chars, ms)

    def do_GET(self):
        started = time.perf_counter()
        if self.path == "/health":
            self._send(200, self.server.service.health())
            self._log("/health", 200, started)
        elif self.path == "/v1/ner":
            self._send(405, {"error": "POST만 받는다"})
            self._log("/v1/ner", 405, started)
        else:
            self._send(404, {"error": "없는 경로"})
            self._log("-", 404, started)

    def do_POST(self):
        started = time.perf_counter()
        if self.path != "/v1/ner":
            self.close_connection = True
            self._send(404, {"error": "없는 경로"})
            self._log("-", 404, started)
            return
        texts: list[str] = []
        try:
            body = self._read_body()
            texts, threshold = NerService.parse_request(body)
            results = self.server.service.recognize(texts, threshold)
        except RequestError as exc:
            self.close_connection = True
            self._send(exc.status, {"error": exc.message})
            self._log("/v1/ner", exc.status, started, len(texts), sum(map(len, texts)))
            return
        except Exception as exc:  # 예외 메시지에 원문이 섞일 수 있어 종류 이름만 남긴다
            log.error("예측 실패: %s", type(exc).__name__)
            self.close_connection = True
            self._send(500, {"error": "내부 오류"})
            self._log("/v1/ner", 500, started, len(texts), sum(map(len, texts)))
            return
        self._send(200, {"model": self.server.service.info.model_id, "results": results})
        self._log("/v1/ner", 200, started, len(texts), sum(map(len, texts)))

    def do_PUT(self):
        self._reject_method()

    def do_DELETE(self):
        self._reject_method()

    def do_PATCH(self):
        self._reject_method()

    def _reject_method(self):
        started = time.perf_counter()
        self.close_connection = True
        self._send(405, {"error": "지원하지 않는 메서드"})
        self._log("-", 405, started)

    def _read_body(self) -> bytes:
        if self.headers.get("Transfer-Encoding"):
            raise RequestError(400, "Content-Length가 있는 본문만 받는다")
        raw = self.headers.get("Content-Length")
        if raw is None or not raw.strip().isdigit():
            raise RequestError(400, "Content-Length가 없거나 잘못됐다")
        length = int(raw)
        if length > MAX_BODY_BYTES:
            raise RequestError(413, "본문은 32 MiB까지다")
        try:
            body = self.rfile.read(length)
        except (TimeoutError, OSError):
            raise RequestError(400, "본문을 다 받지 못했다") from None
        if len(body) != length:
            raise RequestError(400, "본문을 다 받지 못했다")
        return body


class NerHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], service: NerService):
        self.service = service
        super().__init__(address, _Handler)


def make_server(host: str, port: int, service: NerService) -> NerHTTPServer:
    """루프백 주소에만 바인드한 서버를 만든다(아직 serve_forever 전). 루프백이 아니면 ValueError."""
    addr = check_loopback(host)
    if addr.version == 6:
        server_cls = type("NerHTTPServer6", (NerHTTPServer,), {"address_family": socket.AF_INET6})
    else:
        server_cls = NerHTTPServer
    server = server_cls((str(addr), port), service)
    bound = ipaddress.ip_address(server.server_address[0])
    if not bound.is_loopback:  # 만일을 위한 재확인
        server.server_close()
        raise ValueError("루프백이 아닌 주소에 바인드됐다")
    return server
