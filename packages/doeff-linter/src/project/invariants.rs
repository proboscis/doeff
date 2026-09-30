//! DOEFF163 — code を持つ service が業務の不変条件の関数を宣言しているか(agora-redesign #1559・#1155 の定義 1 の条 (b))。
//!
//! 「code を持つ service」は DOEFF136 と同じ母集団: defservice の :layers に entry があり、root/<dir>/entry の下に Hy の定義が 1 本以上ある
//! service(tick・chat のように code の無い service と、entry を持たない service は数えない)。層の dir を持たない repo(merge-queue の
//! ように機能の dir で分けた repo)は、defservice の `:entry-modules ["pkg.module" …]` で code の在りかを宣言し、宣言した service を
//! 母集団に入れる(agora-redesign #1978 — 宣言した module の Hy の file が無ければ欠け)。その service の defservice に
//! `:invariants ["module:関数" …]` が無い・空、名指した関数の定義が repo の Hy の索引に無い、定義の :tags の :role が judgment でない、の
//! どれかを欠けとして返す。不変条件の関数は「記録を受けて破りの列を返す純粋な判断」なので、置き場は問わず(模擬の環境の
//! `*_invariants.hy` でもよい)、role で判じる。

use std::collections::HashMap;

use doeff_indexer::hy_index::HyFileIndex;

use super::architecture::{ArchService, Architecture, DefinitionRef};
use super::settings::normalize_dir;

/// 不変条件の役(定義の :tags の :role)。
pub const JUDGMENT_ROLE: &str = "judgment";

/// service 1 つの欠け 1 つ(閉じた集合)。
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum InvariantGap {
    /// :invariants を書いていない・空の列。
    Undeclared,
    /// 名指した関数の定義が repo の Hy の索引に無い。
    Missing(DefinitionRef),
    /// 名指した定義の :role が judgment でない(role の無い定義は None)。
    NotJudgment { definition: DefinitionRef, role: Option<String> },
    /// `:entry-modules` の module の Hy の file が repo に無い(綴りの誤りで母集団から黙って外れた形にしないため — agora-redesign #1978)。
    EntryModuleMissing(String),
}

impl InvariantGap {
    /// 鍵の service の名の後ろの細目(宣言の欠けは無し・関数の欠けは名指しの綴り・入口の欠けは `entry::<module>`)。
    pub fn detail(&self) -> Option<String> {
        match self {
            InvariantGap::Undeclared => None,
            InvariantGap::Missing(definition) | InvariantGap::NotJudgment { definition, .. } => Some(definition.spelling()),
            InvariantGap::EntryModuleMissing(module) => Some(format!("entry::{}", module)),
        }
    }

    /// 違反の文の中身(何が欠けているか)。
    pub fn describe(&self, service: &str) -> String {
        match self {
            InvariantGap::Undeclared => format!("service {} は :invariants を宣言していない", service),
            InvariantGap::Missing(definition) => {
                format!("service {} の :invariants の {} — 定義が無い(module か関数の名の誤り)", service, definition.spelling())
            }
            InvariantGap::NotJudgment { definition, role } => format!(
                "service {} の :invariants の {} — :role が {}(不変条件は :role \"{}\" の純粋な判断)",
                service,
                definition.spelling(),
                role.as_deref().unwrap_or("無い"),
                JUDGMENT_ROLE
            ),
            InvariantGap::EntryModuleMissing(module) => {
                format!("service {} の :entry-modules の {} — Hy の file({})が無い(module の名の誤り)", service, module, module_path(module))
            }
        }
    }
}

/// module の綴り(`pkg.sub.name`)の Hy の file の repo の根からの path(`pkg/sub/name.hy`)— 入口の宣言を索引の鍵に照らすため。
pub(super) fn module_path(module: &str) -> String {
    format!("{}.hy", module.replace('.', "/"))
}

/// code を持つ service か。`:entry-modules` を宣言した service は宣言で母集団に入る(在りかの欠けは gaps が出す)。
/// 宣言の無い service は entry の層に Hy の定義が 1 本以上あるか。
fn has_code(root: &str, service: &ArchService, hy: &HashMap<String, HyFileIndex>) -> bool {
    if service.entry_modules.is_some() {
        return true;
    }
    if !service.layers.iter().any(|l| l == "entry") {
        return false;
    }
    let entry = format!("{}/{}/entry/", root, service.dir);
    hy.iter().any(|(rel, index)| rel.starts_with(&entry) && !index.definitions.is_empty())
}

/// 名指した定義の :role(外側の None = 定義が無い・内側の None = role の無い定義)。
fn role_of(definition: &DefinitionRef, hy: &HashMap<String, HyFileIndex>) -> Option<Option<String>> {
    let target = definition.target();
    hy.values()
        .flat_map(|index| index.definitions.iter())
        .find(|d| d.qualified_name == target)
        .map(|d| d.tags.as_ref().and_then(|tags| tags.get("role").cloned()))
}

/// service ごとの欠け(architecture.hy の宣言の順・service の中は :entry-modules・:invariants の順)。
pub fn gaps<'a>(architecture: &'a Architecture, hy: &HashMap<String, HyFileIndex>) -> Vec<(&'a ArchService, InvariantGap)> {
    let root = normalize_dir(&architecture.root);
    let mut out = Vec::new();
    for service in architecture.services.iter().filter(|s| has_code(&root, s, hy)) {
        for module in service.entry_modules.iter().flatten() {
            if !hy.contains_key(&module_path(module)) {
                out.push((service, InvariantGap::EntryModuleMissing(module.clone())));
            }
        }
        match service.invariants.as_deref() {
            None | Some([]) => out.push((service, InvariantGap::Undeclared)),
            Some(refs) => {
                for definition in refs {
                    match role_of(definition, hy) {
                        None => out.push((service, InvariantGap::Missing(definition.clone()))),
                        Some(role) if role.as_deref() != Some(JUDGMENT_ROLE) => {
                            out.push((service, InvariantGap::NotJudgment { definition: definition.clone(), role }))
                        }
                        Some(_) => {}
                    }
                }
            }
        }
    }
    out
}
