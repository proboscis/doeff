//! Headings (signatures) of defk / deff, and binding types — `signatures` and `bindings` of editor-json (contract version 2).
//!
//! The editor (doeff-runner) draws a defk's types, effects, and tags as a heading, and draws the type before a binding's name. How types are read is placed in this one place,
//! and the editor holds no copy (display only — source is not rewritten by hand, so no write-direction conversion is held).
//!
//! What is read:
//! - Argument types = the `(: name type)` in the contract dict's `:pre`, the answer type = the `(: % type)` in `:post`, declared effects = `:effects [E …]` (#800),
//!   tags = the string values of `:tags`.
//! - Inferred effects = for a call fired in the body (`(<- …)` / `(! …)`) whose head is a defeffect, that effect; if a defk, add that defk's inference
//!   (a fixed point over the table collected from all of the repo's Hy files). Effects received by a handler are not subtracted (an estimate from above).
//! - The Absent wrapper (the answer is Maybe) = when an effect that declares `:absent` (or a defk whose answer is Maybe) is fired
//!   with neither `:absent F` nor `absent-as`. `:absent F` is replaced by a Raise of F's type. Raise = the fired effect's `:failure` and the fired defk's Raise
//!   (inside the program of `on-raise` it is subtracted).
//! - Binding types = the annotation of `(<- x T …)` / the value answer of the fired effect (`:answer` minus `:absent` and `:failure`) / the answer of the fired defk
//!   / a literal (string, number, bool, None, dict, list) / a call of a type name (builds a record) / the answer of a deff. `(:= x v)` is the type of
//!   `(var x …)` in the same definition. **Where it is unknown, the type is null; it is never filled with a different type**.
//!
//! Type names are resolved up to the module using the file's imports and the definition's location (`smells::Scope`), and if they match a definition in the repo (defrecord, defwire, defenum, defclass,
//! defeffect, a val with a capitalized head) the definition's position is attached (so the editor can click through to the definition).

use std::collections::{BTreeMap, BTreeSet, HashMap};
use std::path::Path;

use doeff_indexer::hy_index::reader::{Delim, Form, Node, Reader};
use serde::{Deserialize, Serialize};

use crate::position::{LineIndex, Range};

use super::blob_chunks::{ChunkedFacts, Lookup};
use super::facts::form_bindings;
use super::names::module_of;
use super::smells::{live, Hy, Scope};

// --- Shape of the contract ------------------------------------------------------------------------------

/// The position of a definition (where the editor jumps to when clicked).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Location {
    pub path: String,
    pub range: Range,
}

/// A type expression (a closed set of the contract). `name` is the spelling as written, `definition` is the definition in the repo (null for built-ins and names that cannot be resolved).
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(tag = "kind", rename_all = "lowercase")]
pub enum TypeRef {
    Name {
        name: String,
        definition: Option<Location>,
    },
    Union {
        members: Vec<TypeRef>,
    },
    Apply {
        head: Box<TypeRef>,
        args: Vec<TypeRef>,
    },
    /// An expression that cannot be read as a type (the spelling as written).
    Unknown {
        text: String,
    },
}

/// One effect (declared or inferred).
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct EffectRef {
    pub name: String,
    pub definition: Option<Location>,
    /// The `:answer` of the defeffect (null if none).
    pub answer: Option<TypeRef>,
    pub absent: Vec<TypeRef>,
    pub failure: Vec<TypeRef>,
}

/// The effects of a heading — declared (null if there is no `:effects`) and inferred.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct SignatureEffects {
    pub declared: Option<Vec<EffectRef>>,
    pub inferred: Vec<EffectRef>,
    /// Whether the inference could trace everything — false if it fires a call that cannot be traced (a function outside the repo, a deff, a method), and then `inferred` is only what was seen.
    /// So that the editor can draw "no effects" and "cannot fully infer" separately (a field added in version 2).
    pub complete: bool,
}

/// One argument (type is null if `:pre` has no type).
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct SignatureParam {
    pub name: String,
    #[serde(rename = "type")]
    pub type_ref: Option<TypeRef>,
}

/// The kind of definition that has a heading.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum SignatureKind {
    Defk,
    Deff,
}

/// The heading of one defk / deff.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct Signature {
    pub kind: SignatureKind,
    pub name: String,
    pub path: String,
    /// The range of the name.
    pub range: Range,
    pub full_range: Range,
    /// The range of the contract dict `{:pre … :post … :effects … :tags …}` (null if none).
    pub contract_range: Option<Range>,
    pub params: Vec<SignatureParam>,
    /// The type of `:post` (null if none). The Absent wrapper is held separately in `absent`.
    pub answer: Option<TypeRef>,
    /// Whether the answer is Maybe (Absent may escape to the caller).
    pub absent: bool,
    pub raises: Vec<TypeRef>,
    pub effects: SignatureEffects,
    pub tags: BTreeMap<String, String>,
}

/// The shape of a binding.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
pub enum BindingForm {
    #[serde(rename = "<-")]
    Bind,
    #[serde(rename = "val")]
    Val,
    #[serde(rename = "var")]
    Var,
    #[serde(rename = "setv")]
    Setv,
    #[serde(rename = ":=")]
    Assign,
}

/// The word placed before `val` / `var` (ADR-DOE-HY-006: `(lazy val x e)` = evaluated on first use, `(session val x e)` = shared in a defhandler's
/// session).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum BindingModifier {
    Lazy,
    Session,
}

impl BindingModifier {
    /// Reads from the head word (None if it is neither `lazy` nor `session`).
    pub(super) fn of(word: &str) -> Option<BindingModifier> {
        [("lazy", BindingModifier::Lazy), ("session", BindingModifier::Session)]
            .into_iter()
            .find(|(spelled, _)| *spelled == word)
            .map(|(_, modifier)| modifier)
    }
}

/// Where the binding's type was read from.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum BindingOrigin {
    /// The annotation of `(<- x T …)`.
    Annotation,
    /// The value answer of the fired effect.
    Effect,
    /// The answer of the fired defk, or the answer of the called deff.
    Call,
    /// A literal (string, number, bool, None, dict, list).
    Literal,
    /// A call of a type name (builds a record).
    Constructor,
    /// The type of the `(var x …)` that declared the x of `(:= x v)`.
    Var,
    /// Unknown (type is null).
    Unknown,
}

/// One binding.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct Binding {
    pub form: BindingForm,
    /// The word before `(lazy val …)` / `(session var …)` (null if none; the form is val / var).
    pub modifier: Option<BindingModifier>,
    pub name: String,
    pub path: String,
    /// The range of the name.
    pub range: Range,
    /// The whole binding form (including parentheses).
    pub form_range: Range,
    /// The range of the head symbol (`<-`, `val` …).
    pub head_range: Range,
    /// The range of T in `(<- x T …)` (null if none).
    pub annotation_range: Option<Range>,
    /// The range of the bound expression (for `(val x (! e))`, the whole `(! e)`).
    pub value_range: Option<Range>,
    #[serde(rename = "type")]
    pub type_ref: Option<TypeRef>,
    pub origin: BindingOrigin,
    pub absent: bool,
    pub raises: Vec<TypeRef>,
}

/// The headings and bindings of one file (combining replacement and body into one file's reading is done in `file_view.rs`).
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize)]
pub struct FileHeads {
    pub signatures: Vec<Signature>,
    pub bindings: Vec<Binding>,
}

// --- The repo's tables ---------------------------------------------------------------------------

/// A type expression (before resolution — the spelling as written and the name including the module).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
enum TypeSyntax {
    Name { spelled: String, qualified: String },
    Union(Vec<TypeSyntax>),
    Apply(Box<TypeSyntax>, Vec<TypeSyntax>),
    Unknown(String),
}

impl TypeSyntax {
    /// If it is a type name, the name including the module.
    fn qualified(&self) -> Option<&str> {
        match self {
            TypeSyntax::Name { qualified, .. } => Some(qualified),
            _ => None,
        }
    }

    /// For a union, the elements; otherwise just itself.
    fn members(&self) -> Vec<&TypeSyntax> {
        match self {
            TypeSyntax::Union(members) => members.iter().collect(),
            other => vec![other],
        }
    }
}

/// One defeffect.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub(super) struct EffectFacts {
    pub(super) location: Location,
    answer: Option<TypeSyntax>,
    absent: Vec<TypeSyntax>,
    failure: Vec<TypeSyntax>,
}

/// One fired call.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
struct Site {
    /// The call's head (name including the module).
    callee: String,
    /// The F type of `(<- … :absent F)` (replaces Absent with Raise(F)).
    absent_as_raise: Option<TypeSyntax>,
    /// Inside `absent-as` (Absent is received).
    absent_handled: bool,
    /// Inside the program of `on-raise` (Raise is received).
    raise_handled: bool,
    /// The byte range of the call's head symbol (used by DOEFF127 for the violation location — not used for table comparison).
    head: (usize, usize),
}

/// One defk / deff (material for inference).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub(super) struct DefinitionFacts {
    pub(super) kind: SignatureKind,
    /// The position of the name (where the display replacement part jumps to when clicked).
    pub(super) location: Location,
    answer: Option<TypeSyntax>,
    sites: Vec<Site>,
}

/// The result of defk inference.
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
struct Summary {
    effects: BTreeSet<String>,
    absent: bool,
    /// Name including the module -> spelling as written.
    raises: BTreeMap<String, String>,
    /// Fires a call that cannot be traced (a function outside the repo, a deff, a method) (the set of inferred effects may be missing — DOEFF127 does not judge
    /// "declared but not raised").
    opaque: bool,
}

/// A table of types, effects, and defks collected from all of the repo's Hy files, and the defk inference.
#[derive(Debug, Clone, Default)]
pub struct World {
    pub(super) types: HashMap<String, Location>,
    pub(super) effects: HashMap<String, EffectFacts>,
    pub(super) definitions: HashMap<String, DefinitionFacts>,
    summaries: HashMap<String, Summary>,
}

/// The material of the table collected from one file.
#[derive(Default, Clone, Serialize, Deserialize)]
struct FileFacts {
    types: Vec<(String, Location)>,
    effects: Vec<(String, EffectFacts)>,
    definitions: Vec<(String, DefinitionFacts)>,
}

impl FileFacts {
    /// Makes all the position paths (`Location::path`) held by the facts into `path`. The cache is put in a form that has no worktree absolute path, and after reading the current root's
    /// path is attached (the header comment of facts_cache). It destructures by naming all fields, so adding a field that holds a path breaks the compile here.
    fn set_path(&mut self, path: &str) {
        let FileFacts { types, effects, definitions } = self;
        for (_, location) in types {
            location.path = path.to_string();
        }
        for (_, EffectFacts { location, answer: _, absent: _, failure: _ }) in effects {
            location.path = path.to_string();
        }
        for (_, DefinitionFacts { kind: _, location, answer: _, sites: _ }) in definitions {
            location.path = path.to_string();
        }
    }
}

/// One file's facts, and the key of that file's inference material (put together in the cache).
#[derive(Clone, Serialize, Deserialize)]
struct KeyedFacts {
    facts: FileFacts,
    inference_key: u64,
}

impl KeyedFacts {
    /// The inference material's key = the file's path, and only the fields inference reads (type names, effect names with Absent / Raise, definition names with kinds and the list of fired calls).
    /// Positions (the location of a definition, the range of a call's head) do not affect inference, so they are not included — even if lines shift from a body rewrite, the key does not change.
    fn of(rel: &str, facts: FileFacts) -> KeyedFacts {
        use std::hash::{Hash, Hasher};
        let mut h = std::collections::hash_map::DefaultHasher::new();
        rel.hash(&mut h);
        for (name, _) in &facts.types {
            name.hash(&mut h);
        }
        for (name, effect) in &facts.effects {
            name.hash(&mut h);
            format!("{:?}{:?}", effect.absent, effect.failure).hash(&mut h);
        }
        for (name, definition) in &facts.definitions {
            name.hash(&mut h);
            (definition.kind == SignatureKind::Defk).hash(&mut h);
            for site in &definition.sites {
                site.callee.hash(&mut h);
                format!("{:?}", site.absent_as_raise).hash(&mut h);
                site.absent_handled.hash(&mut h);
                site.raise_handled.hash(&mut h);
            }
        }
        KeyedFacts { facts, inference_key: h.finish() }
    }
}

