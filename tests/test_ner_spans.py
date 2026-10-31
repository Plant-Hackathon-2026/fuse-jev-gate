"""gate.ner_spans: 토큰 예측 → 개체, 다듬기, 합치기. 모델 없이 시험한다. 이름·기관은 전부 지어낸 예시다."""

import json
import random
from pathlib import Path

import pytest

from gate.ner_spans import (
    EXACT_KINDS,
    JOSA,
    Entity,
    best_per_span,
    entities_from_windows,
    group_tokens,
    is_valid,
    kinds_from_id2label,
    merge,
    refine,
    split_label,
    strip_josa,
)

# 팀원 재학습판(xlmr-pii-ner)의 라벨 (2026-11-01)
KINDS = ["NAME", "CONTACT", "ID_NUMBER", "AFFILIATION", "LOCATION", "AGE_BIRTH"]


def spans(text: str, entities: list[Entity]) -> list[tuple[str, str]]:
    return [(e.label, text[e.start:e.end]) for e in entities]


def at(text: str, word: str, label: str, score: float = 0.9) -> Entity:
    start = text.index(word)
    return Entity(start, start + len(word), label, score)


# --- 라벨 ---

def test_kinds_from_id2label_drops_o_and_bio_in_id_order():
    id2label = {"0": "O"}
    for i, kind in enumerate(KINDS):
        id2label[str(2 * i + 1)] = f"B-{kind}"
        id2label[str(2 * i + 2)] = f"I-{kind}"
    shuffled = dict(sorted(id2label.items(), key=lambda kv: kv[1]))  # 키 순서가 섞여 있어도 id 순으로
    assert kinds_from_id2label(shuffled) == KINDS
    assert len(kinds_from_id2label(shuffled)) == 6


def test_split_label():
    assert split_label("B-NAME") == ("B", "NAME")
    assert split_label("I-AGE_BIRTH") == ("I", "AGE_BIRTH")
    assert split_label("NAME") == ("B", "NAME")


# --- 이어 붙이기 ---

def test_group_tokens_joins_i_of_same_kind_and_averages_score():
    tokens = [(0, 0, "B-NAME", 0.99),            # 특수 토큰은 건너뛴다
              (3, 4, "B-NAME", 0.9), (4, 5, "I-NAME", 0.8), (5, 6, "I-NAME", 0.7),
              (6, 7, "B-NAME", 0.1)]              # threshold 밑이면 끊긴다
    [ent] = group_tokens(tokens, 0.5)
    assert (ent.start, ent.end, ent.label) == (3, 6, "NAME")
    assert ent.score == pytest.approx(0.8)


def test_group_tokens_new_entity_on_b_or_other_kind_or_gap():
    tokens = [(0, 2, "B-NAME", 0.9), (2, 3, "B-NAME", 0.9),       # 같은 종류라도 B면 새 개체
              (3, 4, "I-AFFILIATION", 0.9),                        # 다른 종류의 I도 새 개체
              (5, 6, "I-AFFILIATION", 0.4),                        # 끊김
              (7, 8, "I-AFFILIATION", 0.6)]                        # 끊긴 뒤 I는 새 개체
    got = [(e.start, e.end, e.label) for e in group_tokens(tokens, 0.5)]
    assert got == [(0, 2, "NAME"), (2, 3, "NAME"), (3, 4, "AFFILIATION"), (7, 8, "AFFILIATION")]


def test_group_tokens_threshold_is_inclusive():
    assert len(group_tokens([(0, 1, "B-NAME", 0.5)], 0.5)) == 1
    assert group_tokens([(0, 1, "B-NAME", 0.4999)], 0.5) == []


def test_best_per_span_keeps_highest_score_for_same_range():
    ents = [Entity(0, 3, "NAME", 0.6), Entity(0, 3, "AFFILIATION", 0.8), Entity(5, 7, "LOCATION", 0.7)]
    assert best_per_span(ents) == [Entity(0, 3, "AFFILIATION", 0.8), Entity(5, 7, "LOCATION", 0.7)]


