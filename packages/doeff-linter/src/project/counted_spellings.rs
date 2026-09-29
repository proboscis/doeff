//! DOEFF161: 数を決めた綴り(agora-redesign #1373・#1437 — 元は agora-controllers の一時の判定
//! controllers/screen/tests/intake_only_rules.hy の 1・3 の数と座・4 の残り・5・7 の節・8 の後半・semgrep の規則の実在)。
//!
//! architecture.hy の `:counted-spellings [(counted-spelling "名" :pattern r"…" :files [..] :count N :why "…") …]` の宣言ごとに、
//! `:files` に当たる file(拡張子を問わない — Hy と Python は註を落とし、ほかはそのまま読む)の本文で `:pattern` の当たりを数え、
//! 数が決めた数でなければ 1 件出す。`:count N` はちょうど N・`:at-least N` は N 以上(どちらか 1 つ)。
//! `:within ["名" …]` があれば、`:files` の Hy の file の top level の定義(頭が def で始まる form)のうち名指した物ごとに、その form の
//! 中の当たりを数える(1 つの定義の中に閉じていることを決める — 数だけでは、定義の外に生えた当たりを見分けられない)。
//! `:files` に当たる file が無い・名指した定義が無いなら `missing`(母集団 0 を緑にしない)。

use std::path::Path;

use doeff_indexer::hy_index::reader::Reader;
use doeff_indexer::hy_index::{Position, Range};

use super::architecture::{CountedSpelling, WantedCount};
use super::spelling_scope::{code_text, line_of, selected, Extensions};
use super::top_level;

/// 当たりの種類(閉じた 2 つ)。
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum CountProblem {
    /// 数が合わない(within = 数えた定義の名・None は :files の全体)。
    Mismatch { found: usize, wanted: WantedCount, within: Option<String> },
    /// 数える所が無い。
    Missing { reason: String },
}

/// 当たり 1 つ。
#[derive(Debug, Clone)]
pub struct CountFinding {
    pub rel: String,
    /// 宣言の名。
    pub group: String,
    pub why: String,
    pub range: Range,
    pub problem: CountProblem,
    /// 登録簿の鍵の細目(`<名>` / `<名>:<定義>` / `<名>:missing` / `<名>:<定義>:missing`)。
    pub detail: String,
}

fn at_line(line: u32) -> Range {
    let at = Position { line, character: 0 };
    Range { start: at, end: at }
}

/// top level の、頭が def で始まり 2 つ目が name の form の byte の範囲。
fn definition_span(source: &str, name: &str) -> Option<(usize, usize)> {
    let forms = Reader::new(source, 0, source.len()).read_all();
    top_level::definition(source, &forms, name).map(|form| (form.span.start, form.span.end))
}

/// 宣言 1 つを判じる。読めなかった file は errors へ積む。
fn judge_one(root: &Path, declared: &CountedSpelling, architecture_rel: &str, errors: &mut Vec<String>) -> Vec<CountFinding> {
    let finding = |rel: String, range: Range, problem: CountProblem, detail: String| CountFinding {
        rel,
        group: declared.name.clone(),
        why: declared.why.clone(),
        range,
        problem,
        detail,
    };
    let Ok(regex) = regex::Regex::new(&declared.pattern) else { return Vec::new() };
    let population = selected(root, &declared.files, &[], Extensions::Any);
    if population.is_empty() {
        return vec![finding(
            architecture_rel.to_string(),
            declared.range,
            CountProblem::Missing { reason: ":files に当たる file が無い".to_string() },
            format!("{}:missing", declared.name),
        )];
    }
    // file ごとの (path, 元の本文, 註を落とした本文)。
    let mut texts: Vec<(String, String, String)> = Vec::new();
    for rel in population {
        match std::fs::read_to_string(root.join(&rel)) {
            Ok(source) => {
                let code = code_text(&rel, &source);
                texts.push((rel, source, code));
            }
            Err(error) => errors.push(format!("{}: 読めない: {}", rel, error)),
        }
    }
    if declared.within.is_empty() {
        let starts: Vec<(usize, usize)> =
            texts.iter().enumerate().flat_map(|(i, (_, _, code))| regex.find_iter(code).map(move |m| (i, m.start()))).collect();
        if declared.wanted.accepts(starts.len()) {
            return Vec::new();
        }
        let (rel, line) = match starts.first() {
            Some(&(i, at)) => (texts[i].0.clone(), line_of(&texts[i].2, at)),
            None => (texts.first().map(|t| t.0.clone()).unwrap_or_else(|| architecture_rel.to_string()), 0),
        };
        return vec![finding(
            rel,
            at_line(line),
            CountProblem::Mismatch { found: starts.len(), wanted: declared.wanted, within: None },
            declared.name.clone(),
        )];
    }
    let mut out = Vec::new();
    for name in &declared.within {
        let located = texts
            .iter()
            .filter(|(rel, _, _)| rel.ends_with(".hy"))
            .find_map(|(rel, source, code)| definition_span(source, name).map(|span| (rel, code, span)));
        let Some((rel, code, (start, end))) = located else {
            out.push(finding(
                architecture_rel.to_string(),
                declared.range,
                CountProblem::Missing { reason: format!(":files の Hy の file に定義 {} が無い", name) },
                format!("{}:{}:missing", declared.name, name),
            ));
            continue;
        };
        let found = regex.find_iter(code).filter(|m| m.start() >= start && m.start() < end).count();
        if !declared.wanted.accepts(found) {
            out.push(finding(
                rel.clone(),
                at_line(line_of(code, start)),
                CountProblem::Mismatch { found, wanted: declared.wanted, within: Some(name.clone()) },
                format!("{}:{}", declared.name, name),
            ));
        }
    }
    out
}