/// The facts of one file on disk (the position path is empty — the cache holds no worktree absolute path, and the current root's path is attached after reading). No file lacks `(def`.
fn disk_facts(rel: &str, path: &Path) -> Option<KeyedFacts> {
    let source = std::fs::read_to_string(path).ok()?;
    source_facts(rel, &source)
}

/// Builds the facts of one file from the contents `source` (the position path is empty — the current root's path is attached after reading). A file that does not spell `(def` has no facts.
fn source_facts(rel: &str, source: &str) -> Option<KeyedFacts> {
    source.contains("(def").then(|| {
        let mut keyed = KeyedFacts::of(rel, file_facts(Path::new(""), rel, source));
        keyed.facts.set_path("");
        keyed
    })
}

/// One row of a chunk's table = (the path from the root when the fact was made, the fact). A fact is also decided by path (module name, inference key), so when the same blob is at a different path the row is not used.
type BlobRow = (String, Option<KeyedFacts>);

/// Returns the facts of `files` (path from the root, location), looking up unchanged files by git blob id without reading contents (`ChunkedFacts`).
/// A single-file run used to open the reachable module files one by one and take hashes (hundreds of files in a sim test; 9 s of CPU on a cold worktree).
/// A file that cannot be looked up (not in the table, out of step with the index, or a different path) is built by reading its contents. When `memory` is None, the store keyed by the contents' hash is used.
fn named_facts(root: &Path, memory: Option<&ChunkedFacts<BlobRow>>, files: &[(String, std::path::PathBuf)]) -> Vec<KeyedFacts> {
    use rayon::prelude::*;
    let Some(memory) = memory else {
        return super::facts_cache::per_file_compact_named_bound(root, "signature-facts", files, disk_facts, |keyed, rel, _path| {
            keyed.facts.set_path(&root.join(rel).to_string_lossy())
        });
    };
    let found: Vec<Option<KeyedFacts>> = files
        .par_iter()
        .map(|(rel, path)| {
            let (learn, meta) = match memory.lookup_path(rel, path) {
                Some((Lookup::Known((made_at, value)), _)) if made_at == *rel => return value,
                Some((Lookup::Unknown(oid), meta)) => (Some(oid), meta),
                _ => (None, None),
            };
            let source = std::fs::read_to_string(path).ok()?;
            let value = source_facts(rel, &source);
            if let Some(oid) = learn {
                memory.learn_read(rel, oid, path, meta.as_ref(), source.as_bytes(), (rel.clone(), value.clone()));
            }
            value
        })
        .collect();
    files
        .iter()
        .zip(found)
        .filter_map(|((rel, _), value)| {
            value.map(|mut keyed| {
                keyed.facts.set_path(&root.join(rel).to_string_lossy());
                keyed
            })
        })
        .collect()
}

impl TypeSyntax {
    /// All the names including the module in a type expression (also inside unions and applications).
    fn collect_qualified<'a>(&'a self, out: &mut Vec<&'a str>) {
        match self {
            TypeSyntax::Name { qualified, .. } => out.push(qualified),
            TypeSyntax::Union(members) => members.iter().for_each(|member| member.collect_qualified(out)),
            TypeSyntax::Apply(head, args) => {
                head.collect_qualified(out);
                args.iter().for_each(|arg| arg.collect_qualified(out));
            }
            TypeSyntax::Unknown(_) => {}
        }
    }
}

impl FileFacts {
    /// Among the modules of the names this file's facts name (heads of fired calls, answer types, effect answers with Absent and failure types), those that exist in the repo — material for the modules the table looks up from this file onward
    /// (so that a single-file run reads until the reachable modules are closed). It destructures by naming all fields, so adding a field that holds a name breaks the compile here.
    fn referenced_modules(&self, known: &super::hy_files::ModuleFiles) -> BTreeSet<String> {
        let FileFacts { types: _, effects, definitions } = self;
        let mut names: Vec<&str> = Vec::new();
        for (_, EffectFacts { location: _, answer, absent, failure }) in effects {
            answer.iter().chain(absent).chain(failure).for_each(|syntax| syntax.collect_qualified(&mut names));
        }
        for (_, DefinitionFacts { kind: _, location: _, answer, sites }) in definitions {
            answer.iter().for_each(|syntax| syntax.collect_qualified(&mut names));
            for Site { callee, absent_as_raise, absent_handled: _, raise_handled: _, head: _ } in sites {
                names.push(callee);
                absent_as_raise.iter().for_each(|syntax| syntax.collect_qualified(&mut names));
            }
        }
        let mut modules = BTreeSet::new();
        for name in names {
            modules.extend(name.rmatch_indices('.').map(|(at, _)| &name[..at]).filter(|module| known.contains(module)).map(str::to_string));
        }
        modules
    }
}

/// Heads that have a definition as a type name.
const TYPE_HEADS: &[&str] = &[
    "defrecord",
    "defwire",
    "defenum",
    "defclass",
    "defeffect",
    "deftype",
];

impl World {
    /// Builds the table from all of the repo's Hy files. `overlay` = (path from the root, contents) — reads pre-save contents in place of that file.
    pub fn build(root: &Path, overlay: Option<(&str, &str)>) -> World {
        let files: Vec<(String, std::path::PathBuf)> =
            super::hy_files::collect(root)
                .into_iter()
                .filter_map(|path| super::paths::relative_path(root, &path).map(|rel| (rel, path)))
                .collect();
        // The file written (overlay) is built from the passed contents rather than disk, so it is excluded from the cache's population.
        let on_disk: Vec<(String, std::path::PathBuf)> =
            files.into_iter().filter(|(rel, _)| overlay.is_none_or(|(o, _)| o != rel)).collect();
        // A file's facts are decided only by that file's contents and its path from the root (root is used only for the spelling of position paths), so only changed files are
        // rebuilt. Reading over 5MB of facts as JSON took about 0.05 s each time, so they are put in a binary form that does not hold field names.
        // The position path is the worktree's absolute path, so the value put has the path emptied, and the current root's path is attached after reading — so that
        // facts of files with the same contents are shared across worktrees.
        let mut facts: Vec<KeyedFacts> = crate::timing::timed("effect-world.facts", || {
            super::facts_cache::per_file_compact_bound(root, "signature-facts", &on_disk, disk_facts, |keyed, rel, _path| {
                keyed.facts.set_path(&root.join(rel).to_string_lossy())
            })
        });
        if let Some((rel, source)) = overlay {
            facts.push(KeyedFacts::of(rel, file_facts(root, rel, source)));
        }
        // Fingerprint of the inference material — the sum of per-file keys (independent of file order).
        let key = facts.iter().fold(0u64, |acc, file| acc.wrapping_add(file.inference_key));
        let mut world = World::default();
        for KeyedFacts { facts: file, .. } in facts {
            world.types.extend(file.types);
            world.effects.extend(file.effects);
            world.definitions.extend(file.definitions);
        }
        // The inference fixed point is decided only by the material (facts excluding positions), so if the material's fingerprint is the same as the previous run, the previous answer is used. Even for the single run right after a write,
        // if the body of the written file does not change the inference material (lines only shifted, or the list of calls is the same) it is not run (the proposal of #1033).
        world.summaries = crate::timing::timed("effect-world.infer", || {
            super::facts_cache::keyed_value(root, "signature-summaries", key, || {
                world.infer();
                std::mem::take(&mut world.summaries)
            })
        });
        world
    }

    /// The table for a single-file run (the hook right after a write, the editor) — built not from all of the repo but only from the written file (`source` = pre-save contents) and the module files reachable from it.
    ///
    /// The table is looked up by name including the module (`types`, `effects`, `definitions`, `summaries` all use only `get` and `contains_key` — no scanning), and the names looked up are the written file's
    /// symbols resolved, or the names the reached files' facts hold (heads of fired calls, type names). So, starting from the modules the written file names and adding the modules the facts name until
    /// closed, the table gives the same answer as a table built from the whole repo within the range reachable from the written file (inference too is decided only by the defks reached by calls).
    /// The facts of reachable modules are looked up from the same store as a whole run by the contents' hash (`per_file_compact_named_bound` — the table of the whole repo is not read).
    pub fn build_for_file(root: &Path, rel: &str, source: &str) -> World {
        let known = super::hy_files::by_module(root);
        let own = KeyedFacts::of(rel, file_facts(root, rel, source));
        let mut wanted = super::named_modules::of_file(&known, rel, source);
        wanted.extend(own.facts.referenced_modules(&known));
        let mut facts = vec![own];
        let mut loaded: BTreeSet<String> = BTreeSet::new();
        let memory = ChunkedFacts::<BlobRow>::open(root, "signature-facts-blob", |rel| super::hy_files::walks_to(&super::hy_files::HY_WALK, rel));
        crate::timing::timed("effect-world.facts", || loop {
            let next: BTreeSet<String> = wanted.difference(&loaded).cloned().collect();
            if next.is_empty() {
                break;
            }
            loaded.extend(next.iter().cloned());
            let files = super::named_modules::files_of(&known, &next, Some(rel));
            let batch = named_facts(root, memory.as_ref(), &files);
            for keyed in &batch {
                wanted.extend(keyed.facts.referenced_modules(&known));
            }
            facts.extend(batch);
        });
        if let Some(memory) = &memory {
            let (chunks, missed) = memory.reads();
            crate::timing::count("effect-world.facts", "chunks", chunks);
            crate::timing::count("effect-world.facts", "missed", missed);
            crate::timing::timed("effect-world.remember", || memory.finish());
        }
        let mut world = World::default();
        for KeyedFacts { facts: file, .. } in facts {
            world.types.extend(file.types);
            world.effects.extend(file.effects);
            world.definitions.extend(file.definitions);
        }
        // A small table of only the reachable defks, so the fixed point is not put in the cache but run every time (the whole run's `signature-summaries` is looked up by the fingerprint of the whole repo — it would be a different key per file).
        crate::timing::timed("effect-world.infer", || world.infer());
        world
    }

    /// Runs the defk inference to a fixed point (a cycle of calls falls into the same set — the number of iterations is bounded by the number of defks).
    fn infer(&mut self) {
        let defks: Vec<String> = self
            .definitions
            .iter()
            .filter(|(_, d)| d.kind == SignatureKind::Defk)
            .map(|(q, _)| q.clone())
            .collect();
        for _ in 0..=defks.len().min(64) {
            let mut changed = false;
            for name in &defks {
                let next = self.summary_of_sites(&self.definitions[name].sites);
                if self.summaries.get(name) != Some(&next) {
                    self.summaries.insert(name.clone(), next);
                    changed = true;
                }
            }
            if !changed {
                break;
            }
        }
    }

    /// Whether the fired call's head can be regarded as an effect outside the repo (the head is capitalized and is neither a repo type nor a definition — a capitalized call that is fired builds an effect).
    /// So that the inference can also pick up a declared `:effects [Delay]` (if it could not, it would look like a "declaration only" mismatch).
    pub(super) fn is_foreign_effect(&self, callee: &str) -> bool {
        last_segment(callee).starts_with(|c: char| c.is_ascii_uppercase())
            && !self.types.contains_key(callee)
            && !self.definitions.contains_key(callee)
    }

    /// Collects effects, Absent, and Raise from the list of fired calls.
    fn summary_of_sites(&self, sites: &[Site]) -> Summary {
        let mut summary = Summary::default();
        for site in sites {
            let outcome = self.site_outcome(site);
            summary.effects.extend(outcome.effects);
            summary.absent |= outcome.absent;
            summary.opaque |= outcome.opaque;
            summary.raises.extend(outcome.raises);
        }
        summary
    }

