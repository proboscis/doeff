"""uv の入れた Python の置き場の片付け(deploy/python_installs.py と、boot.sh の doeff_python_installs_gc)の検。

訳: uv は patch の版ごとに dir を作り、版が上がっても前の版を消さない(2026-11-08 の node-h の state/python に 9 本・約 1 GB)。venv は
patch の版の dir を直に指す事が多く、置き場の外の venv を数え切れないので、古い版の dir は消すだけでなく、同じ名で同じ minor の
新しい版への link に置き換える。確かめる事: 使われている版(組の新しい版・走っている process の版)を残し、使われていない古い版を
消す。古い版を指していた venv は新しい版で動き続ける。uv が入れている途中(置き場の錠を持つ間)は何も変えない。

確かめ方: 偽の置き場(版の名の dir と bin/python3 の印・uv の minor の link)を並べ、venv の代わりに pyvenv.cfg の home と bin/python の
link を作り、道具の gc を呼ぶ。走っている process の版は、版の dir の下を作業 dir にする process で作る。
"""

import importlib.util
import json
import os
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

DEPLOY = Path(__file__).resolve().parents[1] / "deploy"
BOOT = DEPLOY / "boot.sh"
TOOL = DEPLOY / "python_installs.py"

_spec = importlib.util.spec_from_file_location("python_installs", TOOL)
assert _spec is not None
assert _spec.loader is not None
python_installs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(python_installs)

PLATFORM = "linux-x86_64-gnu"


def _install(place: Path, version: str, variant: str = "") -> Path:
    """偽の版の dir 1 つ(bin/python3 に版の名を書く — 置き換えの後に link の先の版を読む)。"""
    path = place / f"cpython-{version}{variant}-{PLATFORM}"
    (path / "bin").mkdir(parents=True)
    (path / "bin" / "python3").write_text(path.name, encoding="utf-8")
    return path


def _minor_link(place: Path, minor: str, target: Path, variant: str = "") -> Path:
    """uv の minor の link(cpython-3.14-… → 置き場の中の patch の版の dir — uv と同じ絶対の path)。"""
    link = place / f"cpython-{minor}{variant}-{PLATFORM}"
    link.symlink_to(target)
    return link


def _venv(tmp: Path, name: str, install: Path) -> Path:
    """patch の版の dir を直に指す venv の代わり(pyvenv.cfg の home と bin/python の link)。"""
    venv = tmp / "venvs" / name
    (venv / "bin").mkdir(parents=True)
    (venv / "pyvenv.cfg").write_text(f"home = {install / 'bin'}\n", encoding="utf-8")
    (venv / "bin" / "python").symlink_to(install / "bin" / "python3")
    return venv


def _place(tmp: Path) -> Path:
    place = tmp / "python"
    place.mkdir()
    (place / ".lock").write_text("", encoding="utf-8")
    return place


def _no_proc(tmp: Path) -> Path:
    """process の 1 つも無い /proc の代わり。"""
    proc = tmp / "proc"
    proc.mkdir(exist_ok=True)
    return proc


def _entries(place: Path) -> dict[str, str]:
    """置き場の版の項目: 名 → dir なら "dir"・link なら link の先の字。"""
    return {
        p.name: (os.readlink(p) if p.is_symlink() else "dir")
        for p in sorted(place.iterdir())
        if p.name.startswith("cpython-")
    }


def test_used_versions_stay_and_unused_old_patches_become_links_to_the_newest(
    tmp_path: Path,
) -> None:
    place = _place(tmp_path)
    old_t = _install(place, "3.14.3", "+freethreaded")
    new_t = _install(place, "3.14.7", "+freethreaded")
    old = _install(place, "3.14.6")
    new = _install(place, "3.14.7")
    only = _install(place, "3.12.14")
    rc = _install(place, "3.15.0rc2")
    final = _install(place, "3.15.0")
    _minor_link(place, "3.14", new)
    _minor_link(place, "3.14", new_t, "+freethreaded")
    venv = _venv(tmp_path, "old-free-threaded", old_t)

    result = python_installs.gc(place, _no_proc(tmp_path), remove=True)

    assert result["retired"] == sorted([old_t.name, old.name, rc.name]), result
    assert result["kept"] == sorted([new_t.name, new.name, only.name, final.name]), result
    entries = _entries(place)
    # 使われている版(組の新しい版・組に 1 つだけの版)は dir のまま、uv の minor の link は触らない。
    assert [name for name, what in entries.items() if what == "dir"] == sorted(
        [new_t.name, new.name, only.name, final.name]
    )
    assert entries[f"cpython-3.14-{PLATFORM}"] == str(new)
    assert entries[f"cpython-3.14+freethreaded-{PLATFORM}"] == str(new_t)
    # 古い版は同じ名で、同じ組(変種と platform も同じ)の新しい版への相対の link。pre の版は正式の版に追い越される。
    assert entries[old_t.name] == new_t.name
    assert entries[old.name] == new.name
    assert entries[rc.name] == final.name
    # 古い版を指していた venv は、同じ minor の新しい版で動き続ける。
    assert (venv / "bin" / "python").read_text(encoding="utf-8") == new_t.name
    home = (venv / "pyvenv.cfg").read_text(encoding="utf-8").removeprefix("home = ").strip()
    assert Path(home, "python3").read_text(encoding="utf-8") == new_t.name
    # 脇に置いた dir は消えている。
    assert list((place / ".temp").iterdir()) == []


