//! DOEFF141: definitions judged only from fixed materials (destination for the 2 of the 9 points of agora-controllers' entrance-guarantee check
//! controllers/screen/tests/entrance_guarantee_rules.hy that can be held in a general form).
//!
//! For each definition in architecture.hy's `:blind-definitions [(blind "module:name" :forbid-words [..] :no-imports True :allow-requires [..] :why "…")]`,
//! as follows:
//!   * If a spelling from :forbid-words appears (as a substring) in the body of any repo Hy definition transitively reachable from the definition by calls and by naming (passing as a value)
//!     (including nested definitions, excluding comments), one violation per reached definition and word (`<definition name>:<word>`). Escaping into a helper does not slip through.
//!   * If :no-imports, one violation per import and require in the definition's module (requires of modules listed in :allow-requires are macro loads, so they are excluded)
//!     (`import:<module>`) — the transitive closure follows only the "calls" side, so the entrance that pulls in materials is closed here.
//!   * If a declared definition is not found (no Hy file for the module, or no such definition), one violation at architecture.hy's position (`missing`).
//! Only the declared module and the module files of the reached targets are read (the index is built once per file — the whole repo is not read).
//! Reach targets are what the index resolved names to (`target`), and only modules that have a Hy file in the repo are followed (external packages and Python are not followed).

use std::collections::{BTreeMap, BTreeSet, HashMap};
use std::path::Path;

use doeff_indexer::hy_index::{self, HyFileIndex, Range, RawSettings};

use super::architecture::BlindDefinition;
use crate::position::{offset_of, LineIndex};

/// Kind of violation (a closed set of 3).
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum BlindProblem {
    /// The reached definition `reached` has the word `word` in its body.
    ReadsWord { reached: String, word: String },
    /// The definition's module has an import / require.
    Imports { module: String },
    /// The declared definition was not found.
    Missing { reason: String },
}

/// One violation.
#[derive(Debug, Clone)]
pub struct BlindFinding {
    /// The declared spelling (`module:name`).
    pub declared: String,
    pub why: String,
    /// The file of the violation (path from the root) and the position in it. For Missing, architecture.hy.
    pub rel: String,
    pub range: Range,
    pub problem: BlindProblem,
    /// Details of the registry key.
    pub detail: String,
}

/// One module read (index and contents).
struct Loaded {
    rel: String,
    source: String,
    index: HyFileIndex,
}

/// Store that builds each module's index only once per file.
struct Modules<'a> {
    root: &'a Path,
    raw: &'a RawSettings,
    loaded: HashMap<String, Option<Loaded>>,
    errors: Vec<String>,
}

impl<'a> Modules<'a> {
    /// Mangled dotted module spelling -> Hy file in the repo (None if absent).
    fn load(&mut self, module: &str) -> Option<&Loaded> {
        if !self.loaded.contains_key(module) {
            let rel = format!("{}.hy", module.replace('.', "/"));
            let path = self.root.join(&rel);
            let loaded = match std::fs::read_to_string(&path) {
                Ok(source) => hy_index::index_paths(self.root, &[path.clone()], self.raw).files.into_iter().next().map(|index| Loaded { rel, source, index }),
                Err(error) if path.exists() => {
                    self.errors.push(format!("{}: unreadable: {}", rel, error));
                    None
                }
                Err(_) => None,
            };
            self.loaded.insert(module.to_string(), loaded);
        }
        self.loaded.get(module).and_then(Option::as_ref)
    }

    /// Where the fully qualified name `target` is defined (the module, and the index of the definition in the file) — the module is the longest leading-segment prefix of target that has a Hy file in the repo.
    fn locate(&mut self, target: &str) -> Option<(String, usize)> {
        let parts: Vec<&str> = target.split('.').collect();
        for cut in (1..parts.len()).rev() {
            let module = parts[..cut].join(".");
            if let Some(loaded) = self.load(&module) {
                return loaded.index.definitions.iter().position(|d| d.qualified_name == target).map(|index| (module, index));
            }
        }
        None
    }
}

fn inside(inner: &Range, outer: &Range) -> bool {
    outer.start <= inner.start && inner.end <= outer.end
}

/// Blank out comments in Hy source (from `;` to end of line — `;` inside strings is excluded). The byte length is unchanged, so positions stay the same as in the original source.
fn without_comments(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    let (mut in_string, mut escaped, mut in_comment) = (false, false, false);
    for ch in text.chars() {
        let keep = match ch {
            '\n' => {
                in_comment = false;
                true
            }
            _ if in_comment => false,
            _ if escaped => {
                escaped = false;
                true
            }
            '\\' if in_string => {
                escaped = true;
                true
            }
            '"' => {
                in_string = !in_string;
                true
            }
            ';' if !in_string => {
                in_comment = true;
                false
            }
            _ => true,
        };
        if keep {
            out.push(ch);
        } else {
            out.extend(std::iter::repeat_n(' ', ch.len_utf8()));
        }
    }
    out
}

