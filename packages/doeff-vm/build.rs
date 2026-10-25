//! pyo3 の Python の版の cfg(`Py_3_12` ほか)をこの crate にも渡す — CPython 3.12 からの関数(PyUnstable_Type_AssignVersionTag)は
//! 3.11 の libpython に無く、無条件に呼ぶと 3.11 で import が undefined symbol で落ちる。
//!
//! Windows では、この crate が自分で宣言する PyUnstable_Type_AssignVersionTag のために Python の import library を link する。
//! PyO3 0.29 は Windows で import library を link せず、pyo3-ffi が宣言する関数だけを raw-dylib で解くので、pyo3-ffi に無いこの関数は
//! link が LNK2019 で落ちる(Build doeff-vm run 40220805725 の build-windows)。
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
