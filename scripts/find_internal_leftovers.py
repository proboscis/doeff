#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""List what the maintainer's private tooling left in a doeff tree, in six kinds.

doeff is public, but its tree still carries records written for private tooling: tracker ids,
names of private services and machines, provenance lines, raw verification logs. This script finds
them file by file and line by line so that the work that deletes or rewrites them starts from one
list. It only reads: it changes no file, and its exit status is 0 whatever it finds.

The six kinds:

1. quote / provenance - a speaker word (operator, maintainer, 利用者, 持ち主, ...) on a line with a
   time, an opening quote or a decision word; and the short ``decision <date>`` mark.
2. tracker id - ids of the private tracker (c-, lt-, ci-, m-, idea-, ob-, ki-, ms-) and issue
   numbers of private repositories. Ids inside a file's path are listed apart as kind ``2p``.
3. internal name - names of the private tooling, its repositories and machines, workspace paths of
   its agents, names of its conversations, and mentions of an employer.
4. personal / secret - user names of a person's machine in home paths, email addresses, private
   network addresses, and token or key shapes.
5. Japanese-only note - a Japanese comment line (in code, or an HTML comment in docs) that also
   carries a kind 1-3 mark, and a Japanese or personal TODO. READMEs and the Japanese articles
   under publications/ are written in Japanese on purpose and are left out.
6. leftover file - files that neither users nor contributors need: raw verification records, old
   session records, build output, tool state, docs pages outside the site, drafts, empty files.

Values are not echoed for kinds 1 and 4: kind 1 prints only the speaker word and the time (never
the quoted words), kind 4 prints a masked form. Hits listed in ``KEEP`` (test fakes, example
addresses, measurement logs a README relies on) are marked as kept and left out of the counts.

Usage::

    uv run --script scripts/find_internal_leftovers.py [ROOT] [--table FILE] [--format tsv|md]
        [--top N] [--jobs N]

The summary (counts per kind, per sub-kind, per area and per directory) is written to stdout as
markdown. ``--table FILE`` writes every hit (kind, sub-kind, file, line, matched part, kept) to
FILE, or to stdout with ``-``. Files are the tracked files of ROOT when ROOT is a git work tree,
and every file under ROOT otherwise; they are read in ``--jobs`` processes. Standard library only.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from bisect import bisect_right
from collections import Counter
from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from functools import partial
from itertools import groupby
from pathlib import Path

SELF = "scripts/find_internal_leftovers.py"

# ---- what is not read -------------------------------------------------------------------------

# Files that must spell the shapes out in order to define or test them.
SHAPE_DEFINITIONS = frozenset(
    {
        SELF,
        "scripts/check_no_operator_quotes.py",
        "tests/misc/test_check_no_operator_quotes.py",
    }
)
# Directories never walked (only matters when ROOT is not a git work tree, or one is tracked).
EXCLUDED_DIRS = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".mypy_cache",
        ".ruff_cache",
        ".pytest_cache",
    }
)
# Generated lock files: their content is not read.
EXCLUDED_NAMES = frozenset({"uv.lock", "Cargo.lock", "package-lock.json", "pnpm-lock.yaml"})
# Images, media, fonts and archives: their content is not read (kind 6 path rules still apply).
MEDIA_SUFFIXES = frozenset(
    {
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".ico",
        ".icns",
        ".bmp",
        ".svg",
        ".pdf",
        ".mp4",
        ".webm",
        ".mov",
        ".mp3",
        ".wav",
        ".woff",
        ".woff2",
        ".ttf",
        ".otf",
        ".zip",
        ".gz",
        ".jar",
        ".whl",
    }
)


@dataclass(frozen=True)
class Shape:
    """A named pattern: a sub-kind of hits, or an area of the tree."""

    name: str
    pattern: re.Pattern[str]


# ---- kind 1: quote / provenance ---------------------------------------------------------------

