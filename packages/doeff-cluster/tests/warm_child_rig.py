"""待ちの子(doeff_cluster.worker.entry.warm_child)を本物の process で起こす検の、共通の起こし方。

待ちの子は Hy の module を前もって読む(569 module)。-B(.pyc を作業木に書かない)で env が探し道だけだと、Hy の code の置き場に届かず、
起きるたびに全部を source から compile し直して 25〜33 秒かかり、準備を待つ期限と余裕が無くなる(card ki-2121cf77fe73)。
ここは env に温めた置き場(conftest の job_child_code_store が先に満たす)を渡し、準備を待つ期限を温めた置き場の目安の秒数にして、
冷たい compile が戻った時は期限でなく原因の名(置き場が使われていない)で落とす。待ちの子を起こす検は fixture start_warm_child を通す
(test_warm_child_rig.py が止める)。
"""

from __future__ import annotations

import os
import select
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

from doeff_hy_bytecode_guard._code_store import STORE_ENV, store_dir

WARM = "doeff_cluster.worker.entry.warm_child"
PACKAGE = Path(__file__).resolve().parent.parent
# 温めた置き場での準備の目安(実測 1.2〜1.6 秒・冷たい compile は 25 秒以上)— 負荷の揺れを見て 10 秒。
READY_SECONDS = 10.0
COLD_STORE_NAME = (
    f"待ちの子が {READY_SECONDS} 秒の内に準備できない — Hy の code の置き場が使われていない(冷たい compile)疑い: "
    f"{STORE_ENV} が温めた置き場を指しているか・job_child_code_store を通しているか"
)


def search_path() -> str:
    """待ちの子の探し道(この package の dir + 検の process の探し道)— 環境変数は読まずに、検が今 読めている所を渡すため。"""
    return os.pathsep.join([str(PACKAGE), *(entry for entry in sys.path if entry)])


def warm_child_env() -> dict[str, str]:
    """待ちの子の env: 探し道と、温めた Hy の code の置き場(検の process が引く置き場 — job_child_code_store が温めた所)。資格も値も渡さない。"""
    env = {"PYTHONPATH": search_path()}
    env[STORE_ENV] = store_dir() or "off"  # 使わない設定(off)は子へもそのまま渡す — 子だけ別の既定の置き場を引かない
    return env


def launch(root: Path, socket_path: Path, ready: Path, preload: tuple[str, ...]) -> subprocess.Popen[bytes]:
    """待ちの子を worker と同じ持ち方(stdin を pipe・自分の session)で起こすため。作業木に bytecode を書かない(-B)。"""
    root.mkdir(exist_ok=True)
    argv = [sys.executable, "-B", "-m", WARM, "--root", str(root), "--socket", str(socket_path), "--ready", str(ready)]
    argv += [word for name in preload for word in ("--preload", name)]
    return subprocess.Popen(
        argv, stdin=subprocess.PIPE, stderr=subprocess.PIPE, cwd=PACKAGE, env=warm_child_env(), start_new_session=True
    )


def wait_ready_file(process: subprocess.Popen[bytes], ready: Path) -> float:
    """準備完了の 1 行(stderr)を出来事で待ち、印の file を確かめて、かかった秒数を返すため。待ちの子の終わりと READY_SECONDS の期限で
    止まり、冷たい compile は期限の数字でなく置き場の名で落とす(眠って見に行かない)。"""
    assert process.stderr is not None
    descriptor = process.stderr.fileno()
    started = time.monotonic()
    seen = b""
    while "準備完了".encode() not in seen:
        left = READY_SECONDS - (time.monotonic() - started)
        assert left > 0, COLD_STORE_NAME
        readable, _, _ = select.select([descriptor], [], [], left)
        if not readable:
            continue
        chunk = os.read(descriptor, 65536)
        assert chunk, f"待ちの子が準備の前に終わった: {seen.decode(errors='replace')}"
        seen += chunk
    assert ready.exists(), "準備完了の行の後に印の file が無い"
    return time.monotonic() - started


StartWarmChild = Callable[[Path, Path, Path, tuple[str, ...]], "subprocess.Popen[bytes]"]
