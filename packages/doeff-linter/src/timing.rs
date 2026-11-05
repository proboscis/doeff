//! Instrument that prints per-stage timings to stderr (only when the environment variable `DOEFF_LINTER_TIMING` is set).
//!
//! A single hook run right after a write took 3 seconds of CPU: this measures which processing stage is heavy,
//! without changing the answer (the JSON on stdout). Parallel work hides its weight behind wall-clock time, so the
//! difference in process CPU seconds (utime + stime from `/proc/self/stat`) is printed too. On machines where it cannot be read, the CPU-seconds field is `?`.

use std::time::Instant;

const ENV: &str = "DOEFF_LINTER_TIMING";

fn cpu_seconds() -> Option<f64> {
    let stat = std::fs::read_to_string("/proc/self/stat").ok()?;
    // comm may contain spaces inside its parentheses, so count from after the last ')' (utime = field 14, stime = field 15).
    let rest = stat.get(stat.rfind(')')? + 2..)?;
    let fields: Vec<&str> = rest.split_whitespace().collect();
    let ticks: f64 = fields.get(11)?.parse::<f64>().ok()? + fields.get(12)?.parse::<f64>().ok()?;
    Some(ticks / 100.0)
}

/// Runs `f` and, if the instrument is enabled, prints one line to stderr: `doeff-linter-timing: <label> wall=<seconds> cpu=<seconds>`.
pub fn timed<T>(label: &str, f: impl FnOnce() -> T) -> T {
    if std::env::var_os(ENV).is_none() {
        return f();
    }
    let (wall, cpu) = (Instant::now(), cpu_seconds());
    let out = f();
    let spent = match (cpu, cpu_seconds()) {
        (Some(before), Some(after)) => format!("{:.2}", after - before),
        _ => "?".to_string(),
    };
    eprintln!("doeff-linter-timing: {label} wall={:.3} cpu={spent}", wall.elapsed().as_secs_f64());
    out
}

/// If the instrument is enabled, prints one line to stderr: `doeff-linter-timing: <label> <field>=<number>` (not a stage time but a count of things, e.g. the number of files whose contents were actually read).
pub fn count(label: &str, field: &str, value: usize) {
    if std::env::var_os(ENV).is_some() {
        eprintln!("doeff-linter-timing: {label} {field}={value}");
    }
}
