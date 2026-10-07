"""doeff-hy-check の展開の保存(doeff_hy/static_cache.py)の失敗ケース — 引くか、展開し直すか。

保存した展開は、展開が依った物が今の環境と同じ時だけ引く(agora-redesign #3862)。依った物 = source の中身・module 名・
根からの相対 path・Hy の版と、展開が通った file(require した macro の module・macro が呼ぶ補助の module・推移的な require・
型検査の展開の後処理の module)。展開が通らない doeff_hy の file を変えても、保存は引ける(以前は doeff_hy の package 全体の
指紋がキーに入り、関係ない commit 1 つで全部の展開が作り直しになっていた)。

macro の module を読み直すのは次の実行(別の process)と同じにするため、検は macro の module を sys.modules から外してから
2 度目を引く。doeff_hy 自身の file を変える検は、doeff_hy を一時の dir に写して別の process で走らせる(読み込み済みの
doeff_hy を変えないため)。
"""

import json
import shutil
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import doeff_hy
import pytest
from doeff_hy import static_check
from doeff_hy.static_check import Projection, project_cached

PACKAGE = "probe_cache"

#: macro の module。said = 補助の module の関数の答え・said-late = macro の本体の中でだけ import する補助の答え・
#: said-deep = この module が require する別の macro の module の macro を、この module の compile の時に展開した答え。
#: unused = user.hy が使わない macro。
MACROS = f"""\
(import {PACKAGE}.words [word])
(require {PACKAGE}.deeper [deep])
(defmacro said [] (word))
(defmacro said-late [] (import {PACKAGE}.late) ({PACKAGE}.late.word))
(defmacro said-deep [] (deep))
(defmacro unused [] "unused")
"""

USER = f"""\
(require {PACKAGE}.macros [said said-late said-deep])
(setv a (said) b (said-late) c (said-deep))
"""


@dataclass(frozen=True)
class Tree:
    """根の下の macro の package(probe_cache)と、それを require する user.hy。"""

    root: Path
    cache: Path

    @property
    def package(self) -> Path:
        return self.root / PACKAGE

    @property
    def user(self) -> Path:
        return self.package / "user.hy"


def _write_tree(root: Path, words: str = "old") -> Tree:
    package = root / PACKAGE
    package.mkdir(parents=True)
    (package / "__init__.hy").write_text("", encoding="utf-8")
    (package / "macros.hy").write_text(MACROS, encoding="utf-8")
    (package / "words.py").write_text(
        f'def word() -> str:\n    return "{words}"\n', encoding="utf-8"
    )
    (package / "late.py").write_text('def word() -> str:\n    return "late"\n', encoding="utf-8")
    (package / "deeper.hy").write_text('(defmacro deep [] "deep")\n', encoding="utf-8")
    (package / "user.hy").write_text(USER, encoding="utf-8")
    return Tree(root, root.parent / ".cache")


def _forget(package: str) -> None:
    """読み込み済みの package の module を外す(次の実行と同じく、次の展開で macro の module を読み直させる)。"""
    for name in [n for n in sys.modules if n == package or n.startswith(f"{package}.")]:
        del sys.modules[name]


@pytest.fixture
def expansions(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    """展開した source の列(保存から引いた時は増えない)。"""
    seen: list[Path] = []
    real = static_check.project

    def counting(root: Path, roots: list[Path], source: Path) -> object:
        seen.append(source)
        return real(root, roots, source)

    monkeypatch.setattr(static_check, "project", counting)
    return seen


@pytest.fixture
def tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Tree]:
    monkeypatch.setattr(sys, "dont_write_bytecode", True)
    monkeypatch.setenv("DOEFF_HY_CODE_STORE", "off")
    made = _write_tree(tmp_path / "tree")
    monkeypatch.syspath_prepend(str(made.root))
    yield made
    _forget(PACKAGE)


