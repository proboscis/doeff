#![forbid(unsafe_code)]

use std::process::ExitCode;

fn main() -> ExitCode {
    doeff_complexity::run_cli(std::env::args().skip(1))
}
