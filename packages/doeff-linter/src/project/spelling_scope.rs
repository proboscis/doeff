//! 宣言した file の集まりの本文を、註を落として読む共通の部品(DOEFF148 の :confined-spellings・DOEFF161 の :counted-spellings —
//! agora-redesign #1373・#1436・#1437)。
//!
//! 群の `:files`(repo の根に錨を下ろした glob)に当たり `:except` に当たらない file を集め、拡張子ごとの註を落とした本文を返す。
//! 文字列は落とさない — 外の口の動詞 `"POST"` や route の綴り `"/api/intake"` は文字列で書かれるので、文字列を数えない DOEFF146
//! (:single-point-vocabulary)では見えない。註は落とす(経緯を述べる註まで当たりにしない)。
//! 歩くのは glob の頭の、`*` を含まない段の dir だけ(repo 全体は歩かない)。

use std::path::Path;

use super::paths::{glob_matches, relative_path};

/// 歩かない dir(隠し dir と生成物)。
const SKIPPED_DIRS: &[&str] = &["node_modules", "target", "__pycache__", "venv", "site-packages"];

/// 読む file の拡張子(Hy・Python・Python の型の宣言)。
const READ_EXTENSIONS: &[&str] = &["hy", "py", "pyi"];

/// 読む file の拡張子の選び方(閉じた 2 つ)。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Extensions {
    /// Hy・Python・Python の型の宣言だけ(glob が dir を名指す群 — 設定や文書を拾わない)。
    Code,
    /// glob に当たる file を拡張子を問わず(名指した設定の file — semgrep の yaml など)。
    Any,
}

/// 註の書き方(閉じた 3 つ)。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum CommentStyle {
    /// Hy: `;` から行末。文字列は `"…"`。
    Hy,
    /// Python: `#` から行末。文字列は `'…'`・`"…"`・三重の引用符。
    Python,
    /// 註を落とさない(Hy と Python の外の file はそのまま読む)。
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

/// 註を空白に置き換えた本文(文字列はそのまま・byte の位置と行は保つ — 定義の範囲を元の本文の byte で引くため)。
pub fn code_text(rel: &str, source: &str) -> String {
    let style = style_of(rel);
    if style == CommentStyle::Plain {
        return source.to_string();
    }
    let chars: Vec<char> = source.chars().collect();
    let mut out = String::with_capacity(source.len());
    // 開いている文字列の閉じの綴り(None = 文字列の外)。
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

/// glob の頭の、`*` を含まない段を繋いだ dir(歩き始める所)。
fn walk_base(glob: &str) -> String {
    let parts: Vec<&str> = glob.split('/').collect();
    let fixed: Vec<&str> = parts.iter().take(parts.len().saturating_sub(1)).take_while(|p| !p.contains('*')).copied().collect();
    fixed.join("/")
}

/// `:files` に当たり `:except` に当たらない、読む拡張子の file(根からの path・path の順・重なりなし)。
pub fn selected_files(root: &Path, files: &[String], except: &[String]) -> Vec<String> {
    selected(root, files, except, Extensions::Code)
}

/// `:files` に当たり `:except` に当たらない file(拡張子の選び方を名指す)。
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

/// 本文の byte の位置の行(0 始まり)。
pub fn line_of(text: &str, at: usize) -> u32 {
    text.get(..at).map_or(0, |head| head.matches('\n').count() as u32)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn hy_comments_are_blanked_and_strings_are_kept() {
        let code = code_text("a.hy", "(setv v \"POST\") ; \"PUT\" は書かない\n(f \"a;b\")\n");
        assert!(code.contains("\"POST\""), "{}", code);
        assert!(!code.contains("PUT"), "{}", code);
        assert!(code.contains("\"a;b\""), "{}", code);
        assert_eq!(code.lines().count(), 2);
    }

    #[test]
    fn python_comments_are_blanked_and_strings_are_kept() {
        let code = code_text("a.py", "x = \"#1\"  # sqlite3\ny = '''\n# 中\n'''\nz = 'it\\'s'  # open(\n");
        assert!(code.contains("\"#1\""), "{}", code);
        assert!(!code.contains("sqlite3"), "{}", code);
        assert!(code.contains("# 中"), "三重の引用符の中は文字列: {}", code);
        assert!(!code.contains("open("), "{}", code);
        assert_eq!(code.lines().count(), 5);
    }

    #[test]
    fn byte_positions_are_kept_and_other_files_are_read_as_they_are() {
        let source = "; 日本語の註\n(f \"POST\")\n";
        let code = code_text("a.hy", source);
        assert_eq!(code.len(), source.len());
        assert_eq!(code.find("\"POST\""), source.find("\"POST\""));
        assert_eq!(code_text("a.yaml", "id: x # 註\n"), "id: x # 註\n");
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
