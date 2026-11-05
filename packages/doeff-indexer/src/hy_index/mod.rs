//! Index of Hy files (`*.hy` / `*.hyk` / `*.hyp`) — the body of `doeff-indexer hy-index`.
//!
//! Emits definitions, imports, and references in the shape of the contracts `hy-index-contract.md` (version 1), `hy-index-contract-v2.md` (version 2 = bases, calls), `hy-index-contract-v3.md`
//! (version 3 = per-definition raw side-effect evidence `raw`), and version 4 (fully qualified names = qualified_name, call target). It is independent of the Python index
//! (`indexer.rs`) and does not change each other's behavior. It does not stop on unreadable files or broken parentheses;
//! it emits what could be read and accumulates reasons in `errors`.

mod analyze;
mod cache_form;
pub mod fields;
mod model;
mod position;
mod qualify;
mod raw;
pub mod raw_catalog;
/// The Hy reader (form tree). Public so doeff-linter can read the `:tags` dictionary and import expressions with the same reader.
pub mod reader;

#[cfg(test)]
mod tests;
#[cfg(test)]
mod raw_tests;
#[cfg(test)]
mod contract_tests;
#[cfg(test)]
mod qualify_tests;

use std::collections::BTreeSet;
use std::path::{Component, Path, PathBuf};

pub use analyze::mangle;
pub use cache_form::{CachedHyFile, CachedHyFileMismatch};
pub use model::{
    ArgumentValue, Call, CallArgument, Definition, DefinitionKind, HyFileIndex, HyIndex, Import, Position, Range, RawEvidence, RawEvidenceKind, RawMark,
    RawStep, RawStrength, RawVia, RawViaScope, Reference, CONTRACT_VERSION, ContractClause, ContractSide, NameRef, ParamType,
    TypeNote,
};
pub use position::LineIndex;
pub use raw::{annotate as annotate_raw, matches_pattern, ViaTrace};
pub use raw_catalog::{RawCatalog, RawCategory};

/// The catalog used to judge raw side effects, and reasons for values in user additions that could not be read.
#[derive(Debug, Clone)]
pub struct RawSettings {
    pub catalog: RawCatalog,
    pub problems: Vec<String>,
}

/// Names of directories not descended into during traversal (the contract's list).
pub const SKIPPED_DIRS: &[&str] = &[".venv", "node_modules", "target", ".git", "__pycache__"];

/// Extensions that are indexed.
pub const HY_EXTENSIONS: &[&str] = &["hy", "hyk", "hyp"];

/// Collects all Hy files under root (excluded directories are not descended into). Order is determined by path order.
pub fn collect_hy_files(root: &Path) -> Vec<PathBuf> {
    let walker = walkdir::WalkDir::new(root).follow_links(false).into_iter().filter_entry(|entry| {
        entry.depth() == 0
            || !entry.file_type().is_dir()
            || !SKIPPED_DIRS.contains(&entry.file_name().to_string_lossy().as_ref())
    });
    let mut files: Vec<PathBuf> = walker
        .filter_map(Result::ok)
        .filter(|entry| entry.file_type().is_file() && is_hy_file(entry.path()))
        .map(|entry| entry.into_path())
        .collect();
    files.sort();
    files
}

/// Returns whether the extension is that of a Hy file.
fn is_hy_file(path: &Path) -> bool {
    path.extension().and_then(|ext| ext.to_str()).is_some_and(|ext| HY_EXTENSIONS.contains(&ext))
}

/// Indexes all Hy files under root. Via-evidence for raw side effects spans files, so only this whole-tree run computes it.
pub fn index_root(root: &Path, raw: &RawSettings) -> HyIndex {
    let files = collect_hy_files(root);
    let mut index = read_paths(root, &files);
    raw::annotate(&mut index.files, &raw.catalog, ViaTrace::Through { stops: &BTreeSet::new() });
    index.raw_via = RawViaScope::Computed;
    index.raw_catalog_problems = raw.problems.clone();
    index
}

/// Indexes only the specified files (root is the base for module names). Raw side effects have direct evidence only (via is not computed).
pub fn index_paths(root: &Path, paths: &[PathBuf], raw: &RawSettings) -> HyIndex {
    let mut index = read_paths(root, paths);
    raw::annotate(&mut index.files, &raw.catalog, ViaTrace::Skip);
    index.raw_catalog_problems = raw.problems.clone();
    index
}

