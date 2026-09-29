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
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterator, Mapping
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import TypeAlias

import maturin

TARGET_ENV = "CARGO_TARGET_DIR"
TEMP_PREFIX = "doeff-cargo-target-"

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
    """uv sync・uv build が wheel を求めた時に、作業木の外の target で組むため。"""
    with cargo_target_dir():
        return maturin.build_wheel(wheel_directory, config_settings, metadata_directory)


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
