//! doeff-linter: A linter for enforcing code quality and immutability patterns
//!
//! This crate provides lint rules for Python code, focusing on:
//! - Immutability patterns
//! - Type safety
//! - Code organization

pub mod build_info;
// 外の crate(main.rs・tests)が読む名。crate の中は build_info を直に読む(lib.rs を読み戻すと依存の輪になる — #2119)。
pub use build_info::{BUILD_COMMIT, VERSION_TEXT};

pub mod baseline;
pub mod commit_hook;
pub mod config;
pub mod editor;
pub mod head_report_cache;
pub mod logging;
pub mod models;
pub mod noqa;
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

/// path の決まった file の source に Python の規則を当て、その file を持つ package の宣言の母集団(crate::population — 層の
/// `:exempt` の除外と、外した層の業務の import の DOEFF032)を当てる。実行の入口(file の列・editor の 1 file)はここを通る —
/// lint_source は母集団を知らない(規則の単体の検の口・agora-redesign #2811)。
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
        // 宣言を読めない時は外さず(どの規則もそのまま当たる)、読めないことを当たりで名指す — file の誤り(error の欄)にすると、
        // text の出力はその file の当たりを隠し、終了コードにも数えない。
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
        // module 全体を見る規則は file に 1 度だけ(module の最初の文を stmt にして)— #2858。
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

/// 本体の再帰がこの文を規則に渡すか — 規則が見る単位(RuleReach)に従う。自分で入れ子を歩く規則に入れ子の文を
/// 渡すと、同じ当たりを入れ子の深さの分だけ数える(agora-redesign #2858)。
fn passes_statement(reach: RuleReach, top_level: bool) -> bool {
    match reach {
        RuleReach::Statement => true,
        RuleReach::Subtree => top_level,
        RuleReach::Module => false,
    }
}

/// 規則を 1 回当て、noqa で抑えた当たりを除いて積む。
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

/// 文 1 つを、その単位を受け持つ規則に当て、入れ子の文へ降りる。`top_level` = module の上の段の文。
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

    // Recursively check nested statements
    for body in nested_bodies(stmt) {
        for s in body {
            check_stmt_recursive(s, false, file_path, source, ast, rules, noqa, violations);
        }
    }
}

/// 文の入れ子の本体(規則に 1 つずつ渡す文の列)。文の種類を網羅する(`_ =>` を使わない)— 降りない本体が在ると、
/// そこの code にはどの規則も当たらない(for / while の else・async for・async with・match の case・try* を
/// 落としていた — agora-redesign #2834)。
fn nested_bodies(stmt: &Stmt) -> Vec<&[Stmt]> {
    match stmt {
        Stmt::FunctionDef(func) => vec![func.body.as_slice()],
        Stmt::AsyncFunctionDef(func) => vec![func.body.as_slice()],
        Stmt::ClassDef(class_def) => vec![class_def.body.as_slice()],
        Stmt::For(for_stmt) => vec![for_stmt.body.as_slice(), for_stmt.orelse.as_slice()],
        Stmt::AsyncFor(for_stmt) => vec![for_stmt.body.as_slice(), for_stmt.orelse.as_slice()],
        Stmt::While(while_stmt) => vec![while_stmt.body.as_slice(), while_stmt.orelse.as_slice()],
        Stmt::If(if_stmt) => vec![if_stmt.body.as_slice(), if_stmt.orelse.as_slice()],
        Stmt::With(with_stmt) => vec![with_stmt.body.as_slice()],
        Stmt::AsyncWith(with_stmt) => vec![with_stmt.body.as_slice()],
        Stmt::Match(match_stmt) => match_stmt.cases.iter().map(|case| case.body.as_slice()).collect(),
        Stmt::Try(try_stmt) => try_bodies(&try_stmt.body, &try_stmt.handlers, &try_stmt.orelse, &try_stmt.finalbody),
        Stmt::TryStar(try_stmt) => {
            try_bodies(&try_stmt.body, &try_stmt.handlers, &try_stmt.orelse, &try_stmt.finalbody)
        }
        Stmt::Return(_)
        | Stmt::Delete(_)
        | Stmt::Assign(_)
        | Stmt::TypeAlias(_)
        | Stmt::AugAssign(_)
        | Stmt::AnnAssign(_)
        | Stmt::Raise(_)
        | Stmt::Assert(_)
        | Stmt::Import(_)
        | Stmt::ImportFrom(_)
        | Stmt::Global(_)
        | Stmt::Nonlocal(_)
        | Stmt::Expr(_)
        | Stmt::Pass(_)
        | Stmt::Break(_)
        | Stmt::Continue(_) => Vec::new(),
    }
}

