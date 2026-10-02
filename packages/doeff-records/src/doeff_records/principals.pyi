"""principals.hy の公開面の型(型検査のための宣言 — 実行時は principals.hy を読む)。

principals.hy は Hy の module なので、型の宣言が無いと pyright は中を読めず、書き手 Principal が
Unknown になる(service.pyi の respond の書き手の名の読みが Unknown に連なる)。ここで型を宣言する。

- `(defclass [(dataclass :frozen True)] …)` は位置でも渡せる frozen の dataclass。
- defk は呼ぶと Program を返す(答えの型 = 実装の :post の型)。
- 実装との食い違いは packages/doeff-records/tests/test_static_stubs.py が名・欄の名と順・既定値の有無・引数の名で検める。
"""

from dataclasses import dataclass

from doeff import Program

ANONYMOUS: str

@dataclass(frozen=True)
class Principal:
    name: str

def writer_of(declared: str | None) -> Program[Principal, object]: ...
