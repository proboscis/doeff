//! Fully qualified names (version 4) — a definition's `qualified_name` and a call's `target` (the fully qualified name of the callee). From version 5, by the same name resolution,
//! it also fills in the `target` of declared effects (`effects`), names inside type annotations (`names` of `param_types` / `answer_type`), and the effects an effect clause handles (`handles`).
//! (same as above).
//!
//! Fully qualified name = the module's dotted name + the container's name (if any) + the definition's name. Every separator is a mangled spelling
//! (`pkg.mod.Store.put_row`). A call's `target` is the result of name resolution decided only from that file's contents (module, definitions, imports),
//! without looking at other files. So it has the same value whether run over the whole `--root`, with `--file`, or with `--stdin`,
//! and the reader finds the callee's definition by string match between `target` and `qualified_name`, and finds callers by the reverse of the same match.
//! `target` is not necessarily a Hy definition (a Python function or a name from an external package also becomes its fully qualified name) — if it matches the index's
//! `qualified_name`, it is a Hy definition.
//!
//! Resolution order (the same rules as steps a / b of doeff-runner's resolve.ts and the via-evidence in raw.rs):
//! 1. A definition in the same file — an unqualified name is a top-level definition, `q.name` is a definition inside container q.
//! 2. The file's imports (`require` excluded — a macro is not made a callee's definition) — the first import that binds is used.
//!    `x` of `(import m [x])` is `m.x`, `m.sub.f` of `(import m)` is `m.sub.f`, `C.f` of `(import m [C])` is `m.C.f`.
//!    A form that calls the module itself (`(m)` for `(import m)`) has no callee definition, so null.
//! 3. Anything else (builtins, special forms, local bindings, arguments, attributes on values) is null.

use std::collections::{HashMap, HashSet};

use super::analyze::mangle;
use super::model::{Definition, HyFileIndex, Import, NameRef, TypeNote};

/// Mangles a dotted name separator by separator (empty separators are dropped).
fn mangle_dotted(dotted: &str) -> String {
    dotted.split('.').filter(|part| !part.is_empty()).map(mangle).collect::<Vec<_>>().join(".")
}

/// Joins separators with `.` (empty separators — the module name "" of `__init__` directly under root — are dropped).
fn join(parts: &[&str]) -> String {
    parts.iter().filter(|part| !part.is_empty()).copied().collect::<Vec<_>>().join(".")
}

/// The fully qualified name of a definition (module + container + name).
pub fn qualified_name(module: &str, definition: &Definition) -> String {
    let module = mangle_dotted(module);
    match &definition.container {
        None => join(&[&module, &definition.mangled]),
        Some(container) => join(&[&module, &mangle(container), &definition.mangled]),
    }
}

/// Whether the file is a package's `__init__` (the base of relative imports changes).
fn is_package_init(path: &str) -> bool {
    let base = std::path::Path::new(path).file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default();
    matches!(base.as_str(), "__init__.hy" | "__init__.hyk" | "__init__.hyp")
}

/// Turns a relative import into an absolute dotted name, based on the module of the file that wrote it.
fn absolute_module(file_path: &str, file_module: &str, module: &str) -> String {
    let dots = module.len() - module.trim_start_matches('.').len();
    if dots == 0 {
        return module.to_string();
    }
    let base: Vec<&str> = file_module.split('.').filter(|p| !p.is_empty()).collect();
    let package: Vec<&str> = if is_package_init(file_path) { base } else { base[..base.len().saturating_sub(1)].to_vec() };
    let keep = package.len().saturating_sub(dots - 1);
    let mut parts: Vec<String> = package[..keep].iter().map(|s| s.to_string()).collect();
    let rest = &module[dots..];
    if !rest.is_empty() {
        parts.extend(rest.split('.').map(str::to_string));
    }
    parts.join(".")
}

/// The name an import creates in the file (alias > name > module).
fn bound_name(import: &Import) -> &str {
    import.alias.as_deref().or(import.name.as_deref()).unwrap_or(&import.module)
}

/// One import in the form looked up during resolution (`require` is not included — a macro is not made a callee's definition).
struct BoundImport {
        /// The name the import creates in the file (mangled dotted).
    bound: String,
        /// The import's module (a relative import is made absolute based on the module of the file that wrote it, mangled dotted).
    from: String,
        /// The `x` of `(import m [x])` (mangled name). `(import m)` — the module itself — is None.
    imported: Option<String>,
}

/// The place for name resolution — holds one file's module, definitions, and imports (does not look at other files) as an easy-to-look-up table.
///
/// A file has tens of thousands of calls and references (30,000+ in a 10,000-line file); if each one iterated all definitions and imports and allocated mangled Strings,
/// it would cost "number of calls x (definitions + imports)" (`index_source` took 4.3 seconds for 10,000 lines). The table is built once, and resolution only looks things up.
struct Scope<'a> {
        /// This file's module (mangled dotted).
    own_module: String,
        /// Mangled names of top-level definitions (those with no container).
    top_level: HashSet<&'a str>,
        /// Container (mangled name) -> mangled names of the definitions inside it.
    contained: HashMap<String, HashSet<&'a str>>,
        /// Imports that are not `require` (in written order — a qualified call uses the first match).
    imports: Vec<BoundImport>,
        /// Index of the first import that binds the same name (looked up by an unqualified call — only the first hit is used, later imports are not looked at).
    first_by_bound: HashMap<String, usize>,
}

