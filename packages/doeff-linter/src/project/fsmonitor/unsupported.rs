//! Resident-process entry point outside Linux (no inotify, so there is no resident process).
//! `enable()` has no effect, so single-file runs never come here. Only when `fsmonitor-serve` is started directly does it say why no process exists and exit.

use std::path::Path;
use std::process::ExitCode;

/// No resident process holds the lock, but we do not allow attempts to start one either, so we answer "held".
pub fn lock_is_held(_path: &Path) -> bool {
    true
}

/// `fsmonitor-serve` — the resident process is not built for this OS (it needs inotify).
pub fn serve() -> ExitCode {
    eprintln!("doeff-linter: {} works only on Linux (it needs inotify)", super::SERVE_COMMAND);
    ExitCode::from(2)
}
