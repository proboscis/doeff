//! DOEFF135: tests are deftest only (decision of #1144 and #1104).
//!
//! Selects files by the spelling shape of `:test-forms {:tests [..] :check-scripts [..] :runners [..]}` in architecture.hy, and reports non-deftest test forms
//! once per file (critical):
//!   * `python-test`  — `def test_*` (pytest form) in a Python file of :tests. The count is written in the message.
//!   * `module-skip`  — a file of :tests skips the whole module (`pytest.skip(…, allow_module_level=True)`, `mark.skip` of `pytestmark` — a multi-line binding
//!     whose binding name and mark are on different lines is also read until the brackets close).
//!   * `check-script` — a file of :check-scripts (a check that runs outside pytest).
//!   * `runner`       — a file of :runners (a runner that runs deftest itself).
//! Judged on literal lines (no Python AST — the form is decided by the file head and line head). Hy `defn test-…` belongs to DOEFF118.

use std::path::Path;

use walkdir::WalkDir;

use super::architecture::TestForms;
use super::paths::{glob_matches, relative_path};

/// One form found.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FormFinding {
    pub rel: String,
    /// Name of the form (python-test, module-skip, check-script, runner).
    pub form: &'static str,
    /// Position (0-based line).
    pub line: u32,
    pub detail: String,
}

/// Dirs not walked (hidden dirs and generated output).
const SKIPPED_DIRS: &[&str] = &["node_modules", "target", "__pycache__", "venv", "site-packages"];

/// Whether a Python line defines a pytest test function (`def test_…` / `async def test_…` — indentation means a method inside a class).
fn python_test_line(line: &str) -> bool {
    let trimmed = line.trim_start();
    let rest = trimmed.strip_prefix("async ").unwrap_or(trimmed);
    rest.strip_prefix("def ").is_some_and(|name| name.trim_start().starts_with("test_"))
}

/// Whether the line is a whole-module skip. A `pytest.skip` call starting at an unindented level is a module-top statement, so it alone skips the whole module
/// (a multi-line form with `:allow-module-level` on the next line is also picked up).
fn module_skip_line(line: &str) -> bool {
    let top_level_call = line.starts_with("(pytest.skip ") || line.starts_with("(pytest.skip\t") || line.starts_with("pytest.skip(");
    let skip_call = top_level_call || (line.contains("pytest.skip") && (line.contains("allow_module_level") || line.contains("allow-module-level")));
    let skip_mark = line.contains("pytestmark") && skip_mark_text(line);
    skip_call || skip_mark
}

/// Whether the text contains a skip mark (`mark.skip` / `mark.skipif`).
fn skip_mark_text(text: &str) -> bool {
    text.contains("mark.skip")
}

/// Whether the line is the head of an unindented statement binding `pytestmark` (Hy `(val pytestmark` / `(setv pytestmark`, Python `pytestmark =`).
/// Words inside comments or strings do not come at the line head, so they do not match.
fn pytestmark_binding_head(line: &str) -> bool {
    let rest = ["(val ", "(setv ", "(var "].iter().find_map(|head| line.strip_prefix(head)).map(str::trim_start);
    match rest {
        Some(rest) => rest.strip_prefix("pytestmark").is_some_and(|after| after.is_empty() || after.starts_with(char::is_whitespace)),
        None => line.strip_prefix("pytestmark").is_some_and(|after| after.trim_start().starts_with('=') || after.trim_start().starts_with(':')),
    }
}

/// The text of the form starting at line start, joined until its brackets (`(` `[` `{`) close (brackets inside strings are not counted — `\"` escapes are skipped).
/// If the file ends without closing, up to the end of the file.
fn balanced_form(lines: &[&str], start: usize) -> String {
    let mut depth: i32 = 0;
    let mut in_string = false;
    let mut escaped = false;
    let mut form = String::new();
    for line in &lines[start..] {
        form.push_str(line);
        form.push('\n');
        for ch in line.chars() {
            if in_string {
                match (escaped, ch) {
                    (true, _) => escaped = false,
                    (false, '\\') => escaped = true,
                    (false, '"') => in_string = false,
                    _ => {}
                }
                continue;
            }
            match ch {
                '"' => in_string = true,
                '(' | '[' | '{' => depth += 1,
                ')' | ']' | '}' => depth -= 1,
                _ => {}
            }
        }
        if depth <= 0 && !in_string {
            break;
        }
    }
    form
}

