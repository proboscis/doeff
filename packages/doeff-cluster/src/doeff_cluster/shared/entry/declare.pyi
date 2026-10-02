"""shared/entry/declare.hy の公開面の型(型検査のための宣言 — 実行時は declare.hy を読む・#2824)。

declare.hy は Hy の module なので、pyright は中を読めず、名が全部 Unknown になる。#2751 で defk にした apply-declaration を、
使い手の repo の宣言の命令が土台の下で走らせて答えの真偽で終了の番号を決めると、書き手に直せない赤(Argument type is unknown)が
出た。ここで型を宣言する(service_build.pyi と同じ形)。

- defk(declare-request・service-written・apply-declaration)は呼ぶと Program を返す。答えは実装の :post(書きの返事 HttpResponse・
  全部が通ったかの真偽)。
- main は console script の入口(普通の関数)。
- 実装との食い違いは packages/doeff-cluster/tests/test_service_model_stubs.py が検める。
"""

from collections.abc import Mapping
from typing import Any

from doeff import Program
from doeff_cluster.shared.intent.service_model import Declaration
from doeff_core_effects.http_effects import HttpResponse

DECLARE_REPLY_SECONDS: float

def declare_request(method: str, url: str, actor: str, body: dict[str, object] | None) -> Program[HttpResponse, Any]: ...
def service_written(base: str, actor: str, row: Mapping[str, object], replicas: int | None) -> Program[HttpResponse, Any]: ...
def apply_declaration(
    url: str, declaration: Declaration, actor: str, replicas: int | None = None
) -> Program[bool, Any]: ...
def main() -> None: ...
