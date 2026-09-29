//! DOEFF146: 判定を 1 か所に閉じ込めた語彙(agora-redesign #1106・#1192・#1371 — 元は agora-controllers の一時の検
//! check_screen_slice_single_point.hy)。
//!
//! architecture.hy の `:single-point-vocabulary [(vocabulary-scope "名" :patterns [r"…"] :files [..] :except [..] :instead "…") …]`
//! で群を宣言する。群ごとに `:files` の glob に当たった Hy の file(`:except` に挙げた file を除く)を読み、註(`;` から行末・
//! 文字列の外)を落とした本文に `:patterns` のどれかが当たる行が 1 行でもあれば file ごとに 1 件出す(critical)。判定は `:except`
//! の file(通常は 1 つ = 判定の 1 点)だけが持ってよく、他の file がこの語彙を読むと「第 2 の判定」が生えたことになる。
//! 註を落とすのは DOEFF150(:retired-words)と逆の判断 — あちらは「使わないと決めた綴りは文書にも効く」ので註も数えるが、
//! この規則は「判定の二重化」を見るので、判定でないことを述べる註(例: 「routedTo はここで読まない」)まで赤にしない。
//! 判定は字面の行で読む(この語彙を使わずに済む形かは linter には分からない — repo の宣言が「これは判定の語彙だ」と決める)。

use std::path::Path;

use super::architecture::VocabularyScope;
use super::{glob_matches, relative_path};

/// 文字列と註(`;` から行末)を空白に置き換える(括弧の対応と行・列の位置は保つ)。
fn strip_comments(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    let mut in_string = false;
    let mut chars = text.chars().peekable();
    while let Some(ch) = chars.next() {
        if in_string {
            out.push(if ch == '\n' { '\n' } else { ' ' });
            if ch == '\\' {
                if let Some(&next) = chars.peek() {
                    out.push(if next == '\n' { '\n' } else { ' ' });
                    chars.next();
                }
            } else if ch == '"' {
                in_string = false;
            }
            continue;
        }
        match ch {
            '"' => {
                in_string = true;
                out.push(' ');
            }
            ';' => {
                for c in chars.by_ref() {
                    if c == '\n' {
                        out.push('\n');
                        break;
                    }
                    out.push(' ');
                }
            }
            _ => out.push(ch),
        }
    }
    out
}

/// 見つけた当たり 1 つ(file × 群)。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct VocabularyHit {
    pub rel: String,
    /// 群の名(登録簿の鍵の細目)。
    pub group: String,
    /// 最初に当たった行(0 始まり)。
    pub line: u32,
    /// 当たった行の数。
    pub count: usize,
    pub instead: String,
}

/// 歩かない dir(隠し dir と生成物)。
const SKIPPED_DIRS: &[&str] = &["node_modules", "target", "__pycache__", "venv", "site-packages"];