/// try と try* の本体・各 handler の本体・else・finally。
fn try_bodies<'a>(
    body: &'a [Stmt],
    handlers: &'a [rustpython_ast::ExceptHandler],
    orelse: &'a [Stmt],
    finalbody: &'a [Stmt],
) -> Vec<&'a [Stmt]> {
    let handler_bodies = handlers
        .iter()
        .map(|rustpython_ast::ExceptHandler::ExceptHandler(handler)| handler.body.as_slice());
    std::iter::once(body)
        .chain(handler_bodies)
        .chain([orelse, finalbody])
        .collect()
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

    files
}

/// path が除く pattern(file の名の一致・部分一致か、path の区切りの一致)に当たるか。
pub fn should_exclude(path: &Path, patterns: &[String]) -> bool {
    for pattern in patterns {
        if let Some(name) = path.file_name() {
            if let Some(name_str) = name.to_str() {
                if name_str == pattern || name_str.contains(pattern) {
                    return true;
                }
            }
        }
        // Check if any path component matches
        for component in path.components() {
            if let Some(comp_str) = component.as_os_str().to_str() {
                if comp_str == pattern {
                    return true;
                }
            }
        }
    }
    false
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

    #[test]
    fn test_all_rules_respect_line_noqa() {
        let test_cases = get_noqa_test_cases();

        for test_case in test_cases {
            let rule = get_rule_by_id(test_case.rule_id)
                .unwrap_or_else(|| panic!("Rule {} not found", test_case.rule_id));
            let rules: Vec<Box<dyn LintRule>> = vec![rule];

            // Determine file path (DOEFF010 needs a test_ prefixed file not in tests/)
            let file_path = if test_case.rule_id == "DOEFF010" {
                "src/test_example.py"
            } else {
                "test.py"
            };

            // Test WITHOUT noqa - should have violations
            let result_without_noqa = lint_source(file_path, test_case.triggering_code, &rules);
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
            let result_with_noqa = lint_source(file_path, &code_with_noqa, &rules);

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

        for test_case in test_cases {
            let rule = get_rule_by_id(test_case.rule_id)
                .unwrap_or_else(|| panic!("Rule {} not found", test_case.rule_id));
            let rules: Vec<Box<dyn LintRule>> = vec![rule];

            let file_path = if test_case.rule_id == "DOEFF010" {
                "src/test_example.py"
            } else {
                "test.py"
            };

            // Test WITH blanket noqa (# noqa without rule ID) - should suppress
            let code_with_blanket_noqa = add_blanket_noqa_to_line(
                test_case.triggering_code,
                test_case.violation_line,
            );

            let result = lint_source(file_path, &code_with_blanket_noqa, &rules);

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

        for test_case in test_cases {
            let rule = get_rule_by_id(test_case.rule_id)
                .unwrap_or_else(|| panic!("Rule {} not found", test_case.rule_id));
            let rules: Vec<Box<dyn LintRule>> = vec![rule];

            let file_path = if test_case.rule_id == "DOEFF010" {
                "src/test_example.py"
            } else {
                "test.py"
            };

            // Test WITH file-level noqa - should suppress all violations
            let code_with_file_noqa = format!(
                "# noqa: file={}\n{}",
                test_case.rule_id, test_case.triggering_code
            );

            let result = lint_source(file_path, &code_with_file_noqa, &rules);

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

        for test_case in test_cases {
            let rule = get_rule_by_id(test_case.rule_id)
                .unwrap_or_else(|| panic!("Rule {} not found", test_case.rule_id));
            let rules: Vec<Box<dyn LintRule>> = vec![rule];

            let file_path = if test_case.rule_id == "DOEFF010" {
                "src/test_example.py"
            } else {
                "test.py"
            };

            // Test WITH file-level blanket noqa - should suppress all rules
            let code_with_file_noqa = format!(
                "# noqa: file\n{}",
                test_case.triggering_code
            );

            let result = lint_source(file_path, &code_with_file_noqa, &rules);

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

        for test_case in test_cases {
            let rule = get_rule_by_id(test_case.rule_id)
                .unwrap_or_else(|| panic!("Rule {} not found", test_case.rule_id));
            let rules: Vec<Box<dyn LintRule>> = vec![rule];

            let file_path = if test_case.rule_id == "DOEFF010" {
                "src/test_example.py"
            } else {
                "test.py"
            };

            // Add noqa for a different rule (use DOEFF999 which doesn't exist)
            let code_with_wrong_noqa = add_noqa_to_line(
                test_case.triggering_code,
                test_case.violation_line,
                "DOEFF999",
            );

            let result = lint_source(file_path, &code_with_wrong_noqa, &rules);

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

    /// 本体の文ごとの再帰を通した、規則 1 つの当たりの数(agora-redesign #2858 — 規則が見る単位どおりに渡す)。
    fn hits_of(rule: Box<dyn LintRule>, file_path: &str, code: &str) -> usize {
        let rule_id = rule.rule_id().to_string();
        let rules: Vec<Box<dyn LintRule>> = vec![rule];
        lint_source(file_path, code, &rules)
            .violations
            .iter()
            .filter(|violation| violation.rule_id == rule_id)
            .count()
    }

    /// module 全体を見る規則(DOEFF008)の当たり 1 つは 1 件(直す前は本体が渡す文の数だけ数えていた)。
    #[test]
    fn a_module_rule_counts_one_finding_once() {
        let code = "from dataclasses import dataclass\n\n@dataclass\nclass Person:\n    name: str\n\nperson = Person(\"Alice\")\nperson.name = \"Bob\"\nprint(person)\n";
        let rule = Box::new(crate::rules::doeff008_no_dataclass_attribute_mutation::NoDataclassAttributeMutationRule::new());
        assert_eq!(hits_of(rule, "test.py", code), 1);
    }

    /// 自分で入れ子を歩く規則(DOEFF014)の、関数の中の try 1 つは 1 件(直す前は関数の文と try の文の 2 度)・
    /// class の method の中の try 1 つも 1 件(直す前は class・method・try の 3 度)。
    #[test]
    fn a_subtree_rule_counts_a_nested_finding_once() {
        let in_function = "def f():\n    try:\n        g()\n    except ValueError:\n        pass\n";
        let in_method = "class C:\n    def m(self):\n        try:\n            g()\n        except ValueError:\n            pass\n";
        for code in [in_function, in_method] {
            let rule = Box::new(crate::rules::doeff014_no_try_except::NoTryExceptRule::new());
            assert_eq!(hits_of(rule, "test.py", code), 1, "{code}");
        }
    }

    /// module の規則は、入れ子の文を持つ file でも 1 度だけ当たる(置き場を見る DOEFF010)。
    #[test]
    fn a_module_rule_runs_once_per_file() {
        let code = "def test_a():\n    if True:\n        assert 1\n\ndef test_b():\n    assert 2\n";
        let rule = Box::new(crate::rules::doeff010_test_file_placement::TestFilePlacementRule::new());
        assert_eq!(hits_of(rule, "src/test_example.py", code), 1);
    }

    /// 本体の文ごとの再帰を通した DOEFF004 の当たりの数。規則 1 つ(自分では入れ子に降りない — agora-redesign #2832)を当てる。
    fn environ_hits(code: &str) -> usize {
        hits_of(Box::new(crate::rules::doeff004_no_os_environ::NoOsEnvironRule::new()), "test.py", code)
    }

    /// 関数・メソッドの中の読み 1 つは 1 度だけ数える(直す前は規則が入れ子へ降り、本体の再帰も同じ文を渡して 2 度)。
    #[test]
    fn a_read_inside_a_function_is_counted_once() {
        let code = "import os\ndef store_root():\n    configured = os.environ.get(\"STORE\", \"\").strip()\n    return configured\nclass Settings:\n    def home(self):\n        if True:\n            return os.getenv(\"HOME\")\n";
        assert_eq!(environ_hits(code), 2);
    }

    /// async def の中の await の中の読みにも当たる(本体の再帰が関数の本体を渡し、規則が await の中を辿る)。
    #[test]
    fn a_read_inside_await_in_an_async_function_is_seen() {
        let code = "import os\nasync def g(h):\n    return await h(os.getenv(\"G\"))\n";
        assert_eq!(environ_hits(code), 1);
    }

    // 本体の再帰が降りる入れ子の本体(agora-redesign #2834)。各構文の本体の中の os.getenv() が当たる数を見る —
    // 本体の再帰が降りない本体が在ると、そこの当たりは 0 件になる。

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
}

