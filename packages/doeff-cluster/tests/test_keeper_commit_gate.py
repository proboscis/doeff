"""keeper が新しい世代の committed の印まで旧い世代を退かせず、印の前に落ちた版を退ける事の検
(deploy/boot.sh の keeper_tick・keeper_swap・keeper_committed・keeper_start)。

出自(2026-11-14):
  keeper は新しい世代が ready を書いた時点で旧い世代を drain して止めた。ready は coordinator に名乗れて
  受け渡しの待ち(handover-pending)が解けた事しか言わず、その後の拍の action(env の掃除 SweepEnvs など)を
  回し切れるかは証さない。新しい版が掃除で落ちるたびに、keeper は ready を書いた世代で restarts=0 へ戻して
  同じ版を 30 秒ごとに 8 回起こし直し、そのたびに job が止まった。

確かめ方: boot.sh から keeper の関数を抜き出して sh で走らせ、世代の process は sleep / すぐ終わる script で
代役を立てる(drain の hy は偽物)。
  1. 新しい世代が ready を書いても committed を書く前は、旧い世代を drain しない(draining を置かない)。
     committed を書いた後に旧い世代を drain し、keeper の commit と pod_boot を進める。
  2. 新しい世代が committed を書く前に落ちたら、keeper はその版を退けた版として覚え($keeper_dir/rejected と
     log 1 行)、旧い世代を止めずに動かし続け、版の file が同じ版を名指す間は起こし直さない。別の版に
     替わったらその版を試す。
  3. 起こし直しの間(restarts)は committed を書いた世代でだけ 0(30 秒)へ戻す。ready だけ書いて落ちた世代では
     restarts が伸び続ける。
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

BOOT = Path(__file__).resolve().parents[1] / "deploy" / "boot.sh"
FUNCTIONS = (
    "boot_ms",
    "keeper_log",
    "keeper_short",
    "keeper_alive",
    "keeper_wanted",
    "keeper_mtime",
    "keeper_ready",
    "keeper_committed",
    "keeper_start",
    "keeper_retire",
    "keeper_layer",
    "keeper_room",
    "keeper_swap",
    "keeper_group_open",
    "keeper_group_close",
    "keeper_tick",
)
COMMIT_A = "a" * 40
COMMIT_B = "b" * 40
COMMIT_C = "c" * 40


def _functions() -> str:
    """boot.sh の keeper の関数の定義(本物の文のまま)。"""
    text = BOOT.read_text(encoding="utf-8")
    found = {
        name: re.search(rf"^{name}\(\) \{{\n.*?^\}}\n", text, re.MULTILINE | re.DOTALL)
        for name in FUNCTIONS
    }
    missing = [name for name, match in found.items() if match is None]
    assert not missing, f"boot.sh に関数 {missing} が無い"
    return "\n".join(match.group(0) for match in found.values() if match is not None)


def _fake_root(base: Path, sha: str, worker_body: str) -> Path:
    """keeper_swap が受け入れる準備済みの root(boot.sh に DOEFF_WORKER_GENERATION を含む)。"""
    root = base / "roots" / sha
    deploy = root / "packages" / "doeff-cluster" / "deploy"
    deploy.mkdir(parents=True)
    # keeper_swap は root の boot.sh に DOEFF_WORKER_GENERATION の語が在るかを見る。
    (deploy / "boot.sh").write_text(
        f"#!/bin/sh\n# DOEFF_WORKER_GENERATION\n{worker_body}\n",
        encoding="utf-8",
    )
    return root


def _fake_bin(tmp_path: Path) -> Path:
    """drain_main を受け取る偽の hy(すぐ 0 で終わる)。"""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    hy = bin_dir / "hy"
    hy.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    hy.chmod(0o755)
    return bin_dir


def _run_sh(
    script: str, env: dict[str, str], *, timeout: int = 30
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["/bin/sh", "-c", script],
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=True,
    )


def _base_env(tmp_path: Path, version_file: Path) -> dict[str, str]:
    bin_dir = _fake_bin(tmp_path)
    return {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "WORK_DIR": str(tmp_path / "work"),
        "COORDINATOR_URL": "http://coordinator.test",
        "WORKER_NAME": "w-test",
        "WORKER_VERSION_FILE": str(version_file),
        "KEEPER_STOP_SECONDS": "2",
        "POD_UID": "pod-test",
    }


def _prepared(keeper: Path, sha: str, root: Path) -> None:
    prep = keeper / "prepare" / sha
    prep.mkdir(parents=True, exist_ok=True)
    (prep / "root").write_text(f"{root}\n", encoding="utf-8")


def test_keeper_does_not_drain_elder_until_new_generation_writes_committed(tmp_path: Path) -> None:
    """新しい世代が ready を書いても committed を書く前は、keeper が旧い世代を drain せず、committed を書いた後に drain する。"""
    keeper = tmp_path / "keeper"
    (keeper / "gens").mkdir(parents=True)
    (keeper / "prepare").mkdir(parents=True)
    version_file = tmp_path / "version"
    version_file.write_text(f"{COMMIT_B}\n", encoding="utf-8")

    root_a = _fake_root(tmp_path, COMMIT_A, "sleep 30")
    # 候補 B は ready と boot を書くが、信号 file が置かれるまで committed を書かない。
    gate = tmp_path / "allow-commit"
    root_b = _fake_root(
        tmp_path,
        COMMIT_B,
        f'printf "boot-b\\n" >"$DOEFF_WORKER_BOOT_FILE"\n'
        f'printf "ready\\n" >"$DOEFF_WORKER_READY_FILE"\n'
        f'while [ ! -f "{gate}" ]; do sleep 0.05; done\n'
        f'printf "committed\\n" >"$DOEFF_WORKER_COMMITTED_FILE"\n'
        f"exec sleep 30\n",
    )
    _prepared(keeper, COMMIT_B, root_b)

    elder = subprocess.Popen(["sleep", "30"])
    try:
        elder_gen = keeper / "gens" / "pod-test.1000"
        elder_gen.mkdir(parents=True)
        (elder_gen / "pid").write_text(f"{elder.pid}\n", encoding="utf-8")
        (elder_gen / "commit").write_text(f"{COMMIT_A}\n", encoding="utf-8")
        (elder_gen / "boot").write_text("boot-a\n", encoding="utf-8")
        (elder_gen / "ready").write_text("ready\n", encoding="utf-8")
        (elder_gen / "committed").write_text("committed\n", encoding="utf-8")
        (keeper / "commit").write_text(f"{COMMIT_A}\n", encoding="utf-8")

        pod_ready = tmp_path / "pod-ready"
        pod_boot = tmp_path / "pod-boot"
        pod_boot.write_text("boot-a\n", encoding="utf-8")

        probe = f"""set -eu
{_functions()}
keeper_dir={keeper}
keeper_script={BOOT}
pod_ready={pod_ready}
pod_boot={pod_boot}
target={COMMIT_A}
target_root={root_a}
active={elder_gen}
active_started=$(date +%s)
candidate=""
candidate_commit=""
candidate_root=""
candidate_started=0
rejected=""
restarts=0
unreadable=""
prepare_waiting=""

