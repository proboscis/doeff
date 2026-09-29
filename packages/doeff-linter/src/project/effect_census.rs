//! DOEFF162: effect の宣言の全体(agora-redesign #1373・#1438 — 元は agora-controllers の一時の判定
//! controllers/screen/tests/intake_only_rules.hy の 2)。
//!
//! architecture.hy の `:effect-census [(effect-census "名" :files [..] :effects [..] :base "EffectBase" :why "…") …]` の宣言ごとに、
//! `:files` に当たる Hy・Python の file(註を落とす)から `:base` を継ぐ class の宣言を集め、`:effects` の一覧と比べる。
//!   * 一覧に無い effect の宣言は、宣言の位置で 1 件(鍵の細目 `<名>:<effect>`)。
//!   * 同じ名の宣言が 2 つ以上なら、2 つ目の位置で 1 件(`<名>:<effect>:twice`)。
//!   * 一覧に在って宣言が無い effect は architecture.hy の位置で 1 件(`<名>:<effect>:missing`)。
//!   * `:files` に当たる file が無ければ architecture.hy の位置で 1 件(`<名>:missing` — 母集団 0 を緑にしない)。
//! effect を足す変更は、宣言の一覧も同じ変更で直す(黙って増えない)。宣言の形は Python の `class X(… base …):` と Hy の
//! `(defclass [飾り] X [… base …])` / `(defclass X [… base …])`。

use std::collections::BTreeMap;
use std::path::Path;

use doeff_indexer::hy_index::{Position, Range};

use super::architecture::EffectCensus;
use super::spelling_scope::{code_text, line_of, selected_files};

/// 当たりの種類(閉じた 4 つ)。
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum CensusProblem {
    /// 一覧に無い effect を宣言している。
    Unlisted { effect: String },
    /// 同じ名の effect を 2 度宣言している。
    Twice { effect: String },
    /// 一覧に在る effect の宣言が無い。
    Undeclared { effect: String },
    /// `:files` に当たる file が無い。
    NoFiles,
}

/// 当たり 1 つ。
#[derive(Debug, Clone)]
pub struct CensusFinding {
    pub rel: String,
    /// 宣言の名。
    pub group: String,
    pub why: String,
    pub range: Range,
    pub problem: CensusProblem,
    /// 登録簿の鍵の細目。
    pub detail: String,
}

fn at_line(line: u32) -> Range {
    let at = Position { line, character: 0 };
    Range { start: at, end: at }
}

/// file の本文(註を落とした物)の中の、base を継ぐ class の (名, byte の位置)。
fn declared_in(rel: &str, code: &str, base: &str) -> Vec<(String, usize)> {
    let base = regex::escape(base);
    let pattern = if rel.ends_with(".hy") {
        format!(r"\(defclass\s+(?:\[[^\]]*\]\s+)?([\w\-]+)\s+\[[^\]]*\b{}\b[^\]]*\]", base)
    } else {
        format!(r"(?m)^[ \t]*class\s+(\w+)\s*\([^)]*\b{}\b[^)]*\)\s*:", base)
    };
    let Ok(regex) = regex::Regex::new(&pattern) else { return Vec::new() };
    regex.captures_iter(code).filter_map(|c| c.get(1).map(|m| (m.as_str().to_string(), m.start()))).collect()
}

