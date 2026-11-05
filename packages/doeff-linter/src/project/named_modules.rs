//! The repo range read by a single-file run (`--stdin --path` — the hook right after a write, and editors): the modules the written file names.
//!
//! A single-file check resolves the names in the written file (calls, types, definitions) to `module.name` using import bindings and the module, then looks them up in the repo tables (defk names, record fields, the effect and type
//! world). The names looked up are only those from symbols in the written file resolved by `smells::Scope::qualify`, so reading just those modules' files gives the same answer as reading all the files in the repo.
//! Previously it read every Hy file in the repo (one file at a time per kind of fact, about 10 thousand opens — 2 to 7 seconds on a busy Pod).
//!
//! Named modules = the file's own module plus the modules of the names resolved from symbols in the file (including every prefix of a name cut at `.` — a prefix of an attribute chain may be a module),
//! restricted to those present in the repo. Modules that are absent (packages outside the repo) are not in the tables either, so they drop out. Resolution lives in one place, `Scope::qualify`; this file only collects symbols and passes them on.

use std::collections::BTreeSet;
use std::path::PathBuf;

use doeff_indexer::hy_index::reader::{Node, Reader};

use super::facts::hy_bindings;
use super::hy_files::ModuleFiles;
use super::names::module_of;
use super::smells::{children, Hy, Scope};

/// Whether this is a Hy file (the target for reading symbols — a Python file names only its own module).
fn is_hy(rel: &str) -> bool {
    [".hy", ".hyk", ".hyp"].iter().any(|extension| rel.ends_with(extension))
}

/// Spellings of symbols in the source (no duplicates — all of calls, types, argument names, and binding names. Keywords are not included).
fn symbols(source: &str) -> BTreeSet<String> {
    let mut reader = Reader::new(source, 0, source.len());
    let forms = reader.read_all();
    let hy = Hy { src: source };
    let mut found = BTreeSet::new();
    let mut pending: Vec<&doeff_indexer::hy_index::reader::Form> = forms.iter().collect();
    while let Some(form) = pending.pop() {
        if let Some(symbol) = hy.symbol(form) {
            found.insert(symbol.to_string());
        }
        if !matches!(form.node, Node::Symbol) {
            pending.extend(children(form));
        }
    }
    found
}

/// Candidate modules for a resolved name `a.b.c.d` — the prefixes of the name cut at `.` (`a.b.c`, `a.b`, `a`). The table key is `module.name`, and no lookup treats the name itself as a module
/// (searching for the name itself would look for nonexistent paths for every local variable name).
fn prefixes(qualified: &str) -> impl Iterator<Item = &str> {
    qualified.rmatch_indices('.').map(move |(at, _)| &qualified[..at])
}

/// Spellings of the modules named by the written file (`rel`, `source`) (those present in the repo, plus the file's own module).
pub fn of_file(known: &ModuleFiles, rel: &str, source: &str) -> BTreeSet<String> {
    let own = module_of(rel);
    let mut named = BTreeSet::from([own.clone()]);
    if !is_hy(rel) {
        return named;
    }
    let bindings = hy_bindings(source, &own);
    let scope = Scope { module: &own, bindings: &bindings };
    for symbol in symbols(source) {
        let qualified = scope.qualify(&symbol);
        named.extend(prefixes(&qualified).filter(|module| known.contains(module)).map(str::to_string));
    }
    named
}

/// Files of the named modules (path from the root and absolute path — in path order). Excludes the `except` file (the written file itself — read from stdin contents, not from disk).
pub fn files_of<'a>(known: &ModuleFiles, modules: impl IntoIterator<Item = &'a String>, except: Option<&str>) -> Vec<(String, PathBuf)> {
    let mut files: Vec<(String, PathBuf)> =
        modules.into_iter().flat_map(|module| known.files(module).to_vec()).filter(|(rel, _)| Some(rel.as_str()) != except).collect();
    files.sort();
    files.dedup();
    files
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Tree of modules present in the repo (`a.b` -> `a/b.hy`), and the index above it.
    fn repo(modules: &[&str]) -> (tempfile::TempDir, ModuleFiles) {
        let dir = tempfile::TempDir::new().unwrap();
        for module in modules {
            let path = dir.path().join(format!("{}.hy", module.replace('.', "/")));
            std::fs::create_dir_all(path.parent().unwrap()).unwrap();
            std::fs::write(path, "").unwrap();
        }
        let known = ModuleFiles::new(dir.path(), known_extensions(), known_skipped());
        (dir, known)
    }

    fn known_extensions() -> &'static [&'static str] {
        doeff_indexer::hy_index::HY_EXTENSIONS
    }

    fn known_skipped() -> &'static [&'static str] {
        doeff_indexer::hy_index::SKIPPED_DIRS
    }

    #[test]
    fn a_file_names_its_own_module_and_the_modules_its_symbols_resolve_to() {
        let (_dir, known) = repo(&["app.core.me", "app.core.other", "app.core.unused", "app.store"]);
        let source = "(import app.store [Row])\n(import app.core.other)\n(defk f [x] (<- r (app.core.other.step x)) (Row))\n";
        let named = of_file(&known, "app/core/me.hy", source);
        assert_eq!(named.into_iter().collect::<Vec<_>>(), vec!["app.core.me", "app.core.other", "app.store"]);
    }

    #[test]
    fn a_module_that_is_not_in_the_repo_is_not_named_and_python_files_name_only_themselves() {
        let (_dir, known) = repo(&["app.core.me"]);
        let named = of_file(&known, "app/core/me.hy", "(import httpx)\n(defn f [] (httpx.get \"x\"))\n");
        assert_eq!(named.into_iter().collect::<Vec<_>>(), vec!["app.core.me"]);
        let python = of_file(&known, "app/core/me.py", "import app.core.other\n");
        assert_eq!(python.into_iter().collect::<Vec<_>>(), vec!["app.core.me"]);
    }

    #[test]
    fn the_written_file_itself_is_left_out_of_the_files_to_read() {
        let (_dir, known) = repo(&["app.a", "app.b"]);
        let modules: BTreeSet<String> = ["app.a".to_string(), "app.b".to_string()].into();
        let files = files_of(&known, &modules, Some("app/a.hy"));
        assert_eq!(files.into_iter().map(|(rel, _)| rel).collect::<Vec<_>>(), vec!["app/b.hy"]);
    }
}
