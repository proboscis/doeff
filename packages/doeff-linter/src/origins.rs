//! The single resolver that resolves where a name is defined (the canonical name).
//!
//! The same object can be imported both from the module that defined it and from an entry point that re-exports it (`from x import Y as Y`, the name -> module table of `__getattr__`, Hy's
//! `(import x [Y])`). The index name (`qualify.rs` of doeff-indexer) is the spelling that chains the written import statements, and merely re-importing from an entry point
//! changes the spelling. The rules that match by name (the effect-section keys of DOEFF143, 157, 164, 167, 206, the table matching, and the layer judgment), and DOEFF002 (`rules::class_inheritance`), which resolves a class's base
//! to a class in the same repo, use the re-export tracing here and read names that do not depend on the spelling.
//!
//! - Reads the bindings of a Python module (`import`, `from … import …`, including inside `if TYPE_CHECKING:`), and for a module that has `__getattr__`, the
//!   table from string names to modules (the `_LAZY_EXPORTS` shape — a lazily loaded entry point).
//! - For Hy modules, reads the doeff-indexer index (top-level definitions and imports).
//! - The starting dirs for looking up a module's file are decided by the caller (DOEFF002 only inside the repo; for effect names, the project root and the path dependencies outside it —
//!   the paths in `[tool.uv.sources]` of `pyproject.toml`, the dirs in `.pth` of `.venv`, and site-packages).
//! - The tracing goes up to `MAX_REEXPORT_DEPTH` steps. A name that cannot be resolved returns None and is not silently reduced to the spelling (the caller reports it as a finding).

use std::collections::{HashMap, HashSet};
use std::path::{Path, PathBuf};
use std::sync::Arc;

use doeff_indexer::hy_index::{self, HyFileIndex};
use rustpython_ast::{Constant, Expr, Mod, Stmt};
use rustpython_parser::{parse, Mode};

use crate::utils::each_statement;

/// The upper limit on the steps of tracing a re-export.
pub const MAX_REEXPORT_DEPTH: usize = 8;

/// The shapes of files that a Python module is looked up as (`a/b.py` or `a/b/__init__.py`).
pub const PYTHON_FILES: &[ModuleFile] = &[ModuleFile::Module("py"), ModuleFile::Package("py")];

/// The shapes of files that a Python and Hy module is looked up as (Hy first — the `.pyi` next to a Hy module is a copy of the types, not the definition).
pub const SOURCE_FILES: &[ModuleFile] =
    &[ModuleFile::Module("hy"), ModuleFile::Module("py"), ModuleFile::Package("py"), ModuleFile::Package("hy")];

/// One file shape of a module (with an extension).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ModuleFile {
    /// `a/b.<ext>`
    Module(&'static str),
    /// `a/b/__init__.<ext>` (a module with no name is the starting point's `__init__.<ext>`)
    Package(&'static str),
}

/// Name bindings in a file.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Binding {
    /// `import a.b` (the bound name is `a` — dotted is `a`), `import a.b as c` (dotted is `a.b`).
    Module { dotted: String },
    /// The name of `from <module> import <name>` (level = the number of dots of a relative import).
    Member { module: String, level: u32, name: String },
    /// The same name was bound to different things (an escape route of a try import, and so on) — not decided either way.
    Ambiguous,
}

/// One step beyond a re-export (`from <module> import <name>` — level is the number of dots of a relative import, 0 is absolute).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Hop {
    pub module: String,
    pub level: u32,
    pub name: String,
}

