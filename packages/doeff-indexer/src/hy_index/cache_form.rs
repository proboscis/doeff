//! 索引の file 1 つを disk の cache に置く形(agora-redesign #1364)。
//!
//! doeff-linter は 1 file の実行でも repo 全体の Hy の索引を組む。その大半は file ごとの読みと名前の解決(`index_file` —
//! file の中身と path だけで決まる)なので、linter は file ごとにこの形で cache し、変わった file だけ読み直す。
//! file をまたぐ生の副作用の経由の辿り(`annotate_raw`)は他の file の中身と目録で答えが変わるので cache しない —
//! この形が持つのは辿りの前の索引。
//!
//! 索引の型の serde の形は契約の JSON の形で、linter の判定が読む欄のうち次の 2 種はその形では運べない。この形が別に持つ:
//! - 契約の JSON に出さない欄(`serde(skip)`)— `Reference` の `member`・`target`・`type_only`、`Call` の `form_range`・`keywords`・`arguments`。
//! - 値の有無で出したり出さなかったりする欄(`skip_serializing_if`)— `Definition` の `checks`。cache は欄の名前を持たない
//!   binary の形(linter の facts_cache の compact)で置くので、欄の数が値で変わると読み戻しがずれる。索引の側は常に `Some` に
//!   そろえて書き、読み戻す時に別に持った値へ戻す。
//!
//! 欄の足し忘れを compile で止める: `fields_are_accounted_for` が索引の型を全部、欄を名指して分解する(`..` を使わない)。
//! 型に欄を足すとそこが compile できなくなる — 足した欄が契約の JSON に出る欄(`serde` の skip も skip_serializing_if も
//! 無い)なら `_` で受け、そうでなければこの形で運んでから名で受ける(本線の ecccbd446 が `Call` に `keywords` を足した時、
//! 手で数えた一覧から漏れて、cache を使った実行だけが `:transport` を渡した呼びを「渡していない」と数えた)。

use serde::{Deserialize, Serialize};

use super::model::{
    Call, CallArgument, ContractClause, Definition, HyFileIndex, Import, NameRef, ParamType, RawEvidence, RawMark, RawStep, RawVia, Reference,
    TypeNote,
};
use super::position::{Position, Range};

/// 参照 1 つの、契約に出さない欄。
#[derive(Debug, Clone, Serialize, Deserialize)]
struct ReferenceExtra {
    member: bool,
    target: Option<String>,
    type_only: bool,
}

/// 呼び出し 1 つの、契約に出さない欄。
#[derive(Debug, Clone, Serialize, Deserialize)]
struct CallExtra {
    form_range: Range,
    keywords: Vec<String>,
    arguments: Vec<CallArgument>,
}

/// cache に置く file 1 つの索引(辿りの前)と、契約の JSON の形のままでは運べない欄(参照・呼び出し・定義と同じ順)。
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CachedHyFile {
    file: HyFileIndex,
    references: Vec<ReferenceExtra>,
    calls: Vec<CallExtra>,
    definition_checks: Vec<Option<Vec<String>>>,
}

/// cache の形の欄の数が索引と合わない(壊れた cache)— 読み直す file の path を持つ。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CachedHyFileMismatch {
    pub path: String,
}

impl CachedHyFile {
    /// 索引 1 つを cache の形にする(契約の形のままでは運べない欄を別に写し、`checks` は常に `Some` にそろえる)。
    pub fn of(mut file: HyFileIndex) -> Self {
        let references = file.references.iter().map(|r| ReferenceExtra { member: r.member, target: r.target.clone(), type_only: r.type_only }).collect();
        let calls = file.calls.iter().map(|c| CallExtra { form_range: c.form_range, keywords: c.keywords.clone(), arguments: c.arguments.clone() }).collect();
        let definition_checks = file.definitions.iter_mut().map(|d| d.checks.replace(Vec::new())).collect();
        CachedHyFile { file, references, calls, definition_checks }
    }

    /// cache の形から索引に戻す。欄の数が合わなければ(壊れた cache)読み直す file の path を返す。
    pub fn into_file(self) -> Result<HyFileIndex, CachedHyFileMismatch> {
        let CachedHyFile { mut file, references, calls, definition_checks } = self;
        if references.len() != file.references.len() || calls.len() != file.calls.len() || definition_checks.len() != file.definitions.len() {
            return Err(CachedHyFileMismatch { path: file.path });
        }
        for (definition, checks) in file.definitions.iter_mut().zip(definition_checks) {
            definition.checks = checks;
        }
        for (reference, extra) in file.references.iter_mut().zip(references) {
            reference.member = extra.member;
            reference.target = extra.target;
            reference.type_only = extra.type_only;
        }
        for (call, extra) in file.calls.iter_mut().zip(calls) {
            call.form_range = extra.form_range;
            call.keywords = extra.keywords;
            call.arguments = extra.arguments;
        }
        Ok(file)
    }
}

