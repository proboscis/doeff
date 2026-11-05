//! DOEFF165: the coverage table of intent effects (parent #1155 "definition of having 'tested' business logic").
//!
//! For each effect (defeffect) declared in each service's intent layer (`:assembly-shape :intent-layer`), three columns are read:
//!   * Whether a local test emits it — a definition reachable from a test file's definitions (effect clauses = excluding answerers) names (constructs) the effect.
//!   * Simulation answerer — effect clauses reachable from the simulation roots (simulation environment, assembly layer, simulation assembly functions).
//!   * Production answerer — effect clauses reachable from the production entries (production assembly functions, defsystem, `__main__`, entry strings).
//! The reachability graph is the same one as DOEFF143 and 158 (`judge_business_fakes`). This file holds only the pure judgment that decides gaps from the column values.
//! An effect with even one gap becomes one violation (key detail `<service>::<effect>`). K3 was report-only (severity info). From K4 (#1562),
//! severity is error and the default tier is critical, so it fails; known gaps listed in the repo's registry drop to warning (only new gaps are red).

/// Column values for one effect (facts read from the reachability graph).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct EffectFacts {
    /// Full name of the effect (module + name).
    pub effect: String,
    /// The file that declares the effect (path from the root).
    pub rel: String,
    /// The service that has the declaring file in its location (defservice name in architecture.hy) — None if it is under no service's dir.
    pub service: Option<String>,
    /// Definitions reachable from local tests that name this effect (excluding answerers; full names; sorted).
    pub emitters: Vec<String>,
    /// Answerers reachable from the simulation roots (full names of `<handler>`; sorted).
    pub simulated: Vec<String>,
    /// Answerers reachable from the production entries (sorted).
    pub produced: Vec<String>,
}

/// Kind of gap (a closed set of 3).
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub enum Gap {
    /// No definition reachable from local tests emits this effect (no test goes through this business operation).
    NotEmittedByTest,
    /// No answerer is reachable from the simulation roots (emitting it in the simulation environment gets no answer).
    NoSimulatedAnswerer,
    /// No answerer is reachable from the production entries (emitting it in production gets no answer).
    NoProducedAnswerer,
}

impl Gap {
    /// A phrase for humans.
    pub fn words(self) -> &'static str {
        match self {
            Gap::NotEmittedByTest => "not emitted by local tests",
            Gap::NoSimulatedAnswerer => "no simulation answerer",
            Gap::NoProducedAnswerer => "no production answerer",
        }
    }
}

/// One table row (one effect).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Row {
    pub facts: EffectFacts,
    pub gaps: Vec<Gap>,
}

impl Row {
    /// Notice key detail `<service>::<effect>` (`-` for an effect with no service).
    pub fn detail(&self) -> String {
        format!("{}::{}", self.facts.service.as_deref().unwrap_or("-"), self.facts.effect)
    }

    /// The three columns on one line (`--explain` and the notice body).
    pub fn columns(&self) -> String {
        let list = |names: &[String]| if names.is_empty() { "none".to_string() } else { names.join(", ") };
        format!(
            "definitions emitted by local tests = {} / simulation answerer = {} / production answerer = {}",
            list(&self.facts.emitters),
            list(&self.facts.simulated),
            list(&self.facts.produced)
        )
    }

    /// One line listing the gap phrases.
    pub fn gap_words(&self) -> String {
        self.gaps.iter().map(|g| g.words()).collect::<Vec<_>>().join(", ")
    }
}

/// Decide gaps from the column values.
pub fn gaps_of(facts: &EffectFacts) -> Vec<Gap> {
    let mut gaps = Vec::new();
    if facts.emitters.is_empty() {
        gaps.push(Gap::NotEmittedByTest);
    }
    if facts.simulated.is_empty() {
        gaps.push(Gap::NoSimulatedAnswerer);
    }
    if facts.produced.is_empty() {
        gaps.push(Gap::NoProducedAnswerer);
    }
    gaps
}

