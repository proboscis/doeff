//! doeff-linter: A linter for enforcing code quality and immutability patterns
//!
//! This crate provides lint rules for Python code, focusing on:
//! - Immutability patterns
//! - Type safety
//! - Code organization

pub mod build_info;
// Names read by external crates (main.rs and tests). Inside the crate, build_info is read directly (reading lib.rs back would make a dependency cycle — #2119).
pub use build_info::{BUILD_COMMIT, VERSION_TEXT};

pub mod baseline;
pub mod check_slot;
pub mod commit_hook;
pub mod config;
pub mod editor;
pub mod logging;
pub mod models;
pub mod noqa;
pub mod origins;
pub mod population;
pub mod position;
pub mod project;
pub mod report;
pub mod rule_info;
pub mod rules;
pub mod timing;
pub mod stats;
pub mod utils;

use models::{LintResult, RuleContext, Severity, Violation};
use noqa::{offset_to_line, NoqaDirectives};
use rayon::prelude::*;
use rules::base::{LintRule, RuleReach};
use rustpython_ast::{Mod, Stmt};
use rustpython_parser::{parse, Mode};
use std::path::Path;
use walkdir::WalkDir;

/// Lint a single file and return the results
pub fn lint_file(
    file_path: &Path,
    rules: &[Box<dyn LintRule>],
) -> LintResult {
    let path_str = file_path.to_string_lossy().to_string();

    let source = match std::fs::read_to_string(file_path) {
        Ok(s) => s,
        Err(e) => return LintResult::with_error(path_str, format!("Failed to read file: {}", e)),
    };

    lint_source_at(&path_str, &source, rules)
}

/// Apply the Python rules to the source of a file with a known path, and apply the declaration population of the package that owns the file (crate::population — the layer
/// `:exempt` exclusions, and DOEFF032 for business imports of excluded layers). Run entry points (a list of files, a single editor file) go through here —
/// lint_source does not know the population (it is the entry point for unit-testing a rule).
pub fn lint_source_at(
    file_path: &str,
    source: &str,
    rules: &[Box<dyn LintRule>],
) -> LintResult {
    let population = population::population_of(Path::new(file_path));
    let mut result = lint_source(file_path, source, rules);
    let named = rules.iter().any(|rule| rule.rule_id() == population::BUSINESS_IMPORT_RULE_ID);
    match population {
        Ok(population::FilePopulation::Plain) => {}
        Ok(population::FilePopulation::Exempt { layer, rules: exempt, forbid_modules, declaration }) => {
            result.violations.retain(|v| !exempt.contains(&v.rule_id));
            if named {
                if let Ok(ast) = parse(source, Mode::Module, file_path) {
                    let noqa = NoqaDirectives::parse(source);
                    result.violations.extend(
                        population::business_import_violations(file_path, &ast, &layer, &forbid_modules, &declaration)
                            .into_iter()
                            .filter(|v| !noqa.is_suppressed(offset_to_line(source, v.offset), &v.rule_id)),
                    );
                }
            }
        }
        // If the declaration cannot be read, nothing is excluded (every rule applies as is), and the failure to read is named as a finding — making it a file error (the error field)
        // would make the text output hide that file's findings, and it would not count toward the exit code.
        Err(reason) => {
            if named {
                result.violations.push(Violation::new(
                    population::BUSINESS_IMPORT_RULE_ID.to_string(),
                    reason,
                    0,
                    file_path.to_string(),
                    Severity::Error,
                ));
            }
        }
    }
    result
}

/// Lint source code and return the results
pub fn lint_source(
    file_path: &str,
    source: &str,
    rules: &[Box<dyn LintRule>],
) -> LintResult {
    let ast = match parse(source, Mode::Module, file_path) {
        Ok(ast) => ast,
        Err(e) => return LintResult::with_error(file_path.to_string(), format!("Parse error: {}", e)),
    };

    let noqa = NoqaDirectives::parse(source);
    let mut result = LintResult::new(file_path.to_string());

    // Add noqa parsing warnings as violations
    for warning in &noqa.warnings {
        let offset = line_to_offset(source, warning.line);
        result.violations.push(Violation::new(
            noqa::NOQA_RULE_ID.to_string(),
            format!("{}\n  Suggestion: {}", warning.message, warning.suggestion),
            offset,
            file_path.to_string(),
            Severity::Warning,
        ));
    }

    if let Mod::Module(module) = &ast {
        // Rules that look at the whole module run once per file (with the module's first statement as stmt) — #2858.
        if let Some(first) = module.body.first() {
            let context = RuleContext {
                stmt: first,
                file_path,
                source,
                ast: &ast,
            };
            for rule in rules.iter().filter(|rule| rule.reach() == RuleReach::Module) {
                apply_rule(rule.as_ref(), &context, &noqa, &mut result.violations);
            }
        }
        for stmt in &module.body {
            check_stmt_recursive(stmt, true, file_path, source, &ast, rules, &noqa, &mut result.violations);
        }
    }

    result
}

