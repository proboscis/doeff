//! The judgment of the changed paths against the base. A type is judged only if the change made it: one the base's version of the same file does not have, or has in
//! another shape. A type the base already had in the same shape is not judged here even if the ledger lacks it — that is the base's debt, not this change's (the daily
//! whole-repo run still reports it). The ledgers and the approvals are read at the base; the declaration after the change must equal the base's unless an approval at the
//! base covers the new spelling.

use std::collections::{BTreeMap, BTreeSet};
use std::path::Path;

use doeff_linter::project::architecture::DesignEvidenceDecl;
use doeff_linter::project::design_evidence::{self as evidence, ExistingShapes};
use doeff_linter::project::ledger_guard::{
    self as guard, declaration_fingerprint, ApprovalLedger, Approvals, BaseApprovals, GuardHit,
};
use doeff_linter::project::type_substitutes::{self as substitutes, SubstituteCounts};
use doeff_linter::project::typed_values::substitute_hits;
use serde::Serialize;

use crate::declaration::{spelling_now, BaseDeclaration};
use crate::git::{read_now, BaseTree, FileRead};

/// One reason to refuse the change.
#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct Hit {
    pub(crate) rel: String,
    pub(crate) rule: &'static str,
    pub(crate) detail: String,
    pub(crate) reason: String,
}

const DESIGN_EVIDENCE: &str = "DOEFF212";
const TYPE_SUBSTITUTE: &str = "DOEFF213";
const LEDGER_GUARD: &str = "DOEFF214";
/// The value of a `declaration` approval that lets the declaration be removed.
const REMOVED_DECLARATION: &str = "absent";

/// What the base holds for the judgment, read once.
struct AtBase<'a> {
    base: &'a BaseTree,
    declaration: &'a BaseDeclaration,
    shapes: ExistingShapes,
    substitutes: SubstituteCounts,
    approvals_raw: Approvals,
    approvals: BaseApprovals,
    approval_pattern: Option<regex::Regex>,
    review_pattern: Option<regex::Regex>,
}

fn base_text(base: &BaseTree, rel: Option<&str>) -> Result<Option<String>, String> {
    match rel.map(|rel| (rel, base.read(rel))) {
        None | Some((_, FileRead::Absent)) => Ok(None),
        Some((_, FileRead::Present(text))) => Ok(Some(text)),
        Some((rel, FileRead::Unreadable(reason))) => Err(format!("{} at the base cannot be read: {}", rel, reason)),
    }
}

fn parsed<T: Default>(rel: Option<&str>, text: Option<String>, parse: impl Fn(&str) -> Result<T, String>) -> Result<T, String> {
    match text {
        None => Ok(T::default()),
        Some(text) => parse(&text).map_err(|reason| format!("{} at the base cannot be parsed: {}", rel.unwrap_or(""), reason)),
    }
}

impl<'a> AtBase<'a> {
    fn load(base: &'a BaseTree, declaration: &'a BaseDeclaration) -> Result<AtBase<'a>, String> {
        let decl = &declaration.decl;
        let shapes = parsed(decl.existing.as_deref(), base_text(base, decl.existing.as_deref())?, ExistingShapes::parse)?;
        let substitutes = parsed(decl.existing_substitutes.as_deref(), base_text(base, decl.existing_substitutes.as_deref())?, SubstituteCounts::parse)?;
        let approvals_rel = decl.guard.as_ref().and_then(|g| g.approvals.as_deref());
        let approvals_raw = parsed(approvals_rel, base_text(base, approvals_rel)?, Approvals::parse)?;
        let approval_pattern = decl.guard.as_ref().and_then(guard::approval_pattern);
        let approvals = approvals_raw.clone().spelled(approval_pattern.as_ref());
        let review_pattern = decl.review_pattern.as_deref().and_then(|p| regex::Regex::new(p).ok());
        Ok(AtBase { base, declaration, shapes, substitutes, approvals_raw, approvals, approval_pattern, review_pattern })
    }

    fn decl(&self) -> &DesignEvidenceDecl {
        &self.declaration.decl
    }
}

#[derive(Serialize)]
struct ShapesLedger {
    shapes: BTreeMap<String, String>,
}

#[derive(Serialize)]
struct CountsLedger {
    counts: BTreeMap<String, u32>,
}

