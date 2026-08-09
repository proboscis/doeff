// Bad fixture for doeff-vm-no-per-call-copyreg-resolution: the pre-2026-08-10
// shape of EffectBase.__reduce_ex__, which resolved copyreg.__newobj__ on
// every call and wedged free-threaded runtimes on the import lock.
pub fn reduce_ex_bad(slf: &Bound<'_, PyAny>) -> PyResult<Py<PyAny>> {
    let py = slf.py();
    let copyreg = py.import("copyreg")?;
    let newobj = copyreg.getattr("__newobj__")?;
    Ok(newobj.unbind())
}
