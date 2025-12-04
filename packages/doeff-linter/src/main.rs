//! doeff-linter CLI

use clap::Parser;
use colored::*;
use doeff_linter::{
    collect_python_files, config, lint_files_parallel, logging::{LintLogEntry, LintLogger}, models::Severity, rules,
};
use std::collections::BTreeMap;
use std::io::{self, Read};
use std::path::Path;
use std::process::{Command, ExitCode};

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

    /// Run as Cursor stop hook (reads JSON from stdin, outputs hook response)
    #[arg(long)]
    hook: bool,

    /// Only lint git-modified files (tracked and untracked)
    #[arg(long)]
    modified: bool,

    /// Log violations to a file (JSON Lines format) for later analysis
    /// Defaults to ".doeff-lint.jsonl". Use --no-log to disable.
    #[arg(long, default_value = ".doeff-lint.jsonl")]
    log_file: Option<String>,

    /// Disable logging to file
    #[arg(long)]
    no_log: bool,
}

/// Cursor hook input structure
#[derive(serde::Deserialize, Debug)]
struct HookInput {
    #[allow(dead_code)]
    status: Option<String>,
    #[allow(dead_code)]
    loop_count: Option<u32>,
    workspace_roots: Option<Vec<String>>,
}

/// Cursor hook output structure
#[derive(serde::Serialize)]
struct HookOutput {
    #[serde(skip_serializing_if = "Option::is_none")]
    followup_message: Option<String>,
}

fn main() -> ExitCode {
    let args = Args::parse();

    if args.hook {
        return run_as_hook(&args);
    }

    run_normal(&args)
}

fn run_as_hook(args: &Args) -> ExitCode {
    // Read JSON from stdin
    let mut input = String::new();
    if let Err(e) = io::stdin().read_to_string(&mut input) {
        eprintln!("Failed to read stdin: {}", e);
        // Output empty response and exit
        println!("{}", serde_json::json!({}));
        return ExitCode::SUCCESS;
    }

    // Parse hook input
    let hook_input: HookInput = match serde_json::from_str(&input) {
        Ok(v) => v,
        Err(e) => {
            eprintln!("Failed to parse hook input: {}", e);
            println!("{}", serde_json::json!({}));
            return ExitCode::SUCCESS;
        }
    };

    // Get paths to lint from workspace_roots or use current directory
    let paths: Vec<String> = hook_input
        .workspace_roots
        .unwrap_or_else(|| vec![".".to_string()]);

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

    // Get rules
    let all_rules = rules::get_enabled_rules(enabled_rules.as_deref());

    // Collect files
    let files = collect_python_files(&paths, &exclude_patterns);

    if files.is_empty() {
        // No files to lint, output empty response
        println!("{}", serde_json::json!({}));
        return ExitCode::SUCCESS;
    }

    // Lint files
    let results = lint_files_parallel(&files, &all_rules);

    // Group and count violations
    let mut grouped: BTreeMap<String, Vec<ViolationSummary>> = BTreeMap::new();
    let mut error_count = 0;

    for result in &results {
        for v in &result.violations {
            if v.severity == Severity::Error {
                error_count += 1;
            }
            let line = get_line_from_offset(&result.file_path, v.offset);
            let source_line = read_source_line(&v.file_path, line);
            grouped
                .entry(v.rule_id.clone())
                .or_default()
                .push(ViolationSummary {
                    file_path: v.file_path.clone(),
                    line,
                    source_line,
                });
        }
    }

    // Log results (enabled by default, use --no-log to disable)
    if !args.no_log {
        let log_file = args.log_file.clone().or_else(|| config.as_ref().and_then(|c| c.log_file.clone()));
        if let Some(log_path) = log_file {
            let enabled_rule_ids: Vec<String> = all_rules.iter().map(|r| r.rule_id().to_string()).collect();
            let log_entry = LintLogEntry::from_results(&results, "hook", Some(enabled_rule_ids));
            match LintLogger::new(&log_path) {
                Ok(mut logger) => {
                    if let Err(e) = logger.log(&log_entry) {
                        eprintln!("Warning: Failed to write to log file: {}", e);
                    }
                }
                Err(e) => {
                    eprintln!("Warning: Failed to create log file: {}", e);
                }
            }
        }
    }

    // If there are errors, create a followup message
    let output = if error_count > 0 {
        let message = build_followup_message(&grouped);
        HookOutput {
            followup_message: Some(message),
        }
    } else {
        HookOutput {
            followup_message: None,
        }
    };

    // Output hook response
    println!("{}", serde_json::to_string(&output).unwrap_or_else(|_| "{}".to_string()));
    ExitCode::SUCCESS
}

