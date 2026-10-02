"""検の session が立てる使い捨ての PostgreSQL(postgres_support/disposable_postgres.py — agora-redesign #2830)の検。

- env が無く binary が在れば、立てた PostgreSQL を env が指し、実際に ``select 1`` が通る(session の用意と、部品を直に呼んだ形の両方)。
  反例 = conftest が用意を呼ばない形では、session の env が無く赤になる。
- binary を用意できない形(外皮の path を壊す・uv が無い)では env を置かず、skip の理由に「使い捨ての PostgreSQL を用意できない」と
  その理由が出る(赤にしない)。
"""

from __future__ import annotations

import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path

import pytest
from disposable_postgres import (
    CLIENT_SECONDS,
    DEFAULT_SOURCE,
    RECORDS_VARIABLE,
    SESSION_BINDINGS,
    SHIM,
    SQL_EFFECTS_VARIABLE,
    UNAVAILABLE_PREFIX,
    BinarySource,
    DisposablePostgresError,
    PostgresStarted,
    PostgresUnavailable,
    postgres_bin_dir,
    postgres_skip_reason,
    provide_postgres,
    session_dsn,
    session_provision,
)


def select_one(psql: Path, dsn: str) -> str:
    """dsn の PostgreSQL に ``select 1`` を流し、答えの字面を返す。"""
    answered = subprocess.run(
        (str(psql), "-X", "-tA", "-c", "select 1", dsn),
        capture_output=True,
        text=True,
        timeout=CLIENT_SECONDS,
        check=False,
    )
    assert answered.returncode == 0, answered.stderr
    return answered.stdout.strip()


def test_session_environment_points_at_a_live_postgres() -> None:
    """conftest の用意の後、2 つの DSN が在り、どちらも ``select 1`` に答える PostgreSQL を指す。"""
    provision = session_provision()
    if isinstance(provision, PostgresUnavailable):
        pytest.skip(f"{UNAVAILABLE_PREFIX}: {provision.reason}")
    if isinstance(provision, PostgresStarted):
        psql = provision.server.tool("psql")
    else:
        try:
            psql = postgres_bin_dir(DEFAULT_SOURCE) / "psql"
        except DisposablePostgresError as failed:
            pytest.skip(f"{UNAVAILABLE_PREFIX}: {failed}")
    # 検の module が受ける DSN(用意の結果の dsns — env には書かない・agora-redesign #3012)。
    for variable in (SQL_EFFECTS_VARIABLE, RECORDS_VARIABLE):
        dsn = session_dsn(variable)
        assert dsn, f"{variable} の DSN が無い(conftest が使い捨ての PostgreSQL を用意していない)"
        assert select_one(psql, dsn) == "1"


def test_without_environment_a_started_postgres_answers_select_one_and_is_removed() -> None:
    """外から DSN が渡されなければ立てて結果に DSN を持たせ、どの database も ``select 1`` に答え、後始末で止まって一時の dir が消える。"""
    cleanups: list[Callable[[], None]] = []
    provision = provide_postgres(SESSION_BINDINGS, {}, cleanups.append)
    if isinstance(provision, PostgresUnavailable):
        pytest.skip(f"{UNAVAILABLE_PREFIX}: {provision.reason}")
    try:
        assert isinstance(provision, PostgresStarted), provision
        # data の dir は TMPDIR に従う(作業木の中に作らない)。
        assert provision.server.root.parent == Path(tempfile.gettempdir())
        assert {binding.variable for binding in provision.bound} == {
            SQL_EFFECTS_VARIABLE,
            RECORDS_VARIABLE,
        }
        for binding in SESSION_BINDINGS:
            dsn = dict(provision.dsns)[binding.variable]
            assert binding.database in dsn
            assert select_one(provision.server.tool("psql"), dsn) == "1"
    finally:
        for cleanup in cleanups:
            cleanup()
    assert not provision.server.root.exists()


def test_existing_environment_is_used_and_nothing_is_started() -> None:
    """DSN が全部外から渡されていれば何も立てず、後始末も積まず、渡された DSN をそのまま持つ。"""
    given = {binding.variable: "postgresql://someone@/mine" for binding in SESSION_BINDINGS}
    cleanups: list[Callable[[], None]] = []
    provision = provide_postgres(SESSION_BINDINGS, given, cleanups.append)
    assert not isinstance(provision, PostgresStarted | PostgresUnavailable), provision
    assert cleanups == []
    assert dict(provision.dsns) == given
    assert postgres_skip_reason(SQL_EFFECTS_VARIABLE, provision) == ""


def failing_shim(tmp: Path) -> Path:
    """binary を出さずに失敗する外皮(lock が無いので uv が --locked で断る — download できない・wheel が無い形の代役)。"""
    shim = tmp / "failing_shim.py"
    shim.write_text(
        '# /// script\n# requires-python = ">=3.12,<3.13"\n# dependencies = []\n# ///\n'
        'raise SystemExit("no PostgreSQL binaries here")\n',
        encoding="utf-8",
    )
    return shim


@pytest.mark.parametrize(
    ("source_of", "detail"),
    [
        (lambda tmp: BinarySource(uv="uv", shim=tmp / "missing" / "shim.py"), "外皮が無い"),
        (lambda tmp: BinarySource(uv=str(tmp / "no-uv"), shim=SHIM), "実行ファイルが無い"),
        (lambda tmp: BinarySource(uv="uv", shim=failing_shim(tmp)), "exit "),
    ],
    ids=["broken-shim-path", "uv-absent", "shim-fails"],
)
def test_unpreparable_binaries_skip_with_the_named_reason(
    tmp_path: Path, source_of: Callable[[Path], BinarySource], detail: str
) -> None:
    """binary を用意できなければ DSN を持たず、skip の理由に「使い捨ての PostgreSQL を用意できない: <理由>」が出る。"""
    cleanups: list[Callable[[], None]] = []
    provision = provide_postgres(SESSION_BINDINGS, {}, cleanups.append, source_of(tmp_path))
    assert isinstance(provision, PostgresUnavailable), provision
    assert cleanups == []
    for binding in SESSION_BINDINGS:
        reason = postgres_skip_reason(binding.variable, provision)
        assert reason.startswith(f"{UNAVAILABLE_PREFIX}: "), reason
        assert detail in reason, reason
        assert "\n" not in reason, reason