/// All rows of the table (in service, effect order). Includes rows with no gaps — only rows with gaps become notices (the caller selects).
pub fn table(facts: Vec<EffectFacts>) -> Vec<Row> {
    let mut rows: Vec<Row> = facts.into_iter().map(|f| Row { gaps: gaps_of(&f), facts: f }).collect();
    rows.sort_by(|a, b| a.facts.service.cmp(&b.facts.service).then_with(|| a.facts.effect.cmp(&b.facts.effect)));
    rows
}

/// From the location of the file that declares an effect's full name, look up the service that owns it (longest dir prefix match).
pub fn service_of<'s>(rel: &str, services: &'s [(String, String)]) -> Option<&'s str> {
    services
        .iter()
        .filter(|(_, dir)| rel.starts_with(&format!("{}/", dir.trim_end_matches('/'))))
        .max_by_key(|(_, dir)| dir.len())
        .map(|(name, _)| name.as_str())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn facts(service: &str, effect: &str, emitters: &[&str], simulated: &[&str], produced: &[&str]) -> EffectFacts {
        let owned = |xs: &[&str]| xs.iter().map(|x| x.to_string()).collect::<Vec<_>>();
        EffectFacts {
            effect: effect.to_string(),
            rel: format!("app/{}/intent/effects.hy", service),
            service: Some(service.to_string()),
            emitters: owned(emitters),
            simulated: owned(simulated),
            produced: owned(produced),
        }
    }

    #[test]
    fn a_small_example_table_names_each_gap() {
        // A small example with gaps: 1 covered effect, 1 effect not emitted by tests, 1 effect with no production answerer, 1 effect with nothing (another service).
        let rows = table(vec![
            facts("board", "app.board.intent.effects.MoveCard", &["app.board.core.move"], &["app.board.protocol.h"], &["app.board.protocol.h"]),
            facts("board", "app.board.intent.effects.ArchiveCard", &[], &["app.board.protocol.h"], &["app.board.protocol.h"]),
            facts("board", "app.board.intent.effects.ClaimCard", &["app.board.core.claim"], &["app.board.protocol.h"], &[]),
            facts("chat", "app.chat.intent.effects.Post", &[], &[], &[]),
        ]);
        let got: Vec<(String, Vec<Gap>)> = rows.iter().map(|r| (r.detail(), r.gaps.clone())).collect();
        assert_eq!(
            got,
            vec![
                ("board::app.board.intent.effects.ArchiveCard".to_string(), vec![Gap::NotEmittedByTest]),
                ("board::app.board.intent.effects.ClaimCard".to_string(), vec![Gap::NoProducedAnswerer]),
                ("board::app.board.intent.effects.MoveCard".to_string(), vec![]),
                (
                    "chat::app.chat.intent.effects.Post".to_string(),
                    vec![Gap::NotEmittedByTest, Gap::NoSimulatedAnswerer, Gap::NoProducedAnswerer]
                ),
            ]
        );
        // The three columns list names; an empty column is written "none".
        let claim = &rows[1];
        assert_eq!(claim.columns(), "definitions emitted by local tests = app.board.core.claim / simulation answerer = app.board.protocol.h / production answerer = none");
        assert_eq!(rows[3].gap_words(), "not emitted by local tests, no simulation answerer, no production answerer");
    }

    #[test]
    fn a_fully_covered_effect_has_no_gap() {
        // Counterexample: an effect whose three columns are all filled has 0 gaps (no notice).
        let full = facts("board", "app.board.intent.effects.MoveCard", &["t"], &["s"], &["p"]);
        assert!(gaps_of(&full).is_empty());
    }

    #[test]
    fn the_service_is_the_longest_dir_prefix() {
        let services = vec![("board".to_string(), "app/board".to_string()), ("board-ui".to_string(), "app/board/ui".to_string())];
        assert_eq!(service_of("app/board/intent/effects.hy", &services), Some("board"));
        assert_eq!(service_of("app/board/ui/intent/effects.hy", &services), Some("board-ui"));
        // Counterexample: the same name prefix but a different dir does not match; None if under no dir.
        assert_eq!(service_of("app/boardroom/intent/effects.hy", &services), None);
        assert_eq!(service_of("lib/x.hy", &services), None);
    }
}
