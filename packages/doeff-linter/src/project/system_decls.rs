//! DOEFF173 — architecture.hy の defservice ごとに、cluster に置く系(defsystem)を宣言し、その系が汎用の模擬 cluster のテストに
//! 載る形か(agora-redesign #2187・DOEFF136 と対)。
//!
//! 母集団は architecture.hy の全 defservice(entry の層を持たない service も数える — operator の決め「全 service に defsystem か
//! 理由つきの例外」を、entry の層の無い service が黙って抜けないように・agora-redesign #2221)。defservice の
//! `:system "module:名"`(列も可)を読み、次のどれかを欠けとして返す:
//! - `:system` を書いていない・空の列。
//! - `:system {:exempt "理由"}` の理由が空(例外は旧い経路に残す service か、process を持たない部品の service — 理由の中身は判じない)。
//! - 名指した module がその service の entry の層(`<root>/<dir>/entry/`)の外(`:entry-modules` の service はその module の中)。
//!   `:system {:part-of "module:名"}`(その service の code が別の service の系の中で走る)だけは、どこかの service の entry の層に
//!   在ればよい(引数の検めはその指した系に当てる)。part-of でない宣言が他の service の系を指すのは赤。
//! - 名指した名の定義が無い・defsystem でない(静的な記述 `__doeff_system__` を付けるのは defsystem だけ — 索引の種類で判じる)。
//! - defsystem の引数が 1 つでない。
//! - 引数 1 つ(土台)を受ける job の関数の、その引数の型が書かれていない・素の Callable・写像・中身の型の無い組(汎用のテストが
//!   型から模擬の土台を組めない)。
//!
//! 層の名 entry は他の規則(DOEFF136・163)と同じく層の dir の名で読む。

use std::collections::HashMap;
use std::path::Path;

use doeff_indexer::hy_index::reader::{Delim, Form, Node, Reader};
use doeff_indexer::hy_index::{Definition, DefinitionKind, HyFileIndex};

use super::architecture::{ArchService, Architecture, DefinitionRef, SystemDecl};
use super::invariants::module_path;
use super::layers::normalize_dir;

/// 土台の型として受けない型の頭(汎用のテストが型から土台を組めない物)。
const UNTYPED_FOUNDATION_HEADS: &[&str] =
    &["Callable", "dict", "Dict", "Mapping", "FrozenMap", "tuple", "list", "Any", "object", "JsonBody", "JsonValue", "OpaqueJson"];

/// service 1 つの欠け 1 つ(閉じた集合)。
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum SystemGap {
    /// `:system` を書いていない・空の列。
    Undeclared,
    /// `{:exempt "理由"}` の理由が空。
    EmptyExemption,
    /// 名指した module が entry の層の外。
    OutsideEntry(DefinitionRef),
    /// 名指した定義が無い。
    Missing(DefinitionRef),
    /// 名指した定義が defsystem でない。
    NotSystem(DefinitionRef),
    /// 引数が 1 つでない(引数の名の列)。
    NotOneFoundation { system: DefinitionRef, params: Vec<String> },
    /// 土台を受ける job の関数の、その引数の型が無い・土台の型でない(job の関数の名・型の綴り — 無ければ None)。
    UntypedFoundation { system: DefinitionRef, job: String, type_text: Option<String> },
    /// 土台を受ける job の関数が 1 つも見つからない(型を確かめられない)。
    FoundationUnused { system: DefinitionRef },
}

impl SystemGap {
    /// 鍵の service の名の後ろの細目(宣言の欠けは無し・系の欠けは名指しの綴り)。
    pub fn detail(&self) -> Option<String> {
        match self {
            SystemGap::Undeclared => None,
            SystemGap::EmptyExemption => Some("exempt".to_string()),
            SystemGap::OutsideEntry(system) | SystemGap::Missing(system) | SystemGap::NotSystem(system) => Some(system.spelling()),
            SystemGap::NotOneFoundation { system, .. } => Some(format!("{}::params", system.spelling())),
            SystemGap::FoundationUnused { system } => Some(format!("{}::unused", system.spelling())),
            SystemGap::UntypedFoundation { system, job, .. } => Some(format!("{}::{}", system.spelling(), job)),
        }
    }

