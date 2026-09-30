"""収集と wiring の走査の path の照合(agora-redesign #1551)— rootdir の下は文字列で切り、symlink・rootdir の外・名の頭が
rootdir と同じ兄弟の dir は今までの部分の比べと実の path の解決へ回ることを確かめる。"""

import os
from pathlib import Path

import pytest
from doeff_adr.pytest_plugin import DEFAULT_FILE_PATTERNS, HyFileMatcher

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
    """rootdir の下は rootdir からの path、rootdir そのものは ``.``(``Path.relative_to`` と同じ)。"""
    root = tmp_path / "project"
    assert relative_posix(root / "docs" / "adr" / "defadr_x.hy", root) == "docs/adr/defadr_x.hy"
    assert relative_posix(root, root) == "."
    assert relative_posix(Path("/a/b.hy"), Path("/")) == "a/b.hy"


def test_relative_posix_does_not_cut_a_sibling_that_shares_the_root_name(tmp_path: Path) -> None:
    """``/x/project2/a.hy`` は ``/x/project`` の下ではない — 文字列の頭だけで切ると ``2/a.hy`` になる誤りの反例。"""
    root = tmp_path / "project"
    sibling = tmp_path / "project2" / "a.hy"
    assert relative_posix(sibling, root) == sibling.as_posix()


def test_relative_posix_resolves_a_symlinked_path_into_the_root(tmp_path: Path) -> None:
    """rootdir を指す symlink の下の path は、文字列では外でも、実の path で rootdir の下と分かれば相対にする。"""
    root = tmp_path / "project"
    (root / "docs").mkdir(parents=True)
    (root / "docs" / "defadr_x.hy").write_text("")
    link = tmp_path / "link"
    link.symlink_to(root, target_is_directory=True)
    assert relative_posix(link / "docs" / "defadr_x.hy", root) == "docs/defadr_x.hy"


def test_relative_posix_outside_the_root_is_the_absolute_path(tmp_path: Path) -> None:
    """rootdir の外(実の path でも外)は絶対 path のまま。"""
    root = tmp_path / "project"
    root.mkdir()
    outside = tmp_path / "elsewhere" / "defadr_x.hy"
    assert relative_posix(outside, root) == outside.as_posix()


def test_file_patterns_match_name_relative_glob_and_absolute(tmp_path: Path) -> None:
    """名だけの pattern・rootdir からの glob・絶対 path の pattern が、それぞれの候補に当たる。"""
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
    """走査は .hy の file だけを照合し、実の path で返す。symlink の dir は今までどおり辿らない(``os.walk``)。"""
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
    """symlink の file(実体は pattern に当たらない別の場所)を収集した時も、走査の実の path と収集の実の path が合い、
    strict の wiring が通る(item の file の実の path の解決を file ごとにまとめても答えは同じ)。

    実体が rootdir の外の .hy は、この変更の前から module の名が作れず収集できない(``_module_name_for_path``)ので、
    実体は rootdir の中に置く。
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
    """module の名は拡張子を除いた rootdir からの部分(``with_suffix("").relative_to`` と同じ答え)。rootdir の外は
    今までどおり ValueError。"""
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
    """名に使えない部分(``doeff-cluster``)を含む path は、``__init__`` を持たない最初の祖先を基にする(今までどおり)。"""
    root = tmp_path / "project"
    tests = root / "packages" / "doeff-cluster" / "tests"
    tests.mkdir(parents=True)
    (tests / "__init__.py").write_text("")
    source = tests / "test_x.hy"
    assert import_base_for_path(source, root) == root / "packages" / "doeff-cluster"
    assert import_base_for_path(root / "pkg" / "test_x.hy", root) == root
