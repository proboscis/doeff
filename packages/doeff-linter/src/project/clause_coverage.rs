//! DOEFF167 — coverage of counterexamples per clause of the business invariants (the "per clause" of ADR R2).
//!
//! DOEFF164 is green if a service has at least one counterexample, and does not count coverage per clause (the clause name of a violation returned by an :invariants function). Clause names are values returned by functions and
//! cannot be read statically (string literals, constants, names chosen by branches, format heads — each repo writes them differently), so ownership is divided as follows:
//!
//! - Clause names = the defservice `:clauses` (declaration). A clause with no counterexample gives its reason in `:clause-exemptions`.
//! - Which clause a counterexample breaks = the `breaks: <service>::<clause> …` line of the counterexample table (`:counterexamples` — one key per file) (the broken handler's declaration).
//! - Whether a counterexample is effective = the same graph as DOEFF164: a deftest that reaches the table's clause also reaches a definition in that service's entry layer.
//!
//! For each clause: covered if any one of the counterexample clauses claiming it is reached; not counted if exempted with a reason; a gap if neither. A service that has code
//! but does not declare `:clauses` is one service-level gap. If `breaks:` names a service or clause that is not in the declaration, an unreadable table row is returned with a reason.

use std::collections::{BTreeMap, BTreeSet};
use std::path::Path;

/// Marker on a counterexample table row for the clause that the broken handler claims to break.
pub const BREAKS_PREFIX: &str = "breaks:";

/// A reference to one clause of a service (`<service>::<clause>`).
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct ClauseRef {
    pub service: String,
    pub clause: String,
}

impl ClauseRef {
    /// Read a `<service>::<clause>` spelling (None if either side is empty or there is no separator).
    pub fn parse(text: &str) -> Option<ClauseRef> {
        let (service, clause) = text.split_once("::")?;
        (!service.is_empty() && !clause.is_empty() && !clause.contains("::"))
            .then(|| ClauseRef { service: service.to_string(), clause: clause.to_string() })
    }

    pub fn spelling(&self) -> String {
        format!("{}::{}", self.service, self.clause)
    }
}

/// What was read from the `breaks:` declarations of the counterexample table (table key -> claimed clauses), and the reasons for unreadable rows.
#[derive(Debug, Default, Clone)]
pub struct ClauseClaims {
    pub by_key: BTreeMap<String, Vec<ClauseRef>>,
    pub problems: Vec<String>,
}

impl ClauseClaims {
    /// Read one body of a table with one key per file (the first line is the key; from the second line on, whitespace-separated spellings of lines starting with `breaks:`).
    pub fn read_text(&mut self, shown: &str, text: &str) {
        let mut lines = text.lines();
        let Some(key) = lines.next().map(str::trim).filter(|k| !k.is_empty()) else { return };
        for line in lines.map(str::trim) {
            let Some(rest) = line.strip_prefix(BREAKS_PREFIX) else { continue };
            let mut found = Vec::new();
            for word in rest.split_whitespace() {
                match ClauseRef::parse(word) {
                    Some(clause) => found.push(clause),
                    None => self.problems.push(format!("in counterexample table file {}, {} word {} is not a `<service>::<clause>` spelling", shown, BREAKS_PREFIX, word)),
                }
            }
            if rest.split_whitespace().next().is_none() {
                self.problems.push(format!("in counterexample table file {}, the {} line has no clause", shown, BREAKS_PREFIX));
            }
            self.by_key.entry(key.to_string()).or_default().extend(found);
        }
    }

    /// Read all `*.txt` in a dir relative to root (unreadable dirs and files are added to the reasons).
    pub fn load(root: &Path, dir: &str) -> ClauseClaims {
        let mut claims = ClauseClaims::default();
        let entries = match std::fs::read_dir(root.join(dir)) {
            Ok(entries) => entries,
            Err(error) => {
                claims.problems.push(format!("cannot read counterexample table dir {}: {}", dir, error));
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
                Err(error) => claims.problems.push(format!("cannot read counterexample table file {}: {}", shown, error)),
            }
        }
        claims
    }
}

/// One effective counterexample clause (a clause in the counterexample table that the production entry cannot reach): the clause it claims, and the deftests reaching the clause (indexes of graph clauses).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ClauseCase {
    pub breaks: Vec<ClauseRef>,
    pub tests: BTreeSet<usize>,
}