impl<'a> Scope<'a> {
    fn new(path: &str, module: &str, definitions: &'a [Definition], imports: &[Import]) -> Scope<'a> {
        let mut top_level = HashSet::new();
        let mut contained: HashMap<String, HashSet<&'a str>> = HashMap::new();
        for definition in definitions {
            match &definition.container {
                None => {
                    top_level.insert(definition.mangled.as_str());
                }
                Some(container) => {
                    contained.entry(mangle(container)).or_default().insert(definition.mangled.as_str());
                }
            }
        }
        let imports: Vec<BoundImport> = imports
            .iter()
            .filter(|import| !import.is_require)
            .map(|import| BoundImport {
                bound: mangle_dotted(bound_name(import)),
                from: mangle_dotted(&absolute_module(path, module, &import.module)),
                imported: import.name.as_deref().map(mangle),
            })
            .collect();
        let mut first_by_bound = HashMap::new();
        for (index, import) in imports.iter().enumerate() {
            first_by_bound.entry(import.bound.clone()).or_insert(index);
        }
        Scope { own_module: mangle_dotted(module), top_level, contained, imports, first_by_bound }
    }

        /// The fully qualified name of a written dotted name (`ReadInput`, `intent.ReadInput`).
    fn resolve_written(&self, written: &str) -> Option<String> {
        let (qualifier, name) = match written.rsplit_once('.') {
            Some((qualifier, name)) if !qualifier.is_empty() && !name.is_empty() => (Some(qualifier), name),
            Some(_) | None => (None, written),
        };
        self.resolve(qualifier, &mangle(name))
    }

        /// The fully qualified name of a qualifier (the written spelling) and a mangled name (resolution order is as in the module description).
    fn resolve(&self, qualifier: Option<&str>, name: &str) -> Option<String> {
        match qualifier {
            None => {
                if self.top_level.contains(name) {
                    return Some(join(&[&self.own_module, name]));
                }
                let import = &self.imports[*self.first_by_bound.get(name)?];
                                let imported = import.imported.as_deref()?; // the module itself is not a callee's definition
                Some(join(&[&import.from, imported]))
            }
            Some(qualifier) => {
                let q = mangle_dotted(qualifier);
                if self.contained.get(&q).is_some_and(|names| names.contains(name)) {
                    return Some(join(&[&self.own_module, &q, name]));
                }
                self.imports.iter().find_map(|import| match import.imported.as_deref() {
                                        // `m.f` and `m.sub.f` of `(import m)`
                    None => {
                        let rest = if q == import.bound { Some("") } else { q.strip_prefix(import.bound.as_str()).and_then(|rest| rest.strip_prefix('.')) };
                        rest.map(|rest| join(&[&import.from, rest, name]))
                    }
                                        // `sub.f` of `(import pkg [sub])` and `Class.f` of `(import m [Class])` — both are `from.sub.f`
                    Some(imported) => (q == import.bound).then(|| join(&[&import.from, imported, name])),
                })
            }
        }
    }

        /// Fills in the target for one name.
    fn fill(&self, name_ref: &mut NameRef) {
        name_ref.target = self.resolve_written(&name_ref.name);
    }

        /// Fills in the target for names inside type annotations.
    fn fill_type(&self, note: &mut TypeNote) {
        for name_ref in &mut note.names {
            self.fill(name_ref);
        }
    }
}

/// Fills in fully qualified names in a file's index — a definition's `qualified_name`, a call's `target`, and the `target` of names in a definition's effects, types, and effect clauses.
pub fn link(file: &mut HyFileIndex) {
    for definition in &mut file.definitions {
        definition.qualified_name = qualified_name(&file.module, definition);
    }
        // Resolution is done over a copy of the definitions from before filling (so the definitions list is not read while writing the definitions' fields)
    let definitions = file.definitions.clone();
    let scope = Scope::new(&file.path, &file.module, &definitions, &file.imports);
    for call in &mut file.calls {
        call.target = scope.resolve(call.qualifier.as_deref(), &call.mangled);
    }
    for reference in &mut file.references {
        reference.target = scope.resolve(reference.qualifier.as_deref(), &reference.mangled);
    }
    for definition in &mut file.definitions {
        for effect in definition.effects.iter_mut().flatten() {
            scope.fill(effect);
        }
        for param in &mut definition.param_types {
            scope.fill_type(&mut param.type_note);
        }
        if let Some(answer) = &mut definition.answer_type {
            scope.fill_type(answer);
        }
        if let Some(handles) = &mut definition.handles {
            scope.fill(handles);
        }
    }
}
