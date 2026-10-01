//! この binary を組んだ時の情報(build.rs が env に渡す)。crate の中の読み手は lib.rs でなくここを読む — 子の module が
//! crate の根(lib.rs)を読み戻すと、lib.rs が宣言する全 module が 1 つの依存の輪になる(agora-redesign #2119)。

/// この binary を組んだ doeff の commit(build.rs が決める — 自動の組み直しが渡す env DOEFF_LINTER_BUILD_COMMIT か、
/// 手で組んだ時の git の HEAD に `+dirty`、git が無ければ `unknown`)。`--version` と editor-json の `linter` が名乗る。
pub const BUILD_COMMIT: &str = env!("DOEFF_LINTER_COMMIT");
/// `--version` の文(`<版> (doeff <commit>)`)。
pub const VERSION_TEXT: &str = concat!(env!("CARGO_PKG_VERSION"), " (doeff ", env!("DOEFF_LINTER_COMMIT"), ")");