/// 宣言の全部を判じる。architecture_rel は architecture.hy の根からの path(数える所が無い宣言の位置)。
pub fn find(root: &Path, declarations: &[CountedSpelling], architecture_rel: &str) -> (Vec<CountFinding>, Vec<String>) {
    let mut errors = Vec::new();
    let found = declarations.iter().flat_map(|declared| judge_one(root, declared, architecture_rel, &mut errors)).collect();
    (found, errors)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn declared(pattern: &str, files: &[&str], within: &[&str], wanted: WantedCount) -> CountedSpelling {
        CountedSpelling {
            name: "g".to_string(),
            pattern: pattern.to_string(),
            files: files.iter().map(|p| p.to_string()).collect(),
            within: within.iter().map(|p| p.to_string()).collect(),
            wanted,
            why: "理由".to_string(),
            range: Range::default(),
        }
    }

    fn repo(files: &[(&str, &str)]) -> tempfile::TempDir {
        let dir = tempfile::tempdir().unwrap();
        for (rel, text) in files {
            let path = dir.path().join(rel);
            std::fs::create_dir_all(path.parent().unwrap()).unwrap();
            std::fs::write(path, text).unwrap();
        }
        dir
    }

    #[test]
    fn the_total_over_the_files_must_be_the_count_and_comments_do_not_count() {
        let dir = repo(&[("p/a.hy", "(f \"POST\") ; \"POST\"\n"), ("p/b.hy", "(g \"POST\")\n")]);
        let (hits, _) = find(dir.path(), &[declared(r#""POST""#, &["p/*.hy"], &[], WantedCount::Exactly(2))], "architecture.hy");
        assert!(hits.is_empty(), "{:?}", hits);
        let (hits, _) = find(dir.path(), &[declared(r#""POST""#, &["p/*.hy"], &[], WantedCount::Exactly(3))], "architecture.hy");
        assert_eq!(hits.len(), 1, "{:?}", hits);
        assert_eq!(hits[0].problem, CountProblem::Mismatch { found: 2, wanted: WantedCount::Exactly(3), within: None });
        assert_eq!(hits[0].rel, "p/a.hy");
    }

    #[test]
    fn at_least_accepts_more_and_other_files_are_read_as_they_are() {
        let dir = repo(&[("s/rules.yaml", "- id: one\n- id: one\n")]);
        let (hits, _) = find(dir.path(), &[declared(r"id: one", &["s/rules.yaml"], &[], WantedCount::AtLeast(1))], "architecture.hy");
        assert!(hits.is_empty(), "{:?}", hits);
        let (hits, _) = find(dir.path(), &[declared(r"id: two", &["s/rules.yaml"], &[], WantedCount::AtLeast(1))], "architecture.hy");
        assert_eq!(hits.len(), 1, "{:?}", hits);
        assert_eq!(hits[0].rel, "s/rules.yaml");
    }

    #[test]
    fn within_counts_inside_each_named_definition() {
        let text = "(defk send [x] (Req \"POST\" x))\n\n(defk read [x] (Req \"GET\" x))\n(setv loose (Req \"POST\" 1))\n";
        let dir = repo(&[("p/peer.hy", text)]);
        let (hits, _) =
            find(dir.path(), &[declared(r#""POST""#, &["p/peer.hy"], &["send", "read", "gone"], WantedCount::Exactly(1))], "architecture.hy");
        let details: Vec<&str> = hits.iter().map(|h| h.detail.as_str()).collect();
        assert_eq!(details, vec!["g:read", "g:gone:missing"], "{:?}", hits);
        assert_eq!(hits[0].range.start.line, 2);
        assert_eq!(hits[1].rel, "architecture.hy");
    }

    #[test]
    fn an_empty_population_is_missing_not_green() {
        let dir = repo(&[]);
        let (hits, _) = find(dir.path(), &[declared(r"x", &["gone/*.hy"], &[], WantedCount::Exactly(0))], "architecture.hy");
        assert_eq!(hits.len(), 1, "{:?}", hits);
        assert_eq!(hits[0].detail, "g:missing");
    }
}
