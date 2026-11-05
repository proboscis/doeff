"""Check that the links in README, docs, and the READMEs shown on PyPI pages are not broken (doeff-publish initiative, milestone 11).

Files read (default -- where the links a first-time visitor clicks on GitHub and PyPI live): the root README.md, CONTRIBUTING.md, CHANGELOG.md, CODE_OF_CONDUCT.md, SECURITY.md,
every *.md under docs/, and packages/*/README.md. Only git-tracked files are read (untracked files do not exist on GitHub).
Design record directories (`[tool.doeff] record-dirs` in the root pyproject.toml) are not read -- records are evidence as of
their time, and quoted text is not rewritten afterwards.

A link is a markdown `[text](target)`, `![text](target)`, a reference definition `[name]: target`, `<https://...>`, an HTML href= or src=, or a bare
`https://...` in prose (every GitHub and PyPI URL must be a clickable link). Fenced blocks (``` and ~~~), `code`, and HTML comments are not read.

Two checks:

- In-repo (default -- no network. In CI the job in-repo of .github/workflows/links.yml runs it on every push and pull request,
  and the root tests of Python Compatibility also run it via tests/test_check_links.py::test_repo_links_resolve_in_the_tree):
  - The target of a relative link (`docs/x.md`, `../LICENSE`, `/README.md`) must be a git-tracked file or directory.
  - The target of a GitHub link pointing at main (`https://github.com/proboscis/doeff/blob/main/<path>`, `tree/main`, `raw/main`,
    `https://raw.githubusercontent.com/proboscis/doeff/main/<path>`) must also exist in the tree -- read from the tree, not the network
    (so a change not yet on main can be checked for breaking after it lands).
  - A link with a `#heading` must point at a markdown file that has that heading (GitHub-style anchor) or an `id=` / `name=` anchor.
  - The READMEs that become PyPI pages (the 5 core packages -- defined by CORE in tools/verify_release_install.py) must contain no
    relative-path links. PyPI resolves a relative path against the PyPI page URL, not the GitHub tree, so it breaks on the PyPI page. Write
    them as GitHub links pointing at main. A link that is only `#heading` also works on PyPI (give the heading an anchor and point the link at it).
- External (`--external` -- goes to the network. It fluctuates with the other site's conditions, so it is not run on every push; the job external of links.yml runs it
  weekly and on manual trigger): send a request to http and https links and expect a response. Ask with HEAD, and if the result is not 2xx or 3xx,
  ask again with GET. 404, 410, and unresolvable hosts are broken links. 429, 5xx, 403, and
  timeouts are reported as "unconfirmed" if asking again after a pause gives the same result, but are not counted in the exit code (they fluctuate with the other side).
  GitHub links pointing at main and example hosts such as localhost and example.com are not sent. GitHub attachments (`https://github.com/user-attachments/...`) are also not
  sent -- they return 404 to a request without sign-in, but the GitHub UI rewrites them into a playable form in the body.

    uv run --no-project python tools/check_links.py               # in-repo links
    uv run --no-project python tools/check_links.py --external    # external links
    uv run --no-project python tools/check_links.py docs/x.md     # only the given files

Exit code: 0 if no broken links, 1 if any, 2 if the tool cannot be used (e.g. no git).
Uses only the standard library (runs without syncing the workspace).
"""

from __future__ import annotations

import argparse
import html
import posixpath
import re
import socket
import subprocess
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterable, Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from enum import Enum
from pathlib import Path, PurePosixPath

import tomllib

# A module in the same dir -- when run as a script, Python puts the script's dir at the head of sys.path.
import verify_release_install

