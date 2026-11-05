//! The single point that decides, per file, the rule population for per-statement Python rules, from the layer declarations in the architecture.hy of the package owning that file.
//!
//! The linter reads one declaration per run (the configured architecture, or the root architecture.hy). A root run of doeff (pre-commit, make lint-doeff) does not read
//! package declarations, so rule exemptions that a package wrote on its layers had no effect. Here we find the nearest architecture.hy above the file
//! (the declaration of the package owner), and if the file is a module of a layer with exemptions (named in `:modules`, with `:exempt [(rule RULE-ID "reason")]`):
//!   - drop findings of the exempted rules (the reason lives in the declaration; an exemption without a reason is rejected by the declaration reader)
//!   - name imports of the layer's `:forbid-modules` modules with DOEFF032 (red if business code enters an exempted layer)
//! When the declaration cannot be read, it is named as an error of that file (neither silently exempted nor silently applied).
//!
//! Module names are derived by Python's rule (walking up through directories that have `__init__.py`), independent of the configured root and of
//! architecture.hy's :root (the root of the layer locations).

use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex, OnceLock};

use rustpython_ast::{Mod, Stmt};

use crate::models::{Severity, Violation};
use crate::project::architecture::Architecture;

/// Rule ID for a finding where a module of an exempted layer imports a business module (the name is owned by rules::doeff032_rule_population_declaration).
pub use crate::rules::doeff032_rule_population_declaration::RULE_ID as BUSINESS_IMPORT_RULE_ID;

/// The population answer for one file.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum FilePopulation {
    /// Not a module of a layer with exemptions — every rule applies as is.
    Plain,
    /// A module named by a layer with exemptions.
    Exempt {
        layer: String,
        rules: Vec<String>,
        forbid_modules: Vec<String>,
        declaration: PathBuf,
    },
}

type Declarations = HashMap<PathBuf, Result<Arc<Architecture>, String>>;

/// Declaration file -> the declaration read (or the reason it could not be read). The same declaration is read only once per run.
fn declarations() -> &'static Mutex<Declarations> {
    static CACHE: OnceLock<Mutex<Declarations>> = OnceLock::new();
    CACHE.get_or_init(|| Mutex::new(HashMap::new()))
}

/// Pass the declaration read at the start of the run to the population query, so the same declaration is not read twice (`Architecture::load` takes 30-80 ms for a 530KB architecture.hy —
/// about 1/4 of the CPU of a hook for one written file). Callers pass only declarations read the same way the query reads them (the declaration's dir as the repo root).
pub fn remember_declaration(path: &Path, architecture: Arc<Architecture>) {
    declarations()
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner())
        .insert(path.to_path_buf(), Ok(architecture));
}

/// The nearest architecture.hy above the file. Does not look above the repo root (the dir containing `.git`).
fn nearest_declaration(file: &Path) -> Option<PathBuf> {
    let mut dir = file.parent()?;
    loop {
        let candidate = dir.join("architecture.hy");
        if candidate.is_file() {
            return Some(candidate);
        }
        if dir.join(".git").exists() {
            return None;
        }
        dir = dir.parent()?;
    }
}

/// The file's module name (dot-separated) — built by walking up through dirs that have `__init__.py` (`a/b/__init__.py` is `a.b`).
fn module_name(file: &Path) -> Option<String> {
    let stem = file.file_stem()?.to_str()?.to_string();
    let mut parts: Vec<String> = if stem == "__init__" {
        Vec::new()
    } else {
        vec![stem]
    };
    let mut dir = file.parent()?;
    while dir.join("__init__.py").is_file() {
        parts.push(dir.file_name()?.to_str()?.to_string());
        dir = dir.parent()?;
    }
    if parts.is_empty() {
        return None;
    }
    parts.reverse();
    Some(parts.join("."))
}

