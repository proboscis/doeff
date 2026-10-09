"""待ちの子(worker/entry/warm_child・#3646)を本物の process で確かめる失敗ケース。

worker の代わりに検が待ちの子を起こし(stdin を pipe で持つ = worker と同じ持ち方)、unix socket へ頼みを送る。頼みと答えの 1 行は
doeff_core_effects.os_warm_process の約束の形(WarmRequestWire・WarmForkedWire・WarmRefusedWire)。分かれた子が走らせる入口は
tests/fixtures/warm_job(前もって読む module として名指す)。範囲は Linux と macOS の worker(fork・子孫の引き取りは Linux だけで、macOS は
引き取らない形 — shim.py の註)。macOS の機体で直接 動く worker も warm child で job を走らせるため。
検は眠らずに出来事で待つ: 待ちの子の準備完了は stderr の 1 行・分かれた子 A の終わりはその機体の終わると読める fd(Linux = pidfd・
macOS = kqueue — doeff_core_effects.process_exit の exit-fd-of)・job の進みは検が開いた関所の socket。

守る事と、その失敗ケース:
- 分かれる前に thread が 1 本・VM が 0 個(準備完了の印)
- 分かれた子は読み込み済みの入口を同じ process の中で走らせる(exec しない)・終了コードは exit の file に出る
- 分かれた 2 本は、待ちの子の socket も、もう 1 本の log も継がない
- 待ちの子が落ちても、走っている job は走り切る
- 約束の形でない頼み(知らない欄を含む)は、合わない欄の場所だけを名乗って断る
- 待ちの子は env の値を持たない(頼みの env は分かれた子にだけ在り、待ちの子の環境と log と断りの文に出ない)
- 前もって読めない module は、名を挙げて起動を断る
- worker が消えたら(stdin の EOF)待ちの子は終わる
- 分かれた子の log(task の log)は、shim の道と同じ部品で 1 行ごとに壁の時計の刻の頭を持つ(#3714)
- 待ちの子は読み込んだ heap を受け付けの前に GC で掃いて凍らせ、分かれた子はその凍った heap を受け継ぐ(#3765)
"""

import json
import os
import re
import select
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from doeff import run
from doeff_core_effects.process_exit import exit_fd_of

MODULE_TAGS = {"context": "doeff-cluster-test", "role": "program"}

LINUX = sys.platform.startswith("linux")
DARWIN = sys.platform == "darwin"
pytestmark = pytest.mark.skipif(not (LINUX or DARWIN), reason="待ちの子は fork と終わると読める fd を使う — 範囲は Linux と macOS の worker")

# 待ちの子の木 = この package の dir(tests.fixtures.warm_job として読める)。
PACKAGE = Path(__file__).resolve().parent.parent
JOB = "tests.fixtures.warm_job"
WARM = "doeff_cluster.worker.entry.warm_child"
# 待ちの子の準備(module の読み込み)を待つ上限・job の進みと終わりを待つ上限(秒)。
READY_LIMIT_SECONDS = 60.0
END_LIMIT_SECONDS = 20.0
SECRET_NAME = "WARM_CHILD_TEST_SECRET"
SECRET_VALUE = "s3cr3t-value-that-must-not-leak"
# 刻の頭の形(ISO 8601 の日時・ms・時差・空白 1 つ — tests/test_line_stamp.py と同じ形を検が自分で綴る)。
STAMP = re.compile(rb"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}[+-]\d{2}:\d{2} ")


@dataclass(frozen=True)
class Warm:
    """検が起こした待ちの子 1 つ: process(stderr は pipe)・root の dir・socket・準備完了の印・job の file の置き場。"""

    process: subprocess.Popen[bytes]
    root: Path
    socket: Path
    ready: Path
    place: Path


@dataclass(frozen=True)
class Forked:
    """待ちの子の答え: 分かれた子 A の pid と起動の刻。"""

    pid: int
    start_ticks: int


@dataclass(frozen=True)
class Refused:
    """待ちの子の答え: 断りの理由。"""

    detail: str


@pytest.fixture
def place() -> Iterator[Path]:
    """socket の path が AF_UNIX の上限(107 byte)に収まる、短い一時の dir を作って、終わりに消すため。"""
    directory = Path(tempfile.mkdtemp(prefix="warm-", dir="/tmp"))
    yield directory
    shutil.rmtree(directory, ignore_errors=True)


def search_path() -> str:
    """待ちの子の探し道(この package の dir + 検の process の探し道)— 環境変数は読まずに、検が今 読めている所を渡すため。"""
    return os.pathsep.join([str(PACKAGE), *(entry for entry in sys.path if entry)])


