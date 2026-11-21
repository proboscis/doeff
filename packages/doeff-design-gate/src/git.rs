//! The base commit and what is read at it. The gate reads the declaration, the ledgers, and the approvals only at the base, so a change cannot loosen what judges it.

use std::cell::RefCell;
use std::io::{BufRead, BufReader, Read, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdin, ChildStdout, Command, Stdio};

/// A commit id checked to be 40 hex digits and to name a commit of the repo (the field is private: the only way to make one is `CommitId::resolve`).
#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct CommitId(String);

/// What a path is at the base.
#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) enum FileRead {
    Present(String),
    Absent,
    Unreadable(String),
}

/// The repo the gate runs in and its base commit, with one `git cat-file --batch` that reads every file at the base (one process for all the reads — a process per file
/// spent most of the gate's seconds on a land of a few hundred files).
pub(crate) struct BaseTree {
    repo: PathBuf,
    commit: CommitId,
    reader: RefCell<Option<CatFile>>,
}

/// The running `git cat-file --batch` (killed and waited for on drop, so a gate stopped early leaves no git behind).
struct CatFile {
    child: Child,
    input: ChildStdin,
    output: BufReader<ChildStdout>,
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

    /// One object by name: Ok(None) if git says it is missing.
    fn read(&mut self, name: &str) -> Result<Option<Vec<u8>>, String> {
        writeln!(self.input, "{}", name).and_then(|_| self.input.flush()).map_err(|e| format!("git cat-file: {}", e))?;
        let mut header = String::new();
        self.output.read_line(&mut header).map_err(|e| format!("git cat-file: {}", e))?;
        let fields: Vec<&str> = header.split_whitespace().collect();
        match fields.as_slice() {
            [_, "missing"] => Ok(None),
            [_, "blob", size] => {
                let size: usize = size.parse().map_err(|_| format!("git cat-file: unreadable header {:?}", header))?;
                let mut body = vec![0; size + 1];
                self.output.read_exact(&mut body).map_err(|e| format!("git cat-file: {}", e))?;
                body.truncate(size);
                Ok(Some(body))
            }
            _ => Err(format!("git cat-file: {} is not a file ({:?})", name, header.trim())),
        }
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

impl CommitId {
    fn resolve(repo: &Path, reference: &str) -> Result<CommitId, String> {
        let out = git(repo, &["rev-parse", "--verify", "--quiet", &format!("{}^{{commit}}", reference)])?;
        let id = String::from_utf8_lossy(&out).trim().to_string();
        if id.len() == 40 && id.bytes().all(|b| b.is_ascii_hexdigit()) {
            Ok(CommitId(id))
        } else {
            Err(format!("{} does not name a commit ({:?})", reference, id))
        }
    }
}

impl BaseTree {
    pub(crate) fn open(repo: &Path, reference: &str) -> Result<BaseTree, String> {
        Ok(BaseTree { repo: repo.to_path_buf(), commit: CommitId::resolve(repo, reference)?, reader: RefCell::new(None) })
    }

    pub(crate) fn repo(&self) -> &Path {
        &self.repo
    }

    /// A file's contents at the base: absent if the base has no such path, unreadable if git fails or the bytes are not UTF-8.
    pub(crate) fn read(&self, rel: &str) -> FileRead {
        let mut reader = self.reader.borrow_mut();
        if reader.is_none() {
            match CatFile::start(&self.repo) {
                Ok(started) => *reader = Some(started),
                Err(reason) => return FileRead::Unreadable(reason),
            }
        }
        let Some(cat) = reader.as_mut() else { return FileRead::Unreadable("git cat-file did not start".to_string()) };
        match cat.read(&format!("{}:{}", self.commit.0, rel)) {
            Ok(None) => FileRead::Absent,
            Ok(Some(bytes)) => String::from_utf8(bytes).map(FileRead::Present).unwrap_or_else(|_| FileRead::Unreadable(format!("{} at the base is not UTF-8", rel))),
            Err(reason) => {
                *reader = None;
                FileRead::Unreadable(reason)
            }
        }
    }

    /// The paths of the base's tree (for the declaration's file globs).
    pub(crate) fn paths(&self) -> Result<Vec<String>, String> {
        let out = git(&self.repo, &["ls-tree", "-r", "--name-only", &self.commit.0])?;
        Ok(String::from_utf8_lossy(&out).lines().map(str::to_string).collect())
    }
}

/// A file of the tree after the change (absent if the change removed it).
pub(crate) fn read_now(repo: &Path, rel: &str) -> FileRead {
    let path = repo.join(rel);
    if !path.exists() {
        return FileRead::Absent;
    }
    match std::fs::read_to_string(&path) {
        Ok(text) => FileRead::Present(text),
        Err(error) => FileRead::Unreadable(format!("{}: {}", rel, error)),
    }
}
