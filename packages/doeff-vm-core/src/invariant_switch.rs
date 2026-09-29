//! Runtime switch for the per-step invariant checks (agora-redesign #980).
//!
//! The checks used to be selected at compile time (cargo feature
//! `invariant-checks`), so the same venv ran 15x slower or faster depending
//! on which path last built the extension (`make sync` with the feature,
//! `uv sync` without). The checks are now always compiled in and turned on
//! at run time, so every build path produces the same binary:
//!
//!   - `DOEFF_VM_INVARIANT_CHECKS=1` in the environment (read once, lazily),
//!   - `set_invariant_checks(true)` from the host (doeff's own pytest
//!     sessions do this — ADR-DOE-ENFORCE-001 R4),
//!   - the cargo feature `invariant-checks` still forces them on, for the
//!     Rust conformance tests (`cargo test --features invariant-checks`).
//!
//! When off, the cost is one relaxed atomic load per VM step.

use std::sync::atomic::{AtomicU8, Ordering};

/// Environment variable that turns the checks on (`1`) or off (`0`).
pub const INVARIANT_CHECKS_ENV: &str = "DOEFF_VM_INVARIANT_CHECKS";

const UNREAD: u8 = 0;
const OFF: u8 = 1;
const ON: u8 = 2;

static STATE: AtomicU8 = AtomicU8::new(UNREAD);

/// Whether the per-step invariant checks run.
#[inline]
pub fn invariant_checks_enabled() -> bool {
    match STATE.load(Ordering::Relaxed) {
        ON => true,
        OFF => false,
        _ => {
            let on = cfg!(feature = "invariant-checks") || env_says_on();
            set_invariant_checks(on);
            on
        }
    }
}

/// Turn the per-step invariant checks on or off for this process.
pub fn set_invariant_checks(on: bool) {
    STATE.store(if on { ON } else { OFF }, Ordering::Relaxed);
}

fn env_says_on() -> bool {
    match std::env::var(INVARIANT_CHECKS_ENV) {
        Err(std::env::VarError::NotPresent) => false,
        Ok(value) if value == "1" => true,
        Ok(value) if value == "0" => false,
        Ok(value) => panic!(
            "{INVARIANT_CHECKS_ENV}={value:?}: expected \"1\" (checks on) or \"0\" (checks off)"
        ),
        Err(std::env::VarError::NotUnicode(value)) => panic!(
            "{INVARIANT_CHECKS_ENV}={value:?}: expected \"1\" (checks on) or \"0\" (checks off)"
        ),
    }
}