SPEAKER = (
    r"(?:operator|Operator|OPERATOR|maintainer|Maintainer|利用者|ユーザー|オペレーター|持ち主)"
)
DATE = (
    r"(?:20\d{2}-\d{2}-\d{2}"
    r"|(?<![\d\-/.])\d{1,2}-\d{2}(?![\d\-/.])"
    r"|(?<![\d:])\d{1,2}:[0-5x][0-9x](?!\d))"
)
# An opening quote: Japanese brackets, a curly quote, or a straight quote that opens a span (after a
# space, a colon or a bracket, ASCII or full-width) and is followed by quoted text.
OPEN = r"(?:「|『|“|(?:(?<=[\s:\uff1a(\uff08])|^)\"(?=[^\s,)\]};\"]))"
QUOTED = r"[^」』”\"\n]"
GAP = r"[^「『“\"\n]"
DATED_QUOTE = (
    re.compile(SPEAKER + GAP + r"{0,80}?" + DATE + GAP + r"{0,60}?" + OPEN + QUOTED + r"{2}"),
    re.compile(DATE + GAP + r"{0,40}?" + SPEAKER + GAP + r"{0,60}?" + OPEN + QUOTED + r"{2}"),
)
SPEAKER_QUOTE = re.compile(SPEAKER + GAP + r"{0,20}?" + OPEN + QUOTED + r"{2,}")
DATED_SOURCE = (
    re.compile(SPEAKER + r"[^\n]{0,40}?" + DATE),
    re.compile(DATE + r"[^\n]{0,30}?" + SPEAKER),
)
VERB_SOURCE = re.compile(
    SPEAKER
    + r"\s*の?\s*(?:決定|裁定|指示|判断|答え|要望|依頼|directive|decision|ruling|verdict|request)"
)
SHORT_MARK = re.compile(r"(?:決定|裁定|decision|directive|ruling)\s*20\d{2}-\d{2}-\d{2}")
SPEAKER_WORD = re.compile(SPEAKER)
DATE_WORD = re.compile(DATE)

# ---- kind 2: tracker id -----------------------------------------------------------------------

ULID = r"[0-9A-HJKMNP-TV-Z]{26}"
PRIVATE_REPOS = (
    "agora-redesign",
    "agora-controllers",
    "merge-queue",
    "code-quality",
    "agent-hooks",
    "agent-commons",
    "proboscis-ema",
    "jev-proxy",
    "jevstd",
    "moneta",
    "custody",
)
TRACKER_IDS: tuple[Shape, ...] = (
    Shape("ci- (said)", re.compile(r"(?<![A-Za-z0-9])ci-" + ULID + r"(?![A-Za-z0-9])")),
    Shape("lt- (request)", re.compile(r"(?<![A-Za-z0-9])lt-" + ULID + r"(?![A-Za-z0-9])")),
    Shape("c- (conversation)", re.compile(r"(?<![A-Za-z0-9-])c-" + ULID + r"(?![A-Za-z0-9])")),
    Shape("m- (member)", re.compile(r"(?<![A-Za-z0-9-])m-" + ULID + r"(?![A-Za-z0-9])")),
    Shape(
        "idea- / ob- (record)",
        re.compile(r"(?<![A-Za-z0-9])(?:idea-" + ULID + r"|ob-[0-9a-f]{16})(?![A-Za-z0-9])"),
    ),
    Shape("ki- (card)", re.compile(r"(?<![A-Za-z0-9])ki-[0-9a-f]{12}(?![A-Za-z0-9])")),
    Shape("ms- (milestone)", re.compile(r"(?<![A-Za-z0-9])ms-[0-9a-f]{12}(?![A-Za-z0-9])")),
)
PRIVATE_ISSUE = re.compile(r"(?:" + "|".join(PRIVATE_REPOS) + r")\s*#\d+")
ANY_TRACKER_ID = re.compile("|".join(s.pattern.pattern for s in TRACKER_IDS))

# ---- kind 3: internal name --------------------------------------------------------------------


TOOLING_WORDS = (
    "agora",
    "kanban",
    "card-scheduler",
    "card scheduler",
    "rulebook",
    "jevrule",
    "long-command",
    "image-build",
    "agent-toolset-sync",
    "daily-verify",
    "verify-worker",
    "land-queue",
    "ai land",
    "ai worktree",
    "turn-host",
    "chatgpt work",
    "acp:kanban-issue",
    "acp:kanban-milestone",
    "acp:kanban-board",
)
REPOSITORY_WORDS = (*PRIVATE_REPOS[:-1], "doeff-publish", "hypha", "orch")
MACHINE_WORDS = ("zeus", "atlas", "hermes", "doeff-worker")


def words(ws: tuple[str, ...]) -> re.Pattern[str]:
    """Match any of ``ws`` as a whole word, in any case (a hyphen does not end a word)."""
    return re.compile(
        r"(?<![\w-])(?:" + "|".join(map(re.escape, ws)) + r")(?![\w-])", re.IGNORECASE
    )


