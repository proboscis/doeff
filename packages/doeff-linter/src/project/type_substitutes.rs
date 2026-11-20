//! DOEFF213: a fact is carried by a type, not by a value that stands in for one (docs/design/design-evidence v2 §9).
//!
//! DOEFF212 asks for design evidence when a type's shape changes. A fact carried without a type — a mapping whose keys are written in the source, a tuple whose positions
//! carry different meanings, an optional written as a sequence bounded to 0 or 1 items — needs no evidence, so it is the way around DOEFF212. This rule reads the same
//! population (`:design-evidence :files / :except`) and counts, per file and kind, the values that stand in for a type (typed_values::substitute_hits):
//! - `static-key-dict`: `{:a … :b …}`, `{"a" …}`, `(dict :a …)`, a `dfor` with a literal key — static elements held in a mapping (a mapping is allowed only from an id to data);
//! - `attribute-keyed-dict`: a mapping keyed by a title, a name, a text (`(dfor r rows r.title r)`) — the key of a mapping is an id;
//! - `answer-tuple`: a function (any function — nested, private, a method) answers a tuple literal of 2 or more items;
//! - `zero-or-one`: a defrecord's `:check` bounds a sequence field to 0 or 1 items.
//!
//! The values that exist when the rule is turned on are counted in the ledger `:existing-substitutes` (`"<rel>::<kind>" = <count>`, written once with
//! `--print-existing-substitutes`). A file passes while each kind's count is at most its ledger count; above it, every value of that kind in the file is a hit
//! (the count, not the place, is grandfathered — moving code does not make a hit, adding one does). The ledger only shrinks: DOEFF214 stops a ledger line that is added or raised.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};

use super::architecture::DesignEvidenceDecl;
use super::design_evidence::wants;
use super::retired::judge_files;
use super::typed_values::{substitute_hits, SubstituteHit, SubstituteKind};

/// The ledger of the values that stood in for a type when the rule was turned on: `"<rel>::<kind>"` → count.
#[derive(Debug, Clone, Default, Deserialize, Serialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct SubstituteCounts {
    #[serde(default)]
    counts: BTreeMap<String, u32>,
}

impl SubstituteCounts {
    pub fn parse(text: &str) -> Result<Self, String> {
        toml::from_str(text).map_err(|error| error.to_string().trim().to_string())
    }

    fn key(rel: &str, kind: SubstituteKind) -> String {
        format!("{}::{}", rel, kind.word())
    }

    /// The count the ledger allows for one file and kind (0 if absent).
    pub fn allowed(&self, rel: &str, kind: SubstituteKind) -> u32 {
        self.counts.get(&Self::key(rel, kind)).copied().unwrap_or(0)
    }

    /// The ledger's lines (for DOEFF214's comparison with the base).
    pub fn lines(&self) -> impl Iterator<Item = (&str, u32)> {
        self.counts.iter().map(|(k, v)| (k.as_str(), *v))
    }

    /// The ledger of the given hits, as TOML (`--print-existing-substitutes`).
    pub fn spell<'a>(files: impl Iterator<Item = (&'a str, &'a [SubstituteHit])>) -> String {
        let mut counts = BTreeMap::new();
        for (rel, hits) in files {
            for hit in hits.iter().filter(|h| h.kind.stops()) {
                *counts.entry(Self::key(rel, hit.kind)).or_insert(0) += 1;
            }
        }
        toml::to_string(&SubstituteCounts { counts }).unwrap_or_default()
    }
}

/// One file's kind above its ledger count: the values of that kind (all of them — the ledger holds a count, not places) and the two counts.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Excess {
    pub kind: SubstituteKind,
    pub allowed: u32,
    pub hits: Vec<SubstituteHit>,
}

/// The kinds of one file above their ledger counts (Err if the Hy reader cannot read the file).
pub fn judge(rel: &str, source: &str, counts: &SubstituteCounts) -> Result<Vec<Excess>, String> {
    let hits = substitute_hits(source)?;
    let mut by_kind: BTreeMap<SubstituteKind, Vec<SubstituteHit>> = BTreeMap::new();
    for hit in hits.into_iter().filter(|h| h.kind.stops()) {
        by_kind.entry(hit.kind).or_default().push(hit);
    }
    Ok(by_kind
        .into_iter()
        .filter_map(|(kind, hits)| {
            let allowed = counts.allowed(rel, kind);
            (hits.len() as u32 > allowed).then_some(Excess { kind, allowed, hits })
        })
        .collect())
}

/// Reads the declared ledger (an empty ledger and the reason if it cannot be read — then every value is a hit).
pub fn load(root: &Path, decl: &DesignEvidenceDecl) -> (SubstituteCounts, Option<String>) {
    match &decl.existing_substitutes {
        None => (SubstituteCounts::default(), None),
        Some(path) => match std::fs::read_to_string(root.join(path)).map_err(|e| e.to_string()).and_then(|text| SubstituteCounts::parse(&text)) {
            Ok(counts) => (counts, None),
            Err(reason) => (SubstituteCounts::default(), Some(format!("{}: cannot read the DOEFF213 ledger ({})", path, reason))),
        },
    }
}

/// The excess of one module.
pub struct FileExcess {
    pub rel: String,
    pub path: PathBuf,
    pub source: String,
    pub excess: Vec<Excess>,
}