def started(place: Path, preload: tuple[str, ...] = (JOB,)) -> Warm:
    """待ちの子を worker と同じ持ち方(stdin を pipe・自分の session)で起こすため。env は探し道だけ(資格も値も渡さない)。
    作業木に bytecode を書かない(-B — 根の conftest の固定と、worker が shim を起こす形と同じ)。"""
    root = place / "root"
    root.mkdir(exist_ok=True)
    argv = [sys.executable, "-B", "-m", WARM, "--root", str(root), "--socket", str(place / "s"), "--ready", str(place / "ready.json")]
    argv += [word for name in preload for word in ("--preload", name)]
    process = subprocess.Popen(
        argv,
        stdin=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=PACKAGE,
        env={"PYTHONPATH": search_path()},
        start_new_session=True,
    )
    return Warm(process, root, place / "s", place / "ready.json", place)


def stderr_until(warm: Warm, needle: str, limit: float) -> str:
    """待ちの子の stderr を、needle の行が出るか終わるまで読むため(読めた全部を返す)。"""
    assert warm.process.stderr is not None
    descriptor = warm.process.stderr.fileno()
    deadline = time.monotonic() + limit
    seen = b""
    while needle.encode() not in seen:
        left = deadline - time.monotonic()
        if left <= 0:
            raise AssertionError(f"待ちの子の stderr に {needle} が {limit} 秒の内に出ない: {seen.decode(errors='replace')}")
        readable, _, _ = select.select([descriptor], [], [], left)
        if not readable:
            continue
        chunk = os.read(descriptor, 65536)
        if not chunk:
            return seen.decode(errors="replace")
        seen += chunk
    return seen.decode(errors="replace")


def readied(warm: Warm) -> dict[str, object]:
    """準備完了の 1 行を待って、印を読むため(待ちの子が先に終わったら、その stderr を添えて AssertionError)。"""
    seen = stderr_until(warm, "準備完了", READY_LIMIT_SECONDS)
    if not warm.ready.exists():
        raise AssertionError(f"待ちの子が準備の前に終わった: {seen}")
    facts: dict[str, object] = json.loads(warm.ready.read_text())
    return facts


def request_line(name: str, place: Path, args: list[str], env: dict[str, str]) -> dict[str, object]:
    """入口 tests.fixtures.warm_job を走らせる頼み(約束の形 WarmRequestWire)を作るため。log と exit の file は place の下の name の名。"""
    return {
        "entry": JOB,
        "args": args,
        "cwd": str(PACKAGE),
        "env": [{"name": key, "value": value} for key, value in sorted(env.items())],
        "logPath": str(place / f"{name}.log"),
        "exitPath": str(place / f"{name}.exit"),
        "graceSeconds": 1.0,
    }


def asked(warm: Warm, payload: dict[str, object]) -> Forked | Refused:
    """頼みの JSON 1 行を送り、答えの JSON 1 行を受けて、2 つの形のどちらかにするため。"""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(10.0)
        client.connect(str(warm.socket))
        client.sendall((json.dumps(payload) + "\n").encode("utf-8"))
        data = b""
        while b"\n" not in data:
            chunk = client.recv(65536)
            if not chunk:
                break
            data += chunk
    answer = json.loads(data.split(b"\n", 1)[0])
    match answer:
        case {"pid": int(pid), "startTicks": int(ticks)}:
            return Forked(pid, ticks)
        case {"detail": str(detail)}:
            return Refused(detail)
        case _:
            raise AssertionError(f"約束の形でない答え: {answer}")


def forked_job(warm: Warm, name: str, args: list[str], env: dict[str, str] | None = None) -> Forked:
    """頼みを送り、受けた答え(Forked)を返すため(断られたら AssertionError)。"""
    answer = asked(warm, request_line(name, warm.place, args, env if env is not None else {}))
    if not isinstance(answer, Forked):
        raise AssertionError(f"頼みが断られた: {answer}")
    return answer


def ended(child: Forked) -> None:
    """分かれた子 A の終わりを、その機体の終わると読める fd で待つため(待ちの子の子なので waitpid は使えない — 終わっていれば、
    すぐ戻る)。"""
    descriptor = run(exit_fd_of(child.pid))
    if descriptor is None:
        return
    try:
        readable, _, _ = select.select([descriptor], [], [], END_LIMIT_SECONDS)
        if not readable:
            raise AssertionError(f"分かれた子 {child.pid} が {END_LIMIT_SECONDS} 秒の内に終わらない")
    finally:
        os.close(descriptor)


def environ_of(pid: int) -> bytes:
    """pid の process の環境変数の並び(Linux = /proc/<pid>/environ・macOS = ps -E が命令の後ろに出す環境 — 同じ利用者の process)。"""
    if LINUX:
        with open(f"/proc/{pid}/environ", "rb") as handle:
            return handle.read()
    return subprocess.run(["ps", "-E", "-ww", "-p", str(pid), "-o", "command="], check=True, capture_output=True).stdout


