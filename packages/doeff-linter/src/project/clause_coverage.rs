//! DOEFF167 — 業務の不変条件の条ごとの反例の網羅(agora-redesign #1713・ADR R2 の「条ごとに」)。
//!
//! DOEFF164 は service に反例が 1 本あれば緑で、条(:invariants の関数が返す破りの条の名)ごとの網羅を数えない。条の名は関数の返す値で
//! 静的に読めない(文字列の literal・定数・分岐で選ぶ名・書式の頭と、repo ごとに書き方が違う)ので、持ち主を次のように分ける:
//!
//! - 条の名 = defservice の `:clauses`(宣言)。反例を持たない条は `:clause-exemptions` に理由を書く。
//! - 反例がどの条を破るか = 反例の表(`:counterexamples` — 1 鍵 1 file)の行の `breaks: <service>::<条> …`(壊した handler の宣言)。
//! - 反例が効くか = DOEFF164 と同じ図: 表の節に届く deftest が、その service の entry の層の定義にも届く。
//!
//! 条ごとに、名乗る反例の節の 1 つでも届けば有り・理由つきで外していれば数えない・どちらも無ければ欠け。code を持つ service が
//! `:clauses` を宣言していなければ service の欠け 1 つ。`breaks:` が宣言に無い service か条を名乗れば、表の読めない行として理由を返す。

use std::collections::{BTreeMap, BTreeSet};
use std::path::Path;

/// 反例の表の行で、壊した handler が破ると名乗る条の印。
pub const BREAKS_PREFIX: &str = "breaks:";

/// service の条 1 つの名指し(`<service>::<条>`)。
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct ClauseRef {
    pub service: String,
    pub clause: String,
}

impl ClauseRef {
    /// `<service>::<条>` の綴りを読む(どちらかが空・区切りが無い綴りは None)。
    pub fn parse(text: &str) -> Option<ClauseRef> {
        let (service, clause) = text.split_once("::")?;
        (!service.is_empty() && !clause.is_empty() && !clause.contains("::"))
            .then(|| ClauseRef { service: service.to_string(), clause: clause.to_string() })
    }

    pub fn spelling(&self) -> String {
        format!("{}::{}", self.service, self.clause)
    }
}

/// 反例の表の `breaks:` の宣言を読んだ物(表の鍵 → 名乗る条)と、読めない行の理由。
#[derive(Debug, Default, Clone)]
pub struct ClauseClaims {
    pub by_key: BTreeMap<String, Vec<ClauseRef>>,
    pub problems: Vec<String>,
}

impl ClauseClaims {
    /// 1 鍵 1 file の表の本文 1 つを読む(1 行目が鍵・2 行目から後の `breaks:` で始まる行の空白区切りの綴り)。
    pub fn read_text(&mut self, shown: &str, text: &str) {
        let mut lines = text.lines();
        let Some(key) = lines.next().map(str::trim).filter(|k| !k.is_empty()) else { return };
        for line in lines.map(str::trim) {
            let Some(rest) = line.strip_prefix(BREAKS_PREFIX) else { continue };
            let mut found = Vec::new();
            for word in rest.split_whitespace() {
                match ClauseRef::parse(word) {
                    Some(clause) => found.push(clause),
                    None => self.problems.push(format!("反例の表の file {} の {} の {} は `<service>::<条>` の綴りでない", shown, BREAKS_PREFIX, word)),
                }
            }
            if rest.split_whitespace().next().is_none() {
                self.problems.push(format!("反例の表の file {} の {} の行に条が無い", shown, BREAKS_PREFIX));
            }
            self.by_key.entry(key.to_string()).or_default().extend(found);
        }
    }