ROLE_NAMES = re.compile(r"秘書の会話|見回り|配備の担当|持ち主の会話|受付の会話|担当の会話|依頼書")
WORKSPACE_PATH = re.compile(
    r"/work/agent-workspaces|/work/toolset|/memory-scratch|~/\.worktrees|~/repos/"
    r"|\.credential-homes"
)
EMPLOYER = re.compile(r"会社|社内|employer|corporate|勤め先")
INTERNAL_NAMES: tuple[Shape, ...] = (
    Shape("private tooling", words(TOOLING_WORDS)),
    Shape("conversation role (Japanese)", ROLE_NAMES),
    Shape("private repository / initiative", words(REPOSITORY_WORDS)),
    Shape("machine / pod", words(MACHINE_WORDS)),
    Shape("agent workspace path", WORKSPACE_PATH),
    Shape("employer", EMPLOYER),
)

# ---- kind 4: personal / secret ----------------------------------------------------------------

HOME_PATH = re.compile(r"(?:/Users/|/home/|[A-Za-z]:\\{1,2}Users\\{1,2})([A-Za-z][\w.-]*)")
# Account names of containers, CI runners and documentation examples, not of a person.
SERVICE_USERS = frozenset(
    {
        "ubuntu",
        "runner",
        "runneradmin",
        "user",
        "users",
        "me",
        "username",
        "operator",
        "someone",
        "doeff",
        "agent",
        "agora",
        "node",
        "worker",
        "app",
        "root",
        "dev",
        "you",
        "name",
    }
)
EMAIL = re.compile(
    r"(?<![\w.+@/\\-])([A-Za-z][\w.+-]*)@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,})"
)
SAFE_EMAIL_DOMAIN = re.compile(
    r"(?i)^(?:example\.|users\.noreply\.github\.com|noreply\.|anthropic\.com|localhost|test\.|"
    r"domain\.|email\.|company\.|host)"
)
SAFE_EMAIL_LOCAL = re.compile(
    r"(?i)^(?:noreply|no-reply|git|bot|test|user|foo|me|you|example|someone|admin)$"
)
PRIVATE_ADDRESS = re.compile(
    r"(?<![\d.])(?:192\.168\.\d{1,3}\.\d{1,3}|100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d{1,3}\.\d{1,3}"
    r"|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})(?![\d.])|[\w-]+\.ts\.net"
)
TOKEN = re.compile(
    r"ghp_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{40,}|gh[ousr]_[A-Za-z0-9]{36}"
    r"|sk-ant-[A-Za-z0-9_\-]{20,}|sk-(?:proj-)?[A-Za-z0-9]{32,}|AKIA[0-9A-Z]{16}"
    r"|AIza[0-9A-Za-z\-_]{35}|xox[baprs]-[A-Za-z0-9-]{10,}|hf_[A-Za-z0-9]{30,}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----"
)

# ---- kind 5: Japanese-only note ---------------------------------------------------------------

JAPANESE = re.compile(r"[぀-ヿ一-鿿]")
CODE_SUFFIXES = frozenset(
    {
        ".py",
        ".pyi",
        ".hy",
        ".hyk",
        ".hyp",
        ".rs",
        ".ts",
        ".tsx",
        ".js",
        ".mjs",
        ".cjs",
        ".toml",
        ".yaml",
        ".yml",
        ".sh",
        ".cfg",
        ".ini",
        ".kt",
        ".java",
        ".lua",
        ".el",
        ".vim",
        ".sql",
        ".mk",
    }
)
CODE_NAMES = frozenset({"Makefile", ".gitignore", ".semgrepignore"})
PROSE_SUFFIXES = frozenset({".md", ".txt", ".rst", ".html", ".mdx"})
COMMENT_START = ("#", ";", "//", "/*", "*", "--", "<!--")
INLINE_COMMENT = re.compile(r"(?:\s#|\s;|\s//)[^\n]*" + JAPANESE.pattern)
PROSE_COMMENT = re.compile(r"<!--[^\n]*" + JAPANESE.pattern)
TODO = re.compile(r"\b(?:TODO|FIXME|XXX|HACK)\b")
PERSONAL_TODO = re.compile(r"あとで|後で|要確認|メモ|自分|覚え|TODO\((?:me|[a-z]+)\)|忘れ")

# A line can hit kind 1-5 only if a trigger matches on it (kind 5 needs a kind 1-3 mark or a TODO).
# Triggers are looser than the kinds' patterns but have no leading lookbehind and no IGNORECASE,
# so the regex engine can skip ahead; the case-free words run on the lower-cased text instead.
CASED_TRIGGERS: tuple[re.Pattern[str], ...] = (
    SPEAKER_WORD,
    SHORT_MARK,
    re.compile(r"-[0-9A-HJKMNP-TV-Z]{26}|-[0-9a-f]{12}"),
    ROLE_NAMES,
    WORKSPACE_PATH,
    EMPLOYER,
    re.compile(r"Users|/home/|@|192\.168\.|100\.\d|172\.\d|\.ts\.net"),
    TOKEN,
    re.compile(r"TODO|FIXME|XXX|HACK"),
)
FOLDED_TRIGGER = re.compile(
    "|".join(map(re.escape, (*TOOLING_WORDS, *PRIVATE_REPOS, *REPOSITORY_WORDS, *MACHINE_WORDS)))
)
# Prose written in Japanese on purpose; its lines are not kind 5 notes.
JAPANESE_PROSE_PATHS = re.compile(r"(?:^|/)README[^/]*$|^publications/")

