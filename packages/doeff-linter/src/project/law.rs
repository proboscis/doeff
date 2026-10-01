//! law の対応の検めた形 — law の名・文・当たる層と、law に書かれた規則の ID(層の規則か、既存の Python の規則か)。
//! 説明(`explain`)と設定の組み立て(`settings`)が読む(agora-redesign #2123 で settings.rs から分けた)。

use std::collections::BTreeSet;

use super::layers::LayerId;
use super::rule::ProjectRule;

/// law の対応 1 件(検めた後)。
#[derive(Debug, Clone)]
pub struct LawSpec {
    pub name: String,
    pub adr: Option<String>,
    pub statement: String,
    pub rules: Vec<ProjectRuleOrExternal>,
    /// 当たる層(空なら全部)。
    pub layers: BTreeSet<LayerId>,
}

/// law に書かれた規則の ID — 層の規則(閉じた一覧)か、既存の Python の規則の ID。
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ProjectRuleOrExternal {
    Project(ProjectRule),
    External(String),
}

impl ProjectRuleOrExternal {
    /// 規則の ID の綴りを返す(出力と照合のため)。
    pub fn id(&self) -> &str {
        match self {
            ProjectRuleOrExternal::Project(rule) => rule.id(),
            ProjectRuleOrExternal::External(id) => id,
        }
    }
}