    /// root からの相対の dir の `*.txt` を全部読む(読めない dir・file は理由に積む)。
    pub fn load(root: &Path, dir: &str) -> ClauseClaims {
        let mut claims = ClauseClaims::default();
        let entries = match std::fs::read_dir(root.join(dir)) {
            Ok(entries) => entries,
            Err(error) => {
                claims.problems.push(format!("反例の表の dir {} を読めない: {}", dir, error));
                return claims;
            }
        };
        let mut files: Vec<_> =
            entries.filter_map(Result::ok).map(|e| e.path()).filter(|p| p.extension().is_some_and(|ext| ext == "txt")).collect();
        files.sort();
        for file in files {
            let shown = format!("{}/{}", dir.trim_end_matches('/'), file.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default());
            match std::fs::read_to_string(&file) {
                Ok(text) => claims.read_text(&shown, &text),
                Err(error) => claims.problems.push(format!("反例の表の file {} を読めない: {}", shown, error)),
            }
        }
        claims
    }
}

/// 効く反例の節 1 つ(反例の表に在り本番の入口から届かない節): 名乗る条と、節に届く deftest(図の節の添字)。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ClauseCase {
    pub breaks: Vec<ClauseRef>,
    pub tests: BTreeSet<usize>,
}

/// code を持つ service 1 つ(DOEFF164 と同じ母集団): 宣言した条・理由つきで外した条・entry の層に届く deftest。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ServiceClauses {
    pub name: String,
    pub clauses: Option<Vec<String>>,
    pub exempt: BTreeSet<String>,
    pub entry_tests: BTreeSet<usize>,
}

/// service 1 つの欠け 1 つ(閉じた集合)。
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ClauseGap {
    /// `:clauses` を書いていない・空の列。
    Undeclared,
    /// 条を名乗る反例の節に届く deftest の 1 本も service の entry に届かない(claimed = その条を名乗る節の数)。
    Uncovered { clause: String, claimed: usize },
}

impl ClauseGap {
    /// 鍵の service の名の後ろの細目(宣言の欠けは無し・条の欠けは条の名)。
    pub fn detail(&self) -> Option<&str> {
        match self {
            ClauseGap::Undeclared => None,
            ClauseGap::Uncovered { clause, .. } => Some(clause),
        }
    }

    /// 違反の文。
    pub fn describe(&self, service: &str) -> String {
        match self {
            ClauseGap::Undeclared => format!("service {} は不変条件の条の名(:clauses)を宣言していない — 条ごとの反例を数えられない", service),
            ClauseGap::Uncovered { clause, claimed: 0 } => {
                format!("service {} の条 {} に壊した handler の反例が無い(反例の表に `breaks: {}::{}` の行が無く、:clause-exemptions の理由も無い)", service, clause, service, clause)
            }
            ClauseGap::Uncovered { clause, claimed } => format!(
                "service {} の条 {} に効く反例が無い(名乗る反例の表の節 {} — どれに届く deftest も service の entry の層に届かない)",
                service, clause, claimed
            ),
        }
    }
}

/// DOEFF167: service ごと・条ごとの欠け(service の順・条は :clauses の順)。
pub fn gaps(cases: &[ClauseCase], services: &[ServiceClauses]) -> Vec<(usize, ClauseGap)> {
    let mut out = Vec::new();
    for (index, service) in services.iter().enumerate() {
        let clauses = match service.clauses.as_deref() {
            None | Some([]) => {
                out.push((index, ClauseGap::Undeclared));
                continue;
            }
            Some(clauses) => clauses,
        };
        for clause in clauses.iter().filter(|c| !service.exempt.contains(*c)) {
            let named = ClauseRef { service: service.name.clone(), clause: clause.clone() };
            let claiming: Vec<&ClauseCase> = cases.iter().filter(|case| case.breaks.contains(&named)).collect();
            if !claiming.iter().any(|case| !case.tests.is_disjoint(&service.entry_tests)) {
                out.push((index, ClauseGap::Uncovered { clause: clause.clone(), claimed: claiming.len() }));
            }
        }
    }
    out
}