    /// 違反の文の中身(何が欠けているか)。
    pub fn describe(&self, service: &str) -> String {
        match self {
            SystemGap::Undeclared => format!("service {} は :system(cluster に置く系の defsystem)を宣言していない", service),
            SystemGap::EmptyExemption => format!("service {} の :system {{:exempt …}} の理由が空", service),
            SystemGap::OutsideEntry(system) => {
                format!("service {} の :system の {} — module がその service の entry の層に無い", service, system.spelling())
            }
            SystemGap::Missing(system) => {
                format!("service {} の :system の {} — 定義が無い(module か名の誤り)", service, system.spelling())
            }
            SystemGap::NotSystem(system) => format!("service {} の :system の {} — defsystem でない", service, system.spelling()),
            SystemGap::NotOneFoundation { system, params } => format!(
                "service {} の :system の {} — 引数が土台 1 つでない([{}])",
                service,
                system.spelling(),
                params.join(" ")
            ),
            SystemGap::UntypedFoundation { system, job, type_text } => format!(
                "service {} の :system の {} — 土台を受ける job の関数 {} の引数の型が{}",
                service,
                system.spelling(),
                job,
                match type_text {
                    Some(text) => format!(" {}(素の Callable・写像・組は土台の型にならない)", text),
                    None => "書かれていない".to_string(),
                }
            ),
            SystemGap::FoundationUnused { system } => {
                format!("service {} の :system の {} — 土台を受ける job の関数が見つからない(型を確かめられない)", service, system.spelling())
            }
        }
    }
}

/// 名指した定義(file の相対 path・定義)。
pub(super) fn find<'h>(definition: &DefinitionRef, hy: &'h HashMap<String, HyFileIndex>) -> Option<(&'h str, &'h HyFileIndex, usize)> {
    let target = definition.target();
    hy.iter().find_map(|(rel, index)| {
        index.definitions.iter().position(|d| d.qualified_name == target).map(|at| (rel.as_str(), index, at))
    })
}

/// その service の entry の層の module か。
fn in_entry(root: &str, service: &ArchService, rel: &str) -> bool {
    match service.entry_modules.as_deref() {
        Some(modules) => modules.iter().any(|m| module_path(m) == rel),
        None => rel.starts_with(&format!("{}/{}/entry/", root, service.dir)),
    }
}

pub(super) fn symbol<'s>(source: &'s str, form: &Form) -> Option<&'s str> {
    match form.node {
        Node::Symbol => source.get(form.span.start..form.span.end),
        _ => None,
    }
}

pub(super) fn items_of(form: &Form, want: Delim) -> Option<&[Form]> {
    match &form.node {
        Node::Seq { delim, items } if *delim == want => Some(items),
        _ => None,
    }
}

/// 型の注記を剥がした form(`#^ T 名` の名)。
fn bare(form: &Form) -> &Form {
    match &form.node {
        Node::Annotated { target: Some(target), .. } => bare(target),
        _ => form,
    }
}

/// 型の注記の綴り(`#^ T 名` の T)。注記が無ければ None。
fn annotation_text(source: &str, form: &Form) -> Option<String> {
    match &form.node {
        Node::Annotated { annotation: Some(annotation), .. } => {
            source.get(annotation.span.start..annotation.span.end).map(str::to_string)
        }
        _ => None,
    }
}

/// defsystem 1 つの形(索引は defsystem の引数を持たないので source から読む): 引数の名(`#^ T 名` は名)と、
/// 引数 1 つ目(土台)の型の注記(doeff L983 の `[#^ T foundation]` — 無ければ None)と、
/// その記号を受ける job の呼び出しの (関数の綴り, 位置)。
#[derive(Debug, Clone, PartialEq, Eq)]
struct SystemShape {
    params: Vec<String>,
    foundation_type: Option<String>,
    foundation_calls: Vec<(String, usize)>,
}

