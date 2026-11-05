//! Raw side-effect evidence — from only the catalog (`raw_catalog.rs`) and the index (imports, references, calls, full_range),
//! collects, per definition, direct evidence and via-evidence (with paths). These are facts, not rule verdicts (the source of truth for layer-rule
//! verdicts is the agora-controllers linter). The VS Code extension only displays these facts and holds no catalog.
//!
//! Rules (moved from doeff-runner's TypeScript verdicts — confirmed to give identical results on real data):
//! - Count only the terminal segment of a dotted reference, resolve it to a full name via the file's imports, and compare with the catalog.
//! - Exception types (names ending in Error / Exception / Timeout / Warning) and ignored names are not counted.
//! - Builtins are counted only for unimported, unqualified names, and only at the head position of a call (a local variable `open` is not picked up).
//! - Method names are weak evidence, counted only when the context module is visible as an import of the file or as a reference inside the definition. Only segments written as
//!   an attribute or method on a value (`(.m x)`, `x.m`, `(. x m)`) are counted; name lookups (local bindings, arguments, definition names)
//!   are not counted even if spelled the same as a method name.
//! - Via-evidence follows only calls whose destination is decided within the same file or by an import, up to depth 4 (stopping at cycles, one entry per identical evidence).
//! - Definitions the caller passed as "stop" (definitions declared to own real I/O) are not entered — raw side effects beyond them are that definition's responsibility,
//!   so they are not counted as via-evidence of the caller. Evidence reaching by another path that does not go through a stop definition is counted as before.

use std::collections::{BTreeSet, HashMap, HashSet};

use super::model::{
    ArgumentValue, HyFileIndex, RawEvidence, RawEvidenceKind, RawMark, RawStep, RawStrength, RawVia, Range, Reference,
};
use super::position::Position;
use super::raw_catalog::{RawCatalog, RawCategory};

/// Upper limit on the depth of via propagation.
pub const RAW_VIA_MAX_DEPTH: usize = 4;
/// Upper limit on via-evidence held per definition.
pub const RAW_VIA_MAX_ITEMS: usize = 50;

/// Whether to follow via-evidence.
#[derive(Debug, Clone, Copy)]
pub enum ViaTrace<'a> {
    /// Do not compute via (a single-file run — via crosses files).
    Skip,
    /// Follow via. Do not enter definitions in `stops` (fully qualified names — the same spelling as a definition's `qualified_name`). If empty, follow everything.
    Through { stops: &'a BTreeSet<String> },
}

/// Name rule for matching (same as doeff-runner's mangle) — turns `-` into `_`, keeping leading `-` as is.
fn match_name(name: &str) -> String {
    let trimmed = name.trim_start_matches('-');
    let leading = &name[..name.len() - trimmed.len()];
    format!("{}{}", leading, trimmed.replace('-', "_"))
}

/// Converts a dotted name to matching form, segment by segment.
fn match_dotted(dotted: &str) -> String {
    dotted.split('.').map(match_name).collect::<Vec<_>>().join(".")
}

/// Whether a dotted name matches a pattern (prefix match at segment boundaries; a trailing `*` is a prefix match within a segment).
pub fn matches_pattern(full: &str, pattern: &str) -> bool {
    let name = match_dotted(full);
    if let Some(prefix) = pattern.strip_suffix('*') {
        return name.starts_with(&match_dotted(prefix));
    }
    let p = match_dotted(pattern);
    name == p || name.starts_with(&format!("{}.", p))
}

/// Whether a range contains a position (both ends inclusive).
fn contains(range: &Range, pos: &Position) -> bool {
    let after_start =
        pos.line > range.start.line || (pos.line == range.start.line && pos.character >= range.start.character);
    let before_end = pos.line < range.end.line || (pos.line == range.end.line && pos.character <= range.end.character);
    after_start && before_end
}

/// A pattern converted to matching form (a trailing `*` is a prefix match within a segment).
struct CompiledPattern {
    text: String,
    prefix_only: bool,
}

