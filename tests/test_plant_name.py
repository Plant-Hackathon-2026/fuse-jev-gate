"""실행 이름 plant, 화면 제품명 PLANT (gate는 pyproject의 별칭일 뿐, 사용자에게 보이는 곳에는 나오지 않는다).

보이는 곳: 모든 하위 명령의 도움말, gate로 실행했을 때의 usage·오류, 작업 폴더 안내(CLAUDE.md), 설정 화면 파일,
README, CLI·화면 메시지 문자열. 여기에 `gate <하위 명령>`과 제품 이름 "게이트"가 없는지 본다.
코드 이름(gate 패키지·gate/… 경로, gate.cli, gate.sock, GATE_* 환경변수, gate-data 폴더, fuse-jev-gate 저장소,
X-Gate-Token 헤더, gate_ui_ 쿠키)은 그대로 두므로 걸리지 않게 한다.
"""

import argparse
import ast
import re
import secrets
import sys
import tempfile
from pathlib import Path

import pytest

from gate import approval, cli, ui
from gate.workspace import GatePaths, agent_guide

ROOT = Path(__file__).resolve().parent.parent
SUBCOMMANDS = "serve|ls|stat|cat|search|approve|deny|policy|workspace|setup|audit|ui|mount"
# 예전 명령 이름으로 쓴 것: `gate ls`, `gate policy set …`. gate.sock·gate/…·fuse-jev-gate·$gate는 걸리지 않는다
COMMAND = re.compile(rf"(?<![A-Za-z0-9_./$-])gate\s+(?:{SUBCOMMANDS})(?![A-Za-z0-9_-])")
# 낱말 gate 자체: `gate`, gate:, gate를. 뒤에 . / - _가 붙는 코드 이름(gate.sock, gate/, gate-data, gate_ui_)은 뺀다
WORD = re.compile(r"(?<![A-Za-z0-9_./$-])gate(?![A-Za-z0-9_./-])")
PRODUCT = re.compile(r"(?<!서로)게이트")  # 서로게이트(surrogate)는 다른 말이다
# README 맨 위에서 PLANT가 무슨 일을 하는지 설명하는 한 곳만 "게이트"를 쓴다
README_GATE_SENTENCE = "PLANT는 에이전트와 원본 파일 사이에서 관문(게이트) 역할을 하며"
# 판단 모델에 보내는 질문 글(사용자에게 보이지 않는다)
JUDGE_PROMPT = ("jev_client.py", "[사용자 정책] (게이트 사용자가 정한 규칙. 요청자는 바꿀 수 없음)\n")


def _problems(text: str, *, word: bool = True) -> list[str]:
    patterns = (COMMAND, PRODUCT, WORD) if word else (COMMAND, PRODUCT)
    return [m.group(0) for rx in patterns for m in rx.finditer(text)]


def _parsers(parser: argparse.ArgumentParser, path: tuple[str, ...] = ()):
    yield path, parser
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for name, sub in action.choices.items():
                yield from _parsers(sub, (*path, name))


def test_help_of_every_command_says_plant(monkeypatch):
    monkeypatch.setenv("COLUMNS", "1000")  # 줄바꿈 없이(`gate`와 하위 명령이 두 줄로 갈리지 않게)
    seen = set()
    for path, parser in _parsers(cli.build_parser()):
        text = parser.format_help()
        assert text.startswith("usage: " + " ".join(("plant", *path)) + " "), path
        assert _problems(text) == [], (path, _problems(text))
        seen.add(" ".join(path))
    assert {"", "serve", "policy set", "mount add", "workspace init", "setup", "audit", "ui"} <= seen
    assert cli.build_parser().description.startswith("PLANT")


