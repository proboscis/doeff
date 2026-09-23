"""uv の cache-keys が doeff-vm の Rust の build 入力を漏れなく覆うことの検査。

uv は packages/doeff-vm/pyproject.toml の [tool.uv] cache-keys に載った file だけを見て
「作り直すか」を決める。Rust の source が鍵から漏れると、その file だけが変わった更新の後の
`uv sync` / `uv run` は古い doeff_vm/*.so を使い続ける(2026-09-23 22:42〜23:17 に pod の
controller が古い .so で 35 分落ちた — card acp:kanban-issue:ki-3965aa5188ae)。

入力の母集団は crate 名を書かずに導く: packages/doeff-vm/Cargo.toml の依存表の path = を
推移的にたどった crate の集合について、git が追跡する Cargo.toml / Cargo.lock / build.rs /
pyproject.toml と library の *.rs を集める。tests/ benches/ examples/ は Cargo の library
以外の target の置き場で、maturin が作る cdylib にも path 依存としての build にも入らないので
母集団から外す。
"""

from __future__ import annotations

import os
import posixpath
import re
import subprocess
from collections.abc import Iterable, Mapping
from pathlib import Path, PurePosixPath
from typing import Any

import tomllib

PACKAGE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = PACKAGE_DIR.parents[1]
PACKAGE_PATH = PACKAGE_DIR.relative_to(REPO_ROOT).as_posix()

# 名前で build 入力と分かる file(*.rs は拡張子で別に拾う)。
BUILD_INPUT_NAMES = frozenset({"Cargo.toml", "Cargo.lock", "build.rs", "pyproject.toml"})
# Cargo が library 以外の target(結合テスト・bench・例)を自動で探す置き場。
NON_LIBRARY_TARGET_DIRS = frozenset({"tests", "benches", "examples"})
# build に効く依存表。[dev-dependencies] は library の build に入らない。
DEPENDENCY_TABLES = ("dependencies", "build-dependencies")
_UNSUPPORTED_GLOB_CHARS = frozenset("[]{}")


# ---------------------------------------------------------------------------
# 純粋な判断
# ---------------------------------------------------------------------------


def path_dependency_dirs(manifest: Mapping[str, Any], crate_dir: Path) -> list[Path]:
    """Cargo.toml 1 枚の依存表から path = の依存先の directory を返す。"""
    tables: list[Mapping[str, Any]] = [manifest.get(name, {}) for name in DEPENDENCY_TABLES]
    for target in manifest.get("target", {}).values():
        tables.extend(target.get(name, {}) for name in DEPENDENCY_TABLES)
    return [
        (crate_dir / spec["path"]).resolve()
        for table in tables
        for spec in table.values()
        if isinstance(spec, Mapping) and "path" in spec
    ]


def is_build_input(path_in_crate: str) -> bool:
    """crate の directory からの相対 path が build の入力に当たるか。"""
    parts = PurePosixPath(path_in_crate).parts
    if parts[0] in NON_LIBRARY_TARGET_DIRS:
        return False
    return parts[-1] in BUILD_INPUT_NAMES or parts[-1].endswith(".rs")


def declared_file_globs(pyproject: Mapping[str, Any]) -> list[str]:
    """[tool.uv] cache-keys のうち file を名指す鍵(文字列 / { file = ... })の glob を返す。"""
    keys = pyproject.get("tool", {}).get("uv", {}).get("cache-keys", [])
    globs: list[str] = []
    for key in keys:
        if isinstance(key, str):
            globs.append(key)
        elif isinstance(key, Mapping) and isinstance(key.get("file"), str):
            globs.append(key["file"])
    return globs


def _segment_matches(pattern: str, segment: str) -> bool:
    if not any(ch in pattern for ch in "*?"):
        return pattern == segment
    if segment == "..":
        return False
    if "**" in pattern:
        raise ValueError(f"'**' must be a whole path segment: {pattern!r}")
    regex = "".join(
        "[^/]*" if ch == "*" else "[^/]" if ch == "?" else re.escape(ch) for ch in pattern
    )
    return re.fullmatch(regex, segment) is not None


def _segments_match(pattern: tuple[str, ...], path: tuple[str, ...]) -> bool:
    if not pattern:
        return not path
    head, rest = pattern[0], pattern[1:]
    if head == "**":
        # 0 個以上の directory。uv は package の directory から下へ歩くので '..' は跨がない。
        for taken in range(len(path) + 1):
            if taken > 0 and path[taken - 1] == "..":
                return False
            if _segments_match(rest, path[taken:]):
                return True
        return False
    return bool(path) and _segment_matches(head, path[0]) and _segments_match(rest, path[1:])


def glob_matches(pattern: str, path: str) -> bool:
    """正規化済みの posix path が glob に当たるか。

    '**' は 0 個以上の directory、'*' / '?' は 1 つの segment の中だけに当たる。
    [] や {} は解釈を誤るより止まる方を選ぶ(使いたくなったらここを広げる)。
    """
    unsupported = _UNSUPPORTED_GLOB_CHARS.intersection(pattern)
    if unsupported:
        raise ValueError(f"unsupported glob syntax {sorted(unsupported)} in {pattern!r}")
    return _segments_match(tuple(pattern.split("/")), tuple(path.split("/")))


