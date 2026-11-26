//! Git access for `doeff-complexity`: one lazy `git cat-file --batch` process (killed on drop)
//! reads base commit blobs and staged index blobs without spawning a process per file.

use std::cell::RefCell;
use std::collections::{BTreeMap, BTreeSet};
use std::io::{BufRead, BufReader, ErrorKind, Read, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdin, ChildStdout, Command, Stdio};

use crate::measure::{is_prod_hy, normalize_path};

/// Result of reading a file from git or the working tree.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum FileRead {
    Present(String),
    Absent,
    Unreadable(String),
}

/// Staged production `.hy` changes between `<base>` and the git index.
#[derive(Debug, Clone, Default)]
pub struct StagedDiff {
    /// All affected production `.hy` regular-file paths (sorted, deduped).
    pub paths: Vec<String>,
    /// Regular-file paths present at `<base>` (`mode` 100xxx).
    pub base_regular: BTreeSet<String>,
    /// Regular-file paths present in the staged index (`mode` 100xxx).
    pub index_regular: BTreeSet<String>,
    /// Git rename mappings (`new_path -> old_path`).
    pub renames: BTreeMap<String, String>,
}

struct CatFile {
    child: Child,
    input: ChildStdin,
    output: BufReader<ChildStdout>,
}

impl CatFile {
    fn start(repo: &Path) -> Result<Self, String> {
        let mut child = Command::new("git")
            .args(["cat-file", "--batch"])
            .current_dir(repo)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .spawn()
            .map_err(|e| format!("cannot start git cat-file: {}", e))?;
        let input = child.stdin.take().ok_or("git cat-file has no stdin")?;
        let output = BufReader::new(child.stdout.take().ok_or("git cat-file has no stdout")?);
        Ok(Self { child, input, output })
    }

    fn read(&mut self, spec: &str) -> Result<Option<Vec<u8>>, String> {
        writeln!(self.input, "{}", spec)
            .and_then(|_| self.input.flush())
            .map_err(|e| format!("git cat-file write failed: {}", e))?;
        let mut header = String::new();
        let n = self
            .output
            .read_line(&mut header)
            .map_err(|e| format!("git cat-file read failed: {}", e))?;
        if n == 0 {
            return Err(format!("git cat-file closed unexpectedly for {}", spec));
        }
        let fields: Vec<&str> = header.split_whitespace().collect();
        match fields.as_slice() {
            [.., "missing"] => Ok(None),
            [_, "blob", size] => {
                let size: usize = size
                    .parse()
                    .map_err(|_| format!("git cat-file unreadable header {:?}", header))?;
                let mut body = vec![0u8; size + 1];
                self.output
                    .read_exact(&mut body)
                    .map_err(|e| format!("git cat-file body read failed: {}", e))?;
                body.truncate(size);
                Ok(Some(body))
            }
            _ => Err(format!("git cat-file: {} is not a blob ({:?})", spec, header.trim())),
        }
    }
}

