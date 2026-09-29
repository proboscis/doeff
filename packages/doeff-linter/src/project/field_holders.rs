//! DOEFF149: 型の欄を持つ class の顔ぶれ(agora-redesign #1374・親 #1192 — 元は agora-controllers の一時の判定
//! controllers/screen/tests/record_body_rules.hy の ③)。
//!
//! architecture.hy の `:field-holders [(field-holders "名" :type "T" :files [..] :classes [..] :holders [..] :why "…") …]` の宣言ごとに、
//! `:files` に当たる Python の file(.py・.pyi)の module の直下の class を読み、class の本体の直下の欄(`名: 注記` の注記つきの代入)の
//! 注記に `:type` の綴りが語として在る class を「持ち手」と数え、`:holders` の一覧と比べる。
//!   * 一覧に無い持ち手は、class の位置で 1 件(鍵の細目 `<名>:<class>`)。
//!   * 一覧に在って持ち手でない class(その型の欄が無い・class が無い)は architecture.hy の位置で 1 件(`<名>:<class>:absent`)。
//!   * `:classes` を書けば、名指した class だけを数える。名指した class が無ければ architecture.hy の位置で 1 件
//!     (`<名>:<class>:missing`)。
//!   * `:files` に当たる Python の file が無ければ architecture.hy の位置で 1 件(`<名>:missing` — 母集団 0 を緑にしない)。
//! 構文木にならない file は読めない物として errors に積む(黙って緑にしない)。

use std::collections::BTreeMap;
use std::path::Path;

use doeff_indexer::hy_index::{Position, Range};
use rustpython_ast::{Expr, Mod, Ranged, Stmt};
use rustpython_parser::{parse, Mode};

use super::architecture::FieldHolders;
use super::spelling_scope::{line_of, selected_files};

/// 当たりの種類(閉じた 4 つ)。
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum HolderProblem {
    /// 一覧に無い class がその型の欄を持つ。
    Unlisted { class: String },
    /// 一覧の class がその型の欄を持たない(class が無い時も)。
    Absent { class: String },
    /// :classes で名指した class が無い。
    NoClass { class: String },
    /// `:files` に当たる Python の file が無い。
    NoFiles,
}

/// 当たり 1 つ。
#[derive(Debug, Clone)]
pub struct HolderFinding {
    pub rel: String,
    /// 宣言の名。
    pub group: String,
    /// 数えた型の綴り。
    pub type_name: String,
    pub why: String,
    pub range: Range,
    pub problem: HolderProblem,
    /// 登録簿の鍵の細目。
    pub detail: String,
}

fn at_line(line: u32) -> Range {
    let at = Position { line, character: 0 };
    Range { start: at, end: at }
}

/// Python の本文の module の直下の class ごとの (名, 行, 本体の直下の注記つきの欄の注記の綴りの列)。
fn classes_of(rel: &str, source: &str) -> Result<Vec<(String, u32, Vec<String>)>, String> {
    let module = parse(source, Mode::Module, rel).map_err(|error| format!("構文木にならない: {}", error))?;
    let Mod::Module(module) = module else { return Ok(Vec::new()) };
    Ok(module
        .body
        .iter()
        .filter_map(|statement| match statement {
            Stmt::ClassDef(class) => {
                let annotations = class
                    .body
                    .iter()
                    .filter_map(|field| match field {
                        Stmt::AnnAssign(field) if matches!(field.target.as_ref(), Expr::Name(_)) => {
                            let range = field.annotation.range();
                            source.get(range.start().to_usize()..range.end().to_usize()).map(str::to_string)
                        }
                        _ => None,
                    })
                    .collect();
                Some((class.name.to_string(), line_of(source, class.range().start().to_usize()), annotations))
            }
            _ => None,
        })
        .collect())
}

