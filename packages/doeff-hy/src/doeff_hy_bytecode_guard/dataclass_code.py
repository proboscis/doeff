"""dataclass が型ごとに作る関数の source(``__create_fn__``)の compile を、source の中身で引く保存先(:mod:`code_store`)に覚える
(card ki-e77d44aa61e5)。

dataclasses は型ごとに関数(``__init__``・``__repr__``・``__eq__``・frozen の ``__setattr__`` など)の source の文を作り、``exec`` に
渡して中で compile する。検の読み込み(agora-controllers の test_emulated_screen.hy・3798 型)では、dataclass の作成の費用のほぼ
全部がこの compile で、違う文は 769 通りだった。compile の結果は文・Python の版の印(cache_tag)・最適化の段だけで決まるので、
覚えた code は今 compile した物と同じ(古くなる物が無い — Hy の source の code と違い、macro の照らしは要らない)。

引き方は 2 段: 同じ process の中の覚え(同じ文を compile し直さない)と、process をまたぐ保存先の entry(書き方・時刻の作法・
壊れた entry の扱いは :mod:`code_store` の 1 か所)。保存先を使わない設定(``off``)では process の中の覚えだけ。

包みは dataclasses の module の名 ``exec`` を置き換える(dataclasses は名 exec を module の名前空間から引き、無ければ builtins)。
関数の source でない呼び出しは素の exec へそのまま渡す。dataclasses を自分から import しない(起動を重くしない)— 包むのは
読み込み済みの時だけ(:func:`doeff_hy_bytecode_guard.loader_hooks.install` と Hy の source の初めての読み)。
"""

import builtins
import sys
from collections.abc import Mapping
from types import CellType, CodeType, ModuleType

from doeff_hy_bytecode_guard import code_store

#: entry の鍵の印(形を変えたら末尾の番号を上げる — 古い鍵の entry は当たらなくなる)。
DATACLASS_TAG = "doeff-hy/dataclass-code/1"

#: dataclasses が関数の source の文に付ける外側の関数の名(この名を持つ文だけを覚える)。
_FUNCTION_SOURCE_MARK = "def __create_fn__("

#: この process で引いた・compile した code(文 → code)。
_CODES: dict[str, CodeType] = {}


def install(dataclasses_module: ModuleType) -> None:
    """読み込み済みの dataclasses の名 exec を、関数の source の code を覚える exec に置き換える(何度呼んでも 1 度だけ)。"""
    vars(dataclasses_module)["exec"] = remembering_exec


def remembering_exec(
    source: str | bytes | CodeType,
    globals_: dict[str, object] | None = None,
    locals_: Mapping[str, object] | None = None,
    /,
    *,
    closure: tuple[CellType, ...] | None = None,
) -> None:
    """dataclasses の exec の代わり — 関数の source の文は覚えた code を exec し、それ以外は素の exec へ渡す。"""
    code = _code_of(source) if isinstance(source, str) and source.startswith(_FUNCTION_SOURCE_MARK) else source
    if closure is None:
        builtins.exec(code, globals_, locals_)
    else:
        builtins.exec(code, globals_, locals_, closure=closure)


def _code_of(source: str) -> CodeType:
    """関数の source の文の code — process の中の覚え・保存先の entry・compile の順に引く(compile したら保存先へ足す)。"""
    remembered = _CODES.get(source)
    if remembered is not None:
        return remembered
    path = _entry_path(source)
    stored = None if path is None else code_store.stored_code(path)
    # dataclasses が文を exec に渡した時と同じ compile(file 名 <string>・この module の __future__ を継がない)。
    code = stored if stored is not None else compile(source, "<string>", "exec", dont_inherit=True)
    if stored is None and path is not None and (not sys.dont_write_bytecode or code_store.store_is_named()):
        import marshal  # 保存先へ足す時だけ読む

        # 書けない保存先では足さない — 保存先は速さのためだけで、読み込みのたびに書けない理由を出さない(loader_hooks と同じ作法)。
        code_store.write_entry(path, marshal.dumps(code))
    _CODES[source] = code
    return code


def _entry_path(source: str) -> str | None:
    """関数の source の文の entry の path(保存先を使わない設定なら None)— 鍵は印・Python の版の印・最適化の段・文の中身。"""
    store = code_store.store_dir()
    if store is None:
        return None
    parts = (DATACLASS_TAG, sys.implementation.cache_tag or "", str(sys.flags.optimize))
    return code_store.entry_path(store, code_store.content_key(parts, source.encode("utf-8")), code_store.CODE_SUFFIX)
