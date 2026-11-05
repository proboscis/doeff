"""A disposable PostgreSQL that the test session starts by itself.

The real-PostgreSQL tests of doeff (test_sql_effects.hy of doeff-core-effects, test_pg_*.hy and others of doeff-records) read the DSN from env
(``DOEFF_SQL_TEST_POSTGRES_DSN``, ``DOEFF_RECORDS_TEST_PG_DSN``) at module load time. Neither the daily machine nor a local machine has an external
PostgreSQL, so the conftest.py of the tests of both packages calls the
``provide_session_postgres`` here from pytest_configure (before the test modules are imported):

- If both env vars already exist, use them (for people who point at their own PostgreSQL locally). Start nothing.
- If some env var is missing, start one PostgreSQL 16 in a temporary dir under TMPDIR (``tempfile.mkdtemp``), create a separate database per env var
  (createdb), and set the env var to that DSN. At the end of pytest (``config.add_cleanup`` -- even when it ends with an exception), stop it and delete the dir with it.
  To prepare for endings where cleanup does not run, such as the watchdog's ``os._exit``, also place a watcher child process that does the same cleanup when the pytest process is gone.
- If the binaries cannot be prepared (no uv, cannot download, initdb fails), do not set the env var and remember a one-line reason. The tests read it
  with ``session_postgres_skip_reason`` and skip, naming "cannot prepare a disposable PostgreSQL: <reason>" (not red).

The binaries come bundled in the pgserver wheel; run the neighboring shim.py in a Python 3.12 environment split off by uv (``uv run --script --locked``; version and hash are in
shim.py.lock) and ask it for the bin dir -- this adds no dependency to the uv.lock of doeff. Data, unix socket, and log are placed inside the temporary dir, and
it does not listen on TCP (``listen_addresses = ''`` -- no port contention with parallel tests or other sessions). It is started once per pytest process.
Under xdist the controller starts nothing, and each worker starts its own in a separate dir. Model = agora-controllers'
controllers/foundation/tests/disposable_postgres.hy.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

import pytest
from doeff import run, with_handlers
from doeff_core_effects import subprocess_handler
from doeff_core_effects import ReadEnvironment
from doeff_core_effects import scheduled

SUPPORT_DIR = Path(__file__).resolve().parent
# The shim that tells where the binaries are (runs in a Python 3.12 environment split off by uv).
SHIM = SUPPORT_DIR / "shim.py"
# Upper limit (seconds) for waiting on the shim -- the first time, uv downloads Python 3.12 and the pgserver wheel.
SHIM_SECONDS = 600
# Upper limit (seconds) for waiting on initdb.
INITDB_SECONDS = 120
# Upper limit (seconds) for pg_ctl to wait for start and stop.
PG_CTL_SECONDS = 60
# Upper limit (seconds) for waiting on createdb / psql.
CLIENT_SECONDS = 60
# Maximum length of the one-line reason (it goes into the skip message).
REASON_CHARACTERS = 300

SQL_EFFECTS_VARIABLE = "DOEFF_SQL_TEST_POSTGRES_DSN"
RECORDS_VARIABLE = "DOEFF_RECORDS_TEST_PG_DSN"
UNAVAILABLE_PREFIX = "cannot prepare a disposable PostgreSQL"


@dataclass(frozen=True)
class DsnBinding:
    """The env var name, and the name of the database assigned to that env var on the disposable PostgreSQL."""

    variable: str
    database: str


# The env vars that the tests of the two packages read (separate databases on the same PostgreSQL).
SESSION_BINDINGS = (
    DsnBinding(variable=SQL_EFFECTS_VARIABLE, database="doeff_sql_effects"),
    DsnBinding(variable=RECORDS_VARIABLE, database="doeff_records"),
)


@dataclass(frozen=True)
class BinarySource:
    """How to ask where the binaries are: the uv executable and the shim path (tests plug in broken values to build the cannot-prepare case)."""

    uv: str
    shim: Path


DEFAULT_SOURCE = BinarySource(uv="uv", shim=SHIM)


@dataclass(frozen=True)
class DisposablePostgres:
    """The started PostgreSQL: bin_dir = binary dir, root = temporary dir (parent of data, socket, log)."""

    bin_dir: Path
    root: Path

    @property
    def data_dir(self) -> Path:
        return self.root / "data"

    @property
    def socket_dir(self) -> Path:
        return self.root / "sock"

    @property
    def log_file(self) -> Path:
        return self.root / "postgres.log"

    def tool(self, name: str) -> Path:
        """Paths of the bundled binaries (initdb, pg_ctl, createdb, psql)."""
        return self.bin_dir / name

    def dsn(self, database: str) -> str:
        """URL for connecting to the database (unix socket, user postgres, trust)."""
        host = urllib.parse.quote(str(self.socket_dir), safe="")
        return f"postgresql://postgres@/{database}?host={host}"


@dataclass(frozen=True)
class PostgresFromEnvironment:
    """All the required env vars already existed -- nothing was started (dsns = env var names and the DSNs passed from outside)."""

    dsns: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class PostgresStarted:
    """Started a disposable PostgreSQL and turned the missing env vars into DSNs of its databases (dsns = all of them, including those passed from outside)."""

    server: DisposablePostgres
    bound: tuple[DsnBinding, ...]
    dsns: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class PostgresUnavailable:
    """Could not prepare a disposable PostgreSQL (reason = one-line reason). No env var was set."""

    reason: str


@dataclass(frozen=True)
class PostgresLeftToWorkers:
    """The xdist controller -- each worker starts it (a separate dir per worker)."""


PostgresProvision = (
    PostgresFromEnvironment | PostgresStarted | PostgresUnavailable | PostgresLeftToWorkers
)


class DisposablePostgresError(Exception):
    """Could not prepare a disposable PostgreSQL (message = which step, and the last line of its output)."""


@dataclass(frozen=True)
class StepOutcome:
    """Result of a preparation child process (exit code and output)."""

    exit_code: int
    stdout: str
    stderr: str


def one_line(text: str) -> str:
    """Cut the last non-empty line of the output to a length that fits in the skip message."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return (lines[-1] if lines else "(no output)")[:REASON_CHARACTERS]