fn read_system(source: &str, system: &str) -> Option<SystemShape> {
    let mut reader = Reader::new(source, 0, source.len());
    let forms = reader.read_all();
    let body: &[Form] = forms.iter().find_map(|form| {
        let items = items_of(form, Delim::Paren)?;
        (items.len() >= 3 && symbol(source, &items[0]) == Some("defsystem") && symbol(source, &items[1]) == Some(system)).then_some(items)
    })?;
    let declared = items_of(&body[2], Delim::Bracket)?;
    let params: Vec<String> = declared.iter().filter_map(|p| symbol(source, bare(p))).map(str::to_string).collect();
    let foundation_type = declared.first().and_then(|p| annotation_text(source, p));
    let foundation_calls = match params.first() {
        Some(foundation) => foundation_calls(source, body, foundation),
        None => Vec::new(),
    };
    Some(SystemShape { params, foundation_type, foundation_calls })
}

/// defsystem の job の行から、土台の記号を受ける呼び出しの (関数の綴り, 位置) を集める。
fn foundation_calls(source: &str, body: &[Form], foundation: &str) -> Vec<(String, usize)> {
    body.iter()
        .skip(3)
        .filter_map(|row| items_of(row, Delim::Paren))
        .filter_map(|row| row.get(1).and_then(|program| items_of(program, Delim::Paren)))
        .filter_map(|program| {
            let head = symbol(source, program.first()?)?.to_string();
            // 位置の引数だけを数える(`:鍵 値` の組は飛ばす)。
            let mut positional = Vec::new();
            let mut rest = program[1..].iter();
            while let Some(arg) = rest.next() {
                if matches!(arg.node, Node::Keyword) {
                    rest.next();
                } else {
                    positional.push(arg);
                }
            }
            positional.iter().position(|arg| symbol(source, arg) == Some(foundation)).map(|at| (head, at))
        })
        .collect()
}

/// 呼び出しの頭の綴りを、defsystem の中の呼び出しの名前の解決(索引の target)で Hy の定義へ引く。
fn callee<'h>(
    index: &HyFileIndex,
    caller: usize,
    head: &str,
    hy: &'h HashMap<String, HyFileIndex>,
) -> Option<&'h Definition> {
    let target = index.calls.iter().find(|c| c.caller == Some(caller) && c.callee == head && c.target.is_some())?.target.clone()?;
    hy.values().flat_map(|i| i.definitions.iter()).find(|d| d.qualified_name == target)
}

/// 型の綴りの頭(`(of Callable …)` → Callable・`(| A None)` → A)。
fn type_head(text: &str) -> &str {
    text.split(|c: char| c.is_whitespace() || c == '(' || c == ')' || c == '[' || c == ']')
        .find(|word| !word.is_empty() && !matches!(*word, "of" | "get" | "|"))
        .unwrap_or("")
}

/// 系の置き場の決め: 自分の系はその service 自身の entry の層、`{:part-of …}` はどこかの service の entry の層。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Placement {
    OwnEntry,
    AnyEntry,
}

/// 系 1 つの形の欠け。
fn system_gaps(
    root_path: &Path,
    architecture: &Architecture,
    service: &ArchService,
    system: &DefinitionRef,
    placement: Placement,
    hy: &HashMap<String, HyFileIndex>,
) -> Vec<SystemGap> {
    let root = normalize_dir(&architecture.root);
    let Some((rel, index, at)) = find(system, hy) else { return vec![SystemGap::Missing(system.clone())] };
    let definition = &index.definitions[at];
    if definition.kind != DefinitionKind::Defsystem {
        return vec![SystemGap::NotSystem(system.clone())];
    }
    let mut out = Vec::new();
    let placed = match placement {
        Placement::OwnEntry => in_entry(&root, service, rel),
        Placement::AnyEntry => architecture.services.iter().any(|s| in_entry(&root, s, rel)),
    };
    if !placed {
        out.push(SystemGap::OutsideEntry(system.clone()));
    }
    let Some(shape) = std::fs::read_to_string(root_path.join(rel)).ok().and_then(|source| read_system(&source, &definition.name)) else {
        return out;
    };
    if shape.params.len() != 1 {
        out.push(SystemGap::NotOneFoundation { system: system.clone(), params: shape.params });
        return out;
    }
    // 土台の型を defsystem の引数に書いた系(doeff L983 の `[#^ T foundation]`)は、その注記で判じる(job の関数の :pre より先)。
    if let Some(text) = shape.foundation_type {
        if UNTYPED_FOUNDATION_HEADS.contains(&type_head(&text)) {
            out.push(SystemGap::UntypedFoundation { system: system.clone(), job: definition.name.clone(), type_text: Some(text) });
        }
        return out;
    }
    let calls = shape.foundation_calls;
    let judged: Vec<SystemGap> = calls
        .iter()
        .filter_map(|(head, position)| {
            let job = callee(index, at, head, hy)?;
            let param = job.params.get(*position)?;
            let type_text = job.param_types.iter().find(|t| &t.name == param).map(|t| t.type_note.text.clone());
            match type_text.as_deref() {
                Some(text) if !UNTYPED_FOUNDATION_HEADS.contains(&type_head(text)) => None,
                _ => Some(SystemGap::UntypedFoundation { system: system.clone(), job: job.name.clone(), type_text }),
            }
        })
        .collect();
    if calls.is_empty() {
        out.push(SystemGap::FoundationUnused { system: system.clone() });
    }
    out.extend(judged);
    out
}

