"""doeff の Rust の package の PEP 517 の build の口(agora-redesign #1493)。

maturin の口を包み、cargo の target を package の dir の中ではなく、1 回の build ごとに作る一時の dir に置き、
wheel を作ったら消す。uv sync・uv build・別の repo の path の依存・uv の git の checkout からの build が
この口を通るので、どの作業木にも target が残らない。

1 つの共有の target にしない理由: cargo は source の file の時刻で新旧を判定し、成果物を workspace の中の
相対 path で名付ける。2 つの作業木が 1 つの target を共有すると、先に作った作業木の source は後の build の
成果物より古く見え、別の作業木の build を黙って使う(cargo 1.96.1 で実測・agora-redesign #1472 の comment)。

利用者が CARGO_TARGET_DIR を渡した時はそこに組み、消さない — Rust を直すセッションが差分の build を使う
ための口。その dir を中身の違う作業木どうしで共有すると上の取り違えが起きる。cargo では env の
CARGO_TARGET_DIR が config の build.target-dir(と CARGO_BUILD_TARGET_DIR)に勝つので、口の一時の dir は
それらの設定にも勝つ — 手元の target を使いたい時は CARGO_TARGET_DIR で渡す。

一時の dir の名には build の process の pid を入れる(doeff-cargo-target-<pid>-<乱字>)。SIGTERM・SIGKILL で
止められた build は後始末をしないので、次の build が入口で、持ち主の process が居ない dir を片づける。

正本は tools/doeff_cargo_backend.py。各 package の doeff_cargo_backend.py はこの file への symlink
(backend-path は package の dir の中しか指せない — PEP 517)。`python doeff_cargo_backend.py <命令…>` は
命令を同じ扱いの target の中で走らせる(文書が勧める maturin develop)。

wheel は source の中身の hash で引く(agora-redesign #2364): 一時の target では毎回すべてを組み直す(doeff-linter で 8〜10 分)。
uv は path の依存を file の時刻(tool.uv.cache-keys)で見るので、pin を上げて作業木を作り直す・checkout し直すと、中身の同じ
source でも口を呼ぶ。口は build を始める前に、組みに効く物(tool.uv.cache-keys の file の中身と相対 path・rustc と maturin の版・
機体・組む Python の版と ABI・組みを変える環境変数(RUSTFLAGS・CARGO_PROFILE_* など — #2969)・build の設定)の hash で
`$XDG_CACHE_HOME/doeff-cargo-wheels/<package>/<hash>/` を見て、在ればその wheel を写して返す
(cargo を撃たない)。無ければ組んで置く。どちらも stderr に 1 行(使った / 組んだ秒)。置き場は env DOEFF_WHEEL_CACHE で替えられる。
editable と sdist は引かない(editable は作業木を指すので中身で共有できない)。
"""

from __future__ import annotations

import glob
import hashlib
import os
import platform
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import time
import tomllib
from collections.abc import Iterator, Mapping
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import TypeAlias

import maturin

TARGET_ENV = "CARGO_TARGET_DIR"
TEMP_PREFIX = "doeff-cargo-target-"
WHEEL_CACHE_ENV = "DOEFF_WHEEL_CACHE"
# tool.uv.cache-keys の file を宣言しない package の、組みに効く file の既定(package の dir からの glob)。
DEFAULT_SOURCE_GLOBS = ("pyproject.toml", "Cargo.toml", "Cargo.lock", "build.rs", "src/**/*")
# wheel の中身を変える環境変数(置き場の鍵に名と値を入れる・agora-redesign #2969): rustc の旗・cargo の profile と build の設定・
# maturin と PyO3 の設定。
BUILD_ENV_NAMES = frozenset({"RUSTFLAGS", "CARGO_ENCODED_RUSTFLAGS", "RUSTC", "MACOSX_DEPLOYMENT_TARGET"})
BUILD_ENV_PREFIXES = ("CARGO_PROFILE_", "CARGO_BUILD_", "MATURIN_", "PYO3_")
# 上の接頭辞に当たるが、組む場所と並べる数だけを変えて中身を変えない名 — 鍵に入れると作業木や機体の混み方ごとに置き場が割れて
# 共有が効かなくなる。
PLACE_ONLY_ENV_NAMES = frozenset({"CARGO_BUILD_TARGET_DIR", "CARGO_BUILD_JOBS"})

