"""macro が変わった Hy の module の古い bytecode を使わない包み(doeff_hy_bytecode_guard・agora-redesign #1292)の反例。

#1003 の形: macro の提供元を変えても、使う側の module の .pyc は source の更新時刻と大きさしか見ない Python の判定で
有効とされ、古い展開のまま動く。包みがあれば、使う側は新しい展開で動く。

Hy は **PyPI の版**(fork ではない — 利用者の決定 2026-09-29「Hy には一切手を入れない」)を、この venv と切り離した
使い捨ての環境(``uv run --no-project --isolated --with hy==1.3.1``)で使う。この venv の Hy は fork で、fork 自身の
仕組み(.hydeps)が同じ赤を消してしまうので、ここでは包みの効き目を分けて測れない。
"""

from __future__ import annotations

import importlib.util
import marshal
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from doeff_hy_bytecode_guard import records

PYPI_HY = "hy==1.3.1"
#: 包みの package の親(doeff-hy の src)— 使い捨ての環境へは path で渡す(doeff-hy 全体は入れない)。
GUARD_ROOT = Path(__file__).resolve().parents[1] / "src"

#: 使い捨ての環境へ渡さない環境変数(この suite の固定 PYTHONDONTWRITEBYTECODE=1 は .pyc を書かせないので、
#: 子が .pyc を書けず #1003 の形にならない。venv の指定は uv が使い捨ての環境を作るのを妨げる)。
_ENV_NOT_PASSED = (
    "VIRTUAL_ENV",
    "CONDA_PREFIX",
    "PYTHONDONTWRITEBYTECODE",
    "PYTHONPYCACHEPREFIX",
    "PYTHONPATH",
)

_LOADER = """\
import sys
sys.path.insert(0, {root!r})
guard = {guard!r}


def load_guard():
    # 包みの package は checkout の中にあるので、その .pyc を checkout に書かない(root の conftest の検査)—
    # 記録の module も先に読んでおく(包みは Hy の source に初めて当たった時に読む)。
    sys.dont_write_bytecode = True
    sys.path.insert(0, {guard_root!r})
    import doeff_hy_bytecode_guard
    import doeff_hy_bytecode_guard.records
    doeff_hy_bytecode_guard.install()
    sys.dont_write_bytecode = False


if guard == "before-hy":
    load_guard()
import hy
if guard == "after-hy":
    load_guard()
import pkg.user
print(hy.__version__, pkg.user.value)
"""


def _write_package(root: Path) -> None:
    """macro の提供元(macros)と、macro が展開の時に呼ぶ補助(helpers)と、使う側(user)の 3 つ。"""
    package = root / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "helpers.hy").write_text("(defn base [] 10)\n")
    (package / "macros.hy").write_text(
        "(import pkg.helpers [base])\n(defmacro answer [] (+ (base) 1))\n"
    )
    (package / "user.hy").write_text("(require pkg.macros [answer])\n(setv value (answer))\n")


def _edit(path: Path, text: str) -> None:
    """file を書き換え、更新時刻を 2 秒進める — Python 自身の .pyc の判定は更新時刻を秒で見るので、同じ秒に同じ大きさで
    書き換えると提供元自身の .pyc が古いまま有効とされる(それは Python の限界で、この包みの対象ではない)。"""
    before = path.stat().st_mtime_ns
    path.write_text(text)
    later = before + 2_000_000_000
    os.utime(path, ns=(later, later))