/// Reads and indexes files in parallel (the stage before judging raw side effects).
fn read_paths(root: &Path, paths: &[PathBuf]) -> HyIndex {
    let workers = std::thread::available_parallelism().map(|n| n.get()).unwrap_or(1).max(1);
    let chunk = paths.len().div_ceil(workers).max(1);
    let files = std::thread::scope(|scope| {
        let handles: Vec<_> = paths
            .chunks(chunk)
            .map(|part| scope.spawn(move || part.iter().map(|path| index_file(root, path)).collect::<Vec<_>>()))
            .collect();
        handles
            .into_iter()
            .zip(paths.chunks(chunk))
            .flat_map(|(handle, part)| {
                                // Even if analysis of one file panics, do not stop the whole run; accumulate a reason on that file.
                handle.join().unwrap_or_else(|_| {
                                        part.iter().map(|path| failed_file(root, path, "an internal error occurred during analysis")).collect()
                })
            })
            .collect()
    });
    HyIndex {
        version: CONTRACT_VERSION,
        root: root.to_string_lossy().into_owned(),
        files,
        raw_via: RawViaScope::NotComputed,
        raw_catalog_problems: Vec::new(),
    }
}

/// Indexes the pre-save content (read from stdin) as a single file at `path`. Raw side effects have direct evidence only.
pub fn index_stdin_source(root: &Path, path: &Path, source: &str, raw: &RawSettings) -> HyIndex {
    let mut files = vec![index_source(root, path, source)];
    raw::annotate(&mut files, &raw.catalog, ViaTrace::Skip);
    HyIndex {
        version: CONTRACT_VERSION,
        root: root.to_string_lossy().into_owned(),
        files,
        raw_via: RawViaScope::NotComputed,
        raw_catalog_problems: raw.problems.clone(),
    }
}

/// Reads and indexes one file from disk. If it cannot be read, returns an empty index with the reason accumulated in `errors`.
pub fn index_file(root: &Path, path: &Path) -> HyFileIndex {
    match std::fs::read(path) {
        Ok(bytes) => match String::from_utf8(bytes) {
            Ok(source) => index_source(root, path, &source),
            Err(error) => {
                let source = String::from_utf8_lossy(error.as_bytes()).into_owned();
                let mut file = index_source(root, path, &source);
                                file.errors.insert(0, "read with bytes that are not valid UTF-8 replaced by U+FFFD".to_string());
                file
            }
        },
                Err(error) => failed_file(root, path, &format!("cannot read file: {}", error)),
    }
}

/// Indexes source as the file at `path`.
pub fn index_source(root: &Path, path: &Path, source: &str) -> HyFileIndex {
    let analysis = analyze::analyze(source);
    let mut file = HyFileIndex {
        path: path.to_string_lossy().into_owned(),
        module: module_name(root, path),
        definitions: analysis.definitions,
        imports: analysis.imports,
        references: analysis.references,
        calls: analysis.calls,
        errors: analysis.errors,
    };
    qualify::link(&mut file);
    file
}

/// The index of a file that could not be read (empty contents, reason in `errors`).
fn failed_file(root: &Path, path: &Path, reason: &str) -> HyFileIndex {
    HyFileIndex {
        path: path.to_string_lossy().into_owned(),
        module: module_name(root, path),
        definitions: Vec::new(),
        imports: Vec::new(),
        references: Vec::new(),
        calls: Vec::new(),
        errors: vec![reason.to_string()],
    }
}

/// Turns a path relative to root into a dotted module name (`__init__` becomes the parent package name).
/// For a file outside root, only the file name is the module name.
pub fn module_name(root: &Path, path: &Path) -> String {
    let relative = relative_to_root(root, path).unwrap_or_else(|| PathBuf::from(path.file_name().unwrap_or_default()));
    let mut parts: Vec<String> = relative
        .components()
        .filter_map(|component| match component {
            Component::Normal(part) => Some(part.to_string_lossy().into_owned()),
            _ => None,
        })
        .collect();
    if let Some(last) = parts.pop() {
        let stem = Path::new(&last).file_stem().map(|stem| stem.to_string_lossy().into_owned()).unwrap_or(last);
        if stem != "__init__" {
            parts.push(stem);
        }
    }
    parts.join(".")
}

/// Returns the path's path relative to root (a symlink difference is retried with the normalized path).
fn relative_to_root(root: &Path, path: &Path) -> Option<PathBuf> {
    if let Ok(relative) = path.strip_prefix(root) {
        return Some(relative.to_path_buf());
    }
    let root = root.canonicalize().ok()?;
        // On the stdin path the file may not be on disk yet, so normalize the parent directory.
    let path = match path.canonicalize() {
        Ok(path) => path,
        Err(_) => path.parent()?.canonicalize().ok()?.join(path.file_name()?),
    };
    path.strip_prefix(&root).ok().map(Path::to_path_buf)
}