def run_step(step: str, argv: tuple[str, ...], timeout: float) -> StepOutcome:
    """Run one preparation child process; on failure raise DisposablePostgresError carrying the step name and the last line of the output."""
    try:
        completed = subprocess.run(
            argv, capture_output=True, text=True, timeout=timeout, check=False
        )
    except FileNotFoundError as missing:
        raise DisposablePostgresError(
            f"{step}: executable not found ({missing.filename})"
        ) from missing
    except subprocess.TimeoutExpired as expired:
        raise DisposablePostgresError(f"{step}: did not finish in {timeout:g} seconds") from expired
    except OSError as failed:
        raise DisposablePostgresError(f"{step}: cannot start ({failed})") from failed
    outcome = StepOutcome(
        exit_code=completed.returncode, stdout=completed.stdout, stderr=completed.stderr
    )
    if outcome.exit_code != 0:
        raise DisposablePostgresError(
            f"{step}: exit {outcome.exit_code}: {one_line(outcome.stderr or outcome.stdout)}"
        )
    return outcome


def postgres_bin_dir(source: BinarySource) -> Path:
    """Ask the shim for the bin dir of the bundled PostgreSQL (the last line of the shim output)."""
    # If a nonexistent path is passed to `uv run --script`, uv does not treat it as a script but builds a nearby project environment and runs python
    # (measured 2026-10-02 -- the sync of the .venv of doeff started). Name it and stop before asking.
    if not source.shim.is_file():
        raise DisposablePostgresError(
            f"asking where the binaries are (uv run --script): shim not found ({source.shim})"
        )
    asked = run_step(
        "asking where the binaries are (uv run --script)",
        (source.uv, "run", "--script", "--locked", "--quiet", str(source.shim)),
        SHIM_SECONDS,
    )
    lines = asked.stdout.strip().splitlines()
    if not lines:
        raise DisposablePostgresError("asking where the binaries are (uv run --script): empty answer")
    bin_dir = Path(lines[-1].strip())
    if not (bin_dir / "initdb").is_file():
        raise DisposablePostgresError(
            f"asking where the binaries are (uv run --script): no initdb in {bin_dir}"
        )
    return bin_dir