impl CompiledPattern {
    /// Converts a pattern to matching form.
    fn new(pattern: &str) -> CompiledPattern {
        match pattern.strip_suffix('*') {
            Some(prefix) => CompiledPattern { text: match_dotted(prefix), prefix_only: true },
            None => CompiledPattern { text: match_dotted(pattern), prefix_only: false },
        }
    }

    /// Whether a name in matching form matches (same rule as matches_pattern).
    fn matches(&self, name: &str) -> bool {
        if self.prefix_only {
            return name.starts_with(&self.text);
        }
        name.len() >= self.text.len()
            && name.starts_with(&self.text)
            && (name.len() == self.text.len() || name.as_bytes()[self.text.len()] == b'.')
    }
}

/// The catalog of one classification converted to matching form.
struct CompiledEntry {
    category: super::raw_catalog::RawCategory,
    patterns: Vec<CompiledPattern>,
    builtins: Vec<String>,
    /// Method name, the context module required for counting, and the upper limit on arguments excluding the receiver (the catalog's RawMethod).
    methods: Vec<(String, Vec<String>, Option<usize>)>,
}

/// The whole catalog converted to matching form (built once before scanning files).
struct CompiledCatalog {
    entries: Vec<CompiledEntry>,
    ignored: Vec<CompiledPattern>,
    exception_suffixes: Vec<String>,
    context_modules: Vec<(String, CompiledPattern)>,
}

impl CompiledCatalog {
    /// Converts the catalog to matching form.
    fn new(catalog: &RawCatalog) -> CompiledCatalog {
        let mut context_modules: Vec<(String, CompiledPattern)> = Vec::new();
        for entry in &catalog.categories {
            for method in &entry.methods {
                for module in &method.context {
                    if !context_modules.iter().any(|(m, _)| m == module) {
                        context_modules.push((module.clone(), CompiledPattern::new(module)));
                    }
                }
            }
        }
        CompiledCatalog {
            entries: catalog
                .categories
                .iter()
                .map(|entry| CompiledEntry {
                    category: entry.category,
                    patterns: entry.patterns.iter().map(|p| CompiledPattern::new(p)).collect(),
                    builtins: entry.builtins.iter().map(|b| match_name(b)).collect(),
                    methods: entry.methods.iter().map(|m| (match_name(&m.name), m.context.clone(), m.max_args)).collect(),
                })
                .collect(),
            ignored: catalog.ignored.iter().map(|n| CompiledPattern::new(n)).collect(),
            exception_suffixes: catalog.exception_suffixes.clone(),
            context_modules,
        }
    }
}

/// One import of a file converted to matching form.
struct CompiledImport {
    bound: Vec<String>,
    head: String,
}

/// Converts a file's imports to matching form (relative imports are outside the catalog, so they are dropped).
fn compile_imports(file: &HyFileIndex) -> Vec<CompiledImport> {
    file.imports
        .iter()
        .filter(|imp| !imp.module.starts_with('.'))
        .map(|imp| {
            let module = match_dotted(&imp.module);
            let bound = match_dotted(imp.alias.as_deref().or(imp.name.as_deref()).unwrap_or(&imp.module))
                .split('.')
                .map(str::to_string)
                .collect();
            let head = match &imp.name {
                None => module,
                Some(name) => format!("{}.{}", module, match_name(name)),
            };
            CompiledImport { bound, head }
        })
        .collect()
}

/// Converts the written dotted form of a reference (qualifier + name) to matching form.
fn chain_of(reference: &Reference) -> String {
    match &reference.qualifier {
        Some(qualifier) => match_dotted(&format!("{}.{}", qualifier, reference.name)),
        None => match_dotted(&reference.name),
    }
}

/// The result of resolving a reference to a full name via imports.
enum Expanded {
    /// A name resolved through an import (`time.sleep`)
    Import(String),
    /// An unimported, unqualified name lookup (a builtin, a name decided externally, or a local binding's name)
    Unbound(String),
    /// A lookup of a definition's name inside the file
    Local(String),
    /// An attribute or method name on a value not decided by imports (`(.stat p)`, `p.stat`, `(. p stat)`) — method evidence comes only from here
    Member(String),
}

