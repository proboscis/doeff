//! DOEFF207 — 本番の系(architecture.hy の defservice の `:system` が名指す defsystem)の job が読む外の置き場の組に、書き手が在るか
//! (agora-redesign #3493・#3496 — 新しい入口の画面に会話の本文が出なかった件の作りの直し。本番に書き手の無い記録を画面が読み、
//! 手元の模擬では代役がその分を作っていたので緑だった)。
//!
//! 当たるのは、architecture.hy に `:outside-writers {"<置き場>:<名>" "書き手" …}`(本番の系の job でない書き手 — 外の repo の道具・
//! 人の操作・job でない task — が書く組の表)を書いた repo だけ。母集団は DOEFF173 と同じ本番の系(`:system` の名指しと
//! `{:part-of …}` の指す系 — 同じ系は 1 度)。次のどれかを欠けとして返す:
//! - 本番の系の job に `:reads` か `:writes` が無い(doeff の defsystem では任意の欄 — 本番の系の job にだけ、この規則が求める)。
//! - job の `:reads` の組が、どの本番の job の `:writes` にも、`:outside-writers` の表にも無い(読む物に書き手が居ない)。
//!
//! 欄の値の形(文字列の集合・綴り `<置き場>:<名>`)は doeff-hy の defsystem の展開が検める — ここは綴りを集めて照らすだけ。
//! 名指した系が無い・defsystem でない時は DOEFF173 が出すので、ここは飛ばす。

use std::collections::BTreeSet;
use std::collections::HashMap;
use std::path::Path;

use doeff_indexer::hy_index::reader::{Delim, Form, Node, Reader};
use doeff_indexer::hy_index::{DefinitionKind, HyFileIndex};

use super::architecture::{Architecture, DefinitionRef, SystemDecl};
use super::system_decls::{find, items_of, symbol};

/// 欠けの種類(閉じた集合)。
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum AccessGap {
    /// 本番の系の job に欄が無い(欄の鍵の綴り `:reads` / `:writes`)。
    MissingField { key: &'static str },
    /// 読む組に、本番の job の書き手も外の書き手の表の行も無い(組の綴り)。
    ReadWithoutWriter { access: String },
}

/// 本番の系の job 1 つの欠け 1 つ(defsystem の file の相対 path・job の行の行番号(0 始まり)・系の名・job の名・欠け)。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AccessFinding {
    pub rel: String,
    pub line: u32,
    pub system: String,
    pub job: String,
    pub gap: AccessGap,
}

impl AccessFinding {
    /// 鍵の細目(系::job::欄の鍵 か 系::job::組)。
    pub fn detail(&self) -> String {
        match &self.gap {
            AccessGap::MissingField { key } => format!("{}::{}::{}", self.system, self.job, key.trim_start_matches(':')),
            AccessGap::ReadWithoutWriter { access } => format!("{}::{}::{}", self.system, self.job, access),
        }
    }

    /// 違反の文の中身(何が欠けているか)。
    pub fn describe(&self) -> String {
        match &self.gap {
            AccessGap::MissingField { key } => format!(
                "本番の系 {} の job {} に {} が無い — 本番の系の job は、読む外の置き場の組と書く組を文字列の集合で書く(使わなければ #{{}})",
                self.system, self.job, key
            ),
            AccessGap::ReadWithoutWriter { access } => format!(
                "本番の系 {} の job {} が読む {} に書き手が無い — どの本番の job の :writes にも、architecture.hy の :outside-writers にも無い",
                self.system, self.job, access
            ),
        }
    }
}

/// job の行 1 つの読み書きの欄(欄が無ければ None)。
#[derive(Debug, Clone, PartialEq, Eq)]
struct JobAccess {
    name: String,
    line: u32,
    reads: Option<BTreeSet<String>>,
    writes: Option<BTreeSet<String>>,
}

/// 文字列の literal の集合 `#{"a" "b"}` の中身(集合でない値は空 — 形の検めは doeff-hy の展開が持つ)。
fn strings_of(source: &str, form: &Form) -> BTreeSet<String> {
    items_of(form, Delim::Set)
        .map(|items| {
            items
                .iter()
                .filter_map(|item| match &item.node {
                    Node::Str { body, .. } => source.get(body.start..body.end).map(str::to_string),
                    _ => None,
                })
                .collect()
        })
        .unwrap_or_default()
}