/// 表の `breaks:` が名乗る条のうち、宣言(service の :clauses)に無い物を理由にする(declared = service の名 → 条の名)。
pub fn unknown_claims(claims: &ClauseClaims, declared: &BTreeMap<String, BTreeSet<String>>) -> Vec<String> {
    claims
        .by_key
        .iter()
        .flat_map(|(key, refs)| refs.iter().map(move |r| (key, r)))
        .filter_map(|(key, r)| match declared.get(&r.service) {
            None => Some(format!("反例の表の鍵 {} の {} {} — service {} は宣言に無い", key, BREAKS_PREFIX, r.spelling(), r.service)),
            Some(clauses) if !clauses.contains(&r.clause) => {
                Some(format!("反例の表の鍵 {} の {} {} — 条 {} は service {} の :clauses に無い", key, BREAKS_PREFIX, r.spelling(), r.clause, r.service))
            }
            Some(_) => None,
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn set(nodes: &[usize]) -> BTreeSet<usize> {
        nodes.iter().copied().collect()
    }

    fn named(service: &str, clause: &str) -> ClauseRef {
        ClauseRef { service: service.into(), clause: clause.into() }
    }

    #[test]
    fn breaks_lines_are_read_and_bad_spellings_are_problems() {
        let mut claims = ClauseClaims::default();
        claims.read_text("T/a.txt", "k1\n理由の文\nbreaks: orders::O1 orders::O2\n");
        claims.read_text("T/b.txt", "k2\nbreaks: nocolon\n");
        claims.read_text("T/c.txt", "k3\n理由だけ\n");
        claims.read_text("T/d.txt", "k4\nbreaks:\n");
        assert_eq!(claims.by_key.get("k1"), Some(&vec![named("orders", "O1"), named("orders", "O2")]));
        assert!(!claims.by_key.contains_key("k3"), "breaks の無い行は条を名乗らない");
        assert_eq!(claims.problems.len(), 2, "{:?}", claims.problems);
        assert!(claims.problems.iter().any(|p| p.contains("nocolon")));
        assert!(claims.problems.iter().any(|p| p.contains("条が無い")));
    }

    #[test]
    fn each_clause_needs_a_reaching_counterexample_or_an_exemption() {
        let cases = vec![
            ClauseCase { breaks: vec![named("orders", "O1")], tests: set(&[1]) }, // orders の entry に届く — O1 は有り
            ClauseCase { breaks: vec![named("orders", "O2")], tests: set(&[9]) }, // 名乗るが届かない — O2 は欠け(claimed 1)
            ClauseCase { breaks: vec![named("billing", "B1")], tests: set(&[1]) }, // 別の service の条は orders の条に数えない
        ];
        let services = vec![
            ServiceClauses {
                name: "orders".into(),
                clauses: Some(vec!["O1".into(), "O2".into(), "O3".into(), "O4".into()]),
                exempt: ["O4".to_string()].into_iter().collect(),
                entry_tests: set(&[1, 2]),
            },
            ServiceClauses { name: "stock".into(), clauses: None, exempt: BTreeSet::new(), entry_tests: set(&[3]) },
        ];
        assert_eq!(
            gaps(&cases, &services),
            vec![
                (0, ClauseGap::Uncovered { clause: "O2".into(), claimed: 1 }),
                (0, ClauseGap::Uncovered { clause: "O3".into(), claimed: 0 }),
                (1, ClauseGap::Undeclared),
            ]
        );
        // 陽性対照: 全部の条に届く反例が在れば欠けは無い。
        let all = vec![ClauseCase { breaks: vec![named("orders", "O1"), named("orders", "O2"), named("orders", "O3")], tests: set(&[2]) }];
        assert!(gaps(&all, &services[..1]).is_empty());
    }

    #[test]
    fn claims_of_undeclared_clauses_are_problems() {
        let mut claims = ClauseClaims::default();
        claims.read_text("T/a.txt", "k1\nbreaks: orders::O1 orders::O9 ghost::G1\n");
        let declared: BTreeMap<String, BTreeSet<String>> = [("orders".to_string(), ["O1".to_string()].into_iter().collect())].into_iter().collect();
        let problems = unknown_claims(&claims, &declared);
        assert_eq!(problems.len(), 2, "{:?}", problems);
        assert!(problems.iter().any(|p| p.contains("条 O9 は service orders の :clauses に無い")));
        assert!(problems.iter().any(|p| p.contains("service ghost は宣言に無い")));
    }
}