/// Resolves a reference to a full name via the file's imports (uses the longest matching import; on equal length the one found first wins).
fn expand(imports: &[CompiledImport], reference: &Reference, local_names: &HashSet<String>) -> Expanded {
    // Do not confuse the method name in `.random rng` or `(. rng (random))` with an import of the same name, random.
    if reference.member && reference.qualifier.is_none() {
        return Expanded::Member(match_name(&reference.name));
    }
    let chain = chain_of(reference);
    let segments: Vec<&str> = chain.split('.').collect();
    let mut best: Option<(usize, &CompiledImport)> = None;
    for import in imports {
        let matched = import.bound.iter().enumerate().all(|(i, seg)| segments.get(i) == Some(&seg.as_str()));
        if !matched || best.is_some_and(|(length, _)| length >= import.bound.len()) {
            continue;
        }
        best = Some((import.bound.len(), import));
    }
    if let Some((length, import)) = best {
        let mut full = import.head.clone();
        for segment in segments.iter().skip(length) {
            full.push('.');
            full.push_str(segment);
        }
        return Expanded::Import(full);
    }
    let name = match_name(&reference.name);
    if reference.member {
        Expanded::Member(name)
    } else if local_names.contains(&name) {
        Expanded::Local(name)
    } else {
        Expanded::Unbound(name)
    }
}

/// Result of scanning a file — candidate evidence, and material for deciding the conditions for counting method evidence.
pub struct FileScan {
    evidence: Vec<RawEvidence>,
    /// The context module needed for method evidence, at the same index as evidence (None if not a method).
    method_contexts: Vec<Option<Vec<String>>>,
    imported_contexts: HashSet<String>,
    context_refs: HashMap<String, Vec<Range>>,
}

/// Scans a file's references once and collects candidate raw side-effect evidence (uses the catalog preprocessed into matching form).
fn scan_compiled(file: &HyFileIndex, catalog: &CompiledCatalog) -> FileScan {
    let local_names: HashSet<String> =
        file.definitions.iter().filter(|d| d.container.is_none()).map(|d| d.mangled.clone()).collect();
    let call_heads: HashSet<(u32, u32)> = file
        .calls
        .iter()
        .filter(|c| c.qualifier.is_none())
        .map(|c| (c.range.start.line, c.range.start.character))
        .collect();
    // A table for recognizing references followed by `.x` (the middle of a dotted name)
    let continued: HashSet<(u32, i64, String)> = file
        .references
        .iter()
        .filter_map(|r| {
            r.qualifier
                .as_ref()
                .map(|q| (r.range.start.line, i64::from(r.range.start.character) - 1, match_dotted(q)))
        })
        .collect();
    let imported_contexts: HashSet<String> = catalog
        .context_modules
        .iter()
        .filter(|(_, pattern)| file.imports.iter().any(|imp| pattern.matches(&match_dotted(&imp.module))))
        .map(|(module, _)| module.clone())
        .collect();
    let imports = compile_imports(file);
    let mut context_refs: HashMap<String, Vec<Range>> = HashMap::new();
    let mut evidence = Vec::new();
    let mut method_contexts = Vec::new();
    let seeded_constructors: HashSet<(u32, u32)> = file.calls.iter()
        .filter(|call| call.target.as_deref() == Some("random.Random"))
        .filter(|call| call.arguments.iter().find(|arg| arg.keyword.is_none() || arg.keyword.as_deref() == Some("x"))
            .is_some_and(|arg| arg.value == ArgumentValue::Explicit))
        .map(|call| (call.range.start.line, call.range.start.character))
        .collect();
    for reference in &file.references {
        let expanded = expand(&imports, reference, &local_names);
        if let Expanded::Import(full) = &expanded {
            for (module, pattern) in &catalog.context_modules {
                if pattern.matches(full) {
                    context_refs.entry(module.clone()).or_default().push(reference.range);
                }
            }
        }
        let end_key = (reference.range.end.line, i64::from(reference.range.end.character), chain_of(reference));
        if continued.contains(&end_key) {
            continue; // a segment in the middle of a dotted name
        }
        let at_call_head = call_heads.contains(&(reference.range.start.line, reference.range.start.character));
        let skip_import = match &expanded {
            Expanded::Import(full) => {
                let last = full.rsplit('.').next().unwrap_or("");
                catalog.exception_suffixes.iter().any(|suffix| last.ends_with(suffix.as_str()))
                    || catalog.ignored.iter().any(|pattern| pattern.matches(full))
            }
            _ => false,
        };
        for entry in &catalog.entries {
            if entry.category == RawCategory::Random {
                // Imports and type references do not draw random numbers. A Random with an explicit seed does not touch OS entropy either.
                let imported = file.imports.iter().any(|import| contains(&import.range, &reference.range.start));
                let seeded = seeded_constructors.contains(&(reference.range.start.line, reference.range.start.character));
                if reference.type_only || imported || seeded {
                    continue;
                }
            }
            if let Some((found, context)) = match_entry(entry, &expanded, skip_import, at_call_head, reference.call_arity, &file.path, reference.range) {
                evidence.push(found);
                method_contexts.push(context);
            }
        }
    }
    FileScan { evidence, method_contexts, imported_contexts, context_refs }
}