# ---- kind 6: leftover file --------------------------------------------------------------------

LEFTOVER_NAME = re.compile(
    r"(?:\.bak|\.orig|\.tmp|\.swp|~|\.rej|\.DS_Store|\.pyc)$|(?:^|/)__pycache__/"
    r"|(?:^|/)(?:tmp|temp|scratch|draft|drafts|old|backup|wip|untitled|copy)(?:[._/-]|$)"
)
EMPTY_ALLOWED = frozenset({"__init__.py", "py.typed", ".gitkeep", ".nojekyll"})
# Leftovers by path, matched from the start of the path; the first match names the file.
LEFTOVER_PATHS: tuple[Shape, ...] = (
    Shape("verification record (docs/design-checks)", re.compile(r"docs/design-checks/")),
    Shape("verification record (docs/design)", re.compile(r"docs/design/")),
    Shape("old session record (docs/history)", re.compile(r"docs/history/")),
    Shape("build output", re.compile(r"ide-plugins/pycharm/bin/")),
    Shape("tool state", re.compile(r"\.serena/")),
    Shape(
        "article production record",
        re.compile(r"publications/[^/]+/(?:reviews/|zenn-check/|TODO\.md$|completion-audit\.md$)"),
    ),
)

# ---- hits the inventory decided to keep -------------------------------------------------------


@dataclass(frozen=True)
class Keep:
    """Hits of ``sub`` under ``prefix`` stay in the table but are left out of the counts."""

    prefix: str
    kind: str
    sub: str
    why: str


KEEP: tuple[Keep, ...] = (
    Keep("docs/03-basic-effects.md", "4", "private address", "an example address in a guide"),
    Keep("tools/docs-video/", "4", "private address", "an example address in a guide video"),
    Keep("tools/docs-video/", "6", "draft or log name", "the README names these logs as data"),
    Keep("packages/doeff-agents/tests/test_headless_adapter.hy", "4", "token shape", "test fake"),
    Keep("packages/doeff-cluster/tests/test_supplied_held.hy", "4", "token shape", "test fake"),
    Keep("packages/doeff-cluster/tests/test_supplied_values.hy", "4", "token shape", "test fake"),
    Keep(
        "packages/doeff-hy/src/doeff_hy_bytecode_guard/_macro_use.py",
        "1",
        "quote after a speaker (no time)",
        "code: Python's operator module",
    ),
    Keep(
        "packages/doeff-validation/tests/test_validate.py",
        "1",
        "quote after a speaker (no time)",
        "code: Python's operator module",
    ),
    Keep(
        "packages/doeff-records/src/doeff_records/_laws.hy",
        "1",
        "quote after a speaker (no time)",
        "code: the operator-paths field of a records table",
    ),
    Keep(
        "packages/doeff-records/src/doeff_records/_values.hy",
        "1",
        "quote after a speaker (no time)",
        "code: the operator-paths field of a records table",
    ),
    Keep(
        "packages/doeff-records/tests/test_admission.hy",
        "1",
        "quote after a speaker (no time)",
        "code: the operators field of a records schema",
    ),
)


@dataclass(frozen=True)
class Kind:
    """A kind of the summary; ``by_file`` kinds are judged per file and have no line count."""

    key: str
    title: str
    by_file: bool = False


KINDS: tuple[Kind, ...] = (
    Kind("1", "1 quote / provenance"),
    Kind("2", "2 tracker id"),
    Kind("2p", "2p tracker id in a path", by_file=True),
    Kind("3", "3 internal name"),
    Kind("4", "4 personal / secret"),
    Kind("5", "5 Japanese-only note"),
    Kind("6", "6 leftover file", by_file=True),
)
# The six kinds of the inventory, without the path ids listed apart.
SIX = tuple(k for k in KINDS if k.key != "2p")


@dataclass(frozen=True, order=True)
class Hit:
    """One finding: a line (``line`` >= 1) or a whole file (``line`` == 0)."""

    file: str
    kind: str
    sub: str
    line: int
    match: str
    kept: str = ""


