//! 基点との比べ(`--baseline-report <file>`・仕様 1 節「基点との比べ」・agora-redesign #1803)。
//!
//! マージ前の検査と commit の hook は、保存した既知の一覧を持たず、基点(main の先端)と commit の両方で linter を走らせ、
//! commit の critical の識別子の集合が基点の集合に含まれていれば通す(#1762 の決定 A)。件数ではなく識別子で比べるので、
//! 3 件直して 1 件足した commit は赤になる。基点の側を走らせるのは呼び手の役で、この module は 2 つの集合を比べるだけ。

use std::collections::BTreeSet;

use serde_json::Value;

use crate::editor::EditorViolation;
use crate::project::rule::{ProjectRule, RuleLevel};

/// 違反の識別子 — `<path>::<規則>::<名>`(行番号を含めない)。鍵を持つ違反(層の規則ほか)は鍵そのもの、鍵の無い違反
/// (Python の文ごとの規則)は名の代わりに message を使う。
pub fn identity(violation: &EditorViolation) -> String {
    match &violation.key {
        Some(key) => key.clone(),
        None => format!("{}::{}::{}", violation.path, violation.rule, violation.message),
    }
}

/// この実行の critical の識別子の集合。
pub fn critical_identities(violations: &[EditorViolation]) -> BTreeSet<String> {
    violations.iter().filter(|v| v.level == RuleLevel::Critical).map(identity).collect()
}

/// 基点で走らせた editor-json の出力から、critical の識別子の集合を読む。形が違えば理由を返す(呼び手は終了コード 2)。
pub fn read_baseline(text: &str) -> Result<BTreeSet<String>, String> {
    let report: Value = serde_json::from_str(text).map_err(|e| format!("基点の出力が JSON でない: {}", e))?;
    baseline_identities(&report)
}

/// 基点の editor-json の出力(読み済みの値)の critical の識別子の集合 — read_baseline と、止めた当たりが HEAD の版にも在るかを分ける
/// commit の hook(commit_hook::judge)が同じ綴りで読む。
pub fn baseline_identities(report: &Value) -> Result<BTreeSet<String>, String> {
    let violations = report
        .get("violations")
        .and_then(Value::as_array)
        .ok_or_else(|| "基点の出力に violations の列が無い(editor-json の出力を渡す)".to_string())?;
    let mut identities = BTreeSet::new();
    for violation in violations {
        if violation.get("level").and_then(Value::as_str) != Some("critical") {
            continue;
        }
        let field = |name: &str| {
            violation.get(name).and_then(Value::as_str).ok_or_else(|| format!("基点の違反に欄 {} が無い: {}", name, violation))
        };
        let identity = match violation.get("key").and_then(Value::as_str) {
            Some(key) => key.to_string(),
            None => format!("{}::{}::{}", field("path")?, field("rule")?, field("message")?),
        };
        identities.insert(identity);
    }
    Ok(identities)
}

/// 違反 1 つが、基点の差で下げずに止める当たりか — 規則 `rule` が既知の一覧を持たない規則(登録簿で下げない規則 —
/// `ProjectRule::lowered_by_registry` が偽 = DOEFF206〜209)で、重大さが critical(agora-redesign #3834)。変えた file に在れば止める判定の
/// 1 か所 — 名指しの実行の new_critical(blocking_criticals)と commit の hook の repo 全体の比べ(commit_hook::fresh_whole_repo_hits)が使う。
pub fn blocks_regardless_of_baseline(rule: &str, level: RuleLevel) -> bool {
    level == RuleLevel::Critical && ProjectRule::parse(rule).is_some_and(|rule| !rule.lowered_by_registry())
}

/// この実行の違反のうち、基点の差で下げずに止める当たりの識別子。
fn unlisted_rule_criticals(violations: &[EditorViolation]) -> impl Iterator<Item = String> + '_ {
    violations.iter().filter(|v| blocks_regardless_of_baseline(&v.rule, v.level)).map(identity)
}

/// 止める critical の識別子(辞書順)— 基点に無い critical(new_criticals)に加え、名指しの path が在る実行(`named` — 変えた file を
/// 名指す commit の hook と登録の前の確かめ)では、既知の一覧を持たない規則の critical を全部(agora-redesign #3834・cisco-c8 の可)。
/// その規則は今在る当たりを基点の差でも登録簿でも通さず、変えた file に当たりが在れば止める(細かさは file 単位)。violations は
/// 名指しの path で絞った後の列。名指しの無い実行(全体)は基点の差だけ — 全体の数は日次で出す。
pub fn blocking_criticals(baseline: &BTreeSet<String>, violations: &[EditorViolation], named: bool) -> Vec<String> {
    let fresh = new_criticals(baseline, &critical_identities(violations));
    if !named {
        return fresh;
    }
    fresh.into_iter().chain(unlisted_rule_criticals(violations)).collect::<BTreeSet<String>>().into_iter().collect()
}

/// 識別子の path を除いた部分(`<規則>::<名>`)。
fn without_path(identity: &str) -> &str {
    identity.split_once("::").map_or(identity, |(_, rest)| rest)
}

/// 基点に無い critical の識別子(辞書順)。file の移動・改名は、規則と名が同じで path だけ違い、基点のその識別子が今は
/// 消えている時に、1 対 1 で同じ破れとみなす(基点の識別子が残ったまま別の path に同じ名が増えたら新しい破れ)。
pub fn new_criticals(baseline: &BTreeSet<String>, current: &BTreeSet<String>) -> Vec<String> {
    let mut vanished: Vec<&String> = baseline.difference(current).collect();
    let mut fresh = Vec::new();
    for identity in current.difference(baseline) {
        match vanished.iter().position(|old| without_path(old) == without_path(identity)) {
            Some(index) => {
                vanished.remove(index);
            }
            None => fresh.push(identity.clone()),
        }
    }
    fresh
}
