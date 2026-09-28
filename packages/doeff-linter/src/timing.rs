//! 処理ステージごとの時間を stderr へ出す計器(環境変数 `DOEFF_LINTER_TIMING` が在る時だけ)。
//!
//! 書き込み直後の hook の 1 回が CPU 3 秒かかっていた(agora-redesign #1022)— どの処理ステージが重いかを、
//! 答え(stdout の JSON)を変えずに測るため。並列の処理は壁時計だと重さが隠れるので、process の CPU 秒
//! (`/proc/self/stat` の utime + stime)の差も出す。読めない機体では CPU 秒の欄を `?` にする。

use std::time::Instant;

const ENV: &str = "DOEFF_LINTER_TIMING";

fn cpu_seconds() -> Option<f64> {
    let stat = std::fs::read_to_string("/proc/self/stat").ok()?;
    // comm は括弧の中に空白を含みうるので、最後の ')' の後ろから数える(utime = 14 番目・stime = 15 番目)。
    let rest = stat.get(stat.rfind(')')? + 2..)?;
    let fields: Vec<&str> = rest.split_whitespace().collect();
    let ticks: f64 = fields.get(11)?.parse::<f64>().ok()? + fields.get(12)?.parse::<f64>().ok()?;
    Some(ticks / 100.0)
}

/// `f` を走らせ、計器が有効なら `doeff-linter-timing: <label> wall=<秒> cpu=<秒>` を 1 行 stderr へ出す。
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
