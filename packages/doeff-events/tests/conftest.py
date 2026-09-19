"""Test configuration for doeff-events."""

import sys
from pathlib import Path
from typing import Any

import pytest
from doeff_core_effects.handlers import await_handler
from doeff_core_effects.scheduler import scheduled
from doeff_events.handlers import event_handler

from doeff import handler, run

PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))


@pytest.fixture
def run_events():
    """現行 API で event の program を走らせる — scheduler + await_handler + event_handler の 3 層。

    旧 `run(event_handler()(p), handlers=default_handlers())` の置き換え。旧形は Result を返し
    たが、現行の run は値を返し失敗は例外で出るので、検体は `.is_ok()` / `.value` を読まない。
    """

    def _run(program: Any) -> Any:
        return run(scheduled(handler(await_handler())(event_handler()(program))))

    return _run
