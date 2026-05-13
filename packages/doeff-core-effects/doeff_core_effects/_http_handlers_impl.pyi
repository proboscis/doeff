from pathlib import Path
from typing import Any


def _http_production_handler(client: Any, sleep: Any) -> Any: ...


def _http_fixture_record_handler(
    path: Path,
    fixtures: dict[str, dict[str, Any]],
) -> Any: ...


def _http_fixture_replay_handler(fixtures: dict[str, dict[str, Any]]) -> Any: ...
