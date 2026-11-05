//! Test of the machine's check-slot gate — doeff-linter in heavy mode passes itself to the command wrapper `run` of the canonical source under HOME
//! (`dotfiles/agent/tests/machine_check_slots.py`), and runs after taking a slot.
//!
//! The tests that copy the real canonical source point HOME at a temporary dir, place a copy of the canonical module and a declaration of K = 1 and store = a temporary dir next to it, and fire
//! (they do not touch the running machine's slots /tmp/agora-check-slots — same form as dotfiles' agent/tests/test_machine_check_slots.py).
//! The canonical source is copied from the dotfiles under the HOME of the machine that fires — on a machine without it, the test goes red stating what cannot be verified (an unrun test is not passed).
//! The shape of the gate path (which modes go through the canonical source, the same arguments, the re-entry marker) is checked with a fake canonical source that records arguments and env and execs the command.

use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::time::{Duration, Instant};

const LINTER: &str = env!("CARGO_BIN_EXE_doeff-linter");
/// Upper bound for waiting on a child process.
const DONE: Duration = Duration::from_secs(60);

/// The canonical source of the firing machine (under HOME). If missing, red, stating what cannot be verified.
fn real_canon() -> PathBuf {
    let home = std::env::var_os("HOME").expect("HOME is not set");
    let canon = Path::new(&home).join("dotfiles/agent/tests/machine_check_slots.py");
    assert!(
        canon.is_file(),
        "canonical source {} is missing — this test runs on a machine that placed the machine check-slot canonical source (dotfiles) under HOME (cannot verify the linter that waits for a slot)",
        canon.display()
    );
    canon
}

/// The python of the firing machine (same `uv python find` as when the linter runs the canonical source).
fn python() -> PathBuf {
    let found = Command::new("uv").args(["python", "find"]).output().expect("cannot start uv");
    assert!(found.status.success(), "uv python find: {}", String::from_utf8_lossy(&found.stderr));
    PathBuf::from(String::from_utf8_lossy(&found.stdout).trim())
}

/// A small repo to lint (one Python file; DOEFF016 = one relative-import hit — if lint runs, exit 1).
fn small_repo(root: &Path) -> PathBuf {
    let repo = root.join("repo");
    fs::create_dir_all(&repo).unwrap();
    fs::write(repo.join("pyproject.toml"), "[tool.doeff-linter]\nenable = [\"DOEFF016\"]\n").unwrap();
    fs::write(repo.join("tool.py"), "from . import sibling\n").unwrap();
    repo
}

/// The linter command with HOME pointed at a temporary dir. The place where uv looks for python stays the firing machine's (finds the same python even if HOME changes).
fn linter_under(home: &Path, repo: &Path) -> Command {
    let mut command = Command::new(LINTER);
    command.current_dir(repo).env("HOME", home).env_remove("DOEFF_LINTER_CHECK_SLOT");
    if std::env::var_os("UV_PYTHON_INSTALL_DIR").is_none() && std::env::var_os("XDG_DATA_HOME").is_none() {
        let real_home = std::env::var_os("HOME").expect("HOME is not set");
        command.env("XDG_DATA_HOME", Path::new(&real_home).join(".local/share"));
    }
    command
}

/// Waits until a condition holds. Reason for polling: there is no channel for another process to announce that it took a slot or declared a wait (observation outside the fixture).
fn wait_until(what: &str, mut ready: impl FnMut() -> bool) {
    let deadline = Instant::now() + DONE;
    while !ready() {
        assert!(Instant::now() < deadline, "{what}");
        std::thread::sleep(Duration::from_millis(50));
    }
}

fn read(path: &Path) -> String {
    fs::read_to_string(path).unwrap_or_default()
}

fn finish(mut child: Child, what: &str) -> std::process::ExitStatus {
    let deadline = Instant::now() + DONE;
    loop {
        if let Some(status) = child.try_wait().unwrap() {
            return status;
        }
        if Instant::now() >= deadline {
            let _ = child.kill();
            panic!("{what} did not finish");
        }
        std::thread::sleep(Duration::from_millis(50));
    }
}