    /// The outcome of one fired call (empty if the call's head is neither an effect nor a defk).
    fn site_outcome(&self, site: &Site) -> Summary {
        let mut out = Summary::default();
        let (absent, raises) = if let Some(effect) = self.effects.get(&site.callee) {
            out.effects.insert(site.callee.clone());
            let raises: BTreeMap<String, String> = effect
                .failure
                .iter()
                .filter_map(|t| t.qualified().map(|q| (q.to_string(), spelled_of(t))))
                .collect();
            (!effect.absent.is_empty(), raises)
        } else if let Some(summary) = self.summaries.get(&site.callee) {
            out.effects.extend(summary.effects.iter().cloned());
            out.opaque = summary.opaque;
            (summary.absent, summary.raises.clone())
        } else if self.is_foreign_effect(&site.callee) {
            // An effect outside the repo (doeff core's Delay, Ask, etc.) — it is fired, so it counts as an effect. How the answer is split is unknown
            out.effects.insert(site.callee.clone());
            (false, BTreeMap::new())
        } else {
            // Fires a call that cannot be traced (a function outside the repo, a deff, a method) — the effects beyond it are unknown
            out.opaque = true;
            return out;
        };
        if !site.raise_handled {
            out.raises.extend(raises);
        }
        if absent && !site.absent_handled {
            match &site.absent_as_raise {
                Some(raise) if !site.raise_handled => {
                    if let Some(q) = raise.qualified() {
                        out.raises.insert(q.to_string(), spelled_of(raise));
                    }
                }
                Some(_) => {}
                None => out.absent = true,
            }
        }
        out
    }

    /// The answer type of a defk / deff (the `:post` type or the name's annotation; None if not written).
    pub(super) fn answer_of(&self, qualified: &str) -> Option<TypeRef> {
        self.definitions.get(qualified)?.answer.as_ref().map(|a| self.resolve(a))
    }

    /// Turns the unresolved type into the contract's type (attaches the position if it matches a repo definition).
    fn resolve(&self, syntax: &TypeSyntax) -> TypeRef {
        match syntax {
            TypeSyntax::Name { spelled, qualified } => TypeRef::Name {
                name: spelled.clone(),
                definition: self.types.get(qualified).cloned(),
            },
            TypeSyntax::Union(members) => TypeRef::Union {
                members: members.iter().map(|m| self.resolve(m)).collect(),
            },
            TypeSyntax::Apply(head, args) => TypeRef::Apply {
                head: Box::new(self.resolve(head)),
                args: args.iter().map(|a| self.resolve(a)).collect(),
            },
            TypeSyntax::Unknown(text) => TypeRef::Unknown { text: text.clone() },
        }
    }

    /// The type of a name including the module (the spelling is the last segment).
    fn resolve_qualified(&self, qualified: &str, spelled: &str) -> TypeRef {
        TypeRef::Name {
            name: spelled.to_string(),
            definition: self.types.get(qualified).cloned(),
        }
    }

    /// A reference to an effect (position and answer are null if not in the table).
    fn effect_ref(&self, qualified: &str, spelled: &str) -> EffectRef {
        match self.effects.get(qualified) {
            Some(effect) => EffectRef {
                name: spelled.to_string(),
                definition: Some(effect.location.clone()),
                answer: effect.answer.as_ref().map(|a| self.resolve(a)),
                absent: effect.absent.iter().map(|t| self.resolve(t)).collect(),
                failure: effect.failure.iter().map(|t| self.resolve(t)).collect(),
            },
            None => EffectRef {
                name: spelled.to_string(),
                definition: None,
                answer: None,
                absent: Vec::new(),
                failure: Vec::new(),
            },
        }
    }

    /// The effect's value answer (the elements of `:answer` minus `:absent` and `:failure` — a name if one, a union if several).
    pub(super) fn effect_value(&self, effect: &EffectFacts) -> Option<TypeRef> {
        let answer = effect.answer.as_ref()?;
        let excluded: BTreeSet<&str> = effect
            .absent
            .iter()
            .chain(&effect.failure)
            .filter_map(|t| t.qualified())
            .collect();
        let kept: Vec<&TypeSyntax> = answer
            .members()
            .into_iter()
            .filter(|m| m.qualified().is_none_or(|q| !excluded.contains(q)))
            .collect();
        match kept.as_slice() {
            [] => None,
            [one] => Some(self.resolve(one)),
            many => Some(TypeRef::Union {
                members: many.iter().map(|m| self.resolve(m)).collect(),
            }),
        }
    }
}

/// The type's spelling (the spelling if a name; empty otherwise).
fn spelled_of(syntax: &TypeSyntax) -> String {
    match syntax {
        TypeSyntax::Name { spelled, .. } => spelled.clone(),
        _ => String::new(),
    }
}

/// The last segment of a name including the module.
fn last_segment(qualified: &str) -> &str {
    qualified.rsplit('.').next().unwrap_or(qualified)
}

// --- Reading ---------------------------------------------------------------------------------

/// The tool that reads one file (source, line table, how names are resolved).
pub(super) struct FileReader<'a> {
    pub(super) hy: Hy<'a>,
    pub(super) lines: LineIndex<'a>,
    pub(super) scope: Scope<'a>,
    pub(super) path: String,
}

impl<'a> FileReader<'a> {
    fn location(&self, form: &Form) -> Location {
        Location {
            path: self.path.clone(),
            range: self.lines.range(form.span.start, form.span.end),
        }
    }

    fn range(&self, form: &Form) -> Range {
        self.lines.range(form.span.start, form.span.end)
    }

    /// Reads a type expression (`(| a b)` is a union, `(of H a …)` is an application, a symbol is a name, anything else is an unreadable expression).
    fn type_syntax(&self, form: &Form) -> TypeSyntax {
        match &form.node {
            Node::Symbol => {
                let spelled = self.hy.text(form);
                TypeSyntax::Name {
                    spelled: spelled.to_string(),
                    qualified: self.scope.qualify(spelled),
                }
            }
            Node::Seq {
                delim: Delim::Paren,
                ..
            } => {
                let items = live(form).unwrap_or_default();
                match items.first().and_then(|h| self.hy.symbol(h)) {
                    Some("|") => {
                        let mut members = Vec::new();
                        for item in &items[1..] {
                            match self.type_syntax(item) {
                                TypeSyntax::Union(inner) => members.extend(inner),
                                other => members.push(other),
                            }
                        }
                        TypeSyntax::Union(members)
                    }
                    Some("of") if items.len() >= 2 => TypeSyntax::Apply(
                        Box::new(self.type_syntax(items[1])),
                        items[2..].iter().map(|a| self.type_syntax(a)).collect(),
                    ),
                    _ => TypeSyntax::Unknown(self.hy.text(form).to_string()),
                }
            }
            _ => TypeSyntax::Unknown(self.hy.text(form).to_string()),
        }
    }

    /// The value of a dict key.
    fn dict_value<'f>(&self, dict: &'f Form, key: &str) -> Option<&'f Form> {
        let Node::Seq {
            delim: Delim::Brace,
            items,
        } = &dict.node
        else {
            return None;
        };
        let items: Vec<&Form> = items
            .iter()
            .filter(|i| !matches!(i.node, Node::Discarded))
            .collect();
        items.chunks(2).find_map(|pair| match pair {
            [k, v] if matches!(k.node, Node::Keyword) && self.hy.text(k) == key => Some(*v),
            _ => None,
        })
    }

    /// Makes `[(: name type) …]` into pairs of name -> type form (conditions that are not `(: …)` are skipped).
    fn annotations<'f>(&self, list: Option<&'f Form>) -> Vec<(&'a str, &'f Form)> {
        let Some(items) = list.and_then(|l| l.bracket_items()) else {
            return Vec::new();
        };
        items
            .iter()
            .filter_map(|item| {
                let parts = live(item)?;
                match parts.as_slice() {
                    [head, name, ty] if self.hy.symbol(head) == Some(":") => {
                        Some((self.hy.text(name), *ty))
                    }
                    _ => None,
                }
            })
            .collect()
    }

    /// Reads the list of type names `[A B]`.
    fn type_list(&self, form: Option<&Form>) -> Vec<TypeSyntax> {
        form.and_then(|f| f.bracket_items())
            .map(|items| {
                items
                    .iter()
                    .filter(|i| !matches!(i.node, Node::Discarded))
                    .map(|i| self.type_syntax(i))
                    .collect()
            })
            .unwrap_or_default()
    }
}

/// The forms of top-level definitions (also inside `do` and `eval-and-compile`).
pub(super) fn top_definitions<'f>(hy: &Hy, forms: &'f [Form]) -> Vec<&'f Form> {
    let mut out = Vec::new();
    let mut pending: Vec<&Form> = forms.iter().rev().collect();
    while let Some(form) = pending.pop() {
        let Some(items) = live(form) else { continue };
        match items.first().and_then(|h| hy.symbol(h)) {
            Some("do" | "eval-and-compile" | "eval-when-compile") => {
                pending.extend(items[1..].iter().rev().copied())
            }
            Some(_) => out.push(form),
            None => {}
        }
    }
    out
}

/// The form of the definition's name (the target if it has a `#^ T name` annotation), and the form of the annotation's type.
fn definition_name(form: &Form) -> (Option<&Form>, Option<&Form>) {
    match &form.node {
        Node::Annotated { annotation, target } => (target.as_deref(), annotation.as_deref()),
        _ => (Some(form), None),
    }
}

/// The shape of a defk / deff as read.
pub(super) struct DefinitionShape<'f> {
    pub(super) kind: SignatureKind,
    pub(super) name: &'f Form,
    name_annotation: Option<&'f Form>,
    pub(super) params: Option<&'f Form>,
    pub(super) contract: Option<&'f Form>,
    pub(super) body: Vec<&'f Form>,
}

/// Reads `(defk name [params] {contract}? "doc"? body…)` (the contract may be before or after the doc).
pub(super) fn definition_shape<'f>(hy: &Hy, form: &'f Form) -> Option<DefinitionShape<'f>> {
    let items = live(form)?;
    let kind = match items.first().and_then(|h| hy.symbol(h))? {
        "defk" => SignatureKind::Defk,
        "deff" => SignatureKind::Deff,
        _ => return None,
    };
    let (name, name_annotation) = definition_name(items.get(1)?);
    let name = name.filter(|n| matches!(n.node, Node::Symbol))?;
    let params = items
        .get(2)
        .copied()
        .filter(|p| p.bracket_items().is_some());
    let rest = &items[3.min(items.len())..];
    let contract = match rest {
        [first, ..] if first.is_brace() => Some(*first),
        [doc, second, ..] if matches!(doc.node, Node::Str { .. }) && second.is_brace() => {
            Some(*second)
        }
        _ => None,
    };
    let body = rest
        .iter()
        .copied()
        .filter(|f| contract.is_none_or(|c| !std::ptr::eq(*f, c)))
        .collect();
    Some(DefinitionShape {
        kind,
        name,
        name_annotation,
        params,
        contract,
        body,
    })
}