# PEP 517 の config_settings: frontend が渡す「設定の名 → 文字列か文字列の list」。口は中を読まず maturin へ渡す。
ConfigSettings: TypeAlias = Mapping[str, str | list[str]]


def _owner_pid(name: str) -> int | None:
    """一時の target の名(doeff-cargo-target-<pid>-<乱字>)から、それを作った build の pid を読む。"""
    match name.removeprefix(TEMP_PREFIX).split("-", 1):
        case [pid, _] if pid.isdigit():
            return int(pid)
        case _:
            # この口の名の形ではない — 持ち主を言えないので片づけの対象にしない。
            return None


def _alive(pid: int) -> bool:
    """pid の process がまだ居るか(居なければ、その build は止められて後始末をしていない)。"""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # 別の利用者の process が同じ pid で居る。
        return True
    return True


def _sweep_orphans(root: Path) -> None:
    """止められた build が残した一時の target を消す。持ち主の process が居ない dir だけで、走っている build には触らない。"""
    for path in root.glob(f"{TEMP_PREFIX}*"):
        match _owner_pid(path.name):
            case int(pid) if not _alive(pid):
                # 同時に入口に来た別の build が先に消していれば、消えた file は探さない。
                with suppress(FileNotFoundError):
                    shutil.rmtree(path)
            case _:
                pass


@contextmanager
def cargo_target_dir() -> Iterator[Path]:
    """この build の cargo の target。利用者の指定が無ければ一時の dir を作って env に渡し、抜ける時に消す。"""
    match os.environ.get(TARGET_ENV):
        case (None | "") as absent:
            root = Path(tempfile.gettempdir())
            _sweep_orphans(root)
            made = Path(tempfile.mkdtemp(prefix=f"{TEMP_PREFIX}{os.getpid()}-", dir=root))
            os.environ[TARGET_ENV] = str(made)
            try:
                yield made
            finally:
                match absent:
                    case None:
                        del os.environ[TARGET_ENV]
                    case str():
                        os.environ[TARGET_ENV] = absent
                shutil.rmtree(made)
        case str() as given:
            yield Path(given)


def build_wheel(
    wheel_directory: str,
    config_settings: ConfigSettings | None = None,
    metadata_directory: str | None = None,
) -> str:
    """uv sync・uv build が wheel を求めた時に、同じ中身の source の wheel が置いてあればそれを写し、無ければ作業木の外の target で
    組んで置くため(頭の註)。"""
    slot = _wheel_slot(Path.cwd(), config_settings)
    found = sorted(slot.glob("*.whl"))
    if found:
        shutil.copy2(found[0], Path(wheel_directory) / found[0].name)
        print(f"doeff_cargo_backend: 同じ source の wheel を使う(組まない)— {found[0].name}・{slot}", file=sys.stderr)
        return found[0].name
    started = time.monotonic()
    with cargo_target_dir():
        name = maturin.build_wheel(wheel_directory, config_settings, metadata_directory)
    _store_wheel(slot, Path(wheel_directory) / name)
    print(f"doeff_cargo_backend: wheel を組んだ({time.monotonic() - started:.0f} 秒)— {name}・{slot} に置いた", file=sys.stderr)
    return name


def _source_files(package: Path) -> list[Path]:
    """組みに効く file(package の tool.uv.cache-keys の file の glob か既定 — uv が組み直しを判じる集合と同じ)を相対 path の順で並べる。"""
    declared = tomllib.loads((package / "pyproject.toml").read_text(encoding="utf-8")).get("tool", {}).get("uv", {}).get("cache-keys", [])
    globs = [key["file"] for key in declared if isinstance(key, dict) and isinstance(key.get("file"), str)] or list(DEFAULT_SOURCE_GLOBS)
    matched = {Path(hit) for pattern in globs for hit in glob.glob(str(package / pattern), recursive=True)}
    return sorted((path for path in matched if path.is_file()), key=lambda path: os.path.relpath(path, package))


