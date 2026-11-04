"""click で進む図(docs/assets/figures/)の検 — 図の 1 行が本体に替わり、本体の step が図の中の物だけを指す事。"""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
FIGURES = DOCS / "assets" / "figures"
BODIES = sorted(FIGURES.glob("*.html"))
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
    states = [json.loads(state) for state in STEP.findall(text)]
    assert states, f"{body.name} に step が無い"
    for state in states:
        assert set(state.get("now", [])) <= anchors
        assert set(state.get("marks", {})) <= anchors
        assert set(state.get("tokens", {})) <= tokens
        assert {spec["at"] for spec in state.get("tokens", {}).values()} <= anchors
        assert set(state.get("values", {})) <= slots
        assert state["active"] in panels | {"run"}


@pytest.mark.parametrize("body", BODIES, ids=lambda path: path.stem)
def test_every_figure_has_its_gif(body: Path) -> None:
    gif = body.with_suffix(".gif")
    assert gif.is_file(), f"{gif.name} が無い — tools/docs-video/figure-gif.mjs で作る"
    assert gif.stat().st_size <= 3_000_000, f"{gif.name} が 3 MB を越える"
