//! DOEFF163 — whether a service that has code declares business invariant functions.
//!
//! "A service that has code" is the same population as DOEFF136: the defservice :layers has entry, and there is at least one Hy definition under root/<dir>/entry
//! (services without code such as tick and chat, and services without an entry, are not counted). A repo with no layer dirs (a repo split by feature dirs, like merge-queue)
//! declares where its code lives with the defservice `:entry-modules ["pkg.module" …]`, and declared services are
//! added to the population (a missing Hy file for a declared module is a gap). It returns as gaps any of: the service's defservice has no
//! `:invariants ["module:function" …]` or has an empty one, a named function's definition is not in the repo's Hy index, or the definition's :tags :role is not judgment.
//! An invariant function is "a pure judgment that takes records and returns a list of violations", so its location does not matter (a mock environment's
//! `*_invariants.hy` is fine); it is decided by role.

use std::collections::HashMap;

use doeff_indexer::hy_index::HyFileIndex;

use super::architecture::{ArchService, Architecture, DefinitionRef};
use super::layers::normalize_dir;

/// Role of an invariant (the :role in the definition's :tags).
pub const JUDGMENT_ROLE: &str = "judgment";

/// One gap of one service (a closed set).
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum InvariantGap {
    /// :invariants is not written or is an empty list.
    Undeclared,
    /// The named function's definition is not in the repo's Hy index.
    Missing(DefinitionRef),
    /// The named definition's :role is not judgment (None for a definition with no role).
    NotJudgment { definition: DefinitionRef, role: Option<String> },
    /// The Hy file of a `:entry-modules` module is missing from the repo (so that a spelling mistake does not silently drop it from the population).
    EntryModuleMissing(String),
}

impl InvariantGap {
    /// Detail after the key's service name (none for an undeclared gap, the named spelling for a function gap, `entry::<module>` for an entry gap).
    pub fn detail(&self) -> Option<String> {
        match self {
            InvariantGap::Undeclared => None,
            InvariantGap::Missing(definition) | InvariantGap::NotJudgment { definition, .. } => Some(definition.spelling()),
            InvariantGap::EntryModuleMissing(module) => Some(format!("entry::{}", module)),
        }
    }

    /// Body of the violation sentence (what is missing).
    pub fn describe(&self, service: &str) -> String {
        match self {
            InvariantGap::Undeclared => format!("service {} does not declare :invariants", service),
            InvariantGap::Missing(definition) => {
                format!("service {} :invariants {} — no definition (wrong module or function name)", service, definition.spelling())
            }
            InvariantGap::NotJudgment { definition, role } => format!(
                "service {} :invariants {} — :role is {} (an invariant is a pure judgment with :role \"{}\")",
                service,
                definition.spelling(),
                role.as_deref().unwrap_or("absent"),
                JUDGMENT_ROLE
            ),
            InvariantGap::EntryModuleMissing(module) => {
                format!("service {} :entry-modules {} — Hy file ({}) not found (wrong module name)", service, module, module_path(module))
            }
        }
    }
}

/// Path from the repo root of the Hy file for a module spelling (`pkg.sub.name`) (`pkg/sub/name.hy`) — to check the entry declaration against the index keys.
pub(super) fn module_path(module: &str) -> String {
    format!("{}.hy", module.replace('.', "/"))
}

/// Whether the service has code. A service that declares `:entry-modules` enters the population by the declaration (a missing location is reported by gaps).
/// A service without a declaration has at least one Hy definition in its entry layer.
pub(super) fn has_code(root: &str, service: &ArchService, hy: &HashMap<String, HyFileIndex>) -> bool {
    if service.entry_modules.is_some() {
        return true;
    }
    if !service.layers.iter().any(|l| l == "entry") {
        return false;
    }
    let entry = format!("{}/{}/entry/", root, service.dir);
    hy.iter().any(|(rel, index)| rel.starts_with(&entry) && !index.definitions.is_empty())
}

/// The named definition's :role (outer None = no definition, inner None = definition without a role).
fn role_of(definition: &DefinitionRef, hy: &HashMap<String, HyFileIndex>) -> Option<Option<String>> {
    let target = definition.target();
    hy.values()
        .flat_map(|index| index.definitions.iter())
        .find(|d| d.qualified_name == target)
        .map(|d| d.tags.as_ref().and_then(|tags| tags.get("role").cloned()))
}

/// Gaps per service (in architecture.hy declaration order; within a service, :entry-modules then :invariants order).
pub fn gaps<'a>(architecture: &'a Architecture, hy: &HashMap<String, HyFileIndex>) -> Vec<(&'a ArchService, InvariantGap)> {
    let root = normalize_dir(&architecture.root);
    let mut out = Vec::new();
    for service in architecture.services.iter().filter(|s| has_code(&root, s, hy)) {
        for module in service.entry_modules.iter().flatten() {
            if !hy.contains_key(&module_path(module)) {
                out.push((service, InvariantGap::EntryModuleMissing(module.clone())));
            }
        }
        match service.invariants.as_deref() {
            None | Some([]) => out.push((service, InvariantGap::Undeclared)),
            Some(refs) => {
                for definition in refs {
                    match role_of(definition, hy) {
                        None => out.push((service, InvariantGap::Missing(definition.clone()))),
                        Some(role) if role.as_deref() != Some(JUDGMENT_ROLE) => {
                            out.push((service, InvariantGap::NotJudgment { definition: definition.clone(), role }))
                        }
                        Some(_) => {}
                    }
                }
            }
        }
    }
    out
}
