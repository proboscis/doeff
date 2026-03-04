from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def _read_source(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def test_cli_run_result_uses_typed_access() -> None:
    source = _read_source("doeff/__main__.py")
    forbidden = (
        'getattr(run_result, "display", None)',
        'getattr(run_result, "error", None)',
        'getattr(run_result, "log", None)',
        'getattr(run_result, "trace", None)',
        'getattr(run_result, "raw_store", None)',
        'getattr(result, "traceback_data", None)',
        'getattr(run_result, "effect_observations", None)',
        'getattr(run_result, "context", None)',
        'getattr(context, "effect_observations", None)',
    )
    for snippet in forbidden:
        assert snippet not in source


def test_cli_args_use_typed_access() -> None:
    source = _read_source("doeff/__main__.py")
    forbidden = (
        'getattr(args, "no_runbox", False)',
        'getattr(args, "apply", None)',
        'getattr(args, "transform", None)',
        'getattr(args, "report", False)',
        'getattr(args, "report_verbose", False)',
        'getattr(args, "code", None)',
        'getattr(args, "program", None)',
        'getattr(args, "script", None)',
    )
    for snippet in forbidden:
        assert snippet not in source


def test_kleisli_internal_state_uses_typed_access() -> None:
    source = _read_source("doeff/kleisli.py")
    forbidden = (
        'getattr(self, "__doeff_do_decorated__", False)',
        'getattr(self, "_auto_unwrap_strategy", None)',
        'getattr(self, "_metadata_source", self.func)',
        'getattr(self, "__name__", getattr(metadata_source, "__name__", "<anonymous>"))',
        'getattr(self, "_is_do_decorated", False)',
        'getattr(self, "_doeff_generator_factory", None)',
    )
    for snippet in forbidden:
        assert snippet not in source


def test_rust_vm_handler_metadata_uses_typed_access() -> None:
    source = _read_source("doeff/rust_vm.py")
    forbidden = (
        'getattr(handler, "func", handler)',
        'getattr(handler, "__signature__", None)',
        'getattr(handler, "_metadata_source", None)',
    )
    for snippet in forbidden:
        assert snippet not in source