# 1 歩目: 版 B の候補を起こす。
keeper_tick
# 候補 B が ready を書くのを待つ(committed はまだ書かない)。
tries=0
while [ ! -f "$candidate/ready" ] && [ "$tries" -lt 40 ]; do
  sleep 0.05
  tries=$((tries + 1))
done
[ -f "$candidate/ready" ]
[ ! -f "$candidate/committed" ]

# ready だけでは旧い世代を drain しない(複数 tick 回しても同じ)。
keeper_tick
keeper_tick
echo "BEFORE_DRAINING=$( [ -f "{elder_gen}/draining" ] && echo yes || echo no )"
echo "BEFORE_COMMIT=$(cat "{keeper}/commit")"
echo "BEFORE_TARGET=$target"
echo "BEFORE_POD_READY=$(cat "{pod_ready}" 2>/dev/null || true)"
echo "BEFORE_POD_BOOT=$(cat "{pod_boot}" 2>/dev/null || true)"

# committed を書かせ、次の tick で旧い世代が drain される事を確かめる。
: >"{gate}"
tries=0
while [ ! -f "$candidate/committed" ] && [ "$tries" -lt 40 ]; do
  sleep 0.05
  tries=$((tries + 1))
done
[ -f "$candidate/committed" ]

keeper_tick
echo "AFTER_DRAINING=$( [ -f "{elder_gen}/draining" ] && echo yes || echo no )"
echo "AFTER_COMMIT=$(cat "{keeper}/commit")"
echo "AFTER_TARGET=$target"
echo "AFTER_POD_BOOT=$(cat "{pod_boot}" 2>/dev/null || true)"

