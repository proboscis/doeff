//! DOEFF146: vocabulary whose judgment is confined to one place (#1192, #1371 — originally a temporary check in agora-controllers
//! check_screen_slice_single_point.hy)。
//!
//! Declares groups with `:single-point-vocabulary [(vocabulary-scope "name" :patterns [r"…"] :files [..] :except [..] :instead "…") …]` in architecture.hy.
//! For each group, reads the Hy files matching the `:files` glob (excluding files listed in `:except`), and if any line of the body with comments (from `;` to end of line,
//! outside strings) removed matches any of `:patterns`, emits one hit per file (critical). Only the `:except`
//! files (normally 1 = the single point of the judgment) may hold the judgment; if another file reads this vocabulary, a "second judgment" has sprouted.
//! Removing comments is a decision in the same direction as DOEFF150 (:retired-words' :in lines — comments and docstrings are not counted) —
//! since this rule looks at "duplication of judgment", it does not turn red even for comments that state something is not a judgment (e.g. "routedTo is not read here").
//! The judgment is read on literal lines (the linter cannot tell whether a form avoids using this vocabulary — the repo's declaration decides "this is the judgment vocabulary").
//! However, lines that only copy a field 1:1 (`:routed-to d.routed-to`, `"routedTo" request.routed-to` — no decision or branch) are not counted
//! (decision Q2 of #1762). A line that carries a value as is holds no judgment, and counting it would make even lines that just pass a field outside the
//! single point of the judgment a "second judgment".

use std::path::{Path, PathBuf};

use once_cell::sync::Lazy;
use regex::Regex;

use super::architecture::VocabularyScope;
use super::paths::{glob_matches, relative_path};

/// Forms of decision/branch (applied to lines with comments and strings removed) — a line containing one is not a copy-only line.
static JUDGMENT: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"\((?:if|when|unless|cond|match|case|and|or|not|is|is-not|in|not-in|=|!=|<|>|<=|>=)[\s)]|:if\b").unwrap()
});

/// One pair that copies a field = a key (keyword `:name` or a string key) and the attribute read after it (`name.field` — one or more levels). Applied to the original line.
static COPY_PAIR: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r#"(?::[A-Za-z][\w-]*|"(?:[^"\\]|\\.)*")\s+[A-Za-z_][\w-]*(?:\.[A-Za-z_][\w-]*)+"#).unwrap()
});

/// Byte position within a line -> character index (the copy with comments and strings removed puts one character per character, so character indices line up with the original line —
/// byte positions drift at multi-byte characters).
fn char_index(line: &str, byte: usize) -> usize {
    line[..byte].chars().count()
}

/// Whether it is a line that only copies fields 1:1: it contains no decision/branch form, and every vocabulary hit is inside a pair of "key + attribute read" (#1800).
/// code = the line with comments and strings removed (hits are searched here — inside comments and strings is not counted); source = the original text of the same line (string keys are read).
fn copies_only(code: &str, source: &str, regexes: &[Regex]) -> bool {
    if JUDGMENT.is_match(code) {
        return false;
    }
    let pairs: Vec<(usize, usize)> =
        COPY_PAIR.find_iter(source).map(|m| (char_index(source, m.start()), char_index(source, m.end()))).collect();
    let mut hits = regexes.iter().flat_map(|re| re.find_iter(code)).peekable();
    hits.peek().is_some()
        && hits.all(|m| {
            let (start, end) = (char_index(code, m.start()), char_index(code, m.end()));
            pairs.iter().any(|&(from, to)| from <= start && end <= to)
        })
}

/// Replaces strings and comments (from `;` to end of line) with whitespace (preserving bracket matching and line/column positions).
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

/// One hit found (file x group).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct VocabularyHit {
    pub rel: String,
    /// Name of the group (detail of the registry key).
    pub group: String,
    /// The first line that matched (0-based).
    pub line: u32,
    /// The number of lines that matched.
    pub count: usize,
    pub instead: String,
}

/// Dirs not walked (hidden dirs and generated output).
const SKIPPED_DIRS: &[&str] = &["node_modules", "target", "__pycache__", "venv", "site-packages"];

