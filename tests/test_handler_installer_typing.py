"""handlerの公開型と、既存installerを再適用する契約を検証する。"""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Generator
from pathlib import Path
from typing import Literal

from doeff_core_effects.handlers import await_handler, slog_discard_handler
from doeff_core_effects.scheduler import scheduled
from doeff_vm import K

from doeff import EffectBase, Pass, Program, Pure, do, handler, run


@do
def _forward(effect: EffectBase, k: K) -> Generator[Program, object, None]:
    yield Pass(effect, k)


def test_public_installers_accept_one_program_and_preserve_identity() -> None:
    install = handler(_forward)
    assert handler(install) is install
    assert install.__name__ == _forward.__name__
    assert run(install(Pure(42))) == 42
    assert run(slog_discard_handler(Pure(42))) == 42
    assert run(scheduled(await_handler()(Pure(42)))) == 42


class _ExistingInstaller:
    _doeff_is_handler_fn: Literal[True] = True

    def __call__(self, body: object) -> Program:
        # 合成済みhandlerはWithHandler以外のProgramも返せる。
        return Pure(body)


def test_existing_installer_can_return_a_different_program_node() -> None:
    install = _ExistingInstaller()
    assert handler(install) is install
    assert run(handler(install)(42)) == 42


def test_installer_type_contract_accepts_composition_and_rejects_wrong_arity(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[1]
    original = json.loads((root / "pyrightconfig.json").read_text())
    config = tmp_path / "pyrightconfig.json"
    config.write_text(json.dumps({
        "extends": str(root / "pyrightconfig.json"),
        "extraPaths": [str(root), *(str(root / path) for path in original["extraPaths"])],
    }))
    sample = tmp_path / "installer_contract.py"
    sample.write_text("""from typing import assert_type
from doeff import Program, Pure, handler
from doeff_core_effects.handlers import await_handler, slog_discard_handler

assert_type(slog_discard_handler(Pure(42)), Program)
assert_type(await_handler()(Pure(42)), Program)
assert_type(handler(slog_discard_handler)(Pure(42)), Program)
slog_discard_handler()  # missing body
await_handler()(Pure(42), Pure(0))  # extra argument
handler(42)  # non-callable
""")
    result = subprocess.run(
        [sys.executable, "-m", "pyright", "--project", str(config),
         "--pythonpath", sys.executable, "--outputjson", str(sample)],
        cwd=root, capture_output=True, text=True, timeout=60, check=False,
    )
    assert result.returncode == 1, result.stdout + result.stderr
    report = json.loads(result.stdout)
    errors = [item for item in report["generalDiagnostics"] if item["severity"] == "error"]
    assert [(item["range"]["start"]["line"], item["rule"]) for item in errors] == [
        (7, "reportCallIssue"), (8, "reportCallIssue"), (9, "reportArgumentType"),
    ], errors
