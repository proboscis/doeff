"""日次の全体検証が走る機体の道具の依存の宣言と準備(agora-redesign #3870)。

日次の全体検証(.agents/land-queue.toml の gate.full)は verify-worker の Pod の中でなく、remote_check で木を zeus の上へ送って
走る。本物の Redis の配達の法(packages/doeff-events/tests/test_notice_laws_redis.py)は PATH の redis-server を立てるので、走る
機体に redis-server が無いと 1 本も走らない — 2026-10-08 8:00 の回は 14 本とも skip で、Pod の image に入れた redis-server は
効かなかった(テストは zeus の ~/ai-remote-check/doeff-gate-full で走り、zeus の PATH に redis-server が無い)。

道具の依存の宣言の 1 点 = scripts/gate_tools.sh(名・版・取り寄せ元の image の digest)。処理ステージ packages はテストの前に
`sh scripts/gate_tools.sh path` を実行し、答えた dir を PATH の前に載せる。

- (a) 宣言: packages の処理ステージは make test-packages の前に道具の準備を実行し、準備が失敗すれば make test-packages を
  実行しない(&&)。
- (b) 依存を満たせない機体(PATH にも cache にも無く、取り寄せの道具 docker も無い)では、準備が 0 以外で終わり、道具の名・
  版・image・探した PATH を挙げる — 処理ステージは赤で、黙って skip にならない。
- (c) PATH に宣言の版の redis-server が在れば何も足さない(verify-worker の Pod の image)。cache の dir に在れば、その dir を答える。
- (d) 版の違う redis-server が PATH に在っても宣言の版として数えない(cache も取り寄せも無ければ (b) と同じ赤)。
- (e) 取り寄せ: 宣言の image(digest つき)から実行 file 1 つを取り出して cache の dir に置き、その dir を答える。
"""

from __future__ import annotations

import shutil
import tomllib
from pathlib import Path

from doeff_core_effects.os_process import subprocess_handler
from doeff_core_effects.process_effects import EnvEntry, EnvMode, ProcessOutcome, RunProcess

from doeff import run, with_handlers

REPO_ROOT = Path(__file__).resolve().parents[1]
GATE_TOOLS = REPO_ROOT / "scripts" / "gate_tools.sh"
LAND_QUEUE = REPO_ROOT / ".agents" / "land-queue.toml"

REDIS_VERSION = "7.4.11"
# agora の deploy/verify-worker-image/Dockerfile の REDIS_IMAGE と同じ値(本番 deploy/agora-events の redis:7.4.11 と同じ版)。
REDIS_IMAGE = (
    "redis:7.4.11-bookworm@sha256:4fa24486b8bcca8eec45ee0eb166edc674795e53a2b53d1a9ef263eecebaac85"
)
# 準備の script が使う道具(この機体の物を stand-in の PATH の dir へ link する — docker と redis-server は入れない)。
SCRIPT_TOOLS = ("chmod", "grep", "mkdir", "mktemp", "mv", "rm", "rmdir")


def _version_answer(version: str) -> str:
    """`redis-server --version` に答える stand-in(本物と同じ 1 行の形)。"""
    return (
        "#!/bin/sh\n"
        f'echo "Redis server v={version} sha=00000000:0 malloc=libc bits=64 build=0000000000000000"\n'
    )


def _stand_in_bin(directory: Path, *, redis_version: str | None = None, docker: bool = False) -> Path:
    """準備の script が要る道具だけを持つ PATH の dir(redis-server と docker は求めた時だけ stand-in を置く)。"""
    directory.mkdir(parents=True, exist_ok=True)
    for name in SCRIPT_TOOLS:
        found = shutil.which(name)
        assert found is not None, f"この機体に {name} が無い(準備の script の前提)"
        (directory / name).symlink_to(found)
    if redis_version is not None:
        _write_tool(directory / "redis-server", _version_answer(redis_version))
    if docker:
        _write_tool(directory / "docker", _docker_stand_in(directory.parent / "docker-calls"))
    return directory