REPO = Path(__file__).resolve().parents[1]
GITHUB_REPO = "proboscis/doeff"
DEFAULT_BRANCH = "main"
# Files at fixed names in the root. *.md under docs/ and packages/*/README.md are read from the tree (default_files).
ROOT_FILES = ("README.md", "CONTRIBUTING.md", "CHANGELOG.md", "CODE_OF_CONDUCT.md", "SECURITY.md")
# Hosts written as examples (requests get no answer, or the answer is meaningless). Matches the name or any name under it.
EXAMPLE_HOSTS = ("localhost", "127.0.0.1", "0.0.0.0", "example.com", "example.org", "example.net")
EXAMPLE_HOST_SUFFIXES = (".local", ".localhost", ".test", ".invalid", ".example", ".internal")
GITHUB_ATTACHMENTS = "https://github.com/user-attachments/"
USER_AGENT = "Mozilla/5.0 (compatible; doeff-link-check; +https://github.com/proboscis/doeff)"
TIMEOUT_SECONDS = 20
RETRY_WAITS_SECONDS = (2, 8)
WORKERS = 8


# --- collecting links ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Link:
    """One link in one file. Kept so a broken link can name which file and which line."""

    path: str  # path from the root (POSIX)
    line: int
    target: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.target}"


_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
_CODE_SPAN = re.compile(r"(`+)(?:(?!\1).)+?\1")
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
# Reference definition `[name]: target`. A footnote definition `[^name]: text` is not a link.
_REFERENCE_DEFINITION = re.compile(r"^ {0,3}\[(?!\^)[^\]]+\]:\s*<?([^\s>]+)>?")
_AUTOLINK = re.compile(r"<(https?://[^\s>]+)>")
_HTML_ATTRIBUTE = re.compile(r"""\b(?:href|src)\s*=\s*(?:"([^"]*)"|'([^']*)')""", re.IGNORECASE)
_BARE_URL = re.compile(r"https?://[^\s<>()\[\]\"'`]+")
# Trailing punctuation of a bare URL is not part of the link (same as GitHub autolink).
_TRAILING_PUNCTUATION = ".,:;!?*_~'\""
# What may follow `](target`: whitespace, a title (`"..."`, `'...'`, `(...)`), and the closing `)`. Anything else means it is not a link (`[0](frame only)`).
_LINK_TAIL = re.compile(r"""\s*(?:\s(?:"[^"]*"|'[^']*'|\([^)]*\))\s*)?\)""")


def _blank(match: re.Match[str]) -> str:
    """Replace the matched span with the same number of newlines and spaces -- so line numbers and columns still match the original file after blanking."""
    return re.sub(r"[^\n]", " ", match.group(0))


def _outside_fences(lines: Sequence[str]) -> Iterator[str]:
    """Lines with the inside of ``` and ~~~ fences blanked -- so code inside a fence is not read as links or headings."""
    fence = ""
    for line in lines:
        marker = _FENCE.match(line)
        if not fence and marker:
            fence = marker.group(1)
            yield ""
        elif fence:
            closes = (
                marker is not None
                and marker.group(1)[0] == fence[0]
                and len(marker.group(1)) >= len(fence)
                and not marker.group(2).strip()
            )
            fence = "" if closes else fence
            yield ""
        else:
            yield line


def markdown_lines(text: str, *, keep_code_spans: bool = False) -> list[str]:
    """Lines with fences and HTML comments blanked (line numbers match the original file). When collecting links `code` is blanked too; when building heading
    anchors it is kept (GitHub includes the characters inside a heading's `code` in the anchor)."""
    lines = _outside_fences(_HTML_COMMENT.sub(_blank, text).split("\n"))
    return [line if keep_code_spans else _CODE_SPAN.sub(_blank, line) for line in lines]


def _inline_targets(line: str) -> Iterator[tuple[int, str]]:
    """The target of `](target)` and the column where it starts -- to pick up targets of markdown inline links and images. Parentheses inside the target are
    included only when balanced (URLs of the form `Foo_(bar)`)."""
    start = 0
    while (opened := line.find("](", start)) != -1:
        position = opened + 2
        while position < len(line) and line[position] == " ":
            position += 1
        if position < len(line) and line[position] == "<":
            closed = line.find(">", position)
            if closed != -1 and _LINK_TAIL.match(line, closed + 1):
                yield position, line[position + 1 : closed]
            start = position + 1
            continue
        end = _destination_end(line, position)
        if end > position and _LINK_TAIL.match(line, end):
            yield position, line[position:end]
        start = max(end, position + 1)