# ---- reading the tree -------------------------------------------------------------------------


def tree_files(root: Path) -> list[str]:
    """Return the files to look at, as POSIX paths relative to ``root``."""
    listed = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z"], capture_output=True, check=False
    )
    names = (
        [n for n in listed.stdout.decode("utf-8", "replace").split("\0") if n]
        if listed.returncode == 0
        else list(walk(root))
    )
    return sorted(n for n in names if not set(n.split("/")[:-1]) & EXCLUDED_DIRS)


def walk(root: Path) -> Iterator[str]:
    """Yield every file under ``root`` outside the excluded directories."""
    for here, dirs, names in os.walk(root):
        dirs[:] = [d for d in dirs if d not in EXCLUDED_DIRS]
        yield from ((Path(here) / n).relative_to(root).as_posix() for n in names)


def read_text(root: Path, rel: str) -> str | None:
    """Return the text of a file, or None for a file whose content is not read."""
    path = root / rel
    name = rel.rsplit("/", 1)[-1]
    if (
        rel in SHAPE_DEFINITIONS
        or name in EXCLUDED_NAMES
        or suffix(rel).lower() in MEDIA_SUFFIXES
        or path.is_symlink()
        or not path.is_file()
    ):
        return None
    data = path.read_bytes()
    return None if b"\0" in data[:8192] else data.decode("utf-8", "replace")


def suffix(rel: str) -> str:
    """Return the file's suffix (``Makefile`` counts as ``.mk``)."""
    name = rel.rsplit("/", 1)[-1]
    if name == "Makefile":
        return ".mk"
    return "." + name.rsplit(".", 1)[-1] if "." in name else ""


def site_pages(root: Path) -> tuple[str, ...]:
    """Return the docs paths that mkdocs.yml lets into the site (its ``exclude_docs`` ``!/`` lines)."""
    config = root / "mkdocs.yml"
    if not config.is_file():
        return ()
    block = re.search(r"^exclude_docs:\s*\|\n((?:[ \t]+[^\n]*\n|\n)*)", config.read_text(), re.M)
    if block is None:
        return ()
    return tuple(
        "docs/" + entry.strip()[2:]
        for entry in block.group(1).splitlines()
        if entry.strip().startswith("!/")
    )


# ---- matching ---------------------------------------------------------------------------------


def first(pattern: re.Pattern[str], line: str) -> str:
    """Return the first match of ``pattern`` on ``line``, or ``""``."""
    found = pattern.search(line)
    return found.group(0) if found else ""


def who_when(line: str) -> str:
    """Return the speaker word and the time of a kind 1 line, never the quoted words."""
    return " ".join(w for w in (first(SPEAKER_WORD, line), first(DATE_WORD, line)) if w)


def mask(value: str, keep: int = 1) -> str:
    """Return ``value`` with all but its first ``keep`` characters hidden."""
    return f"{value[:keep]}…({len(value)})"


def quote_hits(line: str) -> Iterator[tuple[str, str]]:
    """Kind 1 on one line: (sub-kind, speaker and time)."""
    if not SPEAKER_WORD.search(line):
        if SHORT_MARK.search(line):
            yield "short decision mark", first(SHORT_MARK, line)
        return
    if any(p.search(line) for p in DATED_QUOTE):
        yield "dated quote", who_when(line)
    elif SPEAKER_QUOTE.search(line):
        yield "quote after a speaker (no time)", who_when(line)
    if any(p.search(line) for p in DATED_SOURCE) or VERB_SOURCE.search(line):
        yield "provenance line", who_when(line) or first(VERB_SOURCE, line)
    elif SHORT_MARK.search(line):
        yield "short decision mark", first(SHORT_MARK, line)


def id_hits(line: str) -> Iterator[tuple[str, str]]:
    """Kind 2 on one line: (sub-kind, id)."""
    for shape in TRACKER_IDS if "-" in line else ():
        if found := shape.pattern.search(line):
            yield shape.name, found.group(0)
    if "#" in line and (found := PRIVATE_ISSUE.search(line)):
        yield "private repository issue", found.group(0)


def name_hits(line: str) -> Iterator[tuple[str, str]]:
    """Kind 3 on one line: (sub-kind, name)."""
    for shape in INTERNAL_NAMES:
        if found := shape.pattern.search(line):
            yield shape.name, found.group(0)


