"""Tests for the GIF at the top of the README and the example files shown in it (initiative doeff-publish).

The GIF (images/readme-demo.gif) is recorded by tools/readme_demo/record.py, which runs the adjacent digest.py (program) and handlers.py (4 handlers)
with a real bash and Python. The GIF does not change until it is re-recorded, so even if doeff's API changes and the 2 files stop running,
the GIF would still look like it "works". This test runs the 2 files on the current doeff and checks the 4 answers shown in the GIF -- canned
'14 chars', the 2 effects the trace sees, 'over budget at Summarize' where budget(1) discards k, and the real fetch path (live_world) --
still pass, and that the GIF's 4 lines return the same answers as the 4 lines of `python -m doeff demo`. If it fails, fix the files
and re-record the GIF (command in record.py's docstring).

live_world runs with a file:// URL -- urlopen reads files through the same path as HTTP, so the test does not go out to the network.
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
    """Load the files in tools/readme_demo/ as modules (tools/ is not a package).

    Register it in sys.modules by name -- record.py's dataclass resolves type annotations from the module name.
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
    """The handlers.py shown in the GIF -- like `python -i handlers.py`, it reads the adjacent digest.py with `from digest import`."""
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
    # The 4 lines typed in the GIF's REPL are the same 4 lines `python -m doeff demo` runs; evaluating with the same-named objects returns the same answers and printed output.
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


def test_readme_opening_shows_the_gif_the_recorder_writes() -> None:
    # The opening keeps `pip install doeff` and the 30-second example above the GIF (about 740 px
    # tall), so the first screen shows the install line and the code; the GIF follows them, before
    # "Why doeff?".
    text = README.read_text(encoding="utf-8")
    assert "\n## Why doeff?\n" in text, "README has no section 'Why doeff?'"
    opening = text.split("\n## Why doeff?\n", 1)[0]
    gif = re.search(rf"!\[[^\]]+\]\({re.escape(GIF_URL)}\)", opening)
    # PyPI shows the README too, so the GIF is linked by its absolute URL, not a path in the repo.
    assert gif, f"README has no GIF ({GIF_URL}) with alt text before 'Why doeff?'"
    assert (
        opening.index("pip install doeff")
        < opening.index("\n## A 30-second example\n")
        < gif.start()
    )
    assert _load(DEMO_DIR / "record.py").DEFAULT_OUT == GIF


def test_gif_is_committed_within_the_size_budget() -> None:
    # The source of the guideline value (about 1 MB) is the recording tool -- notify when the tool also exceeds it with the same value.
    assert GIF.read_bytes()[:6] == b"GIF89a"
    assert GIF.stat().st_size <= _load(DEMO_DIR / "record.py").SIZE_BUDGET_BYTES
