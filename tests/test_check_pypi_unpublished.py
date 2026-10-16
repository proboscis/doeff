"""tools/check_pypi_unpublished.py fails a release whose version is already on PyPI.

The publish workflows upload with ``skip-existing: true``, so a tag pushed without a version bump
used to upload nothing and still report success. The check runs before any upload and reads the
same PyPI JSON API (``/pypi/<name>/<version>/json``) the uploads target; these tests point it at a
local stand-in for that API.
"""

from __future__ import annotations

import subprocess
import sys
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "check_pypi_unpublished.py"

PUBLISHED = {("doeff", "0.4.1"), ("doeff-vm", "0.2.0")}
UNANSWERED = "doeff-broken-index"


class _FakePyPI(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        parts = self.path.strip("/").split("/")
        if len(parts) != 4 or parts[0] != "pypi" or parts[3] != "json":
            self.send_error(400)
            return
        name, version = parts[1], parts[2]
        if name == UNANSWERED:
            self.send_error(503)
            return
        if (name, version) not in PUBLISHED:
            self.send_error(404)
            return
        body = b'{"info": {}}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def index_url() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FakePyPI)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/pypi"
    finally:
        server.shutdown()
        server.server_close()


def _package(root: Path, name: str, version_line: str) -> Path:
    package_dir = root / name
    package_dir.mkdir()
    (package_dir / "pyproject.toml").write_text(
        f'[project]\nname = "{name}"\n{version_line}\n', encoding="utf-8"
    )
    return package_dir


def _check(index_url: str, *package_dirs: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(TOOL), "--index-url", index_url, *map(str, package_dirs)],
        capture_output=True,
        text=True,
        check=False,
    )


def test_fails_when_the_version_is_already_on_pypi(index_url: str, tmp_path: Path) -> None:
    done = _check(index_url, _package(tmp_path, "doeff", 'version = "0.4.1"'))

    assert done.returncode == 1, done.stdout + done.stderr
    assert "[FAIL] doeff 0.4.1" in done.stdout
    assert "already on PyPI" in done.stdout


def test_passes_when_the_version_is_not_on_pypi(index_url: str, tmp_path: Path) -> None:
    done = _check(index_url, _package(tmp_path, "doeff", 'version = "0.5.0"'))

    assert done.returncode == 0, done.stdout + done.stderr
    assert "[OK] doeff 0.5.0" in done.stdout


def test_passes_for_a_project_that_was_never_published(index_url: str, tmp_path: Path) -> None:
    done = _check(index_url, _package(tmp_path, "doeff-hy-pytest", 'version = "0.1.0"'))

    assert done.returncode == 0, done.stdout + done.stderr
    assert "[OK] doeff-hy-pytest 0.1.0" in done.stdout


def test_reports_every_package_before_failing(index_url: str, tmp_path: Path) -> None:
    done = _check(
        index_url,
        _package(tmp_path, "doeff-vm", 'version = "0.2.0"'),
        _package(tmp_path, "doeff-hy", 'version = "0.1.0"'),
        _package(tmp_path, "doeff", 'version = "0.4.1"'),
    )

    assert done.returncode == 1, done.stdout + done.stderr
    assert "[FAIL] doeff-vm 0.2.0" in done.stdout
    assert "[OK] doeff-hy 0.1.0" in done.stdout
    assert "[FAIL] doeff 0.4.1" in done.stdout


def test_fails_when_pypi_does_not_answer(index_url: str, tmp_path: Path) -> None:
    done = _check(index_url, _package(tmp_path, UNANSWERED, 'version = "0.1.0"'))

    assert done.returncode == 1, done.stdout + done.stderr
    assert f"[FAIL] {UNANSWERED} 0.1.0" in done.stdout
    assert "503" in done.stdout


def test_fails_when_the_version_is_not_static(index_url: str, tmp_path: Path) -> None:
    done = _check(index_url, _package(tmp_path, "doeff", 'dynamic = ["version"]'))

    assert done.returncode == 1, done.stdout + done.stderr
    assert "[FAIL]" in done.stdout
    assert "static" in done.stdout