def personal_hits(line: str) -> Iterator[tuple[str, str]]:
    """Kind 4 on one line: (sub-kind, masked value)."""
    people = (
        [n for n in HOME_PATH.findall(line) if n.lower() not in SERVICE_USERS and len(n) >= 3]
        if "Users" in line or "/home/" in line
        else []
    )
    if people:
        yield "home path of a person", "~" + mask(people[0])
    mails = (
        [
            (local, domain)
            for local, domain in EMAIL.findall(line)
            if not SAFE_EMAIL_DOMAIN.match(domain) and not SAFE_EMAIL_LOCAL.match(local)
        ]
        if "@" in line
        else []
    )
    if mails:
        yield "email", f"{mask(mails[0][0])}@{mask(mails[0][1])}"
    if "." in line and (found := PRIVATE_ADDRESS.search(line)):
        yield "private address", mask(found.group(0), 4)
    if found := TOKEN.search(line):
        yield "token shape", mask(found.group(0), 4)


def is_comment(line: str, code: bool) -> bool:
    """Whether a Japanese line is a comment: in code by its comment marker, in prose ``<!--``."""
    if code:
        return line.lstrip().startswith(COMMENT_START) or bool(INLINE_COMMENT.search(line))
    return bool(PROSE_COMMENT.search(line))


def note_hits(rel: str, line: str, marks: list[str]) -> Iterator[tuple[str, str]]:
    """Kind 5 on one line, given the kind 1-3 marks found on it: (sub-kind, mark)."""
    japanese = bool(JAPANESE.search(line))
    todo = bool(TODO.search(line))
    if not (japanese or todo):
        return
    code = suffix(rel) in CODE_SUFFIXES or rel.rsplit("/", 1)[-1] in CODE_NAMES
    prose = suffix(rel) in PROSE_SUFFIXES and not JAPANESE_PROSE_PATHS.search(rel)
    if japanese and marks and (code or prose) and is_comment(line, code):
        yield "Japanese comment with a mark", marks[0]
    if todo and (japanese or PERSONAL_TODO.search(line)):
        yield "Japanese or personal TODO", first(TODO, line)


def candidate_lines(text: str) -> list[int]:
    """Numbers (from 1) of the lines holding a trigger: the only lines worth matching one by one.

    Each trigger runs once over the whole text, which is far faster than running every pattern on
    every line.
    """
    folded = text.lower()
    return sorted(
        {
            bisect_right(starts, m.start())
            for source, patterns in ((text, CASED_TRIGGERS), (folded, (FOLDED_TRIGGER,)))
            for starts in [[0, *(m.end() for m in re.finditer("\n", source))]]
            for p in patterns
            for m in p.finditer(source)
        }
    )


def line_hits(rel: str, text: str) -> Iterator[Hit]:
    """Kinds 1-5 of the lines of one file."""
    lines = text.split("\n")
    for number in candidate_lines(text):
        line = lines[number - 1]
        marked = [
            Hit(rel, kind, sub, number, match)
            for kind, finder in (("1", quote_hits), ("2", id_hits), ("3", name_hits))
            for sub, match in finder(line)
        ]
        yield from marked
        yield from (Hit(rel, "4", sub, number, m) for sub, m in personal_hits(line))
        yield from (
            Hit(rel, "5", sub, number, m)
            for sub, m in note_hits(rel, line, [f"{h.kind}: {h.match}" for h in marked])
        )


def file_hits(rel: str, text: str | None, site: tuple[str, ...]) -> Iterator[Hit]:
    """Kind 2p and kind 6 of one file, judged on its path (and emptiness)."""
    if found := ANY_TRACKER_ID.search(rel):
        yield Hit(rel, "2p", "id in a path", 0, found.group(0))
    if (leftover := leftover_sub(rel, text, site)) is not None:
        yield Hit(rel, "6", leftover, 0, "")


def leftover_sub(rel: str, text: str | None, site: tuple[str, ...]) -> str | None:
    """The kind 6 sub-kind of a file, or None when the file is not a leftover."""
    by_path = next((s.name for s in LEFTOVER_PATHS if s.pattern.match(rel)), None)
    outside_site = (
        bool(site)
        and rel.startswith("docs/")
        and rel.endswith(".md")
        and not rel.startswith("docs/adr/")
        and not any(rel == p or (p.endswith("/") and rel.startswith(p)) for p in site)
    )
    empty = text is not None and not text.strip() and rel.rsplit("/", 1)[-1] not in EMPTY_ALLOWED
    return next(
        (
            sub
            for sub, hit in (
                (by_path, by_path is not None),
                ("docs page outside the site", outside_site),
                ("draft or log name", bool(LEFTOVER_NAME.search(rel)) or suffix(rel) == ".log"),
                ("empty file", empty),
            )
            if hit
        ),
        None,
    )


