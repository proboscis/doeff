//! DOEFF135: テストは deftest だけ(agora-redesign #1106 の R6・#1144 — operator 2026-09-27「検は deftest だけ」・#1104 の決定)。
//!
//! architecture.hy の `:test-forms {:tests [..] :check-scripts [..] :runners [..]}` の綴りの型で file を選び、deftest 以外のテストの形を
//! file ごとに 1 件出す(critical):
//!   * `python-test`  — :tests の Python の file の `def test_*`(pytest の形)。件数を message に書く。
//!   * `module-skip`  — :tests の file が module ごと skip する(`pytest.skip(…, allow_module_level=True)`・`pytestmark` の `mark.skip`)。
//!   * `check-script` — :check-scripts の file(pytest の外で走る検査)。
//!   * `runner`       — :runners の file(deftest を自分で回す runner)。
//! 判定は字面の行で読む(Python の AST は使わない — 形は file の頭と行の頭で決まる)。Hy の `defn test-…` は DOEFF118 が持つ。

use std::path::Path;

use walkdir::WalkDir;

use super::architecture::TestForms;
use super::{glob_matches, relative_path};

/// 見つけた形 1 つ。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FormFinding {
    pub rel: String,
    /// 形の名(python-test・module-skip・check-script・runner)。
    pub form: &'static str,
    /// 位置(0 始まりの行)。
    pub line: u32,
    pub detail: String,
}

/// 歩かない dir(隠し dir と生成物)。
const SKIPPED_DIRS: &[&str] = &["node_modules", "target", "__pycache__", "venv", "site-packages"];

/// Python の行が pytest のテストの関数を定義するか(`def test_…` / `async def test_…` — 字下げは class の中の method)。
fn python_test_line(line: &str) -> bool {
    let trimmed = line.trim_start();
    let rest = trimmed.strip_prefix("async ").unwrap_or(trimmed);
    rest.strip_prefix("def ").is_some_and(|name| name.trim_start().starts_with("test_"))
}

/// 行が module ごとの skip か。字下げの無い段で始まる `pytest.skip` の呼び出しは module の頭の文なので、それだけで module ごと
/// (`:allow-module-level` が次の行に在る複数行の form も拾う)。
fn module_skip_line(line: &str) -> bool {
    let top_level_call = line.starts_with("(pytest.skip ") || line.starts_with("(pytest.skip\t") || line.starts_with("pytest.skip(");
    let skip_call = top_level_call || (line.contains("pytest.skip") && (line.contains("allow_module_level") || line.contains("allow-module-level")));
    let skip_mark = line.contains("pytestmark") && (line.contains("mark.skip") || line.contains("mark.skipif"));
    skip_call || skip_mark
}

/// :test-forms に当たる file の形を全部見つける(path の順)。
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
            found.push(FormFinding { rel: rel.clone(), form: "check-script", line: 0, detail: "pytest の外で走る検査の script".to_string() });
        }
        if matches(&forms.runners, &rel) {
            found.push(FormFinding { rel: rel.clone(), form: "runner", line: 0, detail: "deftest を自分で回す runner".to_string() });
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
                    detail: format!("Python の def test_* が {} 本", tests.len()),
                });
            }
        }
        if let Some(line) = lines.iter().position(|l| module_skip_line(l)) {
            found.push(FormFinding { rel: rel.clone(), form: "module-skip", line: line as u32, detail: "module ごと skip する".to_string() });
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
        assert!(module_skip_line("pytest.skip(\"doeff 側の不足\", allow_module_level=True)"));
        assert!(module_skip_line("(pytest.skip \"x\" :allow-module-level True)"));
        assert!(module_skip_line("(setv pytestmark (pytest.mark.skipif True :reason \"x\"))"));
        assert!(module_skip_line("(pytest.skip (+ \"理由の 1 行目\""), "複数行の module の頭の skip を拾っていない");
        assert!(!module_skip_line("  (pytest.skip \"この検だけ\")"));
    }
}