/// Whether the body's recursion passes this statement to the rule — follows the unit the rule looks at (RuleReach). Passing nested statements to a rule that
/// walks nesting by itself counts the same finding once per nesting depth.
fn passes_statement(reach: RuleReach, top_level: bool) -> bool {
    match reach {
        RuleReach::Statement => true,
        RuleReach::Subtree => top_level,
        RuleReach::Module => false,
    }
}

/// Apply a rule once, and accumulate the findings, excluding those suppressed by noqa.
fn apply_rule(
    rule: &dyn LintRule,
    context: &RuleContext,
    noqa: &NoqaDirectives,
    violations: &mut Vec<Violation>,
) {
    violations.extend(
        rule.check(context)
            .into_iter()
            .filter(|v| !noqa.is_suppressed(offset_to_line(context.source, v.offset), &v.rule_id)),
    );
}

/// Convert line number (1-indexed) to byte offset
fn line_to_offset(source: &str, line: usize) -> usize {
    source
        .lines()
        .take(line.saturating_sub(1))
        .map(|l| l.len() + 1) // +1 for newline
        .sum()
}

/// Apply one statement to the rules that handle its unit, and descend into nested statements. `top_level` = a statement at the module's top level.
#[allow(clippy::too_many_arguments)]
fn check_stmt_recursive(
    stmt: &Stmt,
    top_level: bool,
    file_path: &str,
    source: &str,
    ast: &Mod,
    rules: &[Box<dyn LintRule>],
    noqa: &NoqaDirectives,
    violations: &mut Vec<Violation>,
) {
    let context = RuleContext {
        stmt,
        file_path,
        source,
        ast,
    };

    for rule in rules.iter().filter(|rule| passes_statement(rule.reach(), top_level)) {
        apply_rule(rule.as_ref(), &context, noqa, violations);
    }

    // Recursively check nested statements (the list of nested bodies is in one place in utils — rules that walk a module by themselves read the same list)
    for body in utils::nested_bodies(stmt) {
        for s in body {
            check_stmt_recursive(s, false, file_path, source, ast, rules, noqa, violations);
        }
    }
}

/// Collect Python files from paths
///
/// When `force_exclude` is true, exclusion patterns are also applied to explicitly
/// specified file paths. By default (force_exclude = false), exclusions only apply
/// when scanning directories.
pub fn collect_python_files(paths: &[String], exclude_patterns: &[String]) -> Vec<std::path::PathBuf> {
    collect_python_files_with_options(paths, exclude_patterns, false)
}

/// Collect Python files from paths with explicit force_exclude option
pub fn collect_python_files_with_options(
    paths: &[String],
    exclude_patterns: &[String],
    force_exclude: bool,
) -> Vec<std::path::PathBuf> {
    let mut files = Vec::new();

    for path in paths {
        let p = Path::new(path);
        if p.is_file() {
            if p.extension().map_or(false, |e| e == "py") {
                // When force_exclude is true, apply exclusion to explicit file paths
                if force_exclude && should_exclude(p, exclude_patterns) {
                    continue;
                }
                files.push(p.to_path_buf());
            }
        } else if p.is_dir() {
            for entry in WalkDir::new(p)
                .into_iter()
                .filter_entry(|e| !should_exclude(e.path(), exclude_patterns))
                .filter_map(|e| e.ok())
            {
                let path = entry.path();
                if path.is_file() && path.extension().map_or(false, |e| e == "py") {
                    files.push(path.to_path_buf());
                }
            }
        }
    }

    one_path_per_file(files)
}

/// Merge paths that point to the same real file (a symlink and its target, or another symlink to the same file) into one.
///
/// The path to name: the real (non-symlink) path if one is among the targets, otherwise the first path seen. The order is
/// the order in which each file was first seen. A path whose real file cannot be resolved (such as a broken symlink) counts as one by itself.
/// This is the single point that prevents counting the same finding once per path; every route that collects files (dir traversal, named files,
/// `--modified`) goes through here. Whether a rule applies is decided by the real file
/// (the population declaration is also looked up after resolving symlinks), so merging does not shrink the range examined.
pub fn one_path_per_file(files: Vec<std::path::PathBuf>) -> Vec<std::path::PathBuf> {
    let mut seen: Vec<std::path::PathBuf> = Vec::new();
    let mut chosen: std::collections::HashMap<std::path::PathBuf, std::path::PathBuf> =
        std::collections::HashMap::new();
    for file in files {
        let real = std::fs::canonicalize(&file).unwrap_or_else(|_| file.clone());
        match chosen.get(&real) {
            None => {
                seen.push(real.clone());
                chosen.insert(real, file);
            }
            Some(kept) => {
                if is_symlink(kept) && !is_symlink(&file) {
                    chosen.insert(real, file);
                }
            }
        }
    }
    seen.into_iter().filter_map(|real| chosen.remove(&real)).collect()
}