def kept(hit: Hit) -> Hit:
    """Mark a hit the inventory decided to keep."""
    rule = next(
        (
            k
            for k in KEEP
            if hit.kind == k.kind and hit.sub == k.sub and hit.file.startswith(k.prefix)
        ),
        None,
    )
    return hit if rule is None else Hit(hit.file, hit.kind, hit.sub, hit.line, hit.match, rule.why)


def file_scan(root: Path, site: tuple[str, ...], rel: str) -> list[Hit]:
    """Every hit of one file."""
    text = read_text(root, rel)
    return [kept(h) for h in (*file_hits(rel, text, site), *(line_hits(rel, text) if text else ()))]


def scan(root: Path, jobs: int) -> tuple[list[str], list[Hit]]:
    """Return the files looked at and every hit on them, reading files in ``jobs`` processes."""
    files = tree_files(root)
    one = partial(file_scan, root, site_pages(root))
    if jobs <= 1:
        return files, [h for rel in files for h in one(rel)]
    with ProcessPoolExecutor(jobs) as pool:
        return files, [h for hits in pool.map(one, files, chunksize=32) for h in hits]


# ---- grouping ---------------------------------------------------------------------------------


# Areas: the units a reader would delete or rewrite at once. The first match from the start of the
# path names the area; other files fall in the area of their top directory.
AREAS: tuple[Shape, ...] = (
    Shape("docs/adr", re.compile(r"docs/adr/")),
    Shape("docs/design-checks", re.compile(r"docs/design-checks/")),
    Shape("docs/design", re.compile(r"docs/design/")),
    Shape("docs/history", re.compile(r"docs/history/")),
    Shape("docs (other)", re.compile(r"docs/")),
    Shape("packages README / CHANGELOG", re.compile(r"packages/[^/]+/(?:README|CHANGELOG)[^/]*$")),
    Shape("pyproject", re.compile(r"(?:packages/[^/]+/)?pyproject\.toml$")),
    Shape("packages (src, tests, other)", re.compile(r"packages/")),
    Shape(".agents / .serena / .github", re.compile(r"\.(?:agents|serena|github)/")),
    Shape("root markdown", re.compile(r"[^/]+\.md$")),
    Shape("root config", re.compile(r"[^/]+$")),
)


def area(rel: str) -> str:
    """Return the area of a file."""
    return next((s.name for s in AREAS if s.pattern.match(rel)), rel.split("/", 1)[0])


def directory(rel: str) -> str:
    """Return the directory row of a file: one row for docs/design-checks, two levels elsewhere."""
    parts = rel.split("/")
    if rel.startswith("docs/design-checks/"):
        return "docs/design-checks/ (all)"
    deep = ("docs/design/", "docs/history/", "packages/", "ide-plugins/", "tools/", "publications/")
    if rel.startswith(deep) and len(parts) > 2:
        return "/".join(parts[:3]) + "/"
    return "/".join(parts[:2]) + ("/" if len(parts) > 2 else "")


@dataclass(frozen=True)
class Count:
    """How many files and distinct (file, line) pairs a group of hits covers."""

    files: int
    lines: int

    def render(self, by_file: bool = False) -> str:
        """Render as ``files / lines``, or ``files`` alone for a kind judged per file."""
        if self.files == 0:
            return "-"
        return f"{self.files:,}" if by_file else f"{self.files:,} / {self.lines:,}"


def count(hits: Iterable[Hit]) -> Count:
    """Count the files and the distinct lines of ``hits`` (a file-level hit is one line)."""
    rows = list(hits)
    return Count(len({h.file for h in rows}), len({(h.file, h.line) for h in rows}))


@dataclass(frozen=True)
class Placed:
    """A counted hit with the area and the directory row of its file."""

    hit: Hit
    area: str
    directory: str


def groups(
    placed: Iterable[Placed], key: Callable[[Placed], str]
) -> list[tuple[str, list[Placed]]]:
    """Group placed hits by ``key``, largest group first."""
    ordered = sorted(placed, key=key)
    return sorted(
        ((k, list(g)) for k, g in groupby(ordered, key=key)), key=lambda kg: (-len(kg[1]), kg[0])
    )


def grid_cell(kind: Kind, counted: Count, lines_only: bool) -> str:
    """One cell of the area or directory table: ``files / lines``, or one number."""
    if not lines_only:
        return counted.render(kind.by_file)
    if counted.files == 0:
        return "-"
    return str(counted.files if kind.by_file else counted.lines)