def _destination_end(line: str, position: int) -> int:
    """The end column of the link target starting at position (before whitespace or an unbalanced `)`) -- to count balanced parentheses inside the target."""
    depth = 0
    for end in range(position, len(line)):
        char = line[end]
        if char.isspace() or (char == ")" and depth == 0):
            return end
        depth += {"(": 1, ")": -1}.get(char, 0)
    return len(line)


def _targets_in_line(line: str) -> list[tuple[int, str]]:
    """The (column, target) of each link on one line -- to merge the per-kind extraction into one list. Bare URLs inside other links are excluded."""
    marked = [
        *_inline_targets(line),
        *(
            (match.start(1), match.group(1))
            for match in [_REFERENCE_DEFINITION.match(line)]
            if match
        ),
        *((match.start(1), match.group(1)) for match in _AUTOLINK.finditer(line)),
        *(
            (match.start(), match.group(1) if match.group(1) is not None else match.group(2))
            for match in _HTML_ATTRIBUTE.finditer(line)
        ),
    ]
    bare = [
        (match.start(), match.group(0).rstrip(_TRAILING_PUNCTUATION))
        for match in _BARE_URL.finditer(line)
        if not any(
            position <= match.start() < position + len(target) for position, target in marked
        )
    ]
    return sorted([*marked, *bare])


def extract_links(path: str, text: str) -> list[Link]:
    """Return the links in text (the contents of the file at path) in order of appearance -- to name the objects to check by file and line. The same target on the same line counts once."""
    return [
        Link(path, number, target)
        for number, line in enumerate(markdown_lines(text), start=1)
        for target in dict.fromkeys(
            html.unescape(target.strip()) for _, target in _targets_in_line(line)
        )
        if target
    ]


# --- heading anchors ------------------------------------------------------------------------------

_ATX_HEADING = re.compile(r"^ {0,3}#{1,6}(?:[ \t]+(.*?))?(?:[ \t]+#+)?[ \t]*$")
_SETEXT_UNDERLINE = re.compile(r"^ {0,3}(=+|-+)\s*$")
_HTML_ANCHOR = re.compile(r"""\b(?:id|name)\s*=\s*(?:"([^"]+)"|'([^']+)')""", re.IGNORECASE)
_INLINE_IMAGE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_INLINE_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)|\[([^\]]*)\]\[[^\]]*\]")
_HTML_TAG = re.compile(r"<[^>]+>")
_EMPHASIS = re.compile(r"(?<!\w)[*_]+|[*_]+(?!\w)|\*+")


def heading_text(source: str) -> str:
    """Bring heading markdown close to the characters GitHub renders (strip marks, links, images, HTML tags) -- the source for building an anchor."""
    text = source.replace("`", "")
    text = _INLINE_IMAGE.sub("", text)
    text = _INLINE_LINK.sub(
        lambda match: match.group(1) if match.group(1) is not None else match.group(2), text
    )
    text = _HTML_TAG.sub("", text)
    text = _EMPHASIS.sub("", text)
    return html.unescape(text).strip()


def github_slug(text: str) -> str:
    """The anchor GitHub gives a heading (same as github-slugger: lowercase, delete everything except letters, digits, combining marks, `-`, `_`, and spaces,
    and turn spaces into `-`) -- to check whether the target of a `#heading` link exists, the way GitHub renders it."""
    kept = "".join(
        char
        for char in text.lower()
        if char in "-_ " or unicodedata.category(char)[0] in ("L", "N", "M")
    )
    return kept.replace(" ", "-")