def _run_on_pypi_hy(root: Path, guard: str) -> int:
    """PyPI の Hy の使い捨ての環境で pkg.user を import し、展開された値を返す。"""
    uv = shutil.which("uv")
    assert uv is not None, "uv が PATH に無い — PyPI の Hy の使い捨ての環境を作れない"
    script = root / f"load_{guard}.py"
    script.write_text(_LOADER.format(root=str(root), guard=guard, guard_root=str(GUARD_ROOT)))
    env = {key: value for key, value in os.environ.items() if key not in _ENV_NOT_PASSED}
    python = f"{sys.version_info.major}.{sys.version_info.minor}"
    completed = subprocess.run(
        [
            uv,
            "run",
            "--no-project",
            "--isolated",
            "--python",
            python,
            "--with",
            PYPI_HY,
            "python",
            str(script),
        ],
        capture_output=True,
        text=True,
        env=env,
        cwd=root,
        timeout=300,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    hy_version, value = completed.stdout.split()
    assert hy_version == PYPI_HY.split("==")[1]
    return int(value)


def _user_pyc(root: Path) -> Path:
    [pyc] = (root / "pkg" / "__pycache__").glob("user.*.pyc")
    return pyc


def test_pypi_hy_alone_keeps_the_old_expansion(tmp_path: Path) -> None:
    """包みが無いと #1003 の赤がそのまま出る(この反例のテストが意味を持つことの確かめ)。"""
    _write_package(tmp_path)
    assert _run_on_pypi_hy(tmp_path, "none") == 11
    _edit(
        tmp_path / "pkg" / "macros.hy",
        "(import pkg.helpers [base])\n(defmacro answer [] (+ (base) 2))\n",
    )
    assert _run_on_pypi_hy(tmp_path, "none") == 11


@pytest.mark.parametrize("guard", ["before-hy", "after-hy"])
def test_a_macro_change_recompiles_the_user_on_pypi_hy(tmp_path: Path, guard: str) -> None:
    """macro を変えた後、使う側は新しい展開で動く — 包みを Hy の import の前に入れても(.pth)後に入れても。"""
    _write_package(tmp_path)
    assert _run_on_pypi_hy(tmp_path, guard) == 11
    _edit(
        tmp_path / "pkg" / "macros.hy",
        "(import pkg.helpers [base])\n(defmacro answer [] (+ (base) 2))\n",
    )
    assert _run_on_pypi_hy(tmp_path, guard) == 12
    assert _run_on_pypi_hy(tmp_path, guard) == 12


def test_a_change_of_the_helper_a_macro_calls_recompiles_the_user(tmp_path: Path) -> None:
    """macro が展開の時に呼ぶ同じ package の補助を変えても、使う側は作り直される。"""
    _write_package(tmp_path)
    assert _run_on_pypi_hy(tmp_path, "before-hy") == 11
    _edit(tmp_path / "pkg" / "helpers.hy", "(defn base [] 20)\n")
    assert _run_on_pypi_hy(tmp_path, "before-hy") == 21


def test_the_record_lives_inside_a_standard_pyc(tmp_path: Path) -> None:
    """記録は .pyc の中(code の定数)にあり、__pycache__ には標準の .pyc しか無い(.hydeps のような隣の file を書かない)。"""
    _write_package(tmp_path)
    _run_on_pypi_hy(tmp_path, "before-hy")
    cache = tmp_path / "pkg" / "__pycache__"
    assert sorted(path.suffix for path in cache.iterdir()) == [".pyc"] * 4
    data = _user_pyc(tmp_path).read_bytes()
    assert data[:4] == importlib.util.MAGIC_NUMBER
    record = records.record_of(marshal.loads(data[records.PYC_HEADER_BYTES :]))
    assert record is not None
    assert record.hy_version == PYPI_HY.split("==")[1]
    assert [
        (dependency.module, Path(dependency.file).name) for dependency in record.dependencies
    ] == [
        ("pkg.helpers", "helpers.hy"),
        ("pkg.macros", "macros.hy"),
    ]


def _rewrite_as_hash_based(pyc: Path, source: Path, *, checked: bool) -> None:
    """.pyc の頭を PEP 552 の hash 方式へ差し替える(image の組み立ての道具が焼く形 = unchecked)。"""
    data = pyc.read_bytes()
    flags = records.FLAG_HASH_BASED | (records.FLAG_CHECK_SOURCE if checked else 0)
    header = (
        importlib.util.MAGIC_NUMBER
        + flags.to_bytes(4, "little")
        + importlib.util.source_hash(source.read_bytes())
    )
    pyc.write_bytes(header + data[records.PYC_HEADER_BYTES :])


def test_a_checked_hash_pyc_is_recompiled_in_the_same_form(tmp_path: Path) -> None:
    """Python が source と突き合わせる hash 方式の .pyc も作り直し、同じ方式(checked-hash)で書き直す。"""
    _write_package(tmp_path)
    _run_on_pypi_hy(tmp_path, "before-hy")
    _rewrite_as_hash_based(_user_pyc(tmp_path), tmp_path / "pkg" / "user.hy", checked=True)
    _edit(
        tmp_path / "pkg" / "macros.hy",
        "(import pkg.helpers [base])\n(defmacro answer [] (+ (base) 2))\n",
    )
    assert _run_on_pypi_hy(tmp_path, "before-hy") == 12
    flags = int.from_bytes(_user_pyc(tmp_path).read_bytes()[4:8], "little")
    assert flags == records.FLAG_HASH_BASED | records.FLAG_CHECK_SOURCE


def test_an_unchecked_hash_pyc_is_trusted_like_python_trusts_it(tmp_path: Path) -> None:
    """Python が source と突き合わせない .pyc(image の形)は、macro の変更も突き合わせない — 組み立ての中で焼き直す前提。"""
    _write_package(tmp_path)
    _run_on_pypi_hy(tmp_path, "before-hy")
    _rewrite_as_hash_based(_user_pyc(tmp_path), tmp_path / "pkg" / "user.hy", checked=False)
    _edit(
        tmp_path / "pkg" / "macros.hy",
        "(import pkg.helpers [base])\n(defmacro answer [] (+ (base) 2))\n",
    )
    assert _run_on_pypi_hy(tmp_path, "before-hy") == 11


def test_the_venv_installs_the_guard_at_startup_before_hy() -> None:
    """doeff-hy を入れた venv では、どの Python も起動の時点(Hy の import より前)で包みが入っている(.pth)。"""
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys, doeff_hy_bytecode_guard as g; print(g.installed(), 'hy' in sys.modules)",
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.split() == ["True", "False"]
