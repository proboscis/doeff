//! DOEFF135: テストは deftest だけ(agora-redesign #1106 の R6・#1144 — operator 2026-09-27「検は deftest だけ」・#1104 の決定)。
//!
//! architecture.hy の `:test-forms {:tests [..] :check-scripts [..] :runners [..]}` の綴りの型で file を選び、deftest 以外のテストの形を
//! file ごとに 1 件出す(critical):
//!   * `python-test`  — :tests の Python の file の `def test_*`(pytest の形)。件数を message に書く。
//!   * `module-skip`  — :tests の file が module ごと skip する(`pytest.skip(…, allow_module_level=True)`・`pytestmark` の `mark.skip` — 束ねの名と
//!     印が別の行に在る複数行の束ねも括弧が閉じるまで読む・agora-redesign #1426)。
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
    let skip_mark = line.contains("pytestmark") && skip_mark_text(line);
    skip_call || skip_mark
}

/// 字面が skip の印(`mark.skip` / `mark.skipif`)を含むか。
fn skip_mark_text(text: &str) -> bool {
    text.contains("mark.skip")
}

/// 行が字下げの無い段で `pytestmark` を束ねる文の頭か(Hy の `(val pytestmark` / `(setv pytestmark`・Python の `pytestmark =`)。
/// 註や文字列の中の語は行の頭に来ないので当たらない。
fn pytestmark_binding_head(line: &str) -> bool {
    let rest = ["(val ", "(setv ", "(var "].iter().find_map(|head| line.strip_prefix(head)).map(str::trim_start);
    match rest {
        Some(rest) => rest.strip_prefix("pytestmark").is_some_and(|after| after.is_empty() || after.starts_with(char::is_whitespace)),
        None => line.strip_prefix("pytestmark").is_some_and(|after| after.trim_start().starts_with('=') || after.trim_start().starts_with(':')),
    }
}

/// start の行から始まる form を、括弧(`(` `[` `{`)が閉じるまで繋いだ字面(文字列の中の括弧は数えない — `\"` の escape は飛ばす)。
/// 閉じないまま file が終われば file の終わりまで。
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

/// module ごとの skip の位置(0 始まりの行)。1 行の形(module_skip_line)に加え、束ねの名と印が別の行に在る複数行の `pytestmark` の
/// 束ねも拾う(agora-redesign #1426 — `(val pytestmark` の次の行に `(pytest.mark.skip …` を置く形)。複数行の形は束ねの名の行を指す。
fn module_skip_position(lines: &[&str]) -> Option<usize> {
    lines
        .iter()
        .enumerate()
        .position(|(i, line)| module_skip_line(line) || (pytestmark_binding_head(line) && skip_mark_text(&balanced_form(lines, i))))
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
        if let Some(line) = module_skip_position(&lines) {
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
        let multiline = ["(import pytest)", ";; pytestmark の印で書く", "(val pytestmark", "  (pytest.mark.skip :reason (+ \"(括弧\"", "                              \"2 行目\")))", "(deftest test-x (pytest.mark.skip))"];
        assert_eq!(module_skip_position(&multiline), Some(2), "複数行の束ねを拾っていない");
        let python = ["pytestmark = [", "    pytest.mark.skip(reason=\"x\"),", "]"];
        assert_eq!(module_skip_position(&python), Some(0));
        let real_world = ["(val pytestmark", "  pytest.mark.real-world)", "(deftest test-y (pytest.mark.skip))"];
        assert_eq!(module_skip_position(&real_world), None, "束ねの後の検の skip を束ねに数えた");
        assert!(!pytestmark_binding_head("(val pytestmarks []"));
        assert!(!pytestmark_binding_head(";; (val pytestmark"));
    }
}
