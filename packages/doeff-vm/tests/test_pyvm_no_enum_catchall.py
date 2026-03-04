from pathlib import Path


def _runtime_source() -> str:
    source_path = Path(__file__).resolve().parents[1] / "src" / "pyvm.rs"
    src = source_path.read_text(encoding="utf-8")
    return src.split("#[cfg(test)]", 1)[0]


def _between(src: str, start: str, end: str) -> str:
    return src.split(start, 1)[1].split(end, 1)[0]


def test_pyvm_runtime_has_no_enum_catchall_match_arms() -> None:
    runtime_src = _runtime_source()

    vmerror_fn = _between(
        runtime_src,
        "fn vmerror_to_pyerr_with_traceback_data",
        "fn vmerror_to_pyerr",
    )
    assert "_ =>" not in vmerror_fn

    pending_fn = _between(runtime_src, "fn pending_generator", "fn step_generator")
    assert "_ =>" not in pending_fn

    value_fn = _between(
        runtime_src,
        "fn value_to_runtime_pyobject",
        "fn call_metadata_to_dict",
    )
    assert "_ =>" not in value_fn


def test_pyvm_runtime_explicitly_mentions_all_target_enum_variants() -> None:
    runtime_src = _runtime_source()

    vmerror_fn = _between(
        runtime_src,
        "fn vmerror_to_pyerr_with_traceback_data",
        "fn vmerror_to_pyerr",
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
        assert variant in vmerror_fn

    pending_fn = _between(runtime_src, "fn pending_generator", "fn step_generator")
    for variant in (
        "PendingPython::EvalExpr",
        "PendingPython::CallFuncReturn",
        "PendingPython::StepUserGenerator",
        "PendingPython::ExpandReturn",
        "PendingPython::RustProgramContinuation",
        "PendingPython::AsyncEscape",
        "None =>",
    ):
        assert variant in pending_fn

    value_fn = _between(
        runtime_src,
        "fn value_to_runtime_pyobject",
        "fn call_metadata_to_dict",
    )
    for variant in (
        "Value::Python",
        "Value::Unit",
        "Value::Int",
        "Value::String",
        "Value::Bool",
        "Value::None",
        "Value::Continuation",
        "Value::Handlers",
        "Value::Kleisli",
        "Value::Task",
        "Value::Promise",
        "Value::ExternalPromise",
        "Value::CallStack",
        "Value::Trace",
        "Value::Traceback",
        "Value::ActiveChain",
        "Value::List",
    ):
        assert variant in value_fn