/// One service that has code (same population as DOEFF164): its declared clauses, clauses exempted with a reason, and the deftests reaching the entry layer.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ServiceClauses {
    pub name: String,
    pub clauses: Option<Vec<String>>,
    pub exempt: BTreeSet<String>,
    pub entry_tests: BTreeSet<usize>,
}

/// One gap of one service (a closed set).
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ClauseGap {
    /// `:clauses` is not written or is an empty list.
    Undeclared,
    /// Not even one deftest reaching a counterexample clause that claims the clause reaches the service's entry (claimed = the number of clauses claiming it).
    Uncovered { clause: String, claimed: usize },
}

impl ClauseGap {
    /// Detail after the key's service name (none for an undeclared gap, the clause name for a clause gap).
    pub fn detail(&self) -> Option<&str> {
        match self {
            ClauseGap::Undeclared => None,
            ClauseGap::Uncovered { clause, .. } => Some(clause),
        }
    }

    /// The violation sentence.
    pub fn describe(&self, service: &str) -> String {
        match self {
            ClauseGap::Undeclared => format!("service {} does not declare invariant clause names (:clauses) — counterexamples per clause cannot be counted", service),
            ClauseGap::Uncovered { clause, claimed: 0 } => {
                format!("service {} clause {} has no counterexample with a broken handler (the counterexample table has no `breaks: {}::{}` line, and :clause-exemptions has no reason)", service, clause, service, clause)
            }
            ClauseGap::Uncovered { clause, claimed } => format!(
                "service {} clause {} has no effective counterexample (counterexample table clauses claiming it: {} — no deftest reaching any of them reaches the service's entry layer)",
                service, clause, claimed
            ),
        }
    }
}

/// DOEFF167: gaps per service and per clause (service order; clauses in :clauses order).
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

/// Among the clauses claimed by `breaks:` in the table, turn those not in the declaration (the service's :clauses) into reasons (declared = service name -> clause names).
pub fn unknown_claims(claims: &ClauseClaims, declared: &BTreeMap<String, BTreeSet<String>>) -> Vec<String> {
    claims
        .by_key
        .iter()
        .flat_map(|(key, refs)| refs.iter().map(move |r| (key, r)))
        .filter_map(|(key, r)| match declared.get(&r.service) {
            None => Some(format!("in counterexample table key {}, {} {} — service {} is not in the declaration", key, BREAKS_PREFIX, r.spelling(), r.service)),
            Some(clauses) if !clauses.contains(&r.clause) => {
                Some(format!("in counterexample table key {}, {} {} — clause {} is not in service {}'s :clauses", key, BREAKS_PREFIX, r.spelling(), r.clause, r.service))
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
        claims.read_text("T/a.txt", "k1\nreason text\nbreaks: orders::O1 orders::O2\n");
        claims.read_text("T/b.txt", "k2\nbreaks: nocolon\n");
        claims.read_text("T/c.txt", "k3\nreason only\n");
        claims.read_text("T/d.txt", "k4\nbreaks:\n");
        assert_eq!(claims.by_key.get("k1"), Some(&vec![named("orders", "O1"), named("orders", "O2")]));
        assert!(!claims.by_key.contains_key("k3"), "a line without breaks does not claim a clause");
        assert_eq!(claims.problems.len(), 2, "{:?}", claims.problems);
        assert!(claims.problems.iter().any(|p| p.contains("nocolon")));
        assert!(claims.problems.iter().any(|p| p.contains("has no clause")));
    }

    #[test]
    fn each_clause_needs_a_reaching_counterexample_or_an_exemption() {
        let cases = vec![
            ClauseCase { breaks: vec![named("orders", "O1")], tests: set(&[1]) }, // reaches orders' entry — O1 is covered
            ClauseCase { breaks: vec![named("orders", "O2")], tests: set(&[9]) }, // claimed but not reached — O2 is a gap (claimed 1)
            ClauseCase { breaks: vec![named("billing", "B1")], tests: set(&[1]) }, // another service's clause does not count toward orders' clauses
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
        // Positive control: if counterexamples reach every clause, there is no gap.
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
        assert!(problems.iter().any(|p| p.contains("clause O9 is not in service orders's :clauses")));
        assert!(problems.iter().any(|p| p.contains("service ghost is not in the declaration")));
    }
}
