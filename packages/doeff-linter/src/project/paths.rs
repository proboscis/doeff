//! repo の中の path の道具(根からの相対の綴り・宣言の file の鍵・glob の照合)。規則の module はここを直に読む — 組み立ての
//! project/mod.rs を読み戻すと、mod.rs が宣言する子の module と依存の輪になる(agora-redesign #2119)。

use std::path::Path;

/// path の repo の根からの相対の綴り(区切り `/`)。根の外なら None。symlink の違いは正規化してもう一度試す。
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

/// 宣言の file(architecture.hy)の鍵の綴り — 根の下なら根からの相対、根の外なら `..` を含む根からの相対(区切り `/`)。
/// 設定の root で package の `src/` を根にすると、package の根の architecture.hy は根の外になる。機体ごとに変わる絶対 path や
/// 置き場を失った file 名だけを鍵に入れず、どの機体でも同じ鍵にするため(agora-redesign #1977)。
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

/// path の glob の照合 — `**` は 0 個以上の段、`*` は段の中の任意の綴り(`/` を越えない)。`/` を含まない綴りは file の名に当てる。
pub fn glob_matches(pattern: &str, rel: &str) -> bool {
    let path: Vec<&str> = rel.split('/').collect();
    if !pattern.contains('/') {
        return path.last().is_some_and(|name| segment_matches(pattern, name));
    }
    let parts: Vec<&str> = pattern.split('/').filter(|p| !p.is_empty()).collect();
    segments_match(&parts, &path)
}

/// glob の段の列と path の段の列の照合(`**` は 0 個以上の段)。
pub(crate) fn segments_match(pattern: &[&str], path: &[&str]) -> bool {
    match pattern.split_first() {
        None => path.is_empty(),
        Some((&"**", rest)) => (0..=path.len()).any(|skip| segments_match(rest, &path[skip..])),
        Some((first, rest)) => path.split_first().is_some_and(|(name, tail)| segment_matches(first, name) && segments_match(rest, tail)),
    }
}

/// 1 段の照合(`*` は任意の綴り)。
fn segment_matches(pattern: &str, name: &str) -> bool {
    match pattern.split_once('*') {
        None => pattern == name,
        Some((head, tail)) => {
            name.starts_with(head)
                && (head.len()..=name.len()).any(|at| name.is_char_boundary(at) && segment_matches(tail, &name[at..]))
        }
    }
}
