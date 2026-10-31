//! Linux の外の常駐の process の口(inotify が無いので常駐の process は無い)。
//! `enable()` が効かないので 1 file の実行はここへ来ない。`fsmonitor-serve` を直に起こされた時だけ、居ない訳を言って終わる。

use std::path::Path;
use std::process::ExitCode;

/// 錠を持つ常駐の process は居ないが、起こす試みもさせないので「持たれている」と答える。
pub fn lock_is_held(_path: &Path) -> bool {
    true
}

/// `fsmonitor-serve` — 常駐の process はこの OS で組まない(inotify が要る)。
pub fn serve() -> ExitCode {
    eprintln!("doeff-linter: {} は Linux だけで動く(inotify が要る)", super::SERVE_COMMAND);
    ExitCode::from(2)
}