# 後片付け: 起こした候補の process を止める。
k_pid=$(cat "$active/pid" 2>/dev/null || true)
[ -z "$k_pid" ] || kill -TERM "$k_pid" 2>/dev/null || true
"""
        done = _run_sh(probe, _base_env(tmp_path, version_file))
        out = dict(line.split("=", 1) for line in done.stdout.strip().splitlines() if "=" in line)
        assert out["BEFORE_DRAINING"] == "no", (out, done.stderr)
        assert out["BEFORE_COMMIT"] == COMMIT_A, (out, done.stderr)
        assert out["BEFORE_TARGET"] == COMMIT_A, (out, done.stderr)
        assert out["BEFORE_POD_READY"] == "ready", (out, done.stderr)
        assert out["BEFORE_POD_BOOT"] == "boot-a", (out, done.stderr)

        assert out["AFTER_DRAINING"] == "yes", (out, done.stderr)
        assert out["AFTER_COMMIT"] == COMMIT_B, (out, done.stderr)
        assert out["AFTER_TARGET"] == COMMIT_B, (out, done.stderr)
        assert out["AFTER_POD_BOOT"] == "boot-b", (out, done.stderr)
        elder.wait(timeout=15)
    finally:
        if elder.poll() is None:
            elder.kill()
            elder.wait()


def test_keeper_rejects_version_that_dies_before_committed_and_tries_next_version(
    tmp_path: Path,
) -> None:
    """印 committed を書く前に落ちた新しい版を keeper が退け、旧い世代を止めず同じ版を起こし直さず、別の版で再試行する。"""
    keeper = tmp_path / "keeper"
    (keeper / "gens").mkdir(parents=True)
    (keeper / "prepare").mkdir(parents=True)
    version_file = tmp_path / "version"
    version_file.write_text(f"{COMMIT_B}\n", encoding="utf-8")

    root_a = _fake_root(tmp_path, COMMIT_A, "sleep 30")
    # 版 B は ready を書いた直後に committed を書かずに落ちる(拍の掃除の例外と同じ)。
    root_b = _fake_root(
        tmp_path,
        COMMIT_B,
        'printf "boot-b\\n" >"$DOEFF_WORKER_BOOT_FILE"\n'
        'printf "ready\\n" >"$DOEFF_WORKER_READY_FILE"\n'
        "exit 1\n",
    )
    # 版 C は ready と committed を書く健康な版。
    root_c = _fake_root(
        tmp_path,
        COMMIT_C,
        'printf "boot-c\\n" >"$DOEFF_WORKER_BOOT_FILE"\n'
        'printf "ready\\n" >"$DOEFF_WORKER_READY_FILE"\n'
        'printf "committed\\n" >"$DOEFF_WORKER_COMMITTED_FILE"\n'
        "exec sleep 30\n",
    )
    _prepared(keeper, COMMIT_B, root_b)
    _prepared(keeper, COMMIT_C, root_c)

    elder = subprocess.Popen(["sleep", "30"])
    try:
        elder_gen = keeper / "gens" / "pod-test.1000"
        elder_gen.mkdir(parents=True)
        (elder_gen / "pid").write_text(f"{elder.pid}\n", encoding="utf-8")
        (elder_gen / "commit").write_text(f"{COMMIT_A}\n", encoding="utf-8")
        (elder_gen / "boot").write_text("boot-a\n", encoding="utf-8")
        (elder_gen / "ready").write_text("ready\n", encoding="utf-8")
        (elder_gen / "committed").write_text("committed\n", encoding="utf-8")
        (keeper / "commit").write_text(f"{COMMIT_A}\n", encoding="utf-8")

        pod_ready = tmp_path / "pod-ready"
        pod_boot = tmp_path / "pod-boot"
        pod_boot.write_text("boot-a\n", encoding="utf-8")

        probe = f"""set -eu
{_functions()}
keeper_dir={keeper}
keeper_script={BOOT}
pod_ready={pod_ready}
pod_boot={pod_boot}
target={COMMIT_A}
target_root={root_a}
active={elder_gen}
active_started=$(date +%s)
candidate=""
candidate_commit=""
candidate_root=""
candidate_started=0
rejected=""
restarts=0
unreadable=""
prepare_waiting=""