/// 索引の型の欄の見張り(頭の註)— 契約の JSON が運ぶ欄は `_`、この形が別に運ぶ欄は名で受ける。呼ばれない(compile だけが働く)。
#[allow(dead_code)]
fn fields_are_accounted_for(file: &HyFileIndex) {
    let HyFileIndex { path: _, module: _, definitions, imports, references, calls, errors: _ } = file;
    for definition in definitions {
        let Definition {
            name: _,
            mangled: _,
            qualified_name: _,
            kind: _,
            range,
            full_range,
            container: _,
            docstring: _,
            params: _,
            bases: _,
            raw,
            tags: _,
            checks: _carried_as_definition_checks,
            effects,
            param_types,
            answer_type,
            contracts,
            handles,
            decorators: _,
        } = definition;
        ranges(&[*range, *full_range]);
        raw_mark(raw);
        effects.iter().flatten().for_each(name_ref);
        for ParamType { name: _, type_note } in param_types {
            type_note_fields(type_note);
        }
        answer_type.iter().for_each(type_note_fields);
        for ContractClause { side: _, text: _ } in contracts {}
        handles.iter().for_each(name_ref);
    }
    for Import { module: _, name: _, alias: _, range, is_require: _ } in imports {
        ranges(&[*range]);
    }
    for Reference { name: _, mangled: _, qualifier: _, range, member: _carried, target: _carried_too, type_only: _carried_type } in references {
        ranges(&[*range]);
    }
    for Call { callee: _, mangled: _, qualifier: _, range, form_range: _carried, keywords: _carried_too, arguments: _carried_arguments, caller: _, performed: _, target: _ } in calls {
        ranges(&[*range]);
    }
}

#[allow(dead_code)]
fn ranges(ranges: &[Range]) {
    for Range { start, end } in ranges {
        for Position { line: _, character: _ } in [start, end] {}
    }
}

#[allow(dead_code)]
fn raw_mark(mark: &RawMark) {
    let RawMark { direct, via } = mark;
    direct.iter().for_each(raw_evidence);
    for RawVia { through, evidence } in via {
        for RawStep { path: _, index: _, name: _ } in through {}
        raw_evidence(evidence);
    }
}

#[allow(dead_code)]
fn raw_evidence(evidence: &RawEvidence) {
    let RawEvidence { category: _, name: _, kind: _, strength: _, path: _, range } = evidence;
    ranges(&[*range]);
}

#[allow(dead_code)]
fn name_ref(name: &NameRef) {
    let NameRef { name: _, target: _ } = name;
}

#[allow(dead_code)]
fn type_note_fields(note: &TypeNote) {
    let TypeNote { text: _, names } = note;
    names.iter().for_each(name_ref);
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::hy_index::index_source;
    use std::path::Path;

    const SOURCE: &str = r#"(require doeff-hy.macros [defk <-])
(import doeff_core_effects.os_file [os-file-handler])
(import doeff_records.http_client [EffectTransport RecordsEndpoint])
(import pathlib [Path])

(defk read-it [path]
  {:pre [(: path str)] :post [(: % str)]}
  "読むため"
  (<- text (.read-text (Path path)))
  (with-handlers [os-file-handler] (helper text))
  (RecordsEndpoint "http://r" "t" :poll-seconds 0.2 :transport (EffectTransport))
  text)

(defrecord Span {:check [(>= start 0) (<= start end)]} #^ int start #^ int end)
"#;

    #[test]
    fn a_cached_file_reads_back_the_same() {
        let file = index_source(Path::new("/r"), Path::new("/r/pkg/m.hy"), SOURCE);
        // 別に運ぶ欄が実際に値を持つ file で確かめる(持たなければ検が何も見張らない)。
        assert!(file.references.iter().any(|r| r.member), "method の区切り(member)が無い");
        assert!(file.references.iter().any(|r| r.target.is_some()), "名指しの先(target)が無い");
        assert!(file.calls.iter().any(|c| c.form_range != c.range), "呼び出しの範囲(form_range)が無い");
        assert!(file.calls.iter().any(|c| !c.keywords.is_empty()), "keyword を渡す呼び出し(keywords)が無い");
        assert!(file.definitions.iter().any(|d| d.checks.is_some()), ":check を持つ定義が無い");
        assert!(file.definitions.iter().any(|d| d.checks.is_none()), ":check を持たない定義が無い");
        // linter の cache と同じ欄の名前を持たない binary の形(bincode)で往復する。
        let bytes = bincode::serialize(&CachedHyFile::of(file.clone())).unwrap();
        let back = bincode::deserialize::<CachedHyFile>(&bytes).unwrap().into_file().unwrap();
        assert_eq!(format!("{:?}", back), format!("{:?}", file));
    }

    #[test]
    fn a_cache_whose_extras_do_not_match_is_refused() {
        let file = index_source(Path::new("/r"), Path::new("/r/pkg/m.hy"), SOURCE);
        let mut cached = CachedHyFile::of(file);
        cached.references.pop();
        assert_eq!(cached.into_file().unwrap_err(), CachedHyFileMismatch { path: "/r/pkg/m.hy".to_string() });
    }
}