/// A HOME with a copy of the canonical source and a declaration of K = 1, 1 process per slot, store = a temporary dir, wait limit = wait_s, plus that canonical source and
/// the file for slot 0. The declaration's keys write everything the canonical source needs (without processesPerSlot the canonical source cannot read the declaration and runs without taking a slot).
fn canon_home(root: &Path, wait_s: u64) -> (PathBuf, PathBuf, PathBuf) {
    let home = root.join("home");
    let tests_dir = home.join("dotfiles/agent/tests");
    fs::create_dir_all(&tests_dir).unwrap();
    let canon = tests_dir.join("machine_check_slots.py");
    fs::copy(real_canon(), &canon).unwrap();
    let slots = root.join("slots");
    let declaration =
        serde_json::json!({"slots": 1, "processesPerSlot": 1, "dir": slots.to_string_lossy(), "waitLimitSeconds": wait_s});
    fs::write(tests_dir.join("machine_check_slots.json"), declaration.to_string()).unwrap();
    (home, canon, slots.join("slot-0.lock"))
}

/// Holder = a command that takes 1 slot via the canonical source's wrapper and holds it until stdin is closed (returns after seeing the slot taken).
fn holding(home: &Path, canon: &Path, slot: &Path) -> Child {
    let python = python();
    let holder = Command::new(&python)
        .arg(canon)
        .args(["run", "--label", "long-check", "--"])
        .arg(&python)
        .args(["-c", "import sys; sys.stdin.read()"])
        .env("HOME", home)
        .env_remove("AGORA_CONVERSATION_ID")
        .stdin(Stdio::piped())
        .spawn()
        .unwrap();
    wait_until("the holder did not take the slot", || read(slot).starts_with("pid "));
    holder
}

/// The internal marker (env name) the canonical source passes down to children of the process holding a slot — read from `HOLDER_ENV = "…"` in the canonical source's source (the name is not copied into this test).
fn canon_holder_env() -> String {
    let text = read(&real_canon());
    let line = text.lines().find(|line| line.starts_with("HOLDER_ENV = ")).expect("no HOLDER_ENV line in the canonical source");
    line.split('"').nth(1).expect("value of HOLDER_ENV").to_string()
}

#[test]
fn a_linter_beyond_the_slots_waits_names_the_holder_and_runs_when_one_frees() {
    let tmp = tempfile::TempDir::new().unwrap();
    let (home, canon, slot) = canon_home(tmp.path(), 60);
    let repo = small_repo(tmp.path());
    let mut holder = holding(&home, &canon, &slot);

    let err_path = tmp.path().join("linter.err");
    let mut linter = linter_under(&home, &repo)
        .args(["--no-log", "."])
        .env_remove("AGORA_CONVERSATION_ID")
        .stdout(Stdio::piped())
        .stderr(fs::File::create(&err_path).unwrap())
        .spawn()
        .unwrap();
    wait_until("linter が待ちを名乗らない", || read(&err_path).contains("埋まっているので待つ"));
    let waiting = read(&err_path);
    assert!(
        waiting.contains(&format!("保持 pid {} long-check", holder.id())) && waiting.contains("待つのは doeff-linter lint repo"),
        "{waiting}"
    );
    assert!(linter.try_wait().unwrap().is_none(), "the linter finished although the slot was full");
    assert!(!read(&slot).contains("doeff-linter"), "the linter took the slot although it was full");

    // When the holder releases, the linter takes the slot on that beat, lints, and finishes (announces taking the slot, lint hits, releases when done).
    holder.stdin.take().unwrap().write_all(b"").unwrap();
    assert!(finish(holder, "holder").success());
    let output = linter.wait_with_output().unwrap();
    let stdout = String::from_utf8_lossy(&output.stdout);
    let err = read(&err_path);
    assert_eq!(output.status.code(), Some(1), "should be 1 from a hit (DOEFF016): {stdout}\n{err}");
    assert!(stdout.contains("DOEFF016"), "{stdout}");
    assert!(err.contains("を取った") && err.contains("— doeff-linter lint repo"), "{err}");
    assert_eq!(read(&slot), "", "the slot announcement remained after the linter finished");
}

