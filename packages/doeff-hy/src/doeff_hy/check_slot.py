"""機体の検の枠 — doeff-hy-check の 1 回の実行が、同じ機体(Pod)で走る検(pytest・code-quality・doeff-linter・long-command の
task)と共有する枠を、展開と pyright の前に 1 つ取る(agora-redesign card ki-26ebd3f636e0・親 ki-aabe38bf85ef)。

なぜ要るか: doeff-hy-check は 1 本で展開の子 4 本と pyright を起こし、RSS 約 10 GiB を使う。agora-controllers の手で測る入口
(scripts/hy_type_check.hy の gate)は枠を取るようになったが(card ki-931f88d7815b)、会話が直に起動する doeff-hy-check
(`uv run doeff-hy-check …`・`python -m doeff_hy.static_check …`)は枠の外で走っていた。撃つ道ごとに包まず、入口の main の 1 点で取る
(doeff-linter の packages/doeff-linter/src/check_slot.rs と同じ考え)。

形: 枠の正本は agora の道具立ての dotfiles の `agent/tests/machine_check_slots.py`(本数・置き場・待ちの上限は隣の
machine_check_slots.json の 1 つが持つ)。この module は値を持たず、正本を宿の home の path で読んで read_policy と take を呼ぶだけ
(code-quality の quality/machine_slot.py・agora-controllers の scripts/check_slot.hy と同じ形)。
  - 正本の無い宿(dotfiles を置かない機体 — doeff だけを clone した機体)は黙って素通し。
  - 正本・宣言を読めない時と、正本の口が揃わない・呼び方が合わない時(正本は dotfiles の先端を追い、doeff は使い手の pin で固定
    される)は 1 行名乗って素通し — 枠の壊れで検を走らせなくしない(正本の pytest の受付と同じ)。
  - 枠を持つ process の子孫(pytest の検が呼ぶ main・gate が起こす doeff-hy-check)は 2 つ目を取らない。判定は正本の take
    (/proc の祖先の列・/proc の無い機体では正本の HOLDER_ENV の印)。
  - 待ちは正本が黙らずに名乗る(`check-slot: … 待つ — 待ち N 秒 / 上限 … 保持 pid …`)。上限を越えたら SlotExpired — main は展開も
    pyright も起こさずに「走れなかった」(exit 2)で終わる(合格にも赤にもしない)。
  - 持つ間、子孫への印(正本の HOLDER_ENV)は env に置かない。子孫(展開の子・pyright・pyright が起こす python)は枠を取らず、置くには
    os.environ を書く(doeff-linter の DOEFF004)。正本へ渡す env は、印の今の値だけの写し(読みは doeff_hy.env_places)。
env で正本の path や値を替える口は作らない(口が在ると、替えた会話の検だけが別の数で数え、枠が割れる — 正本の頭の註)。
"""

import importlib.util
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Protocol, TextIO

from doeff_hy.env_places import slot_holder_setting

#: 正本の置き場(宿の home からの相対 — doeff-linter の check_slot.rs・code-quality の quality/machine_slot.py と同じ)。
CANON_UNDER_HOME = Path("dotfiles", "agent", "tests", "machine_check_slots.py")
#: 正本を読んだ module の名の頭(dotfiles の受付 broad_run_admission の _check_slots・code-quality と同じ鍵 — pytest の中では
#: 受付が読んだ個体を使い、同じ process で 2 つ目の個体を作らない)。
MODULE_KEY_PREFIX = "ai_machine_check_slots@"
#: 名乗りの行の頭(正本の MARK と同じ)。
MARK = "check-slot:"
#: 正本の公開の口のうち、この module が引く名(揃わない正本 — dotfiles の先端で口が変わった — は素通し)。
CANON_NAMES = ("read_policy", "take", "PolicyUnreadable", "SlotWaitExpired", "HOLDER_ENV")


class Holding(Protocol):
    """正本の take が返す持ち物(正本の Holding)— 解く口だけを読む。"""

    def release(self) -> None: ...


