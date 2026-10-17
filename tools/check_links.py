"""README・docs・PyPI の頁に出る README の link が切れていない事を確かめる(取り組み doeff-publish の区切り 11)。

読む file(既定 — 初めて来た人が GitHub と PyPI で押す link の在る所): 根の README.md・CONTRIBUTING.md・CHANGELOG.md・
docs/ の下の全部の *.md・packages/*/README.md。git に載っている file だけを読む(載っていない file は GitHub に無い)。
設計の記録の dir(根の pyproject.toml の `[tool.doeff] record-dirs`)の下は読まない — 記録はその時点の証拠で、写しの
文を後から書き換えない。

link = markdown の `[文](先)`・`![文](先)`・参照の定義 `[名]: 先`・`<https://…>`・HTML の href= と src=・文の中の素の
`https://…`(GitHub と PyPI はどれも押せる link にする)。``` と ~~~ の囲み・`code`・HTML の注釈の中は読まない。

2 つの確かめ:

- repo の中(既定 — 網に出ない。CI では .github/workflows/links.yml の job in-repo が push と pull request ごとに走らせ、
  Python Compatibility の root の tests でも tests/test_check_links.py::test_repo_links_resolve_in_the_tree が走らせる):
  - 相対の link(`docs/x.md`・`../LICENSE`・`/README.md`)の先が、git に載った file か dir である事。
  - main を指す GitHub の link(`https://github.com/proboscis/doeff/blob/main/<path>`・`tree/main`・`raw/main`・
    `https://raw.githubusercontent.com/proboscis/doeff/main/<path>`)の先も、同じく木に在る事 — 網に聞かず木で読む
    (main に入る前の変更でも、入った後に切れるかが分かる)。
  - `#見出し` の付いた link は、先の markdown にその見出し(GitHub の付け方の anchor)か `id=`・`name=` の anchor が在る事。
  - PyPI の頁になる README(核の 5 個 — 定義元は tools/verify_release_install.py の CORE)に、相対の path の link が
    無い事。PyPI は相対の path を GitHub の木でなく PyPI の頁の URL に対して解くので、PyPI の頁で切れる。main を指す
    GitHub の link で書く。`#見出し` だけの link は PyPI でも働く(見出しに anchor を付け、link をそこへ向ける)。
- 外の link(`--external` — 網に出る。相手の site の都合で揺れるので push ごとには走らせず、links.yml の job external が
  週 1 回と手で起こす時に走らせる): http と https の link に要求を送り、返事が来る事。HEAD で聞き、2xx・3xx で無ければ
  GET で聞き直す。404・410 と名の引けない host は切れた link。429・5xx・403・
  時間切れは、間を置いて聞き直しても同じなら「確かめられない」として出すが、終了コードには数えない(相手の都合で揺れる)。
  main を指す GitHub の link と、localhost・example.com などの例の host は送らない。

    uv run --no-project python tools/check_links.py               # repo の中の link
    uv run --no-project python tools/check_links.py --external    # 外の link
    uv run --no-project python tools/check_links.py docs/x.md     # 渡した file だけ

終了コード: 切れた link が無ければ 0・在れば 1・道具が使えなければ(git が無いなど)2。
標準ライブラリだけを使う(workspace を同期せずに走る)。
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

# 同じ dir の module — script として走らせると、Python が script の dir を sys.path の頭に置く。
import verify_release_install

REPO = Path(__file__).resolve().parents[1]
GITHUB_REPO = "proboscis/doeff"
DEFAULT_BRANCH = "main"
# 根の決まった名の file。docs/ の下の *.md と packages/*/README.md は木から読む(default_files)。
ROOT_FILES = ("README.md", "CONTRIBUTING.md", "CHANGELOG.md")
# 例として書かれた host(送っても返事の来ない・来ても意味の無い物)。名か、その下の名なら当たる。
EXAMPLE_HOSTS = ("localhost", "127.0.0.1", "0.0.0.0", "example.com", "example.org", "example.net")
EXAMPLE_HOST_SUFFIXES = (".local", ".localhost", ".test", ".invalid", ".example", ".internal")
USER_AGENT = "Mozilla/5.0 (compatible; doeff-link-check; +https://github.com/proboscis/doeff)"
TIMEOUT_SECONDS = 20
RETRY_WAITS_SECONDS = (2, 8)
WORKERS = 8


# --- link を集める ------------------------------------------------------------------------------


@dataclass(frozen=True)
class Link:
    """1 つの file の 1 つの link。切れた時に、どの file の何行目かを名指すため。"""

    path: str  # 根からの path(POSIX)
    line: int
    target: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.target}"


_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
_CODE_SPAN = re.compile(r"(`+)(?:(?!\1).)+?\1")
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
# 参照の定義 `[名]: 先`。脚注の定義 `[^名]: 文` は link でない。
_REFERENCE_DEFINITION = re.compile(r"^ {0,3}\[(?!\^)[^\]]+\]:\s*<?([^\s>]+)>?")
_AUTOLINK = re.compile(r"<(https?://[^\s>]+)>")
_HTML_ATTRIBUTE = re.compile(r"""\b(?:href|src)\s*=\s*(?:"([^"]*)"|'([^']*)')""", re.IGNORECASE)
_BARE_URL = re.compile(r"https?://[^\s<>()\[\]\"'`]+")
# 素の URL の末尾の句読点は link に含めない(GitHub の autolink と同じ)。
_TRAILING_PUNCTUATION = ".,:;!?*_~'\""
# `](先` の後に来てよい物: 空白と題(`"…"`・`'…'`・`(…)`)と閉じの `)`。他の物が来れば link でない(`[0](frame だけ)`)。
_LINK_TAIL = re.compile(r"""\s*(?:\s(?:"[^"]*"|'[^']*'|\([^)]*\))\s*)?\)""")


