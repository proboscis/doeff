"""doeff-linter の置き場(packages/doeff-linter/scripts/linter_snapshot.py・agora-redesign #1582・#2001)の検。

偽の cargo(PATH の先頭)で組み立てを模し、本物の git の repo から断面を取り出す。script は各 repo の hook が呼ぶ入口の形
(`uv run --script <path> <checkout> <commit>`)で撃つ。
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "packages" / "doeff-linter" / "scripts" / "linter_snapshot.py"

# 偽の cargo — 断面の marker を読み、DOEFF_LINTER_BUILD_COMMIT(と FAKE_CARGO_SUFFIX)を名乗る linter を置く。組んだ回数を数える。
FAKE_CARGO = """#!/bin/sh
manifest=$5
marker=$(cat "$(dirname "$manifest")/marker")
mkdir -p "$CARGO_TARGET_DIR/release"
echo built >> "$FAKE_CARGO_COUNT"
sleep 1
printf '#!/bin/sh\\necho "doeff-linter 0.2.0 (doeff %s%s)"\\necho "%s"\\n' "$DOEFF_LINTER_BUILD_COMMIT" "$FAKE_CARGO_SUFFIX" "$marker" > "$CARGO_TARGET_DIR/release/doeff-linter"
chmod +x "$CARGO_TARGET_DIR/release/doeff-linter"
"""


def stage(tmp_path: Path) -> Path:
    """linter の入力の 2 つの dir を commit した仮の doeff の repo(marker = committed)と、偽の cargo の bin を作る。"""
    repo = tmp_path / "doeff"
    for rel in ("packages/doeff-linter", "packages/doeff-indexer"):
        (repo / rel).mkdir(parents=True)
    (repo / "packages/doeff-linter/Cargo.toml").write_text('[package]\nname = "doeff-linter"\n', encoding="utf-8")
    (repo / "packages/doeff-linter/marker").write_text("committed", encoding="utf-8")
    (repo / "packages/doeff-indexer/Cargo.toml").write_text('[package]\nname = "doeff-indexer"\n', encoding="utf-8")
    # git は最小の環境で撃つ(`env -i` — 外側の GIT_* も、使う人の git の設定も読まない)。
    git_env = ["env", "-i", f"PATH={os.pathsep.join(os.get_exec_path())}", f"HOME={tmp_path}"]
    for argv in (["init", "-q"], ["add", "."], ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "x"]):
        subprocess.run([*git_env, "git", "-C", str(repo), *argv], check=True, capture_output=True)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "cargo").write_text(FAKE_CARGO, encoding="utf-8")
    (bin_dir / "cargo").chmod(0o755)
    return repo


def environ(tmp_path: Path, suffix: str) -> list[str]:
    """子の命令の頭に付ける `env` — 子はこの process の環境を継ぎ、偽の cargo を PATH の先頭に置いて 3 つの名を足す。"""
    return [
        "env",
        f"PATH={os.pathsep.join([str(tmp_path / 'bin'), *os.get_exec_path()])}",
        f"DOEFF_LINTER_SNAPSHOT_DIR={tmp_path / 'store'}",
        f"FAKE_CARGO_COUNT={tmp_path / 'count'}",
        f"FAKE_CARGO_SUFFIX={suffix}",
    ]


def snapshot(repo: Path, env: list[str]) -> subprocess.Popen[str]:
    return subprocess.Popen(
        [*env, "uv", "run", "--script", str(SCRIPT), str(repo), "HEAD"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )


def test_three_concurrent_calls_build_the_snapshot_once(tmp_path: Path) -> None:
    # 同じ sha を 3 本が同時に求めても組むのは 1 度で、3 本とも同じ置き場の linter を受け取る(錠は sha の単位)。
    # 作業木の未 commit の変更(marker = moving)を読まず、commit の object の断面(committed)から組む。
    repo = stage(tmp_path)
    (repo / "packages/doeff-linter/marker").write_text("moving", encoding="utf-8")
    env = environ(tmp_path, "")
    procs = [snapshot(repo, env) for _ in range(3)]
    outs = [p.communicate(timeout=50) for p in procs]
    for proc, (_, err) in zip(procs, outs, strict=True):
        assert proc.returncode == 0, err
    paths = {out.strip() for out, _ in outs}
    assert len(paths) == 1, paths
    assert (tmp_path / "count").read_text(encoding="utf-8") == "built\n"
    printed = subprocess.run([next(iter(paths))], capture_output=True, text=True, check=True).stdout
    assert "committed" in printed
    assert "moving" not in printed
    # 一時の名と組み立ての途中の名は残らない。
    assert sorted(p.suffix for p in (tmp_path / "store").iterdir()) == ["", ".lock"]


def test_a_built_snapshot_is_reused_without_building(tmp_path: Path) -> None:
    # 置き場に在る sha は組まずに同じ path を返す。
    repo = stage(tmp_path)
    env = environ(tmp_path, "")
    first = snapshot(repo, env).communicate(timeout=50)[0].strip()
    second = snapshot(repo, env).communicate(timeout=50)[0].strip()
    assert first == second
    assert first.endswith("/doeff-linter")
    assert (tmp_path / "count").read_text(encoding="utf-8") == "built\n"


def test_two_shas_build_into_one_kept_cargo_target(tmp_path: Path) -> None:
    # 違う sha の 2 本は置き場の隣の同じ cargo の target(<store の親>/cargo-target/doeff-linter)で組み、target は組んだ後も残る —
    # 依存の crate を sha をまたいで使い回すため(#2977)。反例: 一時の dir に組んで消す前の形では、target が残らずこの検が赤。
    repo = stage(tmp_path)
    env = environ(tmp_path, "")
    first = snapshot(repo, env).communicate(timeout=50)[0].strip()
    (repo / "packages/doeff-linter/marker").write_text("second", encoding="utf-8")
    git_env = ["env", "-i", f"PATH={os.pathsep.join(os.get_exec_path())}", f"HOME={tmp_path}"]
    subprocess.run([*git_env, "git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-am", "y"],
                   check=True, capture_output=True)
    second = snapshot(repo, env).communicate(timeout=50)[0].strip()
    assert first != second
    assert (tmp_path / "count").read_text(encoding="utf-8") == "built\nbuilt\n"
    kept = tmp_path / "cargo-target" / "doeff-linter" / "release" / "doeff-linter"
    assert kept.exists()
    assert "second" in subprocess.run([str(kept)], capture_output=True, text=True, check=True).stdout


def test_a_linter_not_naming_the_sha_is_not_placed(tmp_path: Path) -> None:
    # 反例: 組んだ linter が sha ちょうどを名乗らなければ(手元の変更の印 +dirty)、置き場に置かず理由を 1 行出して 1 で終わる。
    repo = stage(tmp_path)
    proc = snapshot(repo, environ(tmp_path, "+dirty"))
    out, err = proc.communicate(timeout=50)
    assert proc.returncode == 1, (out, err)
    assert out == ""
    assert "名乗らない" in err
    assert [p for p in (tmp_path / "store").iterdir() if p.is_dir()] == []


def test_a_missing_commit_is_unavailable(tmp_path: Path) -> None:
    # 反例: doeff に無い commit は組まずに理由を返す。
    repo = stage(tmp_path)
    env = environ(tmp_path, "")
    proc = subprocess.Popen(
        [*env, "uv", "run", "--script", str(SCRIPT), str(repo), "0" * 40],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    out, err = proc.communicate(timeout=50)
    assert proc.returncode == 1, (out, err)
    assert "が無い" in err
    assert not (tmp_path / "count").exists()