/// Collects the material of the table from one file.
fn file_facts(root: &Path, rel: &str, source: &str) -> FileFacts {
    let forms = Reader::new(source, 0, source.len()).read_all();
    let module = module_of(rel);
    let bindings = form_bindings(&forms, source, &module);
    let reader = FileReader {
        hy: Hy { src: source },
        lines: LineIndex::new(source),
        scope: Scope {
            module: &module,
            bindings: &bindings,
        },
        path: root.join(rel).to_string_lossy().into_owned(),
    };
    let mut out = FileFacts::default();
    for form in top_definitions(&reader.hy, &forms) {
        let Some(items) = live(form) else { continue };
        let head = items
            .first()
            .and_then(|h| reader.hy.symbol(h))
            .unwrap_or("");
        let named = items
            .get(1)
            .and_then(|n| definition_name(n).0)
            .filter(|n| matches!(n.node, Node::Symbol));
        if TYPE_HEADS.contains(&head)
            || (head == "val"
                && named.is_some_and(|n| {
                    reader
                        .hy
                        .text(n)
                        .starts_with(|c: char| c.is_ascii_uppercase())
                }))
        {
            if let Some(name) = named {
                let qualified = reader.scope.qualify(reader.hy.text(name));
                out.types.push((qualified.clone(), reader.location(name)));
                if head == "defeffect" {
                    let contract = items[2..].iter().take(2).find(|f| f.is_brace()).copied();
                    let effect = EffectFacts {
                        location: reader.location(name),
                        answer: contract
                            .and_then(|c| reader.dict_value(c, ":answer"))
                            .map(|a| reader.type_syntax(a)),
                        absent: reader
                            .type_list(contract.and_then(|c| reader.dict_value(c, ":absent"))),
                        failure: reader
                            .type_list(contract.and_then(|c| reader.dict_value(c, ":failure"))),
                    };
                    out.effects.push((qualified, effect));
                }
            }
            continue;
        }
        if let Some(shape) = definition_shape(&reader.hy, form) {
            let qualified = reader.scope.qualify(reader.hy.text(shape.name));
            let answer = answer_syntax(&reader, &shape);
            let mut sites = Vec::new();
            for item in &shape.body {
                collect_sites(&reader, item, Flags::default(), &mut sites);
            }
            out.definitions.push((
                qualified,
                DefinitionFacts {
                    kind: shape.kind,
                    location: reader.location(shape.name),
                    answer,
                    sites,
                },
            ));
        }
    }
    out
}

/// The answer type (the `(: % T)` of `:post`, or the name's annotation `#^ T` if none).
fn answer_syntax(reader: &FileReader, shape: &DefinitionShape) -> Option<TypeSyntax> {
    let post = shape.contract.and_then(|c| reader.dict_value(c, ":post"));
    reader
        .annotations(post)
        .into_iter()
        .find(|(name, _)| *name == "%")
        .map(|(_, ty)| reader.type_syntax(ty))
        .or_else(|| shape.name_annotation.map(|a| reader.type_syntax(a)))
}

/// How a fired call is received (the outer `absent-as` and `on-raise`).
#[derive(Debug, Clone, Copy, Default)]
struct Flags {
    absent_handled: bool,
    raise_handled: bool,
}

/// What a `(<- …)` read as (name, annotation, expression, `:absent F`).
pub(super) struct BindShape<'f> {
    pub(super) name: Option<&'f Form>,
    pub(super) annotation: Option<&'f Form>,
    pub(super) value: &'f Form,
    pub(super) absent_as_raise: Option<&'f Form>,
}

/// One pair of a declaration's name and value. `bang` is the `!` before the value (`(val x ! (f a))` — Hy's reader reads `!(f a)` too as 2 elements).
pub(super) struct BindingPair<'f> {
    pub(super) name: &'f Form,
    pub(super) bang: Option<&'f Form>,
    /// The value (None if missing, as in `(val x)`).
    pub(super) value: Option<&'f Form>,
}

/// Reads the sequence after the head of a declaration (`val`, `var`, `lazy val` … and `:=`) as pairs of name and value. The 2-element pair of `! expr` before a value is folded into
/// one as a fired value (ADR-DOE-HY-006 §3 — limited to the value part of declarations and `:=`. setv is not folded).
pub(super) fn binding_pairs<'f>(hy: &Hy, items: &[&'f Form]) -> Vec<BindingPair<'f>> {
    let mut pairs = Vec::new();
    let mut i = 0;
    while i < items.len() {
        let name = items[i];
        match (items.get(i + 1), items.get(i + 2)) {
            (Some(bang), Some(value)) if hy.symbol(bang) == Some("!") => {
                pairs.push(BindingPair { name, bang: Some(bang), value: Some(value) });
                i += 3;
            }
            (value, _) => {
                pairs.push(BindingPair { name, bang: None, value: value.copied() });
                i += 2;
            }
        }
    }
    pairs
}

/// `(<- x e)` / `(<- x T e)` / `(<- e)`, with `:absent F` allowed at the end.
pub(super) fn bind_shape<'f>(hy: &Hy, items: &[&'f Form]) -> Option<BindShape<'f>> {
    let mut args = &items[1..];
    let mut absent_as_raise = None;
    if args.len() >= 3
        && matches!(args[args.len() - 2].node, Node::Keyword)
        && hy.text(args[args.len() - 2]) == ":absent"
    {
        absent_as_raise = Some(args[args.len() - 1]);
        args = &args[..args.len() - 2];
    }
    match args {
        [value] => Some(BindShape {
            name: None,
            annotation: None,
            value,
            absent_as_raise,
        }),
        [name, value] => Some(BindShape {
            name: Some(name),
            annotation: None,
            value,
            absent_as_raise,
        }),
        [name, annotation, value] => Some(BindShape {
            name: Some(name),
            annotation: Some(annotation),
            value,
            absent_as_raise,
        }),
        _ => None,
    }
}

/// The byte range of a call's head symbol (the position of the expression's head if it is not a symbol).
fn call_head_span(form: &Form) -> (usize, usize) {
    live(form)
        .and_then(|items| items.first().map(|h| (h.span.start, h.span.end)))
        .unwrap_or((form.span.start, form.span.start))
}

/// The spelling of a call's head (the f of `(f …)`).
fn call_head<'a>(hy: &Hy<'a>, form: &Form) -> Option<&'a str> {
    live(form)?.first().and_then(|h| hy.symbol(h))
}

/// The type F of `:absent F` (the head of `(Conflict "…")`, or the symbol itself).
fn raise_type(reader: &FileReader, form: &Form) -> TypeSyntax {
    match call_head(&reader.hy, form) {
        Some(head) => TypeSyntax::Name {
            spelled: head.to_string(),
            qualified: reader.scope.qualify(head),
        },
        None => reader.type_syntax(form),
    }
}

/// Collects fired calls (quote is not read).
fn collect_sites(reader: &FileReader, form: &Form, flags: Flags, out: &mut Vec<Site>) {
    let children: Vec<&Form> = match &form.node {
        Node::Seq { items, .. } => items
            .iter()
            .filter(|i| !matches!(i.node, Node::Discarded))
            .collect(),
        Node::Prefixed {
            prefix,
            inner: Some(inner),
        } => {
            if matches!(
                prefix,
                doeff_indexer::hy_index::reader::Prefix::Quote
                    | doeff_indexer::hy_index::reader::Prefix::Quasiquote
            ) {
                return;
            }
            vec![inner.as_ref()]
        }
        Node::Annotated {
            annotation: _,
            target: Some(target),
        } => vec![target.as_ref()],
        _ => return,
    };
    let head = match &form.node {
        Node::Seq {
            delim: Delim::Paren,
            ..
        } => children.first().and_then(|h| reader.hy.symbol(h)),
        _ => None,
    };
    match head {
        Some("quote" | "quasiquote") => {}
        Some("<-") => {
            if let Some(bind) = bind_shape(&reader.hy, &children) {
                push_site(
                    reader,
                    bind.value,
                    bind.absent_as_raise.map(|f| raise_type(reader, f)),
                    flags,
                    out,
                );
                collect_sites(reader, bind.value, flags, out);
            }
        }
        Some("!") => {
            if let Some(value) = children.get(1) {
                push_site(reader, value, None, flags, out);
                collect_sites(reader, value, flags, out);
            }
        }
        Some("absent-as") => {
            for child in &children[1..] {
                collect_sites(
                    reader,
                    child,
                    Flags {
                        absent_handled: true,
                        ..flags
                    },
                    out,
                );
            }
        }
        Some("on-raise") => {
            if let Some(program) = children.get(1) {
                collect_sites(
                    reader,
                    program,
                    Flags {
                        raise_handled: true,
                        ..flags
                    },
                    out,
                );
            }
            for child in children.iter().skip(2) {
                collect_sites(reader, child, flags, out);
            }
        }
        _ => {
            for child in children {
                collect_sites(reader, child, flags, out);
            }
        }
    }
}

/// Records the head of a fired expression as a call.
fn push_site(
    reader: &FileReader,
    value: &Form,
    absent_as_raise: Option<TypeSyntax>,
    flags: Flags,
    out: &mut Vec<Site>,
) {
    match call_head(&reader.hy, value) {
        // A form that puts a branch at the firing position (`(<- x (if c (A …) (B …)))`) — what is fired is the branch's expression, so each branch is recorded as a call
        Some("if" | "when" | "unless" | "cond" | "do" | "match" | "let") => {
            let items = live(value).unwrap_or_default();
            let branches = match items.first().and_then(|h| reader.hy.symbol(h)) {
                Some("if" | "when" | "unless" | "let") => items.get(2..).unwrap_or_default(),
                Some("match") => items.get(2..).unwrap_or_default(),
                _ => items.get(1..).unwrap_or_default(),
            };
            for branch in branches {
                if branch.paren_items().is_some() {
                    push_site(reader, branch, absent_as_raise.clone(), flags, out);
                }
            }
        }
        Some(head) => out.push(Site {
            callee: reader.scope.qualify(head),
            absent_as_raise,
            absent_handled: flags.absent_handled,
            raise_handled: flags.raise_handled,
            head: call_head_span(value),
        }),
        None => {}
    }
}

// --- DOEFF127: mismatch between the :effects declaration and inference ------------------------------------------------

/// One DOEFF127 mismatch (only defks that declare `:effects` — the declaration is optional (#800), so defks without a declaration are out of scope).
/// Inference uses the same `World` reading (one place) as the heading. Ranges are bytes.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum EffectMismatch {
    /// An effect that inference says is raised but is not in `:effects`. Location = the head of the fired call that leads to that effect (the first one in the body).
    /// via = the spelling of the called definition when that call goes through a defk (None if the effect is fired directly).
    Undeclared {
        definition: String,
        effect: String,
        via: Option<String>,
        start: usize,
        end: usize,
    },
    /// An effect that is in `:effects` but that inference says is not raised. Location = that name inside `:effects`.
    Unused {
        definition: String,
        effect: String,
        start: usize,
        end: usize,
    },
}

impl EffectMismatch {
    /// The byte range of the violation's location.
    pub fn span(&self) -> (usize, usize) {
        match self {
            EffectMismatch::Undeclared { start, end, .. }
            | EffectMismatch::Unused { start, end, .. } => (*start, *end),
        }
    }

    /// The definition's spelling.
    pub fn definition(&self) -> &str {
        match self {
            EffectMismatch::Undeclared { definition, .. }
            | EffectMismatch::Unused { definition, .. } => definition,
        }
    }

    /// The effect's spelling (the last segment with the module stripped).
    pub fn effect(&self) -> &str {
        match self {
            EffectMismatch::Undeclared { effect, .. } | EffectMismatch::Unused { effect, .. } => {
                effect
            }
        }
    }
}

/// Judges the mismatch between a defk's `:effects` declaration and inference for one file (the table is the one built by `World::build` — in a single-file run
/// the one with this file's same contents as the overlay).
pub fn effect_mismatches(world: &World, rel: &str, source: &str) -> Vec<EffectMismatch> {
    let forms = Reader::new(source, 0, source.len()).read_all();
    let module = module_of(rel);
    let bindings = form_bindings(&forms, source, &module);
    let reader = FileReader {
        hy: Hy { src: source },
        lines: LineIndex::new(source),
        scope: Scope {
            module: &module,
            bindings: &bindings,
        },
        path: rel.to_string(),
    };
    let mut out = Vec::new();
    for form in top_definitions(&reader.hy, &forms) {
        let Some(shape) = definition_shape(&reader.hy, form) else {
            continue;
        };
        if shape.kind != SignatureKind::Defk {
            continue;
        }
        let Some(list) = shape
            .contract
            .and_then(|c| reader.dict_value(c, ":effects"))
            .and_then(|l| l.bracket_items())
        else {
            continue;
        };
        let definition = reader.hy.text(shape.name).to_string();
        let declared: Vec<(String, &Form)> = list
            .iter()
            .filter(|i| matches!(i.node, Node::Symbol))
            .map(|i| (reader.scope.qualify(reader.hy.text(i)), i))
            .collect();
        let (inferred, opaque) = inferred_sites(world, &reader, &shape);
        for (effect, site) in &inferred {
            if declared.iter().any(|(q, _)| q == effect) {
                continue;
            }
            let via = (site.callee != *effect).then(|| {
                source
                    .get(site.head.0..site.head.1)
                    .unwrap_or("")
                    .to_string()
            });
            out.push(EffectMismatch::Undeclared {
                definition: definition.clone(),
                effect: last_segment(effect).to_string(),
                via,
                start: site.head.0,
                end: site.head.1,
            });
        }
        // If a call that cannot be traced is fired, the inferred set may be missing, so "declared but not raised" is not judged
        for (qualified, symbol) in declared.iter().filter(|_| !opaque) {
            if !inferred.iter().any(|(e, _)| e == qualified) {
                out.push(EffectMismatch::Unused {
                    definition: definition.clone(),
                    effect: reader.hy.text(symbol).to_string(),
                    start: symbol.span.start,
                    end: symbol.span.end,
                });
            }
        }
    }
    out
}

