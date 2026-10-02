"""検の session が自分で立てる使い捨ての PostgreSQL(agora-redesign #2830)。

doeff の実 PostgreSQL の検(doeff-core-effects の test_sql_effects.hy と doeff-records の test_pg_*.hy ほか)は DSN を env
(``DOEFF_SQL_TEST_POSTGRES_DSN``・``DOEFF_RECORDS_TEST_PG_DSN``)から module の読み込みの時に読む。日次の機体にも手元にも外の
PostgreSQL は無いので、両方の package の tests の conftest.py が pytest_configure(検の module の import より前)でここの
``provide_session_postgres`` を呼ぶ:

- 2 つの env が既に在れば、それを使う(手元で自分の PostgreSQL を指す人のため)。何も立てない。
- 無い env があれば、PostgreSQL 16 を TMPDIR の下の一時の dir(``tempfile.mkdtemp``)に 1 つ立て、env ごとに別の database を作り
  (createdb)、env をその DSN にする。pytest の終わり(``config.add_cleanup`` — 例外の終わりでも)に止めて dir ごと消す。
  watchdog の ``os._exit`` のように後始末が走らない終わり方に備え、pytest の process が消えたら同じ後始末をする見張りの子 process も置く。
- binary を用意できない(uv が無い・download できない・initdb が失敗する)時は env を置かず、理由の 1 行を覚える。検はそれを
  ``session_postgres_skip_reason`` で読み、「使い捨ての PostgreSQL を用意できない: <理由>」と名指して skip する(赤にしない)。

binary は pgserver の wheel に同梱の物で、隣の shim.py を uv の分けた Python 3.12 の環境(``uv run --script --locked``・版と hash は
shim.py.lock)で走らせて bin の dir を問う — doeff の uv.lock に依存を足さない。data・unix socket・log は一時の dir の中に置き、
TCP では待ち受けない(``listen_addresses = ''`` — 並行の検や他のセッションと port を取り合わない)。pytest の 1 process につき 1 回
立てる。xdist では controller は立てず、worker がそれぞれ別の dir に立てる。手本 = agora-controllers の
controllers/foundation/tests/disposable_postgres.hy。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
from collections.abc import Callable, MutableMapping
from dataclasses import dataclass
from pathlib import Path

import pytest

SUPPORT_DIR = Path(__file__).resolve().parent
# binary の置き場を教える外皮(uv の分けた Python 3.12 の環境で走る)。
SHIM = SUPPORT_DIR / "shim.py"
# 外皮を待つ上限(秒)— 初回は uv が Python 3.12 と pgserver の wheel を download する。
SHIM_SECONDS = 600
# initdb を待つ上限(秒)。
INITDB_SECONDS = 120
# pg_ctl が起動・停止を待つ上限(秒)。
PG_CTL_SECONDS = 60
# createdb / psql を待つ上限(秒)。
CLIENT_SECONDS = 60
# 理由の 1 行の長さの上限(skip の文に載せる)。
REASON_CHARACTERS = 300

SQL_EFFECTS_VARIABLE = "DOEFF_SQL_TEST_POSTGRES_DSN"
RECORDS_VARIABLE = "DOEFF_RECORDS_TEST_PG_DSN"
UNAVAILABLE_PREFIX = "使い捨ての PostgreSQL を用意できない"


@dataclass(frozen=True)
class DsnBinding:
    """env の名と、使い捨ての PostgreSQL の上でその env に割り当てる database の名。"""

    variable: str
    database: str


# 2 つの package の検が読む env(同じ PostgreSQL の別の database)。
SESSION_BINDINGS = (
    DsnBinding(variable=SQL_EFFECTS_VARIABLE, database="doeff_sql_effects"),
    DsnBinding(variable=RECORDS_VARIABLE, database="doeff_records"),
)


@dataclass(frozen=True)
class BinarySource:
    """binary の置き場の問い方: uv の実行ファイルと外皮の path(検は壊した値を差し込んで用意できない形を作る)。"""

    uv: str
    shim: Path


DEFAULT_SOURCE = BinarySource(uv="uv", shim=SHIM)


@dataclass(frozen=True)
class DisposablePostgres:
    """立てた PostgreSQL: bin_dir = binary の dir・root = 一時の dir(data・socket・log の親)。"""

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
        """同梱の binary の path(initdb・pg_ctl・createdb・psql)。"""
        return self.bin_dir / name

    def dsn(self, database: str) -> str:
        """database への接続の URL(unix socket・利用者 postgres・trust)。"""
        host = urllib.parse.quote(str(self.socket_dir), safe="")
        return f"postgresql://postgres@/{database}?host={host}"


@dataclass(frozen=True)
class PostgresFromEnvironment:
    """必要な env が全部既に在った — 何も立てていない。"""


@dataclass(frozen=True)
class PostgresStarted:
    """使い捨ての PostgreSQL を立て、無かった env をその database の DSN にした。"""

    server: DisposablePostgres
    bound: tuple[DsnBinding, ...]


@dataclass(frozen=True)
class PostgresUnavailable:
    """使い捨ての PostgreSQL を用意できなかった(reason = 理由の 1 行)。env は置いていない。"""

    reason: str


@dataclass(frozen=True)
class PostgresLeftToWorkers:
    """xdist の controller — 立てるのは各 worker(worker ごとに別の dir)。"""


PostgresProvision = (
    PostgresFromEnvironment | PostgresStarted | PostgresUnavailable | PostgresLeftToWorkers
)


class DisposablePostgresError(Exception):
    """使い捨ての PostgreSQL を用意できなかった(文 = どの手順か・出力の最後の行)。"""


@dataclass(frozen=True)
class StepOutcome:
    """用意の子 process の答え(終了の番号と出力)。"""

    exit_code: int
    stdout: str
    stderr: str


def one_line(text: str) -> str:
    """出力の最後の空でない行を、skip の文に載る長さに切る。"""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return (lines[-1] if lines else "(出力なし)")[:REASON_CHARACTERS]


def run_step(step: str, argv: tuple[str, ...], timeout: float) -> StepOutcome:
    """用意の子 process を 1 つ走らせ、失敗なら手順の名と出力の最後の行を持つ DisposablePostgresError を上げる。"""
    try:
        completed = subprocess.run(
            argv, capture_output=True, text=True, timeout=timeout, check=False
        )
    except FileNotFoundError as missing:
        raise DisposablePostgresError(
            f"{step}: 実行ファイルが無い ({missing.filename})"
        ) from missing
    except subprocess.TimeoutExpired as expired:
        raise DisposablePostgresError(f"{step}: {timeout:g} 秒で終わらない") from expired
    except OSError as failed:
        raise DisposablePostgresError(f"{step}: 起動できない ({failed})") from failed
    outcome = StepOutcome(
        exit_code=completed.returncode, stdout=completed.stdout, stderr=completed.stderr
    )
    if outcome.exit_code != 0:
        raise DisposablePostgresError(
            f"{step}: exit {outcome.exit_code}: {one_line(outcome.stderr or outcome.stdout)}"
        )
    return outcome


def postgres_bin_dir(source: BinarySource) -> Path:
    """同梱の PostgreSQL の bin の dir を外皮に問う(外皮の最後の行)。"""
    # 無い path を `uv run --script` に渡すと、uv はそれを script と見ずに近くの project の環境を組んで python で走らせる
    # (2026-10-02 実測 — doeff の .venv の同期が始まった)。問う前に名指して止める。
    if not source.shim.is_file():
        raise DisposablePostgresError(
            f"binary の置き場を問う(uv run --script): 外皮が無い ({source.shim})"
        )
    asked = run_step(
        "binary の置き場を問う(uv run --script)",
        (source.uv, "run", "--script", "--locked", "--quiet", str(source.shim)),
        SHIM_SECONDS,
    )
    lines = asked.stdout.strip().splitlines()
    if not lines:
        raise DisposablePostgresError("binary の置き場を問う(uv run --script): 答えが空")
    bin_dir = Path(lines[-1].strip())
    if not (bin_dir / "initdb").is_file():
        raise DisposablePostgresError(
            f"binary の置き場を問う(uv run --script): {bin_dir} に initdb が無い"
        )
    return bin_dir


def start_disposable_postgres(source: BinarySource) -> DisposablePostgres:
    """PostgreSQL 16 を TMPDIR の下の一時の dir に立てる。途中で失敗したら、止めて dir を消してから上げる。"""
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
            # 起動の失敗の理由は PostgreSQL の log にだけ在る。
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
    """server に database を作り、その DSN を返す。"""
    run_step(
        f"createdb {database}",
        (str(server.tool("createdb")), "-h", str(server.socket_dir), "-U", "postgres", database),
        CLIENT_SECONDS,
    )
    return server.dsn(database)


def stop_disposable_postgres(server: DisposablePostgres) -> None:
    """server を止めて(immediate — 検の data は捨てるので checkpoint を待たない)、一時の dir ごと消す。何度呼んでもよい。"""
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
        raise DisposablePostgresError(f"一時の dir を消せない: {server.root}")


# pytest の process が後始末を走らせずに消えた時(watchdog の os._exit・SIGKILL)に、同じ後始末をする見張り。
# 引数 = 見張る pid・pg_ctl・data の dir・一時の dir。一時の dir が消えたら(通常の後始末が済んだら)何もせず終わる。
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
    """pytest の process が消えたら server を止めて dir を消す見張りの子 process を立てる(SIGINT の組から外す)。"""
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


def provide_postgres(
    bindings: tuple[DsnBinding, ...],
    environ: MutableMapping[str, str],
    add_cleanup: Callable[[Callable[[], None]], None],
    source: BinarySource = DEFAULT_SOURCE,
) -> PostgresProvision:
    """無い env があれば使い捨ての PostgreSQL を立てて env をその DSN にする(止める仕事は add_cleanup に渡す)。"""
    missing = tuple(binding for binding in bindings if not environ.get(binding.variable))
    if not missing:
        return PostgresFromEnvironment()
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
    for binding, dsn in dsns:
        environ[binding.variable] = dsn
    return PostgresStarted(server=server, bound=missing)


def postgres_skip_reason(
    variable: str, provision: PostgresProvision | None, environ: MutableMapping[str, str]
) -> str:
    """env が在れば ""(走らせる)。無ければ skip の理由の文(用意できなかった理由を名指す)。"""
    if environ.get(variable):
        return ""
    match provision:
        case PostgresUnavailable(reason=reason):
            return f"{UNAVAILABLE_PREFIX}: {reason}"
        case None:
            return f"env {variable} が無く、使い捨ての PostgreSQL も立てていない(conftest の pytest_configure が provide_session_postgres を呼んでいない)"
        case PostgresFromEnvironment() | PostgresStarted() | PostgresLeftToWorkers():
            return f"env {variable} が無い(使い捨ての PostgreSQL の用意は {type(provision).__name__} で、この env を置いていない)"


@dataclass
class _SessionPostgres:
    """この process の用意の結果(1 process に 1 回だけ立てる — 2 つ目の conftest は同じ結果を読む)。"""

    provision: PostgresProvision | None = None


_SESSION = _SessionPostgres()


def is_xdist_controller(config: pytest.Config) -> bool:
    """xdist が worker を起こす側の process か(controller は検を import しない)。"""
    return not hasattr(config, "workerinput") and bool(
        config.getoption("numprocesses", default=None)
    )


def provide_session_postgres(config: pytest.Config) -> PostgresProvision:
    """conftest の pytest_configure から呼ぶ入口: この process で 1 回だけ用意し、結果を覚える。"""
    if _SESSION.provision is not None:
        return _SESSION.provision
    if is_xdist_controller(config):
        provision: PostgresProvision = PostgresLeftToWorkers()
    else:
        provision = provide_postgres(SESSION_BINDINGS, os.environ, config.add_cleanup)  # noqa: DOEFF004 - 検の module が import の時に読む DSN の env を置く(#2830)
        if isinstance(provision, PostgresStarted):
            start_guard(provision.server)
    _SESSION.provision = provision
    return provision


def session_provision() -> PostgresProvision | None:
    """この process の用意の結果(まだ呼ばれていなければ None)。"""
    return _SESSION.provision


def session_postgres_skip_reason(variable: str) -> str:
    """検の module が skip の理由に使う: env が在れば ""・無ければ用意できなかった理由。"""
    return postgres_skip_reason(variable, _SESSION.provision, os.environ)  # noqa: DOEFF004 - 検の module が読む DSN の env の有無(#2830)
