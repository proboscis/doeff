//! Pass pyo3's Python-version cfgs (`Py_3_12` etc.) to this crate too. A function introduced in CPython 3.12 (PyUnstable_Type_AssignVersionTag)
//! is missing from the 3.11 libpython, so calling it unconditionally makes the import fail on 3.11 with an undefined symbol.
//!
//! On Windows, link the Python import library for PyUnstable_Type_AssignVersionTag, which this crate declares itself.
//! PyO3 0.29 does not link the import library on Windows and resolves only the functions declared by pyo3-ffi via raw-dylib, so this function, absent from pyo3-ffi,
//! fails to link with LNK2019 (build-windows of Build doeff-vm run 40220805725).
fn main() {
    pyo3_build_config::use_pyo3_cfgs();

    if std::env::var("CARGO_CFG_TARGET_OS").as_deref() == Ok("windows") {
        let config = pyo3_build_config::get();
        if let Some(lib_name) = config.lib_name() {
            println!("cargo:rustc-link-lib=dylib={lib_name}");
        }
        if let Some(lib_dir) = config.lib_dir() {
            println!("cargo:rustc-link-search=native={lib_dir}");
        }
    }
}