impl Drop for CatFile {
    fn drop(&mut self) {
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

fn run_git(repo: &Path, args: &[&str]) -> Result<Vec<u8>, String> {
    let output = Command::new("git")
        .args(args)
        .current_dir(repo)
        .stdin(Stdio::null())
        .output()
        .map_err(|e| format!("cannot start git: {}", e))?;
    if output.status.success() {
        Ok(output.stdout)
    } else {
        Err(format!(
            "git {} failed: {}",
            args.join(" "),
            String::from_utf8_lossy(&output.stderr).trim()
        ))
    }
}

fn is_regular_mode(mode: &str) -> bool {
    mode.starts_with("100")
}

/// Handle to a git repository with a reusable `git cat-file --batch` worker.
pub struct GitRepo {
    repo: PathBuf,
    cat: RefCell<Option<CatFile>>,
}

impl GitRepo {
    pub fn new(repo: PathBuf) -> Self {
        Self { repo, cat: RefCell::new(None) }
    }

    pub fn repo(&self) -> &Path {
        &self.repo
    }

    /// Resolves `reference` to a 40-hex-digit commit SHA.
    pub fn resolve_commit(&self, reference: &str) -> Result<String, String> {
        let rev = format!("{}^{{commit}}", reference);
        let out = run_git(&self.repo, &["rev-parse", "--verify", "--quiet", &rev])?;
        let id = String::from_utf8_lossy(&out).trim().to_string();
        if id.len() == 40 && id.bytes().all(|b| b.is_ascii_hexdigit()) {
            Ok(id)
        } else {
            Err(format!("{} does not name a commit ({:?})", reference, id))
        }
    }

    /// Reads any git blob spec (`<commit>:<path>` or `:0:<path>` for staged index).
    pub fn read_blob(&self, spec: &str) -> FileRead {
        let mut guard = self.cat.borrow_mut();
        if guard.is_none() {
            match CatFile::start(&self.repo) {
                Ok(started) => *guard = Some(started),
                Err(reason) => return FileRead::Unreadable(reason),
            }
        }
        let Some(cat) = guard.as_mut() else {
            return FileRead::Unreadable("git cat-file did not start".to_string());
        };
        match cat.read(spec) {
            Ok(None) => FileRead::Absent,
            Ok(Some(bytes)) => String::from_utf8(bytes)
                .map(FileRead::Present)
                .unwrap_or_else(|_| FileRead::Unreadable(format!("{} is not valid UTF-8", spec))),
            Err(reason) => {
                *guard = None;
                FileRead::Unreadable(reason)
            }
        }
    }

    /// Reads a regular file from the working tree. Symlinks are treated as absent (not separate source modules).
    pub fn read_worktree(&self, rel: &str) -> FileRead {
        let full = self.repo.join(rel);
        match std::fs::symlink_metadata(&full) {
            Ok(meta) if meta.file_type().is_symlink() => return FileRead::Absent,
            Ok(_) => {}
            Err(err) if err.kind() == ErrorKind::NotFound => return FileRead::Absent,
            Err(err) => return FileRead::Unreadable(format!("{}: {}", rel, err)),
        }
        match std::fs::read(&full) {
            Ok(bytes) => String::from_utf8(bytes)
                .map(FileRead::Present)
                .unwrap_or_else(|_| FileRead::Unreadable(format!("{}: not valid UTF-8", rel))),
            Err(err) if err.kind() == ErrorKind::NotFound => FileRead::Absent,
            Err(err) => FileRead::Unreadable(format!("{}: {}", rel, err)),
        }
    }

    /// Lists all tracked regular production `.hy` paths in the index (excludes symlinks `120000`).
    pub fn list_tracked_prod_hy(&self) -> Result<Vec<String>, String> {
        let out = run_git(&self.repo, &["ls-files", "-s", "-z", "--", "*.hy"])?;
        parse_mode_tab_prod_hy(&out)
    }

    /// Lists all regular production `.hy` paths in the tree of `commit` (excludes symlinks `120000`).
    pub fn list_tree_prod_hy(&self, commit: &str) -> Result<Vec<String>, String> {
        let out = run_git(&self.repo, &["ls-tree", "-r", "-z", commit])?;
        parse_mode_tab_prod_hy(&out)
    }

    /// Detects renames (`new_path -> old_path`) between `base` and the working tree.
    /// If `pathspecs` is non-empty, restricts diff to those paths; otherwise scans `*.hy`.
    pub fn renames_vs_worktree(
        &self,
        base: &str,
        pathspecs: &[String],
    ) -> Result<BTreeMap<String, String>, String> {
        let mut args: Vec<&str> = vec!["diff", "--name-status", "-M", "-z", base, "--"];
        if pathspecs.is_empty() {
            args.push("*.hy");
        } else {
            for p in pathspecs {
                args.push(p.as_str());
            }
        }
        let out = run_git(&self.repo, &args)?;
        let (_, renames) = parse_name_status_z(&out)?;
        Ok(renames)
    }

    /// Lists staged regular `.hy` changes (`--cached --raw`) relative to `base`.
    pub fn staged_changes(&self, base: &str) -> Result<StagedDiff, String> {
        let out = run_git(
            &self.repo,
            &["diff", "--cached", "--raw", "-M", "-z", base, "--", "*.hy"],
        )?;
        parse_raw_diff_z(&out)
    }
}

/// Parses `git ls-files -s -z` or `git ls-tree -r -z` records (`<mode> ...\t<path>\0`),
/// keeping only regular files (`100xxx`) matching `is_prod_hy`.
fn parse_mode_tab_prod_hy(raw: &[u8]) -> Result<Vec<String>, String> {
    let mut paths = Vec::new();
    for chunk in raw.split(|&b| b == 0) {
        if chunk.is_empty() {
            continue;
        }
        let s = std::str::from_utf8(chunk)
            .map_err(|_| "git path listing contains non-UTF-8 path".to_string())?;
        let Some((meta, path)) = s.split_once('\t') else {
            continue;
        };
        let mode = meta.split_whitespace().next().unwrap_or("");
        if !is_regular_mode(mode) {
            continue;
        }
        let norm = normalize_path(path);
        if is_prod_hy(&norm) {
            paths.push(norm);
        }
    }
    paths.sort();
    paths.dedup();
    Ok(paths)
}

/// Parses `git diff --name-status -M -z` output into `(changed_prod_hy_paths, rename_map[new -> old])`.
pub fn parse_name_status_z(raw: &[u8]) -> Result<(Vec<String>, BTreeMap<String, String>), String> {
    let tokens: Vec<&str> = raw
        .split(|&b| b == 0)
        .filter(|c| !c.is_empty())
        .map(|c| {
            std::str::from_utf8(c)
                .map_err(|_| "git diff output contains non-UTF-8 path".to_string())
        })
        .collect::<Result<Vec<_>, _>>()?;

    let mut changed = Vec::new();
    let mut renames = BTreeMap::new();
    let mut i = 0;
    while i < tokens.len() {
        let status = tokens[i];
        i += 1;
        if status.starts_with('R') {
            if i + 1 >= tokens.len() {
                return Err(format!("truncated rename record in git diff: {:?}", status));
            }
            let old = normalize_path(tokens[i]);
            let new = normalize_path(tokens[i + 1]);
            i += 2;
            if is_prod_hy(&old) {
                changed.push(old.clone());
            }
            if is_prod_hy(&new) {
                changed.push(new.clone());
                if is_prod_hy(&old) {
                    renames.insert(new, old);
                }
            }
        } else if status.starts_with('C') {
            if i + 1 >= tokens.len() {
                return Err(format!("truncated copy record in git diff: {:?}", status));
            }
            let new = normalize_path(tokens[i + 1]);
            i += 2;
            if is_prod_hy(&new) {
                changed.push(new);
            }
        } else {
            if i >= tokens.len() {
                return Err(format!("truncated status record in git diff: {:?}", status));
            }
            let path = normalize_path(tokens[i]);
            i += 1;
            if is_prod_hy(&path) {
                changed.push(path);
            }
        }
    }
    changed.sort();
    changed.dedup();
    Ok((changed, renames))
}

/// Parses `git diff --cached --raw -M -z` output (`:<old_mode> <new_mode> <old_sha> <new_sha> <status>\0...`),
/// skipping symlinks (`120000`) and non-production files.
pub fn parse_raw_diff_z(raw: &[u8]) -> Result<StagedDiff, String> {
    let tokens: Vec<&str> = raw
        .split(|&b| b == 0)
        .filter(|c| !c.is_empty())
        .map(|c| {
            std::str::from_utf8(c)
                .map_err(|_| "git diff output contains non-UTF-8 path".to_string())
        })
        .collect::<Result<Vec<_>, _>>()?;

    let mut diff = StagedDiff::default();
    let mut path_set = BTreeSet::new();
    let mut i = 0;
    while i < tokens.len() {
        let header = tokens[i];
        i += 1;
        let header_body = header.strip_prefix(':').unwrap_or(header);
        let fields: Vec<&str> = header_body.split_whitespace().collect();
        if fields.len() < 5 {
            return Err(format!("malformed raw git diff header: {:?}", header));
        }
        let old_mode = fields[0];
        let new_mode = fields[1];
        let status = fields[4];

        if status.starts_with('R') {
            if i + 1 >= tokens.len() {
                return Err(format!("truncated rename record in raw git diff: {:?}", header));
            }
            let old = normalize_path(tokens[i]);
            let new = normalize_path(tokens[i + 1]);
            i += 2;
            let old_ok = is_regular_mode(old_mode) && is_prod_hy(&old);
            let new_ok = is_regular_mode(new_mode) && is_prod_hy(&new);
            if old_ok {
                path_set.insert(old.clone());
                diff.base_regular.insert(old.clone());
            }
            if new_ok {
                path_set.insert(new.clone());
                diff.index_regular.insert(new.clone());
                if old_ok {
                    diff.renames.insert(new, old);
                }
            }
        } else if status.starts_with('C') {
            if i + 1 >= tokens.len() {
                return Err(format!("truncated copy record in raw git diff: {:?}", header));
            }
            let new = normalize_path(tokens[i + 1]);
            i += 2;
            if is_regular_mode(new_mode) && is_prod_hy(&new) {
                path_set.insert(new.clone());
                diff.index_regular.insert(new);
            }
        } else {
            if i >= tokens.len() {
                return Err(format!("truncated path record in raw git diff: {:?}", header));
            }
            let path = normalize_path(tokens[i]);
            i += 1;
            if is_prod_hy(&path) {
                let old_ok = is_regular_mode(old_mode);
                let new_ok = is_regular_mode(new_mode);
                if old_ok || new_ok {
                    path_set.insert(path.clone());
                }
                if old_ok {
                    diff.base_regular.insert(path.clone());
                }
                if new_ok {
                    diff.index_regular.insert(path);
                }
            }
        }
    }
    diff.paths = path_set.into_iter().collect();
    Ok(diff)
}
