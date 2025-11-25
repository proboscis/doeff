//! doeff-linter CLI

use clap::Parser;
use colored::*;
use doeff_linter::{
    collect_python_files, config, lint_files_parallel, models::Severity, rules,
};
use std::collections::BTreeMap;
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
            print_text_grouped(&results);
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

/// Rule info with description and fix suggestion
struct RuleInfo {
    name: &'static str,
    description: &'static str,
    fix: &'static str,
}

fn get_rule_info(rule_id: &str) -> RuleInfo {
    match rule_id {
        "DOEFF001" => RuleInfo {
            name: "Builtin Shadowing",
            description: "A function parameter or variable shadows a Python builtin (e.g., `list`, `dict`, `id`).",
            fix: "Rename the variable to avoid shadowing: `items` instead of `list`, `mapping` instead of `dict`.",
        },
        "DOEFF002" => RuleInfo {
            name: "Mutable Attribute Naming",
            description: "A mutable class attribute (list, dict, set) doesn't follow the `_mut_` naming convention.",
            fix: "Prefix mutable attributes with `_mut_`: `self._mut_items = []` instead of `self.items = []`.",
        },
        "DOEFF003" => RuleInfo {
            name: "Max Mutable Attributes",
            description: "A class has too many mutable attributes, indicating potential design issues.",
            fix: "Refactor the class to reduce mutable state, or split into smaller classes.",
        },
        "DOEFF004" => RuleInfo {
            name: "No os.environ Access",
            description: "Direct access to `os.environ` breaks dependency injection principles.",
            fix: "Inject configuration as function parameters or use a config dataclass instead.",
        },
        "DOEFF005" => RuleInfo {
            name: "No Setter Methods",
            description: "Setter methods (set_*, @property.setter) violate immutability principles.",
            fix: "Use immutable patterns: return new instances with modified values instead of mutating.",
        },
        "DOEFF006" => RuleInfo {
            name: "No Tuple Returns",
            description: "Returning raw tuples reduces code readability and type safety.",
            fix: "Use a dataclass or NamedTuple: `@dataclass class Result: value: int; error: str`.",
        },
        "DOEFF007" => RuleInfo {
            name: "No Mutable Argument Mutations",
            description: "Mutating function arguments (list.append, dict.update) causes side effects.",
            fix: "Create a copy first: `items = items.copy(); items.append(x)` or return new collections.",
        },
        "DOEFF008" => RuleInfo {
            name: "No Dataclass Attribute Mutation",
            description: "Mutating dataclass attributes after creation breaks immutability.",
            fix: "Use `frozen=True` dataclasses and `dataclasses.replace()` to create modified copies.",
        },
        "DOEFF009" => RuleInfo {
            name: "Missing Return Type Annotation",
            description: "Functions without return type annotations reduce code clarity and type safety.",
            fix: "Add return type: `def foo() -> int:` or `def bar() -> None:` for no return value.",
        },
        "DOEFF010" => RuleInfo {
            name: "Test File Placement",
            description: "Test files should be in a `tests/` directory, not mixed with source code.",
            fix: "Move test files to a dedicated `tests/` directory at the project root.",
        },
        _ => RuleInfo {
            name: "Unknown Rule",
            description: "Unknown rule violation.",
            fix: "Check the documentation for more information.",
        },
    }
}

/// Violation info for grouping
struct ViolationInfo {
    file_path: String,
    line: usize,
    severity: Severity,
    message: String,
    source_line: String,
}

/// Read a specific line from a file
fn read_source_line(file_path: &str, line_num: usize) -> String {
    if let Ok(content) = std::fs::read_to_string(file_path) {
        content
            .lines()
            .nth(line_num.saturating_sub(1))
            .map(|s| s.trim().to_string())
            .unwrap_or_default()
    } else {
        String::new()
    }
}

fn print_text_grouped(results: &[doeff_linter::models::LintResult]) {
    // Group violations by rule ID
    let mut grouped: BTreeMap<String, Vec<ViolationInfo>> = BTreeMap::new();

    for result in results {
        if let Some(error) = &result.error {
            eprintln!("{}: {}", result.file_path.red(), error);
            continue;
        }

        for v in &result.violations {
            let line = get_line_from_offset(&result.file_path, v.offset);
            let source_line = read_source_line(&v.file_path, line);
            grouped
                .entry(v.rule_id.clone())
                .or_default()
                .push(ViolationInfo {
                    file_path: v.file_path.clone(),
                    line,
                    severity: v.severity,
                    message: v.message.clone(),
                    source_line,
                });
        }
    }

    // Print grouped output
    for (rule_id, violations) in &grouped {
        let rule_info = get_rule_info(rule_id);
        let count = violations.len();
        
        // Determine severity color for header
        let severity = violations.first().map(|v| v.severity).unwrap_or(Severity::Warning);
        let header_color = match severity {
            Severity::Error => "error".red().bold(),
            Severity::Warning => "warning".yellow().bold(),
            Severity::Info => "info".blue().bold(),
        };

        println!(
            "\n{} {} - {} ({} occurrence{})",
            header_color,
            rule_id.cyan().bold(),
            rule_info.name.white().bold(),
            count,
            if count == 1 { "" } else { "s" }
        );
        println!("{}", "─".repeat(80).dimmed());
        println!("  {} {}", "What:".bright_white(), rule_info.description);
        println!("  {}  {}", "Fix:".bright_green(), rule_info.fix);
        println!();

        for v in violations {
            println!(
                "    {}:{}",
                v.file_path.dimmed(),
                v.line.to_string().yellow()
            );
            if !v.source_line.is_empty() {
                println!("      {}", v.source_line.bright_white());
            }
        }
    }
}

fn print_json(results: &[doeff_linter::models::LintResult]) {
    // Group by rule for JSON output too
    let mut grouped: BTreeMap<String, Vec<serde_json::Value>> = BTreeMap::new();

    for result in results {
        for v in &result.violations {
            let line = get_line_from_offset(&result.file_path, v.offset);
            let source_line = read_source_line(&v.file_path, line);
            grouped
                .entry(v.rule_id.clone())
                .or_default()
                .push(serde_json::json!({
                    "file": v.file_path,
                    "line": line,
                    "severity": format!("{}", v.severity),
                    "message": v.message,
                    "source": source_line,
                }));
        }
    }

    let output: Vec<serde_json::Value> = grouped
        .into_iter()
        .map(|(rule_id, violations)| {
            let rule_info = get_rule_info(&rule_id);
            serde_json::json!({
                "rule": rule_id,
                "name": rule_info.name,
                "description": rule_info.description,
                "fix": rule_info.fix,
                "count": violations.len(),
                "violations": violations,
            })
        })
        .collect();

    println!("{}", serde_json::to_string_pretty(&output).unwrap_or_default());
}

fn get_line_from_offset(file_path: &str, offset: usize) -> usize {
    if let Ok(content) = std::fs::read_to_string(file_path) {
        doeff_linter::noqa::offset_to_line(&content, offset)
    } else {
        1
    }
}
