// Bad fixture for doeff-vm-no-per-call-copyreg-resolution, placed one level
// BELOW src/ on purpose: doeff-vm-core keeps dispatch/step/handler in src/vm/,
// so a paths.include that only reaches src/*.rs would leave them unguarded.
pub fn reduce_ex_bad_in_subdir(slf: &Bound<'_, PyAny>) -> PyResult<Py<PyAny>> {
    let py = slf.py();
    let copyreg = py.import("copyreg")?;
    let newobj = copyreg.getattr("__newobj__")?;
    Ok(newobj.unbind())
}