def _blank(match: re.Match[str]) -> str:
    """一致した所を同じ数の改行と空白に置き換える — 消した後も行の番号と桁を元の file と揃えるため。"""
    return re.sub(r"[^\n]", " ", match.group(0))


def _outside_fences(lines: Sequence[str]) -> Iterator[str]:
    """``` と ~~~ の囲みの中の行を空にした行の列 — 囲みの中の code を link や見出しと読まないため。"""
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
    """囲みと HTML の注釈を空にした行の列(行の番号は元の file と同じ)。link を集める時は `code` も空にし、見出しの
    anchor を作る時は残す(GitHub は見出しの `code` の中の字も anchor に入れる)。"""
    lines = _outside_fences(_HTML_COMMENT.sub(_blank, text).split("\n"))
    return [line if keep_code_spans else _CODE_SPAN.sub(_blank, line) for line in lines]


def _inline_targets(line: str) -> Iterator[tuple[int, str]]:
    """`](先)` の先と、その始まりの桁 — markdown の inline の link と画像の先を拾うため。先の中の括弧は対になる分だけ
    含める(`Foo_(bar)` の形の URL)。"""
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
    """position から始まる link の先の終わりの桁(空白か、対にならない `)` の前)— 先の中の括弧の対を数えるため。"""
    depth = 0
    for end in range(position, len(line)):
        char = line[end]
        if char.isspace() or (char == ")" and depth == 0):
            return end
        depth += {"(": 1, ")": -1}.get(char, 0)
    return len(line)


def _targets_in_line(line: str) -> list[tuple[int, str]]:
    """1 行の link の (桁, 先) — 種類ごとの拾い方を 1 つの列にするため。素の URL は、他の link の中に在る物を除く。"""
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
    """text(path の file の中身)の link を出た順に返す — 確かめる対象を file と行で名指すため。同じ行の同じ先は 1 つ。"""
    return [
        Link(path, number, target)
        for number, line in enumerate(markdown_lines(text), start=1)
        for target in dict.fromkeys(
            html.unescape(target.strip()) for _, target in _targets_in_line(line)
        )
        if target
    ]


# --- 見出しの anchor ------------------------------------------------------------------------------

_ATX_HEADING = re.compile(r"^ {0,3}#{1,6}(?:[ \t]+(.*?))?(?:[ \t]+#+)?[ \t]*$")
_SETEXT_UNDERLINE = re.compile(r"^ {0,3}(=+|-+)\s*$")
_HTML_ANCHOR = re.compile(r"""\b(?:id|name)\s*=\s*(?:"([^"]+)"|'([^']+)')""", re.IGNORECASE)
_INLINE_IMAGE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_INLINE_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)|\[([^\]]*)\]\[[^\]]*\]")
_HTML_TAG = re.compile(r"<[^>]+>")
_EMPHASIS = re.compile(r"(?<!\w)[*_]+|[*_]+(?!\w)|\*+")