/// 宣言 1 つを判じる。読めなかった file は errors へ積む。
fn judge_one(root: &Path, declared: &EffectCensus, architecture_rel: &str, errors: &mut Vec<String>) -> Vec<CensusFinding> {
    let finding = |rel: String, range: Range, problem: CensusProblem, detail: String| CensusFinding {
        rel,
        group: declared.name.clone(),
        why: declared.why.clone(),
        range,
        problem,
        detail,
    };
    let population = selected_files(root, &declared.files, &[]);
    if population.is_empty() {
        return vec![finding(architecture_rel.to_string(), declared.range, CensusProblem::NoFiles, format!("{}:missing", declared.name))];
    }
    // 名 → 宣言の位置(file・行)の列(path の順・本文の順)。
    let mut seen: BTreeMap<String, Vec<(String, u32)>> = BTreeMap::new();
    for rel in population {
        let source = match std::fs::read_to_string(root.join(&rel)) {
            Ok(source) => source,
            Err(error) => {
                errors.push(format!("{}: 読めない: {}", rel, error));
                continue;
            }
        };
        let code = code_text(&rel, &source);
        for (name, at) in declared_in(&rel, &code, &declared.base) {
            seen.entry(name).or_default().push((rel.clone(), line_of(&code, at)));
        }
    }
    let mut out = Vec::new();
    for (name, places) in &seen {
        let (rel, line) = &places[0];
        if !declared.effects.contains(name) {
            out.push(finding(
                rel.clone(),
                at_line(*line),
                CensusProblem::Unlisted { effect: name.clone() },
                format!("{}:{}", declared.name, name),
            ));
        }
        if let Some((rel, line)) = places.get(1) {
            out.push(finding(
                rel.clone(),
                at_line(*line),
                CensusProblem::Twice { effect: name.clone() },
                format!("{}:{}:twice", declared.name, name),
            ));
        }
    }
    for name in declared.effects.iter().filter(|name| !seen.contains_key(*name)) {
        out.push(finding(
            architecture_rel.to_string(),
            declared.range,
            CensusProblem::Undeclared { effect: name.clone() },
            format!("{}:{}:missing", declared.name, name),
        ));
    }
    out
}

/// 宣言の全部を判じる。architecture_rel は architecture.hy の根からの path(宣言の無い effect の位置)。
pub fn find(root: &Path, declarations: &[EffectCensus], architecture_rel: &str) -> (Vec<CensusFinding>, Vec<String>) {
    let mut errors = Vec::new();
    let found = declarations.iter().flat_map(|declared| judge_one(root, declared, architecture_rel, &mut errors)).collect();
    (found, errors)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn census(files: &[&str], effects: &[&str]) -> EffectCensus {
        EffectCensus {
            name: "screen".to_string(),
            files: files.iter().map(|p| p.to_string()).collect(),
            effects: effects.iter().map(|p| p.to_string()).collect(),
            base: "EffectBase".to_string(),
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
    fn python_and_hy_declarations_are_collected_and_comments_are_not() {
        let py = "# class Old(EffectBase):\nclass Send(EffectBase):\n    pass\n\nclass Log(Mixin, EffectBase):\n    pass\nclass Row(Base):\n    pass\n";
        let hy = "; (defclass [(dataclass)] Gone [EffectBase])\n(defclass [(dataclass :frozen True)] Close [EffectBase])\n(defclass Plain [EffectBase])\n";
        assert_eq!(declared_in("e.py", &code_text("e.py", py), "EffectBase").into_iter().map(|d| d.0).collect::<Vec<_>>(), vec!["Send", "Log"]);
        assert_eq!(declared_in("s.hy", &code_text("s.hy", hy), "EffectBase").into_iter().map(|d| d.0).collect::<Vec<_>>(), vec!["Close", "Plain"]);
    }

    #[test]
    fn unlisted_twice_and_undeclared_effects_are_each_found() {
        let dir = repo(&[
            ("s/effects.py", "class Send(EffectBase):\n    pass\nclass Sneaky(EffectBase):\n    pass\n"),
            ("s/intent/socket.hy", "(defclass [(dataclass)] Send [EffectBase])\n"),
        ]);
        let (hits, errors) = find(dir.path(), &[census(&["s/effects.py", "s/intent/socket.hy"], &["Send", "Close"])], "architecture.hy");
        assert!(errors.is_empty(), "{:?}", errors);
        let details: Vec<&str> = hits.iter().map(|h| h.detail.as_str()).collect();
        assert_eq!(details, vec!["screen:Send:twice", "screen:Sneaky", "screen:Close:missing"], "{:?}", hits);
        assert_eq!(hits[1].rel, "s/effects.py");
        assert_eq!(hits[1].range.start.line, 2);
    }

    #[test]
    fn an_empty_population_is_missing_not_green() {
        let dir = repo(&[]);
        let (hits, _) = find(dir.path(), &[census(&["gone/*.py"], &["Send"])], "architecture.hy");
        assert_eq!(hits.len(), 1, "{:?}", hits);
        assert_eq!(hits[0].problem, CensusProblem::NoFiles);
    }
}
