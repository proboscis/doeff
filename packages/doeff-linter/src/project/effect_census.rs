//! DOEFF162: the whole set of effect declarations (#1438 — originally a temporary check in agora-controllers,
//! 2 of controllers/screen/tests/intake_only_rules.hy).
//!
//! For each architecture.hy declaration `:effect-census [(effect-census "name" :files [..] :effects [..] :base "EffectBase" :why "…") …]`,
//! collect class declarations inheriting `:base` from the Hy and Python files matching `:files` (comments dropped) and compare them with the `:effects` list.
//!   * An effect declaration not in the list -> one hit at the declaration position (key detail `<name>:<effect>`).
//!   * Two or more declarations of the same name -> one hit at the second position (`<name>:<effect>:twice`).
//!   * An effect in the list with no declaration -> one hit at the architecture.hy position (`<name>:<effect>:missing`).
//!   * If no file matches `:files`, one hit at the architecture.hy position (`<name>:missing` — an empty population is never green).
//! A change that adds an effect must update the declaration list in the same change (so effects do not grow silently). Recognized declaration forms are Python `class X(… base …):` and Hy
//! `(defclass [decorators] X [… base …])` / `(defclass X [… base …])`, and when `:base` is `EffectBase`, also Hy `(defeffect X …)`
//! (defeffect always inherits EffectBase).

use std::collections::BTreeMap;
use std::path::Path;

use doeff_indexer::hy_index::{Position, Range};

use super::architecture::EffectCensus;
use super::spelling_scope::{code_text, line_of, selected_files};

/// Kind of hit (a closed set of 4).
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum CensusProblem {
    /// Declares an effect that is not in the list.
    Unlisted { effect: String },
    /// Declares the same effect name twice.
    Twice { effect: String },
    /// An effect in the list has no declaration.
    Undeclared { effect: String },
    /// No file matches `:files`.
    NoFiles,
}

/// One hit.
#[derive(Debug, Clone)]
pub struct CensusFinding {
    pub rel: String,
    /// Name of the declaration.
    pub group: String,
    pub why: String,
    pub range: Range,
    pub problem: CensusProblem,
    /// Detail of the registry key.
    pub detail: String,
}

fn at_line(line: u32) -> Range {
    let at = Position { line, character: 0 };
    Range { start: at, end: at }
}

/// (name, byte position) of classes inheriting base in a file's body (comments dropped).
fn declared_in(rel: &str, code: &str, base: &str) -> Vec<(String, usize)> {
    let is_effect_base = base == EFFECT_BASE;
    let base = regex::escape(base);
    let pattern = if rel.ends_with(".hy") {
        let defclass = format!(r"\(defclass\s+(?:\[[^\]]*\]\s+)?([\w\-]+)\s+\[[^\]]*\b{}\b[^\]]*\]", base);
        // doeff-hy's defeffect always expands to a class inheriting doeff's EffectBase (defeffect-form in doeff_hy/declarations.hy).
        if is_effect_base { format!(r"{}|\(defeffect\s+([\w\-]+)", defclass) } else { defclass }
    } else {
        format!(r"(?m)^[ \t]*class\s+(\w+)\s*\([^)]*\b{}\b[^)]*\)\s*:", base)
    };
    let Ok(regex) = regex::Regex::new(&pattern) else { return Vec::new() };
    regex
        .captures_iter(code)
        .filter_map(|c| c.get(1).or_else(|| c.get(2)).map(|m| (m.as_str().to_string(), m.start())))
        .collect()
}

/// The base name that defeffect inherits.
const EFFECT_BASE: &str = "EffectBase";

/// Judge one declaration. Files that could not be read are pushed to errors.
fn judge_one(root: &Path, declared: &EffectCensus, architecture_rel: &str, errors: &mut Vec<String>) -> Vec<CensusFinding> {
    let finding = |rel: String, range: Range, problem: CensusProblem, detail: String| CensusFinding {
        rel,
        group: declared.name.clone(),
        why: declared.why.clone(),
        range,
        problem,
        detail,
    };
    let population = selected_files(root, &declared.files, &[]);
    if population.is_empty() {
        return vec![finding(architecture_rel.to_string(), declared.range, CensusProblem::NoFiles, format!("{}:missing", declared.name))];
    }
    // name -> list of declaration positions (file, line) (in path order, then body order).
    let mut seen: BTreeMap<String, Vec<(String, u32)>> = BTreeMap::new();
    for rel in population {
        let source = match std::fs::read_to_string(root.join(&rel)) {
            Ok(source) => source,
            Err(error) => {
                errors.push(format!("{}: cannot read: {}", rel, error));
                continue;
            }
        };
        let code = code_text(&rel, &source);
        for (name, at) in declared_in(&rel, &code, &declared.base) {
            seen.entry(name).or_default().push((rel.clone(), line_of(&code, at)));
        }
    }
    let mut out = Vec::new();
    for (name, places) in &seen {
        let (rel, line) = &places[0];
        if !declared.effects.contains(name) {
            out.push(finding(
                rel.clone(),
                at_line(*line),
                CensusProblem::Unlisted { effect: name.clone() },
                format!("{}:{}", declared.name, name),
            ));
        }
        if let Some((rel, line)) = places.get(1) {
            out.push(finding(
                rel.clone(),
                at_line(*line),
                CensusProblem::Twice { effect: name.clone() },
                format!("{}:{}:twice", declared.name, name),
            ));
        }
    }
    for name in declared.effects.iter().filter(|name| !seen.contains_key(*name)) {
        out.push(finding(
            architecture_rel.to_string(),
            declared.range,
            CensusProblem::Undeclared { effect: name.clone() },
            format!("{}:{}:missing", declared.name, name),
        ));
    }
    out
}