/// The import bindings of a Python module (all import statements directly under the module and nested inside functions, classes, if, and so on).
pub fn python_bindings(body: &[Stmt]) -> HashMap<String, Binding> {
    let mut bindings = HashMap::new();
    each_statement(body, &mut |stmt| match stmt {
        Stmt::Import(import) => {
            for alias in &import.names {
                let (bound, dotted) = match &alias.asname {
                    Some(asname) => (asname.to_string(), alias.name.to_string()),
                    None => {
                        let head = alias.name.split('.').next().unwrap_or_default().to_string();
                        (head.clone(), head)
                    }
                };
                bind(&mut bindings, bound, Binding::Module { dotted });
            }
        }
        Stmt::ImportFrom(from) => {
            let level = from.level.as_ref().map_or(0, |level| level.to_u32());
            let module = from.module.as_ref().map(|module| module.to_string()).unwrap_or_default();
            for alias in &from.names {
                if alias.name.as_str() == "*" {
                    continue;
                }
                let bound = alias.asname.as_ref().unwrap_or(&alias.name).to_string();
                bind(&mut bindings, bound, Binding::Member { module: module.clone(), level, name: alias.name.to_string() });
            }
        }
        _ => {}
    });
    bindings
}

fn bind(bindings: &mut HashMap<String, Binding>, name: String, binding: Binding) {
    match bindings.get(&name) {
        Some(existing) if *existing != binding => {
            bindings.insert(name, Binding::Ambiguous);
        }
        Some(_) => {}
        None => {
            bindings.insert(name, binding);
        }
    }
}

/// The table of names loaded lazily by `__getattr__` (name -> module) — only when `def __getattr__` exists directly under the module, among the dict literals directly under it,
/// reads those whose keys and values are all strings (`_LAZY_EXPORTS = {"LeaseOp": "pkg._shared.model", …}`).
pub fn lazy_exports(body: &[Stmt]) -> HashMap<String, String> {
    let has_getattr = body.iter().any(|stmt| matches!(stmt, Stmt::FunctionDef(def) if def.name.as_str() == "__getattr__"));
    let mut out = HashMap::new();
    if !has_getattr {
        return out;
    }
    for stmt in body {
        let value = match stmt {
            Stmt::Assign(assign) => assign.value.as_ref(),
            Stmt::AnnAssign(assign) => match &assign.value {
                Some(value) => value.as_ref(),
                None => continue,
            },
            _ => continue,
        };
        let Expr::Dict(dict) = value else { continue };
        let pairs: Option<Vec<(String, String)>> =
            dict.keys.iter().zip(&dict.values).map(|(key, value)| Some((string(key.as_ref()?)?, string(value)?))).collect();
        out.extend(pairs.into_iter().flatten());
    }
    out
}

fn string(expr: &Expr) -> Option<String> {
    match expr {
        Expr::Constant(constant) => match &constant.value {
            Constant::Str(text) => Some(text.clone()),
            _ => None,
        },
        _ => None,
    }
}

/// One step beyond the re-export of the name `name` — `from x import name` (including an aliased binding) or a row of the lazily loaded name table. Does not trace a binding of the module itself or
/// a binding that cannot be decided.
pub fn reexport_hop(bindings: &HashMap<String, Binding>, lazy: &HashMap<String, String>, name: &str) -> Option<Hop> {
    match bindings.get(name) {
        Some(Binding::Member { module, level, name }) => Some(Hop { module: module.clone(), level: *level, name: name.clone() }),
        Some(Binding::Module { .. } | Binding::Ambiguous) => None,
        None => lazy.get(name).map(|module| Hop { module: module.clone(), level: 0, name: name.to_string() }),
    }
}

pub fn dotted_parts(dotted: &str) -> impl Iterator<Item = String> + '_ {
    dotted.split('.').filter(|part| !part.is_empty()).map(str::to_string)
}

/// Looks up a module's path from the starting dir (tries in the order of `files`).
pub fn lookup(root: &Path, parts: &[String], files: &[ModuleFile]) -> Option<PathBuf> {
    let base = parts.iter().fold(root.to_path_buf(), |path, part| path.join(part));
    files.iter().find_map(|file| {
        let path = match file {
            ModuleFile::Module(_) if parts.is_empty() => return None,
            ModuleFile::Module(ext) => base.with_extension(ext),
            ModuleFile::Package(ext) => base.join(format!("__init__.{}", ext)),
        };
        path.is_file().then_some(path)
    })
}