/// Position of a whole-module skip (0-based line). In addition to the one-line form (module_skip_line), also picks up a multi-line `pytestmark`
/// binding whose name and mark are on different lines (`(pytest.mark.skip …` placed on the line after `(val pytestmark`). A multi-line form points at the line of the binding name.
fn module_skip_position(lines: &[&str]) -> Option<usize> {
    lines
        .iter()
        .enumerate()
        .position(|(i, line)| module_skip_line(line) || (pytestmark_binding_head(line) && skip_mark_text(&balanced_form(lines, i))))
}

/// Finds all forms of files matching :test-forms (in path order).
pub fn find(root: &Path, forms: &TestForms) -> Vec<FormFinding> {
    let walker = WalkDir::new(root).follow_links(false).into_iter().filter_entry(|entry| {
        let name = entry.file_name().to_string_lossy();
        entry.depth() == 0 || !entry.file_type().is_dir() || !(name.starts_with('.') || SKIPPED_DIRS.contains(&name.as_ref()))
    });
    let mut rels: Vec<String> = walker
        .filter_map(Result::ok)
        .filter(|entry| entry.file_type().is_file())
        .filter_map(|entry| relative_path(root, entry.path()))
        .collect();
    rels.sort();
    let matches = |patterns: &[String], rel: &str| patterns.iter().any(|p| glob_matches(p, rel));
    let mut found = Vec::new();
    for rel in rels {
        if matches(&forms.check_scripts, &rel) {
            found.push(FormFinding { rel: rel.clone(), form: "check-script", line: 0, detail: "a check script that runs outside pytest".to_string() });
        }
        if matches(&forms.runners, &rel) {
            found.push(FormFinding { rel: rel.clone(), form: "runner", line: 0, detail: "a runner that runs deftest itself".to_string() });
        }
        if !matches(&forms.tests, &rel) {
            continue;
        }
        let Ok(source) = std::fs::read_to_string(root.join(&rel)) else { continue };
        let lines: Vec<&str> = source.lines().collect();
        if rel.ends_with(".py") {
            let tests: Vec<usize> = lines.iter().enumerate().filter(|(_, l)| python_test_line(l)).map(|(i, _)| i).collect();
            if let Some(first) = tests.first() {
                found.push(FormFinding {
                    rel: rel.clone(),
                    form: "python-test",
                    line: *first as u32,
                    detail: format!("{} Python def test_* functions", tests.len()),
                });
            }
        }
        if let Some(line) = module_skip_position(&lines) {
            found.push(FormFinding { rel: rel.clone(), form: "module-skip", line: line as u32, detail: "skips the whole module".to_string() });
        }
    }
    found
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn lines_are_read_by_their_head() {
        assert!(python_test_line("def test_one():"));
        assert!(python_test_line("    async def test_two(self):"));
        assert!(!python_test_line("def helper_test_x():"));
        assert!(!python_test_line("# def test_commented"));
        assert!(module_skip_line("pytest.skip(\"missing on the doeff side\", allow_module_level=True)"));
        assert!(module_skip_line("(pytest.skip \"x\" :allow-module-level True)"));
        assert!(module_skip_line("(setv pytestmark (pytest.mark.skipif True :reason \"x\"))"));
        assert!(module_skip_line("(pytest.skip (+ \"first line of the reason\""), "did not pick up a multi-line module-head skip");
        assert!(!module_skip_line("  (pytest.skip \"this test only\")"));
        let multiline = ["(import pytest)", ";; written with the pytestmark mark", "(val pytestmark", "  (pytest.mark.skip :reason (+ \"(bracket\"", "                              \"line 2\")))", "(deftest test-x (pytest.mark.skip))"];
        assert_eq!(module_skip_position(&multiline), Some(2), "did not pick up the multi-line binding");
        let python = ["pytestmark = [", "    pytest.mark.skip(reason=\"x\"),", "]"];
        assert_eq!(module_skip_position(&python), Some(0));
        let real_world = ["(val pytestmark", "  pytest.mark.real-world)", "(deftest test-y (pytest.mark.skip))"];
        assert_eq!(module_skip_position(&real_world), None, "counted a test skip after the binding as part of the binding");
        assert!(!pytestmark_binding_head("(val pytestmarks []"));
        assert!(!pytestmark_binding_head(";; (val pytestmark"));
    }
}
