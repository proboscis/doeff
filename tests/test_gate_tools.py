"""日次の全体検証が走る所の道具の依存の宣言と準備(agora-redesign #3870・card ki-9338eec1d15e)。

日次の全体検証(.agents/land-queue.toml の gate.full)は、2026-10-10 から日次の task が走っている所(atlas の doeff-worker の Pod)で
直に走る(ADR-DOE-ENFORCE-001 R10 — それまでは remote_check で木を zeus の上へ送って走っていた)。本物の Redis の配達の法
(packages/doeff-events/tests/test_notice_laws_redis.py)は PATH の redis-server を立てるので、走る所に redis-server が無いと 1 本も
走らない — 2026-10-08 8:00 の回は 14 本とも skip だった(テストは zeus で走り、zeus の PATH に redis-server が無い)。日次の task は
宣言の版の redis-server を task の HOME の .local/bin(PATH の先頭)に置く。

道具の依存の宣言の 1 点 = scripts/gate_tools.sh(名・版・取り寄せ元)。処理ステージ packages はテストの前に
`sh scripts/gate_tools.sh path` を実行し、答えた dir を PATH の前に載せる。処理ステージ lint は基点の比べの前に
`sh scripts/gate_tools.sh linter` を実行し、基点の file が名乗る鍵の doeff-linter を、比べの道具が探す断面の置き場に用意する
(zeus の land-arm の開発版と断面の置き場は、走行ごとに空の task の HOME に無い — 用意しないと --strict の「測れない」で赤)。

- (a) 宣言: packages の処理ステージは make test-packages の前に道具の準備を実行し、準備が失敗すれば make test-packages を
  実行しない(&&)。
- (b) 依存を満たせない機体(PATH にも cache にも無く、取り寄せの道具 docker も無い)では、準備が 0 以外で終わり、道具の名・
  版・image・探した PATH を挙げる — 処理ステージは赤で、黙って skip にならない。
- (c) PATH に宣言の版の redis-server が在れば何も足さない(日次の task の .local/bin)。cache の dir に在れば、その dir を答える。
- (d) 版の違う redis-server が PATH に在っても宣言の版として数えない(cache も取り寄せも無ければ (b) と同じ赤)。
- (e) 取り寄せ: 宣言の image(digest つき)から実行 file 1 つを取り出して cache の dir に置き、その dir を答える。
- (f) 宣言: lint の処理ステージは doeff-linter の基点の比べ(--strict)の前に linter の準備を実行する(&&)。
- (g) linter の準備: 呼び手の DOEFF_LINTER_SNAPSHOT_DIR(走行をまたいで残る置き場)を断面の置き場($HOME/.cache/doeff-linter-snapshots
  — scripts/doeff_linter_locked.py が探す所)へ結び、基点の file を最後に書いた commit の linter を
  packages/doeff-linter/scripts/linter_snapshot.py でその置き場に用意する(在れば組まない・同じ鍵の linter が在れば写す)。
- (h) 断面の置き場が既に在る機体・DOEFF_LINTER_SNAPSHOT_DIR の無い呼び手では、置き場を結び替えない。
- (i) 基点を書いた commit が git に無い・組めない時は、準備が 0 以外で終わって名指す(処理ステージは赤)。
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
# 準備の script が使う道具(この機体の物を stand-in の PATH の dir へ link する — docker・redis-server・git・uv は入れない)。
SCRIPT_TOOLS = ("chmod", "dirname", "grep", "ln", "mkdir", "mktemp", "mv", "rm", "rmdir")
# 基点の file を最後に書いた commit の stand-in(git log の答え)。
BASELINE_COMMIT = "1" * 40


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


def _prepare(
    path_dir: Path, home: Path, verb: str = "path", env: tuple[EnvEntry, ...] = ()
) -> ProcessOutcome:
    """`sh scripts/gate_tools.sh <verb>` を、stand-in の PATH と HOME(と env)だけを見る子の process で実行する。"""
    home.mkdir(parents=True, exist_ok=True)
    done = run(
        with_handlers(
            [subprocess_handler],
            RunProcess(
                argv=("/bin/sh", str(GATE_TOOLS), verb),
                env=(
                    EnvEntry(name="PATH", value=str(path_dir)),
                    EnvEntry(name="HOME", value=str(home)),
                    *env,
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
    prepared = "gate_path=$(sh scripts/gate_tools.sh path) && PATH=$gate_path$PATH "
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


def _linter_stand_ins(directory: Path, *, commit: str, uv_rc: int = 0) -> Path:
    """linter の準備が呼ぶ git と uv の stand-in を置いた PATH の dir: git は `log` に commit を答え(空なら何も答えない)、uv は
    呼ばれた引数と DOEFF_LINTER_SNAPSHOT_DIR を record に書いて uv_rc で終わる。"""
    path_dir = _stand_in_bin(directory)
    record = directory.parent / "calls"
    _write_tool(
        path_dir / "git",
        f'#!/bin/sh\necho "git $*" >> "{record}"\ncase "$*" in *" log "*) [ -n "{commit}" ] && echo "{commit}" ;; esac\nexit 0\n',
    )
    _write_tool(
        path_dir / "uv",
        f'#!/bin/sh\necho "uv store=$DOEFF_LINTER_SNAPSHOT_DIR $*" >> "{record}"\nexit {uv_rc}\n',
    )
    return path_dir


def _calls(tmp_path: Path) -> list[str]:
    return (tmp_path / "calls").read_text("utf-8").splitlines()


def _linter_store(home: Path) -> Path:
    """scripts/doeff_linter_locked.py が探す断面の置き場(HOME の下の決まった場所)。"""
    return home / ".cache" / "doeff-linter-snapshots"


def test_f_the_lint_stage_prepares_the_baseline_linter_before_the_strict_check() -> None:
    stages = {stage["name"]: stage["run"] for stage in tomllib.loads(LAND_QUEUE.read_text("utf-8"))["gate"]["full"]}
    lint = stages["lint"]
    prepared = "sh scripts/gate_tools.sh linter && "
    assert prepared in lint, lint
    assert lint.index(prepared) < lint.index("hook_finding_baseline.py --strict check doeff-linter"), lint


def test_g_the_linter_is_prepared_from_the_baseline_commit_into_the_kept_store(tmp_path: Path) -> None:
    home = tmp_path / "home"
    kept = tmp_path / "work" / "daily-verify-cache" / "doeff-linter-snapshots"
    path_dir = _linter_stand_ins(tmp_path / "bin", commit=BASELINE_COMMIT)
    done = _prepare(path_dir, home, "linter", (EnvEntry(name="DOEFF_LINTER_SNAPSHOT_DIR", value=str(kept)),))
    assert (done.exit_code, done.stdout) == (0, ""), done
    # 走行をまたいで残る置き場を、比べの道具が探す置き場へ結ぶ(task の HOME は走行ごとに空)。
    assert _linter_store(home).is_symlink(), done
    assert _linter_store(home).resolve() == kept.resolve()
    calls = _calls(tmp_path)
    assert f"git -C {REPO_ROOT} log -1 --format=%H -- scripts/hook_finding_baseline/doeff-linter.json" in calls, calls
    snapshot = REPO_ROOT / "packages" / "doeff-linter" / "scripts" / "linter_snapshot.py"
    assert f"uv store={_linter_store(home)} run --script {snapshot} {REPO_ROOT} {BASELINE_COMMIT}" in calls, calls


def test_h_an_existing_store_and_a_caller_without_a_kept_store_are_not_relinked(tmp_path: Path) -> None:
    home = tmp_path / "home"
    _linter_store(home).mkdir(parents=True)
    kept = tmp_path / "kept"
    path_dir = _linter_stand_ins(tmp_path / "bin", commit=BASELINE_COMMIT)
    done = _prepare(path_dir, home, "linter", (EnvEntry(name="DOEFF_LINTER_SNAPSHOT_DIR", value=str(kept)),))
    assert done.exit_code == 0, done
    assert not _linter_store(home).is_symlink() and not kept.exists()

    other_home = tmp_path / "other-home"
    done = _prepare(path_dir, other_home, "linter")
    assert done.exit_code == 0, done
    assert not _linter_store(other_home).is_symlink()
    assert f"uv store={_linter_store(other_home)} run --script" in "\n".join(_calls(tmp_path)), _calls(tmp_path)


def test_i_a_missing_baseline_commit_or_a_failed_build_is_named_and_red(tmp_path: Path) -> None:
    no_commit = _prepare(_linter_stand_ins(tmp_path / "a" / "bin", commit=""), tmp_path / "a" / "home", "linter")
    assert no_commit.exit_code == 1, no_commit
    assert "scripts/hook_finding_baseline/doeff-linter.json" in no_commit.stderr, no_commit.stderr
    assert not any(call.startswith("uv ") for call in _calls(tmp_path / "a")), _calls(tmp_path / "a")

    failed = _prepare(
        _linter_stand_ins(tmp_path / "b" / "bin", commit=BASELINE_COMMIT, uv_rc=1), tmp_path / "b" / "home", "linter"
    )
    assert failed.exit_code == 1, failed
    assert BASELINE_COMMIT in failed.stderr and "doeff-linter" in failed.stderr, failed.stderr
