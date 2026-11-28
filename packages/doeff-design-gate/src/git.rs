//! The base commit and what is read at it, and the tree after the change (the work tree, the staged index, or a commit). The gate reads the declaration, the ledgers,
//! and the approvals only at the base, so a change cannot loosen what judges it.
//!
//! One run starts at most three git processes whatever the number of paths: `rev-parse` of the base, `ls-tree` of the base, and one `git cat-file --batch` that reads
//! every file at the base and in the index or the commit after the change, and resolves `--head` (a process per file spent most of the gate's seconds on a land of a
//! few hundred files). Each object is read once: a second read of the same name is answered from memory.

use std::cell::RefCell;
use std::collections::HashMap;
use std::io::{BufRead, BufReader, Read, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdin, ChildStdout, Command, Stdio};

/// A commit id checked to be 40 hex digits and to name a commit of the repo (the field is private: the only ways to make one are `CommitId::resolve` and the batch's
/// `resolve`).
#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct CommitId(String);

/// What a path is at the base.
#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) enum FileRead {
    Present(String),
    Absent,
    Unreadable(String),
}

/// The repo the gate runs in and its base commit, with the one `git cat-file --batch` of the run.
pub(crate) struct BaseTree {
    repo: PathBuf,
    commit: CommitId,
    reader: Batch,
}

/// Where the tree after the change is read: the work tree (the default), the staged index (`--index` — the index `GIT_INDEX_FILE` names, else the repo's), or a
/// commit (`--head <sha>`). A commit hook judges what it commits and a push hook what it pushes, so neither can be passed by a work tree that differs from it.
pub(crate) enum ReadSource {
    Worktree,
    Index,
    Commit(String),
}

/// The tree after the change, read where `ReadSource` says (the index and a commit through the base's `git cat-file --batch`).
pub(crate) struct ChangedTree<'a> {
    base: &'a BaseTree,
    at: ChangedAt,
}

enum ChangedAt {
    Worktree,
    Index,
    Commit(CommitId),
}

/// One `git cat-file --batch` started on the first read and started again after a failed one, and what it has read by object name.
struct Batch {
    cat: RefCell<Option<CatFile>>,
    read: RefCell<HashMap<String, FileRead>>,
}

/// The running `git cat-file --batch` (killed and waited for on drop, so a gate stopped early leaves no git behind).
struct CatFile {
    child: Child,
    input: ChildStdin,
    output: BufReader<ChildStdout>,
}

/// What `git cat-file --batch` says of one name.
enum Object {
    Missing,
    Found { id: String, kind: String, body: Vec<u8> },
}

impl CatFile {
    fn start(repo: &Path) -> Result<CatFile, String> {
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
        Ok(CatFile { child, input, output })
    }

    /// One object by name (the header is read from the right, so a name with spaces is read as one name).
    fn object(&mut self, name: &str) -> Result<Object, String> {
        if name.contains('\n') {
            return Err(format!("git cat-file: {:?} holds a line break", name));
        }
        writeln!(self.input, "{}", name).and_then(|_| self.input.flush()).map_err(|e| format!("git cat-file: {}", e))?;
        let mut header = String::new();
        self.output.read_line(&mut header).map_err(|e| format!("git cat-file: {}", e))?;
        let header = header.trim_end();
        if header.ends_with(" missing") {
            return Ok(Object::Missing);
        }
        let fields: Vec<&str> = header.split(' ').collect();
        let [id, kind, size] = fields.as_slice() else { return Err(format!("git cat-file: {} gives {:?}", name, header)) };
        let size: usize = size.parse().map_err(|_| format!("git cat-file: unreadable header {:?}", header))?;
        let mut body = vec![0; size + 1];
        self.output.read_exact(&mut body).map_err(|e| format!("git cat-file: {}", e))?;
        body.truncate(size);
        Ok(Object::Found { id: id.to_string(), kind: kind.to_string(), body })
    }
}

