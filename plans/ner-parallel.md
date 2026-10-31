# 3c NER 병렬 작업 지시서

설계와 사용자 결정은 `plans/implementation-plan.md` 2절 "NER (3c)" 행과 5절 Phase 3 "3c"에 있다. 이 문서는 파일을 나눠 맡기 위한 지시서다.
응답·주석·문서는 한국어로 쓴다. **커밋하지 않는다**(메인이 합쳐서 커밋한다). 맡은 파일 밖은 고치지 않는다.

## 파일 나눔

| 누가 | 파일 |
|---|---|
| A (NER 서버) | `nerserve/__init__.py`, `nerserve/__main__.py`(필요하면 `nerserve/server.py` 등 `nerserve/` 아래 파일), `gate/ner_spans.py`, `tests/test_ner_spans.py`, `tests/test_nerserve.py` |
| B (평가 정답) | `eval/must_hide.csv` |
| 메인 | `gate/ner_client.py`, `gate/detectors.py`, `gate/policy.py`, `gate/pipeline.py`, `gate/protocol.py`, `gate/policy_cli.py`, `config/policy.yaml`, `eval/run_eval.py`, `eval/README.md`, `README.md`, 그 밖의 테스트 |

## 공통 계약: NER 서버 HTTP

- 바인드는 127.0.0.1(기본) 또는 다른 루프백 주소만 받는다. 루프백이 아닌 주소를 주면 시작하지 않는다.
- `GET /health` → 200 `{"status": "ok", "model": "<모델 식별값>", "device": "mps|cuda|cpu", "labels": ["NAME", ...]}`
  - `labels`는 체크포인트 `config.json`의 `id2label`에서 `O`를 빼고 `B-`/`I-`를 뗀 종류 이름(32개, 순서는 id 순).
  - 모델 식별값: `"ner:" + sha256(config.json 바이트 + tokenizer.json 크기·수정 시각 + model.safetensors 크기·수정 시각)` 앞 16자리(hex). 체크포인트를 바꾸고 서버를 다시 띄우면 달라진다.
- `POST /v1/ner` 요청 `{"texts": ["...", ...], "threshold": 0.5}` (`threshold`는 선택, 0 초과 1 미만, 기본 0.5)
  - 응답 200 `{"model": "<모델 식별값>", "results": [[{"start": 0, "end": 3, "label": "NAME", "score": 0.97}, ...], ...]}`
  - `results[i]`는 `texts[i]`의 개체 목록. `start`·`end`는 **파이썬 str 인덱스(코드 포인트)**, end 미포함, `0 ≤ start < end ≤ len(text)`.
  - 목록은 start 순이고 **서로 겹치지 않는다**. `label`은 `B-`/`I-` 없는 종류 이름. `score`는 그 개체 토큰들의 P(개체) 평균(0~1).
  - 잘못된 요청은 400, 너무 크면 413(본문 32 MiB 초과, texts 64개 초과), 서버 내부 오류는 500. 오류 본문은 `{"error": "..."}`이고 **원문 내용을 넣지 않는다**.
  - 빈 문자열은 빈 목록이다.
- 서버 로그에도 원문·개체 글자를 남기지 않는다(요청 수, 글자 수, 걸린 시간 정도만).

## A: NER 서버 + 범위 다듬기

목표: 팀원 NER 체크포인트(`/Users/sangwoo/work/personal/hackathon/models/ner`, xlm-roberta-large, `XLMRobertaForTokenClassification`, 라벨 65개 = O + 32종 BIO)를 위 계약대로 띄운다.

- 팀원 코드 `/Users/sangwoo/work/personal/hackathon/models/app/ner_core.py`를 읽고 **방식만 따르고 코드는 복사하지 않는다**(공개 저장소. 팀원 코드·가중치는 넣지 않는다). 따를 방식:
  - 토큰화: `max_length=256`, `stride=32`, `return_overflowing_tokens=True`, `return_offsets_mapping=True`(학습 때와 같은 창).
  - 토큰마다 P(개체) = 1 − P(O). threshold 이상이면 개체 토큰, 종류는 O를 뺀 라벨 중 가장 큰 것(BIO 접두어 포함 라벨을 고른 뒤 접두어와 종류로 나눔).
  - 이어 붙이기: 같은 종류이고 `I-`면 앞 개체에 붙이고, 아니면 새 개체. 개체 점수는 토큰 P(개체) 평균.
  - 겹치는 창에서 같은 (start, end)가 여러 번 나오면 점수 높은 것 하나.
  - 다듬기: 앞뒤 공백을 뗀다. 형식이 있는 종류(API_KEY, CLOUD_KEY, TOKEN, PASSWORD, PRIVATE_KEY, DB_URL, IP_ADDRESS, INTERNAL_HOST, EMAIL, PHONE)는 공백만 다듬는다. 나머지는 한글·영문·숫자로 이어진 낱말 끝까지 넓힌 뒤, 낱말 끝의 조사 하나(긴 것부터: 에서부터, 으로부터, 에서는, 에게서, 이라고, 이라는, 한테서, 께서는, 에서, 에게, 한테, 께서, 으로, 부터, 까지, 처럼, 보다, 이랑, 이나, 하고, 은, 는, 이, 가, 을, 를, 에, 의, 로, 도, 만, 와, 과, 랑 등. 목록은 직접 정한다)를 뗀다. 조사 앞 글자가 한글·숫자이고 남는 글자가 있을 때만 뗀다.
  - 합치기: 겹치면 하나로(범위는 합집합, 종류는 점수 높은 쪽). 바로 붙어 있고 종류가 같으면 하나로.
