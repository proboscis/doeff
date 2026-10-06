"""native の wheel(Rust の package の組み済みの wheel)を、build の口の保存先から用意する時の約束の定義点(2026-10-06・#3860)。

Rust の部品を組む・引く入口は doeff の build の口 tools/doeff_cargo_backend.py の 1 つ(ADR-DOE-BUILD-001)。worker は自前の鍵も置き場も
持たず、`uv build --wheel` でその口を通るだけ: 口は source の中身の鍵で保存先(env DOEFF_WHEEL_CACHE)を引き、無い時だけ組んで置き、
保存先の中の wheel と組んだかを env DOEFF_WHEEL_REPORT の file に 1 行の JSON で書く。この module は、その呼びの環境(uv の子へ足す
変数)・同じ package を同時に組まないための錠・報告の行の読みを定義する。

使い手は 2 つで、どちらもここだけを読む(綴りの複製を作らない):
  - 実行環境の準備(worker): worker/protocol/env_translation の EnsureNativeWheel・掃除 = worker/protocol/env_store の sweep-leftovers
    (保存先 state/wheels の 7 日使われない dir — 口が使うたびに印 .used を置き換えて dir の時刻を進める)。
  - 起動の script(deploy/boot.sh の自己起動): worker/entry/boot_wheel が、自分の root の doeff-vm の wheel を同じ保存先から用意する。

標準ライブラリだけを import する(doeff・hy・doeff_cluster の Hy の module を import しない): 起動の script は doeff-vm を入れる前の venv の
python でこの module を読む。

置き場(state = worker の state dir — 起動の script では $WORK_DIR/state):
  state/wheels/<package>-<鍵>/<wheel>   build の口の保存先(並びと鍵と .used は口の物)
  state/locks/wheel-<package>           package ごとの錠(fcntl.flock の排他 — worker の AcquireLock と同じ錠)
"""

import json
import platform as host_platform
import posixpath
from dataclasses import dataclass

# 層 core の文脈と役の名乗り(DOEFF104)— 隣の runtime_env_rules.hy と同じ。
MODULE_TAGS = {"context": "doeff-cluster", "role": "judgment"}

# 実行環境の鍵 env-key の長さ(sha256 の 16 進の頭の桁数)。
ENV_KEY_LENGTH = 24
# doeff 自身の native の package(Rust の VM)と、その source の dir(doeff の root からの相対 path — `uv build --wheel` に渡す)。
DOEFF_VM_PACKAGE = "doeff-vm"
DOEFF_VM_SOURCE = "packages/doeff-vm"
# uv の子に継がせない呼び手の環境変数の型(呼び手の venv と uv・Python の設定 — fnmatch の型)。
UV_DROP = ("UV_*", "PYTHON*", "VIRTUAL_ENV")
# build の口が読む env(保存先の dir)と書く env(報告の file)— 名の綴りは口(tools/doeff_cargo_backend.py の WHEEL_CACHE_ENV・
# WHEEL_REPORT_ENV)と同じ。
WHEEL_CACHE_ENV = "DOEFF_WHEEL_CACHE"
WHEEL_REPORT_ENV = "DOEFF_WHEEL_REPORT"


def current_platform() -> str:
    """この機体の platform の名(env の鍵の材料 — 例 linux-x86_64)。root の中の native の wheel と venv は platform ごとに違う。
    読むのはこの process の機体の固定の事実(process の間は変わらない)。"""
    return f"{host_platform.system().lower()}-{host_platform.machine().lower()}"


def wheels_root(state_dir: str) -> str:
    """build の口の保存先の dir(口へ DOEFF_WHEEL_CACHE で渡す・掃除が歩く)。"""
    return posixpath.join(state_dir, "wheels")


def wheel_lock(state_dir: str, package: str) -> str:
    """package ごとの錠の file(同じ package を同時に組まない — 保存先への置き換え自体は口が一時の名から行う)。"""
    return posixpath.join(state_dir, "locks", f"wheel-{package}")


@dataclass(frozen=True)
class UvVariable:
    """uv の子の環境へ足す変数 1 つ(name = 環境変数の名・value = 値)。"""

    name: str
    value: str


def uv_environment(state_dir: str, uv_cache: str) -> tuple[UvVariable, ...]:
    """uv の子の環境へ足す変数: 共有の cache は uv_cache の dir(値は起動の script の DOEFF_UV_CACHE_DIR の 1 か所 — 既定は state dir の
    下の uv-cache)、Python と build の口の保存先は state dir の下に置く(呼び手の venv と設定を外すのは UV_DROP)。"""
    return (
        UvVariable("UV_CACHE_DIR", uv_cache),
        UvVariable("UV_PYTHON_INSTALL_DIR", posixpath.join(state_dir, "python")),
        UvVariable("UV_NO_PROGRESS", "1"),
        UvVariable(WHEEL_CACHE_ENV, wheels_root(state_dir)),
    )


@dataclass(frozen=True)
class StoredWheel:
    """build の口が報告した wheel: path = 保存先の中の wheel の file・built = その呼びが組んだ(保存先に無かった)。"""

    path: str
    built: bool


def stored_wheel_of(text: str, project: str) -> "StoredWheel | str":
    """build の口の報告の file の中身(1 行 1 つの JSON)から、package project の最後の行を StoredWheel に読むため。行が無い・形が違う
    時は理由の文(呼び手は native の build の失敗として名指す — 報告を書かない口は通らない)。"""
    found: StoredWheel | str = f"build の口の報告に {project} の行が無い(口が保存先を通っていない)"
    for line in text.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            return f"build の口の報告の行が JSON でない: {line[:200]}"
        match row:
            case {"project": str() as name, "wheel": str() as wheel, "built": bool() as built} if name == project:
                found = StoredWheel(path=wheel, built=built)
            case {"project": str(), "wheel": str(), "built": bool()}:
                pass
            case _:
                return f"build の口の報告の行の形が違う: {line[:200]}"
    return found
