"""로컬 NER 서버.

팀원 NER 체크포인트(xlm-roberta-large, 32종 BIO)를 127.0.0.1에 띄워 게이트 데몬이 HTTP로 개체 위치를 묻게 한다.
데몬에는 torch를 넣지 않으므로 모델은 이 별도 프로세스에서만 돈다.

- server: HTTP 처리부(표준 라이브러리 http.server). torch 없이 import되고, 가짜 예측 함수로 시험한다.
- model: 체크포인트 읽기·모델 식별값·장치 고르기. torch·transformers는 load 함수 안에서만 import한다.
- 범위 다듬기는 gate.ner_spans(표준 라이브러리만)에 있다.

실행 (저장소 루트에서, torch·transformers가 있는 파이썬으로):
    ../laya-serve/.venv/bin/python -m nerserve --model-dir ../models/ner [--host 127.0.0.1] [--port 8001] [--device auto]
"""
