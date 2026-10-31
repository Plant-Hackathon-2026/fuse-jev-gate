"""NER 토큰 예측을 문자 범위 개체로 바꾸는 순수 로직 (torch 없음, 표준 라이브러리만).

NER 서버(nerserve)가 모델이 낸 토큰별 예측을 이 모듈에 넘겨 개체 목록을 만든다. 모델 없이 시험할 수 있게
torch가 필요 없는 부분만 여기에 둔다. 데몬 쪽 테스트 묶음에서도 돌므로 gate 패키지의 다른 모듈을 import하지 않는다.

흐름 (창마다 → 문서 전체)
1. group_tokens: 한 창의 토큰 (start, end, BIO 라벨, P(개체)) 목록에서 P(개체) ≥ threshold인 토큰을 이어 개체로 만든다.
   같은 종류이고 I-면 앞 개체에 붙이고, 아니면 새 개체를 시작한다. 개체 점수는 토큰 P(개체)의 평균이다.
2. best_per_span: 겹치는 창에서 같은 (start, end)가 여러 번 나오면 점수 높은 것 하나만 남긴다.
3. refine: 앞뒤 공백을 떼고, 형식 없는 종류는 낱말 끝까지 넓힌 뒤 끝의 조사 하나를 뗀다. 그다음 merge.
   조사는 넓히면서 붙은 글자에서만 뗀다(모델이 개체로 본 끝 글자는 조사처럼 보여도 남긴다).
   주소·장소는 "로"·"도"를 떼지 않는다(도로명·지명의 일부인 경우가 많다).
4. merge: 겹치면 하나로(범위는 합집합, 종류·점수는 점수 높은 쪽), 바로 붙어 있고 종류가 같으면 하나로.

범위는 파이썬 str 인덱스(코드 포인트)이고 end는 포함하지 않는다. 결과는 start 순이고 서로 겹치지 않는다.
"""

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

# (start, end, BIO 라벨, P(개체)). 라벨은 O를 뺀 라벨 중 확률이 가장 큰 것(예: "B-NAME").
Token = tuple[int, int, str, float]

# 형식이 있는 값은 낱말로 넓히거나 조사를 떼지 않고 앞뒤 공백만 다듬는다.
EXACT_KINDS = frozenset({
    "API_KEY", "CLOUD_KEY", "TOKEN", "PASSWORD", "PRIVATE_KEY", "DB_URL",
    "IP_ADDRESS", "INTERNAL_HOST", "EMAIL", "PHONE",
})

# 낱말 끝에서 떼는 조사(와 자주 붙는 서술격·대화체 끝말). 긴 것부터 맞춰 보고 하나만 뗀다.
JOSA: tuple[str, ...] = tuple(sorted({
    "에서부터", "으로부터",
    "에서는", "에서도", "에게서", "에게는", "으로는", "이라고", "이라는", "이라서", "한테서", "께서는",
    "입니다", "이에요", "이고요", "이세요", "인데요",
    "에서", "에게", "한테", "께서", "으로", "부터", "까지", "처럼", "보다", "이랑", "이나", "이고", "이며", "이다",
    "하고", "와는", "과는", "예요", "이요", "이야", "인데",
    "은", "는", "이", "가", "을", "를", "에", "의", "로", "도", "만", "와", "과", "랑",
}, key=lambda j: (-len(j), j)))

# 종류별로 떼지 않는 끝말. 주소·장소에서 "로"·"도"는 도로명(예시로)·지명(경기도, 제주도)의 일부인 경우가 많다.
_KEEP_ENDINGS: dict[str, frozenset[str]] = {
    "ADDRESS": frozenset({"로", "도"}),
    "PLACE": frozenset({"로", "도"}),
}

_WORD_CHAR = re.compile(r"[가-힣A-Za-z0-9]")
_JOSA_HOST = re.compile(r"[가-힣0-9]")  # 조사 바로 앞 글자가 이것일 때만 뗀다


@dataclass(frozen=True, slots=True)
class Entity:
    start: int
    end: int
    label: str   # B-/I- 없는 종류 이름 (예: "NAME")
    score: float

    def as_dict(self) -> dict:
        return {"start": self.start, "end": self.end, "label": self.label, "score": round(self.score, 4)}


def split_label(label: str) -> tuple[str, str]:
    """"B-NAME" → ("B", "NAME"). 접두어가 없으면 ("B", 라벨)로 본다(새 개체 시작)."""
    prefix, sep, kind = label.partition("-")
    if sep and prefix in ("B", "I") and kind:
        return prefix, kind
    return "B", label


def kinds_from_id2label(id2label: Mapping) -> list[str]:
    """config.json의 id2label에서 O를 빼고 B-/I-를 뗀 종류 이름을 id 순서로(중복 없이)돌려준다."""
    kinds: list[str] = []
    for key in sorted(id2label, key=int):
        label = id2label[key]
        if label == "O":
            continue
        kind = split_label(label)[1]
        if kind not in kinds:
            kinds.append(kind)
    return kinds


