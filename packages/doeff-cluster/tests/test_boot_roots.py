"""古い版の起動の root の片付け(deploy/boot_roots.py と、boot.sh の doeff_root_hold)の検。

訳: boot.sh は宣言された commit ごとに $WORK_DIR/boot/roots/<sha> を作り、前は消す者が居なかった(2026-11-08 の atlas で worker 33 本・
coordinator 95 本・計 約 22 GB)。片付けは、今の版・最後に使った新しい物・錠を持たれた物(drain 中の世代・同じ hostPath の別の Pod)・
この名前空間の process が使う物を残し、ほかを消す。数だけで消すと drain 中の世代の root を消しうるので、錠と process の判じを確かめる。

確かめ方: 偽の root(名が sha の dir と完成の印)を並べ、刻を os.utime で置き、道具の gc を呼ぶ。別の Pod の使い手は、root の印に共有の
錠を持つ別の process で作る(引数に root の絶対 path を書かない — process の判じと分けて錠だけを確かめる)。boot.sh の doeff_root_hold は
本物の関数の文を抜き出して sh で走らせ、exec した後の process が錠を持ち続ける事を確かめる。
"""


import importlib.util
import json
import os
import re
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

DEPLOY = Path(__file__).resolve().parents[1] / "deploy"
BOOT = DEPLOY / "boot.sh"
TOOL = DEPLOY / "boot_roots.py"

_spec = importlib.util.spec_from_file_location("boot_roots", TOOL)
assert _spec is not None and _spec.loader is not None
boot_roots = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(boot_roots)

DAY = 86400.0


def _sha(n: int) -> str:
    return f"{n:040x}"


def _root(boot: Path, n: int, *, used_at: float | None = None, ready_at: float | None = None) -> Path:
    """偽の root 1 本。used_at = 印 .doeff-boot-use の刻(None = 印の無い前の版の root)・ready_at = 完成の印の刻。"""
    root = boot / "roots" / _sha(n)
    (root / "packages").mkdir(parents=True)
    (root / "packages" / "f.py").write_text("x = 1\n", encoding="utf-8")
    ready = root / boot_roots.READY_MARK
    ready.write_text("", encoding="utf-8")
    stamp = ready_at if ready_at is not None else time.time() - 30 * DAY
    os.utime(ready, (stamp, stamp))
    os.utime(root, (stamp, stamp))
    if used_at is not None:
        mark = root / boot_roots.USE_MARK
        mark.write_text("", encoding="utf-8")
        os.utime(mark, (used_at, used_at))
    return root


def _left(boot: Path) -> list[str]:
    return sorted(p.name for p in (boot / "roots").iterdir())


def _no_proc(tmp: Path) -> Path:
    """process の 1 つも無い /proc の代わり(錠の判じだけを確かめる)。"""
    proc = tmp / "proc"
    proc.mkdir(exist_ok=True)
    return proc


@contextmanager
def _holder(boot: Path, root: Path) -> Iterator[subprocess.Popen[str]]:
    """別の Pod の使い手: root の印に共有の錠を持ち続ける process(作業 dir は boot・引数は相対の path)。"""
    rel = os.path.relpath(root / boot_roots.USE_MARK, boot)
    code = "import fcntl,os,sys,time\nfd=os.open(sys.argv[1],os.O_RDONLY)\nfcntl.flock(fd,fcntl.LOCK_SH)\nprint('held',flush=True)\ntime.sleep(60)\n"
    child = subprocess.Popen([sys.executable, "-c", code, rel], cwd=boot, stdout=subprocess.PIPE, text=True)
    try:
        assert child.stdout is not None and child.stdout.readline().strip() == "held"
        yield child
    finally:
        child.kill()
        child.wait()


def test_keeps_the_current_and_the_newest_and_removes_the_rest(tmp_path: Path) -> None:
    boot = tmp_path / "boot"
    now = time.time()
    for n in range(6):
        _root(boot, n, used_at=now - (6 - n) * DAY)
    result = boot_roots.gc(boot, _sha(5), 3, _no_proc(tmp_path), remove=True)
    assert _left(boot) == [_sha(3), _sha(4), _sha(5)], result
    assert result["doomed"] == [_sha(0), _sha(1), _sha(2)]


def test_the_current_root_counts_in_the_keep_even_when_its_mark_is_old(tmp_path: Path) -> None:
    """戻した版(今の版の印が古い)でも残す数を越えない — 今の版 + 新しい物 keep − 1 本。"""
    boot = tmp_path / "boot"
    now = time.time()
    for n in range(5):
        _root(boot, n, used_at=now - (5 - n) * DAY)
    boot_roots.gc(boot, _sha(0), 3, _no_proc(tmp_path), remove=True)
    assert _left(boot) == [_sha(0), _sha(3), _sha(4)]


def test_a_root_whose_mark_another_pod_holds_is_kept(tmp_path: Path) -> None:
    """drain 中の世代・同じ hostPath の旧い Pod は、古い root の印に共有の錠を持つ — 数の外でも消さない。"""
    boot = tmp_path / "boot"
    now = time.time()
    roots = [_root(boot, n, used_at=now - (6 - n) * DAY) for n in range(6)]
    with _holder(boot, roots[0]):
        result = boot_roots.gc(boot, _sha(5), 3, _no_proc(tmp_path), remove=True)
    assert result["held"] == [_sha(0)], result
    assert _left(boot) == [_sha(0), _sha(3), _sha(4), _sha(5)]


