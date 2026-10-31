"""README の頭の GIF と、GIF に映る例の file の検(取り組み doeff-publish)。

GIF(images/readme-demo.gif)は tools/readme_demo/record.py が、隣の digest.py(program)と handlers.py(4 つの handler)を
本物の bash と Python で走らせて録る。GIF は録り直すまで変わらないので、doeff の API が変わって 2 つの file が走らなくなっても
GIF は古いまま「動く」ように見える。この検は 2 つの file を今の doeff で走らせ、GIF に映る 4 つの答え — canned の
'14 chars'・trace が見る 2 つの effect・budget(1) が k を捨てた 'over budget at Summarize'・本物の取り寄せの道(live_world)— が
今も通る事と、GIF の 4 行が `python -m doeff demo` の 4 行と同じ答えを返す事を確かめる。落ちたら file を
直して GIF を録り直す(record.py の docstring の命令)。

live_world は file:// の URL で走らせる — urlopen は HTTP と同じ道で file を読むので、検は network に出ない。
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path
from types import ModuleType

import pytest

from doeff import handler, run
from doeff.cli import demo

ROOT = Path(__file__).resolve().parents[1]
DEMO_DIR = ROOT / "tools" / "readme_demo"
README = ROOT / "README.md"
GIF = ROOT / "images" / "readme-demo.gif"
GIF_URL = "https://raw.githubusercontent.com/proboscis/doeff/main/images/readme-demo.gif"


def _load(path: Path) -> ModuleType:
    """tools/readme_demo/ の file を module として読む(tools/ は package ではない)。

    sys.modules に名で置く — record.py の dataclass は、型の注記を module の名から引く。
    """
    name = f"readme_demo_{path.stem}"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def handlers(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """GIF に映る handlers.py — `python -i handlers.py` と同じく、隣の digest.py を `from digest import` で読む。"""
    monkeypatch.syspath_prepend(str(DEMO_DIR))
    monkeypatch.delitem(sys.modules, "digest", raising=False)
    return _load(DEMO_DIR / "handlers.py")


def test_canned_answers_what_the_gif_shows(handlers: ModuleType) -> None:
    assert run(handler(handlers.canned)(handlers.digest(handlers.url))) == "14 chars"


def test_trace_sees_both_effects_and_passes_them_on(
    handlers: ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    program = handler(handlers.canned)(handler(handlers.trace)(handlers.digest(handlers.url)))
    assert run(program) == "14 chars"
    assert capsys.readouterr().out.splitlines() == [
        "trace: Fetch(url='https://example.com')",
        "trace: Summarize(text='<h1>Hello</h1>')",
    ]


def test_budget_drops_k_at_the_second_effect(handlers: ModuleType) -> None:
    program = handler(handlers.canned)(handlers.budget(1)(handlers.digest(handlers.url)))
    assert run(program) == "over budget at Summarize"


def test_live_world_reads_the_page_at_the_url(handlers: ModuleType, tmp_path: Path) -> None:
    page = tmp_path / "page.html"
    page.write_text("<h1>Hello, live</h1>", encoding="utf-8")
    program = handler(handlers.canned)(handler(handlers.live_world)(handlers.digest(page.as_uri())))
    assert run(program) == "20 chars"


def test_the_gif_runs_the_lines_of_the_demo_command_and_gets_its_answers(
    handlers: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # GIF の REPL で打つ 4 行は `python -m doeff demo` が走らせる 4 行と同じで、同じ名の物で評価すると同じ答えと印字を返す。
    record = _load(DEMO_DIR / "record.py")
    typed = [step.text for step in record.SCRIPT if isinstance(step, record.Type)]
    runs = [text for text in typed if text.startswith("run(")]
    assert runs == [step.expression for step in demo.STEPS]
    page = tmp_path / "page.html"
    page.write_text("<h1>Hello, live</h1>", encoding="utf-8")
    for line in runs:
        answers = [
            (
                eval(line, {"__builtins__": {}}, {**vars(module), "url": page.as_uri()}),
                capsys.readouterr().out,
            )
            for module in (handlers, demo)
        ]
        assert answers[0] == answers[1], line


def test_readme_head_shows_the_gif_the_recorder_writes() -> None:
    head = README.read_text(encoding="utf-8").split("\n## ", 1)[0]
    # PyPI の頁も README を見せるので、repo の中の path でなく絶対の URL で指す。
    assert re.search(rf"!\[[^\]]+\]\({re.escape(GIF_URL)}\)", head), (
        f"README の最初の節の前に、alt の付いた GIF の画像({GIF_URL})が無い"
    )
    assert _load(DEMO_DIR / "record.py").DEFAULT_OUT == GIF


def test_gif_is_committed_within_the_size_budget() -> None:
    # 目安(1 MB ほど)の定義元は録る道具 — 道具も同じ値で越えた事を知らせる。
    assert GIF.read_bytes()[:6] == b"GIF89a"
    assert GIF.stat().st_size <= _load(DEMO_DIR / "record.py").SIZE_BUDGET_BYTES