#[test]
fn a_stale_holder_marker_does_not_let_the_linter_skip_a_full_slot_and_the_expiry_exits_75() {
    // Whether it is nested (whether an ancestor holds a slot) is judged only by the canonical source — a linter that merely inherited the canonical source's internal marker (env) is not inside
    // if the pid the marker points to does not hold a slot (a child left after the holder left, a background child — blind review B).
    // If the slot stays full, it ends at the wait limit without linting and returns the linter's public exit code 75 (not run).
    let tmp = tempfile::TempDir::new().unwrap();
    let (home, canon, slot) = canon_home(tmp.path(), 2);
    let repo = small_repo(tmp.path());
    let mut holder = holding(&home, &canon, &slot);
    let output = linter_under(&home, &repo)
        .args(["--no-log", "."])
        .env(canon_holder_env(), std::process::id().to_string())
        .env_remove("AGORA_CONVERSATION_ID")
        .output()
        .unwrap();
    holder.stdin.take().unwrap().write_all(b"").unwrap();
    assert!(finish(holder, "holder").success());
    let stdout = String::from_utf8_lossy(&output.stdout);
    let err = String::from_utf8_lossy(&output.stderr);
    assert_eq!(output.status.code(), Some(75), "a linter that merely inherited the marker ran outside a slot: {stdout}\n{err}");
    assert!(!stdout.contains("DOEFF016"), "it was supposed to be not run but it linted: {stdout}");
    assert!(err.contains("埋まっているので待つ") && err.contains("doeff-linter lint repo は未実行"), "{err}");
}

/// A fake canonical source — records arguments and the re-entry marker one line each, and execs the command after `--` (takes no slot).
const FAKE_CANON: &str = r#"import json, os, sys
with open(os.path.join(os.path.dirname(__file__), "calls.jsonl"), "a") as log:
    log.write(json.dumps({"argv": sys.argv[1:], "gated": os.environ.get("DOEFF_LINTER_CHECK_SLOT")}) + "\n")
rest = sys.argv[sys.argv.index("--") + 1:]
os.execv(rest[0], rest)
"#;

fn fake_home(root: &Path) -> (PathBuf, PathBuf) {
    let home = root.join("home");
    let tests_dir = home.join("dotfiles/agent/tests");
    fs::create_dir_all(&tests_dir).unwrap();
    fs::write(tests_dir.join("machine_check_slots.py"), FAKE_CANON).unwrap();
    (home, tests_dir.join("calls.jsonl"))
}

fn calls(log: &Path) -> Vec<serde_json::Value> {
    read(log).lines().map(|line| serde_json::from_str(line).unwrap()).collect()
}

#[test]
fn a_repo_scan_hands_itself_and_its_arguments_to_the_canon_once() {
    let tmp = tempfile::TempDir::new().unwrap();
    let (home, log) = fake_home(tmp.path());
    let repo = small_repo(tmp.path());
    let output = linter_under(&home, &repo).args(["--no-log", "--output-format", "json", "."]).output().unwrap();
    assert_eq!(output.status.code(), Some(1), "{}", String::from_utf8_lossy(&output.stderr));
    assert!(String::from_utf8_lossy(&output.stdout).contains("DOEFF016"));
    let seen = calls(&log);
    assert_eq!(seen.len(), 1, "passes through the canonical source only once (the exec'd linter does not pass through, by the marker): {seen:?}");
    let argv: Vec<String> = seen[0]["argv"].as_array().unwrap().iter().map(|v| v.as_str().unwrap().to_string()).collect();
    let exe = fs::canonicalize(LINTER).unwrap();
    assert_eq!(
        argv,
        ["run", "--label", "doeff-linter lint repo", "--", exe.to_str().unwrap(), "--no-log", "--output-format", "json", "."]
    );
    assert_eq!(seen[0]["gated"], "1", "a re-entry marker is attached when handing to the wrapper (descendant linters do not pass through python)");
}