fn from_guard(rule: &'static str, hits: Vec<GuardHit>) -> impl Iterator<Item = Hit> {
    hits.into_iter().map(move |h| Hit { rel: h.rel, rule, detail: h.detail, reason: h.reason })
}

/// The base's version of a file as a source (None if the base has no such file).
fn base_source(at: &AtBase, rel: &str) -> Result<Option<String>, String> {
    base_text(at.base, Some(rel))
}

/// The ledger one module is judged with: the base ledger's lines of the module, then every type of the base's version of the module in its base shape (a type unchanged
/// from the base passes).
fn module_shapes(at: &AtBase, rel: &str, base: Option<&str>) -> Result<ExistingShapes, String> {
    let prefix = format!("{}::", rel);
    let mut shapes: BTreeMap<String, String> =
        at.shapes.lines().filter(|(key, _)| key.starts_with(&prefix)).map(|(k, v)| (k.to_string(), v.to_string())).collect();
    for shape in base.map(|text| evidence::shapes_of(rel, text)).transpose()?.unwrap_or_default() {
        shapes.insert(format!("{}{}", prefix, shape.name), shape.fingerprint());
    }
    let text = toml::to_string(&ShapesLedger { shapes }).map_err(|e| e.to_string())?;
    ExistingShapes::parse(&text)
}

/// The counts one module is judged with: the larger of the base ledger's count and the base version's count, per kind.
fn module_counts(at: &AtBase, rel: &str, base: Option<&str>) -> Result<SubstituteCounts, String> {
    let prefix = format!("{}::", rel);
    let mut counts: BTreeMap<String, u32> =
        at.substitutes.lines().filter(|(key, _)| key.starts_with(&prefix)).map(|(k, v)| (k.to_string(), v)).collect();
    let mut found: BTreeMap<String, u32> = BTreeMap::new();
    for hit in base.map(substitute_hits).transpose()?.unwrap_or_default().into_iter().filter(|h| h.kind.stops()) {
        *found.entry(format!("{}{}", prefix, hit.kind.word())).or_insert(0) += 1;
    }
    for (key, count) in found {
        let allowed = counts.entry(key).or_insert(0);
        *allowed = (*allowed).max(count);
    }
    let text = toml::to_string(&CountsLedger { counts }).map_err(|e| e.to_string())?;
    SubstituteCounts::parse(&text)
}

/// The hits of one module of the population, as it is after the change.
fn judge_module(at: &AtBase, repo: &Path, rel: &str) -> Result<Vec<Hit>, String> {
    let source = match read_now(repo, rel) {
        FileRead::Present(text) => text,
        FileRead::Absent => return Ok(Vec::new()),
        FileRead::Unreadable(reason) => return Err(reason),
    };
    let evidence_rel = evidence::evidence_rel(rel);
    let evidence_text = match read_now(repo, &evidence_rel) {
        FileRead::Present(text) => Some(text),
        FileRead::Absent => None,
        FileRead::Unreadable(reason) => return Err(reason),
    };
    let base = base_source(at, rel)?;
    let ledger = module_shapes(at, rel, base.as_deref())?;
    let mut hits: Vec<Hit> = evidence::judge(rel, &source, evidence_text.as_deref(), &ledger, at.review_pattern.as_ref())?
        .into_iter()
        .chain(match at.decl().guard {
            Some(_) => evidence::grown_fields(rel, &source, &at.shapes, || base.clone(), &at.approvals)?,
            None => Vec::new(),
        })
        .map(|h| Hit { rel: rel.to_string(), rule: DESIGN_EVIDENCE, detail: h.problem.key_detail(&h.name), reason: h.problem.describe(&h.name, &evidence_rel) })
        .collect();
    if substitutes::wants(rel, at.decl()) {
        let counts = module_counts(at, rel, base.as_deref())?;
        hits.extend(substitutes::judge(rel, &source, &counts)?.into_iter().map(|excess| Hit {
            rel: rel.to_string(),
            rule: TYPE_SUBSTITUTE,
            detail: excess.kind.word().to_string(),
            reason: format!(
                "{} has {} {} values standing in for a type (the base allows {}) — give the value a type (defrecord) instead",
                rel,
                excess.hits.len(),
                excess.kind.word(),
                excess.allowed
            ),
        }));
    }
    Ok(hits)
}

