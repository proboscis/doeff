//! Machine check slots — a heavy-mode doeff-linter, at startup, hands itself to the command wrapper `run` of the canonical machine-check-slot source
//! (`~/dotfiles/agent/tests/machine_check_slots.py`), takes one slot, and then runs.
//!
//! Why: limit the process trees of checks running at once on the same machine (Pod) to the K declared in the canonical source. doeff-linter is fired directly by
//! conversations along many paths (`uv run doeff-linter`, poe lint, land_lint_gate, the daily stage, inside code-quality), and wrapping per path
//! leaves forgotten paths running outside the slots (at 10-12 08:39 JST on zeus with load 105, one poe lint at 1.9 cores and two direct conversation runs at 0.64 core ran outside the slots).
//! Taking the slot at the single point of binary startup puts every path into the same slots. The commit hook (`--commit-hook`) is a blocking inspection, not a check, so it
//! takes none (see the weight note).
//!
//! Shape: exec this process into python (same pid — the caller's signals and timeouts still reach this pid as before),
//! and re-run as `python <canon> run --label <name> -- <self> <same args>`. The canonical source takes a slot (if full, it announces the holder and waits, exiting 75
//! at the wait limit), runs this process as a child, and releases the slot when done. If the wrapper dies by SIGKILL the child dies too (die_with_wrapper in the canonical source).
//! The slot values (count, location, wait limit) and how to take them are held only by the canonical source and the adjacent machine_check_slots.json, not here —
//! what is held here is only the location of the canonical source, which modes are heavy, and the re-entry mark.
//! Descendants of a slot-holding process (a linter inside pytest, a linter inside a wrapped stage) are recognized by the canonical source via the ancestor chain and do not take a second slot.
//! A linter started from the wrapper carries the re-entry mark (GATED_ENV), and its descendants do not go through python. The child linter started by `--commit-hook`, which takes no slot
//! (editor-json — a heavy-mode shape), also carries the same mark and runs outside the slots as part of the hook.
//! On a machine without the canonical source (a machine without dotfiles), or when python cannot be found, it runs without taking a slot (same as when the canonical source cannot read its declaration).
//! Cost: 0.2 to 0.6 seconds per heavy-mode run for `uv python find` and starting python (measured at zeus load 70 to 90).

use std::ffi::{OsStr, OsString};
use std::path::{Path, PathBuf};
use std::process::Command;

/// Re-entry mark — set on a linter started by the wrapper (and its descendants) and on the child linter started by `--commit-hook` (commit_hook::run_linter).
/// If set, it is not handed to the wrapper.
pub const GATED_ENV: &str = "DOEFF_LINTER_CHECK_SLOT";

/// Location of the canonical source (relative to HOME) — the same root from which the root conftest of each agora repo reads the intake canonical source.
pub const CANON_UNDER_HOME: &str = "dotfiles/agent/tests/machine_check_slots.py";

/// Weight of the run — whether it takes a slot.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Weight {
    /// Takes no slot (only prints a table, or a query the caller cuts off with a short limit).
    Light,
    /// Takes a slot (lints a repo or a set of paths). The name is the mode name that goes into the holder's announcement.
    Heavy(&'static str),
}

/// Argument fields needed to decide the mode (copied from Args in main).
#[derive(Debug, Clone, Copy, Default)]
pub struct Mode {
    pub list_rules: bool,
    pub split_rules: bool,
    pub affected_tests: bool,
    pub stdin: bool,
    pub hook: bool,
    pub commit_hook: bool,
    pub editor_json: bool,
}

/// Decides the weight of the run (pure).
///
/// Does not take: `--list-rules`, `--split-rules` (only print the rule table — 0 CPU seconds) / `--affected-tests` (a query that selects checks, 1.8 CPU seconds,
/// 0.15 core — its caller land_focus_gate cuts it off at 30 seconds, and if it cannot measure it adds no reverse-dependency checks, so waiting would make checks fail) / `--stdin`
/// (one file before saving — the hook right after a write cuts off at 3 seconds overall and silently passes, so waiting would disable the hook) / `--commit-hook`
/// (not a test but a blocking inspection that applies rules decidable per single file to staged files — each child linter run has its own CPU-second limit
/// [tool.doeff-linter.commit_hook] timeout_s. Queued behind the slots it waited up to 20 minutes behind pytest and type checks, and the commit was
/// cut off by the limit of the conversation's tool — a pre-commit inspection is not a test, so it must not wait behind other checks.
/// The child linter takes none either — commit_hook::run_linter starts it with GATED_ENV set).
/// Takes: `--hook` (Cursor's stop hook — the whole workspace), editor-json and text / json over a whole repo (over 100 CPU seconds).
pub fn weight(mode: Mode) -> Weight {
    if mode.list_rules || mode.split_rules || mode.affected_tests || mode.stdin || mode.commit_hook {
        Weight::Light
    } else if mode.hook {
        Weight::Heavy("--hook")
    } else if mode.editor_json {
        Weight::Heavy("editor-json")
    } else {
        Weight::Heavy("lint")
    }
}

/// Holder's announcement (pure) — the mode name and the name of the dir it ran in (the worktree name). The conversation id is added by the canonical source.
pub fn label(mode_name: &str, cwd: Option<&Path>) -> String {
    let place = cwd
        .and_then(|dir| dir.file_name())
        .map(|name| format!(" {}", name.to_string_lossy()))
        .unwrap_or_default();
    format!("doeff-linter {}{}", mode_name, place)
}