#[test]
fn cheap_and_caller_bounded_modes_do_not_go_through_the_canon() {
    let tmp = tempfile::TempDir::new().unwrap();
    let (home, log) = fake_home(tmp.path());
    let repo = small_repo(tmp.path());
    for args in [
        vec!["--list-rules"],
        vec!["--split-rules", "tool.py"],
        vec!["--affected-tests", "tool.py", "--test-pattern", "test_*.py"],
    ] {
        // The temporary repo is not a git tree — `--split-rules` and `--affected-tests` may refuse for being unable to read git. What is checked is only that they do not pass through the canonical source.
        let output = linter_under(&home, &repo).args(&args).output().unwrap();
        assert!(output.status.code().is_some(), "{args:?} died by a signal: {}", String::from_utf8_lossy(&output.stderr));
    }
    let mut stdin_run = linter_under(&home, &repo)
        .args(["--output-format", "editor-json", "--no-log", "--stdin", "--path", "tool.py"])
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .spawn()
        .unwrap();
    stdin_run.stdin.take().unwrap().write_all(b"import os\n").unwrap();
    assert!(stdin_run.wait_with_output().unwrap().status.code().is_some());
    assert_eq!(calls(&log), Vec::<serde_json::Value>::new(), "a light mode passed through the canonical source");
}

/// Makes small_repo a git tree, commits only pyproject.toml as the baseline, and stages tool.py, which has a hit (DOEFF016).
fn stage_in_git(repo: &Path) {
    let git = |args: &[&str]| {
        let output = Command::new("git")
            .args(["-c", "user.name=check-slot-test", "-c", "user.email=check-slot-test@example.invalid", "-c", "commit.gpgsign=false"])
            .args(args)
            .current_dir(repo)
            .env_remove("GIT_DIR")
            .env_remove("GIT_INDEX_FILE")
            .env_remove("GIT_WORK_TREE")
            .output()
            .unwrap();
        assert!(output.status.success(), "git {args:?}: {}", String::from_utf8_lossy(&output.stderr));
    };
    git(&["init", "-q", "-b", "main"]);
    git(&["add", "pyproject.toml"]);
    git(&["commit", "-q", "-m", "baseline"]);
    git(&["add", "tool.py"]);
}

/// The pre-commit check is not a test, so — do not make it wait behind other tests: the commit hook
/// (`--commit-hook`) is a short blocking check that applies per-file rules to staged files, and has its own CPU-second limit (timeout_s).
/// Neither the parent nor the child linter (editor-json) fired on the paths the parent staged passes through the canonical source — a commit does not stop on a slot wait.
/// That the child ran is seen by stopping on the hit of the staged tool.py (DOEFF016).
#[test]
fn the_commit_hook_and_its_child_linters_do_not_go_through_the_canon() {
    let tmp = tempfile::TempDir::new().unwrap();
    let (home, log) = fake_home(tmp.path());
    let repo = small_repo(tmp.path());
    stage_in_git(&repo);
    let output = linter_under(&home, &repo)
        .arg("--commit-hook")
        .env("DOEFF_LINTER_CACHE_DIR", tmp.path().join("cache")).env("DOEFF_LINTER_SWEEP_INLINE", "1")
        .env_remove("GIT_DIR")
        .env_remove("GIT_INDEX_FILE")
        .env_remove("GIT_WORK_TREE")
        .stdin(Stdio::null())
        .output()
        .unwrap();
    let err = String::from_utf8_lossy(&output.stderr);
    assert_eq!(output.status.code(), Some(1), "should stop on the hit (DOEFF016) of the staged tool.py: {err}");
    assert!(err.contains("violation in staged file: tool.py:1: DOEFF016"), "{err}");
    assert_eq!(calls(&log), Vec::<serde_json::Value>::new(), "the commit hook (the parent or the child linter) passed through the canonical source");
}

