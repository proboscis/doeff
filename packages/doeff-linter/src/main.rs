//! doeff-linter CLI

use clap::Parser;
use colored::*;
use doeff_linter::{
    collect_python_files, config, lint_files_parallel, models::Severity, rules,
};
use std::process::ExitCode;

#[derive(Parser, Debug)]
#[command(name = "doeff-linter")]
#[command(version, about = "A linter for enforcing code quality and immutability patterns")]
struct Args {
    /// Files or directories to lint
    #[arg(default_value = ".")]
    paths: Vec<String>,

    /// Enable specific rules (comma-separated, or "ALL")
    #[arg(long, value_delimiter = ',')]
    enable: Vec<String>,

    /// Disable specific rules (comma-separated)
    #[arg(long, value_delimiter = ',')]
    disable: Vec<String>,

    /// Exclude paths matching patterns
    #[arg(long, value_delimiter = ',')]
    exclude: Vec<String>,

    /// Output format: text, json
    #[arg(long, default_value = "text")]
    output_format: String,

    /// Ignore pyproject.toml configuration
    #[arg(long)]
    no_config: bool,

    /// Show verbose output
    #[arg(short, long)]
    verbose: bool,
}

fn main() -> ExitCode {
    let args = Args::parse();

    // Load config
    let config = if args.no_config {
        None
    } else {
        config::load_config(None)
    };

    // Merge CLI args with config
    let (enabled_rules, exclude_patterns) = config::merge_config(
        config.as_ref(),
        &args.enable,
        &args.disable,
        &args.exclude,
    );

    if args.verbose {
        eprintln!("Enabled rules: {:?}", enabled_rules);
        eprintln!("Exclude patterns: {:?}", exclude_patterns);
    }

    // Get rules
    let all_rules = rules::get_enabled_rules(enabled_rules.as_deref());

    if args.verbose {
        eprintln!(
            "Active rules: {}",
            all_rules
                .iter()
                .map(|r| r.rule_id())
                .collect::<Vec<_>>()
                .join(", ")
        );
    }

    // Collect files
    let files = collect_python_files(&args.paths, &exclude_patterns);

    if args.verbose {
        eprintln!("Found {} Python files", files.len());
    }

    if files.is_empty() {
        eprintln!("No Python files found");
        return ExitCode::SUCCESS;
    }

    // Lint files
    let results = lint_files_parallel(&files, &all_rules);

    // Count violations
    let mut error_count = 0;
    let mut warning_count = 0;
    let mut info_count = 0;

    for result in &results {
        for v in &result.violations {
            match v.severity {
                Severity::Error => error_count += 1,
                Severity::Warning => warning_count += 1,
                Severity::Info => info_count += 1,
            }
        }
    }

    // Output results
    match args.output_format.as_str() {
        "json" => {
            print_json(&results);
        }
        _ => {
            print_text(&results, args.verbose);
        }
    }

    // Print summary
    let total = error_count + warning_count + info_count;
    if total > 0 {
        eprintln!(
            "\nFound {} issue(s): {} error(s), {} warning(s), {} info",
            total, error_count, warning_count, info_count
        );
    } else if args.verbose {
        eprintln!("\nNo issues found.");
    }

    // Return exit code
    if error_count > 0 {
        ExitCode::from(1)
    } else {
        ExitCode::SUCCESS
    }
}

fn print_text(results: &[doeff_linter::models::LintResult], verbose: bool) {
    for result in results {
        if let Some(error) = &result.error {
            eprintln!("{}: {}", result.file_path.red(), error);
            continue;
        }

        for v in &result.violations {
            let severity_str = match v.severity {
                Severity::Error => "error".red().bold(),
                Severity::Warning => "warning".yellow().bold(),
                Severity::Info => "info".blue().bold(),
            };

            println!(
                "{}:{}: {} [{}]: {}",
                v.file_path,
                get_line_from_offset(&result.file_path, v.offset),
                severity_str,
                v.rule_id.cyan(),
                v.message
            );
        }
    }
}

fn print_json(results: &[doeff_linter::models::LintResult]) {
    let mut violations = Vec::new();

    for result in results {
        for v in &result.violations {
            violations.push(serde_json::json!({
                "file": v.file_path,
                "line": get_line_from_offset(&result.file_path, v.offset),
                "rule": v.rule_id,
                "severity": format!("{}", v.severity),
                "message": v.message,
            }));
        }
    }

    println!("{}", serde_json::to_string_pretty(&violations).unwrap_or_default());
}

fn get_line_from_offset(file_path: &str, offset: usize) -> usize {
    if let Ok(content) = std::fs::read_to_string(file_path) {
        doeff_linter::noqa::offset_to_line(&content, offset)
    } else {
        1
    }
}



