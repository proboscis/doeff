//! Compile-time gated debug logging utilities for the VM.

/// Emit VM debug logs only when the `vm_debug_logs` Cargo feature is enabled.
///
/// With the feature disabled (default), this macro compiles to a no-op while
/// still type-checking format arguments.
#[macro_export]
macro_rules! vm_debug_log {
    ($($arg:tt)*) => {{
        #[cfg(feature = "vm_debug_logs")]
        {
            eprintln!($($arg)*);
        }
        #[cfg(not(feature = "vm_debug_logs"))]
        {
            let _ = format_args!($($arg)*);
        }
    }};
}

/// Emit a VM warning to stderr.
///
/// Warning paths are rare failure cases where silent recovery would make later
/// debugging materially harder, so this macro is always enabled.
#[macro_export]
macro_rules! vm_warn_log {
    ($($arg:tt)*) => {{
        eprintln!("[doeff-vm warning] {}", format_args!($($arg)*));
    }};
}