# 1 歩目: 版 B の候補を起こす。すぐ落ちるのを待つ。
keeper_tick
b_pid=$(cat "$candidate/pid")
tries=0
while keeper_alive "$b_pid" && [ "$tries" -lt 40 ]; do
  sleep 0.05
  tries=$((tries + 1))
done
! keeper_alive "$b_pid"

# 落ちた後に 3 回 tick しても、版 B は退けられて起こし直されず、旧い世代 A は drain されない。
keeper_tick
keeper_tick
keeper_tick
echo "AFTER_REJECT_DRAINING=$( [ -f "{elder_gen}/draining" ] && echo yes || echo no )"
echo "AFTER_REJECT_TARGET=$target"
echo "AFTER_REJECT_REJECTED=$rejected"
echo "AFTER_REJECT_FILE=$(cat "{keeper}/rejected" 2>/dev/null || true)"
echo "AFTER_REJECT_CANDIDATE=$candidate"

# 版の file を別の版 C へ替えると、C を試して committed の後に旧い世代を退かせる。
printf '%s\\n' "{COMMIT_C}" >"{version_file}"
keeper_tick
tries=0
while [ ! -f "$candidate/committed" ] && [ "$tries" -lt 40 ]; do
  sleep 0.05
  tries=$((tries + 1))
done
[ -f "$candidate/committed" ]
keeper_tick
echo "AFTER_C_DRAINING=$( [ -f "{elder_gen}/draining" ] && echo yes || echo no )"
echo "AFTER_C_TARGET=$target"
echo "AFTER_C_REJECTED=$rejected"
echo "AFTER_C_REJECTED_FILE_EXISTS=$( [ -f "{keeper}/rejected" ] && echo yes || echo no )"