def heading_text(source: str) -> str:
    """見出しの markdown を GitHub が描く文字に近づける(印・link・画像・HTML の tag を外す)— anchor を作る元にするため。"""
    text = source.replace("`", "")
    text = _INLINE_IMAGE.sub("", text)
    text = _INLINE_LINK.sub(
        lambda match: match.group(1) if match.group(1) is not None else match.group(2), text
    )
    text = _HTML_TAG.sub("", text)
    text = _EMPHASIS.sub("", text)
    return html.unescape(text).strip()


def github_slug(text: str) -> str:
    """GitHub が見出しに付ける anchor(github-slugger と同じ: 小文字にし、字・数字・結合の印・`-`・`_`・空白の外を消し、
    空白を `-` にする)— `#見出し` の link の先が在るかを、GitHub の描き方で確かめるため。"""
    kept = "".join(
        char
        for char in text.lower()
        if char in "-_ " or unicodedata.category(char)[0] in ("L", "N", "M")
    )
    return kept.replace(" ", "-")


def _heading_sources(lines: Sequence[str]) -> Iterator[str]:
    """見出し(`#` の形と、下に `===`・`---` を引く形)の markdown の列 — anchor を作る見出しを出た順に拾うため。"""
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
    """markdown の text の anchor の組(小文字)— `#見出し` の link の先を引くため。見出しの anchor は、同じ物の 2 つ目から
    `-1`・`-2` を付ける(GitHub と同じ)。HTML の `id=`・`name=` も anchor。"""
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


# --- repo の中の確かめ ----------------------------------------------------------------------------


@dataclass(frozen=True)
class Tree:
    """git に載った file と dir(根からの POSIX の path)— link の先が GitHub の木に在るかを、網に出ずに引くため。"""

    files: frozenset[str]
    dirs: frozenset[str]

    @classmethod
    def of(cls, files: Iterable[str]) -> Tree:
        """file の列から、その file を持つ dir(根の "" を含む)も引ける木を作る。"""
        file_set = frozenset(files)
        dirs = frozenset(
            "" if str(parent) == "." else str(parent)
            for name in file_set
            for parent in PurePosixPath(name).parents
        )
        return cls(file_set, dirs | {""})

    def has(self, name: str) -> bool:
        """name(根からの path)が木の file か dir か — link の先が在るかの答え。"""
        return name in self.files or name in self.dirs


def git_tree(root: Path) -> Tree:
    """root の git に載った file の木 — 手元にだけ在る file(git に載っていない)を、在ると読まないため。"""
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
# GitHub の code の頁の行の anchor(#L10・#L10-L20)。
_LINE_ANCHOR = re.compile(r"^L\d+(?:C\d+)?(?:-L\d+(?:C\d+)?)?$")


class Kind(Enum):
    """link の種類 — どの確かめ(木か網か)に回すかを決めるため。"""

    IN_REPO = "in-repo"  # 木で確かめる(相対の link と main を指す GitHub の link)
    EXTERNAL = "external"  # 網で確かめる
    OTHER = "other"  # 確かめない(mailto: など)


@dataclass(frozen=True)
class Resolved:
    """link の先(木の path と anchor)か、外の URL — link の書き方の違いを解いた後の、確かめる物。"""

    kind: Kind
    path: str | None = None  # IN_REPO の時の根からの path(木の外を指せば None)
    fragment: str | None = None
    url: str | None = None  # EXTERNAL の時の URL(# より前)
    relative_path: bool = (
        False  # 相対の path を持つ link か(PyPI の頁で切れる形 — `#見出し` だけの link は含めない)
    )


def resolve(source: str, target: str) -> Resolved:
    """source(根からの path)の file に書かれた target の先 — GitHub が link を解くのと同じ先を引くため。"""
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
    """`.`・`..`・頭と末尾の `/` を解いた根からの path(根は "")— 木の名と比べるため。根の外へ出れば None。"""
    normalized = posixpath.normpath("/" + path) if path else "/"
    if posixpath.normpath(path.lstrip("/") or ".").startswith(".."):
        return None
    return normalized.lstrip("/")


@dataclass(frozen=True)
class Problem:
    """1 つの link の切れ目 — 直す人に、どこの何がなぜ切れているかを言うため。"""

    link: Link
    reason: str

    def __str__(self) -> str:
        return f"{self.link}  — {self.reason}"