/// Hy files to walk (path from the root, in path order) — if focus (the path named on the command line) exists, walks only under it; otherwise walks the whole root.
/// Files under hidden dirs and generated output are counted in neither way of walking (so that even if the named path is inside them, the answer matches the whole run).
fn walked_hy_files(root: &Path, focus: Option<&[PathBuf]>) -> Vec<String> {
    let starts: Vec<PathBuf> = match focus {
        Some(paths) => paths.to_vec(),
        None => vec![root.to_path_buf()],
    };
    let mut rels: Vec<String> = starts
        .iter()
        .flat_map(|start| {
            walkdir::WalkDir::new(start).follow_links(false).into_iter().filter_entry(|entry| {
                let name = entry.file_name().to_string_lossy();
                entry.depth() == 0 || !entry.file_type().is_dir() || !(name.starts_with('.') || SKIPPED_DIRS.contains(&name.as_ref()))
            })
        })
        .filter_map(Result::ok)
        .filter(|entry| entry.file_type().is_file())
        .filter(|entry| entry.path().extension().is_some_and(|e| e == "hy"))
        .filter_map(|entry| relative_path(root, entry.path()))
        .filter(|rel| {
            let dirs: Vec<&str> = rel.split('/').collect();
            !dirs[..dirs.len().saturating_sub(1)].iter().any(|d| d.starts_with('.') || SKIPPED_DIRS.contains(d))
        })
        .collect();
    rels.sort();
    rels.dedup();
    rels
}

