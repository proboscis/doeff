"""排水の宣言の読みの 1 点(設計 ki-b5e0d04de958 修正 1 の試作・probe の branch だけ)。

「何をもって宣言ありとするか」の解釈はここにだけ置く。腕(runtime.drain_port)と器(graceful-stop)は
この関数の答え(bool)だけを使い、file の在否・中身を自分で読まない。書く関数は置かない(書き手は doeff の外)。"""
from __future__ import annotations

import os


def declared(path: str | None) -> bool:
    return path is not None and os.path.exists(path)
