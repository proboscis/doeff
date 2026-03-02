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