def test_a_later_patch_moves_the_links_left_by_an_earlier_run(tmp_path: Path) -> None:
    place = _place(tmp_path)
    first = _install(place, "3.14.3")
    second = _install(place, "3.14.7")
    python_installs.gc(place, _no_proc(tmp_path), remove=True)
    third = _install(place, "3.14.9")

    result = python_installs.gc(place, _no_proc(tmp_path), remove=True)

    assert result["relinked"] == [first.name], result
    assert result["retired"] == [second.name], result
    assert _entries(place) == {first.name: third.name, second.name: third.name, third.name: "dir"}
    again = python_installs.gc(place, _no_proc(tmp_path), remove=True)
    assert (
        again["linked"] == [first.name, second.name]
        and again["retired"] == []
        and again["relinked"] == []
    ), again


def test_an_old_version_a_running_process_uses_is_kept(tmp_path: Path) -> None:
    """走っている Python が後から読む標準の library を別の patch の版に替えない — 版の dir の下を使う process が在れば残す。"""
    place = _place(tmp_path)
    old = _install(place, "3.14.3")
    _install(place, "3.14.7")
    child = subprocess.Popen(["sleep", "60"], cwd=old / "bin")
    try:
        result = python_installs.gc(place, Path("/proc"), remove=True)
    finally:
        child.kill()
        child.wait()
    assert result["used"] == [old.name], result
    assert _entries(place)[old.name] == "dir"


def test_nothing_changes_when_proc_cannot_be_read(tmp_path: Path) -> None:
    place = _place(tmp_path)
    old = _install(place, "3.14.3")
    _install(place, "3.14.7")
    result = python_installs.gc(place, tmp_path / "no-proc", remove=True)
    assert result["proc"] is False and result["retired"] == [], result
    assert _entries(place)[old.name] == "dir"


@contextmanager
def _installing(place: Path) -> Iterator[None]:
    """uv が入れている途中の代わり: 置き場の錠(<置き場>/.lock の flock)を持ち続ける別の process。"""
    code = "import fcntl,os,sys,time\nfd=os.open(sys.argv[1],os.O_WRONLY)\nfcntl.flock(fd,fcntl.LOCK_EX)\nprint('held',flush=True)\ntime.sleep(60)\n"
    child = subprocess.Popen(
        [sys.executable, "-c", code, str(place / ".lock")], stdout=subprocess.PIPE, text=True
    )
    try:
        assert child.stdout is not None
        assert child.stdout.readline().strip() == "held"
        yield
    finally:
        child.kill()
        child.wait()


def test_it_waits_while_uv_holds_the_install_lock(tmp_path: Path) -> None:
    place = _place(tmp_path)
    old = _install(place, "3.14.3")
    _install(place, "3.14.7")
    with _installing(place):
        waiting = subprocess.run(
            [
                "timeout",
                "2",
                sys.executable,
                "-I",
                "-B",
                str(TOOL),
                "gc",
                "--dir",
                str(place),
                "--proc",
                str(_no_proc(tmp_path)),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert waiting.returncode == 124, waiting
        assert _entries(place)[old.name] == "dir"
    done = python_installs.gc(place, _no_proc(tmp_path), remove=True)
    assert done["retired"] == [old.name], done


def test_count_changes_nothing_and_leftovers_of_a_stopped_run_are_swept(tmp_path: Path) -> None:
    place = _place(tmp_path)
    old = _install(place, "3.14.3")
    _install(place, "3.14.7")
    leftover = place / ".temp" / f"{python_installs.RETIRED_PREFIX}cpython-3.14.1-{PLATFORM}.1"
    (leftover / "bin").mkdir(parents=True)
    counted = python_installs.gc(place, _no_proc(tmp_path), remove=False)
    assert (
        counted["retired"] == [old.name]
        and _entries(place)[old.name] == "dir"
        and leftover.exists()
    ), counted
    python_installs.gc(place, _no_proc(tmp_path), remove=True)
    assert not leftover.exists()


def test_a_missing_place_is_left_alone(tmp_path: Path) -> None:
    result = python_installs.gc(tmp_path / "absent", _no_proc(tmp_path), remove=True)
    assert result["installs"] == 0 and not (tmp_path / "absent").exists(), result


def test_the_cli_answers_one_json_line(tmp_path: Path) -> None:
    place = _place(tmp_path)
    old = _install(place, "3.14.3")
    _install(place, "3.14.7")
    done = subprocess.run(
        [
            sys.executable,
            "-I",
            "-B",
            str(TOOL),
            "gc",
            "--dir",
            str(place),
            "--proc",
            str(_no_proc(tmp_path)),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    answer = json.loads(done.stdout)
    assert answer["verb"] == "gc" and answer["retired"] == [old.name], answer


def test_boot_sh_starts_the_gc_on_the_python_install_dir() -> None:
    """boot.sh の準備が、uv の Python の置き場(UV_PYTHON_INSTALL_DIR)へこの道具を起こす。"""
    text = BOOT.read_text(encoding="utf-8")
    assert (
        "doeff_python_installs_gc\n" in text.split("doeff_prepare() {", 1)[1].split("\n}\n", 1)[0]
    )
    body = text.split("doeff_python_installs_gc() {", 1)[1].split("\n}\n", 1)[0]
    assert "python_installs.py" in body
    assert '--dir "$UV_PYTHON_INSTALL_DIR"' in body