def _heading_sources(lines: Sequence[str]) -> Iterator[str]:
    """The markdown lines of headings (the `#` form, and the form underlined with `===` or `---`) -- to pick up the headings that make anchors in order of appearance."""
    for index, line in enumerate(lines):
        if heading := _ATX_HEADING.match(line):
            yield heading.group(1) or ""
        elif (
            index + 1 < len(lines)
            and line.strip()
            and _SETEXT_UNDERLINE.match(lines[index + 1])
            and not _SETEXT_UNDERLINE.match(line)
            and not line.lstrip().startswith(("-", "*", "+", ">", "|"))
            and (index == 0 or not lines[index - 1].strip())
        ):
            yield line


def anchors_of(text: str) -> frozenset[str]:
    """The set of anchors (lowercase) of markdown text -- to look up the targets of `#heading` links. Heading anchors get
    `-1`, `-2` from the second occurrence of the same one (same as GitHub). HTML `id=` and `name=` are anchors too."""
    slugs = [
        github_slug(heading_text(source))
        for source in _heading_sources(markdown_lines(text, keep_code_spans=True))
    ]
    numbered = {
        slug if (seen := slugs[:index].count(slug)) == 0 else f"{slug}-{seen}"
        for index, slug in enumerate(slugs)
    }
    explicit = {
        match.group(1) if match.group(1) is not None else match.group(2)
        for match in _HTML_ANCHOR.finditer(text)
    }
    return frozenset(anchor.lower() for anchor in numbered | explicit)


# --- in-repo checks ----------------------------------------------------------------------------


@dataclass(frozen=True)
class Tree:
    """The git-tracked files and dirs (POSIX paths from the root) -- to look up, without the network, whether a link target exists in the GitHub tree."""

    files: frozenset[str]
    dirs: frozenset[str]

    @classmethod
    def of(cls, files: Iterable[str]) -> Tree:
        """Build from a list of files a tree that can also look up the dirs that contain them (including the root "")."""
        file_set = frozenset(files)
        dirs = frozenset(
            "" if str(parent) == "." else str(parent)
            for name in file_set
            for parent in PurePosixPath(name).parents
        )
        return cls(file_set, dirs | {""})

    def has(self, name: str) -> bool:
        """Whether name (a path from the root) is a file or dir of the tree -- the answer to whether a link target exists."""
        return name in self.files or name in self.dirs


def git_tree(root: Path) -> Tree:
    """The tree of git-tracked files under root -- so a file that exists only locally (untracked) is not treated as existing."""
    listed = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z"], capture_output=True, check=True
    ).stdout.decode("utf-8")
    return Tree.of(name for name in listed.split("\0") if name)


_OWN_GITHUB = re.compile(
    rf"^https?://github\.com/{re.escape(GITHUB_REPO)}/(?:blob|tree|raw)/{DEFAULT_BRANCH}"
    r"(?:/(?P<path>[^?#]*))?(?:\?[^#]*)?(?:#(?P<fragment>.*))?$"
)
_OWN_RAW = re.compile(
    rf"^https?://raw\.githubusercontent\.com/{re.escape(GITHUB_REPO)}/(?:refs/heads/)?{DEFAULT_BRANCH}/(?P<path>[^?#]*)"
)
_SCHEME = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")
# Line anchors of GitHub code pages (#L10, #L10-L20).
_LINE_ANCHOR = re.compile(r"^L\d+(?:C\d+)?(?:-L\d+(?:C\d+)?)?$")


class Kind(Enum):
    """The kind of link -- to decide which check (tree or network) it goes to."""

    IN_REPO = "in-repo"  # checked against the tree (relative links and GitHub links pointing at main)
    EXTERNAL = "external"  # checked over the network
    OTHER = "other"  # not checked (mailto: etc.)


