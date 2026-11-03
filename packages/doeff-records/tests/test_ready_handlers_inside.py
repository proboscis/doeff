"""doeff-records の ready_handlers は handler を被せる関数でなく、検の殻の用意の program(書き手の名 → handler をそのまま返す)
なので、工場にせず内側の _ready_handlers へ移した(ADR-DOE-API-001 R6)。

root の tests/test_handler_wrapper_factories.py(program に handler を被せていた 8 名)から、doeff-records の分を分けた
(2026-11-04 — root の tests が内輪の package を import しないため)。
"""

from __future__ import annotations

import importlib


def test_ready_handlers_is_inside_records_http_server() -> None:
    http_server = importlib.import_module("doeff_records._http_server")

    assert not hasattr(http_server, "ready_handlers")
    assert hasattr(http_server, "_ready_handlers")