def gate_opened(place: Path, name: str) -> socket.socket:
    """job が繋ぐ関所の socket を開くため(job は見え方を書いた後に繋いで「書いた」を送り、検が切るまで待つ)。"""
    gate = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    gate.bind(str(place / name))
    gate.listen(1)
    gate.settimeout(END_LIMIT_SECONDS)
    return gate


def reached(gate: socket.socket) -> socket.socket:
    """job が関所に着いて「書いた」を送るまで待ち、その接続を返すため(検が閉じると job が進む)。"""
    connection, _ = gate.accept()
    connection.settimeout(END_LIMIT_SECONDS)
    assert connection.recv(64).startswith(b"inspected")
    return connection


def stopped(warm: Warm) -> None:
    """待ちの子を止めて回収するため(検の後始末)。"""
    if warm.process.poll() is None:
        os.killpg(warm.process.pid, signal.SIGKILL)
    warm.process.wait(timeout=10)


def test_the_ready_mark_shows_one_thread_and_no_vm_before_any_fork(place: Path) -> None:
    """分かれる前の待ちの子は thread 1 本・doeff の VM 0 個・入口と前もって読む module を読み込み済み。"""
    warm = started(place)
    try:
        facts = readied(warm)
        assert facts["threads"] == 1, facts
        assert facts["vmLive"] == [0, 0, 0], facts
        assert facts["preloaded"] == ["doeff_cluster.worker.entry.job_entry", JOB], facts
        assert facts["root"] == os.path.realpath(warm.root), facts
    finally:
        stopped(warm)


def test_the_warm_child_freezes_its_loaded_heap_so_a_forked_job_starts_with_it_frozen(place: Path) -> None:
    """待ちの子は module を読み込んだ後・受け付けの前に GC で 1 回掃いて heap を凍らせ(準備完了の印の gcFrozen が 0 より大きい)、分かれた子は
    その凍った object を受け継ぐ(#3765 — 凍らせないと、待ちの子の GC の数えを受け継いだ子が最初の行の前に GC を走らせ、読み込んだ約 100 MB の
    heap の頁を写して 271 ms 遅れた)。前の形では両方とも 0 で赤。"""
    warm = started(place)
    try:
        frozen = readied(warm)["gcFrozen"]
        assert isinstance(frozen, int) and frozen > 0, frozen
        out = place / "facts.json"
        ended(forked_job(warm, "a", ["inspect", str(out)]))
        seen = json.loads(out.read_text())
        # 子が fork の後に解放した object は永続の世代から抜けるので、数は待ちの子より少し減る(実測 84,273 → 84,268)— 大半が凍ったままか。
        assert seen["gcFrozen"] > frozen // 2, (seen["gcFrozen"], frozen)
    finally:
        stopped(warm)


def test_a_forked_job_runs_the_loaded_entry_in_process_and_writes_its_exit_code(place: Path) -> None:
    """分かれた子は exec せずに読み込み済みの入口を走らせ(命令行は待ちの子のまま)、終了コードを exit の file に書く。"""
    warm = started(place)
    try:
        readied(warm)
        out = place / "facts.json"
        ended(forked_job(warm, "a", ["inspect", str(out)]))
        assert (place / "a.exit").read_text() == "0"
        seen = json.loads(out.read_text())
        assert WARM in seen["cmdline"], seen["cmdline"]
        assert seen["argv"] == [JOB, "inspect", str(out)], seen["argv"]
        ended(forked_job(warm, "b", ["exit", "7"]))
        assert (place / "b.exit").read_text() == "7"
        assert "warm_job: exit 7" in (place / "b.log").read_text()
    finally:
        stopped(warm)


def test_two_forked_jobs_inherit_neither_the_socket_nor_the_other_log(place: Path) -> None:
    """並んで走る 2 本の分かれた子は、待ちの子の socket(聞く側も頼みの接続も)と、もう 1 本の log を継がない。"""
    warm = started(place)
    try:
        readied(warm)
        first, second = place / "first.json", place / "second.json"
        gate = gate_opened(place, "g")
        held = forked_job(warm, "first", ["inspect", str(first), str(place / "g")])
        connection = reached(gate)
        ended(forked_job(warm, "second", ["inspect", str(second)]))
        connection.close()
        gate.close()
        ended(held)
        for out, other in ((first, "second.log"), (second, "first.log")):
            fds = json.loads(out.read_text())["fds"]
            assert not [target for target in fds if target.startswith("socket:")], fds
            assert not [target for target in fds if target.endswith(other)], fds
    finally:
        stopped(warm)


