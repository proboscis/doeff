"""The place for item records can be named by the environment variable DOEFF_ADR_ITEMS_CACHE.

The daily whole-verification task replaces HOME with an empty dir on each run, so in the default place (under HOME) the records are empty every time, and the atlas daily imported
309 files for collection. So that the daily task can pass a place that spans runs to the child, the place is decided in the order ini
``doeff_adr_items_cache`` -> environment variable ``DOEFF_ADR_ITEMS_CACHE`` -> the default under HOME.

Run pytest in a separate process (pytester's subprocess; pytester replaces HOME with a tmp dir). The pyc place is also under tmp.
"""

from dataclasses import dataclass
from pathlib import Path

import pytest

pytest_plugins = ["pytester"]

ENV = "DOEFF_ADR_ITEMS_CACHE"

TEST_FILE = """
(require doeff-hy.macros [deftest])
(deftest test-one (assert True))
"""

INI_HEAD = """
[pytest]
doeff_adr_hy_files = pkg/test_*.hy
doeff_adr_wiring = off
"""


@dataclass(frozen=True)
class Dirs:
    """The places the tests look at: the dir named by the environment variable, the dir named by ini, and the default dir under HOME."""

    env: Path
    ini: Path
    home_default: Path


@pytest.fixture
def dirs(pytester: pytest.Pytester, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Dirs:
    """A project with one Hy test file. Every record place is under tmp or pytester's HOME."""
    for name, text in {"pkg/__init__.py": "", "pkg/test_one.hy": TEST_FILE}.items():
        path = pytester.path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    monkeypatch.setenv("PYTHONPYCACHEPREFIX", str(tmp_path / "pyc"))
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "")
    monkeypatch.delenv(ENV, raising=False)
    return Dirs(
        env=tmp_path / "env-items",
        ini=tmp_path / "ini-items",
        home_default=pytester.path / ".cache" / "doeff-adr" / "pytest-items",
    )


def _collect_summary(pytester: pytest.Pytester) -> str:
    """Run --collect-only in a separate process and return the line of the report at the end of collection (how many files from records, how many files imported)."""
    result = pytester.runpytest_subprocess("--collect-only", "-q", "-p", "no:cacheprovider")
    lines = [line for line in result.stdout.lines if line.startswith("doeff-adr: collected from record")]
    assert lines, result.stdout.str()
    return lines[0]


def _records(directory: Path) -> list[str]:
    return sorted(path.name for path in directory.glob("*.json")) if directory.exists() else []


def test_env_names_the_dir_and_the_next_collection_reads_from_it(
    pytester: pytest.Pytester, dirs: Dirs, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without ini, records are placed in the dir named by the environment variable, and the next collection reads from there (nothing is written under HOME)."""
    pytester.makeini(INI_HEAD)
    monkeypatch.setenv(ENV, str(dirs.env))
    assert _collect_summary(pytester) == "doeff-adr: collected from record: 0 files; imported during collection: 1 files"
    assert len(_records(dirs.env)) == 1
    assert _records(dirs.home_default) == []
    assert _collect_summary(pytester) == "doeff-adr: collected from record: 1 files; imported during collection: 0 files"


def test_ini_wins_over_env(
    pytester: pytest.Pytester, dirs: Dirs, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If ini ``doeff_adr_items_cache`` exists, the ini dir is used over the environment variable."""
    pytester.makeini(INI_HEAD + f"doeff_adr_items_cache = {dirs.ini}\n")
    monkeypatch.setenv(ENV, str(dirs.env))
    _collect_summary(pytester)
    assert len(_records(dirs.ini)) == 1
    assert _records(dirs.env) == []


def test_without_ini_and_env_the_dir_is_under_home(pytester: pytest.Pytester, dirs: Dirs) -> None:
    """With neither ini nor the environment variable, the default dir under HOME is used."""
    pytester.makeini(INI_HEAD)
    _collect_summary(pytester)
    assert len(_records(dirs.home_default)) == 1