def start_disposable_postgres(source: BinarySource) -> DisposablePostgres:
    """Start PostgreSQL 16 in a temporary dir under TMPDIR. If it fails midway, stop it and delete the dir, then raise."""
    bin_dir = postgres_bin_dir(source)
    server = DisposablePostgres(bin_dir=bin_dir, root=Path(tempfile.mkdtemp(prefix="doeff-pg-")))
    try:
        server.socket_dir.mkdir()
        run_step(
            "initdb",
            (
                str(server.tool("initdb")),
                "-D",
                str(server.data_dir),
                "-U",
                "postgres",
                "--auth=trust",
                "--encoding=UTF8",
                "--locale=C",
                "--no-sync",
                "--no-instructions",
                "-c",
                "listen_addresses=",
                "-c",
                f"unix_socket_directories={server.socket_dir}",
                "-c",
                "fsync=off",
            ),
            INITDB_SECONDS,
        )
        try:
            run_step(
                "pg_ctl start",
                (
                    str(server.tool("pg_ctl")),
                    "-D",
                    str(server.data_dir),
                    "-l",
                    str(server.log_file),
                    "-w",
                    "-t",
                    str(PG_CTL_SECONDS),
                    "start",
                ),
                2 * PG_CTL_SECONDS,
            )
        except DisposablePostgresError as failed:
            # The reason for a start failure exists only in the PostgreSQL log.
            log = (
                server.log_file.read_text(encoding="utf-8", errors="replace")
                if server.log_file.exists()
                else ""
            )
            raise DisposablePostgresError(f"{failed} / log: {one_line(log)}") from failed
    except BaseException:
        stop_disposable_postgres(server)
        raise
    return server


def create_database(server: DisposablePostgres, database: str) -> str:
    """Create a database on the server and return its DSN."""
    run_step(
        f"createdb {database}",
        (str(server.tool("createdb")), "-h", str(server.socket_dir), "-U", "postgres", database),
        CLIENT_SECONDS,
    )
    return server.dsn(database)


def stop_disposable_postgres(server: DisposablePostgres) -> None:
    """Stop the server (immediate -- the data of the tests is discarded so do not wait for a checkpoint) and delete the temporary dir with it. Safe to call any number of times."""
    if (server.data_dir / "postmaster.pid").exists():
        subprocess.run(
            (
                str(server.tool("pg_ctl")),
                "-D",
                str(server.data_dir),
                "-m",
                "immediate",
                "-w",
                "-t",
                str(PG_CTL_SECONDS),
                "stop",
            ),
            capture_output=True,
            text=True,
            timeout=2 * PG_CTL_SECONDS,
            check=False,
        )
    shutil.rmtree(server.root, ignore_errors=True)
    if server.root.exists():
        raise DisposablePostgresError(f"cannot delete the temporary dir: {server.root}")


# A watcher that does the same cleanup when the pytest process vanishes without running cleanup (watchdog os._exit, SIGKILL).
# Arguments = pid to watch, pg_ctl, data dir, temporary dir. If the temporary dir is gone (normal cleanup finished), it ends doing nothing.
_GUARD_SOURCE = """
import os, shutil, subprocess, sys, time
pid, pg_ctl, data, root = int(sys.argv[1]), sys.argv[2], sys.argv[3], sys.argv[4]
def alive():
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
while alive() and os.path.isdir(root):
    time.sleep(1)
if os.path.isdir(root):
    subprocess.run([pg_ctl, "-D", data, "-m", "immediate", "-w", "stop"], capture_output=True, check=False)
    shutil.rmtree(root, ignore_errors=True)
"""


