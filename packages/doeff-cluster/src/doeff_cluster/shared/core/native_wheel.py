"""native の wheel(Rust の package の組み済みの wheel)を、build の口の保存先から用意する時の約束の定義点(2026-10-06・#3860)。

Rust の部品を組む・引く入口は doeff の build の口 tools/doeff_cargo_backend.py の 1 つ(ADR-DOE-BUILD-001)。worker は自前の鍵も置き場も
持たず、`uv build --wheel` でその口を通るだけ: 口は source の中身の鍵で保存先(env DOEFF_WHEEL_CACHE)を引き、無い時だけ組んで置き、
uv build が --out-dir に出した wheel をそのまま使う(どの版の口でも出る)。口が env DOEFF_WHEEL_REPORT の file に書く「組んだか」の
1 行は観測だけで、報告の約束の無い版の口(宣言の古い doeff の root)は書かない — その時は「報告が無い」(NotReported)として名指す
(組んだ・使ったのどちらにも埋めない・報告が無いことを準備の失敗にしない)。この module は、その呼びの環境(uv の子へ足す変数)・
--out-dir の置き場・同じ package を同時に組まないための錠・報告の行の読みを定義する。

使い手は 2 つで、どちらもここだけを読む(綴りの複製を作らない):
  - 実行環境の準備(worker): worker/protocol/env_translation の EnsureNativeWheel・掃除 = worker/protocol/env_store の sweep-leftovers
    (保存先 state/wheels の 7 日使われない dir — 口が使うたびに印 .used を置き換えて dir の時刻を進める)。
  - 起動の script(deploy/boot.sh の自己起動): worker/entry/boot_wheel が、自分の root の doeff-vm の wheel を同じ保存先から用意する。

標準ライブラリだけを import する(doeff・hy・doeff_cluster の Hy の module を import しない): 起動の script は doeff-vm を入れる前の venv の
python でこの module を読む。

置き場(state = worker の state dir — 起動の script では $WORK_DIR/state・root = 準備する root):
  state/wheels/<package>-<鍵>/<wheel>   build の口の保存先(並びと鍵と .used は口の物)
  state/locks/wheel-<package>           package ごとの錠(fcntl.flock の排他 — worker の AcquireLock と同じ錠)
  <root>/.native-wheels/<package>/      uv build の --out-dir(venv へ入れる wheel — root と一緒に消える)
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


def wheel_out_dir(root: str, package: str) -> str:
    """root の package の wheel を uv build の --out-dir に出させる dir(venv へ入れる wheel の在りか — root と一緒に消える)。"""
    return posixpath.join(root, ".native-wheels", package)


@dataclass(frozen=True)
class NotReported:
    """build の口が package の報告の行を書かなかった(報告の約束の無い版の口 — 組んだか保存先から引いたかは分からない)。"""


def reported_built(text: str, project: str) -> "bool | NotReported | str":
    """build の口の報告の file の中身(1 行 1 つの JSON — 無い file は空文字で渡す)から、package project の最後の行の「組んだか」を
    読むため(True = 組んだ・False = 保存先から引いた)。行が無ければ NotReported。行が JSON でない・形が違う時は理由の文(報告を
    書く版の口の約束の破れ — 呼び手は native の build の失敗として名指す)。"""
    found: bool | NotReported = NotReported()
    for line in text.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            return f"build の口の報告の行が JSON でない: {line[:200]}"
        match row:
            case {"project": str() as name, "wheel": str(), "built": bool() as built} if name == project:
                found = built
            case {"project": str(), "wheel": str(), "built": bool()}:
                pass
            case _:
                return f"build の口の報告の行の形が違う: {line[:200]}"
    return found