def pypi_readmes() -> frozenset[str]:
    """PyPI の頁になる README(根からの path)— 相対の link を PyPI で切れる形として止める file を決めるため。"""
    return frozenset(
        "README.md" if name == "doeff" else f"packages/{name}/README.md"
        for name in verify_release_install.CORE
    )


def record_dirs(root: Path) -> tuple[str, ...]:
    """設計の記録の dir(根の pyproject.toml の `[tool.doeff] record-dirs`)— その時点の証拠を書き換えさせないため、
    その下の file を読まない。"""
    with (root / "pyproject.toml").open("rb") as file:
        config = tomllib.load(file)
    return tuple(config.get("tool", {}).get("doeff", {}).get("record-dirs", ()))


class InRepoChecker:
    """木と markdown の anchor を読んで、repo の中の link を確かめる — 同じ file の anchor を何度も読まないため、
    読んだ anchor を file ごとに持つ。"""

    def __init__(self, root: Path, tree: Tree, pypi: frozenset[str]) -> None:
        self._root = root
        self._tree = tree
        self._pypi = pypi
        self._anchors: dict[str, frozenset[str]] = {}

    def anchors(self, path: str) -> frozenset[str]:
        """path の markdown の anchor の組(読んだ物は持っておく)。"""
        if path not in self._anchors:
            self._anchors[path] = anchors_of(
                (self._root / path).read_text(encoding="utf-8", errors="replace")
            )
        return self._anchors[path]

    def problems(self, link: Link) -> list[Problem]:
        """link の切れ目(無ければ空)— 先が木に無い・anchor が無い・PyPI の頁で切れる、のどれか。"""
        resolved = resolve(link.path, link.target)
        if resolved.kind is not Kind.IN_REPO:
            return []
        pypi = (
            [
                Problem(
                    link,
                    "PyPI の頁は相対の path を GitHub の木で解かない — "
                    f"https://github.com/{GITHUB_REPO}/blob/{DEFAULT_BRANCH}/… の形で書く",
                )
            ]
            if link.path in self._pypi and resolved.relative_path
            else []
        )
        path = resolved.path
        if path is None:
            return [*pypi, Problem(link, "repo の根の外を指す")]
        if not self._tree.has(path):
            return [*pypi, Problem(link, f"木に無い: {path or '/'}")]
        fragment = resolved.fragment
        anchored = (
            fragment
            and path in self._tree.files
            and path.endswith(".md")
            and not _LINE_ANCHOR.match(fragment)
        )
        anchor = urllib.parse.unquote(fragment or "").lower().removeprefix("user-content-")
        if anchored and anchor not in self.anchors(path):
            return [*pypi, Problem(link, f"見出しの anchor が無い: {path}#{fragment}")]
        return pypi


# --- 外の確かめ ---------------------------------------------------------------------------------


class Verdict(Enum):
    """外の link の 1 つの答え — 終了コードに数える物(切れている)と数えない物(確かめられない)を分けるため。"""

    OK = "ok"
    BROKEN = "broken"  # 切れている(404・410・名の引けない host)
    UNCONFIRMED = "unconfirmed"  # 確かめられない(429・5xx・403・時間切れ)


@dataclass(frozen=True)
class Answer:
    """1 つの URL に聞いた答えと、その中身(HTTP の番号や網の誤り)— 切れた訳を出すため。"""

    verdict: Verdict
    detail: str


def is_example_host(url: str) -> bool:
    """url の host が例として書かれた物か — 返事の来ない例の URL を切れた link と数えないため。"""
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    return (
        host in EXAMPLE_HOSTS
        or host.endswith(EXAMPLE_HOST_SUFFIXES)
        or any(host.endswith(f".{example}") for example in EXAMPLE_HOSTS)
    )


# 名の引けない host の答え(名が無い・名に番地が無い)。名を引く道の一時の誤り(EAI_AGAIN など)は確かめられないに数える。
_NO_SUCH_HOST = frozenset(
    vars(socket)[name] for name in ("EAI_NONAME", "EAI_NODATA") if name in vars(socket)
)


def _ask_once(url: str, method: str) -> Answer:
    """url に method で 1 度聞く — 返事の番号と網の誤りを、切れている・確かめられないに分けるため。"""
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
            return Answer(Verdict.BROKEN, f"host の名が引けない: {error.reason}")
        return Answer(Verdict.UNCONFIRMED, f"つながらない: {error.reason}")
    except OSError as error:
        return Answer(Verdict.UNCONFIRMED, f"つながらない: {error}")