def test_a_running_job_outlives_the_warm_child(place: Path) -> None:
    """待ちの子が KILL で落ちても、走っている job は走り切り、終了コードを書く。"""
    warm = started(place)
    try:
        readied(warm)
        marker = place / "marker"
        gate = gate_opened(place, "g")
        held = forked_job(warm, "long", ["inspect", str(place / "facts.json"), str(place / "g"), str(marker)])
        connection = reached(gate)
        os.kill(warm.process.pid, signal.SIGKILL)
        warm.process.wait(timeout=10)
        connection.close()
        gate.close()
        ended(held)
        assert marker.read_text() == "done"
        assert (place / "long.exit").read_text() == "0"
    finally:
        stopped(warm)


def test_a_request_out_of_the_contract_is_refused_by_the_field_names(place: Path) -> None:
    """約束の形でない頼み(知らない欄・欄の欠け)は、合わない欄の場所だけを名乗って断り、子を分けない。"""
    warm = started(place)
    try:
        readied(warm)
        stray = request_line("stray", place, ["exit", "0"], {}) | {"root": str(place / "other-root")}
        answer = asked(warm, stray)
        assert isinstance(answer, Refused), answer
        assert "root" in answer.detail, answer
        lacking = {key: value for key, value in request_line("lacking", place, ["exit", "0"], {}).items() if key != "exitPath"}
        answer = asked(warm, lacking)
        assert isinstance(answer, Refused), answer
        assert "exitPath" in answer.detail, answer
        assert not (place / "stray.exit").exists() and not (place / "stray.log").exists()
    finally:
        stopped(warm)


def test_the_warm_child_keeps_no_request_env(place: Path) -> None:
    """頼みの env は分かれた子にだけ在る — 待ちの子の環境・log・断りの文に、その値は出ない。"""
    warm = started(place)
    try:
        readied(warm)
        out = place / "facts.json"
        ended(forked_job(warm, "env", ["inspect", str(out)], env={SECRET_NAME: SECRET_VALUE}))
        assert json.loads(out.read_text())["env"].get(SECRET_NAME) == SECRET_VALUE
        broken = request_line("broken", place, ["exit", "0"], {SECRET_NAME: SECRET_VALUE}) | {"graceSeconds": SECRET_VALUE}
        answer = asked(warm, broken)
        assert isinstance(answer, Refused), answer
        assert SECRET_VALUE not in answer.detail
        assert SECRET_VALUE.encode() not in environ_of(warm.process.pid)
        assert warm.process.stdin is not None
        warm.process.stdin.close()
        assert warm.process.wait(timeout=10) == 0
        assert warm.process.stderr is not None
        assert SECRET_VALUE not in warm.process.stderr.read().decode(errors="replace")
    finally:
        stopped(warm)


def test_an_unreadable_preload_refuses_the_start_by_name(place: Path) -> None:
    """前もって読めない module は、名を挙げて起動を断る(準備完了の印を書かない)。"""
    warm = started(place, preload=("tests.fixtures.no_such_module_for_warm_child",))
    try:
        assert warm.process.wait(timeout=READY_LIMIT_SECONDS) == 3
        assert not warm.ready.exists()
        assert warm.process.stderr is not None
        assert "tests.fixtures.no_such_module_for_warm_child" in warm.process.stderr.read().decode(errors="replace")
    finally:
        stopped(warm)


def test_the_warm_child_ends_when_the_worker_goes(place: Path) -> None:
    """worker が消えたら(stdin の EOF)待ちの子は 0 で終わる。"""
    warm = started(place)
    try:
        readied(warm)
        assert warm.process.stdin is not None
        warm.process.stdin.close()
        assert warm.process.wait(timeout=10) == 0
    finally:
        stopped(warm)


def test_a_forked_job_log_has_a_wall_clock_stamp_on_each_line(place: Path) -> None:
    """分かれた子の log(task の log — worker の job-start の via=warm)も、1 行ごとに刻の頭を持つ(中身はそのまま)。"""
    warm = started(place)
    try:
        readied(warm)
        ended(forked_job(warm, "stamped", ["exit", "5"]))
        assert (place / "stamped.exit").read_text() == "5"
        lines = (place / "stamped.log").read_bytes().splitlines()
        assert lines and all(STAMP.match(line) for line in lines), lines
        assert [STAMP.sub(b"", line) for line in lines] == [b"warm_job: exit 5"], lines
    finally:
        stopped(warm)


def test_the_warm_child_reads_the_process_through_the_machine_readers() -> None:
    """失敗ケース: 待ちの子は process の様子(起動の刻・thread の本数)を /proc から自分で読まず、
    機体ごとの読み(doeff_core_effects.os_warm_process の proc-stat-of・own-thread-count)を使う — /proc の無い macOS で起動の前に落ちない。"""
    source = (PACKAGE / "src" / "doeff_cluster" / "worker" / "entry" / "warm_child.py").read_text()
    assert "/proc" not in source
    assert "proc_stat_of" in source and "own_thread_count" in source
