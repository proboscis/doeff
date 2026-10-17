"""README の頭の GIF と、GIF に映る例の file の検(取り組み doeff-publish)。

GIF(images/readme-demo.gif)は tools/readme_demo/record.py が、隣の digest.py を本物の bash と Python で走らせて録る。
GIF は録り直すまで変わらないので、doeff の API が変わって digest.py が走らなくなっても GIF は古いまま「動く」ように見える。
この検は digest.py を今の doeff で走らせ、GIF に映る答え('14 chars')と本物の取り寄せの道(live_world)が今も通る事を
確かめる。落ちたら digest.py を直して GIF を録り直す(record.py の docstring の命令)。

live_world は file:// の URL で走らせる — urlopen は HTTP と同じ道で file を読むので、検は network に出ない。
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path
from types import ModuleType

from doeff import handler, run

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


def test_demo_answers_what_the_gif_shows_with_the_fake_handler() -> None:
    demo = _load(DEMO_DIR / "digest.py")
    assert run(handler(demo.fake_world)(demo.digest(demo.url))) == "14 chars"


def test_demo_live_handler_reads_the_page_at_the_url(tmp_path: Path) -> None:
    demo = _load(DEMO_DIR / "digest.py")
    page = tmp_path / "page.html"
    page.write_text("<h1>Hello, live</h1>", encoding="utf-8")
    assert run(handler(demo.live_world)(demo.digest(page.as_uri()))) == "20 chars"


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
