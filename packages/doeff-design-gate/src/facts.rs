//! The deterministic facts of a change that the Tier 2 SRP review reads (`--format json`), the DOEFF217 hits drawn from them, and the classification of whether the
//! change needs that review (`--classify-trigger`) — docs/design/srp-design-gate/artifacts/v1/design.md of agora-controllers, "機械可読出力とトリガー分類".
//!
//! A fact is one type of a changed module of the population that the change made (absent at the base, another shape, or another LCOM4), or one changed design document
//! of the shapes the review covers. The review is required when any of three conditions holds:
//! 1. `existing-type-extension`: a type the base had gained a field (DOEFF212's growth of an existing type);
//! 2. `width-or-cohesion`: a record that gained fields is wider than 5 (DOEFF215), a gained field is not touched in its defining module (DOEFF216), or LCOM4 rose
//!    (DOEFF217);
//! 3. `cross-module-design`: a design document names modules in 2 or more directories, or the shared intent types.

use std::collections::{BTreeMap, BTreeSet};

use doeff_linter::project::architecture::DesignEvidenceDecl;
use doeff_linter::project::design_evidence::{shapes_of, RecordShape, ShapeKind};
use doeff_linter::project::srp_metrics::{cohesion_of, degrades, Cohesion};
use serde::Serialize;

use crate::git::{BaseTree, ChangedTree, FileRead};
use crate::judge::{modules, Hit};

const LCOM4_DEGRADED: &str = "DOEFF217";
/// The width above which a record that grew needs the review (DOEFF215's limit).
const WIDTH_LIMIT: usize = 5;
/// The spelling of the shared intent types in a design document.
const SHARED_INTENT: &str = "shared/intent/";

/// What a fact is about.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "kebab-case")]
pub(crate) enum FactKind {
    Defrecord,
    Struct,
    Enum,
    DesignDoc,
}

/// One fact of the change (the fields of a type are null for a design document).
#[derive(Debug, Clone, Serialize)]
pub(crate) struct Fact {
    path: String,
    kind: FactKind,
    type_name: Option<String>,
    width_before: Option<usize>,
    width_after: Option<usize>,
    added_fields: Vec<String>,
    module_unused_fields: Vec<String>,
    lcom4_before: Option<usize>,
    lcom4_after: Option<usize>,
    /// For a type: its defining module and the other changed modules that name it. For a design document: the directories of the modules it names.
    cross_module_touch_count: usize,
    /// The groups of the type's functions after the change (empty for a design document).
    lcom4_groups: Vec<Vec<String>>,
}

/// One reason the change needs the Tier 2 review.
#[derive(Debug, Clone, Serialize)]
pub(crate) struct Trigger {
    condition: &'static str,
    path: String,
    type_name: Option<String>,
    detail: String,
}

fn kind_of(shape: &RecordShape) -> FactKind {
    match shape.kind {
        ShapeKind::Defrecord => FactKind::Defrecord,
        ShapeKind::Struct => FactKind::Struct,
        ShapeKind::Enum => FactKind::Enum,
    }
}

fn now_text(now: &ChangedTree, rel: &str) -> Result<Option<String>, String> {
    match now.read(rel) {
        FileRead::Present(text) => Ok(Some(text)),
        FileRead::Absent => Ok(None),
        FileRead::Unreadable(reason) => Err(reason),
    }
}

/// Whether `text` names `word` as a whole word (a Hy name may hold `-`, so it is a word character).
fn names(text: &str, word: &str) -> bool {
    let part = |c: char| c.is_alphanumeric() || c == '_' || c == '-';
    text.match_indices(word).any(|(at, _)| {
        !text[..at].chars().next_back().is_some_and(part) && !text[at + word.len()..].chars().next().is_some_and(part)
    })
}

fn is_design_doc(rel: &str) -> bool {
    let parts: Vec<&str> = rel.split('/').collect();
    let file = parts.last().copied().unwrap_or("");
    matches!(parts.as_slice(), ["docs", "design", _, "artifacts", _, "design.md"]) || (file.starts_with("field-owners") && file.ends_with(".md"))
}

/// The facts of the changed types of one module.
fn module_facts(base: &BaseTree, rel: &str, source: &str, changed: &BTreeMap<String, String>) -> Result<Vec<Fact>, String> {
    let was = match base.read(rel) {
        FileRead::Present(text) => Some(text),
        FileRead::Absent => None,
        FileRead::Unreadable(reason) => return Err(format!("{} at the base cannot be read: {}", rel, reason)),
    };
    let shapes = shapes_of(rel, source)?;
    let cohesion = cohesion_of(rel, source)?;
    // A base the readers cannot read counts as no base: every type of the module is then new (more review, never less).
    let (was_shapes, was_cohesion) = match was.as_deref() {
        Some(text) => (shapes_of(rel, text).unwrap_or_default(), cohesion_of(rel, text).unwrap_or_default()),
        None => (Vec::new(), Vec::new()),
    };
    let lcom4 = |of: &[Cohesion], name: &str| of.iter().find(|c| c.type_name == name).cloned();
    let mut out = Vec::new();
    for shape in &shapes {
        let before = was_shapes.iter().find(|b| b.name == shape.name && b.kind == shape.kind);
        let (after_c, before_c) = (lcom4(&cohesion, &shape.name), lcom4(&was_cohesion, &shape.name));
        let reshaped = before.is_none_or(|b| b.fingerprint() != shape.fingerprint());
        if !reshaped && after_c.as_ref().map(|c| c.lcom4) == before_c.as_ref().map(|c| c.lcom4) {
            continue;
        }
        let added_fields = match before {
            Some(b) => shape.grown_against(b),
            None => shape.fields.iter().map(|f| f.name.clone()).collect(),
        };
        let module_unused_fields =
            after_c.as_ref().map(|c| c.unused_fields.iter().filter(|f| added_fields.contains(f)).cloned().collect()).unwrap_or_default();
        let others = changed.iter().filter(|(other, text)| other.as_str() != rel && names(text, &shape.name)).count();
        out.push(Fact {
            path: rel.to_string(),
            kind: kind_of(shape),
            type_name: Some(shape.name.clone()),
            width_before: before.map(|b| b.fields.len()),
            width_after: Some(shape.fields.len()),
            added_fields,
            module_unused_fields,
            lcom4_before: before_c.as_ref().map(|c| c.lcom4),
            lcom4_after: after_c.as_ref().map(|c| c.lcom4),
            cross_module_touch_count: 1 + others,
            lcom4_groups: after_c.map(|c| c.groups).unwrap_or_default(),
        });
    }
    Ok(out)
}