def ask(url: str) -> Answer:
    """url に HEAD で聞き、2xx・3xx で無ければ GET で聞き直す(HEAD を断る site が在る)。確かめられない答えは、間を
    置いて聞き直す — 一時の誤りで切れたと言わないため。"""
    answer = Answer(Verdict.UNCONFIRMED, "未だ聞いていない")
    for wait in (0, *RETRY_WAITS_SECONDS):
        time.sleep(wait)
        answer = _ask_once(url, "HEAD")
        answer = answer if answer.verdict is Verdict.OK else _ask_once(url, "GET")
        if answer.verdict is not Verdict.UNCONFIRMED:
            return answer
    return answer


# --- 読む file と入口 ----------------------------------------------------------------------------


def is_default_file(name: str, records: Sequence[str]) -> bool:
    """name(根からの path)を既定で読むか — 初めて来た人が押す link の在る file に絞るため。"""
    parts = PurePosixPath(name).parts
    if any(name.startswith(record) for record in records):
        return False
    return (
        name in ROOT_FILES
        or (parts[0] == "docs" and name.endswith(".md"))
        or (len(parts) == 3 and parts[0] == "packages" and parts[2] == "README.md")
    )


def default_files(root: Path, tree: Tree) -> list[str]:
    """既定で読む file(git に載った物・根の file が先、残りは名の順)。"""
    records = record_dirs(root)
    roots = [name for name in ROOT_FILES if name in tree.files]
    rest = sorted(
        name for name in tree.files if name not in ROOT_FILES and is_default_file(name, records)
    )
    return roots + rest


def collect(root: Path, files: Sequence[str]) -> list[Link]:
    """files の link を全部集める。"""
    return [
        link
        for name in files
        for link in extract_links(name, (root / name).read_text(encoding="utf-8", errors="replace"))
    ]


def check_in_repo(root: Path, tree: Tree, links: Sequence[Link]) -> list[Problem]:
    """repo の中の link の切れ目を全部返す(網に出ない)。"""
    checker = InRepoChecker(root, tree, pypi_readmes())
    return [problem for link in links for problem in checker.problems(link)]


def check_external(links: Sequence[Link]) -> tuple[list[Problem], list[Problem], int]:
    """外の link に聞いて、(切れた物・確かめられない物・聞いた URL の数)を返す。同じ URL は 1 度だけ聞く。"""
    urls = {
        link: resolved.url
        for link in links
        if (resolved := resolve(link.path, link.target)).kind is Kind.EXTERNAL
        and resolved.url
        and not is_example_host(resolved.url)
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
    """命令の入口 — 既定は repo の中の確かめ、`--external` は外の確かめ。"""
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n", 1)[0])
    parser.add_argument(
        "files",
        nargs="*",
        help="読む file(根からの path)。既定は README・docs・packages/*/README.md",
    )
    parser.add_argument(
        "--external", action="store_true", help="外の link に要求を送って確かめる(網に出る)"
    )
    parser.add_argument(
        "--root", type=Path, default=REPO, help="repo の根(既定はこの道具の在る repo)"
    )
    args = parser.parse_args(argv)
    root: Path = args.root
    try:
        tree = git_tree(root)
    except (OSError, subprocess.CalledProcessError) as error:
        print(f"git の木を読めない: {error}", file=sys.stderr)
        return 2
    files = list(args.files) or default_files(root, tree)
    links = collect(root, files)
    if args.external:
        broken, unconfirmed, asked = check_external(links)
        print(
            *(f"確かめられない: {problem}" for problem in unconfirmed),
            sep="\n",
            end="\n" if unconfirmed else "",
        )
        print(
            *(f"切れている: {problem}" for problem in broken), sep="\n", end="\n" if broken else ""
        )
        print(
            f"{len(files)} 個の file の外の link の URL {asked} 個に聞いた: 切れている {len(broken)}・"
            f"確かめられない {len(unconfirmed)}"
        )
        return 1 if broken else 0
    problems = check_in_repo(root, tree, links)
    print(
        *(f"切れている: {problem}" for problem in problems), sep="\n", end="\n" if problems else ""
    )
    in_repo = sum(1 for link in links if resolve(link.path, link.target).kind is Kind.IN_REPO)
    print(
        f"{len(files)} 個の file の repo の中の link {in_repo} 個を木で読んだ: 切れている {len(problems)}"
    )
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
