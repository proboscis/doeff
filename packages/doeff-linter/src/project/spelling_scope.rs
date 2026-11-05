//! Shared component that reads the text of a set of declared files with comments removed (:confined-spellings of DOEFF148 and :counted-spellings of DOEFF161 —
//! #1436, #1437).
//!
//! Collects the files that match a group's `:files` (globs anchored at the repo root) and do not match `:except`, and returns their text with comments removed per extension.
//! Strings are not removed — outward-facing verbs like `"POST"` and route spellings like `"/api/intake"` are written as strings, so DOEFF146
//! (:single-point-vocabulary), which does not count strings, cannot see them. Comments are removed (so that even comments recounting history do not count as hits).
//! Only the leading dirs of a glob, the segments containing no `*`, are walked (the whole repo is not walked).

use std::path::Path;

use super::paths::{glob_matches, relative_path};

/// Dirs that are not walked (hidden dirs and generated output).
const SKIPPED_DIRS: &[&str] = &["node_modules", "target", "__pycache__", "venv", "site-packages"];

/// Extensions of files that are read (Hy, Python, Python type declarations).
const READ_EXTENSIONS: &[&str] = &["hy", "py", "pyi"];

/// How the extensions to read are chosen (a closed set of 2).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Extensions {
    /// Only Hy, Python, and Python type declarations (for groups whose glob names a dir — does not pick up settings or documents).
    Code,
    /// Files matching the glob regardless of extension (named settings files — such as semgrep yaml).
    Any,
}

/// How comments are written (a closed set of 3).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum CommentStyle {
    /// Hy: from `;` to the end of the line. Strings are `"…"`.
    Hy,
    /// Python: from `#` to the end of the line. Strings are `'…'`, `"…"`, and triple quotes.
    Python,
    /// Comments are not removed (files other than Hy and Python are read as they are).
    Plain,
}

fn style_of(rel: &str) -> CommentStyle {
    if rel.ends_with(".hy") {
        CommentStyle::Hy
    } else if rel.ends_with(".py") || rel.ends_with(".pyi") {
        CommentStyle::Python
    } else {
        CommentStyle::Plain
    }
}

/// The text with comments replaced by whitespace (strings kept as they are; byte positions and lines are preserved — so that definition ranges can be taken in bytes of the original text).
pub fn code_text(rel: &str, source: &str) -> String {
    let style = style_of(rel);
    if style == CommentStyle::Plain {
        return source.to_string();
    }
    let chars: Vec<char> = source.chars().collect();
    let mut out = String::with_capacity(source.len());
    // The closing spelling of the open string (None = outside a string).
    let mut closing: Option<Vec<char>> = None;
    let mut index = 0;
    while index < chars.len() {
        let ch = chars[index];
        if let Some(close) = &closing {
            if ch == '\\' {
                out.push(ch);
                if let Some(&next) = chars.get(index + 1) {
                    out.push(next);
                }
                index += 2;
                continue;
            }
            if chars[index..].starts_with(close) {
                out.extend(close.iter());
                index += close.len();
                closing = None;
                continue;
            }
            out.push(ch);
            index += 1;
            continue;
        }
        let comment = match style {
            CommentStyle::Hy => ch == ';',
            CommentStyle::Python => ch == '#',
            CommentStyle::Plain => false,
        };
        if comment {
            while index < chars.len() && chars[index] != '\n' {
                out.extend(std::iter::repeat_n(' ', chars[index].len_utf8()));
                index += 1;
            }
            continue;
        }
        let opens = match style {
            CommentStyle::Hy => ch == '"',
            CommentStyle::Python => ch == '"' || ch == '\'',
            CommentStyle::Plain => false,
        };
        if opens {
            let triple = style == CommentStyle::Python && chars.get(index + 1) == Some(&ch) && chars.get(index + 2) == Some(&ch);
            let close: Vec<char> = if triple { vec![ch, ch, ch] } else { vec![ch] };
            out.extend(close.iter());
            index += close.len();
            closing = Some(close);
            continue;
        }
        out.push(ch);
        index += 1;
    }
    out
}

/// The dir formed by joining the leading segments of the glob that contain no `*` (where the walk starts).
fn walk_base(glob: &str) -> String {
    let parts: Vec<&str> = glob.split('/').collect();
    let fixed: Vec<&str> = parts.iter().take(parts.len().saturating_sub(1)).take_while(|p| !p.contains('*')).copied().collect();
    fixed.join("/")
}