def test_a_root_a_running_process_works_in_is_kept(tmp_path: Path) -> None:
    """錠を継がない子(subprocess の close_fds)と、錠を持つ前の版の script の process — 作業 dir が root の下なら消さない。"""
    boot = tmp_path / "boot"
    now = time.time()
    roots = [_root(boot, n, used_at=now - (6 - n) * DAY) for n in range(6)]
    child = subprocess.Popen(["sleep", "60"], cwd=roots[1] / "packages")
    try:
        result = boot_roots.gc(boot, _sha(5), 3, Path("/proc"), remove=True)
    finally:
        child.kill()
        child.wait()
    assert result["used"] == [_sha(1)], result
    assert _left(boot) == [_sha(1), _sha(3), _sha(4), _sha(5)]


def test_nothing_is_removed_when_proc_cannot_be_read(tmp_path: Path) -> None:
    boot = tmp_path / "boot"
    now = time.time()
    for n in range(5):
        _root(boot, n, used_at=now - (5 - n) * DAY)
    result = boot_roots.gc(boot, _sha(4), 3, tmp_path / "no-proc", remove=True)
    assert result["proc"] is False and result["doomed"] == []
    assert len(_left(boot)) == 5


def test_roots_without_the_use_mark_are_ordered_by_the_ready_mark(tmp_path: Path) -> None:
    """印を置く前の版の script が作った root — 完成の印の刻で新しい物を決める。印を作らない(作ると最後に使った刻が今になる)。"""
    boot = tmp_path / "boot"
    now = time.time()
    for n in range(4):
        _root(boot, n, ready_at=now - (10 - n) * DAY)
    _root(boot, 9, used_at=now)
    boot_roots.gc(boot, _sha(9), 3, _no_proc(tmp_path), remove=True)
    assert _left(boot) == [_sha(2), _sha(3), _sha(9)]
    assert not (boot / "roots" / _sha(2) / boot_roots.USE_MARK).exists()


def test_count_removes_nothing_and_leftovers_of_a_stopped_run_are_swept(tmp_path: Path) -> None:
    boot = tmp_path / "boot"
    now = time.time()
    for n in range(4):
        _root(boot, n, used_at=now - (4 - n) * DAY)
    (boot / "roots" / f"{boot_roots.DOOMED_PREFIX}{_sha(7)}.1" / "x").mkdir(parents=True)
    (boot / "roots" / "not-a-root").mkdir()
    counted = boot_roots.gc(boot, _sha(3), 2, _no_proc(tmp_path), remove=False)
    assert counted["doomed"] == [_sha(0), _sha(1)] and len(_left(boot)) == 6
    boot_roots.gc(boot, _sha(3), 2, _no_proc(tmp_path), remove=True)
    assert _left(boot) == [_sha(2), _sha(3), "not-a-root"]


def test_the_count_stays_within_the_keep_over_many_version_changes(tmp_path: Path) -> None:
    """受入の形: 版を 5 回替える(替えるたびに新しい root を作って印を付け、片付ける)— root の数は残す数を越えない。"""
    boot = tmp_path / "boot"
    now = time.time()
    for n in range(8):
        _root(boot, n, used_at=now - (8 - n) * DAY)
    for step in range(8, 13):
        _root(boot, step, used_at=now)
        boot_roots.gc(boot, _sha(step), boot_roots.DEFAULT_KEEP, _no_proc(tmp_path), remove=True)
        assert len(_left(boot)) == boot_roots.DEFAULT_KEEP
    assert _left(boot)[-1] == _sha(12)


def _hold_function() -> str:
    text = BOOT.read_text(encoding="utf-8")
    found = re.search(r"^doeff_root_hold\(\) \{\n.*?^\}\n", text, re.MULTILINE | re.DOTALL)
    assert found, "boot.sh に関数 doeff_root_hold が無い"
    return found.group(0)


def test_boot_sh_holds_the_root_through_exec(tmp_path: Path) -> None:
    """boot.sh の doeff_root_hold は印を付け直して共有の錠を取り、exec した役の process(ここでは sleep)が錠を持ち続ける。"""
    boot = tmp_path / "boot"
    old = _root(boot, 0, used_at=time.time() - 9 * DAY)
    for n in range(1, 4):
        _root(boot, n, used_at=time.time() - (4 - n) * DAY)
    probe = f"set -eu\n{_hold_function()}\nroot={old}\ndoeff_root_hold\necho held\nexec sleep 60\n"
    child = subprocess.Popen(["/bin/sh", "-c", probe], cwd=tmp_path, stdout=subprocess.PIPE, text=True)
    try:
        assert child.stdout is not None and child.stdout.readline().strip() == "held"
        assert (old / boot_roots.USE_MARK).stat().st_mtime > time.time() - 60
        # 印を付け直したので、0 は最後に使った新しい物に入る — 残す数 1(今の版だけ)で、錠だけが 0 を守る事を見る。
        result = boot_roots.gc(boot, _sha(3), 1, _no_proc(tmp_path), remove=True)
    finally:
        child.kill()
        child.wait()
    assert result["held"] == [_sha(0)], result
    assert _left(boot) == [_sha(0), _sha(3)]


def test_the_cli_answers_one_json_line(tmp_path: Path) -> None:
    boot = tmp_path / "boot"
    now = time.time()
    for n in range(4):
        _root(boot, n, used_at=now - (4 - n) * DAY)
    done = subprocess.run(
        [sys.executable, "-I", str(TOOL), "gc", "--boot", str(boot), "--current", _sha(3), "--keep", "2"],
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    answer = json.loads(done.stdout)
    assert answer["verb"] == "gc" and answer["doomed"] == [_sha(0), _sha(1)], done.stderr
    assert "消した 2" in done.stderr
    assert _left(boot) == [_sha(2), _sha(3)]
