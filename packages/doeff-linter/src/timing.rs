//! Instrument that prints per-stage timings to stderr (only when the environment variable `DOEFF_LINTER_TIMING` is set).
//!
//! A single hook run right after a write took 3 seconds of CPU: this measures which processing stage is heavy,
//! without changing the answer (the JSON on stdout). Parallel work hides its weight behind wall-clock time, so the
//! difference in process CPU seconds (utime + stime from `/proc/self/stat`) is printed too. On machines where it cannot be read, the CPU-seconds field is `?`.
//! The memory after the stage is printed too (`rss` = resident anonymous memory now, `hwm` = the process's resident peak so far, in MiB, from
//! `/proc/self/status`): a few-file run reached 4 GiB, over the 2 GiB a command may use, and the stage whose `hwm` jumps is where it went.

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

/// The resident anonymous memory now and the resident peak so far, in MiB (`?` where `/proc/self/status` cannot be read).
fn memory_mib() -> (String, String) {
    let status = std::fs::read_to_string("/proc/self/status").unwrap_or_default();
    let field = |name: &str| {
        status
            .lines()
            .find_map(|line| line.strip_prefix(name)?.trim().strip_suffix("kB")?.trim().parse::<u64>().ok())
            .map_or_else(|| "?".to_string(), |kib| (kib / 1024).to_string())
    };
    (field("RssAnon:"), field("VmHWM:"))
}

/// Runs `f` and, if the instrument is enabled, prints one line to stderr: `doeff-linter-timing: <label> wall=<seconds> cpu=<seconds> rss=<MiB> hwm=<MiB>`.
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
    let (rss, hwm) = memory_mib();
    eprintln!("doeff-linter-timing: {label} wall={:.3} cpu={spent} rss={rss} hwm={hwm}", wall.elapsed().as_secs_f64());
    out
}

/// Whether the instrument is enabled (for counts that cost a pass of their own, made only when they are printed).
pub fn enabled() -> bool {
    std::env::var_os(ENV).is_some()
}

/// If the instrument is enabled, prints one line to stderr: `doeff-linter-timing: <label> <field>=<number>` (not a stage time but a count of things, e.g. the number of files whose contents were actually read).
pub fn count(label: &str, field: &str, value: usize) {
    if std::env::var_os(ENV).is_some() {
        eprintln!("doeff-linter-timing: {label} {field}={value}");
    }
}
