"""host_contract.hy の公開面の型(宿の契約の鍵と、宿の答え手 — 型検査のための宣言・実行時は host_contract.hy を読む・#2197)。

host_contract.hy は Hy の module で型の宣言が無かったので、土台の組に host-reader・environ-reader を並べる使い手の strict に、書き手に
直せない Unknown の赤(Type of "host_reader" is unknown・Argument type is partially unknown)が出ていた。

- 鍵の組 HostContract は defrecord(凍った dataclass・名の引数だけ)。
- host-reader(Python の名 host_reader)は引数の無い defhandler — 値そのものが本文の Program に被せる関数。
- environ-reader(Python の名 environ_reader)は置き場を受け、本文の Program に被せる関数を返す。
- 実装との食い違いは packages/doeff-cluster/tests/test_host_contract_static_types.py が検める。
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol, TypeVar

from doeff_vm import WithHandler

from doeff import Program

_A = TypeVar("_A")

@dataclass(frozen=True, kw_only=True)
class HostContract:
    run_context_key: str
    program_key: str
    versions_key: str
    program_env: str

HOST_CONTRACT: HostContract
SIM_PASSABLE: tuple[type, ...]

class _HostHandler(Protocol):
    """本文の Program に handler を被せる関数(答えの型は本文のまま)。"""

    def __call__(self, body: Program[_A, object], /) -> WithHandler[_A]: ...

host_reader: _HostHandler

def environ_reader(environ: Mapping[str, str] = ...) -> _HostHandler: ...