/// The effects inferred from one defk's body, and the first fired call leading to each (in body order), and whether a call that cannot be traced is fired.
/// The reading used by DOEFF127.
fn inferred_sites(world: &World, reader: &FileReader, shape: &DefinitionShape) -> (Vec<(String, Site)>, bool) {
    let mut sites = Vec::new();
    for item in &shape.body {
        collect_sites(reader, item, Flags::default(), &mut sites);
    }
    let mut inferred: Vec<(String, Site)> = Vec::new();
    let mut opaque = false;
    for site in sites {
        let outcome = world.site_outcome(&site);
        opaque |= outcome.opaque;
        for effect in outcome.effects {
            if !inferred.iter().any(|(e, _)| *e == effect) {
                inferred.push((effect, site.clone()));
            }
        }
    }
    (inferred, opaque)
}

// --- DOEFF130: a translation handler does not emit a business intent ------------------------------------------

/// One DOEFF130 breach — one business intent emitted by a translation-layer handler, and the first fired call leading to it. Ranges are bytes.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TranslationIntent {
    /// The handler's spelling (the name of a defhandler, or the name of a function that takes `[effect k]`).
    pub handler: String,
    /// The name of the intent including the module.
    pub qualified: String,
    /// The spelling of the defks passed through from the fired call to the intent (in call order — empty if the intent is fired directly).
    pub chain: Vec<String>,
    pub start: usize,
    pub end: usize,
}

impl TranslationIntent {
    /// The intent's spelling (the last segment with the module stripped).
    pub fn effect(&self) -> &str {
        last_segment(&self.qualified)
    }

    /// The path passed through (`a → b`) — None if fired directly.
    pub fn via(&self) -> Option<String> {
        (!self.chain.is_empty()).then(|| self.chain.join(" → "))
    }
}

/// Reads the business intents emitted by one file's translation handlers — `(defhandler name …)` and top-level functions whose parameters are `[effect k]`.
/// `is_intent` answers whether a name including the module is a business intent (a type of the intent layer). If a fired call's head is a repo defk, follow that defk's
/// fired calls down to `max_depth` levels (past imported defks too — reading only the definition's body would let intents through via imported functions).
/// Per handler and per intent, emits the first fired call in body order. Beyond a call that cannot be traced (a function outside the repo, a deff, a method) is not counted.
pub fn translation_intents(world: &World, rel: &str, source: &str, is_intent: &dyn Fn(&str) -> bool, max_depth: usize) -> Vec<TranslationIntent> {
    let forms = Reader::new(source, 0, source.len()).read_all();
    let module = module_of(rel);
    let bindings = form_bindings(&forms, source, &module);
    let reader = FileReader {
        hy: Hy { src: source },
        lines: LineIndex::new(source),
        scope: Scope {
            module: &module,
            bindings: &bindings,
        },
        path: rel.to_string(),
    };
    let mut out = Vec::new();
    for form in top_definitions(&reader.hy, &forms) {
        let Some((name, body)) = translation_handler(&reader.hy, form) else {
            continue;
        };
        let mut sites = Vec::new();
        for item in body {
            collect_sites(&reader, item, Flags::default(), &mut sites);
        }
        let mut found: Vec<TranslationIntent> = Vec::new();
        for site in sites {
            let mut reached = Vec::new();
            reach_intents(world, &site.callee, is_intent, max_depth, &mut BTreeSet::new(), &mut Vec::new(), &mut reached);
            for (qualified, chain) in reached {
                if found.iter().any(|f| f.qualified == qualified) {
                    continue;
                }
                found.push(TranslationIntent {
                    handler: reader.hy.text(name).to_string(),
                    qualified,
                    chain,
                    start: site.head.0,
                    end: site.head.1,
                });
            }
        }
        out.extend(found);
    }
    out
}

/// If a top-level form is a translation handler, the form of its name and its body (the list of forms to search for fired calls).
/// `(defhandler name clause…)`, and `defk` / `deff` / `defn` whose parameters are `[effect k]`.
fn translation_handler<'f>(hy: &Hy, form: &'f Form) -> Option<(&'f Form, Vec<&'f Form>)> {
    let items = live(form)?;
    let head = items.first().and_then(|h| hy.symbol(h))?;
    let name = items.get(1).copied().filter(|n| matches!(n.node, Node::Symbol))?;
    match head {
        "defhandler" => Some((name, items[2..].to_vec())),
        "defk" | "deff" | "defn" => {
            let params: Vec<&str> = items
                .get(2)?
                .bracket_items()?
                .iter()
                .filter(|i| !matches!(i.node, Node::Discarded))
                .map(|i| hy.text(i))
                .collect();
            (params == ["effect", "k"]).then(|| (name, items[3..].to_vec()))
        }
        _ => None,
    }
}

/// Collects the business intents reachable from the fired call's head callee (one if it is an intent; if a repo defk, into its fired calls, down to the remaining depth).
/// `visited` is the defks followed within the same handler (cuts cycles and double traversal), `chain` is the spellings of the defks on the current path.
fn reach_intents(
    world: &World,
    callee: &str,
    is_intent: &dyn Fn(&str) -> bool,
    depth: usize,
    visited: &mut BTreeSet<String>,
    chain: &mut Vec<String>,
    out: &mut Vec<(String, Vec<String>)>,
) {
    if is_intent(callee) {
        out.push((callee.to_string(), chain.clone()));
        return;
    }
    let Some(definition) = world.definitions.get(callee).filter(|d| d.kind == SignatureKind::Defk) else {
        return;
    };
    if depth == 0 || !visited.insert(callee.to_string()) {
        return;
    }
    chain.push(last_segment(callee).to_string());
    for site in &definition.sites {
        reach_intents(world, &site.callee, is_intent, depth - 1, visited, chain, out);
    }
    chain.pop();
}

// --- DOEFF174: business layers do not directly use lower layers' effects and handlers ------------------------------------------

/// The kinds of DOEFF174 hit (closed set).
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum LowerLayerKind {
    /// Emits a lower layer's effect (the spelling of the defks followed from the executing call — empty if executed directly).
    Effect { chain: Vec<String> },
    /// Applies a lower layer's package handler with with_handlers / WithHandler.
    Handler,
}

/// One DOEFF174 breach — a lower layer's effect or handler used by a definition in a business layer, and its position. Ranges are bytes.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LowerLayerUse {
    /// The definition's spelling.
    pub definition: String,
    /// The name including the module of the effect or handler (mangled).
    pub qualified: String,
    pub kind: LowerLayerKind,
    pub start: usize,
    pub end: usize,
}

impl LowerLayerUse {
    /// The spelling of the effect or handler (the last segment with the module stripped).
    pub fn name(&self) -> &str {
        last_segment(&self.qualified)
    }

    /// The path passed through (`a → b`) — None for effects and handlers executed directly.
    pub fn via(&self) -> Option<String> {
        match &self.kind {
            LowerLayerKind::Effect { chain } if !chain.is_empty() => Some(chain.join(" → ")),
            _ => None,
        }
    }
}

/// Definition heads that look for lower layers' effects and handlers.
const LOWER_LAYER_DEFINITION_HEADS: &[&str] = &["defk", "deff", "defn", "defp", "defhandler"];

/// The heads of calls that apply a handler (the last segment with the module stripped, mangled) — the form that takes a list and the form that takes one.
const HANDLER_LIST_HEADS: &[&str] = &["with_handlers"];
const HANDLER_ONE_HEADS: &[&str] = &["with_handler", "WithHandler"];

/// For each top-level definition of one file (defk, deff, defn, defp, defhandler), reads where a lower layer's effect is emitted and where a lower layer's handler is applied.
/// `is_effect` answers whether the head of an executing call (name including the module) is a lower layer's effect, and `is_handler` whether the name of the applied handler is from a lower layer's package. For effects, if the executing call's head is a repo defk,
/// follow down to the depth `max_depth` (the same reach as DOEFF130). Handlers are judged by the name of the call head or the symbol of an element of the applied list (also a symbol expanded with `#*`) — a handler received as a value (a read of the `opened.handlers` field, an argument) has no statically
/// determined name, so it is not counted. Emits the first one in body order per definition and per name.
pub fn lower_layer_uses(
    world: &World,
    rel: &str,
    source: &str,
    is_effect: &dyn Fn(&str) -> bool,
    is_handler: &dyn Fn(&str) -> bool,
    max_depth: usize,
) -> Vec<LowerLayerUse> {
    let forms = Reader::new(source, 0, source.len()).read_all();
    let module = module_of(rel);
    let bindings = form_bindings(&forms, source, &module);
    let reader = FileReader {
        hy: Hy { src: source },
        lines: LineIndex::new(source),
        scope: Scope {
            module: &module,
            bindings: &bindings,
        },
        path: rel.to_string(),
    };
    let mut out = Vec::new();
    for form in top_definitions(&reader.hy, &forms) {
        let Some(items) = live(form) else { continue };
        if !items.first().and_then(|h| reader.hy.symbol(h)).is_some_and(|h| LOWER_LAYER_DEFINITION_HEADS.contains(&h)) {
            continue;
        }
        let Some(name) = items.get(1).and_then(|n| definition_name(n).0).filter(|n| matches!(n.node, Node::Symbol)) else {
            continue;
        };
        let definition = reader.hy.text(name).to_string();
        let body = &items[2..];
        let mut found: Vec<LowerLayerUse> = Vec::new();
        let mut sites = Vec::new();
        for item in body {
            collect_sites(&reader, item, Flags::default(), &mut sites);
        }
        for site in sites {
            let mut reached = Vec::new();
            reach_intents(world, &site.callee, is_effect, max_depth, &mut BTreeSet::new(), &mut Vec::new(), &mut reached);
            for (qualified, chain) in reached {
                if found.iter().any(|f| f.qualified == qualified) {
                    continue;
                }
                found.push(LowerLayerUse {
                    definition: definition.clone(),
                    qualified,
                    kind: LowerLayerKind::Effect { chain },
                    start: site.head.0,
                    end: site.head.1,
                });
            }
        }
        let mut handlers = Vec::new();
        for item in body {
            installed_handlers(&reader, item, &mut handlers);
        }
        for (qualified, span) in handlers {
            if !is_handler(&qualified) || found.iter().any(|f| f.qualified == qualified) {
                continue;
            }
            found.push(LowerLayerUse { definition: definition.clone(), qualified, kind: LowerLayerKind::Handler, start: span.0, end: span.1 });
        }
        found.sort_by_key(|f| f.start);
        out.extend(found);
    }
    out
}