struct ViolationSummary {
    file_path: String,
    line: usize,
    source_line: String,
}

fn build_followup_message(grouped: &BTreeMap<String, Vec<ViolationSummary>>) -> String {
    let mut message = String::from("The doeff-linter found code quality issues that need to be fixed:\n\n");

    for (rule_id, violations) in grouped {
        let rule_info = get_rule_info(rule_id);
        message.push_str(&format!("## {} - {}\n", rule_id, rule_info.name));
        message.push_str(&format!("**Problem:** {}\n", rule_info.description));
        message.push_str(&format!("**How to fix:** {}\n\n", rule_info.fix));

        // Show up to 5 examples per rule
        let examples: Vec<_> = violations.iter().take(5).collect();
        for v in &examples {
            message.push_str(&format!("- `{}:{}`", v.file_path, v.line));
            if !v.source_line.is_empty() {
                message.push_str(&format!(" → `{}`", v.source_line));
            }
            message.push('\n');
        }
        if violations.len() > 5 {
            message.push_str(&format!("- ... and {} more\n", violations.len() - 5));
        }
        message.push('\n');
    }

    message.push_str("Please fix these issues following the suggestions above.");
    message
}

fn run_normal(args: &Args) -> ExitCode {
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
    let files = if args.modified {
        // Get git-modified files
        let base_path = args.paths.first().map(|s| s.as_str()).unwrap_or(".");
        let modified_files = get_git_modified_files(base_path);
        
        if args.verbose {
            eprintln!("Git modified files: {:?}", modified_files);
        }
        
        // Filter by exclude patterns and convert to PathBuf
        modified_files
            .into_iter()
            .filter(|f| {
                !exclude_patterns.iter().any(|pat| f.contains(pat))
            })
            .map(std::path::PathBuf::from)
            .collect()
    } else {
        collect_python_files(&args.paths, &exclude_patterns)
    };

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

    // Log results (enabled by default, use --no-log to disable)
    if !args.no_log {
        let log_file = args.log_file.clone().or_else(|| config.as_ref().and_then(|c| c.log_file.clone()));
        if let Some(log_path) = log_file {
            let run_mode = if args.modified { "modified" } else { "normal" };
            let enabled_rule_ids: Vec<String> = all_rules.iter().map(|r| r.rule_id().to_string()).collect();
            let log_entry = LintLogEntry::from_results(&results, run_mode, Some(enabled_rule_ids));
            match LintLogger::new(&log_path) {
                Ok(mut logger) => {
                    if let Err(e) = logger.log(&log_entry) {
                        eprintln!("Warning: Failed to write to log file: {}", e);
                    } else if args.verbose {
                        eprintln!("Logged {} violations to {}", log_entry.total_violations, log_path);
                    }
                }
                Err(e) => {
                    eprintln!("Warning: Failed to create log file: {}", e);
                }
            }
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
        "DOEFF011" => RuleInfo {
            name: "No Flag/Mode Arguments",
            description: "Functions and dataclasses use flag/mode arguments instead of callbacks or protocol objects.",
            fix: "Accept a callback or protocol object. Example: instead of `def process(data, use_cache: bool)`, use `def process(data, cache: CacheProtocol)` or `def process(data, get_cached: Callable[[Data], Result])`.",
        },
        "DOEFF012" => RuleInfo {
            name: "No Append Loop Pattern",
            description: "Empty list initialization followed by for-loop append obscures the data transformation pipeline.",
            fix: "Use list comprehension: `data = [process(x) for x in items]`. For complex logic, extract to a named function. If mutation is required (queue/stack ops, BFS/DFS, dynamic algorithms), add `# noqa: DOEFF012` to the for-loop line.",
        },
        "DOEFF013" => RuleInfo {
            name: "Prefer Maybe Monad",
            description: "Optional[X] or X | None type annotations should use doeff's Maybe monad for explicit null handling.",
            fix: "Use `Maybe[X]` instead of `Optional[X]`. Import with `from doeff import Maybe, Some, NOTHING`. Use `Maybe.from_optional(value)` to convert existing Optional values.",
        },
        "DOEFF014" => RuleInfo {
            name: "No Try-Except Blocks",
            description: "Using try-except blocks hides error handling flow. Use doeff's error handling effects instead.",
            fix: "Use `Safe(program)` to get a Result, `program.recover(fallback)` for fallbacks, `program.first_success(alt1, alt2)` for alternatives, or `Catch(program, handler)` to transform errors.",
        },
        "DOEFF015" => RuleInfo {
            name: "No Zero-Argument Program Entrypoints",
            description: "Program entrypoints should not be created by zero-argument factory functions.",
            fix: "Pass explicit arguments to make configuration visible: `process(data=input, threshold=0.5)`.",
        },
        "DOEFF016" => RuleInfo {
            name: "No Relative Imports",
            description: "Relative imports make code harder to understand and refactor.",
            fix: "Use absolute imports: `from mypackage.module import func` instead of `from .module import func`.",
        },
        "DOEFF017" => RuleInfo {
            name: "No Program Type Parameters",
            description: "@do functions should accept type T, not Program[T]. Program[T] prevents auto-unwrapping.",
            fix: "Change parameter type from `Program[T]` to `T`. If intentional (Program transforms), suppress with `# noqa: DOEFF017`.",
        },
        "DOEFF018" => RuleInfo {
            name: "No Ask in Try Block",
            description: "Using `yield Ask(...)` inside try blocks can cause unexpected behavior.",
            fix: "Move the Ask outside the try block, or use doeff's error handling effects like `Safe()` or `recover()`.",
        },
        "DOEFF019" => RuleInfo {
            name: "No Ask with Fallback",
            description: "Using fallback values with Ask defeats the purpose of dependency injection.",
            fix: "Remove the fallback and ensure dependencies are properly provided at runtime.",
        },
        "DOEFF020" => RuleInfo {
            name: "Program Naming Convention",
            description: "Program type variables should use 'p_' prefix for consistency.",
            fix: "Rename the variable: `data_program` → `p_data`.",
        },
        "DOEFF021" => RuleInfo {
            name: "No __all__ Declaration",
            description: "This project defaults to exporting everything from modules.",
            fix: "Remove the `__all__` declaration. If needed for specific reasons, use `# noqa: DOEFF021`.",
        },
        "DOEFF022" => RuleInfo {
            name: "Prefer @do Decorated Functions",
            description: "Functions should use @do decorator to enable structured effects and logging with `yield slog`.",
            fix: "Add @do decorator and use `yield slog(\"message\", key=value)` for structured logging. If intentional, suppress with `# noqa: DOEFF022`.",
        },
        "DOEFF023" => RuleInfo {
            name: "Pipeline Marker Required",
            description: "@do functions used to create Program entrypoints must have `# doeff: pipeline` marker.",
            fix: "Add `# doeff: pipeline` marker after @do decorator, def line, or in docstring to acknowledge pipeline-oriented programming.",
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
    #[allow(dead_code)]
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

/// Get list of git-modified Python files (both tracked and untracked)
fn get_git_modified_files(base_path: &str) -> Vec<String> {
    let mut files = Vec::new();

    // Get modified tracked files (staged and unstaged)
    // git diff --name-only HEAD (shows all changes vs HEAD)
    // git diff --name-only (shows unstaged changes)
    // git diff --name-only --cached (shows staged changes)
    // We use git status --porcelain to get both
    if let Ok(output) = Command::new("git")
        .args(["status", "--porcelain", "-uall"])
        .current_dir(base_path)
        .output()
    {
        if output.status.success() {
            let stdout = String::from_utf8_lossy(&output.stdout);
            for line in stdout.lines() {
                // Format: XY filename or XY orig -> renamed
                // X = staged status, Y = unstaged status
                // ?? = untracked, M = modified, A = added, etc.
                if line.len() > 3 {
                    let file_part = &line[3..];
                    // Handle renamed files (take the new name after "->")
                    let filename = if let Some(pos) = file_part.find(" -> ") {
                        &file_part[pos + 4..]
                    } else {
                        file_part
                    };
                    
                    // Only include Python files
                    if filename.ends_with(".py") {
                        let full_path = Path::new(base_path).join(filename);
                        if full_path.exists() {
                            files.push(full_path.to_string_lossy().to_string());
                        }
                    }
                }
            }
        }
    }

    files
}
