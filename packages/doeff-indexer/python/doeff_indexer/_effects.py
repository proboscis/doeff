"""索引を引く effect FindSymbols と、その答えの 1 行の型 SymbolInfo(Rust の拡張を読まない — 答える handler は _handlers)。

決め: 依頼書 lt-S4R44DQ5ZPJZD986TY812Q2DW9 の問い indexer の答え lt-ZDN45RSNPV16BKSM997GGZ9CVB(effect にする・card
ki-1e51e218527e・ADR-DOE-API-001)。前の口の class Indexer(Indexer.for_module(m).find_symbols(…))は内側の拡張
doeff_indexer._native に置き、入口から出さない。答えの 1 行の型 SymbolInfo は R2 の effect の結果の型として入口から見せる。

この module は Python だけで、拡張の .so を読まない: test は FindSymbols に自分の handler で答えれば、Rust の索引器も拡張も
file も無しに同じ program を走らせられる(構成のレビュー lt-QHQJEWJDSAHGTT1ZASS28F9V68 の所見 A)。
"""

from dataclasses import dataclass
from typing import Literal

from doeff import EffectBase


@dataclass(frozen=True)
class SymbolInfo:
    """索引の 1 行: 印(# doeff: … か docstring の doeff:)の付いた関数か変数。"""

    #: 名(例 "auth_interpreter")
    name: str
    #: 名の在る module(例 "myapp.features.auth")
    module_path: str
    #: module と名をつないだ path(例 "myapp.features.auth.auth_interpreter")
    full_path: str
    #: "function"・"async_function"・"variable"
    symbol_type: Literal["function", "async_function", "variable"]
    #: 印の語(例 ("interpreter", "default"))
    tags: tuple[str, ...]
    #: 定義の行(1 から)
    line_number: int
    #: 定義の file(索引の根からの path)
    file_path: str


@dataclass(frozen=True)
class FindSymbols(EffectBase[tuple[SymbolInfo, ...]]):
    """module の階層の印の付いた名を引く: yield FindSymbols("myapp.features.auth", tags=("interpreter", "default"),
    symbol_type="function") → tuple[SymbolInfo, ...]。

    tags は全部を持つ名だけ(大文字と小文字を区別しない)、symbol_type が None なら種類を問わない。索引を作れなければ
    handler が RuntimeError か OSError を effect を出した所へ投げ返す。
    """

    module_path: str
    tags: tuple[str, ...] = ()
    symbol_type: Literal["function", "async_function", "variable"] | None = None