def group_tokens(tokens: Iterable[Token], threshold: float) -> list[Entity]:
    """한 창의 토큰 예측을 개체로 잇는다. start == end인 토큰(특수 토큰·패딩)은 건너뛴다."""
    out: list[Entity] = []
    cur: tuple[int, int, str, list[float]] | None = None  # (start, end, 종류, 점수들)

    def close() -> None:
        if cur is not None:
            start, end, kind, scores = cur
            out.append(Entity(start, end, kind, sum(scores) / len(scores)))

    for start, end, label, p_ent in tokens:
        if start >= end:
            continue
        if p_ent < threshold:
            close()
            cur = None
            continue
        bio, kind = split_label(label)
        if cur is not None and bio == "I" and kind == cur[2]:
            cur[3].append(p_ent)
            cur = (cur[0], end, kind, cur[3])
        else:
            close()
            cur = (start, end, kind, [p_ent])
    close()
    return out


def best_per_span(entities: Iterable[Entity]) -> list[Entity]:
    """같은 (start, end)는 점수가 가장 높은 것 하나만 남긴다. 결과는 (start, end) 순."""
    best: dict[tuple[int, int], Entity] = {}
    for ent in entities:
        key = (ent.start, ent.end)
        if key not in best or ent.score > best[key].score:
            best[key] = ent
    return [best[key] for key in sorted(best)]


def strip_josa(word: str, keep: int = 1, skip: frozenset[str] = frozenset()) -> int:
    """낱말 끝 조사 하나의 길이(뗄 것이 없으면 0). 긴 조사부터 맞춰 본다.

    조사 바로 앞 글자가 한글·숫자이고, 떼고 나서 keep 글자 이상 남을 때만 뗀다. skip에 있는 조사는 떼지 않는다.
    """
    keep = max(1, keep)
    for josa in JOSA:
        rest = len(word) - len(josa)
        if (rest >= keep and josa not in skip and word.endswith(josa)
                and _JOSA_HOST.match(word[rest - 1])):
            return len(josa)
    return 0


def _refine_one(text: str, ent: Entity) -> Entity | None:
    start, end = max(0, ent.start), min(len(text), ent.end)
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    if start >= end:
        return None
    if ent.label not in EXACT_KINDS:
        model_end = end  # 모델이 개체로 본 끝
        while start > 0 and _WORD_CHAR.match(text[start - 1]):
            start -= 1
        while end < len(text) and _WORD_CHAR.match(text[end]):
            end += 1
        # 넓히면서 붙은 글자에서만 조사를 뗀다. 모델이 개체로 본 글자는 조사처럼 보여도 남긴다
        # (체육교육"과", 경기"도", 김지"은" 같은 이름의 끝 글자를 드러내지 않게).
        end -= strip_josa(text[start:end], keep=model_end - start,
                          skip=_KEEP_ENDINGS.get(ent.label, frozenset()))
    return Entity(start, end, ent.label, ent.score)


def merge(entities: Iterable[Entity]) -> list[Entity]:
    """겹치면 하나로(합집합, 종류·점수는 점수 높은 쪽), 바로 붙어 있고 종류가 같으면 하나로(점수는 큰 쪽)."""
    merged: list[Entity] = []
    for ent in sorted(entities, key=lambda e: (e.start, -e.end)):
        if merged and ent.start < merged[-1].end:
            last = merged[-1]
            keep = last if last.score >= ent.score else ent
            merged[-1] = Entity(last.start, max(last.end, ent.end), keep.label, keep.score)
        elif merged and ent.start == merged[-1].end and ent.label == merged[-1].label:
            last = merged[-1]
            merged[-1] = Entity(last.start, ent.end, last.label, max(last.score, ent.score))
        else:
            merged.append(ent)
    return merged


def refine(text: str, entities: Iterable[Entity]) -> list[Entity]:
    """범위를 다듬고(공백·낱말·조사) 합친다. 문서 밖으로 나간 범위는 문서 안으로 자르고, 빈 범위는 버린다."""
    refined = [r for r in (_refine_one(text, e) for e in entities) if r is not None]
    return merge(refined)


def entities_from_windows(text: str, windows: Iterable[Sequence[Token]], threshold: float) -> list[Entity]:
    """한 문서의 창별 토큰 예측 → 다듬고 합친 개체 목록."""
    if not text:
        return []
    found: list[Entity] = []
    for window in windows:
        found.extend(group_tokens(window, threshold))
    return refine(text, best_per_span(found))


def is_valid(text: str, entities: Sequence[Entity]) -> bool:
    """0 ≤ start < end ≤ len(text), start 순, 서로 겹치지 않음, 점수 0~1."""
    prev_end = 0
    for ent in entities:
        if not (prev_end <= ent.start < ent.end <= len(text)) or not (0.0 <= ent.score <= 1.0):
            return False
        prev_end = ent.end
    return True