@dataclass(frozen=True)
class Resolved:
    """The target of a link (tree path and anchor), or an external URL -- the thing to check after resolving the differences in how links are written."""

    kind: Kind
    path: str | None = None  # path from the root when IN_REPO (None if it points outside the tree)
    fragment: str | None = None
    url: str | None = None  # URL (before #) when EXTERNAL
    relative_path: bool = (
        False  # whether the link has a relative path (a form that breaks on a PyPI page -- links that are only `#heading` are excluded)
    )


def resolve(source: str, target: str) -> Resolved:
    """The target written in the file at source (a path from the root) as target -- to look up the same target GitHub resolves the link to."""
    if match := _OWN_GITHUB.match(target):
        path = _normalize(urllib.parse.unquote(match.group("path") or ""))
        return Resolved(Kind.IN_REPO, path, match.group("fragment"))
    if match := _OWN_RAW.match(target):
        return Resolved(Kind.IN_REPO, _normalize(urllib.parse.unquote(match.group("path"))))
    if target.startswith(("http://", "https://")):
        return Resolved(Kind.EXTERNAL, url=target.split("#", 1)[0])
    if target.startswith("//") or _SCHEME.match(target):
        return Resolved(Kind.OTHER)
    before, _, fragment = target.partition("#")
    before = urllib.parse.unquote(before.split("?", 1)[0])
    if not before:
        return Resolved(Kind.IN_REPO, source, fragment or None)
    joined = before if before.startswith("/") else posixpath.join(posixpath.dirname(source), before)
    return Resolved(Kind.IN_REPO, _normalize(joined), fragment or None, relative_path=True)


def _normalize(path: str) -> str | None:
    """The path from the root with `.`, `..`, and leading/trailing `/` resolved (the root is "") -- to compare with names in the tree. None if it goes outside the root."""
    normalized = posixpath.normpath("/" + path) if path else "/"
    if posixpath.normpath(path.lstrip("/") or ".").startswith(".."):
        return None
    return normalized.lstrip("/")


@dataclass(frozen=True)
class Problem:
    """One break in a link -- to tell the person fixing it what is broken where and why."""

    link: Link
    reason: str

    def __str__(self) -> str:
        return f"{self.link}  — {self.reason}"


def pypi_readmes() -> frozenset[str]:
    """The READMEs that become PyPI pages (paths from the root) -- decides which files get relative links stopped as a form that breaks on PyPI."""
    return frozenset(
        "README.md" if name == "doeff" else f"packages/{name}/README.md"
        for name in verify_release_install.CORE
    )


def record_dirs(root: Path) -> tuple[str, ...]:
    """Design record directories (`[tool.doeff] record-dirs` in the root pyproject.toml) -- files under them are not read, so that
    evidence as of its time is not rewritten."""
    with (root / "pyproject.toml").open("rb") as file:
        config = tomllib.load(file)
    return tuple(config.get("tool", {}).get("doeff", {}).get("record-dirs", ()))


class InRepoChecker:
    """Check in-repo links by reading the tree and markdown anchors -- keeps the anchors read per file so the same file's anchors are not read repeatedly."""

    def __init__(self, root: Path, tree: Tree, pypi: frozenset[str]) -> None:
        self._root = root
        self._tree = tree
        self._pypi = pypi
        self._anchors: dict[str, frozenset[str]] = {}

    def anchors(self, path: str) -> frozenset[str]:
        """The set of anchors of the markdown at path (keeps what was read)."""
        if path not in self._anchors:
            self._anchors[path] = anchors_of(
                (self._root / path).read_text(encoding="utf-8", errors="replace")
            )
        return self._anchors[path]

    def problems(self, link: Link) -> list[Problem]:
        """The breaks of a link (empty if none) -- one of: target not in the tree, anchor missing, or breaks on a PyPI page."""
        resolved = resolve(link.path, link.target)
        if resolved.kind is not Kind.IN_REPO:
            return []
        pypi = (
            [
                Problem(
                    link,
                    "A PyPI page does not resolve a relative path against the GitHub tree -- "
                    f"write it in the form https://github.com/{GITHUB_REPO}/blob/{DEFAULT_BRANCH}/...",
                )
            ]
            if link.path in self._pypi and resolved.relative_path
            else []
        )
        path = resolved.path
        if path is None:
            return [*pypi, Problem(link, "points outside the repo root")]
        if not self._tree.has(path):
            return [*pypi, Problem(link, f"not in the tree: {path or '/'}")]
        fragment = resolved.fragment
        anchored = (
            fragment
            and path in self._tree.files
            and path.endswith(".md")
            and not _LINE_ANCHOR.match(fragment)
        )
        anchor = urllib.parse.unquote(fragment or "").lower().removeprefix("user-content-")
        if anchored and anchor not in self.anchors(path):
            return [*pypi, Problem(link, f"heading anchor missing: {path}#{fragment}")]
        return pypi