/// `:single-point-vocabulary` の群ごとに `:except` の外の当たりを探す(path の順・群の宣言の順)。
pub fn find(root: &Path, scopes: &[VocabularyScope]) -> Vec<VocabularyHit> {
    let mut out = Vec::new();
    for scope in scopes {
        let regexes: Vec<regex::Regex> = scope.patterns.iter().filter_map(|p| regex::Regex::new(p).ok()).collect();
        if regexes.is_empty() {
            continue;
        }
        let walker = walkdir::WalkDir::new(root).follow_links(false).into_iter().filter_entry(|entry| {
            let name = entry.file_name().to_string_lossy();
            entry.depth() == 0 || !entry.file_type().is_dir() || !(name.starts_with('.') || SKIPPED_DIRS.contains(&name.as_ref()))
        });
        let mut rels: Vec<String> = walker
            .filter_map(Result::ok)
            .filter(|entry| entry.file_type().is_file())
            .filter(|entry| entry.path().extension().is_some_and(|e| e == "hy"))
            .filter_map(|entry| relative_path(root, entry.path()))
            .filter(|rel| scope.files.iter().any(|p| glob_matches(p, rel)))
            .filter(|rel| !scope.except.iter().any(|p| glob_matches(p, rel)))
            .collect();
        rels.sort();
        for rel in rels {
            let Ok(source) = std::fs::read_to_string(root.join(&rel)) else { continue };
            let code = strip_comments(&source);
            let mut first: Option<u32> = None;
            let mut count = 0usize;
            for (idx, line) in code.lines().enumerate() {
                if regexes.iter().any(|re| re.is_match(line)) {
                    count += 1;
                    if first.is_none() {
                        first = Some(idx as u32);
                    }
                }
            }
            if let Some(line) = first {
                out.push(VocabularyHit { rel: rel.clone(), group: scope.name.clone(), line, count, instead: scope.instead.clone() });
            }
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_pattern_hit_is_counted_and_positioned_at_its_first_line() {
        let dir = tempfile::tempdir().unwrap();
        std::fs::create_dir_all(dir.path().join("glue")).unwrap();
        std::fs::write(dir.path().join("glue/queue.hy"), "(setv a 1)\n(when (= phase JOB-PHASE-RUNNING) 1)\n(when (= phase JOB-PHASE-ENDED) 2)\n").unwrap();
        std::fs::write(dir.path().join("glue/slice.hy"), "(setv JOB-PHASE-RUNNING \"Running\")\n").unwrap();
        let scopes = vec![VocabularyScope {
            name: "job-phase".to_string(),
            patterns: vec![r"\bJOB-PHASE-[A-Z]+\b".to_string()],
            files: vec!["glue/**".to_string()],
            except: vec!["glue/slice.hy".to_string()],
            instead: "glue/slice.hy の答えを読む".to_string(),
        }];
        let hits = find(dir.path(), &scopes);
        assert_eq!(hits.len(), 1, "{:?}", hits);
        assert_eq!(hits[0].rel, "glue/queue.hy");
        assert_eq!(hits[0].group, "job-phase");
        assert_eq!(hits[0].line, 1);
        assert_eq!(hits[0].count, 2);
    }

    #[test]
    fn a_mention_inside_a_comment_or_string_is_not_counted() {
        let dir = tempfile::tempdir().unwrap();
        std::fs::create_dir_all(dir.path().join("glue")).unwrap();
        std::fs::write(
            dir.path().join("glue/queue.hy"),
            "; JOB-PHASE-RUNNING はここで読まない\n(setv label \"JOB-PHASE-RUNNING\")\n(when (= phase JOB-PHASE-RUNNING) 1)\n",
        )
        .unwrap();
        std::fs::write(dir.path().join("glue/slice.hy"), "(setv JOB-PHASE-RUNNING \"Running\")\n").unwrap();
        let scopes = vec![VocabularyScope {
            name: "job-phase".to_string(),
            patterns: vec![r"\bJOB-PHASE-[A-Z]+\b".to_string()],
            files: vec!["glue/**".to_string()],
            except: vec!["glue/slice.hy".to_string()],
            instead: "glue/slice.hy の答えを読む".to_string(),
        }];
        let hits = find(dir.path(), &scopes);
        assert_eq!(hits.len(), 1, "{:?}", hits);
        assert_eq!(hits[0].line, 2, "註と文字列の中の当たりは数えない: {:?}", hits);
        assert_eq!(hits[0].count, 1);
    }

    #[test]
    fn non_hy_files_are_not_walked() {
        let dir = tempfile::tempdir().unwrap();
        std::fs::create_dir_all(dir.path().join("glue")).unwrap();
        std::fs::write(dir.path().join("glue/queue.py"), "JOB_PHASE_RUNNING = 1\n# JOB-PHASE-RUNNING\n").unwrap();
        let scopes = vec![VocabularyScope {
            name: "job-phase".to_string(),
            patterns: vec![r"\bJOB-PHASE-[A-Z]+\b".to_string()],
            files: vec!["glue/**".to_string()],
            except: vec![],
            instead: "glue/slice.hy の答えを読む".to_string(),
        }];
        assert!(find(dir.path(), &scopes).is_empty());
    }

    #[test]
    fn the_except_file_itself_is_never_a_hit() {
        let dir = tempfile::tempdir().unwrap();
        std::fs::create_dir_all(dir.path().join("glue")).unwrap();
        std::fs::write(dir.path().join("glue/slice.hy"), "(setv JOB-PHASE-RUNNING \"Running\")\n").unwrap();
        let scopes = vec![VocabularyScope {
            name: "job-phase".to_string(),
            patterns: vec![r"\bJOB-PHASE-[A-Z]+\b".to_string()],
            files: vec!["glue/**".to_string()],
            except: vec!["glue/slice.hy".to_string()],
            instead: "glue/slice.hy の答えを読む".to_string(),
        }];
        assert!(find(dir.path(), &scopes).is_empty());
    }
}