/// Judge all declarations. architecture_rel is the path of architecture.hy from the root (the position for effects with no declaration).
pub fn find(root: &Path, declarations: &[EffectCensus], architecture_rel: &str) -> (Vec<CensusFinding>, Vec<String>) {
    let mut errors = Vec::new();
    let found = declarations.iter().flat_map(|declared| judge_one(root, declared, architecture_rel, &mut errors)).collect();
    (found, errors)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn census(files: &[&str], effects: &[&str]) -> EffectCensus {
        EffectCensus {
            name: "screen".to_string(),
            files: files.iter().map(|p| p.to_string()).collect(),
            effects: effects.iter().map(|p| p.to_string()).collect(),
            base: "EffectBase".to_string(),
            why: "reason".to_string(),
            range: Range::default(),
        }
    }

    fn repo(files: &[(&str, &str)]) -> tempfile::TempDir {
        let dir = tempfile::tempdir().unwrap();
        for (rel, text) in files {
            let path = dir.path().join(rel);
            std::fs::create_dir_all(path.parent().unwrap()).unwrap();
            std::fs::write(path, text).unwrap();
        }
        dir
    }

    #[test]
    fn python_and_hy_declarations_are_collected_and_comments_are_not() {
        let py = "# class Old(EffectBase):\nclass Send(EffectBase):\n    pass\n\nclass Log(Mixin, EffectBase):\n    pass\nclass Row(Base):\n    pass\n";
        let hy = "; (defclass [(dataclass)] Gone [EffectBase])\n(defclass [(dataclass :frozen True)] Close [EffectBase])\n(defclass Plain [EffectBase])\n";
        assert_eq!(declared_in("e.py", &code_text("e.py", py), "EffectBase").into_iter().map(|d| d.0).collect::<Vec<_>>(), vec!["Send", "Log"]);
        assert_eq!(declared_in("s.hy", &code_text("s.hy", hy), "EffectBase").into_iter().map(|d| d.0).collect::<Vec<_>>(), vec!["Close", "Plain"]);
    }

    #[test]
    fn hy_defeffect_declarations_count_as_effect_base() {
        let hy = "; (defeffect Gone {:answer int :tags {}})\n(defeffect Tally\n  \"Counts\"\n  {:answer int :tags {}})\n(defeffect Frame-Cut {:answer int :tags {}})\n";
        let code = code_text("s.hy", hy);
        assert_eq!(declared_in("s.hy", &code, "EffectBase").into_iter().map(|d| d.0).collect::<Vec<_>>(), vec!["Tally", "Frame-Cut"]);
        // defeffect always inherits doeff's EffectBase — it does not count as a declaration of a different base.
        assert!(declared_in("s.hy", &code, "ScreenEffect").is_empty());
        let dir = repo(&[("s/loop_reports.hy", hy)]);
        let (hits, errors) = find(dir.path(), &[census(&["s/loop_reports.hy"], &["Tally", "Frame-Cut"])], "architecture.hy");
        assert!(errors.is_empty(), "{:?}", errors);
        assert!(hits.is_empty(), "{:?}", hits);
    }

    #[test]
    fn unlisted_twice_and_undeclared_effects_are_each_found() {
        let dir = repo(&[
            ("s/effects.py", "class Send(EffectBase):\n    pass\nclass Sneaky(EffectBase):\n    pass\n"),
            ("s/intent/socket.hy", "(defclass [(dataclass)] Send [EffectBase])\n"),
        ]);
        let (hits, errors) = find(dir.path(), &[census(&["s/effects.py", "s/intent/socket.hy"], &["Send", "Close"])], "architecture.hy");
        assert!(errors.is_empty(), "{:?}", errors);
        let details: Vec<&str> = hits.iter().map(|h| h.detail.as_str()).collect();
        assert_eq!(details, vec!["screen:Send:twice", "screen:Sneaky", "screen:Close:missing"], "{:?}", hits);
        assert_eq!(hits[1].rel, "s/effects.py");
        assert_eq!(hits[1].range.start.line, 2);
    }

    #[test]
    fn an_empty_population_is_missing_not_green() {
        let dir = repo(&[]);
        let (hits, _) = find(dir.path(), &[census(&["gone/*.py"], &["Send"])], "architecture.hy");
        assert_eq!(hits.len(), 1, "{:?}", hits);
        assert_eq!(hits[0].problem, CensusProblem::NoFiles);
    }
}
