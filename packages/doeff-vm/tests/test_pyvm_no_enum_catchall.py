"""Source guards for exhaustive matches in the Python VM bridge."""

from pathlib import Path


def _runtime_source() -> str:
    source_path = Path(__file__).resolve().parents[1] / "src" / "pyvm.rs"
    return source_path.read_text(encoding="utf-8").split("#[cfg(test)]", 1)[0]


def _between(source: str, start: str, end: str) -> str:
    assert start in source, f"start marker not found: {start}"
    assert end in source, f"end marker not found: {end}"
    return source.split(start, 1)[1].split(end, 1)[0]


def test_pyvm_runtime_has_no_catchall_match_arms() -> None:
    runtime_source = _runtime_source()
    guarded_sections = (
        _between(runtime_source, "fn convert_vm_error(", "fn make_unhandled_effect_error"),
        _between(runtime_source, "fn describe_effect(", "fn step_loop("),
        _between(runtime_source, "fn step_loop(", "// classify_program"),
    )

    for section in guarded_sections:
        assert "_ =>" not in section
        assert "other =>" not in section


def test_pyvm_error_conversion_explicitly_mentions_all_vmerror_variants() -> None:
    runtime_source = _runtime_source()
    convert_vm_error = _between(
        runtime_source,
        "fn convert_vm_error(",
        "fn make_unhandled_effect_error",
    )

    for variant in (
        "VMError::OneShotViolation",
        "VMError::UnhandledEffect",
        "VMError::NoMatchingHandler",
        "VMError::DelegateNoOuterHandler",
        "VMError::HandlerNotFound",
        "VMError::InvalidSegment",
        "VMError::PythonError",
        "VMError::InternalError",
        "VMError::TypeError",
        "VMError::UncaughtException",
    ):
        assert variant in convert_vm_error


def test_pyvm_external_call_driver_names_all_non_callable_value_variants() -> None:
    runtime_source = _runtime_source()
    step_loop = _between(runtime_source, "fn step_loop(", "// classify_program")

    assert "Value::Callable(callable)" in step_loop
    for variant in (
        "Value::Unit",
        "Value::Int",
        "Value::Bool",
        "Value::String",
        "Value::None",
        "Value::Stream",
        "Value::Continuation",
        "Value::Var",
        "Value::List",
        "Value::Opaque",
    ):
        assert variant in step_loop
