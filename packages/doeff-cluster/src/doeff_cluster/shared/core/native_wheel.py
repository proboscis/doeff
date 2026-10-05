"""native の wheel(Rust の package の組み済みの wheel)の鍵・置き場・組む時の uv の環境の定義点(2026-10-06)。

使い手は 2 つで、どちらもここだけを読む(綴りの複製を作らない):
  - 実行環境の準備(worker): 鍵 = shared/core/runtime_env_rules の native-key(本体は native_key)・置き場と錠 = worker/protocol/
    env_translation の EnsureNativeWheel・掃除 = worker/protocol/env_store の sweep-leftovers。
  - 起動の script(deploy/boot.sh の自己起動): worker/entry/boot_wheel が、自分の root の doeff-vm の wheel を同じ鍵・同じ置き場・同じ錠で
    使う(無ければ組んで置く)。2026-10-06 00:02〜00:05 の版上げで、起動の uv sync が doeff-vm を source から 94 秒かけて組み、同じ PVC に
    実行環境の準備が置いた同じ中身の wheel が在るのに使わなかった(その間 利用者の画面が切れた)。

標準ライブラリだけを import する(doeff・hy・doeff_cluster の Hy の module を import しない): 起動の script は doeff-vm を入れる前の venv の
python でこの module を読む。

置き場(state = worker の state dir — 起動の script では $WORK_DIR/state):
  state/wheels/<package>-<鍵>/<wheel>   組み済みの wheel(dir の中の名の順の先頭の .whl)と使った印 .used
  state/wheels/.<package>-<鍵>.tmp/     組んでいる途中(組み終えてから名を変えて置く — 書きかけを読ませない)
  state/locks/wheel-<鍵>                 鍵ごとの錠(fcntl.flock の排他 — worker の AcquireLock と同じ錠)
"""

import hashlib
import json
import platform as host_platform
import posixpath
from dataclasses import dataclass

# 層 core の文脈と役の名乗り(DOEFF104)— 隣の runtime_env_rules.hy と同じ。
MODULE_TAGS = {"context": "doeff-cluster", "role": "judgment"}

# 鍵の長さ(sha256 の 16 進の頭の桁数)— 実行環境の鍵 env-key と native の wheel の鍵 native_key の両方。
ENV_KEY_LENGTH = 24
# native の wheel の dir の使った印(掃除は dir の mtime を読む — 名を変えて置く書き直しで dir の mtime が進む)。
WHEEL_USED = ".used"
# doeff 自身の native の package(Rust の VM)と、その wheel の中身を決める doeff の中の dir(tree hash を鍵の材料にする順)。
DOEFF_VM_PACKAGE = "doeff-vm"
DOEFF_VM_PATHS = ("packages/doeff-vm", "packages/doeff-vm-core")
# uv の子に継がせない呼び手の環境変数の型(呼び手の venv と uv・Python の設定 — fnmatch の型)。
UV_DROP = ("UV_*", "PYTHON*", "VIRTUAL_ENV")


def current_platform() -> str:
    """この機体の platform の名(env の鍵と native の wheel の鍵の材料 — 例 linux-x86_64)。root の中の native の wheel と venv は
    platform ごとに違う。読むのはこの process の機体の固定の事実(process の間は変わらない)。"""
    return f"{host_platform.system().lower()}-{host_platform.machine().lower()}"


def native_key(package: str, paths: tuple[str, ...], tree_hashes: tuple[str, ...], python: str, platform: str) -> str:
    """native の wheel の鍵(定義点はここ 1 つ)= package・wheel の中身を決める dir ごとの git の tree hash・Python・platform の正規化した
    JSON の sha256 の頭 ENV_KEY_LENGTH 桁。tree_hashes は paths と同じ順。"""
    if len(tree_hashes) != len(paths):
        raise ValueError(f"tree hash の数 {len(tree_hashes)} が dir の数 {len(paths)} と違う({package})")
    material = {
        "package": package,
        "trees": [[path, tree] for path, tree in zip(paths, tree_hashes, strict=True)],
        "python": python,
        "platform": platform,
    }
    text = json.dumps(material, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:ENV_KEY_LENGTH]


def wheels_root(state_dir: str) -> str:
    """組み済みの wheel の dir を並べる dir(掃除が歩く)。"""
    return posixpath.join(state_dir, "wheels")


def wheel_dir(state_dir: str, package: str, key: str) -> str:
    """鍵の wheel を置く dir。"""
    return posixpath.join(wheels_root(state_dir), f"{package}-{key}")


def wheel_lock(state_dir: str, key: str) -> str:
    """鍵ごとの錠の file(組む・使った印を置く間の排他)。"""
    return posixpath.join(state_dir, "locks", f"wheel-{key}")


def wheel_tmp(target: str) -> str:
    """target(wheel_dir)へ組んでいる途中の dir — 同じ親の .<名>.tmp(組み終えてから名を変えて置く)。"""
    return posixpath.join(posixpath.dirname(target), f".{posixpath.basename(target)}.tmp")


@dataclass(frozen=True)
class UvVariable:
    """uv の子の環境へ足す変数 1 つ(name = 環境変数の名・value = 値)。"""

    name: str
    value: str


def uv_environment(state_dir: str) -> tuple[UvVariable, ...]:
    """uv の子の環境へ足す変数: 共有の cache と Python を state dir の下に置く(呼び手の venv と設定を外すのは UV_DROP)。"""
    return (
        UvVariable("UV_CACHE_DIR", posixpath.join(state_dir, "uv-cache")),
        UvVariable("UV_PYTHON_INSTALL_DIR", posixpath.join(state_dir, "python")),
        UvVariable("UV_NO_PROGRESS", "1"),
    )