/// Judge one declaration.
fn judge_one(blind: &BlindDefinition, modules: &mut Modules, architecture_rel: &str) -> Vec<BlindFinding> {
    let declared = blind.definition.spelling();
    let finding = |rel: String, range: Range, problem: BlindProblem, detail: String| BlindFinding { declared: declared.clone(), why: blind.why.clone(), rel, range, problem, detail };
    let module = blind.definition.mangled_module();
    let root_target = blind.definition.target();
    let (rel, has_root, imports) = match modules.load(&module) {
        Some(loaded) => (
            loaded.rel.clone(),
            loaded.index.definitions.iter().any(|d| d.qualified_name == root_target),
            loaded.index.imports.iter().map(|i| (i.module.clone(), i.is_require, i.range)).collect::<Vec<_>>(),
        ),
        None => {
            let reason = format!("Hy file ({}.hy) for module {} is not in the repo", module.replace('.', "/"), blind.definition.module);
            return vec![finding(architecture_rel.to_string(), blind.range, BlindProblem::Missing { reason }, "missing".to_string())];
        }
    };
    if !has_root {
        let reason = format!("{} has no definition {}", rel, blind.definition.name);
        return vec![finding(architecture_rel.to_string(), blind.range, BlindProblem::Missing { reason }, "missing".to_string())];
    }
    let mut out = Vec::new();
    if blind.no_imports {
        for (imported, is_require, range) in imports {
            let allowed = is_require && blind.allow_requires.iter().any(|m| m == &imported);
            if !allowed {
                let detail = format!("import:{}", imported);
                out.push(finding(rel.clone(), range, BlindProblem::Imports { module: imported }, detail));
            }
        }
        // When the same module is read in two forms (import and require), there is still one key per module.
        let mut seen: BTreeSet<String> = BTreeSet::new();
        out.retain(|f| seen.insert(f.detail.clone()));
    }
    // Transitive closure of reachable definitions (fully qualified names) — nested definitions lie inside the outer definition's range, so they are included in the outer definition's body and edges.
    let mut reached: BTreeMap<String, (String, usize)> = BTreeMap::new();
    let mut queue: Vec<String> = vec![root_target.clone()];
    while let Some(target) = queue.pop() {
        if reached.contains_key(&target) {
            continue;
        }
        let Some((module, index)) = modules.locate(&target) else { continue };
        reached.insert(target.clone(), (module.clone(), index));
        let Some(file) = modules.load(&module) else { continue };
        let full = file.index.definitions[index].full_range;
        let in_import = |range: &Range| file.index.imports.iter().any(|imp| inside(range, &imp.range));
        let targets: Vec<String> = file
            .index
            .calls
            .iter()
            .filter(|c| inside(&c.form_range, &full))
            .filter_map(|c| c.target.clone())
            .chain(file.index.references.iter().filter(|r| inside(&r.range, &full) && !in_import(&r.range)).filter_map(|r| r.target.clone()))
            .filter(|t| !reached.contains_key(t))
            .collect();
        queue.extend(targets);
    }
    for (module, index) in reached.values() {
        let Some(file) = modules.load(module) else { continue };
        let definition = &file.index.definitions[*index];
        let start = offset_of(&file.source, definition.full_range.start);
        let end = offset_of(&file.source, definition.full_range.end).max(start);
        let body = without_comments(&file.source[start..end]);
        let lines = LineIndex::new(&file.source);
        for word in &blind.forbid_words {
            if let Some(at) = body.find(word.as_str()) {
                let range = lines.range(start + at, start + at + word.len());
                let reached_name = definition.name.clone();
                let detail = format!("{}:{}", reached_name, word);
                out.push(finding(file.rel.clone(), range, BlindProblem::ReadsWord { reached: reached_name, word: word.clone() }, detail));
            }
        }
    }
    out
}

/// Judge all declarations. architecture_rel is the path of architecture.hy from the root (the position for declarations not found).
pub fn find(root: &Path, blinds: &[BlindDefinition], raw: &RawSettings, architecture_rel: &str) -> (Vec<BlindFinding>, Vec<String>) {
    let mut modules = Modules { root, raw, loaded: HashMap::new(), errors: Vec::new() };
    let found: Vec<BlindFinding> = blinds.iter().flat_map(|blind| judge_one(blind, &mut modules, architecture_rel)).collect();
    (found, modules.errors)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn comments_become_spaces_of_the_same_length() {
        let text = "(defk f [x] ; view.policy is not read\n  \"a;b\" x) ;; trailing";
        let stripped = without_comments(text);
        assert_eq!(stripped.len(), text.len());
        assert!(!stripped.contains("view.policy"));
        assert!(stripped.contains("\"a;b\""), "; inside a string is not a comment");
        assert!(stripped.contains("\n  \"a;b\" x)"));
    }
}
