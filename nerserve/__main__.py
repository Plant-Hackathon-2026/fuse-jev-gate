"""NER 서버 실행: python -m nerserve --model-dir ../models/ner [--host 127.0.0.1] [--port 8001] [--device auto]

모델을 읽기 전에 주소부터 확인한다(루프백이 아니면 바로 끝낸다). 로그에는 원문을 남기지 않는다.
"""

import argparse
import logging
import signal
import sys
import threading
import time
from pathlib import Path

from nerserve import server as srv

log = logging.getLogger("nerserve")


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m nerserve", description="로컬 NER 서버 (127.0.0.1 전용)")
    parser.add_argument("--model-dir", required=True, type=Path, help="NER 체크포인트 폴더(config.json, tokenizer.json, model.safetensors)")
    parser.add_argument("--host", default="127.0.0.1", help="루프백 IP만 받는다 (기본 127.0.0.1)")
    parser.add_argument("--port", default=8001, type=int, help="기본 8001")
    parser.add_argument("--device", default="auto", choices=("auto", "cuda", "mps", "cpu"),
                        help="auto면 cuda → mps → cpu")
    parser.add_argument("--batch-size", default=16, type=int, help="한 번에 모델에 넣는 창 수 (기본 16)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    try:
        srv.check_loopback(args.host)
    except ValueError as exc:
        log.error("%s", exc)
        return 2
    if args.batch_size < 1:
        log.error("--batch-size는 1 이상이어야 한다")
        return 2

    from nerserve import model as ner_model  # torch는 load 안에서만 import한다

    try:
        info_labels = ner_model.read_labels(args.model_dir)
        info_model = ner_model.model_id(args.model_dir)
        started = time.perf_counter()
        predict_tokens, device = ner_model.load(args.model_dir, args.device, args.batch_size)
        loaded = time.perf_counter() - started
        started = time.perf_counter()
        predict_tokens(["워밍업 문장입니다."])  # 첫 요청이 커널 준비 시간을 떠안지 않게
        warmed = time.perf_counter() - started
    except (OSError, ValueError, RuntimeError) as exc:
        log.error("모델을 불러오지 못했다: %s", exc)
        return 1
    log.info("모델 %s 로드 %.1f초, 워밍업 %.2f초, 장치 %s, 종류 %d개", info_model, loaded, warmed, device, len(info_labels))

    service = srv.NerService(predict_tokens, srv.ServiceInfo(info_model, device, tuple(info_labels)))
    try:
        httpd = srv.make_server(args.host, args.port, service)
    except (OSError, ValueError) as exc:
        log.error("서버를 열지 못했다: %s", exc)
        return 1

    def stop(signum, _frame):
        log.info("신호 %d, 끝낸다", signum)
        threading.Thread(target=httpd.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    host, port = httpd.server_address[:2]
    log.info("http://%s:%d 에서 받는다 (POST /v1/ner, GET /health)", host if ":" not in host else f"[{host}]", port)
    try:
        httpd.serve_forever(poll_interval=0.2)
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