@dataclass(frozen=True)
class SlotHeld:
    """検を走らせてよい。holding = 取った枠・素通し(正本が無い・読めない・祖先が持つ)では None。"""

    holding: Holding | None

    def release(self) -> None:
        """取った枠を解く(素通しでは何もしない・2 度目も何もしない — 正本の release が 2 度目を無視する)。"""
        if self.holding is not None:
            self.holding.release()


@dataclass(frozen=True)
class SlotExpired:
    """枠の待ちが正本の宣言の上限を越えた — 検は未実行(赤でも合格でもない)。line = 正本の名乗りの 1 行。"""

    line: str


def slot_label(root: Path, conversation: str | None) -> str:
    """保持者の名乗り(純粋)— 検める根の名(作業木の名)と、在れば会話の id(正本の pytest・命令の包みの名乗りと同じ形)。"""
    place = root.name or str(root)
    return f"doeff-hy-check {place}" + (f" ({conversation})" if conversation else "")


def _named(out: TextIO, text: str) -> None:
    """素通しの理由を名乗りの 1 行で out へ書く(黙って枠の外で走らない)。"""
    out.write(f"{MARK} {text}\n")
    out.flush()


def canon_module(home: Path, out: TextIO) -> ModuleType | None:
    """正本を宿の path で読む。無い宿は None(素通し)。読めなければ 1 行名乗って None。同じ file を読んだ module が在ればそれ
    (pytest の中では受付の正本が既に読んでいる)。"""
    path = home / CANON_UNDER_HOME
    if not path.is_file():
        return None
    real = os.path.realpath(path)
    key = MODULE_KEY_PREFIX + real
    loaded = sys.modules.get(key)
    if loaded is not None:
        return loaded
    spec = importlib.util.spec_from_file_location(key, real)
    if spec is None or spec.loader is None:
        _named(out, f"枠の正本 {real} を読めないので枠を取らずに走る")
        return None
    module = importlib.util.module_from_spec(spec)
    sys.modules[key] = module  # 正本の dataclass が sys.modules を読む
    try:
        spec.loader.exec_module(module)
    except Exception as error:  # 正本の壊れ(構文・import)は名乗って素通し — 検を走らせなくしない
        sys.modules.pop(key, None)
        _named(
            out, f"枠の正本 {real} を読めないので枠を取らずに走る({type(error).__name__}: {error})"
        )
        return None
    return module


def take_slot(home: Path, label: str, out: TextIO) -> SlotHeld | SlotExpired:
    """同じ機体で走る検と共有する枠を 1 つ取る(埋まっていれば正本が保持者を名乗って待つ)。home = 正本を探す宿の home・
    label = 保持者の名乗り・out = 名乗りの行の行き先。方策は正本の read_policy の答えを中を読まずに take へ渡す — 待ちの上限を
    縮める・延ばす判断をここに置かない。答え = SlotHeld(release を呼ぶまで枠を持つ)か、待ちの上限を越えた SlotExpired。"""
    module = canon_module(home, out)
    if module is None:
        return SlotHeld(None)
    missing = [name for name in CANON_NAMES if not hasattr(module, name)]
    if missing:
        _named(
            out,
            f"枠の正本 {module.__file__} の口が揃わない({'・'.join(missing)})ので枠を取らずに走る",
        )
        return SlotHeld(None)
    marker_name: str = module.HOLDER_ENV
    marker = slot_holder_setting(marker_name)
    environ = {} if marker is None else {marker_name: marker}
    try:
        return SlotHeld(module.take(module.read_policy(), label, environ, out))
    except module.PolicyUnreadable as error:
        _named(out, f"宣言を読めないので枠を取らずに走る — {error}")
    except module.SlotWaitExpired as error:
        return SlotExpired(str(error))
    except TypeError as error:  # 正本の口の呼び方が doeff の pin の版と食い違う — 検を止めない
        _named(out, f"枠の正本の呼び方が合わないので枠を取らずに走る({error})")
    return SlotHeld(None)