/// Whether the path itself is a symlink (looked at without following it). This is asked only when two paths resolved to the same real file,
/// and then both are readable — unreadable happens only when removed during the run, and naming by either path gives the same result.
fn is_symlink(path: &Path) -> bool {
    std::fs::symlink_metadata(path).map_or(false, |meta| meta.file_type().is_symlink())
}

/// Whether the path matches an exclude pattern — only when one component of the path (a dir name or a file name) equals the pattern.
///
/// Substring matches are not looked at: formerly a file name (and a dir name during dir traversal) was excluded merely by "containing" the pattern, and the default
/// `build`, `dist`, and `.git` silently put names like `doeff_indexer_build_backend.py`, `verify_dist_metadata.py`, and `.github/`
/// outside the judgment (not 0 findings, but not judged).
pub fn should_exclude(path: &Path, patterns: &[String]) -> bool {
    path.components().any(|component| {
        component
            .as_os_str()
            .to_str()
            .map_or(false, |part| patterns.iter().any(|pattern| part == pattern))
    })
}

/// Lint multiple files in parallel
pub fn lint_files_parallel(
    files: &[std::path::PathBuf],
    rules: &[Box<dyn LintRule>],
) -> Vec<LintResult> {
    files
        .par_iter()
        .map(|file| lint_file(file, rules))
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::rules::get_all_rules;

    /// Test case for noqa verification
    struct NoqaTestCase {
        rule_id: &'static str,
        /// Code that triggers the rule (without noqa)
        triggering_code: &'static str,
        /// Line number where the violation should occur (1-indexed)
        violation_line: usize,
    }

    /// Get test cases for all rules
    fn get_noqa_test_cases() -> Vec<NoqaTestCase> {
        vec![
            // DOEFF001: Builtin Shadowing
            NoqaTestCase {
                rule_id: "DOEFF001",
                triggering_code: "def dict():\n    return {}",
                violation_line: 1,
            },
            // DOEFF002: Mutable Attribute Naming
            NoqaTestCase {
                rule_id: "DOEFF002",
                triggering_code: r#"class Foo:
    def __init__(self):
        self.data = []
    def update(self):
        self.data = [1, 2, 3]"#,
                violation_line: 5,
            },
            // DOEFF003: Max Mutable Attributes
            NoqaTestCase {
                rule_id: "DOEFF003",
                triggering_code: r#"class Foo:
    def __init__(self):
        self.mut_a = 1
        self.mut_b = 2
        self.mut_c = 3
        self.mut_d = 4
        self.mut_e = 5
        self.mut_f = 6"#,
                violation_line: 1,
            },
            // DOEFF004: No os.environ Access
            NoqaTestCase {
                rule_id: "DOEFF004",
                triggering_code: "import os\nkey = os.environ[\"KEY\"]",
                violation_line: 2,
            },
            // DOEFF005: No Setter Methods
            NoqaTestCase {
                rule_id: "DOEFF005",
                triggering_code: r#"class Foo:
    def set_value(self, v):
        pass"#,
                violation_line: 2,
            },
            // DOEFF006: No Tuple Returns
            NoqaTestCase {
                rule_id: "DOEFF006",
                triggering_code: "def foo() -> tuple[int, str]:\n    return (1, \"a\")",
                violation_line: 1,
            },
            // DOEFF007: No Mutable Argument Mutations
            NoqaTestCase {
                rule_id: "DOEFF007",
                triggering_code: r#"def foo(items):
    items.append(1)"#,
                violation_line: 2,
            },
            // DOEFF008: No Dataclass Attribute Mutation
            NoqaTestCase {
                rule_id: "DOEFF008",
                triggering_code: r#"from dataclasses import dataclass
@dataclass
class User:
    name: str
user = User("test")
user.name = "new""#,
                violation_line: 6,
            },
            // DOEFF009: Missing Return Type Annotation
            NoqaTestCase {
                rule_id: "DOEFF009",
                triggering_code: "def foo():\n    return 1",
                violation_line: 1,
            },
            // DOEFF010: Test File Placement - uses file path detection
            // This rule checks file path, so we test it separately with a special file path
            NoqaTestCase {
                rule_id: "DOEFF010",
                triggering_code: "def test_foo():\n    pass",
                violation_line: 1,
            },
            // DOEFF011: No Flag Arguments
            NoqaTestCase {
                rule_id: "DOEFF011",
                triggering_code: "def foo(verbose: bool = False):\n    pass",
                violation_line: 1,
            },
            // DOEFF012: No Append Loop - violation is on the for loop line
            NoqaTestCase {
                rule_id: "DOEFF012",
                triggering_code: r#"items = [1, 2, 3]
result = []
for x in items:
    result.append(x)"#,
                violation_line: 3,
            },
            // DOEFF013: Prefer Maybe Monad
            NoqaTestCase {
                rule_id: "DOEFF013",
                triggering_code: "from typing import Optional\ndef foo(x: Optional[int]) -> int:\n    return x or 0",
                violation_line: 2,
            },
            // DOEFF014: No Try-Except
            NoqaTestCase {
                rule_id: "DOEFF014",
                triggering_code: r#"def foo():
    try:
        pass
    except:
        pass"#,
                violation_line: 2,
            },
            // DOEFF015: No Zero-Arg Program
            NoqaTestCase {
                rule_id: "DOEFF015",
                triggering_code: "p: Program = create_program()",
                violation_line: 1,
            },
            // DOEFF016: No Relative Import
            NoqaTestCase {
                rule_id: "DOEFF016",
                triggering_code: "from . import foo",
                violation_line: 1,
            },
            // DOEFF017: No Program Type Param
            NoqaTestCase {
                rule_id: "DOEFF017",
                triggering_code: "@do\ndef foo(p: Program[int]) -> int:\n    return 1",
                violation_line: 2,
            },
            // DOEFF018: No Ask in Try
            NoqaTestCase {
                rule_id: "DOEFF018",
                triggering_code: r#"@do
def foo():
    try:
        x = yield ask("key")
    except:
        pass"#,
                violation_line: 4,
            },
            // DOEFF019: No Ask with Fallback
            NoqaTestCase {
                rule_id: "DOEFF019",
                triggering_code: r#"@do
def foo(arg=None):
    x = arg or (yield ask("key"))"#,
                violation_line: 3,
            },
            // DOEFF020: Program Naming Convention
            NoqaTestCase {
                rule_id: "DOEFF020",
                triggering_code: "my_program: Program = get_program()",
                violation_line: 1,
            },
            // DOEFF021: No __all__
            NoqaTestCase {
                rule_id: "DOEFF021",
                triggering_code: "__all__ = [\"foo\", \"bar\"]",
                violation_line: 1,
            },
            // DOEFF022: Prefer @do Function
            NoqaTestCase {
                rule_id: "DOEFF022",
                triggering_code: "def foo() -> EffectGenerator[int]:\n    yield Log(\"test\")\n    return 1",
                violation_line: 1,
            },
            // DOEFF023: Pipeline Marker - requires @do function called to create Program variable
            // violation is reported on the `def` line, not the decorator
            NoqaTestCase {
                rule_id: "DOEFF023",
                triggering_code: r#"@do
def process():
    return 1

p: Program = process()"#,
                violation_line: 2,
            },
        ]
    }

    /// Helper to get a single rule by ID
    fn get_rule_by_id(rule_id: &str) -> Option<Box<dyn LintRule>> {
        get_all_rules()
            .into_iter()
            .find(|r| r.rule_id() == rule_id)
    }

    /// An importable package (a temporary dir containing `__init__.py`). It creates a place where DOEFF010, which looks at the location, fires.
    fn importable_package() -> tempfile::TempDir {
        let dir = tempfile::tempdir().expect("temporary dir");
        std::fs::write(dir.path().join("__init__.py"), "").expect("place __init__.py");
        dir
    }

    /// The file path passed to rules in the noqa test. DOEFF010 fires only for `test_` files inside a package, so name such a path.
    fn noqa_case_path(rule_id: &str, package: &tempfile::TempDir) -> String {
        if rule_id == "DOEFF010" {
            package.path().join("test_example.py").to_string_lossy().into_owned()
        } else {
            "test.py".to_string()
        }
    }

    #[test]
    fn test_all_rules_respect_line_noqa() {
        let test_cases = get_noqa_test_cases();
        let package = importable_package();

        for test_case in test_cases {
            let rule = get_rule_by_id(test_case.rule_id)
                .unwrap_or_else(|| panic!("Rule {} not found", test_case.rule_id));
            let rules: Vec<Box<dyn LintRule>> = vec![rule];

            let file_path = noqa_case_path(test_case.rule_id, &package);

            // Test WITHOUT noqa - should have violations
            let result_without_noqa = lint_source(&file_path, test_case.triggering_code, &rules);
            assert!(
                !result_without_noqa.violations.is_empty(),
                "Rule {} should produce violations without noqa. Code:\n{}",
                test_case.rule_id,
                test_case.triggering_code
            );

            // Test WITH line-level noqa - should suppress violations
            let code_with_noqa = add_noqa_to_line(
                test_case.triggering_code,
                test_case.violation_line,
                test_case.rule_id,
            );

            let rule = get_rule_by_id(test_case.rule_id).unwrap();
            let rules: Vec<Box<dyn LintRule>> = vec![rule];
            let result_with_noqa = lint_source(&file_path, &code_with_noqa, &rules);

            assert!(
                result_with_noqa.violations.is_empty(),
                "Rule {} should be suppressed by noqa comment. Code:\n{}\nViolations: {:?}",
                test_case.rule_id,
                code_with_noqa,
                result_with_noqa
                    .violations
                    .iter()
                    .map(|v| format!("line {}: {}", noqa::offset_to_line(&code_with_noqa, v.offset), &v.message))
                    .collect::<Vec<_>>()
            );
        }
    }

    #[test]
    fn test_all_rules_respect_blanket_noqa() {
        let test_cases = get_noqa_test_cases();
        let package = importable_package();

        for test_case in test_cases {
            let rule = get_rule_by_id(test_case.rule_id)
                .unwrap_or_else(|| panic!("Rule {} not found", test_case.rule_id));
            let rules: Vec<Box<dyn LintRule>> = vec![rule];

            let file_path = noqa_case_path(test_case.rule_id, &package);

            // Test WITH blanket noqa (# noqa without rule ID) - should suppress
            let code_with_blanket_noqa = add_blanket_noqa_to_line(
                test_case.triggering_code,
                test_case.violation_line,
            );

            let result = lint_source(&file_path, &code_with_blanket_noqa, &rules);

            assert!(
                result.violations.is_empty(),
                "Rule {} should be suppressed by blanket noqa comment. Code:\n{}\nViolations: {:?}",
                test_case.rule_id,
                code_with_blanket_noqa,
                result
                    .violations
                    .iter()
                    .map(|v| format!("line {}: {}", noqa::offset_to_line(&code_with_blanket_noqa, v.offset), &v.message))
                    .collect::<Vec<_>>()
            );
        }
    }

    #[test]
    fn test_all_rules_respect_file_level_noqa() {
        let test_cases = get_noqa_test_cases();
        let package = importable_package();

        for test_case in test_cases {
            let rule = get_rule_by_id(test_case.rule_id)
                .unwrap_or_else(|| panic!("Rule {} not found", test_case.rule_id));
            let rules: Vec<Box<dyn LintRule>> = vec![rule];

            let file_path = noqa_case_path(test_case.rule_id, &package);

            // Test WITH file-level noqa - should suppress all violations
            let code_with_file_noqa = format!(
                "# noqa: file={}\n{}",
                test_case.rule_id, test_case.triggering_code
            );

            let result = lint_source(&file_path, &code_with_file_noqa, &rules);

            assert!(
                result.violations.is_empty(),
                "Rule {} should be suppressed by file-level noqa comment. Code:\n{}\nViolations: {:?}",
                test_case.rule_id,
                code_with_file_noqa,
                result
                    .violations
                    .iter()
                    .map(|v| format!("line {}: {}", noqa::offset_to_line(&code_with_file_noqa, v.offset), &v.message))
                    .collect::<Vec<_>>()
            );
        }
    }

    #[test]
    fn test_all_rules_respect_file_level_blanket_noqa() {
        let test_cases = get_noqa_test_cases();
        let package = importable_package();

        for test_case in test_cases {
            let rule = get_rule_by_id(test_case.rule_id)
                .unwrap_or_else(|| panic!("Rule {} not found", test_case.rule_id));
            let rules: Vec<Box<dyn LintRule>> = vec![rule];

            let file_path = noqa_case_path(test_case.rule_id, &package);

            // Test WITH file-level blanket noqa - should suppress all rules
            let code_with_file_noqa = format!(
                "# noqa: file\n{}",
                test_case.triggering_code
            );

            let result = lint_source(&file_path, &code_with_file_noqa, &rules);

            assert!(
                result.violations.is_empty(),
                "Rule {} should be suppressed by file-level blanket noqa. Code:\n{}\nViolations: {:?}",
                test_case.rule_id,
                code_with_file_noqa,
                result
                    .violations
                    .iter()
                    .map(|v| format!("line {}: {}", noqa::offset_to_line(&code_with_file_noqa, v.offset), &v.message))
                    .collect::<Vec<_>>()
            );
        }
    }

    #[test]
    fn test_noqa_for_different_rule_does_not_suppress() {
        // Test that noqa for a different rule doesn't suppress the violation
        let test_cases = get_noqa_test_cases();
        let package = importable_package();

        for test_case in test_cases {
            let rule = get_rule_by_id(test_case.rule_id)
                .unwrap_or_else(|| panic!("Rule {} not found", test_case.rule_id));
            let rules: Vec<Box<dyn LintRule>> = vec![rule];

            let file_path = noqa_case_path(test_case.rule_id, &package);

            // Add noqa for a different rule (use DOEFF999 which doesn't exist)
            let code_with_wrong_noqa = add_noqa_to_line(
                test_case.triggering_code,
                test_case.violation_line,
                "DOEFF999",
            );

            let result = lint_source(&file_path, &code_with_wrong_noqa, &rules);

            assert!(
                !result.violations.is_empty(),
                "Rule {} should NOT be suppressed by noqa for different rule. Code:\n{}",
                test_case.rule_id,
                code_with_wrong_noqa
            );
        }
    }

    #[test]
    fn test_noqa_case_insensitive() {
        // Test that noqa rule IDs are case-insensitive
        let code = "def dict():  # noqa: doeff001\n    return {}";
        let rule = get_rule_by_id("DOEFF001").unwrap();
        let rules: Vec<Box<dyn LintRule>> = vec![rule];

        let result = lint_source("test.py", code, &rules);

        assert!(
            result.violations.is_empty(),
            "noqa should be case-insensitive. Violations: {:?}",
            result.violations
        );
    }

    #[test]
    fn test_noqa_multiple_rules() {
        // Test that multiple rules can be suppressed on one line
        let code = "def dict() -> tuple[int, str]:  # noqa: DOEFF001, DOEFF006\n    return (1, \"a\")";
        let rules = get_all_rules();

        let result = lint_source("test.py", code, &rules);

        // Should not have DOEFF001 or DOEFF006 violations
        let remaining_violations: Vec<_> = result
            .violations
            .iter()
            .filter(|v| v.rule_id == "DOEFF001" || v.rule_id == "DOEFF006")
            .collect();

        assert!(
            remaining_violations.is_empty(),
            "Both DOEFF001 and DOEFF006 should be suppressed. Remaining: {:?}",
            remaining_violations
        );
    }

    /// Helper: Add noqa comment for specific rule to a specific line
    fn add_noqa_to_line(code: &str, line_num: usize, rule_id: &str) -> String {
        let lines: Vec<&str> = code.lines().collect();
        let mut result = Vec::new();

        for (i, line) in lines.iter().enumerate() {
            if i + 1 == line_num {
                result.push(format!("{}  # noqa: {}", line, rule_id));
            } else {
                result.push(line.to_string());
            }
        }

        result.join("\n")
    }

    /// Helper: Add blanket noqa comment (without rule ID) to a specific line
    fn add_blanket_noqa_to_line(code: &str, line_num: usize) -> String {
        let lines: Vec<&str> = code.lines().collect();
        let mut result = Vec::new();

        for (i, line) in lines.iter().enumerate() {
            if i + 1 == line_num {
                result.push(format!("{}  # noqa", line));
            } else {
                result.push(line.to_string());
            }
        }

        result.join("\n")
    }

    /// The number of findings of one rule through the body's per-statement recursion (passed according to the unit the rule looks at).
    fn hits_of(rule: Box<dyn LintRule>, file_path: &str, code: &str) -> usize {
        let rule_id = rule.rule_id().to_string();
        let rules: Vec<Box<dyn LintRule>> = vec![rule];
        lint_source(file_path, code, &rules)
            .violations
            .iter()
            .filter(|violation| violation.rule_id == rule_id)
            .count()
    }

    /// One finding of a rule that looks at the whole module (DOEFF008) counts as one (before the fix, it was counted as many times as the number of statements the body passed).
    #[test]
    fn a_module_rule_counts_one_finding_once() {
        let code = "from dataclasses import dataclass\n\n@dataclass\nclass Person:\n    name: str\n\nperson = Person(\"Alice\")\nperson.name = \"Bob\"\nprint(person)\n";
        let rule = Box::new(crate::rules::doeff008_no_dataclass_attribute_mutation::NoDataclassAttributeMutationRule::new());
        assert_eq!(hits_of(rule, "test.py", code), 1);
    }

    /// One try inside a function, for a rule that walks nesting by itself (DOEFF014), counts as one (before the fix, twice: the function statement and the try statement) —
    /// and one try inside a class method also counts as one (before the fix, three times: class, method, and try).
    #[test]
    fn a_subtree_rule_counts_a_nested_finding_once() {
        let in_function = "def f():\n    try:\n        g()\n    except ValueError:\n        pass\n";
        let in_method = "class C:\n    def m(self):\n        try:\n            g()\n        except ValueError:\n            pass\n";
        for code in [in_function, in_method] {
            let rule = Box::new(crate::rules::doeff014_no_try_except::NoTryExceptRule::new());
            assert_eq!(hits_of(rule, "test.py", code), 1, "{code}");
        }
    }

    /// A module rule applies only once even to a file with nested statements (DOEFF010, which looks at the location).
    #[test]
    fn a_module_rule_runs_once_per_file() {
        let code = "def test_a():\n    if True:\n        assert 1\n\ndef test_b():\n    assert 2\n";
        let rule = Box::new(crate::rules::doeff010_test_file_placement::TestFilePlacementRule::new());
        let package = importable_package();
        assert_eq!(hits_of(rule, &noqa_case_path("DOEFF010", &package), code), 1);
    }

    /// The number of DOEFF004 findings through the body's per-statement recursion. Applies one rule (which does not descend into nesting by itself).
    fn environ_hits(code: &str) -> usize {
        hits_of(Box::new(crate::rules::doeff004_no_os_environ::NoOsEnvironRule::new()), "test.py", code)
    }

    /// One read inside a function or method is counted only once (before the fix, the rule descended into nesting and the body's recursion passed the same statement too, so twice).
    #[test]
    fn a_read_inside_a_function_is_counted_once() {
        let code = "import os\ndef store_root():\n    configured = os.environ.get(\"STORE\", \"\").strip()\n    return configured\nclass Settings:\n    def home(self):\n        if True:\n            return os.getenv(\"HOME\")\n";
        assert_eq!(environ_hits(code), 2);
    }

    /// It also matches a read inside an await inside an async def (the body's recursion passes the function body, and the rule follows into the await).
    #[test]
    fn a_read_inside_await_in_an_async_function_is_seen() {
        let code = "import os\nasync def g(h):\n    return await h(os.getenv(\"G\"))\n";
        assert_eq!(environ_hits(code), 1);
    }

    /// References to os.environ (passed as a value, `in`) and os.getenv references inside nested functions and methods are each also counted only once
    /// (before the fix, only subscript and call shapes were looked at, so 0).
    #[test]
    fn a_reference_inside_nested_functions_is_counted_once() {
        let code = "import os\ndef outer():\n    def inner():\n        return dict(os.environ)\n    return inner\nclass Settings:\n    def present(self):\n        return \"HOME\" in os.environ\n    def reader(self):\n        return os.getenv\n";
        assert_eq!(environ_hits(code), 3);
    }

    // Nested bodies the body's recursion descends into. Check how many os.getenv() calls inside each construct's body match —
    // if a body exists that the body's recursion does not descend into, the findings there are 0.

    #[test]
    fn the_else_body_of_for_is_checked() {
        let code = "import os\nfor x in []:\n    pass\nelse:\n    os.getenv(\"A\")\n";
        assert_eq!(environ_hits(code), 1);
    }

    #[test]
    fn the_else_body_of_while_is_checked() {
        let code = "import os\nwhile False:\n    pass\nelse:\n    os.getenv(\"A\")\n";
        assert_eq!(environ_hits(code), 1);
    }

    #[test]
    fn the_body_and_else_of_async_for_are_checked() {
        let code = "import os\nasync def f(xs):\n    async for x in xs:\n        os.getenv(\"A\")\n    else:\n        os.getenv(\"B\")\n";
        assert_eq!(environ_hits(code), 2);
    }

    #[test]
    fn the_body_of_async_with_is_checked() {
        let code = "import os\nasync def f(lock):\n    async with lock:\n        os.getenv(\"A\")\n";
        assert_eq!(environ_hits(code), 1);
    }

    #[test]
    fn the_bodies_of_match_cases_are_checked() {
        let code = "import os\nmatch 1:\n    case 1:\n        os.getenv(\"A\")\n    case _:\n        os.getenv(\"B\")\n";
        assert_eq!(environ_hits(code), 2);
    }

    #[test]
    fn the_bodies_of_try_star_are_checked() {
        let code = "import os\ntry:\n    os.getenv(\"A\")\nexcept* ValueError:\n    os.getenv(\"B\")\nelse:\n    os.getenv(\"C\")\nfinally:\n    os.getenv(\"D\")\n";
        assert_eq!(environ_hits(code), 4);
    }

    /// A dir containing one real file (`tools/backend.py`) and two symlinks pointing at it (`a/backend.py`,
    /// `b/backend.py`) — the same shape as doeff's `doeff_cargo_backend.py` (#2905).
    fn real_file_and_two_links(code: &str) -> (tempfile::TempDir, std::path::PathBuf) {
        let dir = tempfile::tempdir().unwrap();
        let real = dir.path().join("tools").join("backend.py");
        std::fs::create_dir_all(dir.path().join("tools")).unwrap();
        std::fs::write(&real, code).unwrap();
        for package in ["a", "b"] {
            let at = dir.path().join(package);
            std::fs::create_dir_all(&at).unwrap();
            std::os::unix::fs::symlink(&real, at.join("backend.py")).unwrap();
        }
        (dir, real)
    }

    fn doeff004_hits(files: &[std::path::PathBuf]) -> usize {
        let rules = crate::rules::get_enabled_rules(Some(&["DOEFF004".to_string()]));
        lint_files_parallel(files, &rules)
            .iter()
            .map(|result| result.violations.len())
            .sum()
    }

    const ONE_ENVIRON_READ: &str = "import os\nos.environ[\"A\"]\n";

    #[test]
    fn a_file_reached_through_symlinks_is_linted_once_under_its_real_path() {
        // Traversing the dir collects one real file and two symlinks — one finding had been counted as three (#2905)
        let (dir, real) = real_file_and_two_links(ONE_ENVIRON_READ);
        let files = collect_python_files(&[dir.path().to_string_lossy().to_string()], &[]);
        assert_eq!(files, vec![real]);
        assert_eq!(doeff004_hits(&files), 1);
    }

    #[test]
    fn symlinks_alone_still_count_their_real_file_once() {
        // Even when only the symlink is named without including the real file, the count does not vanish and is one (by the path named first)
        let (dir, _real) = real_file_and_two_links(ONE_ENVIRON_READ);
        let link_a = dir.path().join("a").join("backend.py");
        let link_b = dir.path().join("b").join("backend.py");
        let named = [link_a.clone(), link_b]
            .map(|p| p.to_string_lossy().to_string())
            .to_vec();
        let files = collect_python_files(&named, &[]);
        assert_eq!(files, vec![link_a]);
        assert_eq!(doeff004_hits(&files), 1);
    }

    #[test]
    fn the_real_path_is_named_even_when_a_symlink_comes_first() {
        let (dir, real) = real_file_and_two_links(ONE_ENVIRON_READ);
        let other = dir.path().join("other.py");
        std::fs::write(&other, "x = 1\n").unwrap();
        let named = [dir.path().join("a").join("backend.py"), other.clone(), real.clone()];
        assert_eq!(one_path_per_file(named.to_vec()), vec![real, other]);
    }

    fn default_excludes() -> Vec<String> {
        ["build", "dist", ".git", "fixtures"].map(String::from).to_vec()
    }

    #[test]
    fn a_file_whose_name_only_contains_an_excluded_word_is_still_linted() {
        // Counterexample (#3012): a file whose name merely contains build or dist was outside the judgment both when named and during dir traversal
        let dir = tempfile::TempDir::new().unwrap();
        let backend = dir.path().join("doeff_indexer_build_backend.py");
        let metadata = dir.path().join("verify_dist_metadata.py");
        std::fs::write(&backend, ONE_ENVIRON_READ).unwrap();
        std::fs::write(&metadata, "x = 1\n").unwrap();
        let named = [&backend, &metadata].map(|p| p.to_string_lossy().to_string()).to_vec();
        let forced = collect_python_files_with_options(&named, &default_excludes(), true);
        assert_eq!(forced, vec![backend.clone(), metadata.clone()]);
        assert_eq!(doeff004_hits(&forced), 1);
        let mut walked = collect_python_files(&[dir.path().to_string_lossy().to_string()], &default_excludes());
        walked.sort();
        assert_eq!(walked, vec![backend, metadata]);
    }

    #[test]
    fn a_dir_whose_name_only_contains_an_excluded_word_is_still_walked() {
        // .github/ merely contains .git, and fixtures_discovery/ merely contains fixtures — whole dirs had been pruned during traversal
        let dir = tempfile::TempDir::new().unwrap();
        for sub in [".github", "fixtures_discovery"] {
            std::fs::create_dir(dir.path().join(sub)).unwrap();
            std::fs::write(dir.path().join(sub).join("m.py"), "x = 1\n").unwrap();
        }
        let walked = collect_python_files(&[dir.path().to_string_lossy().to_string()], &default_excludes());
        assert_eq!(walked.len(), 2);
    }

    #[test]
    fn a_dir_or_file_named_exactly_like_a_pattern_is_still_excluded() {
        // Matches are excluded as before: inside the dir build/ or fixtures/, and when a component of a named file equals the pattern
        let dir = tempfile::TempDir::new().unwrap();
        for sub in ["build", "fixtures"] {
            std::fs::create_dir(dir.path().join(sub)).unwrap();
            std::fs::write(dir.path().join(sub).join("m.py"), ONE_ENVIRON_READ).unwrap();
        }
        let walked = collect_python_files(&[dir.path().to_string_lossy().to_string()], &default_excludes());
        assert!(walked.is_empty());
        let named = vec![dir.path().join("build").join("m.py").to_string_lossy().to_string()];
        assert!(collect_python_files_with_options(&named, &default_excludes(), true).is_empty());
        assert!(should_exclude(Path::new("pkg/dist/x.py"), &default_excludes()));
        assert!(!should_exclude(Path::new("pkg/redistribute/x.py"), &default_excludes()));
    }
}