def _docker_stand_in(record: Path) -> str:
    """create・cp・rm に答える docker の stand-in: create の image を record に書き、cp は宣言の版の stand-in を置く。"""
    answer = _version_answer(REDIS_VERSION).replace("\n", "\\n").replace('"', '\\"')
    return (
        "#!/bin/sh\n"
        f'echo "$*" >> "{record}"\n'
        'case "$1" in\n'
        '  create) echo stand-in-container ;;\n'
        f'  cp) printf "{answer}" > "$3" ;;\n'
        "  rm) : ;;\n"
        "  *) exit 64 ;;\n"
        "esac\n"
    )


def _write_tool(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)


def _prepare(path_dir: Path, home: Path) -> ProcessOutcome:
    """`sh scripts/gate_tools.sh path` を、stand-in の PATH と HOME だけを見る子の process で実行する。"""
    home.mkdir(parents=True, exist_ok=True)
    done = run(
        with_handlers(
            [subprocess_handler],
            RunProcess(
                argv=("/bin/sh", str(GATE_TOOLS), "path"),
                env=(
                    EnvEntry(name="PATH", value=str(path_dir)),
                    EnvEntry(name="HOME", value=str(home)),
                ),
                env_mode=EnvMode.REPLACE,
                timeout=30.0,
            ),
        )
    )
    assert not done.timed_out, f"準備の script が 30 秒で終わらなかった\n{done.stdout}\n{done.stderr}"
    return done


def _cache(home: Path) -> Path:
    return home / ".cache" / f"redis-server-{REDIS_VERSION}"


def test_a_the_packages_stage_prepares_the_declared_tools_before_its_tests() -> None:
    stages = {stage["name"]: stage["run"] for stage in tomllib.loads(LAND_QUEUE.read_text("utf-8"))["gate"]["full"]}
    packages = stages["packages"]
    prepared = "gate_path=\\$(sh scripts/gate_tools.sh path) && PATH=\\$gate_path\\$PATH "
    assert prepared in packages, packages
    assert packages.index(prepared) < packages.index("make test-packages"), packages


def test_b_a_machine_that_cannot_provide_the_tool_fails_naming_it(tmp_path: Path) -> None:
    path_dir = _stand_in_bin(tmp_path / "bin")
    done = _prepare(path_dir, tmp_path / "home")
    assert done.exit_code == 1, done
    assert done.stdout == "", done
    for named in ("redis-server", REDIS_VERSION, REDIS_IMAGE, str(path_dir)):
        assert named in done.stderr, (named, done.stderr)


def test_c_the_declared_version_on_path_adds_nothing(tmp_path: Path) -> None:
    done = _prepare(_stand_in_bin(tmp_path / "bin", redis_version=REDIS_VERSION), tmp_path / "home")
    assert (done.exit_code, done.stdout) == (0, ""), done


def test_c_the_cached_tool_is_answered_as_a_path_directory(tmp_path: Path) -> None:
    home = tmp_path / "home"
    _cache(home).mkdir(parents=True)
    _write_tool(_cache(home) / "redis-server", _version_answer(REDIS_VERSION))
    done = _prepare(_stand_in_bin(tmp_path / "bin"), home)
    assert (done.exit_code, done.stdout) == (0, f"{_cache(home)}:"), done


def test_d_another_version_on_path_is_not_the_declared_tool(tmp_path: Path) -> None:
    done = _prepare(_stand_in_bin(tmp_path / "bin", redis_version="7.0.15"), tmp_path / "home")
    assert done.exit_code == 1, done
    assert REDIS_IMAGE in done.stderr, done.stderr


def test_e_the_tool_is_fetched_from_the_declared_image_into_the_cache(tmp_path: Path) -> None:
    home = tmp_path / "home"
    done = _prepare(_stand_in_bin(tmp_path / "bin", docker=True), home)
    assert (done.exit_code, done.stdout) == (0, f"{_cache(home)}:"), done
    calls = (tmp_path / "docker-calls").read_text("utf-8").splitlines()
    assert calls[0] == f"create {REDIS_IMAGE}", calls
    assert calls[1].startswith("cp stand-in-container:/usr/local/bin/redis-server "), calls
    assert calls[2] == "rm stand-in-container", calls
    assert f"v={REDIS_VERSION} " in (_cache(home) / "redis-server").read_text("utf-8")