k_pid=$(cat "$active/pid" 2>/dev/null || true)
[ -z "$k_pid" ] || kill -TERM "$k_pid" 2>/dev/null || true
"""
        done = _run_sh(probe, _base_env(tmp_path, version_file))
        out = dict(line.split("=", 1) for line in done.stdout.strip().splitlines() if "=" in line)
        assert out["AFTER_REJECT_DRAINING"] == "no", (out, done.stderr)
        assert out["AFTER_REJECT_TARGET"] == COMMIT_A, (out, done.stderr)
        assert out["AFTER_REJECT_REJECTED"] == COMMIT_B, (out, done.stderr)
        assert out["AFTER_REJECT_FILE"] == COMMIT_B, (out, done.stderr)
        assert out["AFTER_REJECT_CANDIDATE"] == "", (out, done.stderr)
        # 版 B の起こしは 1 回だけ、退けた log も 1 行だけ。
        assert done.stderr.count(f"doeff {COMMIT_B[:12]} の世代") == 1, done.stderr
        assert done.stderr.count("この版を退け") == 1, done.stderr
        assert COMMIT_B[:12] in done.stderr, done.stderr

        assert out["AFTER_C_DRAINING"] == "yes", (out, done.stderr)
        assert out["AFTER_C_TARGET"] == COMMIT_C, (out, done.stderr)
        assert out["AFTER_C_REJECTED"] == "", (out, done.stderr)
        assert out["AFTER_C_REJECTED_FILE_EXISTS"] == "no", (out, done.stderr)
        elder.wait(timeout=15)
    finally:
        if elder.poll() is None:
            elder.kill()
            elder.wait()


def test_keeper_resets_restart_backoff_only_when_active_generation_writes_committed(
    tmp_path: Path,
) -> None:
    """active の世代が ready だけ書いて committed を書かずに落ちた時は restarts を 0 へ戻さず、committed を書いた世代でだけ 0 へ戻す。"""
    keeper = tmp_path / "keeper"
    (keeper / "gens").mkdir(parents=True)
    (keeper / "prepare").mkdir(parents=True)
    version_file = tmp_path / "version"
    version_file.write_text(f"{COMMIT_A}\n", encoding="utf-8")

    root_a = _fake_root(tmp_path, COMMIT_A, "sleep 30")
    pod_ready = tmp_path / "pod-ready"
    pod_boot = tmp_path / "pod-boot"

    # 1) ready だけ書いて committed を書いていない active 世代: restarts=2 が 0 へ戻らない。
    uncommitted_proc = subprocess.Popen(["sleep", "30"])
    committed_proc = subprocess.Popen(["sleep", "30"])
    try:
        gen_u = keeper / "gens" / "pod-test.1001"
        gen_u.mkdir(parents=True)
        (gen_u / "pid").write_text(f"{uncommitted_proc.pid}\n", encoding="utf-8")
        (gen_u / "commit").write_text(f"{COMMIT_A}\n", encoding="utf-8")
        (gen_u / "boot").write_text("boot-u\n", encoding="utf-8")
        (gen_u / "ready").write_text("ready\n", encoding="utf-8")

        gen_c = keeper / "gens" / "pod-test.1002"
        gen_c.mkdir(parents=True)
        (gen_c / "pid").write_text(f"{committed_proc.pid}\n", encoding="utf-8")
        (gen_c / "commit").write_text(f"{COMMIT_A}\n", encoding="utf-8")
        (gen_c / "boot").write_text("boot-c\n", encoding="utf-8")
        (gen_c / "ready").write_text("ready\n", encoding="utf-8")
        (gen_c / "committed").write_text("committed\n", encoding="utf-8")

        probe = f"""set -eu
{_functions()}
keeper_dir={keeper}
keeper_script={BOOT}
pod_ready={pod_ready}
pod_boot={pod_boot}
target={COMMIT_A}
target_root={root_a}
active={gen_u}
active_started=$(date +%s)
candidate=""
candidate_commit=""
candidate_root=""
candidate_started=0
rejected=""
restarts=2
unreadable=""
prepare_waiting=""

# gen_c はまだ別の世代なので消されないよう dir 外で試すため、gen_u だけで tick する。
mv "{gen_c}" "{tmp_path}/gen_c"
keeper_tick
echo "RESTARTS_AFTER_READY_ONLY=$restarts"

# 今度は committed を書いた世代で tick すると restarts が 0 に戻る。
mv "{tmp_path}/gen_c" "{gen_c}"
rm -rf "{gen_u}"
active={gen_c}
keeper_tick
echo "RESTARTS_AFTER_COMMITTED=$restarts"
"""
        done = _run_sh(probe, _base_env(tmp_path, version_file))
        out = dict(line.split("=", 1) for line in done.stdout.strip().splitlines() if "=" in line)
        assert out["RESTARTS_AFTER_READY_ONLY"] == "2", (out, done.stderr)
        assert out["RESTARTS_AFTER_COMMITTED"] == "0", (out, done.stderr)
    finally:
        for p in (uncommitted_proc, committed_proc):
            if p.poll() is None:
                p.kill()
                p.wait()