# --- 다듬기: 공백, 넓히기, 조사 ---

def test_refine_trims_whitespace():
    text = "담당  홍길동 씨"
    [ent] = refine(text, [Entity(2, 7, "NAME", 0.9)])
    assert text[ent.start:ent.end] == "홍길동"


def test_refine_widens_to_word_and_strips_josa():
    text = "저는 가온대학교에서 공부했고 홍길동은 친구다."
    ents = [Entity(3, 5, "AFFILIATION", 0.9),  # "가온"만 잡혀도 낱말 끝까지 넓힌 뒤 "에서"를 뗀다
            Entity(17, 18, "NAME", 0.9)]       # "길"만 잡혀도 "홍길동은" → "홍길동"
    assert spans(text, refine(text, ents)) == [("AFFILIATION", "가온대학교"), ("NAME", "홍길동")]


@pytest.mark.parametrize("word, expected", [
    ("서울에서부터", "서울"),       # 긴 조사부터
    ("가온대학교에서는", "가온대학교"),
    ("안동으로", "안동"),
    ("김철수가", "김철수"),
    ("이영희를", "이영희"),
    ("20231234는", "20231234"),    # 숫자 뒤 조사도 뗀다
    ("Kakao에서", "Kakao에서"),    # 조사 앞이 한글·숫자가 아니면 떼지 않는다
    ("이", "이"),                  # 남는 글자가 없으면 떼지 않는다
    ("홍길동님", "홍길동님"),       # 조사가 아니다
])
def test_strip_josa(word, expected):
    assert word[:len(word) - strip_josa(word)] == expected


def test_strip_josa_keeps_at_least_keep_chars():
    assert strip_josa("홍길동이랑", keep=4) == 1   # "이랑"을 떼면 모델이 본 "홍길동이"를 깎으니 "랑"만
    assert strip_josa("경기도", keep=3) == 0


def test_josa_like_ending_inside_model_span_is_kept():
    """모델이 개체로 본 끝 글자는 조사처럼 보여도 떼지 않고, 넓히면서 붙은 조사만 뗀다."""
    text = "김지은 학생은 경기도 체육교육과, 김지은은 을지로에서"
    ents = [at(text, "김지은", "NAME"), at(text, "경기도", "CONTACT"), at(text, "체육교육과", "AFFILIATION"),
            Entity(text.index("김지은은"), text.index("김지은은") + 3, "NAME", 0.9), at(text, "을지로", "CONTACT")]
    assert spans(text, refine(text, ents)) == [
        ("NAME", "김지은"), ("CONTACT", "경기도"), ("AFFILIATION", "체육교육과"), ("NAME", "김지은"), ("CONTACT", "을지로")]


def test_contact_and_location_keep_ro_and_do_endings():
    """연락처(상세 주소)·장소의 도로명(예시로)·지명(제주도)은 모델이 앞부분만 잡아도 "로"·"도"를 떼지 않는다.
    다른 종류는 뗀다."""
    text = "주소는 수원시 예시로 45, 고향은 제주도 그리고 홍길동도 왔다"
    ents = [Entity(text.index("수원시"), text.index("예시로") + 2, "CONTACT", 0.9),
            Entity(text.index("제주"), text.index("제주") + 2, "LOCATION", 0.9),
            Entity(text.index("홍길동"), text.index("홍길동") + 3, "NAME", 0.9)]
    assert spans(text, refine(text, ents)) == [("CONTACT", "수원시 예시로"), ("LOCATION", "제주도"), ("NAME", "홍길동")]
    assert spans("서울에서 왔다", refine("서울에서 왔다", [Entity(0, 2, "LOCATION", 0.9)])) == [("LOCATION", "서울")]
    assert spans("가온대학교도 왔다", refine("가온대학교도 왔다", [Entity(0, 5, "AFFILIATION", 0.9)])) == [
        ("AFFILIATION", "가온대학교")]


