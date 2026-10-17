"""tools/check_links.py が README・docs・PyPI の頁に出る README の切れた link を名指す事と、今の木に切れた link が無い事。

道具は標準ライブラリだけで、workspace の外の interpreter でも走る。ここでは道具の部品を読んで、link の拾い方・GitHub の
見出しの anchor の付け方・先の解き方・切れ目の名指し方と、外の link の答えの分け方(手元の HTTP の server に聞く)を
確かめ、最後に repo の木そのものを道具で読んで切れた link が 0 である事を確かめる。最後の検は main の CI(Python
Compatibility の job が root の tests を走らせる)で push ごとに走り、登記の前の make test-changed でも、docs と README を
変えた便で選ばれる(pyproject.toml の contract の表)。
"""

from __future__ import annotations

import importlib.util
import sys
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str) -> ModuleType:
    """tools/ の module を読む(tools/ は package ではない)。道具は隣の module を名で import するので、sys.modules に置く。"""
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / f"{name}.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_load("readme_examples")
_load("verify_release_install")
links = _load("check_links")


def _targets(text: str) -> list[str]:
    return [link.target for link in links.extract_links("docs/page.md", text)]


# --- link を拾う --------------------------------------------------------------------------------


def test_extract_reads_every_kind_of_link_in_prose() -> None:
    text = "\n".join(
        [
            "See [guide](01-getting-started.md) and ![demo](../images/demo.gif).",
            "[ref]: https://example.org/ref",
            "Autolink <https://docs.python.org/3/> and bare https://pypi.org/project/doeff/.",
            '<a href="../LICENSE">license</a> <img src="logo.png">',
            '[Wiki](https://en.wikipedia.org/wiki/Effect_(computer_science)) with a [titled](x.md "Title") link.',
        ]
    )
    assert _targets(text) == [
        "01-getting-started.md",
        "../images/demo.gif",
        "https://example.org/ref",
        "https://docs.python.org/3/",
        "https://pypi.org/project/doeff/",
        "../LICENSE",
        "logo.png",
        "https://en.wikipedia.org/wiki/Effect_(computer_science)",
        "x.md",
    ]


def test_extract_skips_code_comments_footnotes_and_non_links() -> None:
    text = "\n".join(
        [
            "```python",
            "print('[not](a-link.md) https://inside.fence.example/')",
            "```",
            "Inline `[code](span.md)` is not a link.",
            "<!-- [hidden](comment.md) -->",
            "[^D1]: **D1 Scope**: a footnote, not a link definition.",
            "reads inspect.stack's [n][0](frame only) shape",
            "~~~~",
            "[tilde](fence.md)",
            "~~~~",
            "[after](after.md)",
        ]
    )
    assert _targets(text) == ["after.md"]


def test_extract_keeps_line_numbers_and_unescapes_entities() -> None:
    found = links.extract_links("README.md", "line one\n\n[x](https://a.example/?a=1&amp;b=2)\n")
    assert [(link.line, link.target) for link in found] == [(3, "https://a.example/?a=1&b=2")]


# --- 見出しの anchor ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("heading", "slug"),
    [
        ("What `@do` Returns", "what-do-returns"),
        ("Ok/Err Result Values", "okerr-result-values"),
        ("Local - Temporary Environment Override", "local---temporary-environment-override"),
        (
            "3.2 Effect `created_at` / `EffectCreationContext`",
            "32-effect-created_at--effectcreationcontext",
        ),
        ("**Bold** and [a link](x.md)", "bold-and-a-link"),
        ("日本語の見出し・中点", "日本語の見出し中点"),
    ],
)
def test_github_slug_matches_github(heading: str, slug: str) -> None:
    assert links.github_slug(links.heading_text(heading)) == slug


def test_anchors_number_duplicates_and_read_setext_and_html_ids() -> None:
    text = "\n".join(
        [
            "# Title",
            "## Usage",
            "## Usage",
            "",
            "Setext Heading",
            "--------------",
            '<a name="Custom-Anchor"></a>',
            "```",
            "# not a heading",
            "```",
        ]
    )
    assert links.anchors_of(text) == {
        "title",
        "usage",
        "usage-1",
        "setext-heading",
        "custom-anchor",
    }


# --- 先を解く ------------------------------------------------------------------------------------


def test_resolve_reads_own_github_links_from_the_tree() -> None:
    blob = links.resolve(
        "README.md",
        "https://github.com/proboscis/doeff/blob/main/docs/01-getting-started.md#install",
    )
    assert (blob.kind, blob.path, blob.fragment, blob.relative_path) == (
        links.Kind.IN_REPO,
        "docs/01-getting-started.md",
        "install",
        False,
    )
    assert (
        links.resolve("README.md", "https://github.com/proboscis/doeff/tree/main/packages").path
        == "packages"
    )
    raw = links.resolve(
        "README.md", "https://raw.githubusercontent.com/proboscis/doeff/main/images/readme-demo.gif"
    )
    assert (raw.kind, raw.path) == (links.Kind.IN_REPO, "images/readme-demo.gif")
    tagged = links.resolve("README.md", "https://github.com/proboscis/doeff/blob/v0.6.0/README.md")
    assert (tagged.kind, tagged.url) == (
        links.Kind.EXTERNAL,
        "https://github.com/proboscis/doeff/blob/v0.6.0/README.md",
    )


