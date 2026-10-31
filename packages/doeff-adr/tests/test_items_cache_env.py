"""item の記録の置き場を環境変数 DOEFF_ADR_ITEMS_CACHE で名指せる。

日次の全体検証の task は HOME を回ごとに空の dir に替えるので、既定の置き場(HOME の下)では記録が毎回空になり、atlas の日次では
収集のために 309 file を import していた。日次の task が回をまたぐ置き場を子へ渡せるよう、置き場は ini
``doeff_adr_items_cache`` → 環境変数 ``DOEFF_ADR_ITEMS_CACHE`` → HOME の下の既定、の順で決まる。

別の process で pytest を走らせる(pytester の subprocess・HOME は pytester が tmp の dir に替える)。pyc の置き場も tmp の下。
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
    """検で見る置き場: 環境変数で名指す dir・ini で名指す dir・HOME の下の既定の dir。"""

    env: Path
    ini: Path
    home_default: Path


@pytest.fixture
def dirs(pytester: pytest.Pytester, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Dirs:
    """Hy の test file 1 つの project。記録の置き場はどれも tmp か pytester の HOME の下。"""
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
    """別の process で --collect-only を走らせ、収集の終わりの報告の行(記録から何 file・import 何 file)を返す。"""
    result = pytester.runpytest_subprocess("--collect-only", "-q", "-p", "no:cacheprovider")
    lines = [line for line in result.stdout.lines if line.startswith("doeff-adr: 記録から収集")]
    assert lines, result.stdout.str()
    return lines[0]


def _records(directory: Path) -> list[str]:
    return sorted(path.name for path in directory.glob("*.json")) if directory.exists() else []


def test_env_names_the_dir_and_the_next_collection_reads_from_it(
    pytester: pytest.Pytester, dirs: Dirs, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ini が無ければ、環境変数で名指した dir に記録が置かれ、次の収集はそこから引く(HOME の下には書かない)。"""
    pytester.makeini(INI_HEAD)
    monkeypatch.setenv(ENV, str(dirs.env))
    assert _collect_summary(pytester) == "doeff-adr: 記録から収集 0 file・収集で import 1 file"
    assert len(_records(dirs.env)) == 1
    assert _records(dirs.home_default) == []
    assert _collect_summary(pytester) == "doeff-adr: 記録から収集 1 file・収集で import 0 file"


def test_ini_wins_over_env(
    pytester: pytest.Pytester, dirs: Dirs, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ini ``doeff_adr_items_cache`` が在れば、環境変数より ini の dir を使う。"""
    pytester.makeini(INI_HEAD + f"doeff_adr_items_cache = {dirs.ini}\n")
    monkeypatch.setenv(ENV, str(dirs.env))
    _collect_summary(pytester)
    assert len(_records(dirs.ini)) == 1
    assert _records(dirs.env) == []


def test_without_ini_and_env_the_dir_is_under_home(pytester: pytest.Pytester, dirs: Dirs) -> None:
    """ini も環境変数も無ければ、HOME の下の既定の dir を使う。"""
    pytester.makeini(INI_HEAD)
    _collect_summary(pytester)
    assert len(_records(dirs.home_default)) == 1