/// For each group of `:single-point-vocabulary`, searches for hits outside `:except` (in group declaration order, path order). The repo is walked only once (not re-walked per group),
/// and if focus exists, only files under it are read — since a hit is attached to its file, the answer is the same as reading everything and then narrowing
/// by the named path (a one-file commit hook was reading all of the repo's Hy).
pub fn find(root: &Path, scopes: &[VocabularyScope], focus: Option<&[PathBuf]>) -> Vec<VocabularyHit> {
    let mut out = Vec::new();
    let walked = walked_hy_files(root, focus);
    for scope in scopes {
        let regexes: Vec<Regex> = scope.patterns.iter().filter_map(|p| Regex::new(p).ok()).collect();
        if regexes.is_empty() {
            continue;
        }
        let rels = walked
            .iter()
            .filter(|rel| scope.files.iter().any(|p| glob_matches(p, rel)))
            .filter(|rel| !scope.except.iter().any(|p| glob_matches(p, rel)))
            .cloned();
        for rel in rels {
            let Ok(source) = std::fs::read_to_string(root.join(&rel)) else { continue };
            let code = strip_comments(&source);
            let mut first: Option<u32> = None;
            let mut count = 0usize;
            for (idx, (line, written)) in code.lines().zip(source.lines()).enumerate() {
                if regexes.iter().any(|re| re.is_match(line)) && !copies_only(line, written, &regexes) {
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
            instead: "read the answer in glue/slice.hy".to_string(),
        }];
        let hits = find(dir.path(), &scopes, None);
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
            "; JOB-PHASE-RUNNING is not read here\n(setv label \"JOB-PHASE-RUNNING\")\n(when (= phase JOB-PHASE-RUNNING) 1)\n",
        )
        .unwrap();
        std::fs::write(dir.path().join("glue/slice.hy"), "(setv JOB-PHASE-RUNNING \"Running\")\n").unwrap();
        let scopes = vec![VocabularyScope {
            name: "job-phase".to_string(),
            patterns: vec![r"\bJOB-PHASE-[A-Z]+\b".to_string()],
            files: vec!["glue/**".to_string()],
            except: vec!["glue/slice.hy".to_string()],
            instead: "read the answer in glue/slice.hy".to_string(),
        }];
        let hits = find(dir.path(), &scopes, None);
        assert_eq!(hits.len(), 1, "{:?}", hits);
        assert_eq!(hits[0].line, 2, "hits inside comments and strings are not counted: {:?}", hits);
        assert_eq!(hits[0].count, 1);
    }

    fn routed_to_scope() -> Vec<VocabularyScope> {
        vec![VocabularyScope {
            name: "routed-to".to_string(),
            patterns: vec![r"\broutedTo\b".to_string(), r"\brouted-to\b".to_string()],
            files: vec!["glue/**".to_string()],
            except: vec!["glue/slice.hy".to_string()],
            instead: "read the answer in glue/slice.hy".to_string(),
        }]
    }

    #[test]
    fn a_line_that_only_copies_the_field_is_not_counted() {
        // Lines that only copy a field 1:1 (a keyword key, a string key, a line preceded by a Japanese comment) are not counted.
        let dir = tempfile::tempdir().unwrap();
        std::fs::create_dir_all(dir.path().join("glue")).unwrap();
        std::fs::write(
            dir.path().join("glue/rows.hy"),
            concat!(
                ";; 宛先の会話を写す(判定は slice.hy)\n",
                "(.append requests (HoldingRequest :id ask-id :routed-to mail.routed-to :routed-at mail.routed-at))\n",
                "{\"routedTo\" request.routed-to \"class\" request.request-class}\n",
                "(Row :note \"宛先\" :routed-to d.routed-to)\n",
            ),
        )
        .unwrap();
        assert!(find(dir.path(), &routed_to_scope(), None).is_empty());
    }

    #[test]
    fn a_line_that_judges_the_field_is_still_counted() {
        // Counterexample (#1800): even for the same field, a line with a decision/branch, a read with no key, or a line that uses the field as a key and puts another value is not copy-only — counted line by line.
        let dir = tempfile::tempdir().unwrap();
        std::fs::create_dir_all(dir.path().join("glue")).unwrap();
        std::fs::write(
            dir.path().join("glue/joins.hy"),
            concat!(
                ":routed-to (if (is d None) None d.routed-to)\n",
                "(when (= d.routed-to chat) (hold d))\n",
                "(lfor d rows :if (= d.routed-to chat) d)\n",
                "(setv target d.routed-to)\n",
                ":routed-to (pick-carrier d)\n",
            ),
        )
        .unwrap();
        let hits = find(dir.path(), &routed_to_scope(), None);
        assert_eq!(hits.len(), 1, "{:?}", hits);
        assert_eq!((hits[0].line, hits[0].count), (0, 5), "{:?}", hits);
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
            instead: "read the answer in glue/slice.hy".to_string(),
        }];
        assert!(find(dir.path(), &scopes, None).is_empty());
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
            instead: "read the answer in glue/slice.hy".to_string(),
        }];
        assert!(find(dir.path(), &scopes, None).is_empty());
    }

    #[test]
    fn named_paths_read_only_the_named_files_with_the_same_answer() {
        // A named run's hits are the same as the whole run's hits narrowed by the named path (b.hy outside the named path does not appear).
        // Files under hidden dirs are not counted even when named (same as the whole run). Unreadable files are silently skipped even in a whole run (as before).
        let dir = tempfile::tempdir().unwrap();
        std::fs::create_dir_all(dir.path().join("glue")).unwrap();
        std::fs::create_dir_all(dir.path().join("glue/.hidden")).unwrap();
        std::fs::write(dir.path().join("glue/a.hy"), "(when (= phase JOB-PHASE-RUNNING) 1)\n").unwrap();
        std::fs::write(dir.path().join("glue/b.hy"), "(when (= phase JOB-PHASE-ENDED) 2)\n").unwrap();
        std::fs::write(dir.path().join("glue/.hidden/c.hy"), "(when (= phase JOB-PHASE-ENDED) 3)\n").unwrap();
        std::fs::write(dir.path().join("glue/broken.hy"), [0xff_u8, 0xfe]).unwrap();
        let scopes = vec![VocabularyScope {
            name: "job-phase".to_string(),
            patterns: vec![r"\bJOB-PHASE-[A-Z]+\b".to_string()],
            files: vec!["glue/**".to_string()],
            except: vec![],
            instead: "read the answer".to_string(),
        }];
        let whole = find(dir.path(), &scopes, None);
        assert_eq!(whole.iter().map(|h| h.rel.as_str()).collect::<Vec<_>>(), vec!["glue/a.hy", "glue/b.hy"]);
        let named = [dir.path().join("glue/a.hy"), dir.path().join("glue/.hidden/c.hy")];
        let hits = find(dir.path(), &scopes, Some(&named));
        assert_eq!(hits, whole.into_iter().filter(|h| h.rel == "glue/a.hy").collect::<Vec<_>>());
    }
}
