"""schema_digest.hy の公開面の型(型検査のための宣言 — 実行時は schema_digest.hy を読む)。

schema_digest.hy は Hy の module なので、型の宣言が無いと pyright は中を読めず、表の要約の評価 schema-digests が Unknown になる
(使い手が木ごとに書く表の要約の data の script の strict の型検査で赤になる・#2742)。defk は呼ぶと Program を返す(答え = 表の名 →
64 字の sha256 の FrozenMap)。実装との食い違いは packages/doeff-records/tests/test_static_stubs.py が名と引数の名で検める。
"""

from doeff_hy.frozen import FrozenMap
from doeff_records.values import RecordsSchema

from doeff import Program

def schema_digests(schema: RecordsSchema) -> Program[FrozenMap[str], object]: ...