def grid_row(name: str, files: int, rows: list[Placed], lines_only: bool) -> str:
    """One row of the area or directory table: a cell per kind of the six."""
    cells = (
        grid_cell(k, count(p.hit for p in rows if p.hit.kind == k.key), lines_only) for k in SIX
    )
    return f"| {name} | {files:,} | " + " | ".join(cells) + " |"


def summary(root: Path, files: list[str], hits: list[Hit], top: int) -> str:
    """Render the counts as markdown."""
    counted = [h for h in hits if not h.kept]
    head = (
        subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--short=12", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip()
        or "(not a git tree)"
    )
    placed = [Placed(h, area(h.file), directory(h.file)) for h in counted]
    hit_areas = {p.area for p in placed}
    area_files = Counter(area(f) for f in files)
    dir_files = Counter(directory(f) for f in files)
    marked = [p for p in placed if p.hit.kind in {"1", "2", "3", "4"}]
    top_dirs = [d for d, _ in groups(marked, lambda p: p.directory)[:top]]
    header = " | ".join(k.title for k in SIX)
    return "\n".join(
        [
            f"# Internal leftovers in {root.resolve().name} at {head}",
            "",
            (
                f"{len(files):,} files looked at; {len({h.file for h in counted}):,} files carry "
                "at least one kind (2p included). Kept hits are in the table, not in the counts."
            ),
            "",
            "## Per kind",
            "",
            "| kind | files | lines | kept hits |",
            "|---|---|---|---|",
            *(
                f"| {k.title} | {c.files:,} | {'-' if k.by_file else f'{c.lines:,}'} | "
                f"{sum(1 for h in hits if h.kind == k.key and h.kept):,} |"
                for k in KINDS
                for c in [count(h for h in counted if h.kind == k.key)]
            ),
            "",
            "## Per sub-kind (files / lines)",
            "",
            "| kind | sub-kind | files / lines | kept |",
            "|---|---|---|---|",
            *(
                f"| {k} | {s} | {count(h for h in rows if not h.kept).render()} | "
                f"{count(h for h in rows if h.kept).render()} |"
                for (k, s), rows in (
                    (ks, list(g))
                    for ks, g in groupby(
                        sorted(hits, key=lambda h: (h.kind, h.sub)), key=lambda h: (h.kind, h.sub)
                    )
                )
            ),
            "",
            "## Per area (files / lines; kinds judged per file in files)",
            "",
            f"| area | files | {header} |",
            "|---|---|" + "---|" * len(SIX),
            *(
                grid_row(a, area_files[a], rows, False)
                for a, rows in groups(placed, lambda p: p.area)
            ),
            *(
                f"| {a} | {n:,} |" + " - |" * len(SIX)
                for a, n in sorted(area_files.items())
                if a not in hit_areas
            ),
            "",
            f"## Top {top} directories by kind 1-4 hits (lines; kinds judged per file in files)",
            "",
            f"| directory | files | {header} |",
            "|---|---|" + "---|" * len(SIX),
            *(
                grid_row(f"`{d}`", dir_files[d], [p for p in placed if p.directory == d], True)
                for d in top_dirs
            ),
            "",
        ]
    )


def table(hits: list[Hit], fmt: str) -> str:
    """Render every hit as TSV or as a markdown table."""
    rows = [
        (h.kind, h.sub, h.file, str(h.line) if h.line else "-", h.match, h.kept)
        for h in sorted(hits, key=lambda h: (h.file, h.line, h.kind, h.sub))
    ]
    header = ("kind", "sub-kind", "file", "line", "match", "kept")
    if fmt == "tsv":
        return "\n".join("\t".join(r) for r in [header, *rows]) + "\n"
    return (
        "\n".join(
            ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
            + ["| " + " | ".join(c.replace("|", "\\|") for c in r) + " |" for r in rows]
        )
        + "\n"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("root", nargs="?", default=".", type=Path, help="repository root")
    parser.add_argument("--table", metavar="FILE", help="write every hit to FILE ('-' = stdout)")
    parser.add_argument("--format", choices=("tsv", "md"), default="tsv", help="table format")
    parser.add_argument("--top", type=int, default=30, help="directory rows in the summary")
    parser.add_argument(
        "--jobs", type=int, default=os.cpu_count() or 1, help="processes reading files"
    )
    args = parser.parse_args(argv)
    files, hits = scan(args.root, args.jobs)
    if args.table == "-":
        sys.stdout.write(table(hits, args.format))
        return 0
    if args.table:
        Path(args.table).write_text(table(hits, args.format), encoding="utf-8")
    sys.stdout.write(summary(args.root, files, hits, args.top))
    return 0


if __name__ == "__main__":
    sys.exit(main())