/// 宣言 1 つを判じる。読めなかった file は errors へ積む。
fn judge_one(root: &Path, declared: &FieldHolders, architecture_rel: &str, errors: &mut Vec<String>) -> Vec<HolderFinding> {
    let finding = |rel: String, range: Range, problem: HolderProblem, detail: String| HolderFinding {
        rel,
        group: declared.name.clone(),
        type_name: declared.type_name.clone(),
        why: declared.why.clone(),
        range,
        problem,
        detail,
    };
    let population: Vec<String> =
        selected_files(root, &declared.files, &[]).into_iter().filter(|rel| rel.ends_with(".py") || rel.ends_with(".pyi")).collect();
    if population.is_empty() {
        return vec![finding(architecture_rel.to_string(), declared.range, HolderProblem::NoFiles, format!("{}:missing", declared.name))];
    }
    let Ok(word) = regex::Regex::new(&format!(r"\b{}\b", regex::escape(&declared.type_name))) else { return Vec::new() };
    // class の名 → (file・行・その型の欄を持つか)。同じ名の class が 2 つ在れば、持つ方を採る(持ち手を見落とさない)。
    let mut seen: BTreeMap<String, (String, u32, bool)> = BTreeMap::new();
    for rel in population {
        let source = match std::fs::read_to_string(root.join(&rel)) {
            Ok(source) => source,
            Err(error) => {
                errors.push(format!("{}: 読めない: {}", rel, error));
                continue;
            }
        };
        let classes = match classes_of(&rel, &source) {
            Ok(classes) => classes,
            Err(reason) => {
                errors.push(format!("{}: {}", rel, reason));
                continue;
            }
        };
        for (name, line, annotations) in classes {
            let holds = annotations.iter().any(|annotation| word.is_match(annotation));
            seen.entry(name)
                .and_modify(|entry| {
                    if holds && !entry.2 {
                        *entry = (rel.clone(), line, true);
                    }
                })
                .or_insert((rel.clone(), line, holds));
        }
    }
    let counted = |name: &str| declared.classes.is_empty() || declared.classes.iter().any(|c| c == name);
    let mut out = Vec::new();
    for name in declared.classes.iter().filter(|name| !seen.contains_key(*name)) {
        out.push(finding(
            architecture_rel.to_string(),
            declared.range,
            HolderProblem::NoClass { class: name.clone() },
            format!("{}:{}:missing", declared.name, name),
        ));
    }
    for (name, (rel, line, holds)) in &seen {
        if *holds && counted(name) && !declared.holders.contains(name) {
            out.push(finding(rel.clone(), at_line(*line), HolderProblem::Unlisted { class: name.clone() }, format!("{}:{}", declared.name, name)));
        }
    }
    for name in &declared.holders {
        let holds = seen.get(name).is_some_and(|entry| entry.2);
        // :classes で名指して class が無いなら、上の missing が既に出ている(同じ事を 2 件にしない)。
        let reported = !declared.classes.is_empty() && !seen.contains_key(name);
        if !holds && !reported {
            out.push(finding(
                architecture_rel.to_string(),
                declared.range,
                HolderProblem::Absent { class: name.clone() },
                format!("{}:{}:absent", declared.name, name),
            ));
        }
    }
    out
}

