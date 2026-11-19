"""Path matching in the collection and wiring scans — below rootdir it is cut as a string, and symlinks, paths outside rootdir, and sibling dirs whose name starts the same as
rootdir are sent to the previous partial comparison and real-path resolution; this checks that."""

import os
from pathlib import Path

import pytest
from doeff_adr._pytest_plugin import DEFAULT_FILE_PATTERNS, HyFileMatcher

pytest_plugins = ["pytester"]


def relative_posix(path: Path, root: Path) -> str:
    return HyFileMatcher.from_root(root, ()).relative_posix(str(path))


def matches_file_patterns(path: Path, root: Path, patterns: list[str] | tuple[str, ...]) -> bool:
    return HyFileMatcher.from_root(root, patterns).matches(path)


def discover_executable_adrs(root: Path, patterns: list[str] | tuple[str, ...]) -> set[Path]:
    return HyFileMatcher.from_root(root, patterns).discover()


def relative_module_parts(path: Path, root: Path) -> list[str] | None:
    return HyFileMatcher.from_root(root, ()).relative_module_parts(path)


def module_name_for_path(path: Path, root: Path) -> str:
    return HyFileMatcher.from_root(root, ()).module_name(path)


def import_base_for_path(path: Path, root: Path) -> Path:
    return HyFileMatcher.from_root(root, ()).import_base(path)


def test_relative_posix_under_root_and_at_root(tmp_path: Path) -> None:
    """Below rootdir it is the path from rootdir, and rootdir itself is ``.`` (the same as ``Path.relative_to``)."""
    root = tmp_path / "project"
    assert relative_posix(root / "docs" / "adr" / "defadr_x.hy", root) == "docs/adr/defadr_x.hy"
    assert relative_posix(root, root) == "."
    assert relative_posix(Path("/a/b.hy"), Path("/")) == "a/b.hy"


def test_relative_posix_does_not_cut_a_sibling_that_shares_the_root_name(tmp_path: Path) -> None:
    """``/x/project2/a.hy`` is not under ``/x/project`` — a counterexample for the mistake where cutting only by the string prefix gives ``2/a.hy``."""
    root = tmp_path / "project"
    sibling = tmp_path / "project2" / "a.hy"
    assert relative_posix(sibling, root) == sibling.as_posix()


def test_relative_posix_resolves_a_symlinked_path_into_the_root(tmp_path: Path) -> None:
    """A path under a symlink pointing to rootdir is made relative if the real path shows it is under rootdir, even if it is outside as a string."""
    root = tmp_path / "project"
    (root / "docs").mkdir(parents=True)
    (root / "docs" / "defadr_x.hy").write_text("")
    link = tmp_path / "link"
    link.symlink_to(root, target_is_directory=True)
    assert relative_posix(link / "docs" / "defadr_x.hy", root) == "docs/defadr_x.hy"


def test_relative_posix_outside_the_root_is_the_absolute_path(tmp_path: Path) -> None:
    """Outside rootdir (also outside by the real path) stays an absolute path."""
    root = tmp_path / "project"
    root.mkdir()
    outside = tmp_path / "elsewhere" / "defadr_x.hy"
    assert relative_posix(outside, root) == outside.as_posix()


def test_file_patterns_match_name_relative_glob_and_absolute(tmp_path: Path) -> None:
    """A name-only pattern, a glob from rootdir, and an absolute-path pattern each hit their candidates."""
    root = tmp_path / "project"
    assert matches_file_patterns(root / "any" / "defadr_x.hy", root, DEFAULT_FILE_PATTERNS)
    assert matches_file_patterns(root / "pkg" / "a" / "test_x.hy", root, ["pkg/*/test_*.hy"])
    assert not matches_file_patterns(root / "pkg" / "test_x.hy", root, ["pkg/*/test_*.hy"])
    assert matches_file_patterns(root / "p" / "t.hy", root, [(root / "p").as_posix() + "/*.hy"])
    sibling = tmp_path / "project2" / "pkg" / "a" / "test_x.hy"
    assert not matches_file_patterns(sibling, root, ["2/pkg/*/test_*.hy"])