/// Among the dirs above the file being read, those that do not have `__init__.py` (dirs that become import starting points — a package dir is not a starting point), and its `src`
/// (nearest first). Up to `bound` (the repo root), or if absent, up to a dir that has `.git`.
pub fn import_roots_within(from: &Path, bound: Option<&Path>) -> Vec<PathBuf> {
    let mut roots = Vec::new();
    let mut dir = from.parent();
    while let Some(current) = dir {
        if !current.join("__init__.py").is_file() {
            for root in [current.to_path_buf(), current.join("src")] {
                if !roots.contains(&root) {
                    roots.push(root);
                }
            }
        }
        let at_root = match bound {
            Some(root) => current == root,
            None => current.join(".git").exists(),
        };
        if at_root {
            break;
        }
        dir = current.parent();
    }
    roots
}

/// The facts of name definitions and re-exports of one Python module.
struct PythonModule {
    /// Names defined directly under the module (class, function, assignment target names).
    defined: HashSet<String>,
    bindings: HashMap<String, Binding>,
    lazy: HashMap<String, String>,
    /// The target of `from <module> import *` (the shape a moved module's shell shows to the type checker) — searched in order when the name is not found elsewhere.
    stars: Vec<Hop>,
}

impl PythonModule {
    fn of(ast: &Mod) -> Self {
        let Mod::Module(module) = ast else {
            return Self { defined: HashSet::new(), bindings: HashMap::new(), lazy: HashMap::new(), stars: Vec::new() };
        };
        let mut defined = HashSet::new();
        for stmt in &module.body {
            match stmt {
                Stmt::ClassDef(def) => {
                    defined.insert(def.name.to_string());
                }
                Stmt::FunctionDef(def) => {
                    defined.insert(def.name.to_string());
                }
                Stmt::AsyncFunctionDef(def) => {
                    defined.insert(def.name.to_string());
                }
                Stmt::Assign(assign) => defined.extend(assign.targets.iter().filter_map(name_of)),
                Stmt::AnnAssign(assign) => defined.extend(name_of(&assign.target)),
                _ => {}
            }
        }
        let mut stars = Vec::new();
        each_statement(&module.body, &mut |stmt| {
            if let Stmt::ImportFrom(from) = stmt {
                if from.names.iter().any(|alias| alias.name.as_str() == "*") {
                    let level = from.level.as_ref().map_or(0, |level| level.to_u32());
                    let module = from.module.as_ref().map(|module| module.to_string()).unwrap_or_default();
                    stars.push(Hop { module, level, name: "*".to_string() });
                }
            }
        });
        Self { defined, bindings: python_bindings(&module.body), lazy: lazy_exports(&module.body), stars }
    }
}

fn name_of(expr: &Expr) -> Option<String> {
    match expr {
        Expr::Name(name) => Some(name.id.to_string()),
        _ => None,
    }
}

/// The reading of one module file.
enum Facts {
    Python(PythonModule),
    Hy(Arc<HyFileIndex>),
    Unreadable,
}

/// Resolves a name of an effect and the like (full name `<module>.<name>`) to the canonical name of its definition. Resolved once per name and per file within one judgment.
pub struct Origins<'a> {
    project_root: PathBuf,
    /// The starting dirs for looking up an absolute module (nearest first).
    roots: Vec<PathBuf>,
    /// The index of the project's Hy files (path from root -> index) — used without re-reading.
    project_hy: &'a HashMap<String, HyFileIndex>,
    /// The modules of the project's Hy files (index name — `-` in a dir becomes `_`) -> path from root.
    project_modules: HashMap<String, String>,
    files: HashMap<PathBuf, Arc<Facts>>,
    /// (The dir of the file where the name was written, name) -> the resolved name.
    names: HashMap<(Option<PathBuf>, String), Option<String>>,
}