def _project(tree: Tree) -> Projection:
    result = project_cached(tree.root, [tree.root], tree.user, tree.cache)
    assert isinstance(result, Projection), result
    _forget(PACKAGE)
    return result


def _rewrite(path: Path, text: str) -> None:
    """中身を替える(大きさも替える — file の sha256 の覚えは path・更新時刻・大きさで引くため)。"""
    assert len(text) != len(path.read_text(encoding="utf-8"))
    path.write_text(text, encoding="utf-8")


def test_an_unchanged_tree_reads_the_stored_expansion(tree: Tree, expansions: list[Path]) -> None:
    first = _project(tree)
    assert all(word in first.text for word in ("'old'", "'late'", "'deep'")), first.text
    assert _project(tree).text == first.text
    assert expansions == [tree.user], "何も変えていないのに展開し直した"


def test_a_changed_macro_module_expands_again(tree: Tree, expansions: list[Path]) -> None:
    # 失敗ケース 2: require した macro の module の source を変えたら作り直す。
    _project(tree)
    _rewrite(
        tree.package / "macros.hy",
        MACROS.replace("(defmacro said [] (word))", '(defmacro said [] "newer")'),
    )
    assert "'newer'" in _project(tree).text
    assert len(expansions) == 2


def test_a_changed_helper_a_macro_calls_expands_again(tree: Tree, expansions: list[Path]) -> None:
    # 失敗ケース 3: macro が展開の中で呼ぶ補助の Python の module(macro でない code)を変えたら作り直す。
    # 以前のキーは require の先の .hy だけを入れたので、words.py を変えても古い展開が当たった。
    _project(tree)
    _rewrite(tree.package / "words.py", 'def word() -> str:\n    return "newer"\n')
    assert "'newer'" in _project(tree).text
    assert len(expansions) == 2


def test_a_changed_helper_imported_in_a_macro_body_expands_again(
    tree: Tree, expansions: list[Path]
) -> None:
    # 失敗ケース 4: macro の本体の中でだけ import する補助の module を変えたら作り直す。
    _project(tree)
    _rewrite(tree.package / "late.py", 'def word() -> str:\n    return "later"\n')
    assert "'later'" in _project(tree).text
    assert len(expansions) == 2


def test_a_changed_macro_of_a_transitive_require_expands_again(
    tree: Tree, expansions: list[Path]
) -> None:
    # 失敗ケース 8: macro の module が require する先の macro を変えたら作り直す。
    _project(tree)
    _rewrite(tree.package / "deeper.hy", '(defmacro deep [] "deeper")\n')
    assert "'deeper'" in _project(tree).text
    assert len(expansions) == 2


def test_a_relative_require_follows_the_macro_module_of_its_package(
    tree: Tree, expansions: list[Path]
) -> None:
    # 失敗ケース 2 の相対の require(`.macros`)の形(agora-redesign #2696)。
    tree.user.write_text("(require .deeper [deep])\n(setv c (deep))\n", encoding="utf-8")
    _project(tree)
    _rewrite(tree.package / "deeper.hy", '(defmacro deep [] "deeper")\n')
    assert "'deeper'" in _project(tree).text
    assert len(expansions) == 2


