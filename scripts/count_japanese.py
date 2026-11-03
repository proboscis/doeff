#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Count the lines of the tracked files that contain Japanese letters.

The repository language is English. This check measures how far the tree is from that: it counts,
over every line of every tracked text file, the lines with at least one hiragana, katakana or kanji
letter. It only counts; it translates and changes nothing.

Left out: binary files (a NUL byte in the first 8 KiB, which covers images), ``publications/``
(moving to another repository) and the directories in ``EXCLUDED_DIRS``. Words in ``KEEP`` (proper
nouns that stay Japanese) are removed from a line before it is tested.

Usage::

    uv run --script scripts/count_japanese.py [ROOT] [--depth N] [--top N] [--table FILE] [--fail-on-any]

The summary is markdown on stdout: the total, then lines and files per directory (to ``--depth``
path parts, default 1; ``--top`` rows, default all) and per extension, in a form that pastes into a
card. ``--table FILE`` writes ``file<TAB>line count<TAB>first line number`` for every file with a
hit (``-`` for stdout). ``--fail-on-any`` exits 1 when the total is not 0. Standard library only.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from collections import Counter
from collections.abc import Iterator
from pathlib import Path, PurePosixPath

JAPANESE = re.compile("[ぁ-ゖァ-ヺー々㐀-䶿一-鿿]")

# Proper nouns that stay Japanese (a name of a person, a place, a product). Empty until one is needed.
KEEP: tuple[str, ...] = ()

# Moving to another repository, so not counted here.
EXCLUDED_PREFIXES: tuple[str, ...] = ("publications/",)
EXCLUDED_DIRS = frozenset({".git", ".venv", "node_modules", "target", "__pycache__"})
SNIFF_BYTES = 8192


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
    return sorted(
        n
        for n in names
        if not n.startswith(EXCLUDED_PREFIXES) and not set(n.split("/")[:-1]) & EXCLUDED_DIRS
    )


def walk(root: Path) -> Iterator[str]:
    for here, dirs, names in os.walk(root):
        dirs[:] = [d for d in dirs if d not in EXCLUDED_DIRS]
        yield from ((Path(here) / n).relative_to(root).as_posix() for n in names)


def japanese_lines(root: Path, rel: str) -> list[int]:
    """Return the 1-based numbers of the lines of one file that contain Japanese letters."""
    path = root / rel
    try:
        if path.is_symlink() or not path.is_file():
            return []
        data = path.read_bytes()
    except OSError:
        return []
    if b"\0" in data[:SNIFF_BYTES]:
        return []
    hits = []
    for number, line in enumerate(data.decode("utf-8", "replace").splitlines(), 1):
        for word in KEEP:
            line = line.replace(word, "")
        if JAPANESE.search(line):
            hits.append(number)
    return hits


def group_of(rel: str, depth: int) -> str:
    parts = rel.split("/")[:-1]
    return "/".join(parts[:depth]) + "/" if parts else "(root)"


def extension_of(rel: str) -> str:
    return PurePosixPath(rel).suffix.lower() or "(none)"


def table(title: str, rows: Counter[str], files: Counter[str], top: int) -> list[str]:
    head = [f"### By {title.lower()}", "", f"| {title} | lines | files |", "|---|---:|---:|"]
    body = [f"| `{key}` | {lines:,} | {files[key]:,} |" for key, lines in rows.most_common(top or None)]
    return [*head, *body, ""]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("root", nargs="?", default=".")
    parser.add_argument("--depth", type=int, default=1)
    parser.add_argument("--top", type=int, default=0)
    parser.add_argument("--table", metavar="FILE")
    parser.add_argument("--fail-on-any", action="store_true")
    args = parser.parse_args(argv)
    root = Path(args.root)

    per_file = {}
    for rel in tree_files(root):
        hits = japanese_lines(root, rel)
        if hits:
            per_file[rel] = hits

    total_lines = sum(len(h) for h in per_file.values())
    by_dir: Counter[str] = Counter()
    dir_files: Counter[str] = Counter()
    by_ext: Counter[str] = Counter()
    ext_files: Counter[str] = Counter()
    for rel, hits in per_file.items():
        for rows, files, key in (
            (by_dir, dir_files, group_of(rel, args.depth)),
            (by_ext, ext_files, extension_of(rel)),
        ):
            rows[key] += len(hits)
            files[key] += 1

    report = [
        "## Lines with Japanese letters",
        "",
        f"**Total: {total_lines:,} lines in {len(per_file):,} files** (`publications/` not counted)",
        "",
        *table("Directory", by_dir, dir_files, args.top),
        *table("Extension", by_ext, ext_files, args.top),
    ]
    sys.stdout.write("\n".join(report) + "\n")

    if args.table:
        lines = [f"{rel}\t{len(h)}\t{h[0]}" for rel, h in sorted(per_file.items())]
        text = "\n".join(lines) + ("\n" if lines else "")
        if args.table == "-":
            sys.stdout.write(text)
        else:
            Path(args.table).write_text(text, encoding="utf-8")

    if args.fail_on_any and total_lines:
        print(f"count_japanese: {total_lines} lines in {len(per_file)} files", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
