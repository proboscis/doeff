//! Base trait for all lint rules

use crate::models::{RuleContext, Violation};

/// 規則が 1 回の当てで見る単位。本体(lib の check_stmt_recursive)はこの単位どおりに文を渡す。
/// 単位が規則と本体の約束に無いと、自分で入れ子へ降りる規則や module 全体を見る規則が、本体が渡す文の数だけ
/// 同じ当たりを数える(agora-redesign #2858)。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RuleReach {
    /// 渡された文 1 つ(の自分の式)だけを見る。入れ子の文は本体が 1 つずつ渡す — 全部の文に当てる。
    Statement,
    /// 渡された文とその中の入れ子を自分で歩く(囲みの try・class の欄ほかの文脈が要る規則)。本体は module の
    /// 上の段の文だけを渡す。
    Subtree,
    /// module 全体を見る。本体は file に 1 度だけ、module の最初の文を `stmt` にして渡す。
    Module,
}

/// Base trait that all lint rules must implement
pub trait LintRule: Send + Sync {
    /// The unique identifier for this rule (e.g., "DOEFF001")
    fn rule_id(&self) -> &str;

    /// Short description of what the rule checks
    fn description(&self) -> &str;

    /// Check if this rule is enabled (default: true)
    fn is_enabled(&self) -> bool {
        true
    }

    /// この規則が 1 回の当てで見る単位(既定 = 文 1 つ)。本体はこれに従って `check` を呼ぶ。
    fn reach(&self) -> RuleReach {
        RuleReach::Statement
    }

    /// Perform the lint check on a statement
    fn check(&self, context: &RuleContext) -> Vec<Violation>;
}



