# 3e: NER 교체 (팀원 재학습판 6종) + IP·내부 주소 규칙

_작성 2026-11-01. 근거: `eval/README.md` "NER 재학습판 조합 비교 (2026-11-01)"(추천 v3 + 새 NER 단독)._

## 1. 목표

- 게이트의 NER을 팀원 새 모델(`xlmr-pii-ner`, xlm-roberta-large, 라벨 NAME·CONTACT·ID_NUMBER·AFFILIATION·LOCATION·AGE_BIRTH)로 바꾼다. 기존 모델(32종)은 쓰지 않는다(둘 합침은 평가에서 이득이 없었다).
- 기존 모델만 찾던 IP·내부 주소는 규칙(정규식)으로 찾는다. 비밀번호가 든 URL(`postgres://u:pw@host`)의 비밀번호는 지금 정규식(`secret`)이 이미 잡는다.
- NER을 기본으로 켠다.

## 2. 결정 (2026-11-01 사용자)

| 항목 | 결정 |
|---|---|
| 모델 | 새 NER 단독. 기존 32종 모델은 받지 않는다(모르는 라벨 → NerError → 차단, 지금과 같은 계약) |
| 식별번호 | ID_NUMBER는 `student_id` 묶음에 넣고, 이름을 "식별번호(학번·사번·여권번호 등)"로 넓힌다. 키 `entities.student_id.action`은 그대로 둔다(예전 정책 파일이 그대로 읽힌다) |
| 나이·생년월일 | AGE_BIRTH를 값과 바로 앞 말로 나눈다. 생년월일(`birth`, 기본 가림): 값에 `생`, 월·일, 날짜 형식(`1990.01.01`, `98-03-21`)이 있거나 바로 앞(약 10자)에 `출생`·`생년`·`생일`·`태어`가 있을 때. 그 밖은 나이(`plain`, 기본 공개) |
| 기술 종류 | NER에서 뺀다. IP(IPv4·IPv6)와 내부 주소는 규칙으로 찾아 지금의 `host` 묶음("IP·내부 주소", 기본 가림)에 넣는다. NER을 꺼도 적용된다 |
| 찾지 않는 묶음 | **뺀다.** `date`(날짜)·`event`(사건)는 화면·`gate policy show`·문장 해석에서 뺀다. `plain`은 이름을 "나이"로 바꾼다(시간·연도·직함·과목은 새 모델이 찾지 않는다). 예전 정책 파일의 `entities.date.action`·`entities.event.action`은 파일이 깨지지 않게 읽되 효과가 없다. 새로 저장하지는 못한다. "날짜는 가려줘"류 문장은 "지금은 따로 찾지 않아요"로 답하고 저장하지 않는다 |
| NER 기본값 | **켠다**(`ner_backend: server`). NER 서버가 없으면 지금처럼 모든 내용 읽기가 차단된다(fail-closed). `NER_BASE_URL`이 없으면 `http://127.0.0.1:8001`을 쓴다(화면 상태 카드와 같은 기본값) |

## 3. 바꿀 곳

### 3.1 `gate/ner_client.py`
- `NER_LABELS`(서버가 주는 라벨) = 새 6종. `parse_entities`는 이 6종만 받는다.
- 서버 라벨 → 게이트 내부 종류: `ServerNerClient.find`가 원문을 보고 나눈다(위치는 그대로). 내부 종류마다 정책 칸과 가림 표시가 정해진다.

| 서버 라벨 | 나누는 기준 | 내부 종류 | 정책 칸 | 가림 표시 |
|---|---|---|---|---|
| NAME | | NAME | `name` | `[이름]` |
| CONTACT | 이메일 형식 | EMAIL | `numbers.email` | `[이메일]` |
| CONTACT | 숫자 7자 이상이고 숫자가 70% 이상 | PHONE | `numbers.mobile`·`numbers.phone`(지금 규칙) | `[전화번호]` |
| CONTACT | 한글이 있음(상세 주소) | ADDRESS | `address` | `[주소]` |
| CONTACT | 그 밖(메신저·SNS 아이디 등) | MESSENGER | `etc` | `[연락처]` |
| ID_NUMBER | | ID_NUMBER | `student_id` | `[식별번호]` |
| AFFILIATION | | AFFILIATION | `org` | `[소속]` |
| LOCATION | | LOCATION | `place` | `[장소]` |
| AGE_BIRTH | 위 2절 기준 | BIRTH / AGE | `birth` / `plain` | `[생년월일]` / `[나이]` |
| (규칙) | IPv4·IPv6 | IP_ADDRESS | `host` | `[IP]` |
| (규칙) | 내부 주소 | INTERNAL_HOST | `host` | `[내부 주소]` |

- `ENTITY_GROUPS` = name, address, birth, student_id, org, place, etc, host, plain(9개, 표시 순서). `RETIRED_ENTITY_GROUPS` = date, event.
- `ENTITY_NAMES`: student_id "식별번호(학번·사번·여권번호 등)", etc "기타(메신저·SNS 아이디 등)", plain "나이". 나머지는 그대로.
- 기본 주소: `NER_BASE_URL`이 없으면 `http://127.0.0.1:8001`. 이 컴퓨터 주소만 받는 검사는 그대로.

### 3.2 `gate/ner_spans.py`, `nerserve/`
- `EXACT_KINDS`·`_KEEP_ENDINGS`를 새 라벨에 맞춘다. CONTACT(상세 주소 포함)와 LOCATION은 "로"·"도"를 떼지 않는다. 형식이 정해진 종류는 없다(이메일·전화도 CONTACT 안에 섞여 있어 낱말 넓히기를 그대로 쓴다).
- nerserve 계약(라벨은 모델 config의 id2label 그대로)은 바꾸지 않는다.