/// Read the declaration file (the same declaration only once). The root is the dir containing the declaration.
fn declaration(path: &Path) -> Result<Arc<Architecture>, String> {
    let mut cache = declarations()
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    cache
        .entry(path.to_path_buf())
        .or_insert_with(|| {
            let root = path.parent().unwrap_or(Path::new("."));
            Architecture::load(path, root)
                .map(Arc::new)
                .map_err(|problems| {
                    format!(
                        "error in {} (cannot decide the rule population of this file):\n  {}",
                        path.display(),
                        problems.join("\n  ")
                    )
                })
        })
        .clone()
}

/// Decide the population of a file. Err(reason) when the declaration cannot be read.
pub fn population_of(file: &Path) -> Result<FilePopulation, String> {
    let absolute = file.canonicalize().unwrap_or_else(|_| file.to_path_buf());
    let Some(path) = nearest_declaration(&absolute) else {
        return Ok(FilePopulation::Plain);
    };
    let architecture = declaration(&path)?;
    // Look first for a naming by path in :files (files without a module name), otherwise the names in :modules
    let base = path.parent().unwrap_or(Path::new("."));
    let by_file = absolute
        .strip_prefix(base)
        .ok()
        .and_then(|relative| relative.to_str())
        .and_then(|relative| architecture.exempt_layer_of_file(relative));
    let layer = match by_file {
        Some(layer) => Some(layer),
        None => module_name(&absolute).and_then(|module| architecture.exempt_layer_of(&module)),
    };
    Ok(match layer {
        Some(layer) => FilePopulation::Exempt {
            layer: layer.name.clone(),
            rules: layer.exempt.iter().map(|e| e.rule.clone()).collect(),
            forbid_modules: layer.forbid_modules.clone(),
            declaration: path,
        },
        None => FilePopulation::Plain,
    })
}

/// Whether an import name (`a.b.c`) is a forbidden module (`a` or `a.b`) or under one.
fn forbidden(name: &str, forbid_modules: &[String]) -> Option<String> {
    forbid_modules
        .iter()
        .find(|m| {
            name == m.as_str()
                || name
                    .strip_prefix(m.as_str())
                    .is_some_and(|rest| rest.starts_with('.'))
        })
        .cloned()
}

/// From a list of statements (including nested functions, classes, and branches), collect imports of forbidden modules (position, import name, and forbidden module).
fn forbidden_imports(
    stmts: &[Stmt],
    forbid_modules: &[String],
    out: &mut Vec<(usize, String, String)>,
) {
    for stmt in stmts {
        match stmt {
            Stmt::Import(import) => {
                for alias in &import.names {
                    if let Some(hit) = forbidden(alias.name.as_str(), forbid_modules) {
                        out.push((import.range.start().to_usize(), alias.name.to_string(), hit));
                    }
                }
            }
            Stmt::ImportFrom(import) => {
                // A relative import (level > 0) stays within the same package — it cannot be the spelling of a forbidden module.
                let level: u32 = import.level.as_ref().map(|l| l.to_u32()).unwrap_or(0);
                if let (Some(module), 0) = (&import.module, level) {
                    if let Some(hit) = forbidden(module.as_str(), forbid_modules) {
                        out.push((import.range.start().to_usize(), module.to_string(), hit));
                    }
                }
            }
            Stmt::FunctionDef(def) => forbidden_imports(&def.body, forbid_modules, out),
            Stmt::AsyncFunctionDef(def) => forbidden_imports(&def.body, forbid_modules, out),
            Stmt::ClassDef(def) => forbidden_imports(&def.body, forbid_modules, out),
            Stmt::If(s) => {
                forbidden_imports(&s.body, forbid_modules, out);
                forbidden_imports(&s.orelse, forbid_modules, out);
            }
            Stmt::For(s) => {
                forbidden_imports(&s.body, forbid_modules, out);
                forbidden_imports(&s.orelse, forbid_modules, out);
            }
            Stmt::While(s) => {
                forbidden_imports(&s.body, forbid_modules, out);
                forbidden_imports(&s.orelse, forbid_modules, out);
            }
            Stmt::With(s) => forbidden_imports(&s.body, forbid_modules, out),
            Stmt::Try(s) => {
                forbidden_imports(&s.body, forbid_modules, out);
                for handler in &s.handlers {
                    if let rustpython_ast::ExceptHandler::ExceptHandler(h) = handler {
                        forbidden_imports(&h.body, forbid_modules, out);
                    }
                }
                forbidden_imports(&s.orelse, forbid_modules, out);
                forbidden_imports(&s.finalbody, forbid_modules, out);
            }
            _ => {}
        }
    }
}