@pytest.mark.parametrize("word, expected", [
    ("홍길순이고요", "홍길순"), ("수성구예요", "수성구"), ("성춘향이요", "성춘향"), ("홍길동입니다", "홍길동"),
])
def test_chat_endings_are_stripped(word, expected):
    assert word[:len(word) - strip_josa(word)] == expected


def test_josa_list_is_longest_first():
    assert [len(j) for j in JOSA] == sorted((len(j) for j in JOSA), reverse=True)
    assert {"에서부터", "으로부터", "에서", "은", "는", "이", "가", "을", "를"} <= set(JOSA)


def test_no_kind_is_exact_so_contact_is_widened_like_others():
    """새 라벨에는 형식이 정해진 종류가 없다. 이메일·전화번호도 CONTACT라 낱말 넓히기를 그대로 쓴다."""
    assert EXACT_KINDS == frozenset()
    text = "연락처 hong@example.com에 남김, 전화는 010-1234-56"
    start = text.index("hong@")
    ent = Entity(start - 1, start + len("hong@exam"), "CONTACT", 0.9)  # 앞 공백 포함, 끝은 덜 잡힘
    [got] = refine(text, [ent])
    assert text[got.start:got.end] == "hong@example"  # 낱말(영문·숫자·한글) 끝까지만 넓힌다. '.'에서 멈춘다
    phone = text.index("010")
    [got] = refine(text, [Entity(phone + 4, phone + 6, "CONTACT", 0.9)])
    assert text[got.start:got.end] == "1234"


def test_refine_drops_whitespace_only_and_clips_out_of_range():
    text = "홍길동 "
    assert refine(text, [Entity(3, 4, "NAME", 0.9)]) == []                  # 공백뿐
    assert spans(text, refine(text, [Entity(1, 99, "NAME", 0.9)])) == [("NAME", "홍길동")]
    assert refine(text, [Entity(10, 20, "NAME", 0.9)]) == []                # 문서 밖


# --- 합치기 ---

def test_merge_overlap_takes_union_and_higher_score_label():
    got = merge([Entity(0, 5, "AFFILIATION", 0.7), Entity(3, 9, "LOCATION", 0.9)])
    assert got == [Entity(0, 9, "LOCATION", 0.9)]


def test_merge_nested_and_chain():
    got = merge([Entity(0, 10, "CONTACT", 0.8), Entity(2, 4, "LOCATION", 0.6), Entity(9, 12, "LOCATION", 0.5)])
    assert got == [Entity(0, 12, "CONTACT", 0.8)]


def test_merge_adjacent_same_kind_joins_but_other_kind_stays():
    assert merge([Entity(0, 3, "NAME", 0.6), Entity(3, 5, "NAME", 0.9)]) == [Entity(0, 5, "NAME", 0.9)]
    assert merge([Entity(0, 3, "NAME", 0.6), Entity(3, 5, "AGE_BIRTH", 0.9)]) == [
        Entity(0, 3, "NAME", 0.6), Entity(3, 5, "AGE_BIRTH", 0.9)]
    assert merge([Entity(0, 3, "NAME", 0.6), Entity(4, 5, "NAME", 0.9)]) == [
        Entity(0, 3, "NAME", 0.6), Entity(4, 5, "NAME", 0.9)]  # 한 칸이라도 떨어져 있으면 따로


def test_refine_merges_after_widening():
    text = "가온대학교 체육교육과"
    ents = [Entity(0, 2, "AFFILIATION", 0.9), Entity(2, 5, "AFFILIATION", 0.7), Entity(6, 11, "AFFILIATION", 0.8)]
    got = refine(text, ents)
    assert spans(text, got) == [("AFFILIATION", "가온대학교"), ("AFFILIATION", "체육교육과")]
    assert got[0].score == 0.9


# --- 이모지·빈 문자열·창 ---

