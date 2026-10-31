"""NER 체크포인트 읽기와 토큰 예측.

- read_labels, model_id, ServiceInfo 만들기는 표준 라이브러리만 쓴다(테스트에서 torch 없이 돈다).
- torch·transformers는 load 함수 안에서만 import한다. 오프라인으로만 읽고(local_files_only, HF_HUB_OFFLINE) 아무것도
  내려받지 않는다.

토큰화는 학습 때와 같은 창을 쓴다: 256토큰, 겹침 32, overflow 창, offset mapping(코드 포인트 위치).
토큰마다 P(개체) = 1 − P(O), 종류 후보는 O를 뺀 라벨 중 확률이 가장 큰 것. threshold 비교와 개체 잇기는
gate.ner_spans가 한다(요청마다 threshold가 다를 수 있어 모델 쪽에서는 자르지 않는다).
"""

import hashlib
import json
import os
from collections.abc import Callable, Sequence
from pathlib import Path

from gate.ner_spans import Token, kinds_from_id2label

MAX_LENGTH = 256
STRIDE = 32
DEFAULT_BATCH_SIZE = 16
DEVICES = ("auto", "cuda", "mps", "cpu")
_ID_FILES = ("tokenizer.json", "model.safetensors")


def read_id2label(model_dir: Path) -> dict[int, str]:
    config = json.loads((Path(model_dir) / "config.json").read_text(encoding="utf-8"))
    id2label = config.get("id2label")
    if not isinstance(id2label, dict) or not id2label:
        raise ValueError("config.json에 id2label이 없다")
    return {int(k): str(v) for k, v in id2label.items()}


def read_labels(model_dir: Path) -> list[str]:
    """/health의 labels: O를 빼고 B-/I-를 뗀 종류 이름(id 순)."""
    return kinds_from_id2label(read_id2label(model_dir))


def model_id(model_dir: Path) -> str:
    """"ner:" + sha256(config.json 바이트 + tokenizer.json·model.safetensors의 크기·수정 시각) 앞 16자리.

    가중치 2 GB를 매번 해시하지 않으려고 큰 파일은 크기와 수정 시각(ns)만 넣는다. 체크포인트를 바꾸면 달라진다.
    """
    model_dir = Path(model_dir)
    digest = hashlib.sha256((model_dir / "config.json").read_bytes())
    for name in _ID_FILES:
        st = (model_dir / name).stat()
        digest.update(f"\n{name}:{st.st_size}:{st.st_mtime_ns}".encode())
    return "ner:" + digest.hexdigest()[:16]


def _pick_device(torch, wanted: str) -> str:
    if wanted not in DEVICES:
        raise ValueError(f"장치는 {', '.join(DEVICES)} 중 하나여야 한다")
    cuda = torch.cuda.is_available()
    mps = getattr(torch.backends, "mps", None) is not None and torch.backends.mps.is_available()
    if wanted == "auto":
        return "cuda" if cuda else "mps" if mps else "cpu"
    if (wanted == "cuda" and not cuda) or (wanted == "mps" and not mps):
        raise ValueError(f"{wanted} 장치를 쓸 수 없다")
    return wanted


def load(model_dir: Path, device: str = "auto",
         batch_size: int = DEFAULT_BATCH_SIZE) -> tuple[Callable[[list[str]], list[list[Sequence[Token]]]], str]:
    """체크포인트를 오프라인으로 읽어 (토큰 예측 함수, 실제 장치)를 돌려준다."""
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    import torch
    from transformers import AutoModelForTokenClassification, AutoTokenizer

    model_dir = Path(model_dir)
    dev = _pick_device(torch, device)
    tokenizer = AutoTokenizer.from_pretrained(model_dir, local_files_only=True)
    if not getattr(tokenizer, "is_fast", False):
        raise RuntimeError("offset mapping을 주는(fast) 토크나이저가 필요하다")
    model = AutoModelForTokenClassification.from_pretrained(model_dir, local_files_only=True)
    model.to(dev)
    model.eval()
    id2label = [model.config.id2label[i] for i in range(model.config.num_labels)]
    if id2label[0] != "O":
        raise RuntimeError("라벨 0이 O가 아니다")
    pad_id = tokenizer.pad_token_id if tokenizer.pad_token_id is not None else 0

    def predict_tokens(texts: list[str]) -> list[list[Sequence[Token]]]:
        # (문서 번호, 토큰 id, offset) 창 목록
        chunks: list[tuple[int, list[int], list[tuple[int, int]]]] = []
        for ti, text in enumerate(texts):
            enc = tokenizer(text, truncation=True, max_length=MAX_LENGTH, stride=STRIDE,
                            return_overflowing_tokens=True, return_offsets_mapping=True)
            for ids, offsets in zip(enc["input_ids"], enc["offset_mapping"]):
                chunks.append((ti, list(ids), [(int(a), int(b)) for a, b in offsets]))
        out: list[list[Sequence[Token]]] = [[] for _ in texts]
        with torch.inference_mode():
            for s in range(0, len(chunks), batch_size):
                part = chunks[s:s + batch_size]
                width = max(len(ids) for _, ids, _ in part)
                input_ids = torch.full((len(part), width), pad_id, dtype=torch.long)
                attention = torch.zeros((len(part), width), dtype=torch.long)
                for row, (_, ids, _) in enumerate(part):
                    input_ids[row, :len(ids)] = torch.tensor(ids, dtype=torch.long)
                    attention[row, :len(ids)] = 1
                logits = model(input_ids=input_ids.to(dev), attention_mask=attention.to(dev)).logits
                probs = logits.float().softmax(-1)
                p_ent = (1.0 - probs[..., 0]).clamp(0.0, 1.0).cpu().tolist()
                best = (probs[..., 1:].argmax(-1) + 1).cpu().tolist()
                for row, (ti, ids, offsets) in enumerate(part):
                    tokens = [(a, b, id2label[best[row][k]], p_ent[row][k])
                              for k, (a, b) in enumerate(offsets) if a < b]
                    out[ti].append(tokens)
        return out

    return predict_tokens, dev