#[test]
fn a_linter_inside_the_gate_or_without_a_canon_runs_directly() {
    let tmp = tempfile::TempDir::new().unwrap();
    let (home, log) = fake_home(tmp.path());
    let repo = small_repo(tmp.path());
    let inside = linter_under(&home, &repo).args(["--no-log", "."]).env("DOEFF_LINTER_CHECK_SLOT", "1").output().unwrap();
    assert_eq!(inside.status.code(), Some(1));
    assert_eq!(calls(&log), Vec::<serde_json::Value>::new(), "a linter marked as such passed through the canonical source again");

    let bare_home = tmp.path().join("bare-home");
    fs::create_dir_all(&bare_home).unwrap();
    let bare = linter_under(&bare_home, &repo).args(["--no-log", "."]).output().unwrap();
    assert_eq!(bare.status.code(), Some(1));
    let said = String::from_utf8_lossy(&bare.stderr);
    assert!(!said.contains("machine check slot") && !said.contains("check-slot:"), "正本の無い機体で枠の名乗りを出した: {said}");
}

/// The code of the linter's source with `//` comments removed.
fn code_of(path: &Path) -> String {
    read(path).lines().map(|line| line.split("//").next().unwrap_or("")).collect::<Vec<_>>().join("\n")
}

fn rust_sources(dir: &Path) -> Vec<PathBuf> {
    fs::read_dir(dir)
        .unwrap()
        .map(|entry| entry.unwrap().path())
        .flat_map(|path| if path.is_dir() { rust_sources(&path) } else { vec![path] })
        .filter(|path| path.extension().is_some_and(|ext| ext == "rs"))
        .collect()
}

#[test]
fn the_linter_keeps_no_copy_of_the_slot_values() {
    // The owner of the values is one declaration next to the canonical source (K, store, wait limit). The linter's code spells neither those values nor the declaration file's name —
    // it spells only the canonical source's location (relative to HOME). It also does not spell the canonical source's internal marker (the env name passed to children of a slot-holding process) —
    // the nesting decision is not copied into the linter (blind review B).
    let holder_env = canon_holder_env();
    let declaration_path = real_canon().with_file_name("machine_check_slots.json");
    let declaration: serde_json::Value = serde_json::from_str(&read(&declaration_path)).unwrap();
    let dir = declaration["dir"].as_str().expect("dir of the declaration");
    let slots = declaration["slots"].as_u64().expect("slots of the declaration").to_string();
    let wait = declaration["waitLimitSeconds"].as_f64().expect("waitLimitSeconds of the declaration");
    let sources = rust_sources(&Path::new(env!("CARGO_MANIFEST_DIR")).join("src"));
    let gate = Path::new(env!("CARGO_MANIFEST_DIR")).join("src/check_slot.rs");
    assert!(sources.contains(&gate));
    let copies: Vec<String> = sources
        .iter()
        .flat_map(|path| {
            let code = code_of(path);
            let named = [dir, "machine_check_slots.json", "waitLimitSeconds", "slot-", holder_env.as_str()]
                .into_iter()
                .filter(|word| code.contains(word))
                .map(|word| format!("{}: {word}", path.display()))
                .collect::<Vec<_>>();
            let numbers = if *path == gate {
                // 識別子の中の数字(from_utf8 の 8)を数えない — 語(英数字と _ と .)の単位で、値と同じ綴りの語を探す。
                let tokens: Vec<&str> = code.split(|c: char| !(c.is_ascii_alphanumeric() || c == '_' || c == '.')).collect();
                [slots.clone(), format!("{wait}"), format!("{}", wait as u64)]
                    .into_iter()
                    .filter(|value| tokens.contains(&value.as_str()))
                    .map(|value| format!("{}: number {value}", path.display()))
                    .collect()
            } else {
                Vec::new()
            };
            named.into_iter().chain(numbers)
        })
        .collect();
    assert_eq!(copies, Vec::<String>::new(), "a copy of the slot value is in the linter's code");
}