def start_guard(server: DisposablePostgres) -> None:
    """Start a watcher child process that stops the server and deletes the dir when the pytest process vanishes (detached from the SIGINT group)."""
    subprocess.Popen(
        (
            sys.executable,
            "-c",
            _GUARD_SOURCE,
            str(os.getpid()),
            str(server.tool("pg_ctl")),
            str(server.data_dir),
            str(server.root),
        ),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def read_given_dsns(variables: tuple[str, ...]) -> dict[str, str]:
    """Read, once, the env vars whose DSNs were passed from outside, with the process environment effect ReadEnvironment and the real handler subprocess-handler (#3012)."""
    entries = run(scheduled(with_handlers([subprocess_handler], ReadEnvironment(names=variables))))
    return {entry.name: entry.value for entry in entries if entry.value}


def provide_postgres(
    bindings: tuple[DsnBinding, ...],
    given: Mapping[str, str],
    add_cleanup: Callable[[Callable[[], None]], None],
    source: BinarySource = DEFAULT_SOURCE,
) -> PostgresProvision:
    """If some env vars are missing from given (DSNs passed from outside), start a disposable PostgreSQL and put the DSNs in the result (the job of stopping it is handed to add_cleanup)."""
    missing = tuple(binding for binding in bindings if not given.get(binding.variable))
    passed = tuple((binding.variable, given[binding.variable]) for binding in bindings if given.get(binding.variable))
    if not missing:
        return PostgresFromEnvironment(dsns=passed)
    try:
        server = start_disposable_postgres(source)
    except DisposablePostgresError as failed:
        return PostgresUnavailable(reason=one_line(str(failed)))
    add_cleanup(lambda: stop_disposable_postgres(server))
    try:
        dsns = tuple((binding, create_database(server, binding.database)) for binding in missing)
    except DisposablePostgresError as failed:
        stop_disposable_postgres(server)
        return PostgresUnavailable(reason=one_line(str(failed)))
    started = tuple((binding.variable, dsn) for binding, dsn in dsns)
    return PostgresStarted(server=server, bound=missing, dsns=passed + started)


def provision_dsn(provision: PostgresProvision | None, variable: str) -> str | None:
    """The DSN for env var name variable held by the preparation result (None if absent)."""
    match provision:
        case PostgresFromEnvironment(dsns=dsns) | PostgresStarted(dsns=dsns):
            return dict(dsns).get(variable)
        case _:
            return None


def postgres_skip_reason(variable: str, provision: PostgresProvision | None) -> str:
    """If a DSN exists, "" (run). Otherwise the skip reason message (naming why preparation failed)."""
    if provision_dsn(provision, variable):
        return ""
    match provision:
        case PostgresUnavailable(reason=reason):
            return f"{UNAVAILABLE_PREFIX}: {reason}"
        case None:
            return f"env {variable} is missing and no disposable PostgreSQL was started either (pytest_configure of conftest did not call provide_session_postgres)"
        case PostgresFromEnvironment() | PostgresStarted() | PostgresLeftToWorkers():
            return f"env {variable} is missing (the disposable PostgreSQL preparation is {type(provision).__name__} and did not set this env var)"


@dataclass
class _SessionPostgres:
    """The preparation result of this process (started only once per process -- the second conftest reads the same result)."""

    provision: PostgresProvision | None = None


_SESSION = _SessionPostgres()


def is_xdist_controller(config: pytest.Config) -> bool:
    """Whether this is the process on the side that spawns xdist workers (the controller does not import the tests)."""
    return not hasattr(config, "workerinput") and bool(
        config.getoption("numprocesses", default=None)
    )


def provide_session_postgres(config: pytest.Config) -> PostgresProvision:
    """Entry point called from pytest_configure of conftest: prepare once in this process and remember the result."""
    if _SESSION.provision is not None:
        return _SESSION.provision
    if is_xdist_controller(config):
        provision: PostgresProvision = PostgresLeftToWorkers()
    else:
        given = read_given_dsns(tuple(binding.variable for binding in SESSION_BINDINGS))
        provision = provide_postgres(SESSION_BINDINGS, given, config.add_cleanup)
        if isinstance(provision, PostgresStarted):
            start_guard(provision.server)
    _SESSION.provision = provision
    return provision


def session_provision() -> PostgresProvision | None:
    """The preparation result of this process (None if not called yet)."""
    return _SESSION.provision


def session_dsn(variable: str) -> str | None:
    """The DSN the test modules use for connecting (for env var name variable; None if absent) -- from this process's preparation result, not from the env."""
    return provision_dsn(_SESSION.provision, variable)


def session_postgres_skip_reason(variable: str) -> str:
    """Used by test modules as the skip reason: "" if a DSN exists, otherwise the reason preparation failed."""
    return postgres_skip_reason(variable, _SESSION.provision)