/// defsystem `system` の job の行ごとの欄を読む(job の行 = 名の記号・Program の呼び・`:鍵 値` の組)。
fn job_rows(source: &str, system: &str) -> Vec<JobAccess> {
    let mut reader = Reader::new(source, 0, source.len());
    let forms = reader.read_all();
    let Some(body) = forms.iter().find_map(|form| {
        let items = items_of(form, Delim::Paren)?;
        (items.len() >= 3 && symbol(source, &items[0]) == Some("defsystem") && symbol(source, &items[1]) == Some(system)).then_some(items)
    }) else {
        return Vec::new();
    };
    body.iter()
        .skip(3)
        .filter_map(|row| items_of(row, Delim::Paren).map(|items| (row, items)))
        .filter_map(|(row, items)| {
            let name = symbol(source, items.first()?)?.to_string();
            let line = source[..row.span.start].matches('\n').count() as u32;
            let mut access = JobAccess { name, line, reads: None, writes: None };
            let options = items.get(2..).unwrap_or(&[]);
            for pair in options.chunks(2) {
                let [key, value] = pair else { continue };
                match source.get(key.span.start..key.span.end) {
                    Some(":reads") => access.reads = Some(strings_of(source, value)),
                    Some(":writes") => access.writes = Some(strings_of(source, value)),
                    _ => {}
                }
            }
            Some(access)
        })
        .collect()
}

/// 本番の系の名指し(architecture.hy の宣言の順・同じ綴りは 1 度)。例外の宣言の service は系を持たない。
fn production_systems(architecture: &Architecture) -> Vec<DefinitionRef> {
    let mut seen = BTreeSet::new();
    architecture
        .services
        .iter()
        .flat_map(|service| match service.system.as_ref() {
            Some(SystemDecl::Systems(refs)) => refs.clone(),
            Some(SystemDecl::PartOf(target)) => vec![target.clone()],
            Some(SystemDecl::Exempt(_)) | None => Vec::new(),
        })
        .filter(|system| seen.insert(system.spelling()))
        .collect()
}

/// 本番の系の job の欠け(系の順・job の行の順)。`:outside-writers` を書いていない repo には当てない(空)。
pub fn gaps(root_path: &Path, architecture: &Architecture, hy: &HashMap<String, HyFileIndex>) -> Vec<AccessFinding> {
    let Some(outside) = architecture.outside_writers.as_ref() else { return Vec::new() };
    let outside: BTreeSet<&str> = outside.iter().map(|writer| writer.access.as_str()).collect();
    let jobs: Vec<(String, String, JobAccess)> = production_systems(architecture)
        .iter()
        .filter_map(|system| {
            let (rel, index, at) = find(system, hy)?;
            let definition = &index.definitions[at];
            (definition.kind == DefinitionKind::Defsystem).then_some(())?;
            let source = std::fs::read_to_string(root_path.join(rel)).ok()?;
            Some(job_rows(&source, &definition.name).into_iter().map(|job| (rel.to_string(), definition.name.clone(), job)).collect::<Vec<_>>())
        })
        .flatten()
        .collect();
    let written: BTreeSet<&str> = jobs.iter().flat_map(|(_, _, job)| job.writes.iter().flatten().map(String::as_str)).collect();
    jobs.iter()
        .flat_map(|(rel, system, job)| {
            let finding = |gap: AccessGap| AccessFinding { rel: rel.clone(), line: job.line, system: system.clone(), job: job.name.clone(), gap };
            let missing = [(":reads", job.reads.is_none()), (":writes", job.writes.is_none())]
                .into_iter()
                .filter(|(_, absent)| *absent)
                .map(|(key, _)| finding(AccessGap::MissingField { key }));
            let unwritten = job
                .reads
                .iter()
                .flatten()
                .filter(|access| !written.contains(access.as_str()) && !outside.contains(access.as_str()))
                .map(|access| finding(AccessGap::ReadWithoutWriter { access: access.clone() }));
            missing.chain(unwritten).collect::<Vec<_>>()
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn job_rows_read_the_access_fields_and_leave_absent_ones_none() {
        let source = "(defsystem s [#^ T foundation]\n  \"doc\"\n  (a (job-a foundation) :needs #{\"x\"} :reads #{\"records:intake\" \"record:turn\"} :writes #{})\n  \
                      (b (job-b foundation) :needs #{\"x\"} :replicas 1))\n";
        let rows = job_rows(source, "s");
        assert_eq!(rows.len(), 2);
        assert_eq!(rows[0].name, "a");
        assert_eq!(rows[0].line, 2);
        assert_eq!(rows[0].reads, Some(BTreeSet::from(["record:turn".to_string(), "records:intake".to_string()])));
        assert_eq!(rows[0].writes, Some(BTreeSet::new()));
        assert_eq!((rows[1].reads.clone(), rows[1].writes.clone()), (None, None));
        assert!(job_rows(source, "other").is_empty());
    }
}