/// Compares one reference with one classification of the catalog (a method is weak evidence and carries the required context).
fn match_entry(
    entry: &CompiledEntry,
    expanded: &Expanded,
    skip_import: bool,
    at_call_head: bool,
    call_arity: Option<usize>,
    path: &str,
    range: Range,
) -> Option<(RawEvidence, Option<Vec<String>>)> {
    let evidence = |name: String, kind: RawEvidenceKind, strength: RawStrength| RawEvidence {
        category: entry.category,
        name,
        kind,
        strength,
        path: path.to_string(),
        range,
    };
    match expanded {
        Expanded::Import(full) => (!skip_import && entry.patterns.iter().any(|p| p.matches(full)))
            .then(|| (evidence(full.clone(), RawEvidenceKind::Name, RawStrength::Strong), None)),
        // A name lookup is not method evidence (the local binding name stat is not .stat).
        Expanded::Unbound(name) => (at_call_head && entry.builtins.iter().any(|b| b == name))
            .then(|| (evidence(name.clone(), RawEvidenceKind::Builtin, RawStrength::Strong), None)),
        Expanded::Local(_) => None,
        Expanded::Member(name) => method_match(entry, name, call_arity).map(|context| {
            (evidence(format!(".{}", name), RawEvidenceKind::Method, RawStrength::Weak), Some(context))
        }),
    }
}

/// Method name match and the context it requires. A call whose arguments (excluding the receiver) exceed the catalog's limit is not counted as a same-named method of another type
/// (string `.replace`). A reference with unknown arguments (not at the head of a call) is counted.
fn method_match(entry: &CompiledEntry, name: &str, call_arity: Option<usize>) -> Option<Vec<String>> {
    entry
        .methods
        .iter()
        .find(|(m, _, max_args)| m == name && !matches!((call_arity, max_args), (Some(arity), Some(max)) if arity > *max))
        .map(|(_, context, _)| context.clone())
}

/// Direct evidence within a definition's range (method evidence only when the context module is visible).
pub fn direct_evidence(scan: &FileScan, full_range: &Range) -> Vec<RawEvidence> {
    let context_visible = |module: &String| {
        scan.imported_contexts.contains(module)
            || scan.context_refs.get(module).is_some_and(|ranges| ranges.iter().any(|r| contains(full_range, &r.start)))
    };
    scan.evidence
        .iter()
        .zip(&scan.method_contexts)
        .filter(|(e, context)| {
            contains(full_range, &e.range.start)
                && match context {
                    None => true,
                    Some(modules) => modules.is_empty() || modules.iter().any(context_visible),
                }
        })
        .map(|(e, _)| e.clone())
        .collect()
}

