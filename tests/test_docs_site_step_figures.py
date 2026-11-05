"""click で進む図(docs/assets/figures/)の検 — 図の 1 行が本体に替わり、本体の step が図の中の物だけを指し、図の本体と GIF が
対で在る事。

site では本体(<名>.html)が動き、GitHub と Zenn では同じ名の GIF が動く。片方だけが在ると、site か markdown の画面の一方で
図が欠けるか古い版が残るので、対で無い名を落とす。"""

from __future__ import annotations

import html
import importlib.util
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
FIGURES = DOCS / "assets" / "figures"
BODIES = sorted(FIGURES.glob("*.html"))
GIFS = sorted(FIGURES.glob("*.gif"))
# page が図を置く 1 行の GIF の名(hooks.FIGURE_LINE が本体に替える形の markdown の側)。
FIGURE_IMAGE = re.compile(r"!\[[^\]]*\]\((?:\.\./)*assets/figures/([a-z0-9-]+)\.gif\)")
STEP = re.compile(r"<li data-state='([^']*)'>")

_spec = importlib.util.spec_from_file_location("docs_site_hooks", ROOT / "tools" / "docs_site" / "hooks.py")
hooks = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hooks)


def test_the_hook_turns_the_gif_line_into_the_figure() -> None:
    line = '<p><a href="x.gif"><img alt="A figure" src="../assets/figures/handler-k.gif" /></a></p>'
    out = hooks.figures_to_steps(line, DOCS)
    assert 'data-step-figure="handler-k"' in out
    assert 'aria-label="A figure"' in out
    assert '<noscript><img alt="A figure" src="../assets/figures/handler-k.gif" /></noscript>' in out
    assert "<p>" not in out.split("<figure", 1)[0]


def test_the_hook_refuses_a_gif_without_a_body() -> None:
    with pytest.raises(ValueError, match="図の本体が無い"):
        hooks.figures_to_steps('<p><img alt="" src="assets/figures/nothing-here.gif" /></p>', DOCS)


@pytest.mark.parametrize("body", BODIES, ids=lambda path: path.stem)
def test_every_step_names_only_what_the_figure_has(body: Path) -> None:
    text = body.read_text(encoding="utf-8")
    anchors = set(re.findall(r'data-anchor="([^"]+)"', text))
    tokens = set(re.findall(r'data-token="([^"]+)"', text))
    slots = set(re.findall(r'data-slot="([^"]+)"', text))
    panels = set(re.findall(r'data-panel="([^"]+)"', text))
    states = [json.loads(html.unescape(state)) for state in STEP.findall(text)]
    assert states, f"{body.name} に step が無い"
    for state in states:
        assert set(state.get("now", [])) <= anchors
        assert set(state.get("marks", {})) <= anchors
        assert set(state.get("tokens", {})) <= tokens
        stops = [
            stop if isinstance(stop, str) else stop["at"]
            for spec in state.get("tokens", {}).values()
            for stop in [spec, *([spec["from"]] if "from" in spec else []), *spec.get("via", [])]
        ]
        assert {stop if isinstance(stop, str) else stop["at"] for stop in stops} <= anchors
        assert set(state.get("values", {})) <= slots
        assert state.get("active", "run") in panels | {"run"}


@pytest.mark.parametrize("body", BODIES, ids=lambda path: path.stem)
def test_every_figure_has_its_gif(body: Path) -> None:
    gif = body.with_suffix(".gif")
    assert gif.is_file(), f"{gif.name} が無い — tools/docs-video/figure-gif.mjs で作る"
    assert gif.stat().st_size <= 10_000_000, f"{gif.name} が 10 MB を越える"


@pytest.mark.parametrize("gif", GIFS, ids=lambda path: path.stem)
def test_every_gif_has_its_figure(gif: Path) -> None:
    assert gif.with_suffix(".html").is_file(), f"{gif.name} の図の本体 {gif.stem}.html が無い — GIF だけが残っている"


def test_every_figure_a_page_shows_has_its_body_and_gif() -> None:
    shown = {name for page in DOCS.rglob("*.md") for name in FIGURE_IMAGE.findall(page.read_text(encoding="utf-8"))}
    assert shown, "図を置く page が無い"
    for name in shown:
        assert (FIGURES / f"{name}.html").is_file(), f"page が {name}.gif を置くが、図の本体が無い"
        assert (FIGURES / f"{name}.gif").is_file(), f"page が {name}.gif を置くが、GIF が無い"
    assert {body.stem for body in BODIES} <= shown, "どの page にも置かれない図が在る"


def test_the_pair_check_finds_a_lone_gif(tmp_path: Path) -> None:
    (tmp_path / "lone.gif").write_bytes(b"GIF89a")
    with pytest.raises(AssertionError, match="GIF だけが残っている"):
        test_every_gif_has_its_figure(tmp_path / "lone.gif")
