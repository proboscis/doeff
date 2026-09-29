//! DOEFF148: 書いてよい file を決めた綴り(agora-redesign #1373・#1436 — 元は agora-controllers の一時の判定
//! controllers/screen/tests/intake_only_rules.hy の 3 の一部・4・6・7 の語・8 の前半・9)。
//!
//! architecture.hy の `:confined-spellings [(confined-spelling "名" :patterns [r"…"] :files [..] :except [..] :why "…") …]` の群ごとに、
//! `:files` に当たり `:except` に当たらない Hy・Python の file を読み、註を落とした本文(文字列は残す)に `:patterns` のどれかが
//! 当たれば file ごとに 1 件出す(当たった数を message に書く・位置は最初の当たり)。`:except` はこの綴りを書いてよい file で、
//! 空なら `:files` のどこにも書かない綴り。`:files` に当たる file が 1 つも無ければ architecture.hy の位置で 1 件(`missing` —
//! 母集団 0 を緑にしない)。
//! DOEFF146(:single-point-vocabulary)と違い、文字列の中も数え、Python の file も読む — 外の口の動詞 `"POST"`・route の綴り
//! `"/api/intake"`・effect の宣言の file(effects.py)は、文字列か Python の中に在る。

use std::path::{Path, PathBuf};

use doeff_indexer::hy_index::Range;

use super::architecture::ConfinedSpelling;
use super::spelling_scope::{code_text, line_of, selected_files};

/// 当たりの種類(閉じた 2 つ)。
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ConfinedProblem {
    /// 書いてよい file の外に count 件在る。
    Outside { count: usize },
    /// `:files` に当たる file が無い。
    Missing,
}

/// 当たり 1 つ(file × 群)。
#[derive(Debug, Clone)]
pub struct ConfinedFinding {
    pub rel: String,
    /// 群の名(登録簿の鍵の細目 — missing は `<群>:missing`)。
    pub group: String,
    pub why: String,
    pub range: Range,
    pub problem: ConfinedProblem,
}