/// service ごとの欠け(architecture.hy の宣言の順・service の中は :system の順)。
pub fn gaps<'a>(root_path: &Path, architecture: &'a Architecture, hy: &HashMap<String, HyFileIndex>) -> Vec<(&'a ArchService, SystemGap)> {
    architecture
        .services
        .iter()
        .flat_map(|service| {
            let found: Vec<SystemGap> = match service.system.as_ref() {
                None => vec![SystemGap::Undeclared],
                Some(SystemDecl::Systems(refs)) if refs.is_empty() => vec![SystemGap::Undeclared],
                Some(SystemDecl::Exempt(reason)) if reason.trim().is_empty() => vec![SystemGap::EmptyExemption],
                Some(SystemDecl::Exempt(_)) => Vec::new(),
                Some(SystemDecl::PartOf(target)) => system_gaps(root_path, architecture, service, target, Placement::AnyEntry, hy),
                Some(SystemDecl::Systems(refs)) => {
                    refs.iter().flat_map(|r| system_gaps(root_path, architecture, service, r, Placement::OwnEntry, hy)).collect()
                }
            };
            found.into_iter().map(move |gap| (service, gap))
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn read_system_reads_params_and_positional_foundation_arguments() {
        let source = "(defsystem s [foundation]\n  \"doc\"\n  (a (job-a foundation) :needs #{\"x\"})\n  (b (job-b \"lit\" foundation :k foundation)))\n\
                      (defsystem two [foundation programs]\n  (c (job-c foundation programs)))\n";
        assert_eq!(
            read_system(source, "s"),
            Some(SystemShape {
                params: vec!["foundation".to_string()],
                foundation_type: None,
                foundation_calls: vec![("job-a".to_string(), 0), ("job-b".to_string(), 1)]
            })
        );
        assert_eq!(read_system(source, "two").map(|s| s.params), Some(vec!["foundation".to_string(), "programs".to_string()]));
        assert_eq!(read_system(source, "other"), None);
    }

    #[test]
    fn read_system_reads_an_annotated_foundation_by_its_name() {
        // doeff L983(agora-redesign #2213)の `[#^ T foundation]` — 注記の付いた引数を落とさず名で数え、注記を土台の型として持つ(#2215)。
        let source = "(defsystem typed [#^ AgoraHost foundation]\n  (a (job-a foundation)))\n\
                      (defsystem typed-two [#^ AgoraHost foundation programs]\n  (b (job-b foundation programs)))\n";
        assert_eq!(
            read_system(source, "typed"),
            Some(SystemShape {
                params: vec!["foundation".to_string()],
                foundation_type: Some("AgoraHost".to_string()),
                foundation_calls: vec![("job-a".to_string(), 0)]
            })
        );
        assert_eq!(read_system(source, "typed-two").map(|s| s.params), Some(vec!["foundation".to_string(), "programs".to_string()]));
    }

    #[test]
    fn type_head_reads_the_outer_name() {
        assert_eq!(type_head("Callable"), "Callable");
        assert_eq!(type_head("(of Callable [str] int)"), "Callable");
        assert_eq!(type_head("(| JobFoundation None)"), "JobFoundation");
        assert_eq!(type_head("JobFoundation"), "JobFoundation");
    }
}