/// 宣言の全部を判じる。architecture_rel は architecture.hy の根からの path(一覧の class の欠けの位置)。
pub fn find(root: &Path, declarations: &[FieldHolders], architecture_rel: &str) -> (Vec<HolderFinding>, Vec<String>) {
    let mut errors = Vec::new();
    let found = declarations.iter().flat_map(|declared| judge_one(root, declared, architecture_rel, &mut errors)).collect();
    (found, errors)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn holders(type_name: &str, files: &[&str], classes: &[&str], holders: &[&str]) -> FieldHolders {
        FieldHolders {
            name: "cache".to_string(),
            type_name: type_name.to_string(),
            files: files.iter().map(|p| p.to_string()).collect(),
            classes: classes.iter().map(|p| p.to_string()).collect(),
            holders: holders.iter().map(|p| p.to_string()).collect(),
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

    const TYPES: &str = "from dataclasses import dataclass\n\n\
        @dataclass(frozen=True)\nclass RecordCache:\n    \"\"\"Note: RecordCache は本文の容器。\"\"\"\n    rows: tuple\n\n\
        @dataclass(frozen=True)\nclass ConversationSent:\n    cid: str\n    records: RecordCache | None = None\n\n\
        class Helper:\n    def f(self) -> None:\n        cache: RecordCache = RecordCache(())\n\n\
        @dataclass(frozen=True)\nclass ServerState:\n    sent: dict\n    record_note: str = \"RecordCache\"\n";

    #[test]
    fn a_field_annotation_holds_the_type_and_docstrings_methods_and_strings_do_not() {
        let found = classes_of("t.py", TYPES).unwrap();
        let word = regex::Regex::new(r"\bRecordCache\b").unwrap();
        let held: Vec<&str> =
            found.iter().filter(|(_, _, annotations)| annotations.iter().any(|a| word.is_match(a))).map(|(name, _, _)| name.as_str()).collect();
        assert_eq!(held, vec!["ConversationSent"]);
    }

    #[test]
    fn the_word_must_stand_alone() {
        let dir = repo(&[("m/types.py", "class Page:\n    body: RecordBody\n\nclass State:\n    note: Record\n")]);
        let (hits, errors) = find(dir.path(), &[holders("Record", &["m/types.py"], &[], &["State"])], "architecture.hy");
        assert!(errors.is_empty(), "{:?}", errors);
        assert!(hits.is_empty(), "{:?}", hits);
    }

    #[test]
    fn unlisted_and_absent_holders_are_each_found() {
        let dir = repo(&[
            ("m/types.py", TYPES),
            ("m/more.py", "class Sneaky:\n    cache: list[RecordCache]\n"),
        ]);
        let (hits, errors) =
            find(dir.path(), &[holders("RecordCache", &["m/types.py", "m/more.py"], &[], &["ConversationSent", "RecordPage"])], "architecture.hy");
        assert!(errors.is_empty(), "{:?}", errors);
        let details: Vec<&str> = hits.iter().map(|h| h.detail.as_str()).collect();
        assert_eq!(details, vec!["cache:Sneaky", "cache:RecordPage:absent"], "{:?}", hits);
        assert_eq!(hits[0].rel, "m/more.py");
        assert_eq!(hits[0].range.start.line, 0);
        assert_eq!(hits[1].rel, "architecture.hy");
    }

    #[test]
    fn named_classes_narrow_the_census_and_a_missing_one_is_found() {
        let dir = repo(&[("m/types.py", "class ServerState:\n    cache: Record\n\nclass Other:\n    cache: Record\n")]);
        let (hits, _) = find(dir.path(), &[holders("Record", &["m/types.py"], &["ServerState", "Gone"], &[])], "architecture.hy");
        let details: Vec<&str> = hits.iter().map(|h| h.detail.as_str()).collect();
        assert_eq!(details, vec!["cache:Gone:missing", "cache:ServerState"], "{:?}", hits);
    }

    #[test]
    fn an_empty_population_is_missing_and_an_unparsable_file_is_an_error() {
        let empty = repo(&[("m/types.hy", "(defclass X [])\n")]);
        let (hits, _) = find(empty.path(), &[holders("X", &["m/*"], &[], &[])], "architecture.hy");
        assert_eq!(hits.len(), 1, "{:?}", hits);
        assert_eq!(hits[0].problem, HolderProblem::NoFiles);
        let broken = repo(&[("m/types.py", "class Broken(:\n")]);
        let (_, errors) = find(broken.path(), &[holders("X", &["m/types.py"], &[], &[])], "architecture.hy");
        assert!(errors.iter().any(|e| e.contains("構文木にならない")), "{:?}", errors);
    }
}