/// A number that uniquely identifies a definition (file index and definition index).
type DefId = (usize, usize);

/// Lookup tables and memo for the whole index, for the via computation.
struct ViaWorld<'a> {
    files: &'a [HyFileIndex],
    /// Fully qualified name -> definition (the destination of a call's `target` — name resolution lives in one place, qualify.rs)
    by_qualified: HashMap<&'a str, Vec<DefId>>,
    direct: Vec<Vec<Vec<RawEvidence>>>,
    callees: HashMap<DefId, Vec<DefId>>,
    /// Fully qualified names of definitions not entered (`stops` of `ViaTrace::Through`)
    stops: &'a BTreeSet<String>,
    /// (definition, remaining steps) -> whether, within that many steps, it can reach a definition holding direct evidence (an upper-bound estimate that does not exclude definitions being visited)
    reachable: HashMap<(DefId, usize), bool>,
}

impl<'a> ViaWorld<'a> {
    /// Builds the lookup tables for the whole index and computes the direct evidence of all definitions.
    fn new(files: &'a [HyFileIndex], direct: Vec<Vec<Vec<RawEvidence>>>, stops: &'a BTreeSet<String>) -> ViaWorld<'a> {
        let mut by_qualified: HashMap<&'a str, Vec<DefId>> = HashMap::new();
        for (fi, file) in files.iter().enumerate() {
            for (di, definition) in file.definitions.iter().enumerate() {
                by_qualified.entry(definition.qualified_name.as_str()).or_default().push((fi, di));
            }
        }
        ViaWorld { files, by_qualified, direct, callees: HashMap::new(), stops, reachable: HashMap::new() }
    }

    /// Definitions that calls within a definition's range end up at (excluding nested ones inside itself and non-entered definitions `stops`; in written order, without duplicates).
    fn callees_of(&mut self, id: DefId) -> Vec<DefId> {
        if let Some(known) = self.callees.get(&id) {
            return known.clone();
        }
        let file = &self.files[id.0];
        let range = file.definitions[id.1].full_range;
        let mut found: Vec<DefId> = Vec::new();
        for call in &file.calls {
            if !contains(&range, &call.range.start) {
                continue;
            }
            let Some(targets) = call.target.as_deref().and_then(|name| self.by_qualified.get(name)) else {
                continue;
            };
            for &target in targets {
                let target_def = &self.files[target.0].definitions[target.1];
                let inside = target.0 == id.0 && contains(&range, &target_def.range.start);
                let stopped = self.stops.contains(&target_def.qualified_name);
                if !inside && !stopped && !found.contains(&target) {
                    found.push(target);
                }
            }
        }
        self.callees.insert(id, found.clone());
        found
    }

    /// Whether, by calls within `steps` steps from a definition, it can reach a definition holding direct evidence. Estimates broadly, as it does not exclude definitions being visited,
    /// so false means it truly cannot reach — skipping branches where this is false does not change the via search result.
    fn can_reach(&mut self, id: DefId, steps: usize) -> bool {
        if steps == 0 {
            return false;
        }
        if let Some(&known) = self.reachable.get(&(id, steps)) {
            return known;
        }
        // The remaining steps shrink with each level of the query, so we never return to the same (definition, steps) query (it stops even with cycles)
        let mut found = false;
        for callee in self.callees_of(id) {
            if !self.direct[callee.0][callee.1].is_empty() || self.can_reach(callee, steps - 1) {
                found = true;
                break;
            }
        }
        self.reachable.insert((id, steps), found);
        found
    }

    /// Collects via-evidence depth-first (stops at cycles by the definitions in the path; one entry per identical evidence position).
    fn collect_via(
        &mut self,
        id: DefId,
        through: &mut Vec<DefId>,
        visiting: &mut Vec<DefId>,
        out: &mut Vec<RawVia>,
        seen: &mut HashSet<(String, u32, u32)>,
    ) {
        if through.len() >= RAW_VIA_MAX_DEPTH || out.len() >= RAW_VIA_MAX_ITEMS {
            return;
        }
        for callee in self.callees_of(id) {
            if visiting.contains(&callee) {
                continue;
            }
            // A callee that cannot reach evidence within the remaining steps adds nothing even if visited
            let remaining = RAW_VIA_MAX_DEPTH - (through.len() + 1);
            if self.direct[callee.0][callee.1].is_empty() && !self.can_reach(callee, remaining) {
                continue;
            }
            through.push(callee);
            for evidence in &self.direct[callee.0][callee.1] {
                let at = (evidence.path.clone(), evidence.range.start.line, evidence.range.start.character);
                if !seen.contains(&at) && out.len() < RAW_VIA_MAX_ITEMS {
                    seen.insert(at);
                    let steps = through
                        .iter()
                        .map(|&(fi, di)| RawStep {
                            path: self.files[fi].path.clone(),
                            index: di,
                            name: self.files[fi].definitions[di].name.clone(),
                        })
                        .collect();
                    out.push(RawVia { through: steps, evidence: evidence.clone() });
                }
            }
            visiting.push(callee);
            self.collect_via(callee, through, visiting, out, seen);
            visiting.pop();
            through.pop();
        }
    }
}