impl<'a> Origins<'a> {
    /// The resolver that looks up the project root `root`'s modules and the sources of path dependencies outside it.
    pub fn new(root: &Path, project_hy: &'a HashMap<String, HyFileIndex>) -> Self {
        let mut roots: Vec<PathBuf> = Vec::new();
        let mut push = |dir: PathBuf| {
            let dir = dir.canonicalize().unwrap_or(dir);
            if dir.is_dir() && !roots.contains(&dir) {
                roots.push(dir);
            }
        };
        push(root.to_path_buf());
        push(root.join("src"));
        for dir in uv_source_dirs(root) {
            push(dir.join("src"));
            push(dir);
        }
        for dir in venv_dirs(root) {
            push(dir);
        }
        let project_modules = project_hy.iter().map(|(rel, index)| (index.module.clone(), rel.clone())).collect();
        Self {
            project_root: root.canonicalize().unwrap_or_else(|_| root.to_path_buf()),
            roots,
            project_hy,
            project_modules,
            files: HashMap::new(),
            names: HashMap::new(),
        }
    }

    /// Adds a starting dir (for the checks, and for other locations the caller knows).
    pub fn with_roots(mut self, extra: impl IntoIterator<Item = PathBuf>) -> Self {
        for dir in extra {
            if !self.roots.contains(&dir) {
                self.roots.push(dir);
            }
        }
        self
    }

    /// The canonical name of the definition of the full name `name` (the defining module + name). None when the module's file is not found, or the name's definition is not found and no traceable re-export exists.
    pub fn origin(&mut self, name: &str) -> Option<String> {
        self.origin_from(name, None)
    }

    /// The same as `origin` — also looks up the import starting dirs above the file where the name was written (path from the project root) (packages in nested dirs).
    pub fn origin_from(&mut self, name: &str, written_in: Option<&str>) -> Option<String> {
        let from = written_in.map(|rel| self.project_root.join(rel));
        let key = (from.as_ref().and_then(|f| f.parent().map(Path::to_path_buf)), name.to_string());
        if let Some(hit) = self.names.get(&key) {
            return hit.clone();
        }
        let found = name.rsplit_once('.').and_then(|(module, short)| self.walk(module, short, 0, from.as_deref()));
        self.names.insert(key, found.clone());
        found
    }

    /// The module's file — looked up in the order of the project's Hy module (index name), the starting dirs above the file where the name was written, and the starting dirs of the project and path dependencies.
    fn module_file(&self, module: &str, from: Option<&Path>) -> Option<PathBuf> {
        if let Some(rel) = self.project_modules.get(module) {
            return Some(self.project_root.join(rel));
        }
        let parts: Vec<String> = dotted_parts(module).collect();
        let bound = from.filter(|f| f.starts_with(&self.project_root)).map(|_| self.project_root.as_path());
        let near = from.map(|f| import_roots_within(f, bound)).unwrap_or_default();
        near.iter().chain(&self.roots).find_map(|root| lookup(root, &parts, SOURCE_FILES))
    }

    fn walk(&mut self, module: &str, name: &str, depth: usize, from: Option<&Path>) -> Option<String> {
        if depth > MAX_REEXPORT_DEPTH {
            return None;
        }
        let file = self.module_file(module, from)?;
        let facts = self.facts(&file);
        let package = file.file_stem().is_some_and(|stem| stem == "__init__");
        let next = match facts.as_ref() {
            Facts::Python(python) => {
                if python.defined.contains(name) {
                    return Some(format!("{}.{}", module, name));
                }
                match reexport_hop(&python.bindings, &python.lazy, name) {
                    Some(hop) => hop,
                    None => {
                        let stars = python.stars.clone();
                        return stars.iter().find_map(|star| {
                            let target = absolute(module, package, star)?;
                            self.walk(&target, name, depth + 1, Some(&file))
                        });
                    }
                }
            }
            Facts::Hy(index) => {
                let mangled = hy_index::mangle(name);
                if index.definitions.iter().any(|d| d.container.is_none() && d.mangled == mangled) {
                    return Some(format!("{}.{}", module, name));
                }
                let import = index.imports.iter().find(|i| {
                    !i.is_require && i.name.is_some() && hy_index::mangle(i.alias.as_deref().or(i.name.as_deref()).unwrap_or_default()) == mangled
                })?;
                let written = import.module.trim_start_matches('.');
                Hop {
                    module: written.to_string(),
                    level: (import.module.len() - written.len()) as u32,
                    name: hy_index::mangle(import.name.as_deref().unwrap_or_default()),
                }
            }
            Facts::Unreadable => return None,
        };
        let target = absolute(module, package, &next)?;
        self.walk(&target, &next.name, depth + 1, Some(&file))
    }

