"""macro が変わった Hy の module の古い bytecode を使わない(agora-redesign #1292・親 #1290)。

Hy の module の bytecode は source だけでなく、展開に使った macro にも依る。Python の .pyc の有効判定は
source の更新時刻と大きさ(または source の hash)しか見ないので、macro の定義(doeff-hy の macros.hy など)を
変えても、使う側の module は古い展開のまま動く(agora-redesign #1003 の赤)。

この package は Hy に手を入れずに(Hy は改めず、回避は doeff の側に置く — 利用者の決定 2026-09-29)、
Python 標準の ``importlib.machinery.SourceFileLoader`` の 2 つの口を包んで同じ正しさを出す:

- ``source_to_code``(compile の口): Hy の module を compile した直後に、その展開が実際に使った macro(提供元の module 名・
  macro 名・macro の関数の code の閉包の digest)の記録を、code object の定数の末尾に 1 つ足す。使った macro は、展開の間だけ
  module の macro の表を引いた名を覚える表に替えて拾う(``macro_recording`` — 読みの口と import の外の compile の口が開く)。
  digest は閉包の関数の code の中身を、その展開で実行された関数(展開の間に :mod:`doeff_hy_bytecode_guard._called_code` が拾う)と
  値を作った関数に絞って覆う(展開で呼ばれない method の変更で使い手が冷えない — agora-redesign #3769 の子)。
  .pyc は標準の形のまま(隣に別の file を書かない)。
  その前に code の Hy の gensym の名を module の中の順の通し番号へ振り直す(``records.canonical_gensyms`` —
  同じ source の code を compile の順・process・thread に依らず同じにする・agora-redesign #3667)。
- ``get_code``(読みの口): .pyc から読んだ Hy の module の code に載った記録の macro を、提供元の module 名から今の環境で
  引き直して digest を比べ、1 つでも変わっていれば source から compile し直して .pyc を書き直す(記録は file の path を持たない
  ので、別の木から引き継いだ .pyc も今の木の macro で照らす — agora-redesign #2598)。使っていない macro・行番号だけの変更では
  compile し直さない。

import の外で bytecode を前もって作る道具は :func:`source_to_code_as_import` で compile する(import と同じく module を
置いた中で compile し、記録を付ける — 記録の無い .pyc は読みの口が compile し直す)。前の木から引き継いだ .pyc を
焼き直すかは :func:`bytecode_is_current`(読みの口と同じ照らし方)で決める。

同じ保存先(code_store)で、dataclass が型ごとに作る関数の source の compile も中身で覚える(:mod:`doeff_hy_bytecode_guard._dataclass_code`
— compile は文と Python の版だけで決まるので照らしは要らない。読み込みの固定費の大半が型ごとの compile だった・card ki-e77d44aa61e5)。
dataclasses は読み込み済みの時だけ包む(起動時の :func:`install` と Hy の source の初めての読み)。
同じ module の本体の実行の口の包みで、一番外の読み込みの間だけ GC の閾値を上げる(:mod:`doeff_hy_bytecode_guard._import_gc` —
読み込みが heap を育てるたびに heap 全体を掃いていた・card ki-de9d3593bbde)。
読みの口は Hy の module の code を返す前に、doeff-hy の macro を require し忘れた・import で入れた誤りを見て、書き足す 1 行を
警告で言う(:mod:`doeff_hy_bytecode_guard._macro_hints` — 文は Python が出し通る frame に doeff の code が無いので、実行の前に言う・
card ki-909e814cc835)。

入れる所は venv の起動時(doeff-hy が配る ``doeff_hy_bytecode_guard.pth``)と ``import doeff_hy`` の 2 か所。
どちらも :func:`install` を呼ぶだけで、何度呼んでも 1 度しか包まない。Hy の import の前でも後でも効く
(前なら Hy の ``source_to_code`` の下に、後なら上に入る — どちらでも compile の直後に一覧を足せる)。

この package は venv の全ての Python の起動時に読まれるので、ここと :mod:`doeff_hy_bytecode_guard._loader_hooks` は
標準 library の軽い module しか import しない。記録の形(hashlib・marshal)は :mod:`doeff_hy_bytecode_guard._records`、
使った macro の拾い方と digest の作り方と照らし方(dis)は :mod:`doeff_hy_bytecode_guard._macro_use` にあり、Hy の module を
初めて読む時にだけ import する。
"""

from importlib import import_module

from doeff_hy_bytecode_guard._expansion import TYPE_CHECK_EXPANSION as TYPE_CHECK_EXPANSION
from doeff_hy_bytecode_guard._import_scope import SharedScope as SharedScope
from doeff_hy_bytecode_guard._loader_hooks import bytecode_is_current as bytecode_is_current
from doeff_hy_bytecode_guard._loader_hooks import file_sha256 as file_sha256
from doeff_hy_bytecode_guard._loader_hooks import gensym_renaming as gensym_renaming
from doeff_hy_bytecode_guard._loader_hooks import install as install
from doeff_hy_bytecode_guard._loader_hooks import installed as installed
from doeff_hy_bytecode_guard._loader_hooks import loaded_record as loaded_record
from doeff_hy_bytecode_guard._loader_hooks import macro_recording as macro_recording
from doeff_hy_bytecode_guard._loader_hooks import record_fingerprint as record_fingerprint
from doeff_hy_bytecode_guard._loader_hooks import record_from_json as record_from_json
from doeff_hy_bytecode_guard._loader_hooks import record_is_current_here as record_is_current_here
from doeff_hy_bytecode_guard._loader_hooks import record_to_json as record_to_json
from doeff_hy_bytecode_guard._loader_hooks import (
    source_to_code_as_import as source_to_code_as_import,
)
from doeff_hy_bytecode_guard._loader_hooks import value_reference as value_reference

TYPE_CHECKING = False  # typing と records を起動時に読まない — 型検査器はこの名の分岐を真として読む

if TYPE_CHECKING:
    # 記録の型(注記に使う公開の名 — 値は macro_recording・record_from_json・loaded_record が作る)。
    from doeff_hy_bytecode_guard._macro_hints import HyMacroUseWarning as HyMacroUseWarning
    from doeff_hy_bytecode_guard._records import MacroRecord as MacroRecord

# Names of the submodules behind `_` (ADR-DOE-API-001 R5), read on first use: the root does not import
# them while it loads. `X as X` shows each name to type checkers. A root that already has `__all__`
# lists the names there too; the tool does not add `__all__` to a root without one (DOEFF021).
# MacroRecord は前から入口の型だけの名 — 入口に __getattr__ が在ると目録が TYPE_CHECKING の名を数えるので、実行の時も読める様に
# ここへ置く(records は起動時に読まない — 名を初めて引いた時に読む)。
_LAZY_EXPORTS: dict[str, str] = {
    "HyMacroUseWarning": "doeff_hy_bytecode_guard._macro_hints",
    "MacroRecord": "doeff_hy_bytecode_guard._records",
}


def __getattr__(name: str) -> object:
    module_name = _LAZY_EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module_name), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted({*globals(), *_LAZY_EXPORTS})