/// Collects, within a form tree, the names (including the module) and positions of handlers placed in the with_handlers list and as the first argument of with_handler / WithHandler.
/// An element of the list is its head if a call, the symbol itself if a symbol, or the symbol inside if a `#*` expansion — anything else (a field read, an expression) has no determined name, so it is not collected.
fn installed_handlers(reader: &FileReader, form: &Form, out: &mut Vec<(String, (usize, usize))>) {
    if let Some(items) = live(form) {
        let head = items.first().and_then(|h| reader.hy.symbol(h)).map(|h| reader.scope.qualify(h));
        let installed: Vec<&Form> = match head.as_deref().map(last_segment) {
            Some(h) if HANDLER_LIST_HEADS.contains(&h) => items.get(1).and_then(|list| list.bracket_items()).map(|l| l.iter().filter(|i| !matches!(i.node, Node::Discarded)).collect()).unwrap_or_default(),
            Some(h) if HANDLER_ONE_HEADS.contains(&h) => items.get(1).copied().into_iter().collect(),
            _ => Vec::new(),
        };
        for handler in installed {
            let named = match &handler.node {
                Node::Prefixed { inner: Some(inner), .. } => Some(inner.as_ref()),
                _ => Some(handler),
            }
            .and_then(|h| match call_head(&reader.hy, h) {
                Some(_) => live(h).and_then(|i| i.first().copied()),
                None => Some(h),
            })
            .filter(|h| matches!(h.node, Node::Symbol));
            if let Some(symbol) = named {
                out.push((reader.scope.qualify(reader.hy.text(symbol)), (symbol.span.start, symbol.span.end)));
            }
        }
    }
    match &form.node {
        Node::Seq { items, .. } => items.iter().filter(|i| !matches!(i.node, Node::Discarded)).for_each(|i| installed_handlers(reader, i, out)),
        Node::Prefixed { prefix, inner: Some(inner) }
            if !matches!(prefix, doeff_indexer::hy_index::reader::Prefix::Quote | doeff_indexer::hy_index::reader::Prefix::Quasiquote) =>
        {
            installed_handlers(reader, inner, out)
        }
        Node::Annotated { target: Some(target), .. } => installed_handlers(reader, target, out),
        _ => {}
    }
}

// --- Headings and bindings of one file -----------------------------------------------------------------

/// Reads one file's headings and bindings and passes them with the reading tools (reader, list of forms) to `then` (the table is the one built by `World::build` with this file's same contents as the
/// overlay). The reader borrows the file's contents and bindings, so the subsequent reading (replacement and body — `file_view.rs`) is done inside `then`.
/// The heading reading knows nothing about the replacement and body reading.
pub(super) fn read_file<T>(
    world: &World,
    root: &Path,
    rel: &str,
    source: &str,
    then: impl FnOnce(&FileReader, &[Form], FileHeads) -> T,
) -> T {
    let forms = Reader::new(source, 0, source.len()).read_all();
    let module = module_of(rel);
    let bindings = form_bindings(&forms, source, &module);
    let reader = FileReader {
        hy: Hy { src: source },
        lines: LineIndex::new(source),
        scope: Scope {
            module: &module,
            bindings: &bindings,
        },
        path: root.join(rel).to_string_lossy().into_owned(),
    };
    let mut out = FileHeads::default();
    crate::timing::timed("signatures.heads", || {
        for form in top_definitions(&reader.hy, &forms) {
            if let Some(shape) = definition_shape(&reader.hy, form) {
                out.signatures
                    .push(signature_of(world, &reader, form, &shape));
            }
            let mut vars: HashMap<String, Option<TypeRef>> = HashMap::new();
            collect_bindings(
                world,
                &reader,
                form,
                Flags::default(),
                &mut vars,
                &mut out.bindings,
            );
        }
    });
    then(&reader, &forms, out)
}

/// The heading of one defk / deff.
fn signature_of(
    world: &World,
    reader: &FileReader,
    form: &Form,
    shape: &DefinitionShape,
) -> Signature {
    let pre = shape.contract.and_then(|c| reader.dict_value(c, ":pre"));
    let annotated: BTreeMap<&str, TypeRef> = reader
        .annotations(pre)
        .into_iter()
        .map(|(name, ty)| (name, world.resolve(&reader.type_syntax(ty))))
        .collect();
    let params = shape
        .params
        .and_then(|p| p.bracket_items())
        .map(|items| {
            items
                .iter()
                .filter(|i| matches!(i.node, Node::Symbol) && !reader.hy.text(i).starts_with('&'))
                .map(|i| {
                    let name = reader.hy.text(i);
                    SignatureParam {
                        name: name.to_string(),
                        type_ref: annotated.get(name).cloned(),
                    }
                })
                .collect()
        })
        .unwrap_or_default();
    let qualified = reader.scope.qualify(reader.hy.text(shape.name));
    let summary = world.summaries.get(&qualified).cloned().unwrap_or_default();
    let declared = shape
        .contract
        .and_then(|c| reader.dict_value(c, ":effects"))
        .and_then(|list| list.bracket_items())
        .map(|items| {
            items
                .iter()
                .filter_map(|i| reader.hy.symbol(i))
                .map(|spelled| world.effect_ref(&reader.scope.qualify(spelled), spelled))
                .collect()
        });
    let inferred = summary
        .effects
        .iter()
        .map(|q| world.effect_ref(q, last_segment(q)))
        .collect();
    let tags = shape
        .contract
        .and_then(|c| reader.dict_value(c, ":tags"))
        .map(|dict| string_entries(reader, dict))
        .unwrap_or_default();
    Signature {
        kind: shape.kind,
        name: reader.hy.text(shape.name).to_string(),
        path: reader.path.clone(),
        range: reader.range(shape.name),
        full_range: reader.range(form),
        contract_range: shape.contract.map(|c| reader.range(c)),
        params,
        answer: answer_syntax(reader, shape).map(|a| world.resolve(&a)),
        absent: summary.absent,
        raises: summary
            .raises
            .iter()
            .map(|(q, spelled)| world.resolve_qualified(q, spelled))
            .collect(),
        effects: SignatureEffects { declared, inferred, complete: !summary.opaque },
        tags,
    }
}

/// The key of a dict's string value (`:context "x"` -> context = x).
fn string_entries(reader: &FileReader, dict: &Form) -> BTreeMap<String, String> {
    let Node::Seq {
        delim: Delim::Brace,
        items,
    } = &dict.node
    else {
        return BTreeMap::new();
    };
    let items: Vec<&Form> = items
        .iter()
        .filter(|i| !matches!(i.node, Node::Discarded))
        .collect();
    items
        .chunks(2)
        .filter_map(|pair| match pair {
            [k, v] if matches!(k.node, Node::Keyword) => match &v.node {
                Node::Str { body, .. } => Some((
                    reader.hy.text(k).trim_start_matches(':').to_string(),
                    reader
                        .hy
                        .src
                        .get(body.start..body.end)
                        .unwrap_or("")
                        .to_string(),
                )),
                _ => None,
            },
            _ => None,
        })
        .collect()
}

/// The result of reading a binding's type.
struct Typed {
    type_ref: Option<TypeRef>,
    origin: BindingOrigin,
    absent: bool,
    raises: Vec<TypeRef>,
}

impl Typed {
    fn unknown() -> Typed {
        Typed {
            type_ref: None,
            origin: BindingOrigin::Unknown,
            absent: false,
            raises: Vec::new(),
        }
    }
}

/// The type and Absent / Raise of a fired expression (the right side of `(<- …)`, the inside of `(! …)`).
fn performed_type(
    world: &World,
    reader: &FileReader,
    value: &Form,
    absent_as_raise: Option<&Form>,
    flags: Flags,
) -> Typed {
    let Some(head) = call_head(&reader.hy, value) else {
        return Typed::unknown();
    };
    let callee = reader.scope.qualify(head);
    let site = Site {
        callee: callee.clone(),
        absent_as_raise: absent_as_raise.map(|f| raise_type(reader, f)),
        absent_handled: flags.absent_handled,
        raise_handled: flags.raise_handled,
        head: call_head_span(value),
    };
    let outcome = world.site_outcome(&site);
    let raises = outcome
        .raises
        .iter()
        .map(|(q, spelled)| world.resolve_qualified(q, spelled))
        .collect();
    if let Some(effect) = world.effects.get(&callee) {
        let type_ref = world.effect_value(effect);
        let origin = if type_ref.is_some() {
            BindingOrigin::Effect
        } else {
            BindingOrigin::Unknown
        };
        return Typed {
            type_ref,
            origin,
            absent: outcome.absent,
            raises,
        };
    }
    match world.definitions.get(&callee) {
        Some(definition) if definition.kind == SignatureKind::Defk => {
            let type_ref = definition.answer.as_ref().map(|a| world.resolve(a));
            let origin = if type_ref.is_some() {
                BindingOrigin::Call
            } else {
                BindingOrigin::Unknown
            };
            Typed {
                type_ref,
                origin,
                absent: outcome.absent,
                raises,
            }
        }
        _ => Typed::unknown(),
    }
}

/// The type of a non-fired expression (a literal, a call of a type name, a call of a deff, `(! …)`).
fn value_type(world: &World, reader: &FileReader, value: &Form, flags: Flags) -> Typed {
    let literal = |name: &str| Typed {
        type_ref: Some(TypeRef::Name {
            name: name.to_string(),
            definition: None,
        }),
        origin: BindingOrigin::Literal,
        absent: false,
        raises: Vec::new(),
    };
    match &value.node {
        Node::Str { .. } => literal("str"),
        Node::Number => literal(if reader.hy.text(value).contains('.') {
            "float"
        } else {
            "int"
        }),
        Node::Symbol => match reader.hy.text(value) {
            "True" | "False" => literal("bool"),
            "None" => literal("None"),
            _ => Typed::unknown(),
        },
        Node::Seq {
            delim: Delim::Brace,
            ..
        } => literal("dict"),
        Node::Seq {
            delim: Delim::Bracket,
            ..
        } => literal("list"),
        Node::Seq {
            delim: Delim::Paren,
            ..
        } => {
            let items = live(value).unwrap_or_default();
            let Some(head) = items.first().and_then(|h| reader.hy.symbol(h)) else {
                return Typed::unknown();
            };
            if head == "!" {
                return match items.get(1) {
                    Some(inner) => performed_type(world, reader, inner, None, flags),
                    None => Typed::unknown(),
                };
            }
            let qualified = reader.scope.qualify(head);
            if head.starts_with(|c: char| c.is_ascii_uppercase())
                && world.types.contains_key(&qualified)
                && !world.effects.contains_key(&qualified)
            {
                return Typed {
                    type_ref: Some(TypeRef::Name {
                        name: head.to_string(),
                        definition: world.types.get(&qualified).cloned(),
                    }),
                    origin: BindingOrigin::Constructor,
                    absent: false,
                    raises: Vec::new(),
                };
            }
            match world.definitions.get(&qualified) {
                Some(definition) if definition.kind == SignatureKind::Deff => {
                    match &definition.answer {
                        Some(answer) => Typed {
                            type_ref: Some(world.resolve(answer)),
                            origin: BindingOrigin::Call,
                            absent: false,
                            raises: Vec::new(),
                        },
                        None => Typed::unknown(),
                    }
                }
                _ => Typed::unknown(),
            }
        }
        _ => Typed::unknown(),
    }
}