- `gate/ner_spans.py`: 위 로직 중 **torch 없이 되는 부분**(토큰별 (offset, 라벨, P(개체)) 목록 → 개체, 다듬기, 합치기)을 표준 라이브러리만으로 둔다. 데몬 쪽 테스트 묶음에서 돌아야 하고 `gate` 패키지의 다른 모듈을 import하지 않는다.
- `nerserve/`: torch·transformers는 모델을 불러오는 함수 안에서만 import한다(HTTP 처리부는 가짜 예측 함수로 시험할 수 있게). 표준 라이브러리 `http.server`(ThreadingHTTPServer) + 모델 호출은 잠금 하나로 직렬화. 실행:
  ```
  cd fuse-jev-gate && ../laya-serve/.venv/bin/python -m nerserve --model-dir ../models/ner [--host 127.0.0.1] [--port 8001] [--device auto]
  ```
  (laya-serve의 .venv에 torch 2.15·transformers 5.22.1이 있다. 저장소 루트에서 실행하므로 `gate.ner_spans`를 import할 수 있다. 장치는 auto면 cuda → mps → cpu.) 시작할 때 오프라인으로만 읽는다(`local_files_only=True`, 필요하면 `HF_HUB_OFFLINE=1`). 아무것도 내려받지 않는다.
- 테스트(`uv run pytest tests/test_ner_spans.py tests/test_nerserve.py`, torch 없이 통과해야 한다): 이어 붙이기·조사 떼기·넓히기·형식 있는 종류·겹침 합치기·이모지·빈 문자열, HTTP 계약(정상, 400/413, 루프백 아닌 주소 거부, 오류 본문에 원문 없음, 결과가 겹치지 않고 범위 안).
- 실제 확인(필수): 진짜 모델로 서버를 띄워 `eval/labels.csv`의 36개 샘플을 보내 보고, 샘플별 개체(종류, 글자, 점수)를 scratchpad `ner_samples.jsonl`에 쓴다. 모델 로드 시간, 샘플별 지연(평균·최대), 장치를 보고한다. 끝나면 서버를 끈다(백그라운드 프로세스를 남기지 않는다).
- 끝나면 `uv run pytest` 전체가 통과하는지 확인한다(메인이 다른 파일을 고치는 중이라 다른 테스트가 깨져 있으면 그 사실만 적는다).

## B: 평가 정답 `eval/must_hide.csv`

목표: 게이트를 거친 결과에 **남으면 누출로 셀 글자**를 민감 샘플마다 적는다. NER 결과를 보지 않고 샘플 원문만 보고 정한다(정답이 모델에 맞춰지지 않게).

- 대상: `eval/labels.csv`에서 case가 `pii`·`quasi`인 24개. `none` 12개는 적지 않는다.
- 열: `path,tier,group,text,note`
  - `tier`: `identifier`(그것만으로 사람을 특정: 이름, 전화, 이메일, 주민번호, 주소(동·도로명 수준), 생년월일, 학번, 계좌, 카드) 또는 `quasi`(합치면 좁혀짐: 학교·학과·회사·병원·모임 등 고유한 소속 이름, 시·군·구 이하 지역 이름, 고유한 장소 이름).
  - `group`: name, address, birth, student_id, org, place, phone, email, rrn, card, account, secret, other 중 하나.
  - `text`: 원문에 **그대로 있는 글자**(부분 문자열 검사로 쓴다). 누출 판단은 "결과에 이 글자가 한 번이라도 남았는가"다. 그러니 너무 흔한 글자(`대학교`, `서울`만 등)는 피하고 고유한 부분을 쓴다. 예: `가온대학교`, `체육교육과`, `안동`.
  - 이름은 성명 전체를 적는다(예: `홍길동`). 문서에 성만, 또는 이름만 따로 나오면 그것도 따로 한 줄.
  - 번호(전화·이메일·주민번호 등)는 정규식이 가리는 것이지만 끝까지 확인하기 위해 적는다.
- 적지 않는 것: 기본 정책이 보여 주는 것(나이·연도·직함·과목·시간, 예: `30대`, `2016년`, `체육 교사`), 진료과·일반 명사(`정형외과`, `내과`, `중학교`), 생년월일이 아닌 일반 날짜.
- 샘플당 보통 1~8줄. 판단이 애매한 것은 `note`에 이유를 적는다.
- 끝나면 샘플별 줄 수와 애매했던 것을 보고한다.