# --- external checks ---------------------------------------------------------------------------


class Verdict(Enum):
    """The answer for one external link -- separates what counts toward the exit code (broken) from what does not (unconfirmed)."""

    OK = "ok"
    BROKEN = "broken"  # broken (404, 410, unresolvable host)
    UNCONFIRMED = "unconfirmed"  # unconfirmed (429, 5xx, 403, timeout)


@dataclass(frozen=True)
class Answer:
    """The answer from asking one URL, and its content (HTTP status or network error) -- to show why it is broken."""

    verdict: Verdict
    detail: str


def is_example_host(url: str) -> bool:
    """Whether the host of url is one written as an example -- so an example URL that gets no answer is not counted as a broken link."""
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    return (
        host in EXAMPLE_HOSTS
        or host.endswith(EXAMPLE_HOST_SUFFIXES)
        or any(host.endswith(f".{example}") for example in EXAMPLE_HOSTS)
    )


# Answers for unresolvable hosts (no such name, no address for the name). Transient errors on the name lookup path (EAI_AGAIN etc.) count as unconfirmed.
_NO_SUCH_HOST = frozenset(
    vars(socket)[name] for name in ("EAI_NONAME", "EAI_NODATA") if name in vars(socket)
)


def _ask_once(url: str, method: str) -> Answer:
    """Ask url once with method -- split the status and network error into broken and unconfirmed."""
    request = urllib.request.Request(
        url, method=method, headers={"User-Agent": USER_AGENT, "Accept": "*/*"}
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return Answer(Verdict.OK, f"{response.status}")
    except urllib.error.HTTPError as error:
        verdict = Verdict.BROKEN if error.code in (404, 410) else Verdict.UNCONFIRMED
        return Answer(verdict, f"HTTP {error.code}")
    except urllib.error.URLError as error:
        if isinstance(error.reason, socket.gaierror) and error.reason.errno in _NO_SUCH_HOST:
            return Answer(Verdict.BROKEN, f"host name cannot be resolved: {error.reason}")
        return Answer(Verdict.UNCONFIRMED, f"cannot connect: {error.reason}")
    except OSError as error:
        return Answer(Verdict.UNCONFIRMED, f"cannot connect: {error}")


def ask(url: str) -> Answer:
    """Ask url with HEAD, and if the result is not 2xx or 3xx ask again with GET (some sites refuse HEAD). Unconfirmed answers are asked again
    after a pause -- so a transient error is not reported as broken."""
    answer = Answer(Verdict.UNCONFIRMED, "not asked yet")
    for wait in (0, *RETRY_WAITS_SECONDS):
        time.sleep(wait)
        answer = _ask_once(url, "HEAD")
        answer = answer if answer.verdict is Verdict.OK else _ask_once(url, "GET")
        if answer.verdict is not Verdict.UNCONFIRMED:
            return answer
    return answer


# --- files to read and entry point ---------------------------------------------------------------


def is_default_file(name: str, records: Sequence[str]) -> bool:
    """Whether to read name (a path from the root) by default -- to narrow to files containing links a first-time visitor clicks."""
    parts = PurePosixPath(name).parts
    if any(name.startswith(record) for record in records):
        return False
    return (
        name in ROOT_FILES
        or (parts[0] == "docs" and name.endswith(".md"))
        or (len(parts) == 3 and parts[0] == "packages" and parts[2] == "README.md")
    )


def default_files(root: Path, tree: Tree) -> list[str]:
    """Files read by default (git-tracked; root files first, the rest in name order)."""
    records = record_dirs(root)
    roots = [name for name in ROOT_FILES if name in tree.files]
    rest = sorted(
        name for name in tree.files if name not in ROOT_FILES and is_default_file(name, records)
    )
    return roots + rest


def collect(root: Path, files: Sequence[str]) -> list[Link]:
    """Collect all links of files."""
    return [
        link
        for name in files
        for link in extract_links(name, (root / name).read_text(encoding="utf-8", errors="replace"))
    ]


def check_in_repo(root: Path, tree: Tree, links: Sequence[Link]) -> list[Problem]:
    """Return all breaks of in-repo links (no network)."""
    checker = InRepoChecker(root, tree, pypi_readmes())
    return [problem for link in links for problem in checker.problems(link)]


def check_external(links: Sequence[Link]) -> tuple[list[Problem], list[Problem], int]:  # noqa: DOEFF006 — small return bundle for the tool
    """Ask the external links and return (broken, unconfirmed, number of URLs asked). The same URL is asked only once."""
    urls = {
        link: resolved.url
        for link in links
        if (resolved := resolve(link.path, link.target)).kind is Kind.EXTERNAL
        and resolved.url
        and not is_example_host(resolved.url)
        and not resolved.url.startswith(GITHUB_ATTACHMENTS)
    }
    unique = sorted(set(urls.values()))
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        answers = dict(zip(unique, pool.map(ask, unique), strict=True))
    broken = [
        Problem(link, answers[url].detail)
        for link, url in urls.items()
        if answers[url].verdict is Verdict.BROKEN
    ]
    unconfirmed = [
        Problem(link, answers[url].detail)
        for link, url in urls.items()
        if answers[url].verdict is Verdict.UNCONFIRMED
    ]
    return broken, unconfirmed, len(unique)


def main(argv: Sequence[str] | None = None) -> int:
    """Command entry point -- the default is the in-repo check, `--external` is the external check."""
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n", 1)[0])
    parser.add_argument(
        "files",
        nargs="*",
        help="files to read (paths from the root). Default is README, docs, packages/*/README.md",
    )
    parser.add_argument(
        "--external", action="store_true", help="send requests to external links to check them (goes to the network)"
    )
    parser.add_argument(
        "--root", type=Path, default=REPO, help="repo root (default is the repo where this tool lives)"
    )
    args = parser.parse_args(argv)
    root: Path = args.root
    try:
        tree = git_tree(root)
    except (OSError, subprocess.CalledProcessError) as error:
        print(f"cannot read the git tree: {error}", file=sys.stderr)
        return 2
    files = list(args.files) or default_files(root, tree)
    links = collect(root, files)
    if args.external:
        broken, unconfirmed, asked = check_external(links)
        print(
            *(f"unconfirmed: {problem}" for problem in unconfirmed),
            sep="\n",
            end="\n" if unconfirmed else "",
        )
        print(
            *(f"broken: {problem}" for problem in broken), sep="\n", end="\n" if broken else ""
        )
        print(
            f"asked {asked} URLs of external links in {len(files)} files: broken {len(broken)}, "
            f"unconfirmed {len(unconfirmed)}"
        )
        return 1 if broken else 0
    problems = check_in_repo(root, tree, links)
    print(
        *(f"broken: {problem}" for problem in problems), sep="\n", end="\n" if problems else ""
    )
    in_repo = sum(1 for link in links if resolve(link.path, link.target).kind is Kind.IN_REPO)
    print(
        f"read {in_repo} in-repo links of {len(files)} files against the tree: broken {len(problems)}"
    )
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