def test_emoji_offsets_are_code_points_and_stop_widening():
    text = "👨‍👩‍👧 가족 😀홍길동😀은 왔다"
    start = text.index("홍")
    [ent] = refine(text, [Entity(start + 1, start + 2, "NAME", 0.9)])
    assert text[ent.start:ent.end] == "홍길동"  # 이모지에서 넓히기를 멈추고, "은"은 이모지 뒤라 따로다
    assert is_valid(text, [ent])


def test_empty_text_gives_no_entities():
    assert entities_from_windows("", [[(0, 1, "B-NAME", 0.9)]], 0.5) == []
    assert refine("", [Entity(0, 1, "NAME", 0.9)]) == []


def test_entities_from_overlapping_windows():
    text = "상담사 김철수와 홍길동이 가온대학교에서 만났다"
    s1, s2 = text.index("홍길동"), text.index("가온")
    window1 = [(4, 5, "B-NAME", 0.95), (5, 7, "I-NAME", 0.85),
               (s1, s1 + 1, "B-NAME", 0.9), (s1 + 1, s1 + 3, "I-NAME", 0.7)]  # 창 끝에서 "홍길동"
    window2 = [(s1, s1 + 1, "B-NAME", 0.95), (s1 + 1, s1 + 3, "I-NAME", 0.95),  # 같은 범위는 높은 점수
               (s2, s2 + 2, "B-AFFILIATION", 0.9), (s2 + 2, s2 + 5, "I-AFFILIATION", 0.9), (s2 + 5, s2 + 7, "I-AFFILIATION", 0.3)]
    got = entities_from_windows(text, [window1, window2], 0.5)
    assert spans(text, got) == [("NAME", "김철수"), ("NAME", "홍길동"), ("AFFILIATION", "가온대학교")]
    assert got[1].score == pytest.approx(0.95)
    assert got[0].as_dict() == {"start": 4, "end": 7, "label": "NAME", "score": 0.9}


def test_threshold_changes_result():
    text = "홍길동 왔다"
    window = [(0, 1, "B-NAME", 0.6), (1, 3, "I-NAME", 0.6)]
    assert spans(text, entities_from_windows(text, [window], 0.5)) == [("NAME", "홍길동")]
    assert entities_from_windows(text, [window], 0.7) == []


def test_result_is_always_sorted_disjoint_and_in_range():
    """무작위 토큰(겹치는 창, 문서 밖 offset, 이모지 섞인 글)에도 결과는 계약을 지킨다."""
    rng = random.Random(20261031)
    alphabet = list("홍길동가온대학교에서은는이 \n\tab12😀👩‍💻.,@-")
    labels = [f"{p}-{k}" for k in KINDS for p in "BI"]
    for _ in range(300):
        text = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 60)))
        windows = []
        for _ in range(rng.randint(1, 3)):
            tokens = []
            for _ in range(rng.randint(0, 30)):
                a = rng.randint(-2, len(text) + 2)
                tokens.append((a, a + rng.randint(-1, 4), rng.choice(labels), rng.random()))
            tokens.sort()
            windows.append(tokens)
        got = entities_from_windows(text, windows, rng.choice([0.3, 0.5, 0.9]))
        assert is_valid(text, got), (text, got)
        assert all(json.dumps(e.as_dict()) for e in got)


def test_is_valid_rejects_overlap_out_of_range_and_unsorted():
    text = "abcdef"
    assert is_valid(text, [Entity(0, 2, "NAME", 0.5), Entity(2, 4, "NAME", 0.5)])
    assert not is_valid(text, [Entity(0, 3, "NAME", 0.5), Entity(2, 4, "NAME", 0.5)])
    assert not is_valid(text, [Entity(2, 4, "NAME", 0.5), Entity(0, 1, "NAME", 0.5)])
    assert not is_valid(text, [Entity(4, 7, "NAME", 0.5)])
    assert not is_valid(text, [Entity(3, 3, "NAME", 0.5)])


def test_module_imports_no_other_gate_module():
    source = Path(__file__).resolve().parent.parent.joinpath("gate", "ner_spans.py").read_text(encoding="utf-8")
    assert "from gate" not in source and "import gate" not in source