impl Drop for CatFile {
    fn drop(&mut self) {
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

fn git(repo: &Path, args: &[&str]) -> Result<Vec<u8>, String> {
    let output = Command::new("git").args(args).current_dir(repo).stdin(Stdio::null()).output().map_err(|e| format!("cannot start git: {}", e))?;
    if output.status.success() {
        Ok(output.stdout)
    } else {
        Err(format!("git {} failed: {}", args.join(" "), String::from_utf8_lossy(&output.stderr).trim()))
    }
}

fn commit_id(reference: &str, id: &str) -> Result<CommitId, String> {
    if id.len() == 40 && id.bytes().all(|b| b.is_ascii_hexdigit()) {
        Ok(CommitId(id.to_string()))
    } else {
        Err(format!("{} does not name a commit ({:?})", reference, id))
    }
}

impl CommitId {
    fn resolve(repo: &Path, reference: &str) -> Result<CommitId, String> {
        let out = git(repo, &["rev-parse", "--verify", "--quiet", &format!("{}^{{commit}}", reference)])?;
        commit_id(reference, String::from_utf8_lossy(&out).trim())
    }
}

impl Batch {
    fn new() -> Batch {
        Batch { cat: RefCell::new(None), read: RefCell::new(HashMap::new()) }
    }

    fn object(&self, repo: &Path, name: &str) -> Result<Object, String> {
        let mut cat = self.cat.borrow_mut();
        if cat.is_none() {
            *cat = Some(CatFile::start(repo)?);
        }
        let Some(reader) = cat.as_mut() else { return Err("git cat-file did not start".to_string()) };
        reader.object(name).inspect_err(|_| *cat = None)
    }

    /// The object `name` as text: absent if git says it is missing, unreadable if git fails, it is not a file, or the bytes are not UTF-8 (`rel` and `at` name it in
    /// the reason). Read once per name.
    fn read(&self, repo: &Path, name: &str, rel: &str, at: &str) -> FileRead {
        if let Some(known) = self.read.borrow().get(name) {
            return known.clone();
        }
        let read = match self.object(repo, name) {
            Ok(Object::Missing) => FileRead::Absent,
            Ok(Object::Found { kind, body, .. }) if kind == "blob" => {
                String::from_utf8(body).map(FileRead::Present).unwrap_or_else(|_| FileRead::Unreadable(format!("{} {} is not UTF-8", rel, at)))
            }
            Ok(Object::Found { kind, .. }) => FileRead::Unreadable(format!("{} {} is a {}, not a file", rel, at, kind)),
            Err(reason) => FileRead::Unreadable(reason),
        };
        self.read.borrow_mut().insert(name.to_string(), read.clone());
        read
    }

    /// The commit `reference` names, through the batch (no process of its own).
    fn resolve(&self, repo: &Path, reference: &str) -> Result<CommitId, String> {
        match self.object(repo, &format!("{}^{{commit}}", reference))? {
            Object::Found { id, kind, .. } if kind == "commit" => commit_id(reference, &id),
            _ => Err(format!("{} does not name a commit", reference)),
        }
    }
}

impl BaseTree {
    pub(crate) fn open(repo: &Path, reference: &str) -> Result<BaseTree, String> {
        Ok(BaseTree { repo: repo.to_path_buf(), commit: CommitId::resolve(repo, reference)?, reader: Batch::new() })
    }

    pub(crate) fn repo(&self) -> &Path {
        &self.repo
    }

    /// A file's contents at the base: absent if the base has no such path, unreadable if git fails or the bytes are not UTF-8.
    pub(crate) fn read(&self, rel: &str) -> FileRead {
        self.reader.read(&self.repo, &format!("{}:{}", self.commit.0, rel), rel, "at the base")
    }

    /// The paths of the base's tree (for the declaration's file globs).
    pub(crate) fn paths(&self) -> Result<Vec<String>, String> {
        let out = git(&self.repo, &["ls-tree", "-r", "--name-only", &self.commit.0])?;
        Ok(String::from_utf8_lossy(&out).lines().map(str::to_string).collect())
    }
}

/// The index git reads: Err if `GIT_INDEX_FILE` names a file that is not there (git would read it as an empty index, and every path would pass as removed).
fn index_is_there(repo: &Path) -> Result<(), String> {
    match std::env::var_os("GIT_INDEX_FILE") {
        Some(named) if !repo.join(&named).is_file() => Err(format!("GIT_INDEX_FILE names {}, which is not a file", Path::new(&named).display())),
        _ => Ok(()),
    }
}

impl<'a> ChangedTree<'a> {
    /// The tree after the change in the base's repo (Err if `GIT_INDEX_FILE` names no file or `--head` does not name a commit).
    pub(crate) fn open(base: &'a BaseTree, source: ReadSource) -> Result<ChangedTree<'a>, String> {
        let at = match source {
            ReadSource::Worktree => ChangedAt::Worktree,
            ReadSource::Index => {
                index_is_there(&base.repo)?;
                ChangedAt::Index
            }
            ReadSource::Commit(reference) => ChangedAt::Commit(base.reader.resolve(&base.repo, &reference)?),
        };
        Ok(ChangedTree { base, at })
    }

    /// A file of the tree after the change (absent if the change removed it — from the index or the commit, never from the work tree).
    pub(crate) fn read(&self, rel: &str) -> FileRead {
        let (repo, reader) = (&self.base.repo, &self.base.reader);
        match &self.at {
            ChangedAt::Worktree => read_worktree(repo, rel),
            // `:<path>` is the path's entry of stage 0 in the index — the entry a commit takes (an unmerged path has none, and cannot be committed).
            ChangedAt::Index => reader.read(repo, &format!(":{}", rel), rel, "in the index"),
            ChangedAt::Commit(commit) => reader.read(repo, &format!("{}:{}", commit.0, rel), rel, &format!("at {}", commit.0)),
        }
    }

    /// Whether the tree after the change has the path (a path that cannot be read counts as there, so reading it fails closed).
    pub(crate) fn has(&self, rel: &str) -> bool {
        match &self.at {
            ChangedAt::Worktree => self.base.repo.join(rel).exists(),
            ChangedAt::Index | ChangedAt::Commit(_) => !matches!(self.read(rel), FileRead::Absent),
        }
    }
}

fn read_worktree(repo: &Path, rel: &str) -> FileRead {
    let path = repo.join(rel);
    if !path.exists() {
        return FileRead::Absent;
    }
    match std::fs::read_to_string(&path) {
        Ok(text) => FileRead::Present(text),
        Err(error) => FileRead::Unreadable(format!("{}: {}", rel, error)),
    }
}
