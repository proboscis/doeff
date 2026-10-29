"""import の compile が展開した木の置き場 — 同じ source を doeff-effect-analyzer が展開し直さないため(card ki-e053a847c515・ki-5c1fc605c17d)。

変わった Hy の module は、日次の各 session が検を集める時の import で compile され(macro の展開が重い — 4,447 行の module で約 30 秒)、
閉じの検の analyzer が同じ source をもう一度展開していた(同じ秒の 2 回目)。import の compile の口(``loader_hooks``)は Hy の口の下で
展開した後の木と、展開が依った macro の記録を受け取るので、その 2 つをここへ置く。analyzer は自分の展開の前にここを引く
(向きは analyzer → doeff-hy のまま — doeff-hy は analyzer を import しない)。

置き場は bytecode の置き場(``code_store`` — 環境変数 ``DOEFF_HY_CODE_STORE``)の中の木の entry(``.tree``)。compile した process が
bytecode を置く所と同じ置き場に木を置くので、その module の bytecode が置き場に在る間は、どの process が compile しても木が隣に在る
(前は analyzer の置き場の変数が dir を指す process の compile だけが置き、worker が job の子へ渡さないその変数が無い job の compile では
木が残らなかった — 日次は bytecode に当たって compile せず、analyzer が展開し直した)。環境変数が dir を指す(名指しの)置き場にだけ
置く・引く(未設定・空・``off`` は置かない — 普通の import の compile に木の書きを足さない。利用者の cache の既定の dir も使わない)。
analyzer の展開の木の置き場(``DOEFF_EFFECT_ANALYZER_CACHE``)はここを決めない — 木の置き場を 2 つ持たない。

鍵: 木の印(``TREE_TAG``)・Python の版・Hy の版・記録の形(``RECORD_TAG``)・module の名・source の sha256 を ``code_store.content_key`` に
通した鍵 1 つの dir に、展開が使った macro の記録の指紋を名にした entry を持つ。引く側は今の環境の macro で照らして
(``record_is_current_here``)から木を読む — macro が変わった entry の木は読まない。path は鍵に入れない(同じ中身の source は別の作業木
でも当たる — analyzer の置き場と同じ)。

entry の中身: macro の記録の pickle、続けて木(``ast.Module``)の pickle を zlib で圧縮した bytes。木は同じ source の code の数倍の
大きさ(平均 約 196 KB・pickle は繰り返しの多い構文木で、圧縮すると約 1/6〜1/7)なので、置き場の大きさを bytecode と同じ桁に収めるため
圧縮して置く(圧縮は 1 module あたり約 2 ms)。entry の数の上限(``code_store.TREE_ENTRIES_PER_KEY``)と、7 日使われない entry を消す
worker の掃除は ``code_store`` の作法のまま — 引いて当たるたびに entry の時刻を今にする。

analyzer の自分の置き場(``expansions/``)と混ぜない: 向こうの鍵は analyzer 自身の source の digest を持ち、analyzer が書いた木の
持ち主は analyzer 1 つ。ここの木は Hy の compile の口が受け取った物の持ち主がここ 1 つ。型検査のための展開の木は書かない
(口が ``TYPE_CHECK_EXPANSION`` で記録を付けないので、ここへも来ない)。

起動時に読まれる包みから import される module ではない(Hy の source に当たった時だけ読まれる)。
"""

import ast
import pickle
import sys
import zlib
from dataclasses import dataclass

from doeff_hy_bytecode_guard import _code_store as code_store
from doeff_hy_bytecode_guard._loader_hooks import record_fingerprint, record_is_current_here
from doeff_hy_bytecode_guard._records import RECORD_TAG, MacroRecord

#: 木の entry の形の印(形か置き場を変えたら末尾の番号を上げる — 古い鍵は当たらなくなる)。2 = 置き場が bytecode の置き場の中で、
#: 木を圧縮して置く版(1 = analyzer の置き場の下に圧縮せずに置いた版)。
TREE_TAG = "doeff-hy/import-trees/2"

#: 圧縮の段(zlib の 1 = 最も速い段: 構文木の pickle は 1 段でも約 1/6.5 になり、段を上げても 1 割ほどしか小さくならない — 2026-10-21 の実測)。
COMPRESSION_LEVEL = 1


@dataclass(frozen=True)
class ImportTree:
    """import の compile が展開した木と、その展開が依った macro の記録(analyzer の ``_Expanded`` と同じ 2 つ)。"""

    tree: ast.Module
    macros: MacroRecord


def tree_store() -> str | None:
    """木を置く bytecode の置き場の dir(環境変数が dir を指す時だけ。未設定・空・``off`` は None = 置かない・引かない)。"""
    return code_store.store_dir() if code_store.store_is_named() else None


def _key(module_name: str, source: str | bytes) -> str:
    """この source をこの名の module として展開した木の鍵 — 鍵の全部の digest なので、どれが変わっても当たらない。"""
    import hy  # 起動時に読まない

    data = source if isinstance(source, bytes) else source.encode("utf-8")
    return code_store.content_key((TREE_TAG, sys.version, hy.__version__, RECORD_TAG, module_name), data)


def read_import_tree(module_name: str, source: str) -> ImportTree | None:
    """import の compile が置いた、この source の木と macro の記録(置き場が名指しでない・今の環境の macro で照らして使える entry が無ければ
    None)。当たった entry は使った印に時刻を今にする。"""
    store = tree_store()
    if store is None:
        return None
    for entry in code_store.tree_entries(store, _key(module_name, source)):
        found = _read_entry(entry)
        if found is not None:
            code_store.touch_entry(entry)
            return found
    return None


def _read_entry(entry: str) -> ImportTree | None:
    """entry の木と記録(記録が今の環境の macro の物でない・読めない entry は None)。entry の先頭が macro の記録なので、記録が
    合わない entry の木は読まない。"""
    try:
        with open(entry, "rb") as handle:
            macros = pickle.load(handle)
            if not isinstance(macros, MacroRecord) or not record_is_current_here(macros):
                return None
            tree = pickle.loads(zlib.decompress(handle.read()))
    except (OSError, pickle.UnpicklingError, EOFError, AttributeError, ImportError, IndexError, zlib.error):
        return None
    if not isinstance(tree, ast.Module):
        return None
    return ImportTree(tree=tree, macros=macros)


def add_import_tree(module_name: str, source: str | bytes, macros: MacroRecord, tree: object) -> None:
    """import の compile が展開した木を、使った macro の記録と並べて置く(置き場が名指しでない・木が module でない・書けない時は
    何もしない — 書けなくても次の analyzer の展開が 1 回余るだけ。書けない理由は出さない: import の口は venv のすべての Python の
    起動で走る)。"""
    store = tree_store()
    if store is None or not isinstance(tree, ast.Module):
        return
    try:
        data = pickle.dumps(macros, protocol=pickle.HIGHEST_PROTOCOL) + zlib.compress(
            pickle.dumps(tree, protocol=pickle.HIGHEST_PROTOCOL), COMPRESSION_LEVEL
        )
    except (pickle.PicklingError, RecursionError):
        return
    code_store.add_tree(store, _key(module_name, source), record_fingerprint(macros), data)