def test_the_hy_version_is_part_of_what_the_expansion_used(
    tree: Tree, expansions: list[Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    # 失敗ケース 7: Hy の版が変わったら作り直す(agora-redesign #2774)。
    import hy

    _project(tree)
    monkeypatch.setattr(hy, "__version__", f"{hy.__version__}.probe")
    _project(tree)
    assert len(expansions) == 2


def test_another_worktree_checks_its_own_macro_files(
    tmp_path: Path, expansions: list[Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    # 失敗ケース 9: 別の作業木で作った保存は、出所の file を今の環境の module 名から引き直して照らす。同じ中身の source・
    # 同じ module 名・同じ相対 path でも、今の木の補助が違えば作り直す(保存の先は 2 つの木で同じ)。
    monkeypatch.setattr(sys, "dont_write_bytecode", True)
    monkeypatch.setenv("DOEFF_HY_CODE_STORE", "off")
    cache = tmp_path / ".cache"
    first = _write_tree(tmp_path / "first")
    second = _write_tree(tmp_path / "second", words="other")
    try:
        monkeypatch.syspath_prepend(str(first.root))
        assert "'old'" in _project(Tree(first.root, cache)).text
        sys.path.remove(str(first.root))
        monkeypatch.syspath_prepend(str(second.root))
        assert "'other'" in _project(Tree(second.root, cache)).text
        assert len(expansions) == 2
    finally:
        _forget(PACKAGE)


OUTSIDE_PACKAGE = "probe_outside_macros"


@pytest.fixture
def outside_macros(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """根(proj)の外に置いた別の package の macro の module(outside/probe_outside_macros/macros.hy — sys.path から引ける)と、
    根の下でそれを require する proj/probe.hy。"""
    outside = tmp_path / "outside"
    monkeypatch.syspath_prepend(str(outside))
    monkeypatch.setattr(sys, "dont_write_bytecode", True)
    monkeypatch.setenv("DOEFF_HY_CODE_STORE", "off")
    package = outside / OUTSIDE_PACKAGE
    package.mkdir(parents=True)
    (package / "__init__.hy").write_text("", encoding="utf-8")
    macros = package / "macros.hy"
    macros.write_text('(defmacro said [] "old")\n', encoding="utf-8")
    project = tmp_path / "proj"
    project.mkdir()
    (project / "probe.hy").write_text(
        f"(require {OUTSIDE_PACKAGE}.macros [said])\n(setv word (said))\n", encoding="utf-8"
    )
    yield macros
    _forget(OUTSIDE_PACKAGE)


def test_a_changed_macro_of_another_package_outside_the_roots_expands_again(
    tmp_path: Path, outside_macros: Path, expansions: list[Path]
) -> None:
    # 失敗ケース 5: 根の外の別の package の macro(例 doeff-adr.macros)を変えたら作り直す(agora-redesign #2774)。
    project = tmp_path / "proj"
    cache = tmp_path / ".cache"
    first = project_cached(project, [project], project / "probe.hy", cache)
    assert isinstance(first, Projection), first
    assert "'old'" in first.text
    _forget(OUTSIDE_PACKAGE)
    _rewrite(outside_macros, '(defmacro said [] "newer")\n')
    second = project_cached(project, [project], project / "probe.hy", cache)
    assert isinstance(second, Projection), second
    assert "'newer'" in second.text
    assert len(expansions) == 2


# ---- 展開が使った macro 単位の記録 -----------------------------------------------------------------------------


def test_a_changed_body_of_a_used_macro_expands_again(tree: Tree, expansions: list[Path]) -> None:
    # 冷えるべき時: user.hy が使う macro(said-deep)の本体を替えたら作り直す。
    _project(tree)
    _rewrite(
        tree.package / "macros.hy",
        MACROS.replace("(defmacro said-deep [] (deep))", '(defmacro said-deep [] "used-now")'),
    )
    assert "'used-now'" in _project(tree).text
    assert len(expansions) == 2


def test_a_changed_unused_macro_keeps_the_stored_expansion(tree: Tree, expansions: list[Path]) -> None:
    # 冷えなくてよい時(直す前は赤): user.hy が使わない macro の本体を替えても、保存から引く。
    _project(tree)
    _rewrite(
        tree.package / "macros.hy",
        MACROS.replace('(defmacro unused [] "unused")', '(defmacro unused [] "unused-now")'),
    )
    _project(tree)
    assert expansions == [tree.user], "使わない macro を替えただけで展開し直した"


# ---- doeff_hy 自身の file を変える検(doeff_hy を写して別の process で走らせる)---------------------------------

#: 写した doeff_hy で project_cached を 1 度走らせ、展開した数と読んだ doeff_hy の在りかを JSON で出す。
CHILD = """\
import json, sys
from pathlib import Path
from doeff_hy import static_check
seen = []
real = static_check.project
def counting(root, roots, source):
    seen.append(str(source))
    return real(root, roots, source)
static_check.project = counting
root, cache = Path(sys.argv[1]), Path(sys.argv[2])
result = static_check.project_cached(root, [root], root / "probe.hy", cache)
print(json.dumps({"expanded": len(seen), "projected": isinstance(result, static_check.Projection),
                  "doeff_hy": static_check.__file__}))
"""

PROBE = """\
(require doeff-hy.macros [defk])

(defk twice [x]
  {:pre [(: x int)] :post [(: % int)]}
  (* 2 x))
"""


@dataclass(frozen=True)
class CopiedDoeffHy:
    """一時の dir に写した doeff_hy(lib/doeff_hy)と、それを使う子の process の根・保存先。子の process は fixture が
    PYTHONPATH(写しを先に読ませる)と PYTHONPYCACHEPREFIX(bytecode を一時の dir へ)を与えた環境を継ぐ。"""

    lib: Path
    root: Path
    cache: Path

    @property
    def package(self) -> Path:
        return self.lib / "doeff_hy"

    def expanded(self) -> int:
        """子の process で 1 度走らせ、展開した数を返す(保存から引けば 0)。"""
        done = subprocess.run(
            [sys.executable, "-c", CHILD, str(self.root), str(self.cache)],
            capture_output=True,
            text=True,
            timeout=25,
            check=False,
        )
        assert done.returncode == 0, done.stderr
        answer = json.loads(done.stdout.strip().splitlines()[-1])
        assert answer["projected"], done.stderr
        assert Path(answer["doeff_hy"]).is_relative_to(self.package), (
            answer
        )  # 写した doeff_hy を読んだ
        return int(answer["expanded"])


@pytest.fixture
def copied_doeff_hy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> CopiedDoeffHy:
    lib = tmp_path / "lib"
    monkeypatch.setenv("PYTHONPATH", str(lib))
    monkeypatch.setenv("PYTHONPYCACHEPREFIX", str(tmp_path / "pycache"))
    shutil.copytree(
        Path(doeff_hy.__file__).parent,
        lib / "doeff_hy",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    root = tmp_path / "proj"
    root.mkdir()
    (root / "probe.hy").write_text(PROBE, encoding="utf-8")
    return CopiedDoeffHy(lib, root, tmp_path / ".cache")


def _append_comment(path: Path) -> None:
    comment = "\n;; 変えた\n" if path.suffix == ".hy" else "\n# 変えた\n"
    path.write_text(path.read_text(encoding="utf-8") + comment, encoding="utf-8")


def test_a_doeff_hy_file_the_expansion_does_not_go_through_keeps_the_stored_expansion(
    copied_doeff_hy: CopiedDoeffHy,
) -> None:
    # 失敗ケース 1: 展開が通らない doeff_hy の file(HTTP の部品 http.hy)を変えても、保存から引く。以前は doeff_hy の
    # package 全体の指紋がキーに入り、関係ない commit 1 つで全部の展開が作り直しになっていた(直す前は赤)。
    assert copied_doeff_hy.expanded() == 1
    _append_comment(copied_doeff_hy.package / "http.hy")
    assert copied_doeff_hy.expanded() == 0


def test_a_changed_type_check_post_processing_expands_again(copied_doeff_hy: CopiedDoeffHy) -> None:
    # 失敗ケース 6: 型検査の展開の後処理(static_check の記帳の外し・補助の import の足し)は macro から辿れない。
    # それを変えたら作り直す。
    assert copied_doeff_hy.expanded() == 1
    _append_comment(copied_doeff_hy.package / "static_check.py")
    assert copied_doeff_hy.expanded() == 1


def test_a_changed_doeff_hy_macro_expands_again(copied_doeff_hy: CopiedDoeffHy) -> None:
    # 失敗ケース 2 の doeff_hy の形: doeff_hy の macro(macros.hy)を変えたら作り直す。
    assert copied_doeff_hy.expanded() == 1
    _append_comment(copied_doeff_hy.package / "macros.hy")
    assert copied_doeff_hy.expanded() == 1


# ---- 保存先(agora-redesign #3863)--------------------------------------------------------------------------


def test_doeff_hy_check_cache_names_the_place_of_the_stored_expansions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 失敗ケース(#3863): 日次の検証の task は HOME を走りごとの空の dir に替えるので、XDG の cache の下の保存先は毎回空で、
    # 全部の .hy を展開し直していた。DOEFF_HY_CHECK_CACHE が名指す dir を保存先にし、走りをまたいで残る dir へ向けられる
    # ようにする(テストの実行の側の DOEFF_HY_CODE_STORE・doeff-effect-analyzer の DOEFF_EFFECT_ANALYZER_CACHE と同じ形)。
    from doeff_hy.static_cache import default_cache_dir

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    assert default_cache_dir() == tmp_path / "xdg" / "doeff-hy-check"
    monkeypatch.setenv("DOEFF_HY_CHECK_CACHE", str(tmp_path / "kept"))
    assert default_cache_dir() == tmp_path / "kept"


# ---- 2 つの書き手(agora-redesign #3863 の (b) — 2 台の worker が保存先を共有する)--------------------------------------------

#: 子の process が同じ entry へ何度も書く(大きい中身で、書きの途中に別の書き手の書きが重なるようにする)。
WRITER = """\
import sys
from pathlib import Path
from doeff_hy.static_cache import CachedProjection, store
from doeff_hy_bytecode_guard import record_from_rows
cache, mark, rounds = Path(sys.argv[1]), sys.argv[2], int(sys.argv[3])
used = record_from_rows("probe", ())
for _ in range(rounds):
    store(cache, "a" * 64, CachedProjection(mark * 400_000, (), (), used))
"""


def test_two_writers_of_one_entry_do_not_mix(tmp_path: Path) -> None:
    # 失敗ケース(#3863 の (b)): 保存の書きの一時の file の名が <entry>.tmp で固定だったので、2 つの process(共有の保存先を使う
    # 2 台の worker)が同じ entry を同時に書くと、同じ一時の file へ互いに書き込み、混ざった中身が rename されうる。書き手ごとの
    # 一時の file に書いてから rename する。最後の entry は、どちらかの書き手の中身そのまま(読める JSON)で、一時の file は残らない。
    import json
    import subprocess

    from doeff_hy import static_cache

    cache = tmp_path / ".cache"
    writers = [
        subprocess.Popen([sys.executable, "-c", WRITER, str(cache), mark, "40"]) for mark in ("x", "y")
    ]
    assert [writer.wait(timeout=25) for writer in writers] == [0, 0]
    entries = list(cache.rglob("*.json"))
    assert len(entries) == 1, entries
    text = json.loads(entries[0].read_text(encoding="utf-8"))["text"]
    assert text in {"x" * 400_000, "y" * 400_000}, "2 つの書き手の中身が混ざった"
    assert not [p for p in cache.rglob("*") if p.is_file() and p.suffix != ".json"], "一時の file が残った"
    assert static_cache.CACHE_VERSION == 2


def test_each_writer_uses_its_own_temporary_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # 失敗ケース(#3863 の (b)): 2 度の書きが同じ一時の file を使わない(直す前は <entry>.tmp で同じ — 赤)。
    from doeff_hy import static_cache
    from doeff_hy_bytecode_guard import record_from_rows

    written: list[str] = []
    real = Path.write_text

    def recording(path: Path, data: str, *args: object, **kwargs: object) -> int:
        written.append(path.name)
        return real(path, data, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", recording)
    used = record_from_rows("probe", ())
    for _ in range(2):
        static_cache.store(tmp_path, "b" * 64, static_cache.CachedProjection("t", (), (), used))
    assert len(written) == 2
    assert written[0] != written[1], written