def _tool_versions() -> str:
    """組みに効く道具の版(rustc と maturin)— 道具が替われば同じ source でも wheel を組み直すため。"""
    rustc = subprocess.run(["rustc", "-V"], capture_output=True, text=True, check=False).stdout.strip()
    return f"rustc={rustc} maturin={getattr(maturin, '__version__', '?')}"


def _build_environment() -> str:
    """組みを変える環境変数(BUILD_ENV_NAMES と BUILD_ENV_PREFIXES に当たる名、置き場だけの名を除く)の名と値を並べるため
    (agora-redesign #2969 — 同じ source でも RUSTFLAGS・CARGO_PROFILE_RELEASE_STRIP などを変えて組んだ wheel を、既定の build が
    引かないように)。値は鍵の hash に入るだけで、どこにも出さない。"""
    chosen = sorted(
        (name, value)
        for name, value in os.environ.items()
        if (name in BUILD_ENV_NAMES or name.startswith(BUILD_ENV_PREFIXES)) and name not in PLACE_ONLY_ENV_NAMES
    )
    return repr(chosen)


def _python_abi() -> str:
    """組む Python の版・ABI・機体(拡張 module の名の末尾 — 例 `.cpython-314t-x86_64-linux-gnu.so`)— free-threading の cp314t と
    cp313 のように ABI の違う環境で同じ source を組んでも、置き場の wheel を取り違えないため(agora-redesign #2969)。"""
    return f"{sys.implementation.cache_tag} {sysconfig.get_config_var('EXT_SUFFIX')}"


def _wheel_slot(package: Path, config_settings: ConfigSettings | None) -> Path:
    """source の中身・道具の版・機体・組む Python の ABI・組みを変える環境変数・build の設定の hash で、wheel を置く dir を決めるため
    (file の時刻と作業木の path に依らない — 置き場だけを変える CARGO_TARGET_DIR なども入れない)。"""
    digest = hashlib.sha256()
    lines = (_tool_versions(), sys.platform, platform.machine(), _python_abi(), _build_environment(), repr(sorted((config_settings or {}).items())))
    for line in lines:
        digest.update(line.encode("utf-8") + b"\0")
    for path in _source_files(package):
        digest.update(os.path.relpath(path, package).encode("utf-8") + b"\0")
        digest.update(path.read_bytes() + b"\0")
    base = os.environ.get(WHEEL_CACHE_ENV) or str(Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "doeff-cargo-wheels")
    # 置き場の名は package の名(作業木の dir の名は作業木ごとに違うので使わない)。
    project = tomllib.loads((package / "pyproject.toml").read_text(encoding="utf-8")).get("project", {}).get("name", "unnamed")
    return Path(base) / str(project) / digest.hexdigest()[:32]


def _store_wheel(slot: Path, built: Path) -> None:
    """組んだ wheel を置き場へ写すため(一時の名で書いてから名を替える — 同じ hash を同時に組んだ別の build と混ざらない)。"""
    slot.mkdir(parents=True, exist_ok=True)
    staged = slot / f".{built.name}.{os.getpid()}.part"
    shutil.copy2(built, staged)
    os.replace(staged, slot / built.name)


def build_editable(
    wheel_directory: str,
    config_settings: ConfigSettings | None = None,
    metadata_directory: str | None = None,
) -> str:
    """uv の workspace の一員を editable で入れる時も、作業木の外の target で組むため。"""
    with cargo_target_dir():
        return maturin.build_editable(wheel_directory, config_settings, metadata_directory)


def build_sdist(sdist_directory: str, config_settings: ConfigSettings | None = None) -> str:
    """sdist を組む時も、maturin が cargo に作らせる target を作業木の外に置くため。"""
    with cargo_target_dir():
        return maturin.build_sdist(sdist_directory, config_settings)


get_requires_for_build_wheel = maturin.get_requires_for_build_wheel
get_requires_for_build_editable = maturin.get_requires_for_build_editable
get_requires_for_build_sdist = maturin.get_requires_for_build_sdist
prepare_metadata_for_build_wheel = maturin.prepare_metadata_for_build_wheel
prepare_metadata_for_build_editable = maturin.prepare_metadata_for_build_editable


def main(command: list[str]) -> int:
    """命令を、build の口と同じ扱いの target の中で走らせ、その終了 code を返す。"""
    with cargo_target_dir():
        return subprocess.run(command, check=False).returncode


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