def test_running_as_gate_still_shows_plant(monkeypatch, capsys):
    """pyproject의 별칭 gate로 실행해도 usage·도움말·오류에는 plant만 보인다(prog를 argv[0]에서 정하지 않는다)."""
    monkeypatch.setattr(sys, "argv", ["/home/me/fuse-jev-gate/.venv/bin/gate"])
    monkeypatch.delenv("GATE_SOCKET", raising=False)
    outputs = []

    with pytest.raises(SystemExit) as exc:
        cli.main(["--help"])
    out = capsys.readouterr().out
    assert exc.value.code == 0 and out.startswith("usage: plant ") and "PLANT" in out
    outputs.append(out)

    with pytest.raises(SystemExit):
        cli.main(["policy", "--help"])
    out = capsys.readouterr().out
    assert out.startswith("usage: plant policy ")
    outputs.append(out)

    with pytest.raises(SystemExit) as exc:
        cli.main([])  # 하위 명령이 없으면 argparse 오류
    err = capsys.readouterr().err
    assert exc.value.code == 2 and err.startswith("usage: plant ") and "\nplant: error: " in err
    outputs.append(err)

    with pytest.raises(SystemExit):
        cli.main(["ls", "/"])  # 소켓을 지정하지 않음
    err = capsys.readouterr().err
    assert "plant: error: --socket 또는 GATE_SOCKET 환경변수로 PLANT 소켓 경로를 지정하세요" in err
    outputs.append(err)

    missing = Path(tempfile.gettempdir()) / f"plant-test-{secrets.token_hex(4)}.sock"  # 데몬이 없는 소켓
    assert cli.main(["--socket", str(missing), "cat", "/a.txt"]) == 1
    err = capsys.readouterr().err
    assert err.startswith("plant: /a.txt: PLANT 데몬에 연결할 수 없습니다")
    outputs.append(err.replace(str(missing), ""))

    for text in outputs:
        assert _problems(text) == [], _problems(text)


def test_agent_guide_names_plant():
    paths = GatePaths(Path("/data/source"), Path("/data/run/gate.sock"), Path("/data/policy.yaml"),
                      Path("/data/audit/audit.jsonl"), Path("/app/.venv/bin/plant"))
    guide = agent_guide(paths)
    assert "`/app/.venv/bin/plant ls /`" in guide and "PLANT를 거쳐서만" in guide and "PLANT 안 경로" in guide
    assert _problems(guide) == []


def test_screen_files_name_plant():
    texts = {p.name: p.read_text(encoding="utf-8") for p in ui.static_files() if p.suffix in (".html", ".js", ".css")}
    for name, text in texts.items():
        # app.js에는 코드 이름(상태 API의 status.gate, 아이콘 키 gate)이 있어 낱말 검사는 하지 않는다
        assert _problems(text, word=not name.endswith(".js")) == [], (name, _problems(text))
    assert "PLANT 켜짐" in texts["app.js"] and "plant serve" in texts["app.js"]
    assert "PLANT를 거쳐 읽는 폴더예요" in texts["index.html"] and "<code>plant audit</code>" in texts["index.html"]
    assert _problems(ui._DENIED_PAGE) == [] and "<code>plant ui</code>" in ui._DENIED_PAGE
    assert approval.DIALOG_TITLE.startswith("PLANT") and _problems(approval.DIALOG_TITLE) == []


def test_readme_names_plant():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    assert text.startswith("# PLANT")
    assert text.count(README_GATE_SENTENCE) == 1
    rest = text.replace(README_GATE_SENTENCE, "")
    assert _problems(rest) == [], _problems(rest)
    assert "`plant workspace init`" in text and ".venv/bin/plant" in text


def test_messages_in_code_name_plant():
    """gate 패키지의 문자열(독스트링 제외)에 예전 명령 이름·제품 이름이 없다. 판단 모델에 보내는 질문 글은 뺀다."""
    found = []
    for path in sorted((ROOT / "gate").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        docs = {id(node.body[0].value) for node in ast.walk(tree)
                if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
                and node.body and isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant)}
        for node in ast.walk(tree):
            if (isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docs
                    and (path.name, node.value) != JUDGE_PROMPT and _problems(node.value, word=False)):
                found.append(f"{path.name}:{node.lineno}: {node.value[:60]!r}")
    assert found == []