def test_resolve_relative_links_like_github() -> None:
    assert links.resolve("docs/a/b.md", "../c.md#Part").path == "docs/c.md"
    assert links.resolve("docs/a/b.md", "../c.md#Part").fragment == "Part"
    assert links.resolve("docs/a/b.md", "/README.md").path == "README.md"
    assert links.resolve("docs/a/b.md", "My%20File.md?plain=1").path == "docs/a/My File.md"
    assert links.resolve("docs/b.md", "../../outside.md").path is None
    in_page = links.resolve("docs/b.md", "#section")
    assert (in_page.path, in_page.fragment, in_page.relative_path) == (
        "docs/b.md",
        "section",
        False,
    )
    assert links.resolve("docs/b.md", "mailto:someone@example.org").kind is links.Kind.OTHER


# --- repo の中の切れ目 -----------------------------------------------------------------------------


def _problems(tmp_path: Path, files: dict[str, str]) -> list[str]:
    """files を tmp_path に置き、木を files の名で作って、全部の file の切れ目を `<path>:<行>: <先>` で返す。"""
    for name, text in files.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(text, encoding="utf-8")
    tree = links.Tree.of(files)
    found = links.check_in_repo(tmp_path, tree, links.collect(tmp_path, list(files)))
    return [str(problem.link) for problem in found]


def test_in_repo_check_names_missing_files_and_anchors(tmp_path: Path) -> None:
    files = {
        "docs/guide.md": "\n".join(
            [
                "[ok](other.md#real-heading)",
                "[missing file](gone.md)",
                "[missing anchor](other.md#no-such-heading)",
                "[dir](../packages/)",
                "[line anchor](../packages/tool.py#L10-L20)",
                "[outside](../../etc/passwd)",
                "[own main](https://github.com/proboscis/doeff/blob/main/docs/removed.md)",
            ]
        ),
        "docs/other.md": "# Real Heading\n",
        "packages/tool.py": "",
    }
    assert _problems(tmp_path, files) == [
        "docs/guide.md:2: gone.md",
        "docs/guide.md:3: other.md#no-such-heading",
        "docs/guide.md:6: ../../etc/passwd",
        "docs/guide.md:7: https://github.com/proboscis/doeff/blob/main/docs/removed.md",
    ]


def test_pypi_readmes_reject_relative_paths_but_allow_in_page_anchors(tmp_path: Path) -> None:
    files = {
        "packages/doeff-vm/README.md": "\n".join(
            [
                "# doeff-vm",
                "[jump](#doeff-vm)",
                "[relative](../../LICENSE)",
                "[absolute](https://github.com/proboscis/doeff/blob/main/LICENSE)",
            ]
        ),
        "packages/doeff-gemini/README.md": "[relative is fine off PyPI](../../LICENSE)",
        "LICENSE": "",
    }
    assert _problems(tmp_path, files) == ["packages/doeff-vm/README.md:3: ../../LICENSE"]


def test_default_files_cover_entry_docs_and_skip_record_dirs() -> None:
    assert links.is_default_file("README.md", ("docs/design/",))
    assert links.is_default_file("docs/history/specs/x.md", ("docs/design/",))
    assert links.is_default_file("packages/doeff-vm/README.md", ("docs/design/",))
    assert not links.is_default_file("docs/design/model.md", ("docs/design/",))
    assert not links.is_default_file("packages/doeff-vm/docs/README.md", ("docs/design/",))
    assert not links.is_default_file("docs/architecture.hy", ("docs/design/",))


# --- 外の link の答え --------------------------------------------------------------------------------


class _Site(BaseHTTPRequestHandler):
    """path ごとに決まった答えを返す手元の site。/head-refused は HEAD を 405 で断り GET には 200 を返す。"""

    def do_HEAD(self) -> None:
        self._answer(head=True)

    def do_GET(self) -> None:
        self._answer(head=False)

    def _answer(self, *, head: bool) -> None:
        """path の決まった番号で答える(本文は空)— 道具が番号ごとに答えを分ける事を確かめるため。"""
        codes = {"/ok": 200, "/gone": 404, "/removed": 410, "/busy": 503, "/forbidden": 403}
        code = 405 if head and self.path == "/head-refused" else codes.get(self.path, 200)
        self.send_response(code)
        self.send_header("Content-Length", "0")
        self.end_headers()


@pytest.fixture
def site() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Site)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def test_ask_sorts_answers_into_broken_and_unconfirmed(
    site: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(links, "RETRY_WAITS_SECONDS", (0,))
    verdicts = {
        path: links.ask(f"{site}{path}").verdict
        for path in ("/ok", "/head-refused", "/gone", "/removed", "/busy", "/forbidden")
    }
    assert verdicts == {
        "/ok": links.Verdict.OK,
        "/head-refused": links.Verdict.OK,
        "/gone": links.Verdict.BROKEN,
        "/removed": links.Verdict.BROKEN,
        "/busy": links.Verdict.UNCONFIRMED,
        "/forbidden": links.Verdict.UNCONFIRMED,
    }


def test_example_hosts_are_not_asked() -> None:
    assert links.is_example_host("http://localhost:8000/api")
    assert links.is_example_host("https://api.example.com/v1")
    assert links.is_example_host("https://service.internal/")
    assert not links.is_example_host("https://pypi.org/project/doeff/")


# --- 今の木 ---------------------------------------------------------------------------------------


def test_repo_links_resolve_in_the_tree(capsys: pytest.CaptureFixture[str]) -> None:
    """README・docs・packages/*/README.md の repo の中の link が、今の木で全部解ける(CI の repo の中の job と同じ確かめ)。"""
    assert links.main(["--root", str(ROOT)]) == 0, capsys.readouterr().out
