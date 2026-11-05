//! Test that the answers of the resident process (`fsmonitor`) follow changes in the working tree.
#![cfg(target_os = "linux")]
//! Creates a temporary git repo, makes edits, additions, deletions, directory additions and removals, and index rewrites, and checks the answers to queries.

use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::Duration;

use doeff_linter::project::fsmonitor::{self, daemon, Answer};
use doeff_linter::project::git_index::GitIndex;

fn git(repo: &Path, args: &[&str]) {
    let status = std::process::Command::new("git")
        .args(args)
        .current_dir(repo)
        .env("GIT_CONFIG_GLOBAL", "/dev/null")
        .status()
        .expect("git runs");
    assert!(status.success(), "git {args:?}");
}

fn write(repo: &Path, rel: &str, text: &str) {
    let path = repo.join(rel);
    std::fs::create_dir_all(path.parent().unwrap()).unwrap();
    std::fs::write(path, text).unwrap();
}

fn ask(dir: &Path, repo: &Path) -> Answer {
    // The first query on a working tree is made to wait while it is built (queries can also arrive before the resident process is up), so try a few times.
    for _ in 0..40 {
        if let Some(answer) = fsmonitor::ask(dir, repo) {
            if answer.ok {
                return answer;
            }
        }
        std::thread::sleep(Duration::from_millis(100));
    }
    panic!("the resident process does not answer");
}

struct Setup {
    dir: PathBuf,
    repo: PathBuf,
    stop: Arc<AtomicBool>,
    handle: Option<std::thread::JoinHandle<()>>,
}

impl Drop for Setup {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::Relaxed);
        if let Some(handle) = self.handle.take() {
            let _ = handle.join();
        }
        let _ = std::fs::remove_dir_all(self.dir.parent().unwrap());
    }
}

fn setup(name: &str) -> Setup {
    let base = std::env::temp_dir().join(format!("fsmonitor-test-{name}-{}", std::process::id()));
    let repo = base.join("repo");
    let dir = base.join("state");
    std::fs::create_dir_all(&repo).unwrap();
    git(&repo, &["init", "-q"]);
    write(&repo, "a/x.hy", "(defk x [] 1)\n");
    write(&repo, "b.py", "x = 1\n");
    write(&repo, "notes.txt", "memo\n");
    // Use a modification time earlier than the time written to the index (a line with the same instant is racy, and answering dirty is correct).
    std::thread::sleep(Duration::from_millis(1200));
    git(&repo, &["add", "-A"]);
    let stop = Arc::new(AtomicBool::new(false));
    let handle = {
        let (dir, stop) = (dir.clone(), stop.clone());
        std::thread::spawn(move || {
            daemon::run(&dir, Duration::from_millis(20), &stop, Duration::from_secs(3600));
        })
    };
    // Wait until the resident process places its socket (a query before that would try to start the test binary itself as the resident process).
    for _ in 0..100 {
        if fsmonitor::socket_path(&dir).exists() {
            break;
        }
        std::thread::sleep(Duration::from_millis(20));
    }
    Setup { dir, repo, stop, handle: Some(handle) }
}

#[test]
fn a_fresh_worktree_has_nothing_dirty_and_follows_edits_adds_and_removals() {
    let s = setup("follow");
    let answer = ask(&s.dir, &s.repo);
    assert!(answer.dirty.is_empty(), "{:?}", answer.dirty);
    assert!(answer.extras.is_empty() && answer.missing.is_empty());
    assert_eq!(answer.ident, GitIndex::read(&s.repo).unwrap().ident());

    // Edit — always included before the answer (notifications are queued within the write).
    std::thread::sleep(Duration::from_millis(20));
    write(&s.repo, "a/x.hy", "(defk x [] 2222)\n");
    assert_eq!(ask(&s.dir, &s.repo).dirty, vec!["a/x.hy".to_string()]);

    // Addition (under a new dir, deep down, an extension that is not picked up).
    write(&s.repo, "c/d/e/new.hy", "(defk n [] 1)\n");
    write(&s.repo, "c/skip.txt", "x\n");
    write(&s.repo, "node_modules/p/ignored.py", "x = 1\n");
    let answer = ask(&s.dir, &s.repo);
    assert_eq!(answer.extras, vec!["c/d/e/new.hy".to_string()]);

    // Deletion, and deletion of a whole dir.
    std::fs::remove_file(s.repo.join("b.py")).unwrap();
    std::fs::remove_dir_all(s.repo.join("c")).unwrap();
    let answer = ask(&s.dir, &s.repo);
    assert!(answer.extras.is_empty(), "{:?}", answer.extras);
    assert_eq!(answer.missing, vec!["b.py".to_string()]);
    assert!(answer.dirty.contains(&"b.py".to_string()));
}

#[test]
fn rewriting_the_index_changes_the_ident_and_clears_the_dirt() {
    let s = setup("index");
    let before = ask(&s.dir, &s.repo);
    write(&s.repo, "a/x.hy", "(defk x [] 3)\n");
    assert_eq!(ask(&s.dir, &s.repo).dirty, vec!["a/x.hy".to_string()]);
    std::thread::sleep(Duration::from_millis(1200));
    git(&s.repo, &["add", "-A"]);
    let after = ask(&s.dir, &s.repo);
    assert_ne!(before.ident, after.ident);
    assert_eq!(after.ident, GitIndex::read(&s.repo).unwrap().ident());
    assert!(after.dirty.is_empty(), "{:?}", after.dirty);
}

#[test]
fn a_directory_that_is_not_a_git_worktree_is_refused() {
    let s = setup("refuse");
    let plain = s.repo.parent().unwrap().join("plain");
    std::fs::create_dir_all(&plain).unwrap();
    std::thread::sleep(Duration::from_millis(300));
    let answer = fsmonitor::ask(&s.dir, &plain);
    assert!(answer.map_or(true, |a| !a.ok));
}