def uncovered_inputs(
    inputs: Iterable[str], file_globs: Iterable[str], *, package_path: str
) -> list[str]:
    """どの glob にも当たらない入力を返す。

    inputs と file_globs はどちらも package の directory からの相対 path で書く。両側を
    repo の根からの path(package_path を前に付けて normpath)へ揃えてから比べるので、
    '../doeff-vm/src/lib.rs' と 'src/lib.rs' は同じ file として扱われる。
    """

    def anchored(relative: str) -> str:
        return posixpath.normpath(posixpath.join(package_path, relative))

    anchored_globs = [anchored(pattern) for pattern in file_globs]
    return sorted(
        path
        for path in inputs
        if not any(glob_matches(pattern, anchored(path)) for pattern in anchored_globs)
    )


# ---------------------------------------------------------------------------
# 実際の checkout を読む部分
# ---------------------------------------------------------------------------


def input_crate_dirs(root_crate_dir: Path) -> list[Path]:
    """root の crate と、path 依存を推移的にたどった crate の directory(root が先頭)。"""
    ordered: list[Path] = []
    pending = [root_crate_dir.resolve()]
    while pending:
        crate_dir = pending.pop(0)
        if crate_dir in ordered:
            continue
        ordered.append(crate_dir)
        manifest = tomllib.loads((crate_dir / "Cargo.toml").read_text(encoding="utf-8"))
        pending.extend(path_dependency_dirs(manifest, crate_dir))
    return ordered


def tracked_build_inputs_by_crate() -> dict[str, list[str]]:
    """crate(package からの相対 directory)ごとの、git が追跡する build 入力の一覧。"""
    by_crate: dict[str, list[str]] = {}
    for crate_dir in input_crate_dirs(PACKAGE_DIR):
        crate_path = crate_dir.relative_to(REPO_ROOT).as_posix()
        listed = subprocess.run(
            ["git", "ls-files", "-z", "--", f"{crate_path}/"],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        by_crate[Path(os.path.relpath(crate_dir, PACKAGE_DIR)).as_posix()] = sorted(
            Path(os.path.relpath(REPO_ROOT / tracked, PACKAGE_DIR)).as_posix()
            for tracked in listed.split("\0")
            if tracked and is_build_input(PurePosixPath(tracked).relative_to(crate_path).as_posix())
        )
    return by_crate


def _declared_globs() -> list[str]:
    pyproject = tomllib.loads((PACKAGE_DIR / "pyproject.toml").read_text(encoding="utf-8"))
    return declared_file_globs(pyproject)


# ---------------------------------------------------------------------------
# 検査
# ---------------------------------------------------------------------------


def test_uv_cache_keys_cover_every_rust_build_input() -> None:
    inputs = [path for paths in tracked_build_inputs_by_crate().values() for path in paths]
    uncovered = uncovered_inputs(inputs, _declared_globs(), package_path=PACKAGE_PATH)
    assert not uncovered, (
        "packages/doeff-vm/pyproject.toml の [tool.uv] cache-keys に載っていない build 入力が"
        " あります。この file だけが変わると uv は doeff-vm を作り直さず、古い .so が残ります:\n"
        + "\n".join(f"  {path}" for path in uncovered)
    )


def test_checker_reports_dependency_sources_when_their_glob_is_missing() -> None:
    """較正: 依存 crate の *.rs の glob を抜いた合成の鍵では、その *.rs がちょうど漏れと出る。"""
    by_crate = tracked_build_inputs_by_crate()
    crates_with_inputs = [crate for crate, paths in by_crate.items() if paths]
    assert "." in crates_with_inputs
    assert len(crates_with_inputs) >= 2, f"母集団が 2 crate 未満: {by_crate}"
    dependency_crates = [crate for crate in by_crate if crate != "."]
    assert dependency_crates, "path 依存の crate が 1 つも導けていない"
    synthetic = [*sorted(BUILD_INPUT_NAMES), "src/**/*.rs"] + [
        f"{crate}/{name}" for crate in dependency_crates for name in sorted(BUILD_INPUT_NAMES)
    ]
    expected = sorted(
        path
        for crate in dependency_crates
        for path in by_crate[crate]
        if path.endswith(".rs") and path != f"{crate}/build.rs"
    )
    assert expected, "依存 crate の *.rs が母集団に無い — 検査が空回りしている"
    inputs = [path for paths in by_crate.values() for path in paths]
    assert uncovered_inputs(inputs, synthetic, package_path=PACKAGE_PATH) == expected


def test_glob_semantics() -> None:
    assert glob_matches("a/src/**/*.rs", "a/src/lib.rs")
    assert glob_matches("a/src/**/*.rs", "a/src/vm/step.rs")
    assert not glob_matches("a/src/**/*.rs", "a/src/lib.rsx")
    assert not glob_matches("a/src/**/*.rs", "a/srcx/lib.rs")
    assert not glob_matches("a/src/*.rs", "a/src/vm/step.rs")
    assert not glob_matches("**/*.rs", "../outside/lib.rs")
    pkg = "packages/doeff-vm"
    assert uncovered_inputs(["src/lib.rs"], ["../doeff-vm/src/*.rs"], package_path=pkg) == []
    assert uncovered_inputs(["src/lib.rs"], ["./src/lib.rs"], package_path=pkg) == []
    assert uncovered_inputs(["../doeff-vm-core/src/lib.rs"], ["**/*.rs"], package_path=pkg) == [
        "../doeff-vm-core/src/lib.rs"
    ]
    assert (
        uncovered_inputs(
            ["../doeff-vm-core/src/vm/step.rs"],
            ["../doeff-vm-core/src/**/*.rs"],
            package_path=pkg,
        )
        == []
    )
