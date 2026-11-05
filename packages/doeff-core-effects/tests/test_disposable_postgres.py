"""Tests of the disposable PostgreSQL that the test session starts (postgres_support/disposable_postgres.py).

- If the env var is missing and the binaries exist, the env var points at the started PostgreSQL and ``select 1`` really goes through (both the session preparation and the form that calls the component directly).
  Counterexample = if conftest does not call the preparation, the session env var is missing and the test goes red.
- If the binaries cannot be prepared (broken shim path, no uv), no env var is set, and the skip reason says "cannot prepare a disposable PostgreSQL" with
  the reason (not red).
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
    """Run ``select 1`` against the PostgreSQL at the dsn and return the literal text of the answer."""
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
    """After the conftest preparation, the two DSNs exist and both point at a PostgreSQL that answers ``select 1``."""
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
    # The DSNs the test modules receive (dsns of the preparation result -- not written to env).
    for variable in (SQL_EFFECTS_VARIABLE, RECORDS_VARIABLE):
        dsn = session_dsn(variable)
        assert dsn, f"no DSN for {variable} (conftest did not prepare a disposable PostgreSQL)"
        assert select_one(psql, dsn) == "1"


def test_without_environment_a_started_postgres_answers_select_one_and_is_removed() -> None:
    """If no DSN is passed from outside, start one and put the DSNs in the result; every database answers ``select 1``; cleanup stops it and removes the temporary dir."""
    cleanups: list[Callable[[], None]] = []
    provision = provide_postgres(SESSION_BINDINGS, {}, cleanups.append)
    if isinstance(provision, PostgresUnavailable):
        pytest.skip(f"{UNAVAILABLE_PREFIX}: {provision.reason}")
    try:
        assert isinstance(provision, PostgresStarted), provision
        # The data dir follows TMPDIR (not created inside the working tree).
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
    """If all DSNs are passed from outside, start nothing, push no cleanup, and keep the DSNs as passed."""
    given = {binding.variable: "postgresql://someone@/mine" for binding in SESSION_BINDINGS}
    cleanups: list[Callable[[], None]] = []
    provision = provide_postgres(SESSION_BINDINGS, given, cleanups.append)
    assert not isinstance(provision, PostgresStarted | PostgresUnavailable), provision
    assert cleanups == []
    assert dict(provision.dsns) == given
    assert postgres_skip_reason(SQL_EFFECTS_VARIABLE, provision) == ""


def failing_shim(tmp: Path) -> Path:
    """A shim that fails without emitting binaries (no lock, so uv refuses under --locked -- a stand-in for the cases where download fails or there is no wheel)."""
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
        (lambda tmp: BinarySource(uv="uv", shim=tmp / "missing" / "shim.py"), "shim not found"),
        (lambda tmp: BinarySource(uv=str(tmp / "no-uv"), shim=SHIM), "executable not found"),
        (lambda tmp: BinarySource(uv="uv", shim=failing_shim(tmp)), "exit "),
    ],
    ids=["broken-shim-path", "uv-absent", "shim-fails"],
)
def test_unpreparable_binaries_skip_with_the_named_reason(
    tmp_path: Path, source_of: Callable[[Path], BinarySource], detail: str
) -> None:
    """If the binaries cannot be prepared, there is no DSN and the skip reason shows "cannot prepare a disposable PostgreSQL: <reason>"."""
    cleanups: list[Callable[[], None]] = []
    provision = provide_postgres(SESSION_BINDINGS, {}, cleanups.append, source_of(tmp_path))
    assert isinstance(provision, PostgresUnavailable), provision
    assert cleanups == []
    for binding in SESSION_BINDINGS:
        reason = postgres_skip_reason(binding.variable, provision)
        assert reason.startswith(f"{UNAVAILABLE_PREFIX}: "), reason
        assert detail in reason, reason
        assert "\n" not in reason, reason
