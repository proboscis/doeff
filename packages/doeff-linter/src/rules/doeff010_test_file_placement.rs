//! DOEFF010: Test File Placement
//!
//! A test file (`test_*.py` / `test.py`) must not sit inside an importable package — a directory
//! with `__init__.py` — outside a `tests` directory. There it ships with the package and pytest
//! collects the package's classes as tests.
//!
//! 鳴るのは「import できる package の中に置かれた検の file」だけ(agora-redesign #2880)。package でない dir
//! (agentd の conformance の組・設計の記録の模型・examples・道具の script)に置かれた `test_*.py` は配る code に混ざらず、
//! この規則の害の外 — 前は祖先に `tests` の dir が無いだけで鳴り、doeff だけで 41 件の誤検出を出していた。
//! 失う当たり: package でない dir(repo の根など)の `test_*.py` と、PEP 420 の名前空間 package(`__init__.py` の無い
//! package)の中の検の file。

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

    /// file の dir が import できる package か(`__init__.py` が在るか)— この規則が file system を読む唯一の所。
    /// 相対の path は linter が file を開いたのと同じ作業 dir から読む。
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

    /// file の置き場を見る規則なので、本体が file に 1 度だけ当てる(#2858 — 最初の文の絞りは本体の役目)。
    fn reach(&self) -> RuleReach {
        RuleReach::Module
    }

    fn check(&self, context: &RuleContext) -> Vec<Violation> {
        let path = context.file_path;
        // 名と置き場の判じ(純粋)を先に済ませ、file system は名が検の file で tests の外の時だけ読む。
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

    /// 一時の dir の下に dir を作り、`package` が真なら `__init__.py` を置く。返すのは作った dir の path。
    fn make_dir(root: &TempDir, relative: &str, package: bool) -> String {
        let dir = root.path().join(relative);
        fs::create_dir_all(&dir).expect("dir を作る");
        if package {
            fs::write(dir.join("__init__.py"), "").expect("__init__.py を置く");
        }
        dir.to_string_lossy().into_owned()
    }

    const TEST_CODE: &str = "def test_something(): pass";

    // 鳴るべき形: import できる package の中の検の file。

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

    // 鳴らないべき形: package でない dir(conformance の組・examples・道具)と tests の dir の中。

    #[test]
    fn a_test_file_in_a_directory_without_init_is_not_flagged() {
        let root = tempfile::tempdir().unwrap();
        let suite = make_dir(&root, "packages/doeff-agents/conformance", false);
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

    /// 親の dir だけが package で、file の dir 自身に `__init__.py` が無い時は鳴らない(その dir は import できない)。
    #[test]
    fn a_plain_directory_under_a_package_is_not_flagged() {
        let root = tempfile::tempdir().unwrap();
        make_dir(&root, "pkg", true);
        let plain = make_dir(&root, "pkg/examples", false);
        assert_eq!(check_code(TEST_CODE, &format!("{plain}/test_example.py")).len(), 0);
    }
}