/// Reads and judges the declared .hy files (only files under the focus if there is one).
pub fn find(root: &Path, decl: &DesignEvidenceDecl, focus: Option<&[PathBuf]>) -> (Vec<FileExcess>, Vec<String>) {
    let (counts, ledger_error) = load(root, decl);
    let (found, mut errors) = judge_files(
        root,
        decl.selection.files.iter(),
        focus,
        |rel, _| wants(rel, decl),
        |rel, path, source| {
            let excess = judge(&rel, &source, &counts).map_err(|reason| format!("{}: cannot read the DOEFF213 judgment ({})", rel, reason))?;
            Ok((!excess.is_empty()).then(|| FileExcess { rel, path, source, excess }))
        },
    );
    errors.extend(ledger_error);
    (found, errors)
}

/// The ledger of every value that stands in for a type in the population, as TOML (`--print-existing-substitutes`; written once when the rule is turned on).
pub fn existing_counts(root: &Path, decl: &DesignEvidenceDecl) -> (String, Vec<String>) {
    let (mut found, errors) = judge_files(
        root,
        decl.selection.files.iter(),
        None,
        |rel, _| wants(rel, decl),
        |rel, _, source| substitute_hits(&source).map(|hits| Some((rel.clone(), hits))).map_err(|reason| format!("{}: {}", rel, reason)),
    );
    found.sort_by(|a, b| a.0.cmp(&b.0));
    // DOEFF_LINTER_SUBSTITUTE_SITES=1: one line per value on stderr (`<rel>:<byte> <kind> <owner> [boundary]`) — for measuring what the counts hold before turning the rule on.
    if std::env::var_os("DOEFF_LINTER_SUBSTITUTE_SITES").is_some() {
        for (rel, hits) in &found {
            for hit in hits {
                eprintln!("site {}:{} {} {}{}", rel, hit.start, hit.kind.word(), hit.owner, if hit.boundary { " boundary" } else { "" });
            }
        }
    }
    (SubstituteCounts::spell(found.iter().map(|(rel, hits)| (rel.as_str(), hits.as_slice()))), errors)
}

#[cfg(test)]
mod tests {
    use super::*;

    const HY: &str = r#"
(defrecord Gap
  "one gap"
  {:check [(<= (len links) 1)] :tags {:role "type"}}
  (#^ (get tuple #(str ...)) links))
(defk material-of [card rows]
  {:pre [(: card str)] :post [(: % Gap)] :tags {:context "x" :role "judgment"}}
  (setv by-id (dfor r rows r.id r))
  (setv by-title (dfor r rows r.title r))
  (setv seen #{1 2})
  (setv extra {:card card :rows (len rows)})
  (setv more (dict :card card))
  #(card extra))
(defk to-json [gap]
  {:pre [(: gap Gap)] :post [(: % dict)]}
  {"links" (list gap.links)})
(defn _pair [a b]
  (fn [x] #(a x)))
(defeffect Ask {:fields [(: x str)] :tags {:context "x" :role "intent"}})
(defk ordered [rows]
  {:pre [(: rows list)] :post [(: % list)]}
  (sorted rows :key (fn [r] #(r.at r.id))))
(setv BY-NAME (dfor v VARS v.name v))
(val MODULE-TAGS {:context "x" :role "judgment"})
(defrecord Parts
  {:check [(<= (len (lfor p parts :if p.main p)) 1)]}
  (#^ (get tuple #(str ...)) parts))
(defmacro m [x] `{:x ~x})
"#;

    fn kinds(source: &str) -> Vec<(String, &'static str, bool)> {
        substitute_hits(source).expect("readable").into_iter().map(|h| (h.owner, h.kind.word(), h.boundary)).collect()
    }

    #[test]
    fn values_that_stand_in_for_a_type_are_found_and_id_maps_sort_keys_and_form_options_are_not() {
        assert_eq!(
            kinds(HY),
            vec![
                ("Gap".to_string(), "zero-or-one", false),
                ("material-of".to_string(), "attribute-keyed-dict", false),
                ("material-of".to_string(), "static-key-dict", false),
                ("material-of".to_string(), "static-key-dict", false),
                ("material-of".to_string(), "answer-tuple", false),
                ("to-json".to_string(), "static-key-dict", true),
                ("_pair".to_string(), "answer-tuple", false),
            ]
        );
    }

    #[test]
    fn the_ledger_lets_a_file_keep_its_count_and_stops_one_more() {
        let hits = substitute_hits(HY).unwrap();
        let ledger = SubstituteCounts::parse(&SubstituteCounts::spell(std::iter::once(("m.hy", hits.as_slice())))).unwrap();
        assert_eq!(ledger.allowed("m.hy", SubstituteKind::StaticKeyDict), 3);
        assert!(judge("m.hy", HY, &ledger).unwrap().is_empty());
        let more = HY.replace("#(card extra))", "#(card {:a 1}))");
        let excess = judge("m.hy", &more, &ledger).unwrap();
        assert!(matches!(excess.as_slice(), [Excess { kind: SubstituteKind::StaticKeyDict, allowed: 3, hits }] if hits.len() == 4), "{:?}", excess);
        // answer-tuple is found but not stopped: 3 kinds above an empty ledger.
        assert_eq!(judge("m.hy", HY, &SubstituteCounts::default()).unwrap().len(), 3);
    }
}