/// Collects bindings (quote is not read). `vars` is the types of `var`s within the same top-level definition (for `:=`).
fn collect_bindings(
    world: &World,
    reader: &FileReader,
    form: &Form,
    flags: Flags,
    vars: &mut HashMap<String, Option<TypeRef>>,
    out: &mut Vec<Binding>,
) {
    let children: Vec<&Form> = match &form.node {
        Node::Seq { items, .. } => items
            .iter()
            .filter(|i| !matches!(i.node, Node::Discarded))
            .collect(),
        Node::Prefixed {
            prefix,
            inner: Some(inner),
        } => {
            if matches!(
                prefix,
                doeff_indexer::hy_index::reader::Prefix::Quote
                    | doeff_indexer::hy_index::reader::Prefix::Quasiquote
            ) {
                return;
            }
            vec![inner.as_ref()]
        }
        Node::Annotated {
            target: Some(target),
            ..
        } => vec![target.as_ref()],
        _ => return,
    };
    let is_paren = matches!(
        &form.node,
        Node::Seq {
            delim: Delim::Paren,
            ..
        }
    );
    let head_form = children.first().copied().filter(|_| is_paren);
    let head = head_form.and_then(|h| match h.node {
        Node::Symbol | Node::Keyword => Some(reader.hy.text(h)),
        _ => None,
    });
    let mut inner_flags = flags;
    match head {
        Some("quote" | "quasiquote") => return,
        Some("<-") => {
            if let Some(bind) = bind_shape(&reader.hy, &children) {
                if let Some(name) = bind.name.filter(|n| matches!(n.node, Node::Symbol)) {
                    let performed =
                        performed_type(world, reader, bind.value, bind.absent_as_raise, flags);
                    let typed = match bind.annotation {
                        Some(annotation) => Typed {
                            type_ref: Some(world.resolve(&reader.type_syntax(annotation))),
                            origin: BindingOrigin::Annotation,
                            absent: performed.absent,
                            raises: performed.raises,
                        },
                        None => performed,
                    };
                    out.push(binding(
                        reader,
                        BindingForm::Bind,
                        None,
                        form,
                        head_form,
                        name,
                        bind.annotation,
                        Some(bind.value),
                        typed,
                    ));
                }
            }
        }
        Some("setv") => {
            for pair in children[1..].chunks(2) {
                if let [name, value] = pair {
                    if matches!(name.node, Node::Symbol) {
                        let typed = value_type(world, reader, value, flags);
                        out.push(binding(
                            reader,
                            BindingForm::Setv,
                            None,
                            form,
                            head_form,
                            name,
                            None,
                            Some(value),
                            typed,
                        ));
                    }
                }
            }
        }
        Some(word @ ("val" | "var" | "lazy" | "session")) => {
            // `(lazy val x e)`, `(session var x e)`: what follows the prior word is val / var
            let (modifier, word, start) = match BindingModifier::of(word) {
                Some(modifier) => (Some(modifier), children.get(1).and_then(|w| reader.hy.symbol(w)).unwrap_or(""), 2),
                None => (None, word, 1),
            };
            let kind = [("val", BindingForm::Val), ("var", BindingForm::Var)]
                .into_iter()
                .find(|(spelled, _)| *spelled == word)
                .map(|(_, kind)| kind);
            if let Some(kind) = kind {
                for pair in binding_pairs(&reader.hy, &children[start.min(children.len())..]) {
                    let (name, Some(value)) = (pair.name, pair.value) else { continue };
                    if !matches!(name.node, Node::Symbol) {
                        continue;
                    }
                    let typed = match pair.bang {
                        Some(_) => performed_type(world, reader, value, None, flags),
                        None => value_type(world, reader, value, flags),
                    };
                    let typed = match typed {
                // `(var x None)` is a place to put a value of a different type later — None is not read as a type (it becomes unknown).
                        Typed {
                            type_ref: Some(TypeRef::Name { name, .. }),
                            origin: BindingOrigin::Literal,
                            ..
                        } if kind == BindingForm::Var && name == "None" => Typed::unknown(),
                        other => other,
                    };
                    if kind == BindingForm::Var {
                        vars.insert(reader.hy.text(name).to_string(), typed.type_ref.clone());
                    }
                    out.push(binding(reader, kind, modifier, form, head_form, name, None, Some(value), typed));
                }
            }
        }
        Some(":=") => {
            let pair = binding_pairs(&reader.hy, &children[1..]).into_iter().next();
            if let Some(BindingPair { name, value: Some(value), .. }) = pair {
                if matches!(name.node, Node::Symbol) {
                    let typed = match vars.get(reader.hy.text(name)) {
                        Some(Some(type_ref)) => Typed {
                            type_ref: Some(type_ref.clone()),
                            origin: BindingOrigin::Var,
                            absent: false,
                            raises: Vec::new(),
                        },
                        _ => Typed::unknown(),
                    };
                    out.push(binding(
                        reader,
                        BindingForm::Assign,
                        None,
                        form,
                        head_form,
                        name,
                        None,
                        Some(value),
                        typed,
                    ));
                }
            }
        }
        Some("absent-as") => inner_flags.absent_handled = true,
        Some("on-raise") => {
            if let Some(program) = children.get(1) {
                collect_bindings(
                    world,
                    reader,
                    program,
                    Flags {
                        raise_handled: true,
                        ..flags
                    },
                    vars,
                    out,
                );
            }
            for child in children.iter().skip(2) {
                collect_bindings(world, reader, child, flags, vars, out);
            }
            return;
        }
        _ => {}
    }
    for child in children {
        collect_bindings(world, reader, child, inner_flags, vars, out);
    }
}

