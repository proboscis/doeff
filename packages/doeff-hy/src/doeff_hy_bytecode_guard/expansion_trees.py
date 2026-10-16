"""import の compile が展開した木の置き場 — 同じ source を doeff-effect-analyzer が展開し直さないため(card ki-e053a847c515)。

変わった Hy の module は、日次の各 session が検を集める時の import で compile され(macro の展開が重い — 4,447 行の module で約 30 秒)、
閉じの検の analyzer が同じ source をもう一度展開していた(同じ秒の 2 回目)。import の compile の口(``loader_hooks``)は Hy の口の下で
展開した後の木と、展開が依った macro の記録を受け取るので、その 2 つをここへ置く。analyzer は自分の展開の前にここを引く
(向きは analyzer → doeff-hy のまま — doeff-hy は analyzer を import しない)。

置き場は analyzer の展開の木の置き場(``DOEFF_EFFECT_ANALYZER_CACHE``)の下の ``import-trees/``。環境変数が dir を指す時だけ書く
(未設定・``off`` では書かない — 普通の import の compile に木の書きを足さない。analyzer の既定の置き場も使わない)。

鍵: 場所 = Python の版・Hy の版・記録の形(``RECORD_TAG``)・module の名・source の sha256 の digest。entry = 展開が使った macro の
記録の指紋。entry の先頭が macro の記録なので、引く側は今の環境の macro で照らして(``record_is_current_here``)から木を読む —
macro が変わった entry の木は読まない。path は鍵に入れない(同じ中身の source は別の作業木でも当たる — analyzer の置き場と同じ)。

analyzer の自分の置き場(``expansions/``)と混ぜない: 向こうの鍵は analyzer 自身の source の digest を持ち、analyzer が書いた木の
持ち主は analyzer 1 つ。ここの木は Hy の compile の口が受け取った物の持ち主がここ 1 つ。型検査のための展開の木は書かない
(口が ``TYPE_CHECK_EXPANSION`` で記録を付けないので、ここへも来ない)。

起動時に読まれる包みから import される module ではない(Hy の source に当たった時だけ読まれる)。
"""

import ast
import hashlib
import os
import pickle
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from doeff_hy_bytecode_guard import record_fingerprint, record_is_current_here
from doeff_hy_bytecode_guard.records import RECORD_TAG, MacroRecord

#: 置き場の dir を指す環境変数(値 = dir の path・``off`` = 使わない)— doeff-effect-analyzer の展開の木の置き場と同じ 1 つ。
STORE_ENV = "DOEFF_EFFECT_ANALYZER_CACHE"

#: 木の entry の形の印(形を変えたら末尾の番号を上げる — 古い場所は当たらなくなる)。
TREE_TAG = "doeff-hy/import-trees/1"


@dataclass(frozen=True)
class ImportTree:
    """import の compile が展開した木と、その展開が依った macro の記録(analyzer の ``_Expanded`` と同じ 2 つ)。"""

    tree: ast.Module
    macros: MacroRecord


def tree_store_dir() -> Path | None:
    """木を置く dir(環境変数が dir を指す時だけ。未設定・``off`` は None = 書かない・引かない)。"""
    configured = os.environ.get(STORE_ENV)
    if not configured or configured == "off":
        return None
    return Path(configured)


def _place(directory: Path, module_name: str, source: str | bytes) -> Path:
    """この source をこの名の module として展開した木の場所 — 鍵の全部の digest なので、どれが変わっても当たらない。"""
    import hy  # 起動時に読まない

    data = source if isinstance(source, bytes) else source.encode("utf-8")
    text = "\n".join([TREE_TAG, sys.version, hy.__version__, RECORD_TAG, module_name, hashlib.sha256(data).hexdigest()])
    return directory / "import-trees" / hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_import_tree(directory: Path, module_name: str, source: str) -> ImportTree | None:
    """import の compile が置いた、この source の木と macro の記録(今の環境の macro で照らして使える entry が無ければ None)。"""
    place = _place(directory, module_name, source)
    for entry in sorted(place.glob("*.pickle")):
        try:
            with entry.open("rb") as handle:
                macros = pickle.load(handle)
                if not isinstance(macros, MacroRecord) or not record_is_current_here(macros):
                    continue
                tree = pickle.load(handle)
        except (OSError, pickle.UnpicklingError, EOFError, AttributeError, ImportError, IndexError):
            continue
        if isinstance(tree, ast.Module):
            return ImportTree(tree=tree, macros=macros)
    return None


def add_import_tree(module_name: str, source: str | bytes, macros: MacroRecord, tree: object) -> None:
    """import の compile が展開した木を、使った macro の記録と並べて置く(置き場が無い・木が module でない・書けない時は何もしない —
    書けなくても次の analyzer の展開が 1 回余るだけ)。一時の file に書いてから置き換える。"""
    directory = tree_store_dir()
    if directory is None or not isinstance(tree, ast.Module):
        return
    try:
        place = _place(directory, module_name, source)
        place.mkdir(parents=True, exist_ok=True)
        handle, temporary = tempfile.mkstemp(dir=place, suffix=".tmp")
        with os.fdopen(handle, "wb") as out:
            pickle.dump(macros, out, protocol=pickle.HIGHEST_PROTOCOL)
            pickle.dump(tree, out, protocol=pickle.HIGHEST_PROTOCOL)
        os.replace(temporary, place / f"{record_fingerprint(macros)}.pickle")
    except (OSError, pickle.PicklingError, RecursionError):
        return