def test_wiring_discovery_sees_only_hy_files_and_does_not_follow_symlinked_dirs(
    tmp_path: Path,
) -> None:
    """The scan matches only .hy files and returns real paths. Symlinked dirs are not followed, as before (``os.walk``)."""
    root = tmp_path / "project"
    adr = root / "docs" / "adr"
    adr.mkdir(parents=True)
    (adr / "defadr_a.hy").write_text("")
    (adr / "defadr_a.py").write_text("")
    (adr / "notes.hy").write_text("")
    outside = tmp_path / "outside" / "docs" / "adr"
    outside.mkdir(parents=True)
    (outside / "defadr_b.hy").write_text("")
    (root / "linked").symlink_to(tmp_path / "outside", target_is_directory=True)
    found = discover_executable_adrs(root, DEFAULT_FILE_PATTERNS)
    assert found == {(adr / "defadr_a.hy").resolve()}


def test_strict_wiring_accepts_a_collected_defadr_reached_through_a_symlink(
    pytester: pytest.Pytester,
) -> None:
    """Even when a symlinked file (whose target is elsewhere and does not match the pattern) is collected, the real path of the scan and the real path of the collection agree,
    and strict wiring passes (the answer is the same even if the resolution of the item files' real paths is done once per file).

    A .hy whose target is outside rootdir could not have its module name built and could not be collected even before this change (``_module_name_for_path``),
    so the target is placed inside rootdir.
    """
    source = pytester.path / "src"
    source.mkdir()
    (source / "__init__.py").write_text("")
    (source / "linked_source.hy").write_text(
        "(require doeff-hy.macros [deftest])\n(deftest test-linked (assert True))\n(deftest test-linked-again (assert True))\n"
    )
    adr = pytester.path / "docs" / "adr"
    adr.mkdir(parents=True)
    os.symlink(source / "linked_source.hy", adr / "defadr_linked.hy")
    (adr / "defadr_plain.hy").write_text(
        "(require doeff-hy.macros [deftest])\n(deftest test-plain (assert True))\n"
    )
    pytester.makeconftest(
        """
        import pytest


        @pytest.fixture
        def doeff_interpreter():
            from doeff import run

            return lambda program, *, env=None: run(program)
        """
    )
    pytester.makeini(
        f"""
        [pytest]
        testpaths = docs/adr
        doeff_adr_wiring = strict
        doeff_adr_items_cache = {pytester.path / "items"}
        """
    )
    result = pytester.runpytest_subprocess("-p", "no:cacheprovider", "--collect-only", "-q")
    assert result.ret == 0, result.stdout.str()
    result.stdout.fnmatch_lines(
        ["docs/adr/defadr_linked.hy::test_linked", "docs/adr/defadr_plain.hy::test_plain"]
    )


def test_module_name_parts_are_cut_from_the_string_under_the_root(tmp_path: Path) -> None:
    """The module name is the part from rootdir without the extension (the same answer as ``with_suffix("").relative_to``). Outside rootdir it is
    ValueError, as before."""
    root = tmp_path / "project"
    assert relative_module_parts(root / "controllers" / "x" / "tests" / "test_y.hy", root) == [
        "controllers",
        "x",
        "tests",
        "test_y",
    ]
    assert relative_module_parts(root / "a" / "b.c.hy", root) == ["a", "b.c"]
    assert relative_module_parts(tmp_path / "project2" / "a.hy", root) is None
    assert module_name_for_path(root / "pkg" / "tests" / "test_z.hy", root) == "pkg.tests.test_z"
    with pytest.raises(ValueError, match="outside pytest root"):
        module_name_for_path(tmp_path / "elsewhere" / "test_z.hy", root)


def test_import_base_falls_back_to_the_first_non_package_ancestor(tmp_path: Path) -> None:
    """A path containing a part that cannot be used in a name (``doeff-cluster``) is based on the first ancestor without ``__init__`` (as before)."""
    root = tmp_path / "project"
    tests = root / "packages" / "doeff-cluster" / "tests"
    tests.mkdir(parents=True)
    (tests / "__init__.py").write_text("")
    source = tests / "test_x.hy"
    assert import_base_for_path(source, root) == root / "packages" / "doeff-cluster"
    assert import_base_for_path(root / "pkg" / "test_x.hy", root) == root
