//! Path utilities inside a repo (relative spelling from the root, keys of declaration files, glob matching). Rule modules read this directly -- reading back
//! project/mod.rs, which assembles things, would form a dependency cycle with the child modules that mod.rs declares.

use std::path::Path;

/// Spelling of a path relative to the repo root (separator `/`). None if outside the root. Differences in symlinks are normalized and tried once more.
pub fn relative_path(root: &Path, path: &Path) -> Option<String> {
    let joined = |rel: &Path| rel.components().map(|c| c.as_os_str().to_string_lossy().into_owned()).collect::<Vec<_>>().join("/");
    if let Ok(rel) = path.strip_prefix(root) {
        return Some(joined(rel));
    }
    let canonical = match path.canonicalize() {
        Ok(p) => p,
        Err(_) => path.parent()?.canonicalize().ok()?.join(path.file_name()?),
    };
    let root = root.canonicalize().ok()?;
    canonical.strip_prefix(&root).ok().map(joined)
}

/// Key spelling of a declaration file (architecture.hy) -- relative to the root if under it, otherwise relative to the root including `..` (separator `/`).
/// With the config root at a package's `src/`, the package's root architecture.hy lies outside the root. This is so the key is the same on every machine,
/// rather than putting into the key an absolute path or a file name that varies per machine and loses its location.
pub fn declared_rel(root: &Path, path: &Path) -> String {
    if let Some(rel) = relative_path(root, path) {
        return rel;
    }
    let canonical = |p: &Path| p.canonicalize().unwrap_or_else(|_| p.to_path_buf());
    let (root, path) = (canonical(root), canonical(path));
    let root_parts: Vec<_> = root.components().collect();
    let path_parts: Vec<_> = path.components().collect();
    let common = root_parts.iter().zip(&path_parts).take_while(|(a, b)| a == b).count();
    std::iter::repeat_n("..".to_string(), root_parts.len() - common)
        .chain(path_parts[common..].iter().map(|c| c.as_os_str().to_string_lossy().into_owned()))
        .collect::<Vec<_>>()
        .join("/")
}

/// Path glob matching -- `**` is zero or more segments, `*` is any spelling within a segment (does not cross `/`). A spelling without `/` is matched against the file name.
pub fn glob_matches(pattern: &str, rel: &str) -> bool {
    let path: Vec<&str> = rel.split('/').collect();
    if !pattern.contains('/') {
        return path.last().is_some_and(|name| segment_matches(pattern, name));
    }
    let parts: Vec<&str> = pattern.split('/').filter(|p| !p.is_empty()).collect();
    segments_match(&parts, &path)
}

/// The files a path glob names (`/`-separated, `*` and `**` as in glob_matches, matched against the whole path) — walks only the dir before the first segment with `*`,
/// in path order. A spelling without `*` is returned as it is (whether it exists is for the reader to say).
pub fn expand_glob(pattern: &Path) -> Vec<std::path::PathBuf> {
    let spelling = pattern.to_string_lossy().replace('\\', "/");
    if !spelling.contains('*') {
        return vec![pattern.to_path_buf()];
    }
    let parts: Vec<&str> = spelling.split('/').collect();
    let literal = parts.iter().take_while(|part| !part.contains('*')).count();
    let base = parts[..literal].join("/");
    let base = if base.is_empty() && spelling.starts_with('/') { "/".to_string() } else { base };
    let rest: Vec<&str> = parts[literal..].iter().copied().filter(|p| !p.is_empty()).collect();
    let mut found: Vec<std::path::PathBuf> = walkdir::WalkDir::new(if base.is_empty() { "." } else { base.as_str() })
        .into_iter()
        .filter_map(Result::ok)
        .filter(|entry| entry.file_type().is_file())
        .filter(|entry| {
            let rel = entry.path().strip_prefix(if base.is_empty() { "." } else { base.as_str() }).unwrap_or(entry.path());
            let names: Vec<String> = rel.components().map(|c| c.as_os_str().to_string_lossy().into_owned()).collect();
            segments_match(&rest, &names.iter().map(String::as_str).collect::<Vec<_>>())
        })
        .map(|entry| entry.into_path())
        .collect();
    found.sort();
    found
}

/// Matches a sequence of glob segments against a sequence of path segments (`**` is zero or more segments).
pub(crate) fn segments_match(pattern: &[&str], path: &[&str]) -> bool {
    match pattern.split_first() {
        None => path.is_empty(),
        Some((&"**", rest)) => (0..=path.len()).any(|skip| segments_match(rest, &path[skip..])),
        Some((first, rest)) => path.split_first().is_some_and(|(name, tail)| segment_matches(first, name) && segments_match(rest, tail)),
    }
}

/// Matches a single segment (`*` is any spelling).
fn segment_matches(pattern: &str, name: &str) -> bool {
    match pattern.split_once('*') {
        None => pattern == name,
        Some((head, tail)) => {
            name.starts_with(head)
                && (head.len()..=name.len()).any(|at| name.is_char_boundary(at) && segment_matches(tail, &name[at..]))
        }
    }
}