/// The fact of one changed design document: the directories of the .hy and .rs modules it names.
fn design_doc_fact(rel: &str, text: &str) -> Fact {
    let module = regex::Regex::new(r"[A-Za-z0-9_./-]+\.(?:hy|rs)\b").expect("the module pattern compiles");
    let dirs: BTreeSet<&str> = module.find_iter(text).filter_map(|m| m.as_str().rsplit_once('/').map(|(dir, _)| dir)).collect();
    let shared = text.contains(SHARED_INTENT);
    Fact {
        path: rel.to_string(),
        kind: FactKind::DesignDoc,
        type_name: None,
        width_before: None,
        width_after: None,
        added_fields: Vec::new(),
        module_unused_fields: Vec::new(),
        lcom4_before: None,
        lcom4_after: None,
        // A design document that names the shared intent types counts as touching modules beyond its own (condition 3), whatever it names besides.
        cross_module_touch_count: dirs.len().max(if shared { 2 } else { 0 }),
        lcom4_groups: Vec::new(),
    }
}

/// The facts of the changed paths (Err = cannot judge: a changed file or its base version cannot be read or parsed).
pub(crate) fn facts(base: &BaseTree, now: &ChangedTree, decl: &DesignEvidenceDecl, paths: &[String]) -> Result<Vec<Fact>, String> {
    let mut changed: BTreeMap<String, String> = BTreeMap::new();
    for rel in paths.iter().filter(|rel| rel.ends_with(".hy") || rel.ends_with(".rs") || is_design_doc(rel)) {
        if let Some(text) = now_text(now, rel)? {
            changed.insert(rel.clone(), text);
        }
    }
    let mut out = Vec::new();
    for rel in modules(now, paths, decl) {
        let source = match changed.get(&rel) {
            Some(text) => text.clone(),
            None => match now_text(now, &rel)? {
                Some(text) => text,
                None => continue,
            },
        };
        out.extend(module_facts(base, &rel, &source, &changed).map_err(|reason| format!("{}: {}", rel, reason))?);
    }
    out.extend(changed.iter().filter(|(rel, _)| is_design_doc(rel)).map(|(rel, text)| design_doc_fact(rel, text)));
    Ok(out)
}

/// The DOEFF217 hits of the facts: a type whose LCOM4 rose above max(1, its LCOM4 at the base).
pub(crate) fn lcom4_hits(facts: &[Fact]) -> Vec<Hit> {
    facts
        .iter()
        .filter_map(|f| {
            let (name, after) = (f.type_name.as_ref()?, f.lcom4_after?);
            if !degrades(f.lcom4_before, after) {
                return None;
            }
            Some(Hit {
                rel: f.path.clone(),
                rule: LCOM4_DEGRADED,
                detail: format!("lcom4:{}", name),
                reason: format!(
                    "the functions of {} in {} fall into {} unrelated groups (the base has {}): {} — split the type so each group's fields are one type, or join the groups",
                    name,
                    f.path,
                    after,
                    f.lcom4_before.map(|b| b.to_string()).unwrap_or_else(|| "no such type".to_string()),
                    f.lcom4_groups.iter().map(|g| format!("[{}]", g.join(" "))).collect::<Vec<_>>().join(" ")
                ),
            })
        })
        .collect()
}

/// The reasons the change needs the Tier 2 review (empty = it does not).
pub(crate) fn triggers(facts: &[Fact]) -> Vec<Trigger> {
    let mut out = Vec::new();
    for f in facts {
        let at = |condition: &'static str, detail: String| Trigger { condition, path: f.path.clone(), type_name: f.type_name.clone(), detail };
        if f.kind == FactKind::DesignDoc {
            if f.cross_module_touch_count >= 2 {
                out.push(at("cross-module-design", format!("names modules in {} directories or the shared intent types", f.cross_module_touch_count)));
            }
            continue;
        }
        let grew = !f.added_fields.is_empty();
        if grew && f.width_before.is_some() {
            out.push(at("existing-type-extension", format!("gained {}", f.added_fields.join(", "))));
        }
        let width = f.width_after.unwrap_or(0);
        if grew && f.kind != FactKind::Enum && width > WIDTH_LIMIT {
            out.push(at("width-or-cohesion", format!("DOEFF215: {} fields > {}", width, WIDTH_LIMIT)));
        }
        if !f.module_unused_fields.is_empty() {
            out.push(at("width-or-cohesion", format!("DOEFF216: not touched in its module: {}", f.module_unused_fields.join(", "))));
        }
        if let Some(after) = f.lcom4_after.filter(|a| degrades(f.lcom4_before, *a)) {
            out.push(at("width-or-cohesion", format!("DOEFF217: LCOM4 {} -> {}", f.lcom4_before.map(|b| b.to_string()).unwrap_or_else(|| "none".to_string()), after)));
        }
    }
    out
}
