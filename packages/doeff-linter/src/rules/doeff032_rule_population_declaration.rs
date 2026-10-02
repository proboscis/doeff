//! DOEFF032: 規則の母集団の宣言の破れ(agora-redesign #2811)
//!
//! 層の宣言は、名指しの module に限って規則を母集団から外せる(`(layer 名 :modules [..] :exempt [(rule 規則 ID "理由")]
//! :forbid-modules [..])`)。外すのは Program の外の code(Python の起動の時点で `.pth` から入る見張りほか)だけで、そこに業務の code が
//! 入ると、外した規則がその code にも当たらなくなる。この規則は次の 2 つを error にする:
//!   - 外した層の module が、その層の `:forbid-modules` の module を import している(外した層に業務の code が入った)
//!   - file を持つ package の architecture.hy を読めず、その file の母集団を決められない(外さずに、読めないことを名指す)
//!
//! 当たりは、file ごとに最も近い architecture.hy を引く 1 点(crate::population — lib::lint_source_at)が出す。文ごとの文脈(RuleContext)は
//! file の層を持たないので、この規則の check は何も返さず、規則の名と有効・無効の口(設定の enable / disable)だけを持つ。

use crate::models::{RuleContext, Violation};
use crate::population::BUSINESS_IMPORT_RULE_ID;
use crate::rules::base::LintRule;

pub struct RulePopulationDeclarationRule;

impl RulePopulationDeclarationRule {
    pub fn new() -> Self {
        Self
    }
}

impl Default for RulePopulationDeclarationRule {
    fn default() -> Self {
        Self::new()
    }
}

impl LintRule for RulePopulationDeclarationRule {
    fn rule_id(&self) -> &str {
        BUSINESS_IMPORT_RULE_ID
    }

    fn description(&self) -> &str {
        "規則の母集団から外した層に業務の code が入っている・母集団を決める宣言を読めない"
    }

    fn check(&self, _context: &RuleContext) -> Vec<Violation> {
        // 当たりは crate::population が file ごとに出す(上の頭注)。
        Vec::new()
    }
}
