# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""本体の行のうち、表に無い form として lisp のまま残る行を、頭の form ごとに数える。

使い方: uv run --script lisp_tally.py <doeff-linter の binary> <repo の root> <file の glob …>
出力: 頭の form ごとの行数(lisp の島を含む行の数)と、file ごとの本体の行数 / lisp の行数。
"""

from __future__ import annotations

import collections
import glob
import json
import os
import subprocess
import sys


def head_of(text: str) -> str:
    """lisp の島の文字から頭の記号を取る(`(cond` → `cond`・`#(` → `#(`・裸の記号はそのまま)。"""
    s = text.strip()
    if s.startswith("(") or s.startswith("#("):
        inner = s.lstrip("#(").strip()
        return inner.split()[0] if inner else "()"
    if s.startswith("["):
        return "[…]"
    if s.startswith("{"):
        return "{…}"
    return s.split()[0] if s else "(空)"


def lines_of(document: object):
    """editor-json の中から本体の行を全部取り出す(欄の場所に依らないように木を歩く)。"""
    stack = [document]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            if "lines" in node and isinstance(node["lines"], list) and "kind" in node:
                for line in node["lines"]:
                    yield node, line
            for value in node.values():
                if isinstance(value, (dict, list)):
                    stack.append(value)
        elif isinstance(node, list):
            stack.extend(node)


def main() -> int:
    binary, root, *globs = sys.argv[1:]
    files = sorted({p for g in globs for p in glob.glob(os.path.join(root, g), recursive=True)})
    per_head: collections.Counter[str] = collections.Counter()
    per_head_examples: dict[str, str] = {}
    total_lines = 0
    lisp_lines = 0
    per_file: dict[str, tuple[int, int]] = {}
    for path in files:
        with open(path, "rb") as f:
            source = f.read()
        run = subprocess.run(
            [binary, "--output-format", "editor-json", "--stdin", "--path", path, "--no-log"],
            cwd=root, input=source, capture_output=True, check=False,
        )
        if run.returncode not in (0, 1):
            print(f"落ちた: {path} rc={run.returncode} {run.stderr.decode()[:200]}", file=sys.stderr)
            continue
        document = json.loads(run.stdout.decode())
        n_lines = 0
        n_lisp = 0
        for _, line in lines_of(document):
            parts = line.get("segments", [])
            n_lines += 1
            islands = [p for p in parts if p.get("role") == "lisp"]
            if not islands:
                continue
            n_lisp += 1
            # 行の頭が lisp なら「form 全体が表に無い」、そうでなければ「式の中の島」
            first = parts[0] if parts else {}
            if first.get("role") == "lisp":
                head = head_of(first.get("text", ""))
            else:
                head = "式の中の島: " + head_of(islands[0].get("text", ""))
            per_head[head] += 1
            per_head_examples.setdefault(head, "".join(p.get("text", "") for p in parts)[:90])
        total_lines += n_lines
        lisp_lines += n_lisp
        per_file[os.path.relpath(path, root)] = (n_lines, n_lisp)
    print(f"file {len(per_file)} 本・本体の行 {total_lines}・lisp の目印が残る行 {lisp_lines}")
    print()
    print("| 頭の form | 行 | 例 |")
    print("|---|---:|---|")
    for head, n in per_head.most_common(40):
        print(f"| `{head}` | {n} | `{per_head_examples[head]}` |")
    print()
    worst = sorted(per_file.items(), key=lambda kv: -kv[1][1])[:8]
    print("| file | 本体の行 | lisp の行 |")
    print("|---|---:|---:|")
    for path, (a, b) in worst:
        print(f"| {path} | {a} | {b} |")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