/// Findings (DOEFF032) for imports of forbidden modules from a module of an exempted layer.
pub fn business_import_violations(
    file_path: &str,
    ast: &Mod,
    layer: &str,
    forbid_modules: &[String],
    declaration: &Path,
) -> Vec<Violation> {
    let Mod::Module(module) = ast else {
        return Vec::new();
    };
    let mut hits = Vec::new();
    forbidden_imports(&module.body, forbid_modules, &mut hits);
    hits.into_iter()
        .map(|(offset, name, hit)| {
            Violation::new(
                BUSINESS_IMPORT_RULE_ID.to_string(),
                format!(
                    "module of layer {} excluded from the rule population imports business module {} ({} — {} of the layer's :forbid-modules). An excluded layer \
                     is for code outside any Program, such as at startup time; if business code enters it, the excluded rules stop applying to that code too. \
                     Move the business code to a layer that is not excluded ({})",
                    layer,
                    name,
                    file_path,
                    hit,
                    declaration.display()
                ),
                offset,
                file_path.to_string(),
                Severity::Error,
            )
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    const DECLARATION: &str = r#"
(defarchitecture pkg
  :root "pkg"
  :layers [(layer startup
             :summary "A watcher installed at Python startup (.pth)"
             :modules [guard]
             :exempt [(rule DOEFF004 "Installed at startup outside any Program, so there is no entry point to receive config via Ask")]
             :forbid-modules [doeff doeff_hy])])
"#;

    /// Build a repo containing a broken declaration file (`guard.py` is a module of the declaration's `:modules [guard]`). Returns (repo dir, declaration path, guard.py).
    fn broken_repo() -> (tempfile::TempDir, PathBuf, PathBuf) {
        let dir = tempfile::TempDir::new().unwrap();
        std::fs::create_dir_all(dir.path().join(".git")).unwrap();
        let root = dir.path().canonicalize().unwrap();
        std::fs::write(root.join("architecture.hy"), "(this is not a declaration").unwrap();
        std::fs::write(root.join("guard.py"), "x = 1\n").unwrap();
        (dir, root.join("architecture.hy"), root.join("guard.py"))
    }

    /// When given a declaration read at the start of the run, the population query does not re-read architecture.hy.
    /// In a repo whose declaration file is broken, where re-reading would produce an error, answer from the passed declaration.
    #[test]
    fn a_remembered_declaration_answers_without_reading_the_file_again() {
        let (_unremembered, _, guard) = broken_repo();
        assert!(population_of(&guard).is_err(), "test precondition: reading the broken declaration is an error");

        let good = tempfile::TempDir::new().unwrap();
        let good_declaration = good.path().canonicalize().unwrap().join("architecture.hy");
        std::fs::write(&good_declaration, DECLARATION).unwrap();
        let loaded = Architecture::load(&good_declaration, good_declaration.parent().unwrap()).expect("test precondition: the declaration is readable");

        let (_remembered, declaration, guard) = broken_repo();
        remember_declaration(&declaration, Arc::new(loaded));
        assert!(matches!(population_of(&guard), Ok(FilePopulation::Exempt { .. })), "answers from the passed declaration");
    }
}