/// Files with a readable extension that match `:files` and do not match `:except` (paths from the root, in path order, no duplicates).
pub fn selected_files(root: &Path, files: &[String], except: &[String]) -> Vec<String> {
    selected(root, files, except, Extensions::Code)
}

/// Files that match `:files` and do not match `:except` (naming how the extension is chosen).
pub fn selected(root: &Path, files: &[String], except: &[String], extensions: Extensions) -> Vec<String> {
    let mut rels: Vec<String> = Vec::new();
    for glob in files {
        let base = root.join(walk_base(glob));
        if !base.exists() {
            continue;
        }
        let walker = walkdir::WalkDir::new(&base).follow_links(false).into_iter().filter_entry(|entry| {
            let name = entry.file_name().to_string_lossy();
            entry.depth() == 0 || !entry.file_type().is_dir() || !(name.starts_with('.') || SKIPPED_DIRS.contains(&name.as_ref()))
        });
        rels.extend(
            walker
                .filter_map(Result::ok)
                .filter(|entry| entry.file_type().is_file())
                .filter(|entry| {
                    extensions == Extensions::Any || entry.path().extension().is_some_and(|e| READ_EXTENSIONS.iter().any(|x| e == *x))
                })
                .filter_map(|entry| relative_path(root, entry.path()))
                .filter(|rel| glob_matches(glob, rel))
                .filter(|rel| !except.iter().any(|p| glob_matches(p, rel))),
        );
    }
    rels.sort();
    rels.dedup();
    rels
}

/// The line (0-based) at a byte position in the text.
pub fn line_of(text: &str, at: usize) -> u32 {
    text.get(..at).map_or(0, |head| head.matches('\n').count() as u32)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn hy_comments_are_blanked_and_strings_are_kept() {
        let code = code_text("a.hy", "(setv v \"POST\") ; do not write \"PUT\"\n(f \"a;b\")\n");
        assert!(code.contains("\"POST\""), "{}", code);
        assert!(!code.contains("PUT"), "{}", code);
        assert!(code.contains("\"a;b\""), "{}", code);
        assert_eq!(code.lines().count(), 2);
    }

    #[test]
    fn python_comments_are_blanked_and_strings_are_kept() {
        let code = code_text("a.py", "x = \"#1\"  # sqlite3\ny = '''\n# inner\n'''\nz = 'it\\'s'  # open(\n");
        assert!(code.contains("\"#1\""), "{}", code);
        assert!(!code.contains("sqlite3"), "{}", code);
        assert!(code.contains("# inner"), "inside triple quotes is a string: {}", code);
        assert!(!code.contains("open("), "{}", code);
        assert_eq!(code.lines().count(), 5);
    }

    #[test]
    fn byte_positions_are_kept_and_other_files_are_read_as_they_are() {
        let source = "; Japanese comment\n(f \"POST\")\n";
        let code = code_text("a.hy", source);
        assert_eq!(code.len(), source.len());
        assert_eq!(code.find("\"POST\""), source.find("\"POST\""));
        assert_eq!(code_text("a.yaml", "id: x # note\n"), "id: x # note\n");
    }

    #[test]
    fn the_walk_starts_at_the_fixed_head_of_the_glob() {
        assert_eq!(walk_base("controllers/screen/**"), "controllers/screen");
        assert_eq!(walk_base("controllers/screen/protocol/assets.hy"), "controllers/screen/protocol");
        assert_eq!(walk_base("**/x.hy"), "");
        assert_eq!(walk_base("x.hy"), "");
    }

    #[test]
    fn selected_files_honour_files_except_and_extensions() {
        let dir = tempfile::tempdir().unwrap();
        for rel in ["s/a.hy", "s/b.py", "s/c.txt", "s/tests/t.hy", "other/d.hy"] {
            let path = dir.path().join(rel);
            std::fs::create_dir_all(path.parent().unwrap()).unwrap();
            std::fs::write(path, "").unwrap();
        }
        let got = selected_files(dir.path(), &["s/**".to_string()], &["s/tests/**".to_string()]);
        assert_eq!(got, vec!["s/a.hy".to_string(), "s/b.py".to_string()]);
    }
}