/// Makes one binding into the contract's shape.
#[allow(clippy::too_many_arguments)]
fn binding(
    reader: &FileReader,
    form: BindingForm,
    modifier: Option<BindingModifier>,
    whole: &Form,
    head: Option<&Form>,
    name: &Form,
    annotation: Option<&Form>,
    value: Option<&Form>,
    typed: Typed,
) -> Binding {
    Binding {
        form,
        modifier,
        name: reader.hy.text(name).to_string(),
        path: reader.path.clone(),
        range: reader.range(name),
        form_range: reader.range(whole),
        head_range: head
            .map(|h| reader.range(h))
            .unwrap_or_else(|| reader.range(whole)),
        annotation_range: annotation.map(|a| reader.range(a)),
        value_range: value.map(|v| reader.range(v)),
        type_ref: typed.type_ref,
        origin: typed.origin,
        absent: typed.absent,
        raises: typed.raises,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Lays files out under a root to build the table, and reads one file's headings and bindings.
    fn read(files: &[(&str, &str)], target: &str) -> FileHeads {
        let dir = tempfile::tempdir().unwrap();
        let root = dir.path().canonicalize().unwrap();
        for (rel, text) in files {
            let path = root.join(rel);
            std::fs::create_dir_all(path.parent().unwrap()).unwrap();
            std::fs::write(path, text).unwrap();
        }
        let source = files.iter().find(|(rel, _)| *rel == target).unwrap().1;
        let world = World::build(&root, Some((target, source)));
        let heads = read_file(&world, &root, target, source, |_, _, heads| heads);
        // A single-file run builds the table only from the module files the written file names (`build_for_file`). The headings and bindings of the written file are the same as in a table built from the whole repo
        // (the names looked up are the written file's symbols resolved, or only the names held by the reached files' facts — the inference result is the same too).
        let named = World::build_for_file(&root, target, source);
        let named_heads = read_file(&named, &root, target, source, |_, _, heads| heads);
        assert_eq!(named_heads, heads, "the table of build_for_file differs from the table of build ({target})");
        heads
    }

    fn name_of(t: &TypeRef) -> String {
        match t {
            TypeRef::Name { name, .. } => name.clone(),
            TypeRef::Union { members } => {
                members.iter().map(name_of).collect::<Vec<_>>().join(" | ")
            }
            TypeRef::Apply { head, args } => format!(
                "{}[{}]",
                name_of(head),
                args.iter().map(name_of).collect::<Vec<_>>().join(", ")
            ),
            TypeRef::Unknown { text } => format!("?{}", text),
        }
    }

    const INTENT: &str = r#"
(defrecord Row "row" (#^ str id))
(defrecord Missing "missing" (#^ str id))
(defrecord Unreadable "unreadable" (#^ str id))
(defeffect ReadRow
  "reads a row"
  {:fields [(: id str)]
   :answer (| Row Missing Unreadable)
   :absent [Missing] :failure [Unreadable]
   :tags {:context "demo" :role "intent"}})
(defeffect PutRow
  "writes a row"
  {:fields [(: row Row)] :answer bool :tags {:context "demo" :role "intent"}})
"#;

    /// Inference follows down past the defks reached by calls (a -> b -> c is fired, and c fires an effect). The table of a single-file run (`build_for_file`) also reads until the reachable modules are closed, so
    /// the written file's heading is the same as the whole repo's table (`read` builds both and compares). The definition of an unreachable module (z) is not in the table.
    #[test]
    fn inference_follows_calls_through_the_modules_a_file_reaches() {
        let fx = r#"
(defrecord Row "row" (#^ str id))
(defeffect Ping "pings" {:fields [(: n int)] :answer Row :tags {:context "demo" :role "intent"}})
"#;
        let leaf = "(import intent.fx [Ping])\n(defk leaf [] {:tags {:context \"demo\" :role \"judgment\"}} (<- x (Ping 1)) x)\n";
        let mid = "(import core.leaf [leaf])\n(defk mid [] {:tags {:context \"demo\" :role \"judgment\"}} (<- r (leaf)) r)\n";
        let top = "(import core.mid [mid])\n(defk top [] {:tags {:context \"demo\" :role \"judgment\"}} (<- r (mid)) r)\n";
        let stray = "(import intent.fx [Ping])\n(defk stray [] {:tags {:context \"demo\" :role \"judgment\"}} (<- x (Ping 2)) x)\n";
        let files = [("intent/fx.hy", fx), ("core/leaf.hy", leaf), ("core/mid.hy", mid), ("core/top.hy", top), ("core/stray.hy", stray)];
        let got = read(&files, "core/top.hy");
        let names: Vec<&str> = got.signatures[0].effects.inferred.iter().map(|e| e.name.as_str()).collect();
        assert_eq!(names, vec!["Ping"], "top fires leaf's Ping through mid");
        assert!(got.signatures[0].effects.inferred[0].definition.is_some(), "a reached effect can be resolved down to the definition's position");
        // The table of a single-file run does not hold the definitions of unreachable modules (core/stray).
        let dir = tempfile::tempdir().unwrap();
        let root = dir.path().canonicalize().unwrap();
        for (rel, text) in files {
            let path = root.join(rel);
            std::fs::create_dir_all(path.parent().unwrap()).unwrap();
            std::fs::write(path, text).unwrap();
        }
        let named = World::build_for_file(&root, "core/top.hy", top);
        assert!(named.definitions.contains_key("core.leaf.leaf") && !named.definitions.contains_key("core.stray.stray"));
    }

    #[test]
    fn pure_defk_signature_reads_pre_post_and_tags() {
        let core = r#"
(defk body-version-line [n document]
  {:tags {:context "kanban" :role "judgment"}
   :pre [(: n int) (: document str)]
   :post [(: % str)]}
  "doc"
  f"{n}{document}")
"#;
        let got = read(&[("core/body.hy", core)], "core/body.hy");
        let sig = &got.signatures[0];
        assert_eq!(sig.kind, SignatureKind::Defk);
        assert_eq!(sig.name, "body-version-line");
        let params: Vec<(String, String)> = sig
            .params
            .iter()
            .map(|p| {
                (
                    p.name.clone(),
                    p.type_ref.as_ref().map(name_of).unwrap_or_default(),
                )
            })
            .collect();
        assert_eq!(
            params,
            vec![
                ("n".into(), "int".into()),
                ("document".into(), "str".into())
            ]
        );
        assert_eq!(sig.answer.as_ref().map(name_of).as_deref(), Some("str"));
        assert!(!sig.absent);
        assert!(sig.effects.declared.is_none());
        assert!(sig.effects.inferred.is_empty());
        assert_eq!(sig.tags.get("role").map(String::as_str), Some("judgment"));
        assert_eq!(sig.contract_range.unwrap().start.line, 2);
        assert!(sig.effects.complete, "a defk with no fired calls has complete inference");
    }

    #[test]
    fn docstring_before_contract_and_untrackable_calls_are_read() {
        // The form of agora-controllers' sim-part (controllers/agora_sim/full_system.hy) and run-emulated-with
        let core = r#"
(defk sim-part [services handlers]
  "A part value."
  {:pre [(: services list) (: handlers (| Callable None))]
   :post [(: % dict)]
   :tags {:context "turn" :role "judgment"}}
  {"services" services "handlers" handlers})
(defk run-emulated-with [agora parts]
  {:pre [(: agora int) (: parts list)] :post [(: % list)] :tags {:context "turn" :role "entry"}}
  (<- results list (with_handlers (! (helper parts)) (run agora)))
  results)
"#;
        let got = read(&[("core/sim.hy", core)], "core/sim.hy");
        let sim = &got.signatures[0];
        let contract = sim.contract_range.unwrap();
        assert_eq!((contract.start.line, contract.end.line), (3, 5), "the contract dict is found even when the docstring comes first");
        assert_eq!(sim.params[1].type_ref.as_ref().map(name_of).as_deref(), Some("Callable | None"));
        assert_eq!(sim.tags.get("role").map(String::as_str), Some("judgment"));
        assert!(sim.effects.complete);
        let run = &got.signatures[1];
        assert!(run.effects.inferred.is_empty());
        assert!(!run.effects.complete, "it fires the untraceable call with_handlers, so even if the inference is empty it cannot be said to be \"no effects\"");
    }

    #[test]
    fn effects_are_inferred_transitively_with_absent_and_raise() {
        let core = r#"
(import intent.rows [ReadRow PutRow Row])
(defk fetch [id]
  {:pre [(: id str)] :post [(: % Row)] :effects [ReadRow]}
  (<- row Row (ReadRow id))
  row)
(defk store [id]
  {:pre [(: id str)] :post [(: % bool)] :effects [PutRow]}
  (val row (! (fetch id)))
  (<- ok (PutRow row))
  ok)
"#;
        let got = read(
            &[("intent/rows.hy", INTENT), ("core/flow.hy", core)],
            "core/flow.hy",
        );
        let fetch = &got.signatures[0];
        assert!(fetch.absent, "ReadRow's :absent escapes to the caller");
        assert_eq!(
            fetch.raises.iter().map(name_of).collect::<Vec<_>>(),
            vec!["Unreadable"]
        );
        let store = &got.signatures[1];
        let inferred: Vec<String> = store
            .effects
            .inferred
            .iter()
            .map(|e| e.name.clone())
            .collect();
        assert_eq!(inferred, vec!["PutRow", "ReadRow"]);
        let declared: Vec<String> = store
            .effects
            .declared
            .as_ref()
            .unwrap()
            .iter()
            .map(|e| e.name.clone())
            .collect();
        assert_eq!(
            declared,
            vec!["PutRow"],
            "the mismatch between declaration and inference is shown as is (the editor shows it)"
        );
        assert!(store.absent);
        let read_row = &fetch.effects.declared.as_ref().unwrap()[0];
        assert!(read_row
            .definition
            .as_ref()
            .unwrap()
            .path
            .ends_with("intent/rows.hy"));
        assert_eq!(
            read_row.absent.iter().map(name_of).collect::<Vec<_>>(),
            vec!["Missing"]
        );
    }

    /// The inference material's key: a rewrite that changes only positions (lines are added and shifted) does not change it, but a change in the inference material
    /// (the effect fired, the definition's name, how Absent is received) does. If the key is the same the previous inference answer is used, so if the key leaks the material the answer becomes stale.
    #[test]
    fn inference_key_ignores_positions_and_follows_what_inference_reads() {
        let key = |source: &str| KeyedFacts::of("core/flow.hy", file_facts(Path::new("/r"), "core/flow.hy", source)).inference_key;
        let base = "(defk fetch [id]\n  {:pre [(: id str)] :post [(: % Row)]}\n  (<- row Row (ReadRow id))\n  row)\n";
        let shifted = format!("; note\n\n{base}");
        assert_eq!(key(base), key(&shifted), "merely shifting lines does not change the key");
        assert_ne!(key(base), key(&base.replace("(ReadRow id)", "(PutRow id)")), "the key changes if the fired effect changes");
        assert_ne!(key(base), key(&base.replace("defk fetch", "defk fetch-row")), "the key changes if the definition's name changes");
        assert_ne!(key(base), key(&base.replace("(<- row Row (ReadRow id))", "(absent-as None (<- row Row (ReadRow id)))")), "the key changes if how Absent is received changes");
        assert_ne!(
            KeyedFacts::of("core/a.hy", file_facts(Path::new("/r"), "core/a.hy", base)).inference_key,
            KeyedFacts::of("core/b.hy", file_facts(Path::new("/r"), "core/b.hy", base)).inference_key,
            "the file's path is also part of the key (a different file with the same contents is not mistaken)"
        );
    }

    #[test]
    fn absent_suffix_and_absent_as_do_not_wrap_in_maybe() {
        let core = r#"
(import intent.rows [ReadRow Row])
(defrecord Conflict "conflict" (#^ str why))
(defk strict [id]
  {:pre [(: id str)] :post [(: % Row)]}
  (<- row (ReadRow id) :absent (Conflict "missing"))
  row)
(defk lenient [id]
  {:pre [(: id str)] :post [(: % (| Row None))]}
  (absent-as None (ReadRow id)))
"#;
        let got = read(
            &[("intent/rows.hy", INTENT), ("core/flow.hy", core)],
            "core/flow.hy",
        );
        let strict = &got.signatures[0];
        assert!(!strict.absent);
        let mut raises: Vec<String> = strict.raises.iter().map(name_of).collect();
        raises.sort();
        assert_eq!(raises, vec!["Conflict", "Unreadable"]);
        assert!(!got.signatures[1].absent);
    }

    #[test]
    fn bindings_carry_types_or_null_when_unknown() {
        let core = r#"
(import intent.rows [ReadRow Row])
(defk fetch [id]
  {:pre [(: id str)] :post [(: % Row)]}
  (<- a str (helper id))
  (<- row (ReadRow id))
  (val made (Row :id id))
  (var count 0)
  (:= count (+ count 1))
  (var slot None)
  (:= slot row)
  (setv mystery (compute id))
  row)
"#;
        let got = read(
            &[("intent/rows.hy", INTENT), ("core/flow.hy", core)],
            "core/flow.hy",
        );
        let seen: Vec<(BindingForm, String, Option<String>, BindingOrigin)> = got
            .bindings
            .iter()
            .map(|b| {
                (
                    b.form,
                    b.name.clone(),
                    b.type_ref.as_ref().map(name_of),
                    b.origin,
                )
            })
            .collect();
        assert_eq!(
            seen,
            vec![
                (
                    BindingForm::Bind,
                    "a".into(),
                    Some("str".into()),
                    BindingOrigin::Annotation
                ),
                (
                    BindingForm::Bind,
                    "row".into(),
                    Some("Row".into()),
                    BindingOrigin::Effect
                ),
                (
                    BindingForm::Val,
                    "made".into(),
                    Some("Row".into()),
                    BindingOrigin::Constructor
                ),
                (
                    BindingForm::Var,
                    "count".into(),
                    Some("int".into()),
                    BindingOrigin::Literal
                ),
                (
                    BindingForm::Assign,
                    "count".into(),
                    Some("int".into()),
                    BindingOrigin::Var
                ),
                (
                    BindingForm::Var,
                    "slot".into(),
                    None,
                    BindingOrigin::Unknown
                ),
                (
                    BindingForm::Assign,
                    "slot".into(),
                    None,
                    BindingOrigin::Unknown
                ),
                (
                    BindingForm::Setv,
                    "mystery".into(),
                    None,
                    BindingOrigin::Unknown
                ),
            ]
        );
        let row = &got.bindings[1];
        assert!(row.absent);
        assert_eq!(
            row.raises.iter().map(name_of).collect::<Vec<_>>(),
            vec!["Unreadable"]
        );
        assert!(got.bindings[0].annotation_range.is_some());
        let TypeRef::Name { definition, .. } = got.bindings[2].type_ref.as_ref().unwrap() else {
            panic!("type of the name")
        };
        assert!(
            definition
                .as_ref()
                .unwrap()
                .path
                .ends_with("intent/rows.hy"),
            "a type name is tied to its definition"
        );
    }

    #[test]
    fn quoted_forms_are_not_read() {
        let core = r#"
(defk f [x]
  {:pre [(: x int)] :post [(: % int)]}
  (val q '(val hidden 1))
  x)
"#;
        let got = read(&[("core/q.hy", core)], "core/q.hy");
        assert!(got.bindings.iter().all(|b| b.name != "hidden"));
    }

    /// Lays files out under a root to build the table, and reads one file's DOEFF127 mismatches.
    fn mismatches(files: &[(&str, &str)], target: &str) -> Vec<EffectMismatch> {
        let dir = tempfile::tempdir().unwrap();
        let root = dir.path().canonicalize().unwrap();
        for (rel, text) in files {
            let path = root.join(rel);
            std::fs::create_dir_all(path.parent().unwrap()).unwrap();
            std::fs::write(path, text).unwrap();
        }
        let source = files.iter().find(|(rel, _)| *rel == target).unwrap().1;
        let world = World::build(&root, Some((target, source)));
        let mismatches = effect_mismatches(&world, target, source);
        // A single-file run's table (only the module files the written file names) gives the same mismatch.
        let named = World::build_for_file(&root, target, source);
        assert_eq!(effect_mismatches(&named, target, source), mismatches, "{target}");
        mismatches
    }

    #[test]
    fn effect_mismatches_point_at_the_call_and_the_declaration() {
        let core = r#"
(import intent.rows [ReadRow PutRow Row])
(import doeff_core_effects [Delay])
(defk fetch [id]
  {:pre [(: id str)] :post [(: % Row)] :effects [ReadRow]}
  (<- row (ReadRow id))
  row)
(defk store [id]
  {:pre [(: id str)] :post [(: % bool)] :effects [PutRow Delay]}
  (val row (! (fetch id)))
  (<- ok (PutRow row))
  ok)
(defk undeclared-ok [id]
  {:pre [(: id str)] :post [(: % Row)]}
  (<- row (ReadRow id))
  row)
(defk waits [s]
  {:pre [(: s int)] :post [(: % int)] :effects [Delay]}
  (<- (Delay s))
  s)
(defk branches [id flag]
  {:pre [(: id str) (: flag bool)] :post [(: % Row)] :effects [ReadRow PutRow]}
  (<- got (if flag (ReadRow id) (PutRow id)))
  got)
(defk through-helper [id]
  {:pre [(: id str)] :post [(: % Row)] :effects [ReadRow]}
  (<- row (read-typed id))
  row)
"#;
        let got = mismatches(
            &[("intent/rows.hy", INTENT), ("core/flow.hy", core)],
            "core/flow.hy",
        );
        let at = |m: &EffectMismatch| &core[m.span().0..m.span().1];
        // Not emitted: undeclared-ok with no declaration, fetch and waits that match (the outside effect Delay), branches that fire both branches,
        // through-helper that fires the untraceable call read-typed (it cannot be said not to have raised)
        assert_eq!(got.len(), 2, "{:?}", got);
        match &got[0] {
            EffectMismatch::Undeclared {
                definition,
                effect,
                via,
                ..
            } => {
                assert_eq!(
                    (definition.as_str(), effect.as_str(), via.as_deref()),
                    ("store", "ReadRow", Some("fetch"))
                );
                assert_eq!(at(&got[0]), "fetch", "the location is the head of the call that went through a defk");
            }
            other => panic!("{:?}", other),
        }
        match &got[1] {
            EffectMismatch::Unused {
                definition, effect, ..
            } => {
                assert_eq!((definition.as_str(), effect.as_str()), ("store", "Delay"));
                assert_eq!(at(&got[1]), "Delay", "the location is the name inside :effects");
            }
            other => panic!("{:?}", other),
        }
    }

    #[test]
    fn foreign_effects_are_inferred_so_declarations_match() {
        let core = r#"
(import doeff_core_effects [Delay])
(import intent.rows [Row])
(defk wait [seconds]
  {:pre [(: seconds int)] :post [(: % int)] :effects [Delay]}
  (<- (Delay seconds))
  (val made (! (Row :id "x")))
  seconds)
"#;
        let got = read(
            &[("intent/rows.hy", INTENT), ("core/wait.hy", core)],
            "core/wait.hy",
        );
        let sig = &got.signatures[0];
        let inferred: Vec<&str> = sig
            .effects
            .inferred
            .iter()
            .map(|e| e.name.as_str())
            .collect();
        assert_eq!(
            inferred,
            vec!["Delay"],
            "an effect outside the repo is also part of inference; a form that fires a repo record is not an effect"
        );
        assert!(sig.effects.inferred[0].definition.is_none());
        assert_eq!(sig.effects.declared.as_ref().unwrap()[0].name, "Delay");
    }
}