/// Computes the direct evidence of definitions in all files in parallel, split by file (each file is independent, so the order does not change).
fn direct_in_parallel(files: &[HyFileIndex], compiled: &CompiledCatalog) -> Vec<Vec<Vec<RawEvidence>>> {
    let per_file = |file: &HyFileIndex| -> Vec<Vec<RawEvidence>> {
        let scan = scan_compiled(file, compiled);
        file.definitions.iter().map(|d| direct_evidence(&scan, &d.full_range)).collect()
    };
    let workers = std::thread::available_parallelism().map(|n| n.get()).unwrap_or(1).max(1);
    let chunk = files.len().div_ceil(workers).max(1);
    std::thread::scope(|scope| {
        let handles: Vec<_> =
            files.chunks(chunk).map(|part| scope.spawn(move || part.iter().map(per_file).collect::<Vec<_>>())).collect();
        handles
            .into_iter()
            .zip(files.chunks(chunk))
            .flat_map(|(handle, part)| {
                // Even if a scan panics, do not stop the whole run; proceed with that file's definitions having no evidence
                handle.join().unwrap_or_else(|_| part.iter().map(|f| vec![Vec::new(); f.definitions.len()]).collect())
            })
            .collect()
    })
}

/// Fills the definitions of all files in the index with direct evidence and (with `with_via`) via-evidence. Via crosses files, so
/// only a full `--root` run computes it (a single-file or `--file` run leaves it empty with `ViaTrace::Skip`).
pub fn annotate(files: &mut [HyFileIndex], catalog: &RawCatalog, trace: ViaTrace) {
    let compiled = CompiledCatalog::new(catalog);
    let direct = direct_in_parallel(files, &compiled);
    let via: Vec<Vec<Vec<RawVia>>> = match trace {
        ViaTrace::Through { stops } => {
        let mut world = ViaWorld::new(files, direct.clone(), stops);
        let mut all = Vec::with_capacity(files.len());
        for fi in 0..files.len() {
            let mut per_file = Vec::with_capacity(files[fi].definitions.len());
            for di in 0..files[fi].definitions.len() {
                let mut out = Vec::new();
                world.collect_via((fi, di), &mut Vec::new(), &mut vec![(fi, di)], &mut out, &mut HashSet::new());
                per_file.push(out);
            }
            all.push(per_file);
        }
        all
        }
        ViaTrace::Skip => files.iter().map(|f| vec![Vec::new(); f.definitions.len()]).collect(),
    };
    for ((file, direct_per_def), via_per_def) in files.iter_mut().zip(direct).zip(via) {
        for ((definition, direct), via) in file.definitions.iter_mut().zip(direct_per_def).zip(via_per_def) {
            definition.raw = RawMark { direct, via };
        }
    }
}
