"""doeff-hy-check(doeff_hy.static_check の main)が、展開と pyright の前に機体の検の枠を 1 つ取る失敗ケース(agora-redesign card
ki-26ebd3f636e0・親 ki-aabe38bf85ef)。

直す前は、会話が直に起動した doeff-hy-check(`uv run doeff-hy-check …`・`python -m doeff_hy.static_check …`)は枠を取らずに走った —
枠に入っていたのは agora-controllers の手の入口(scripts/hy_type_check.hy の gate)の子だけだった(card ki-931f88d7815b の残る道)。

縁の検は機体の正本(~/dotfiles/agent/tests/machine_check_slots.py)を検体の home の下へ写し、隣に宣言(K = 1・置き場は検体の dir)を
置く — 機体の本物の枠を埋めず、本物の保持者に待たされない。枠を持つ別の process は正本の命令の包み run。doeff-hy-check は本物の
入口(`python -m doeff_hy.static_check`)を子の process で撃ち、偽にするのは pyright だけ(撃たれた事を file に記す)。正本の無い宿
(doeff だけを clone した機体)では skip。
"""

import io
import json
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest
from doeff_hy.check_slot import CANON_UNDER_HOME, MARK, SlotHeld, take_slot
from doeff_hy.env_places import cache_home

#: 機体の正本(検の process の home の下 — 検体の home へ写す元)。
CANON = Path.home() / CANON_UNDER_HOME


@dataclass(frozen=True)
class Scene:
    """検体の世界 — 正本の写しを置いた home・その写し・検める根・偽の pyright と、それが撃たれた事を記す file。"""

    home: Path
    canon: Path
    root: Path
    pyright: Path
    started: Path

    def check_command(self) -> list[str]:
        """会話が直に撃つのと同じ入口で、根の 1 file を検める命令。"""
        return [
            sys.executable,
            "-m",
            "doeff_hy.static_check",
            "--root",
            str(self.root),
            "--no-cache",
            "--pyright",
            str(self.pyright),
            str(self.root / "probe.hy"),
        ]

    def pyright_runs(self) -> list[str]:
        return (
            self.started.read_text(encoding="utf-8").splitlines() if self.started.exists() else []
        )


def _scene(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, wait_s: float) -> Scene:
    """機体の正本を検体の home へ写し、隣に宣言(K = 1・置き場 tmp_path/slots・待ちの上限 wait_s 秒)を置いて、子の process の
    HOME を検体の home にする。Hy の bytecode などの cache の根は今の機体の物のまま(冷えた cache で起動に分を払わない)。"""
    if not CANON.is_file():
        pytest.skip(f"機体の正本 {CANON} が無い宿(dotfiles を置かない機体)")
    home = tmp_path / "home"
    canon = home / CANON_UNDER_HOME
    canon.parent.mkdir(parents=True)
    shutil.copy2(CANON, canon)
    canon.with_name("machine_check_slots.json").write_text(
        json.dumps({"slots": 1, "dir": str(tmp_path / "slots"), "waitLimitSeconds": wait_s}),
        encoding="utf-8",
    )
    root = tmp_path / "repo"
    root.mkdir()
    (root / "probe.hy").write_text("(setv answer 1)\n", encoding="utf-8")
    started = tmp_path / "pyright-started.log"
    pyright = tmp_path / "pyright"
    pyright.write_text(
        f"#!/bin/sh\necho started >> {shlex.quote(str(started))}\necho '{{\"generalDiagnostics\": []}}'\n",
        encoding="utf-8",
    )
    pyright.chmod(0o755)
    monkeypatch.setenv("XDG_CACHE_HOME", str(cache_home()))
    monkeypatch.setenv("HOME", str(home))
    return Scene(home, canon, root, pyright, started)


