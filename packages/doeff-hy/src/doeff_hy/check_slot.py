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
  - 正本・宣言を読めない時と、正本の口が揃わない時・正本の take が中で落ちた時(正本は dotfiles の先端を追い、doeff は使い手の
    pin で固定される)は 1 行名乗って素通し — 枠の壊れで検を走らせなくしない(正本の pytest の受付と同じ)。正本の中の失敗を
    traceback のまま抜けさせると、main の終了 1(= 新しい赤)と区別できない(card ki-26ebd3f636e0 の盲検 A の副次の実測)。
  - 方策(read_policy の答え)と持ち物(take の答え)は開かない — 正本の公開の口(take・process_width)へそのまま渡すだけで、
    欄の名を読まず、作り直さず、別の名へ写さない(待ちの上限を縮める・延ばす判断と、方策の欄の名をここに置かない)。持ち物は解く口
    (release)だけを SlotHeld に預ける。形は tests/test_check_slot_opacity.py の AST の検が守る(入る向きも出る向きも)。
  - 1 枠の中で並べてよい process の数は、正本の公開の口 process_width に方策・持ち物・環境を渡して受ける(SlotHeld.width)。入れ子の
    規則(祖先の枠の内側は外側が配った幅を残す)も env の名も正本の中にあり、ここへ写さない。口が無い正本(古い dotfiles)では
    枠は取り、幅は None — 呼び手(static_check)が宣言の無い機体の既定で走る(1 行名乗る)。幅の口が落ちた時も取った枠は持ち続ける。
  - 枠を持つ process の子孫(pytest の検が呼ぶ main・gate が起こす doeff-hy-check)は 2 つ目を取らない。判定は正本の take
    (/proc の祖先の列・/proc の無い機体では正本の HOLDER_ENV の印)。
  - 待ちは正本が黙らずに名乗る(`check-slot: … 待つ — 待ち N 秒 / 上限 … 保持 pid …`)。上限を越えたら SlotExpired — main は展開も
    pyright も起こさずに「走れなかった」(exit 2)で終わる(合格にも赤にもしない)。
  - 正本へ渡す env は、名指しで 1 つずつ読む写し(doeff_hy.env_places.EnvironmentByName)— 正本の take がどの名を読むか
    (今は保持者の印・明日は会話の id かもしれない)を doeff は知らない。名を 1 つに絞って写すと、正本が新しい名を読んだ時に
    doeff だけが黙って従わない(盲検 A の反例)。
  - 持つ間、子孫への印(正本の HOLDER_ENV)は env に置かない。子孫(展開の子・pyright・pyright が起こす python)は枠を取らず、置くには
    os.environ を書く(doeff-linter の DOEFF004)。Linux では正本が /proc の祖先の列で子孫を判じる。
