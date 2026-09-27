"""doeff-core-effects の検の実行環境(Hy の ``test_*.hy`` の収集だけ)。

- doeff-adr の Hy の file の収集(``DoeffAdrHyFile``)をこの dir の中だけで使う(doeff-cluster の tests/conftest.py と同じ形 — 根の ini の
  ``doeff_adr_hy_files`` には足さない。package の母集団は ``make test-packages`` が別に走らせる)。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from doeff_adr.pytest_plugin import DoeffAdrHyFile


def pytest_collect_file(file_path: Path, parent: pytest.Collector) -> pytest.Collector | None:
    if file_path.suffix == ".hy" and file_path.name.startswith("test_"):
        return DoeffAdrHyFile.from_parent(parent, path=file_path)
    return None
