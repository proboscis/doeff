"""principals.hy の公開面の型(型検査のための宣言 — 実行時は principals.hy を読む)。

principals.hy は Hy の module なので、型の宣言が無いと pyright は中を読めず、名簿 Roster と書き手 Principal が
Unknown になる(service.pyi の RecordsService の欄・respond の書き手の名の読みが Unknown に連なる)。ここで型を宣言する。

- `(defclass [(dataclass :frozen True)] …)` は位置でも渡せる frozen の dataclass。
- defk は呼ぶと Program を返す(答えの型 = 実装の :post の型)。
- 実装との食い違いは packages/doeff-records/tests/test_static_stubs.py が名・欄の名と順・既定値の有無・引数の名で検める。
"""

from dataclasses import dataclass, field

from doeff import Program
from doeff_hy.frozen import FrozenMap

ROSTER_VERSION: int
ROSTER_DOCUMENT_KEYS: frozenset[str]
ROSTER_ENTRY_KEYS: frozenset[str]
AUTH_SCHEME: str
HEX_DIGITS: frozenset[str]
ANONYMOUS: str

@dataclass(frozen=True)
class Roster:
    digests: FrozenMap[str] = field(default_factory=FrozenMap)

@dataclass(frozen=True)
class Principal:
    name: str

def token_digest(token: str) -> Program[str, object]: ...
def decode_roster(text: str) -> Program[Roster, object]: ...
def identify(roster: Roster, header: str | None) -> Program[Principal, object]: ...
def writer_of(declared: str | None) -> Program[Principal, object]: ...