env で正本の path や値を替える口は作らない(口が在ると、替えた会話の検だけが別の数で数え、枠が割れる — 正本の頭の註)。
"""

import importlib.util
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import TextIO

from doeff_hy.env_places import EnvironmentByName

#: 正本の置き場(宿の home からの相対 — doeff-linter の check_slot.rs・code-quality の quality/machine_slot.py と同じ)。
CANON_UNDER_HOME = Path("dotfiles", "agent", "tests", "machine_check_slots.py")
#: 正本を読んだ module の名の頭(dotfiles の受付 broad_run_admission の _check_slots・code-quality と同じ鍵 — pytest の中では
#: 受付が読んだ個体を使い、同じ process で 2 つ目の個体を作らない)。
MODULE_KEY_PREFIX = "ai_machine_check_slots@"
#: 名乗りの行の頭(正本の MARK と同じ)。
MARK = "check-slot:"
#: 正本の公開の口のうち、揃わなければ枠を取らずに素通しにする関数(必須)・無くても枠は取る関数(任意 — 無い正本では幅が届かない)・
#: 捕まえる例外の名。この module が正本から読んでよい名はこの 3 つの組だけ(tests/test_check_slot_opacity.py が守る)。
CANON_CALLS = ("read_policy", "take")
CANON_OPTIONAL_CALLS = ("process_width",)
CANON_ERRORS = ("PolicyUnreadable", "SlotWaitExpired")


@dataclass(frozen=True)
class SlotHeld:
    """検を走らせてよい。let_go = 取った枠を解く口(素通し・祖先が持つ時は None)・width = 正本が答えた 1 枠の中で並べてよい
    process の数(正本の口が無い・落ちた・素通しでは None — 呼び手が宣言の無い機体の既定で走る)。
    正本の持ち物そのものは預からない — 解く口だけ(持ち物の中身は take_slot の中で閉じる)。"""

    let_go: Callable[[], None] | None = None
    width: int | None = None

    def release(self) -> None:
        """取った枠を解く(素通しでは何もしない・2 度目も何もしない — 正本の release が 2 度目を無視する)。"""
        if self.let_go is not None:
            self.let_go()


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


def unfit_names(module: ModuleType) -> list[str]:
    """正本の口のうち、呼べない関数・例外の class でない名(純粋)。空 = 口が揃う。"""
    found = vars(module)
    return [name for name in CANON_CALLS if not callable(found.get(name))] + [
        name for name in CANON_ERRORS if not _error_class(found.get(name))
    ]


def _error_class(value: object) -> bool:
    return isinstance(value, type) and issubclass(value, BaseException)


def absent_optional(module: ModuleType) -> list[str]:
    """正本の任意の口のうち、呼べない物(純粋)。空 = 全部在る。無くても枠は取る。"""
    found = vars(module)
    return [name for name in CANON_OPTIONAL_CALLS if not callable(found.get(name))]


def take_slot(home: Path, label: str, out: TextIO) -> SlotHeld | SlotExpired:
    """同じ機体で走る検と共有する枠を 1 つ取る(埋まっていれば正本が保持者を名乗って待つ)。home = 正本を探す宿の home・
    label = 保持者の名乗り・out = 名乗りの行の行き先。方策と持ち物は正本の口へそのまま渡し、中を開かない。答え = SlotHeld
    (release を呼ぶまで枠を持つ・width = 正本が答えた並走の数)か、待ちの上限を越えた SlotExpired。"""
    module = canon_module(home, out)
    if module is None:
        return SlotHeld()
    unfit = unfit_names(module)
    if unfit:
        _named(
            out,
            f"枠の正本 {os.path.realpath(home / CANON_UNDER_HOME)} の口が揃わない({'・'.join(unfit)})ので枠を取らずに走る",
        )
        return SlotHeld()
    environ = EnvironmentByName()
    try:
        policy = module.read_policy()
        holding = module.take(policy, label, environ, out)
    except module.PolicyUnreadable as error:
        _named(out, f"宣言を読めないので枠を取らずに走る — {error}")
        return SlotHeld()
    except module.SlotWaitExpired as error:
        return SlotExpired(str(error))
    except Exception as error:  # 正本(別の repo の先端の code)の中の失敗・呼び方の食い違い — 名乗って素通し(頭の註)
        _named(
            out, f"枠の正本の take が落ちたので枠を取らずに走る({type(error).__name__}: {error})"
        )
        return SlotHeld()
    let_go = None if holding is None else holding.release
    width = None
    absent = absent_optional(module)
    if absent:
        _named(out, f"枠の正本に並走の数の口({'・'.join(absent)})が無いので、並走の数は宣言の無い機体の既定で走る")
    else:
        try:  # 幅の口が落ちても、取った枠は持ち続ける(let_go は上で預けた)— take の失敗と同じ囲いに入れない
            width = module.process_width(policy, holding, environ)
        except Exception as error:
            _named(
                out,
                f"枠の正本の process_width が落ちたので、並走の数は宣言の無い機体の既定で走る({type(error).__name__}: {error})",
            )
    return SlotHeld(let_go, _positive(width, out))


def _positive(width: object, out: TextIO) -> int | None:
    """正本が答えた並走の数を、正の整数なら通し、それ以外は 1 行名乗って None にする(口の形の確かめで、数の判断ではない)。"""
    if width is None:
        return None
    if isinstance(width, int) and not isinstance(width, bool) and width >= 1:
        return width
    _named(out, f"枠の正本の process_width の答えが正の整数でない({width!r})ので、並走の数は宣言の無い機体の既定で走る")
    return None