/// The command list that hands itself to the canonical `run` (pure) — `[python, canon, run, --label, name, --, self, args…]`.
pub fn gate_command(python: &Path, canon: &Path, label: &str, exe: &Path, args: &[OsString]) -> Vec<OsString> {
    let head: [&OsStr; 6] = [
        python.as_os_str(),
        canon.as_os_str(),
        OsStr::new("run"),
        OsStr::new("--label"),
        OsStr::new(label),
        OsStr::new("--"),
    ];
    head.iter()
        .map(|part| part.to_os_string())
        .chain(std::iter::once(exe.as_os_str().to_os_string()))
        .chain(args.iter().cloned())
        .collect()
}

/// Location of the canonical source (under HOME). None if HOME is unset or the canonical source is absent.
fn canon_path() -> Option<PathBuf> {
    let home = std::env::var_os("HOME").filter(|value| !value.is_empty())?;
    let canon = Path::new(&home).join(CANON_UNDER_HOME);
    canon.is_file().then_some(canon)
}

/// The python that runs the canonical source — the answer of `uv python find` (the worktree venv if present). Do not look up the machine's python3 or the venv's bin directly
/// (2026-09-25 — python is chosen via uv, never the machine's python or venv directly).
/// The canonical source runs on the standard library only. It is not wrapped in `uv run` because, with uv starting a child and waiting, the caller's SIGKILL kills only uv,
/// leaving the wrapper and the linter orphaned (the exec'd python has the same pid as the linter).
fn python_for_canon() -> Result<PathBuf, String> {
    let found = Command::new("uv")
        .args(["python", "find"])
        .output()
        .map_err(|error| format!("cannot start uv to find python ({error})"))?;
    let text = String::from_utf8_lossy(&found.stdout).trim().to_string();
    if found.status.success() && !text.is_empty() {
        Ok(PathBuf::from(text))
    } else {
        Err(format!(
            "`uv python find` returned no python (exit {:?}, {})",
            found.status.code(),
            String::from_utf8_lossy(&found.stderr).trim()
        ))
    }
}

/// At heavy-mode startup, hands itself to the canonical `run` (exec — does not return). Returns doing nothing if the mark is set or the canonical source is absent.
/// If handing over fails (no python, exec failure), prints one line and returns, running without taking a slot.
#[cfg(unix)]
pub fn enter(weight: Weight) {
    use std::os::unix::process::CommandExt;

    let Weight::Heavy(mode_name) = weight else {
        return;
    };
    if std::env::var_os(GATED_ENV).is_some() {
        return;
    }
    let Some(canon) = canon_path() else {
        return;
    };
    let exe = match std::env::current_exe() {
        Ok(exe) => exe,
        Err(error) => {
            eprintln!("doeff-linter: running without taking a machine check slot — cannot read own path ({error})");
            return;
        }
    };
    let python = match python_for_canon() {
        Ok(python) => python,
        Err(reason) => {
            eprintln!("doeff-linter: running without taking a machine check slot — {reason}");
            return;
        }
    };
    let cwd = std::env::current_dir().ok();
    let args: Vec<OsString> = std::env::args_os().skip(1).collect();
    let command = gate_command(&python, &canon, &label(mode_name, cwd.as_deref()), &exe, &args);
    let error = Command::new(&command[0]).args(&command[1..]).env(GATED_ENV, "1").exec();
    eprintln!(
        "doeff-linter: running without taking a machine check slot — cannot start canonical source {} with {} ({error})",
        canon.display(),
        python.display()
    );
}

/// A non-Unix machine has no canonical command wrapper (it counts via flock and the /proc ancestor chain) — runs without taking a slot.
#[cfg(not(unix))]
pub fn enter(_weight: Weight) {}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn cheap_and_caller_bounded_modes_take_no_slot() {
        for mode in [
            Mode { list_rules: true, ..Mode::default() },
            Mode { split_rules: true, commit_hook: true, ..Mode::default() },
            Mode { affected_tests: true, ..Mode::default() },
            Mode { stdin: true, editor_json: true, ..Mode::default() },
            Mode { commit_hook: true, ..Mode::default() },
        ] {
            assert_eq!(weight(mode), Weight::Light, "{mode:?}");
        }
    }

    #[test]
    fn repo_scans_take_a_slot_named_by_their_mode() {
        assert_eq!(weight(Mode { hook: true, ..Mode::default() }), Weight::Heavy("--hook"));
        assert_eq!(weight(Mode { editor_json: true, ..Mode::default() }), Weight::Heavy("editor-json"));
        assert_eq!(weight(Mode::default()), Weight::Heavy("lint"));
    }

    #[test]
    fn the_gate_command_hands_the_same_arguments_to_the_canon() {
        let args = vec![OsString::from("--no-log"), OsString::from("src")];
        let command = gate_command(
            Path::new("/venv/bin/python"),
            Path::new("/home/u/dotfiles/agent/tests/machine_check_slots.py"),
            "doeff-linter lint repo",
            Path::new("/venv/bin/doeff-linter"),
            &args,
        );
        let expected: Vec<OsString> = [
            "/venv/bin/python",
            "/home/u/dotfiles/agent/tests/machine_check_slots.py",
            "run",
            "--label",
            "doeff-linter lint repo",
            "--",
            "/venv/bin/doeff-linter",
            "--no-log",
            "src",
        ]
        .iter()
        .map(OsString::from)
        .collect();
        assert_eq!(command, expected);
    }

    #[test]
    fn the_label_names_the_mode_and_the_working_tree() {
        assert_eq!(label("lint", Some(Path::new("/w/agora-controllers-wt-x"))), "doeff-linter lint agora-controllers-wt-x");
        assert_eq!(label("--commit-hook", None), "doeff-linter --commit-hook");
    }
}