def _blocker(scene: Scene) -> "subprocess.Popen[str]":
    """写しの枠を正本の命令の包み run で持つ別の process(stdin を閉じるまで持つ)。"""
    blocker = subprocess.Popen(
        [
            sys.executable,
            str(scene.canon),
            "run",
            "--label",
            "blocker-holds",
            "--",
            sys.executable,
            "-c",
            "import sys; print('held', flush=True); sys.stdin.read()",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    assert blocker.stdout is not None
    assert blocker.stdout.readline() == "held\n", "別の process が写しの枠を取れなかった"
    return blocker


def _end(blocker: "subprocess.Popen[str]") -> None:
    if blocker.stdin is not None and not blocker.stdin.closed:
        blocker.stdin.close()
    if blocker.poll() is None:
        blocker.kill()
    blocker.wait(timeout=15)


def test_a_full_slot_holds_a_directly_started_doeff_hy_check_until_it_frees(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 受入 c07865153: 枠(写しの宣言 K = 1)が埋まっている間、会話が直に起動した doeff-hy-check は pyright を撃たずに待って保持者
    # (pid と名乗り)を名乗り、空いた拍に枠を取って検める。直す前は枠を見ずに検め、名乗りの行を 1 つも出さずに終わった。
    scene = _scene(tmp_path, monkeypatch, 60.0)
    blocker = _blocker(scene)
    try:
        check = subprocess.Popen(
            scene.check_command(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        assert check.stderr is not None
        waiting = next((line for line in check.stderr if MARK in line), "")
        runs_while_full = scene.pyright_runs()
        assert blocker.stdin is not None
        blocker.stdin.close()
        _, rest = check.communicate(timeout=120)
        assert f"pid {blocker.pid} blocker-holds" in waiting, (waiting, rest)
        assert "待つのは doeff-hy-check repo" in waiting, waiting
        assert runs_while_full == [], "枠が埋まっている間に pyright を撃った"
        assert check.returncode == 0, rest
        assert "を取った(待ち" in rest, rest
        assert scene.pyright_runs() == ["started"]
        assert blocker.wait(timeout=15) == 0, "枠を持っていた process が終わらない"
    finally:
        _end(blocker)


def test_doeff_hy_check_runs_nothing_when_the_slot_wait_expires(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 待ちが正本の宣言の上限を越えたら、展開も pyright も起こさずに「走れなかった」(exit 2)で終わる — 呼び手(agora-controllers の
    # hy_type_check.hy・hy_type_trend.hy)は 0・1 以外を「測れなかった」と読む。合格(0)にも赤(1)にもしない。
    scene = _scene(tmp_path, monkeypatch, 1.0)
    blocker = _blocker(scene)
    try:
        done = subprocess.run(
            scene.check_command(), capture_output=True, text=True, timeout=120, check=False
        )
        assert done.returncode == 2, done.stderr
        assert f"doeff-hy-check: 走れなかった: {MARK}" in done.stderr, done.stderr
        assert scene.pyright_runs() == [], "待ちの上限を越えたのに pyright を撃った"
    finally:
        _end(blocker)


def test_doeff_hy_check_inside_a_slot_holder_takes_no_second_slot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 枠を持つ process の子孫(gate が起こす doeff-hy-check・pytest の中の呼び)は 2 つ目を取らない — K = 1 で祖先が唯一の枠を持っても
    # 待たずに検める(待てば上限 5 秒で exit 2 になる)。
    scene = _scene(tmp_path, monkeypatch, 5.0)
    done = subprocess.run(
        [
            sys.executable,
            str(scene.canon),
            "run",
            "--label",
            "outer-holds",
            "--",
            *scene.check_command(),
        ],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    assert MARK not in done.stderr, done.stderr
    assert scene.pyright_runs() == ["started"]


def test_a_host_without_the_canon_takes_no_slot_and_says_nothing(tmp_path: Path) -> None:
    # 正本の無い宿(doeff だけを clone した機体)は黙って素通し — 枠を取らず、名乗らない。
    out = io.StringIO()
    held = take_slot(tmp_path / "home", "doeff-hy-check repo", out)
    assert held == SlotHeld(None)
    assert out.getvalue() == ""