/// 群ごとに `:except` の外の当たりを探す(群の宣言の順・path の順)。読めなかった file は errors へ積む。focus(命令の行の名指し)が
/// 在れば、その下の file だけを読む — 当たりは file ごとに 1 件でその file に付くので、答えは全部を読んで名指しで絞った時と同じ
/// (母集団 0 の知らせは glob で決まり、読まない)。1 file の commit の hook で群ごとに母集団を全部読んでいた(agora-redesign #1418)。
pub fn find(root: &Path, groups: &[ConfinedSpelling], architecture_rel: &str, focus: Option<&[PathBuf]>) -> (Vec<ConfinedFinding>, Vec<String>) {
    let mut out = Vec::new();
    let mut errors = Vec::new();
    for group in groups {
        let regexes: Vec<regex::Regex> = group.patterns.iter().filter_map(|p| regex::Regex::new(p).ok()).collect();
        let population = selected_files(root, &group.files, &[]);
        if population.is_empty() {
            out.push(ConfinedFinding {
                rel: architecture_rel.to_string(),
                group: group.name.clone(),
                why: group.why.clone(),
                range: group.range,
                problem: ConfinedProblem::Missing,
            });
            continue;
        }
        for rel in population
            .iter()
            .filter(|rel| !group.except.iter().any(|p| super::glob_matches(p, rel)))
            .filter(|rel| focus.is_none_or(|only| only.iter().any(|p| root.join(rel.as_str()).starts_with(p))))
        {
            let source = match std::fs::read_to_string(root.join(rel)) {
                Ok(source) => source,
                Err(error) => {
                    errors.push(format!("{}: 読めない: {}", rel, error));
                    continue;
                }
            };
            let code = code_text(rel, &source);
            let mut starts: Vec<usize> = regexes.iter().flat_map(|re| re.find_iter(&code).map(|m| m.start())).collect();
            if starts.is_empty() {
                continue;
            }
            starts.sort_unstable();
            starts.dedup();
            let line = line_of(&code, starts[0]);
            let at = doeff_indexer::hy_index::Position { line, character: 0 };
            out.push(ConfinedFinding {
                rel: rel.clone(),
                group: group.name.clone(),
                why: group.why.clone(),
                range: Range { start: at, end: at },
                problem: ConfinedProblem::Outside { count: starts.len() },
            });
        }
    }
    (out, errors)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn group(patterns: &[&str], files: &[&str], except: &[&str]) -> ConfinedSpelling {
        ConfinedSpelling {
            name: "g".to_string(),
            patterns: patterns.iter().map(|p| p.to_string()).collect(),
            files: files.iter().map(|p| p.to_string()).collect(),
            except: except.iter().map(|p| p.to_string()).collect(),
            why: "理由".to_string(),
            range: Range::default(),
        }
    }

    #[test]
    fn a_spelling_in_a_string_is_counted_but_not_in_a_comment() {
        let dir = tempfile::tempdir().unwrap();
        std::fs::create_dir_all(dir.path().join("s")).unwrap();
        std::fs::write(dir.path().join("s/a.hy"), "; \"/api/intake\" は退いた\n(setv u \"/api/intake\")\n").unwrap();
        std::fs::write(dir.path().join("s/b.py"), "# /api/intake\nx = 1\n").unwrap();
        let (hits, errors) = find(dir.path(), &[group(&[r"/api/intake"], &["s/**"], &[])], "architecture.hy", None);
        assert!(errors.is_empty(), "{:?}", errors);
        assert_eq!(hits.len(), 1, "{:?}", hits);
        assert_eq!(hits[0].rel, "s/a.hy");
        assert_eq!(hits[0].range.start.line, 1);
        assert_eq!(hits[0].problem, ConfinedProblem::Outside { count: 1 });
    }

    #[test]
    fn the_except_files_may_hold_the_spelling() {
        let dir = tempfile::tempdir().unwrap();
        std::fs::create_dir_all(dir.path().join("s")).unwrap();
        std::fs::write(dir.path().join("s/a.hy"), "(PostIntake x)\n(PostIntake y)\n").unwrap();
        std::fs::write(dir.path().join("s/b.hy"), "(PostIntake x)\n").unwrap();
        let (hits, _) = find(dir.path(), &[group(&[r"\bPostIntake\b"], &["s/**"], &["s/b.hy"])], "architecture.hy", None);
        assert_eq!(hits.len(), 1, "{:?}", hits);
        assert_eq!(hits[0].rel, "s/a.hy");
        assert_eq!(hits[0].problem, ConfinedProblem::Outside { count: 2 });
    }

    #[test]
    fn an_empty_population_is_missing_not_green() {
        let dir = tempfile::tempdir().unwrap();
        let (hits, _) = find(dir.path(), &[group(&[r"x"], &["gone/**"], &[])], "architecture.hy", None);
        assert_eq!(hits.len(), 1, "{:?}", hits);
        assert_eq!(hits[0].rel, "architecture.hy");
        assert_eq!(hits[0].problem, ConfinedProblem::Missing);
    }

    #[test]
    fn named_paths_read_only_the_named_files() {
        // agora-redesign #1418: 名指しの下の file だけを読む — 名指しの外の読めない file(UTF-8 でない)を読まず、当たりは名指しの分だけ。
        let dir = tempfile::tempdir().unwrap();
        std::fs::create_dir_all(dir.path().join("s")).unwrap();
        std::fs::write(dir.path().join("s/a.hy"), "(PostIntake x)\n").unwrap();
        std::fs::write(dir.path().join("s/b.hy"), "(PostIntake y)\n").unwrap();
        std::fs::write(dir.path().join("s/broken.hy"), [0xff_u8, 0xfe]).unwrap();
        let groups = [group(&[r"\bPostIntake\b"], &["s/**"], &[])];
        let (all, all_errors) = find(dir.path(), &groups, "architecture.hy", None);
        assert_eq!(all.iter().map(|h| h.rel.as_str()).collect::<Vec<_>>(), vec!["s/a.hy", "s/b.hy"]);
        assert_eq!(all_errors.len(), 1, "{:?}", all_errors);
        let named = [dir.path().join("s/a.hy")];
        let (hits, errors) = find(dir.path(), &groups, "architecture.hy", Some(&named));
        assert!(errors.is_empty(), "{:?}", errors);
        assert_eq!(hits.iter().map(|h| h.rel.as_str()).collect::<Vec<_>>(), vec!["s/a.hy"]);
    }
}