    fn facts(&mut self, file: &Path) -> Arc<Facts> {
        if let Some(hit) = self.files.get(file) {
            return Arc::clone(hit);
        }
        let hy_file = file.extension().is_some_and(|ext| ext == "hy");
        let facts = if hy_file {
            let rel = file.strip_prefix(&self.project_root).ok().map(|rel| rel.to_string_lossy().replace('\\', "/"));
            match rel.and_then(|rel| self.project_hy.get(&rel)) {
                Some(index) => Facts::Hy(Arc::new(index.clone())),
                None => Facts::Hy(Arc::new(hy_index::index_file(file.parent().unwrap_or(file), file))),
            }
        } else {
            std::fs::read_to_string(file)
                .ok()
                .and_then(|source| parse(&source, Mode::Module, &file.to_string_lossy()).ok())
                .map_or(Facts::Unreadable, |ast| Facts::Python(PythonModule::of(&ast)))
        };
        let facts = Arc::new(facts);
        self.files.insert(file.to_path_buf(), Arc::clone(&facts));
        facts
    }
}

/// Converts a relative import (level steps) to an absolute module, from the module that wrote it (for a package's `__init__`, the package itself).
fn absolute(module: &str, package: bool, hop: &Hop) -> Option<String> {
    if hop.level == 0 {
        return Some(hop.module.clone());
    }
    let mut base: Vec<&str> = module.split('.').filter(|p| !p.is_empty()).collect();
    if !package {
        base.pop()?;
    }
    for _ in 1..hop.level {
        base.pop()?;
    }
    base.extend(hop.module.split('.').filter(|p| !p.is_empty()));
    Some(base.join("."))
}

/// The dir of a path dependency in `[tool.uv.sources]` of `pyproject.toml` (a path relative to root is from root).
fn uv_source_dirs(root: &Path) -> Vec<PathBuf> {
    let Ok(text) = std::fs::read_to_string(root.join("pyproject.toml")) else { return Vec::new() };
    let Ok(value) = text.parse::<toml::Table>() else { return Vec::new() };
    let Some(sources) = value.get("tool").and_then(|t| t.get("uv")).and_then(|u| u.get("sources")).and_then(|s| s.as_table()) else {
        return Vec::new();
    };
    sources.values().filter_map(|source| source.get("path")?.as_str()).map(|path| root.join(path)).collect()
}

