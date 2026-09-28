//! Hy の file(`*.hy` / `*.hyk` / `*.hyp`)の索引 — `doeff-indexer hy-index` の本体。
//!
//! 定義・import・参照を契約 `hy-index-contract.md`(版 1)・`hy-index-contract-v2.md`(版 2 = bases・calls)・`hy-index-contract-v3.md`
//! (版 3 = 定義ごとの生の副作用の証拠 raw)の形で出す。Python の索引
//! (`indexer.rs`)とは独立で、互いの挙動を変えない。読めない file・壊れた括弧でも止まらず、
//! 読めた分を出して `errors` に理由を積む。

mod analyze;
pub mod fields;
mod model;
mod position;
mod raw;
pub mod raw_catalog;
/// Hy の読み取り器(form の木)。doeff-linter が同じ読み取りで `:tags` の辞書と import の式を読むために公開する。
pub mod reader;

#[cfg(test)]
mod tests;
#[cfg(test)]
mod raw_tests;

use std::path::{Component, Path, PathBuf};

pub use analyze::mangle;
pub use model::{
    Call, Definition, DefinitionKind, HyFileIndex, HyIndex, Import, Position, Range, RawEvidence, RawEvidenceKind, RawMark,
    RawStep, RawStrength, RawVia, RawViaScope, Reference, CONTRACT_VERSION,
};
pub use position::LineIndex;
pub use raw::{annotate as annotate_raw, matches_pattern};
pub use raw_catalog::{RawCatalog, RawCategory};

/// 生の副作用の判定に使う目録と、利用者の追加の中で読めなかった値の理由。
#[derive(Debug, Clone)]
pub struct RawSettings {
    pub catalog: RawCatalog,
    pub problems: Vec<String>,
}

/// 探索で降りない directory の名前(契約の一覧)。
const SKIPPED_DIRS: &[&str] = &[".venv", "node_modules", "target", ".git", "__pycache__"];

/// 索引の対象の拡張子。
const HY_EXTENSIONS: &[&str] = &["hy", "hyk", "hyp"];

/// root 以下の Hy の file を全部集める(除く directory は降りない)。順は path の順で決まる。
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

/// 拡張子が Hy の file かを返す。
fn is_hy_file(path: &Path) -> bool {
    path.extension().and_then(|ext| ext.to_str()).is_some_and(|ext| HY_EXTENSIONS.contains(&ext))
}

/// root 以下の Hy の file を全部索引する。生の副作用の経由の証拠は file をまたぐので、この全体の実行だけが計算する。
pub fn index_root(root: &Path, raw: &RawSettings) -> HyIndex {
    let files = collect_hy_files(root);
    let mut index = read_paths(root, &files);
    raw::annotate(&mut index.files, &raw.catalog, true);
    index.raw_via = RawViaScope::Computed;
    index.raw_catalog_problems = raw.problems.clone();
    index
}

/// 指定した file だけを索引する(root は module 名の基準)。生の副作用は直接の証拠だけ(経由は計算しない)。
pub fn index_paths(root: &Path, paths: &[PathBuf], raw: &RawSettings) -> HyIndex {
    let mut index = read_paths(root, paths);
    raw::annotate(&mut index.files, &raw.catalog, false);
    index.raw_catalog_problems = raw.problems.clone();
    index
}

/// file を並列に読んで索引する(生の副作用の判定の前の段)。
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
                // 1 つの file の解析が panic しても全体は止めず、その file に理由を積む。
                handle.join().unwrap_or_else(|_| {
                    part.iter().map(|path| failed_file(root, path, "解析の途中で内部の誤りが起きた")).collect()
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

/// 保存前の内容(stdin から読んだもの)を `path` の file として 1 件索引する。生の副作用は直接の証拠だけ。
pub fn index_stdin_source(root: &Path, path: &Path, source: &str, raw: &RawSettings) -> HyIndex {
    let mut files = vec![index_source(root, path, source)];
    raw::annotate(&mut files, &raw.catalog, false);
    HyIndex {
        version: CONTRACT_VERSION,
        root: root.to_string_lossy().into_owned(),
        files,
        raw_via: RawViaScope::NotComputed,
        raw_catalog_problems: raw.problems.clone(),
    }
}

/// 1 つの file を disk から読んで索引する。読めなければ `errors` に理由を積んだ空の索引を返す。
pub fn index_file(root: &Path, path: &Path) -> HyFileIndex {
    match std::fs::read(path) {
        Ok(bytes) => match String::from_utf8(bytes) {
            Ok(source) => index_source(root, path, &source),
            Err(error) => {
                let source = String::from_utf8_lossy(error.as_bytes()).into_owned();
                let mut file = index_source(root, path, &source);
                file.errors.insert(0, "UTF-8 として読めない byte を U+FFFD に置き換えて読んだ".to_string());
                file
            }
        },
        Err(error) => failed_file(root, path, &format!("file を読めない: {}", error)),
    }
}

/// source を `path` の file として索引する。
pub fn index_source(root: &Path, path: &Path, source: &str) -> HyFileIndex {
    let analysis = analyze::analyze(source);
    HyFileIndex {
        path: path.to_string_lossy().into_owned(),
        module: module_name(root, path),
        definitions: analysis.definitions,
        imports: analysis.imports,
        references: analysis.references,
        calls: analysis.calls,
        errors: analysis.errors,
    }
}

/// 読めなかった file の索引(中身は空、`errors` に理由)。
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

/// root からの相対 path を dotted の module 名にする(`__init__` は親の package 名)。
/// root の外の file は file 名だけを module 名にする。
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

/// path の root からの相対 path を返す(symlink の違いは正規化した path でもう一度試す)。
fn relative_to_root(root: &Path, path: &Path) -> Option<PathBuf> {
    if let Ok(relative) = path.strip_prefix(root) {
        return Some(relative.to_path_buf());
    }
    let root = root.canonicalize().ok()?;
    // stdin の経路では file がまだ disk に無いことがあるので、親の directory を正規化する。
    let path = match path.canonicalize() {
        Ok(path) => path,
        Err(_) => path.parent()?.canonicalize().ok()?.join(path.file_name()?),
    };
    path.strip_prefix(&root).ok().map(Path::to_path_buf)
}
