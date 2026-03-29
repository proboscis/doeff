from __future__ import annotations

import importlib
import logging
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_run_module_does_not_import_cli_entrypoint() -> None:
    src = (ROOT / "doeff" / "run.py").read_text(encoding="utf-8")
    assert "from doeff.__main__" not in src
    assert "import doeff.__main__" not in src
    assert "def _apply_envs" not in src
    assert "def _load_default_env" not in src


def test_run_program_uses_shared_services_for_string_inputs(
    tmp_path: Path, monkeypatch
) -> None:
    import doeff
    from doeff_vm import run as vm_run
    from doeff.run import run_program

    pkg_dir = tmp_path / "sample_run_pkg"
    pkg_dir.mkdir()
    (pkg_dir / "__init__.py").write_text("", encoding="utf-8")
    (pkg_dir / "app.py").write_text(
        """
from doeff import Ask, Program, do
from doeff_vm import default_handlers, run as vm_run


@do
def program():
    value = yield Ask("value")
    return value


def env():
    return {"value": 5}


@do
def add_three(value: int) -> int:
    return value + 3


def double_result(program: Program[int]) -> Program[int]:
    @do
    def _transformed():
        value = yield program
        return value * 2

    return _transformed()


def interpreter(program: Program[int]):
    return vm_run(program, handlers=default_handlers())
""",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    importlib.invalidate_caches()

    try:
        result = run_program(
            "sample_run_pkg.app.program",
            interpreter="sample_run_pkg.app.interpreter",
            envs=["sample_run_pkg.app.env"],
            apply="sample_run_pkg.app.add_three",
            transform=["sample_run_pkg.app.double_result"],
            quiet=True,
            load_default_env=False,
        )
    finally:
        doeff.run = vm_run

    assert result.value == 16
    assert result.interpreter_path == "sample_run_pkg.app.interpreter"
    assert result.env_sources == ["sample_run_pkg.app.env"]
    assert result.applied_kleisli == "sample_run_pkg.app.add_three"
    assert result.applied_transforms == ["sample_run_pkg.app.double_result"]


def test_run_program_quiet_default_env_behavior_uses_shared_loader(
    tmp_path: Path, monkeypatch, caplog
) -> None:
    import doeff
    from doeff_vm import run as vm_run
    from doeff.run import run_program

    monkeypatch.setenv("HOME", str(tmp_path))

    caplog.set_level(logging.DEBUG, logger="doeff.run")
    try:
        quiet_result = run_program(
            doeff.Program.pure(7),
            quiet=True,
            load_default_env=True,
        )
        assert quiet_result.value == 7
        assert quiet_result.env_sources == []
        assert not caplog.records

        caplog.clear()
        loud_result = run_program(
            doeff.Program.pure(9),
            quiet=False,
            load_default_env=True,
        )
        assert loud_result.value == 9
        assert loud_result.env_sources == []
        assert any("~/.doeff.py not found" in record.message for record in caplog.records)
    finally:
        doeff.run = vm_run