### 3.3 IP·내부 주소 규칙 (새 모듈, 표준 라이브러리만)
- IPv4: 네 칸, 칸마다 0~255, 앞뒤가 숫자·점이 아닌 곳(`1.2.3.4.5`나 더 긴 숫자열의 일부는 아님). IPv6: `::`가 있거나 16진수 칸이 6개 이상(시각 `10:11:12`는 아님).
- 내부 주소: 점으로 이은 호스트 이름 중 칸 하나가 `internal`·`intra`·`intranet`·`corp`·`local`·`lan`·`localdomain`·`home.arpa` 계열인 것(예: `db.internal.example.local`, `wiki.intra.example.co.kr`, `build-07.internal`). 이메일·URL의 사용자 정보 부분은 정규식이 먼저 가린다.
- 우선순위: 정규식 > 규칙 > NER. 규칙 범위는 정규식 범위를 빼고(`outside`), NER 개체는 정규식·규칙 범위를 뺀다.

### 3.4 `gate/pipeline.py`
- 규칙은 NER이 꺼져 있어도 늘 돈다. NER이 켜져 있으면 규칙 개체와 NER 개체를 합친다(겹치지 않게).
- ② 흐름의 "NER이 켜져 있고 가린 것이 있으면 가린 결과를 Jev에 묻는다"는 그대로(NER이 꺼져 있으면 규칙으로 가렸어도 3b 흐름).

### 3.5 `gate/policy.py`, `config/policy.yaml`
- 기본 `ner_backend: server`.
- 예전 키 `entities.date.action`·`entities.event.action`: 정책 파일을 읽을 때 받고 무시한다(정책 설정에 넣지 않음). `gate policy set`·화면은 이 키를 거부한다. `describe_setting`은 "(지금은 찾지 않음)"을 붙여 보여 준다.

### 3.6 `gate/policy_text.py`
- 묶음 설명(해석기가 읽는 표)·단서 표를 9개 묶음에 맞춘다.
  - `student_id` 단서에 식별번호·사번·직원 번호·여권번호·면허번호·차량번호·회원번호를 더한다.
  - `plain` 단서는 나이·연령·몇 살만.
  - `etc` 단서에 메신저 아이디·SNS 아이디·카톡 아이디를 더한다.
- 날짜·사건·시간·연도·직함·과목 단서가 걸리면 저장하지 않고 "지금은 날짜·사건·시간·연도·직함·과목을 따로 찾지 않아요"라고 답한다(결과 `unsupported`).
- `eval/policy_sentences*.csv`에서 date·event·plain(나이 아닌 것) 정답을 `unsupported`로 바꾸고 비고에 "2026-11-01 NER 교체로 찾지 않음"을 적는다. Mock을 첫 세트·검증 세트로 다시 잰다.

### 3.7 설정 화면 (`gate/ui_api.py`, `gate/ui_static/`)
- NER 줄은 9개. `host` 줄에는 "규칙으로 찾아요 · NER을 꺼도 적용돼요"를 보여 주고 NER 꺼짐 안내를 붙이지 않는다. `plain` 줄의 "다섯 가지가 함께 바뀌어요" 안내는 뺀다.

### 3.8 문서
- `README.md` NER 절(새 모델, 라벨, 기본 켜짐, nerserve를 먼저 띄우기, 끄는 법), 빠른 시작·하네스 절(데몬을 띄울 때 NER 서버도 필요).
- `eval/README.md`(교체 뒤 다시 잰 결과), `plans/implementation-plan.md`(2절 표·5절·6절 한계), `HANDOFF.md`.
- 팀원용 평가 보고서(아티팩트)에 조합 비교와 교체 뒤 결과를 넣는다(사용자 요청).

## 4. 테스트
- 새 라벨 나누기(CONTACT 4갈래, AGE_BIRTH 앞말 포함), 모르는 라벨 거부, 기본 주소.
- 규칙: IPv4·IPv6·내부 주소 찾음과 안 찾음(버전 번호, 날짜, 시각, 일반 도메인 `example.com`, 이메일), 정규식과 겹칠 때.
- 파이프라인: NER 꺼짐 + 규칙만, NER 켜짐 + 규칙·NER 합침, 기본 정책이 NER 켜짐이고 서버가 없으면 차단.
- 정책: 기본값, 예전 키 읽기·무시·쓰기 거부, 완화 확인(NER 끄기는 완화).
- 기존 테스트 중 NER을 신경 쓰지 않는 것은 NER을 명시적으로 끈다(기본값에 기대지 않는다). 기본값 테스트는 따로 둔다.
- 맥 `uv run pytest`, Linux 컨테이너.

## 5. 확인
- 실제 nerserve(새 모델) + laya-serve(v3)로 `eval.run_eval --client laya --ner server --details` → 조합 비교 표의 "v3 + 새 NER"과 같은지(가림 표시가 바뀌어 v3 확신도가 조금 다를 수 있다).
- `gate ui --no-browser` + curl로 `/api/state`의 NER 줄 9개, host 안내.

## 6. 범위 밖
- 기존 32종 모델 지원 유지, 둘 합침.
- NER 서버 자동 시작(기본 켜짐이라 서버가 없으면 차단된다는 점은 README·화면에 알린다).
- 성 없는 이름(`월매`), 나이·연도 때문에 생기는 승인.
