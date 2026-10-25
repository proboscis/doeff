//! pyo3 の Python の版の cfg(`Py_3_12` ほか)をこの crate にも渡す — CPython 3.12 からの関数(PyUnstable_Type_AssignVersionTag)は
//! 3.11 の libpython に無く、無条件に呼ぶと 3.11 で import が undefined symbol で落ちる。
fn main() {
    pyo3_build_config::use_pyo3_cfgs();
}