/// The dirs of `.venv`'s site-packages, and the dirs listed by the `.pth` files there (lines starting with `import` and comments are not read).
fn venv_dirs(root: &Path) -> Vec<PathBuf> {
    let Ok(libs) = std::fs::read_dir(root.join(".venv").join("lib")) else { return Vec::new() };
    let mut out = Vec::new();
    let mut site_dirs: Vec<PathBuf> = libs.filter_map(Result::ok).map(|e| e.path().join("site-packages")).filter(|p| p.is_dir()).collect();
    site_dirs.sort();
    for site in site_dirs {
        let mut pths: Vec<PathBuf> = std::fs::read_dir(&site)
            .map(|entries| entries.filter_map(Result::ok).map(|e| e.path()).filter(|p| p.extension().is_some_and(|x| x == "pth")).collect())
            .unwrap_or_default();
        pths.sort();
        for pth in pths {
            let Ok(text) = std::fs::read_to_string(&pth) else { continue };
            for line in text.lines().map(str::trim) {
                if line.is_empty() || line.starts_with('#') || line.starts_with("import ") || line.starts_with("import\t") {
                    continue;
                }
                out.push(site.join(line));
            }
        }
        out.push(site);
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    fn tree(files: &[(&str, &str)]) -> tempfile::TempDir {
        let dir = tempfile::tempdir().unwrap();
        for (path, text) in files {
            let path = dir.path().join(path);
            std::fs::create_dir_all(path.parent().unwrap()).unwrap();
            std::fs::write(path, text).unwrap();
        }
        dir
    }

    #[test]
    fn a_name_spelled_through_a_reexporting_root_resolves_to_its_defining_module() {
        let dir = tree(&[
            (
                "pkg/__init__.py",
                "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from pkg._shared.model import Op as Op\n",
            ),
            ("pkg/_shared/__init__.py", ""),
            ("pkg/_shared/model.hy", "(defeffect Op [name])\n"),
        ]);
        let empty = HashMap::new();
        let mut origins = Origins::new(dir.path(), &empty);
        assert_eq!(origins.origin("pkg.Op").as_deref(), Some("pkg._shared.model.Op"));
        assert_eq!(origins.origin("pkg._shared.model.Op").as_deref(), Some("pkg._shared.model.Op"));
    }

    #[test]
    fn a_lazy_export_table_and_a_hy_import_are_followed() {
        let dir = tree(&[
            (
                "pkg/__init__.py",
                "from importlib import import_module\n_LAZY = {\"Op\": \"pkg._inner\"}\ndef __getattr__(name):\n    return getattr(import_module(_LAZY[name]), name)\n",
            ),
            ("pkg/_inner.hy", "(import pkg._model [Op])\n"),
            ("pkg/_model.py", "class Op:\n    pass\n"),
        ]);
        let empty = HashMap::new();
        let mut origins = Origins::new(dir.path(), &empty);
        assert_eq!(origins.origin("pkg.Op").as_deref(), Some("pkg._model.Op"));
    }

    #[test]
    fn a_relative_reexport_climbs_from_the_package() {
        let dir = tree(&[("pkg/__init__.py", "from .sub.model import Op\n"), ("pkg/sub/model.py", "Op = object()\n")]);
        let empty = HashMap::new();
        let mut origins = Origins::new(dir.path(), &empty);
        assert_eq!(origins.origin("pkg.Op").as_deref(), Some("pkg.sub.model.Op"));
    }

    #[test]
    fn a_path_dependency_outside_the_root_is_read() {
        let dir = tree(&[
            ("project/pyproject.toml", "[tool.uv.sources]\nlib = { path = \"../lib\", editable = true }\n"),
            ("lib/src/lib/__init__.py", "from lib._effects import Op as Op\n"),
            ("lib/src/lib/_effects.py", "class Op:\n    pass\n"),
        ]);
        let empty = HashMap::new();
        let mut origins = Origins::new(&dir.path().join("project"), &empty);
        assert_eq!(origins.origin("lib.Op").as_deref(), Some("lib._effects.Op"));
    }

    #[test]
    fn a_moved_module_shim_is_followed_through_its_star_import() {
        let dir = tree(&[
            (
                "pkg/old.py",
                "import sys\nfrom typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from pkg._new import *\nsys.modules[__name__] = __import__('pkg._new')\n",
            ),
            ("pkg/_new.hy", "(defeffect Op [name])\n"),
        ]);
        let empty = HashMap::new();
        let mut origins = Origins::new(dir.path(), &empty);
        assert_eq!(origins.origin("pkg.old.Op").as_deref(), Some("pkg._new.Op"));
    }

    #[test]
    fn a_package_under_a_nested_import_root_is_found_from_the_file_that_names_it() {
        let dir = tree(&[("tool/cli/app/__init__.py", ""), ("tool/cli/app/model.py", "class Op:\n    pass\n")]);
        let empty = HashMap::new();
        let mut origins = Origins::new(dir.path(), &empty);
        assert_eq!(origins.origin("app.model.Op"), None);
        assert_eq!(origins.origin_from("app.model.Op", Some("tool/cli/app/user.hy")).as_deref(), Some("app.model.Op"));
    }

    #[test]
    fn a_name_that_cannot_be_traced_is_none_not_its_spelling() {
        let dir = tree(&[("pkg/__init__.py", "")]);
        let empty = HashMap::new();
        let mut origins = Origins::new(dir.path(), &empty);
        assert_eq!(origins.origin("pkg.Missing"), None);
        assert_eq!(origins.origin("nowhere.Op"), None);
    }
}
