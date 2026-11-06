//! DOEFF010: Test File Placement
//!
//! A test file (`test_*.py` / `test.py`) must not sit inside an importable package — a directory
//! with `__init__.py` — outside a `tests` directory. There it ships with the package and pytest
//! collects the package's classes as tests.
//!
//! It fires only for "test files placed inside an importable package". `test_*.py` placed in a non-package dir
//! (agentd's conformance suite, design-record models, examples, tool scripts) does not get mixed into shipped code and is
//! outside this rule's concern — previously it fired merely because no ancestor dir was named `tests`, producing 41 false positives in doeff alone.
//! Cases lost: `test_*.py` in a non-package dir (such as the repo root), and test files inside a PEP 420 namespace package (a
//! package without `__init__.py`).

use crate::models::{RuleContext, Severity, Violation};
use crate::rules::base::{LintRule, RuleReach};
use std::path::Path;

pub struct TestFilePlacementRule;

impl TestFilePlacementRule {
    pub fn new() -> Self {
        Self
    }

    fn is_test_file(file_path: &str) -> bool {
        if let Some(file_name) = Path::new(file_path).file_name() {
            if let Some(name_str) = file_name.to_str() {
                return (name_str.starts_with("test_") || name_str == "test.py")
                    && name_str.ends_with(".py");
            }
        }
        false
    }

    fn is_in_tests_directory(file_path: &str) -> bool {
        let path = Path::new(file_path);
        for ancestor in path.ancestors() {
            if let Some(dir_name) = ancestor.file_name() {
                if let Some(name_str) = dir_name.to_str() {
                    if name_str == "tests" {
                        return true;
                    }
                }
            }
        }
        false
    }

    /// Whether the file's dir is an importable package (has `__init__.py`) — the only place this rule reads the file system.
    /// A relative path is read from the same working dir from which the linter opened the file.
    fn dir_is_package(file_path: &str) -> bool {
        Path::new(file_path)
            .parent()
            .is_some_and(|dir| dir.join("__init__.py").is_file())
    }
}

impl LintRule for TestFilePlacementRule {
    fn rule_id(&self) -> &str {
        "DOEFF010"
    }

    fn description(&self) -> &str {
        "Test files must not be placed inside an importable package outside a 'tests' directory"
    }

    /// This rule looks at where a file is placed, so the body applies it to a file exactly once (#2858 — narrowing by the first sentence is the body's job).
    fn reach(&self) -> RuleReach {
        RuleReach::Module
    }

    fn check(&self, context: &RuleContext) -> Vec<Violation> {
        let path = context.file_path;
        // Do the name and placement judgment (pure) first; read the file system only when the name is a test file and it is outside tests.
        if !Self::is_test_file(path) || Self::is_in_tests_directory(path) || !Self::dir_is_package(path) {
            return Vec::new();
        }
        let file_name = Path::new(path)
            .file_name()
            .and_then(|n| n.to_str())
            .unwrap_or("test file");
        vec![Violation::new(
            self.rule_id().to_string(),
            format!(
                "Test file '{}' is inside an importable package (its directory has __init__.py) \
                 outside a 'tests' directory, so it ships with the package and pytest collects \
                 the package's classes as tests. Move it under a 'tests' directory, or rename it \
                 if it is not a test.",
                file_name
            ),
            0,
            path.to_string(),
            Severity::Error,
        )]
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use rustpython_ast::Mod;
    use rustpython_parser::{parse, Mode};
    use std::fs;
    use tempfile::TempDir;

    fn check_code(code: &str, file_path: &str) -> Vec<Violation> {
        let ast = parse(code, Mode::Module, file_path).unwrap();
        let rule = TestFilePlacementRule::new();
        let mut violations = Vec::new();

        if let Mod::Module(module) = &ast {
            if let Some(first_stmt) = module.body.first() {
                let context = RuleContext {
                    stmt: first_stmt,
                    file_path,
                    source: code,
                    ast: &ast,
                };
                violations.extend(rule.check(&context));
            }
        }

        violations
    }

    /// Create a dir under a temporary dir, and place `__init__.py` if `package` is true. Returns the path of the created dir.
    fn make_dir(root: &TempDir, relative: &str, package: bool) -> String {
        let dir = root.path().join(relative);
        fs::create_dir_all(&dir).expect("create dir");
        if package {
            fs::write(dir.join("__init__.py"), "").expect("place __init__.py");
        }
        dir.to_string_lossy().into_owned()
    }

    const TEST_CODE: &str = "def test_something(): pass";

    // Shapes that should fire: a test file inside an importable package.

    #[test]
    fn a_test_file_inside_a_package_is_flagged() {
        let root = tempfile::tempdir().unwrap();
        let pkg = make_dir(&root, "pkg", true);
        assert_eq!(check_code(TEST_CODE, &format!("{pkg}/test_module.py")).len(), 1);
    }

    #[test]
    fn a_bare_test_py_inside_a_nested_package_is_flagged() {
        let root = tempfile::tempdir().unwrap();
        make_dir(&root, "pkg", true);
        let sub = make_dir(&root, "pkg/sub", true);
        assert_eq!(check_code(TEST_CODE, &format!("{sub}/test.py")).len(), 1);
    }

    // Shapes that should not fire: non-package dirs (conformance suite, examples, tools) and inside a tests dir.

    #[test]
    fn a_test_file_in_a_directory_without_init_is_not_flagged() {
        let root = tempfile::tempdir().unwrap();
        let suite = make_dir(&root, "packages/doeff-example/conformance", false);
        assert_eq!(check_code(TEST_CODE, &format!("{suite}/test_s1.py")).len(), 0);
    }

    #[test]
    fn a_test_file_under_tests_inside_a_package_is_not_flagged() {
        let root = tempfile::tempdir().unwrap();
        make_dir(&root, "pkg", true);
        let tests = make_dir(&root, "pkg/tests/unit", true);
        assert_eq!(check_code(TEST_CODE, &format!("{tests}/test_module.py")).len(), 0);
    }

    #[test]
    fn a_non_test_module_inside_a_package_is_not_flagged() {
        let root = tempfile::tempdir().unwrap();
        let pkg = make_dir(&root, "pkg", true);
        assert_eq!(check_code("def something(): pass", &format!("{pkg}/module.py")).len(), 0);
    }

    /// Does not fire when only the parent dir is a package and the file's own dir has no `__init__.py` (that dir is not importable).
    #[test]
    fn a_plain_directory_under_a_package_is_not_flagged() {
        let root = tempfile::tempdir().unwrap();
        make_dir(&root, "pkg", true);
        let plain = make_dir(&root, "pkg/examples", false);
        assert_eq!(check_code(TEST_CODE, &format!("{plain}/test_example.py")).len(), 0);
    }
}
