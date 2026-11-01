"""보이는 문구에 판단 모델의 코드 이름 "Jev"가 나오지 않는다(사용자 결정: 보이는 곳은 "판단 모델").

설정 키(jev_backend 등)·환경변수(JEV_*)·감사 로그 키(jev_label 등)·모듈·클래스 이름은 코드 이름이라 그대로 둔다.
"""

import argparse
import ast
import io
import re
from pathlib import Path

import pytest

from gate.audit_cli import format_record
from gate.jev_client import MockJevClient, make_client
from gate.ner_client import Entity, NerResult
from gate.peer import Peer
from gate.pipeline import Inspector
from gate.policy import SETTING_NAMES, Entry, PolicySource, describe_setting, write_entries
from gate.policy_cli import register, run
from gate.store import PlainStore

ROOT = Path(__file__).resolve().parent.parent
JEV = re.compile(r"(?<![A-Za-z_])Jev(?![A-Za-z_])")  # 낱말 Jev. JevClient 같은 이름은 빼고 본다
PEER = Peer(pid=4321, uid=501, exe="/usr/local/bin/claude")
INTRO = "지원자 홍길동입니다.\n연락처는 010-1234-5678입니다.\n"


def visible_strings(path: Path) -> list[tuple[int, str]]:
    """파이썬 파일의 문자열 상수(f-string 조각 포함). docstring처럼 문장으로만 놓인 문자열은 뺀다."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docs = {id(n.value) for n in ast.walk(tree) if isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant)}
    return [(n.lineno, n.value) for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs]


def test_no_jev_in_string_literals_or_ui_files():
    found = [f"{path.relative_to(ROOT)}:{line}: {text!r}"
             for folder in ("gate", "nerserve") for path in sorted((ROOT / folder).rglob("*.py"))
             for line, text in visible_strings(path) if JEV.search(text)]
    found += [str(path.relative_to(ROOT)) for path in sorted((ROOT / "gate" / "ui_static").rglob("*"))
              if path.suffix in (".html", ".js", ".css") and JEV.search(path.read_text(encoding="utf-8"))]
    assert found == []


class FakeNer:
    def cache_tag(self):
        return "ner:test"

    def find(self, text):
        start = text.find("홍길동")
        return NerResult((Entity("NAME", start, start + 3, 0.9),) if start >= 0 else (), "ner:test")


class Broken:
    def judge(self, text, context):
        return "형식이 아닌 답"


@pytest.fixture
def source(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    (root / "자소서.txt").write_text(INTRO, encoding="utf-8")
    return root


def reason(source, tmp_path, *, ner=False, jev=None, factory=None) -> str:
    policy_path = tmp_path / "policy.yaml"
    write_entries(policy_path, [Entry(settings={"ner_backend": "server" if ner else "none"})])
    inspector = Inspector(PlainStore(source), PolicySource(policy_path),
                          jev_factory=factory or (lambda policy: jev),
                          ner_factory=lambda policy: FakeNer() if policy.ner_backend == "server" else None)
    return inspector.inspect(PEER, "/자소서.txt").judgment.reason


@pytest.mark.parametrize("ner, label, confidence, expected", [
    (False, "sensitive", 0.99, "판단 모델 결과(sensitive, 확신도 0.99)에 따라 차단했습니다"),
    (False, "sensitive", 0.70, "판단 모델 결과(sensitive, 확신도 0.70)가 확실하지 않습니다"),
    (True, "sensitive", 0.99, "가린 뒤에도 판단 모델이 가려지지 않은 정보가 남았다고 판단했습니다"),
    (True, "safe", 0.99, "(가린 뒤 판단 모델 결과 safe, 확신도 0.99)"),
])
def test_pipeline_reasons_say_judge_model(source, tmp_path, ner, label, confidence, expected):
    text = reason(source, tmp_path, ner=ner, jev=MockJevClient("fixed", label=label, confidence=confidence))
    assert expected in text and not JEV.search(text)


def test_pipeline_error_reasons_say_judge_model(source, tmp_path):
    broken = reason(source, tmp_path, jev=Broken())
    assert broken == "판단 모델 검사에 실패해 차단했습니다: 판단 모델 응답 형식이 올바르지 않습니다: str"
    unknown = reason(source, tmp_path, factory=lambda policy: make_client("없는백엔드"))
    assert unknown.startswith("판단 모델을 준비하지 못해 차단했습니다: 알 수 없는 판단 모델 백엔드: ")
    assert not JEV.search(broken + unknown)


def gate_policy(path, *argv, answers=""):
    parser = argparse.ArgumentParser(prog="gate")
    register(parser.add_subparsers(dest="command", required=True))
    out = io.StringIO()
    run(parser.parse_args(["policy", "--policy", str(path), *argv]), stdin=io.StringIO(answers), stdout=out,
        require_tty=False)
    return out.getvalue()


def test_policy_cli_output_says_judge_model(tmp_path):
    path = tmp_path / "policy.yaml"
    added = gate_policy(path, "add", "병원 기록은 보여주지 마", answers="y\n")
    assert "판단 모델이 정할 때" in added and "지금 판단 모델은 Mock" in added
    shown = gate_policy(path, "show")
    assert "판단 모델 기준" in shown and "판단 모델에게 함께 알려 주는 내 문장" in shown
    assert "가린 결과를 판단 모델에 다시 물어" in shown and "판단 모델 백엔드: mock" in shown
    allowed = gate_policy(path, "add", "이름은 그대로 보여줘", answers="n\n")
    assert "다만 판단 모델이 문서를 민감하다고 보면" in allowed
    assert not JEV.search(added + shown + allowed)


def test_setting_names_and_audit_details_say_judge_model():
    assert not [name for name in SETTING_NAMES.values() if JEV.search(name)]
    assert describe_setting("jev_backend", "laya") == "판단 모델 백엔드: laya"
    line = format_record({"ts": "2026-10-31T09:00:00+09:00", "op": "cat", "decision": "mask", "path": "/a.txt",
                          "jev_label": "safe", "jev_confidence": 0.93, "cached": True, "process": "python3.12"})
    assert "판단 모델 safe 0.93 (캐시)" in line and not JEV.search(line)
