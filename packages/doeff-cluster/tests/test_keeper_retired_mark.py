"""keeper が前の世代を退かせる時に置く印と、世代の見張りが読む印の path が揃う事の検(deploy/boot.sh の keeper_retire と
DOEFF_WORKER_RETIRED_FILE)。

実例(2026-11-10): keeper が版を入れ替えた後も前の世代が同じ container に残り、その見張りが新しい世代と同じ jobs の層の memory.max を
書き続けた。直した見張りは印(worker/protocol/eviction_line の retired-file)を読んだ後は書かない(tests/test_eviction_retired_generation.hy)。
印を置くのは keeper(container の起動の版の script)、path を世代に渡すのは世代の版の script なので、2 つの path が食い違うと見張りは
印を読めず、前の世代が書き続ける。

確かめ方: boot.sh から keeper_retire と keeper の小さな関数を抜き出して sh で走らせ(drain の hy は偽物、止める世代は sleep の process)、
世代の起動の文(DOEFF_WORKER_GENERATION の時の export)を同じ世代の boot の file で評価して、置かれた印の path と比べる。
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from doeff_cluster._foundation.process_environ import child_environ, named_environ

BOOT = Path(__file__).resolve().parents[1] / "deploy" / "boot.sh"
FUNCTIONS = ("keeper_log", "keeper_short", "keeper_alive", "keeper_retire")


def _functions() -> str:
    """boot.sh の keeper の関数の定義(本物の文のまま)。"""
    text = BOOT.read_text(encoding="utf-8")
    found = {name: re.search(rf"^{name}\(\) \{{\n.*?^\}}\n", text, re.MULTILINE | re.DOTALL) for name in FUNCTIONS}
    missing = [name for name, match in found.items() if match is None]
    assert not missing, f"boot.sh に関数 {missing} が無い"
    return "\n".join(match.group(0) for match in found.values() if match is not None)


def _retired_export() -> str:
    """世代の起動の文のうち、退きの印の path を世代へ渡す if の塊(本物の文のまま)。"""
    text = BOOT.read_text(encoding="utf-8")
    found = re.search(
        r'^if \[ -n "\$\{DOEFF_WORKER_GENERATION:-\}" \]; then\n  export DOEFF_WORKER_RETIRED_FILE=.*?^fi\n',
        text,
        re.MULTILINE | re.DOTALL,
    )
    assert found, "boot.sh に DOEFF_WORKER_RETIRED_FILE を渡す塊が無い"
    return found.group(0)


def test_the_keeper_puts_the_retired_mark_where_the_generation_reads_it(tmp_path: Path) -> None:
    gen = tmp_path / "keeper" / "gens" / "pod1.1000"
    gen.mkdir(parents=True)
    (gen / "commit").write_text("a" * 40 + "\n", encoding="utf-8")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_hy = fake_bin / "hy"
    fake_hy.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake_hy.chmod(0o755)
    elder = subprocess.Popen(["sleep", "30"])
    try:
        (gen / "pid").write_text(f"{elder.pid}\n", encoding="utf-8")
        script = (
            _functions()
            + f'\nkeeper_retire "{gen}"\n'
            + f'DOEFF_WORKER_GENERATION=pod1.1000 DOEFF_WORKER_BOOT_FILE="{gen}/boot"\n'
            + _retired_export()
            + 'printf "%s\\n" "$DOEFF_WORKER_RETIRED_FILE"\n'
        )
        env = {
            **child_environ((), ()),
            "PATH": f"{fake_bin}:{named_environ(('PATH',))['PATH']}",
            "WORK_DIR": str(tmp_path / "work"),
            "COORDINATOR_URL": "http://coordinator.test",
            "WORKER_NAME": "w",
            "KEEPER_STOP_SECONDS": "5",
        }
        done = subprocess.run(["sh", "-c", script], env=env, capture_output=True, text=True, timeout=30, check=True)
        handed = done.stdout.strip()
        # keeper_retire は drain の前に印を置き(同期)、世代へ渡す path はその印そのもの。
        assert (gen / "draining").is_file(), done.stderr
        assert handed == str(gen / "draining"), (handed, done.stderr)
        elder.wait(timeout=20)
    finally:
        if elder.poll() is None:
            elder.kill()
            elder.wait()


def test_a_generation_outside_the_keeper_gets_no_retired_mark(tmp_path: Path) -> None:
    # keeper の世代でない worker(DOEFF_WORKER_GENERATION が無い)は印を読まない — 退きの無い worker が書き続ける今までの形。
    script = f'DOEFF_WORKER_BOOT_FILE="{tmp_path}/boot"\n' + _retired_export() + 'printf "[%s]\\n" "${DOEFF_WORKER_RETIRED_FILE:-}"\n'
    env = child_environ(("DOEFF_WORKER_GENERATION", "DOEFF_WORKER_RETIRED_FILE"), ())
    done = subprocess.run(["sh", "-c", script], env=env, capture_output=True, text=True, timeout=10, check=True)
    assert done.stdout.strip() == "[]", done.stdout