/// The hits of a changed ledger or approvals file (DOEFF214), against its version at the base.
fn judge_guarded(at: &AtBase, repo: &Path, rel: &str) -> Result<Vec<Hit>, String> {
    let decl = at.decl();
    let now = |parse: &dyn Fn(&str) -> Result<(), String>| -> Result<Option<String>, String> {
        match read_now(repo, rel) {
            FileRead::Present(text) => parse(&text).map(|_| Some(text)).map_err(|reason| format!("{} cannot be parsed: {}", rel, reason)),
            FileRead::Absent => Ok(None),
            FileRead::Unreadable(reason) => Err(reason),
        }
    };
    let mut hits = Vec::new();
    if decl.existing.as_deref() == Some(rel) {
        let text = now(&|t| ExistingShapes::parse(t).map(|_| ()))?;
        let shapes = text.map(|t| ExistingShapes::parse(&t)).transpose()?.unwrap_or_default();
        hits.extend(from_guard(LEDGER_GUARD, guard::grown_shapes(rel, &shapes, &at.shapes, &at.approvals)));
    }
    if decl.existing_substitutes.as_deref() == Some(rel) {
        let text = now(&|t| SubstituteCounts::parse(t).map(|_| ()))?;
        let counts = text.map(|t| SubstituteCounts::parse(&t)).transpose()?.unwrap_or_default();
        hits.extend(from_guard(LEDGER_GUARD, guard::grown_substitutes(rel, &counts, &at.substitutes, &at.approvals)));
    }
    if decl.guard.as_ref().and_then(|g| g.approvals.as_deref()) == Some(rel) {
        let text = now(&|t| Approvals::parse(t).map(|_| ()))?;
        let approvals = text.map(|t| Approvals::parse(&t)).transpose()?.unwrap_or_default();
        hits.extend(from_guard(LEDGER_GUARD, guard::unspelled_approvals(rel, &approvals, &at.approvals_raw, at.approval_pattern.as_ref())));
    }
    Ok(hits)
}

/// The hit if the declaration after the change differs from the base's and no approval at the base covers the new spelling (removing it needs the value `absent`).
fn judge_declaration(at: &AtBase, repo: &Path) -> Result<Option<Hit>, String> {
    let file = &at.declaration.file;
    let now = spelling_now(repo, file)?;
    if now.as_deref() == Some(at.declaration.spelling.as_str()) {
        return Ok(None);
    }
    let value = now.as_deref().map(declaration_fingerprint).unwrap_or_else(|| REMOVED_DECLARATION.to_string());
    if at.approvals.covers(ApprovalLedger::Declaration, ":design-evidence", &value) {
        return Ok(None);
    }
    Ok(Some(Hit {
        rel: file.clone(),
        rule: LEDGER_GUARD,
        detail: "declaration:design-evidence".to_string(),
        reason: format!(
            "{} changes the :design-evidence declaration of the base (now {}) without an approval at the base — narrowing it or removing it is a way around DOEFF212 to 214",
            file, value
        ),
    }))
}

/// Judges the changed paths (Err = cannot judge: something the judgment reads at the base or in the tree cannot be read).
pub(crate) fn judge(base: &BaseTree, declaration: &BaseDeclaration, paths: &[String]) -> Result<Vec<Hit>, String> {
    let at = AtBase::load(base, declaration)?;
    let repo = base.repo();
    let mut hits: Vec<Hit> = judge_declaration(&at, repo)?.into_iter().collect();
    for rel in paths {
        hits.extend(judge_guarded(&at, repo, rel)?);
    }
    for rel in modules(repo, paths, at.decl()) {
        hits.extend(judge_module(&at, repo, &rel)?);
    }
    Ok(hits)
}

/// The modules of the population a change touches: each changed .hy or .rs, and the module next to each changed evidence file.
pub(crate) fn modules(repo: &Path, paths: &[String], decl: &DesignEvidenceDecl) -> BTreeSet<String> {
    let suffix = ".evidence.toml";
    let mut modules: BTreeSet<String> = BTreeSet::new();
    for rel in paths {
        if let Some(stem) = rel.strip_suffix(suffix) {
            modules.extend([format!("{}.hy", stem), format!("{}.rs", stem)].into_iter().filter(|m| repo.join(m).exists()));
        } else {
            modules.insert(rel.clone());
        }
    }
    modules.retain(|rel| evidence::wants(rel, decl));
    modules
}
