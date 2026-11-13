//! Tests for `doeff-linter --commit-hook`: make a temporary git repo, commit a clean baseline, stage changes, and
//! look at the hook's exit code and stderr lines. Source-only commits apply single-file rules to staged paths;
//! declaration changes apply all enabled rules across the repo, including findings on untouched files.

use serde_json::Value;
use std::path::Path;
use std::process::{Command, Stdio};

const JUDGED: &str = "{:pre [(: writes tuple)] :post [(: % tuple)] :tags {:context \"lease\" :role \"judgment\"}}";

/// The test repo's architecture.hy (`queue` is a service that declares :entry-modules and :invariants).
const ARCHITECTURE: &str = r#"
(defarchitecture sample
  :root "app"
  :layers [(layer core :roles [judgment] :imports [core])
           (layer foundation :roles [foundation] :imports [foundation])
           (layer entry :roles [entry] :imports [core foundation entry])]
  :foundation foundation
  :world-handlers [(world-handler "app.foundation.host:with-host" :touches [http file]
                     :wraps ["doeff_core_effects._os_file:os-file-handler"])])
(defservice queue "queue" {:layers [core] :entry-modules ["app.queue.main"] :invariants ["app.queue.lease_invariants:fenced-writes"]})
"#;

/// Settings: DOEFF163 is a whole-repo rule; DOEFF016 (Python relative imports) is a rule applied to staged paths.
const PYPROJECT: &str = r#"[tool.doeff-linter]
enable = ["DOEFF163", "DOEFF016"]

[tool.doeff-linter.commit_hook]
timeout_s = 120
"#;

fn git(root: &Path, args: &[&str]) {
    let output = Command::new("git")
        .args(["-c", "user.name=commit-hook-test", "-c", "user.email=commit-hook-test@example.invalid", "-c", "commit.gpgsign=false"])
        .args(args)
        .current_dir(root)
        .env_remove("GIT_DIR")
        .env_remove("GIT_INDEX_FILE")
        .env_remove("GIT_WORK_TREE")
        .output()
        .unwrap();
    assert!(output.status.success(), "git {:?}: {}", args, String::from_utf8_lossy(&output.stderr));
}

fn write(root: &Path, rel: &str, text: &str) {
    let path = root.join(rel);
    std::fs::create_dir_all(path.parent().unwrap()).unwrap();
    std::fs::write(path, text).unwrap();
}

/// A repo with one clean baseline commit.
fn baseline_repo() -> tempfile::TempDir {
    let dir = tempfile::TempDir::new().unwrap();
    let root = dir.path();
    write(root, "pyproject.toml", PYPROJECT);
    write(root, "architecture.hy", ARCHITECTURE);
    write(root, "app/foundation/host.hy", "(val MODULE-TAGS {:context \"shared\" :role \"foundation\"})\n(defk with-host [body] body)\n");
    write(root, "app/queue/main.hy", "(defk cycle [] 1)\n");
    write(root, "app/queue/lease_invariants.hy", &format!("(defk fenced-writes [writes]\n  {}\n  \"Fencing clause.\"\n  #())\n", JUDGED));
    write(root, "app/queue/tool.py", "import os\n");
    git(root, &["init", "-q", "-b", "main"]);
    git(root, &["add", "-A"]);
    git(root, &["commit", "-q", "-m", "baseline"]);
    dir
}

/// Runs the hook with root as the root and returns (exit code, stderr). The stores (facts cache and the HEAD tree's results) are kept under the repo's .git so they are not shared between tests or
/// mixed with the machine's store.
fn hook(root: &Path, extra: &[&str]) -> (i32, String) {
    let output = Command::new(env!("CARGO_BIN_EXE_doeff-linter"))
        .arg("--commit-hook")
        .args(extra)
        .current_dir(root)
        .env("DOEFF_LINTER_CACHE_DIR", root.join(".git").join("doeff-linter-cache")).env("DOEFF_LINTER_SWEEP_INLINE", "1")
        .env_remove("GIT_DIR")
        .env_remove("GIT_INDEX_FILE")
        .env_remove("GIT_WORK_TREE")
        .stdin(Stdio::null())
        .output()
        .unwrap();
    (output.status.code().unwrap_or(-1), String::from_utf8_lossy(&output.stderr).into_owned())
}

/// The baseline itself is green, and the settings' `[tool.doeff-linter.commit_hook]` does not become an unknown key (DOEFF100).
#[test]
fn commit_hook_section_is_a_known_key_and_the_baseline_is_clean() {
    let dir = baseline_repo();
    let output = Command::new(env!("CARGO_BIN_EXE_doeff-linter"))
        .args(["--output-format", "editor-json", "--no-log"])
        .current_dir(dir.path())
        .output()
        .unwrap();
    let report: Value = serde_json::from_slice(&output.stdout).unwrap_or_else(|e| panic!("{}: {}", e, String::from_utf8_lossy(&output.stderr)));
    let rules: Vec<&str> = report["violations"].as_array().unwrap().iter().map(|v| v["rule"].as_str().unwrap()).collect();
    assert!(rules.is_empty(), "{}", report["violations"]);
}

/// Failure case: an architecture.hy that declares a :wraps not in the catalog (data/world_handlers.json) is a configuration error; the linter
/// does not present it as 0 hits but names it with exit code 2, and the hook also stops with 2, naming the wrong :wraps (not only the first-line heading "configuration error:").
#[test]
fn a_wraps_outside_the_catalog_is_named_and_stops() {
    let dir = baseline_repo();
    let root = dir.path();
    let stale = "doeff_cluster.host_contract:host-reader";
    write(root, "architecture.hy", &ARCHITECTURE.replace("doeff_core_effects._os_file:os-file-handler", stale));
    let output = Command::new(env!("CARGO_BIN_EXE_doeff-linter"))
        .args(["--output-format", "editor-json", "--no-log"])
        .current_dir(root)
        .output()
        .unwrap();
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert_eq!(output.status.code(), Some(2), "{}", stderr);
    assert!(output.stdout.is_empty(), "a configuration error must not produce a report of 0 hits: {}", String::from_utf8_lossy(&output.stdout));
    assert!(stderr.contains(&format!(":wraps {} is not in the catalog of real I/O handlers", stale)), "{}", stderr);
    git(root, &["add", "architecture.hy"]);
    let (code, stderr) = hook(root, &[]);
    assert_eq!(code, 2, "{}", stderr);
    assert!(stderr.contains(stale), "the hook does not name the wrong :wraps: {}", stderr);
}

/// A repo that names the catalog of a package outside doeff in world_catalogs (a path to the package's repo next to it, `../<repo>/…`) commits as before:
/// the HEAD tree is placed under the fact cache's root, where that relative path does not reach the package's repo, so the hook passes the tip's catalogs
/// to both children as absolute paths (without it the HEAD tree's run is a config error and every commit stops with 2). `--split-rules` prints the same paths
/// for the merge gate.
#[test]
fn a_repo_catalog_next_to_the_repo_reaches_the_head_tree() {
    let outer = tempfile::TempDir::new().unwrap();
    let root = outer.path().join("repo");
    std::fs::create_dir_all(&root).unwrap();
    write(
        outer.path(),
        "agent-tools/world_handlers.json",
        r#"{"handlers": [{"handler": "pkg_agents:agent-handler", "touches": ["process"], "why": "starts the agent as a child process"}]}"#,
    );
    write(&root, "pyproject.toml", &PYPROJECT.replacen("[tool.doeff-linter]\n", "[tool.doeff-linter]\nworld_catalogs = [\"../agent-tools/world_handlers.json\"]\n", 1));
    write(&root, "architecture.hy", &ARCHITECTURE.replace("\"doeff_core_effects._os_file:os-file-handler\"", "\"doeff_core_effects._os_file:os-file-handler\" \"pkg_agents:agent-handler\""));
    write(&root, "app/foundation/host.hy", "(val MODULE-TAGS {:context \"shared\" :role \"foundation\"})\n(defk with-host [body] body)\n");
    write(&root, "app/queue/main.hy", "(defk cycle [] 1)\n");
    write(&root, "app/queue/lease_invariants.hy", &format!("(defk fenced-writes [writes]\n  {}\n  \"Fencing clause.\"\n  #())\n", JUDGED));
    write(&root, "app/queue/tool.py", "import os\n");
    git(&root, &["init", "-q", "-b", "main"]);
    git(&root, &["add", "-A"]);
    git(&root, &["commit", "-q", "-m", "baseline"]);

    write(&root, "app/queue/main.hy", "(defk cycle [] 2)\n");
    git(&root, &["add", "app/queue/main.hy"]);
    let (code, stderr) = hook(&root, &[]);
    assert_eq!(code, 0, "{}", stderr);

    let output = Command::new(env!("CARGO_BIN_EXE_doeff-linter"))
        .args(["--split-rules", "app/queue/main.hy"])
        .current_dir(&root)
        .env_remove("GIT_DIR")
        .output()
        .unwrap();
    assert!(output.status.success(), "{}", String::from_utf8_lossy(&output.stderr));
    let split: Value = serde_json::from_slice(&output.stdout).unwrap();
    let named: Vec<std::path::PathBuf> = split["world_catalogs"].as_array().unwrap().iter().map(|p| std::path::PathBuf::from(p.as_str().unwrap())).collect();
    assert_eq!(named.len(), 1, "{}", split);
    assert!(named[0].is_absolute() && named[0].is_file(), "{}", split);
}

/// Failure case (ii): a clean change passes with 0. A new error of a rule applied to staged paths (DOEFF016) stops with 1.
#[test]
fn a_clean_change_passes_and_a_staged_error_blocks() {
    let dir = baseline_repo();
    let root = dir.path();
    write(root, "app/queue/main.hy", "(defk cycle [] 2)\n");
    git(root, &["add", "app/queue/main.hy"]);
    let (code, stderr) = hook(root, &[]);
    assert_eq!(code, 0, "{}", stderr);
    assert!(!stderr.contains("not on HEAD"), "{}", stderr);

    write(root, "app/queue/tool.py", "from . import main\n");
    git(root, &["add", "app/queue/tool.py"]);
    let (code, stderr) = hook(root, &[]);
    assert_eq!(code, 1, "{}", stderr);
    assert!(stderr.contains("doeff-linter commit-hook: violation in staged file: app/queue/tool.py:1: DOEFF016"), "{}", stderr);
}

/// If the retired settings key whole_repo_rules remains, it is announced in one line without being read (rules are split as the rules declare themselves; even if 016 is listed,
/// DOEFF016 for a staged Python file stops as a rule applied to staged paths).
#[test]
fn a_retired_hand_list_is_named_and_not_read() {
    let dir = baseline_repo();
    let root = dir.path();
    write(root, "pyproject.toml", &PYPROJECT.replace("timeout_s = 120", "whole_repo_rules = [\"DOEFF016\"]\ntimeout_s = 120"));
    git(root, &["add", "pyproject.toml"]);
    git(root, &["commit", "-q", "-m", "retired key"]);
    write(root, "app/queue/tool.py", "from . import main\n");
    git(root, &["add", "app/queue/tool.py"]);
    let (code, stderr) = hook(root, &[]);
    assert_eq!(code, 1, "{}", stderr);
    assert!(stderr.contains("whole_repo_rules is a retired key and is not read"), "{}", stderr);
    assert!(stderr.contains("violation in staged file: app/queue/tool.py:1: DOEFF016"), "{}", stderr);
}

/// `--list-rules` prints every rule's ID and its declaration of whether it needs the whole repo (the one place the gate and the hook choose by). Python's per-statement rules are single-file.
#[test]
fn list_rules_names_which_rules_need_the_whole_repo() {
    let output = Command::new(env!("CARGO_BIN_EXE_doeff-linter")).arg("--list-rules").output().unwrap();
    assert!(output.status.success(), "{}", String::from_utf8_lossy(&output.stderr));
    let listed: Value = serde_json::from_slice(&output.stdout).unwrap();
    let whole = |id: &str| {
        listed.as_array().unwrap().iter().find(|r| r["id"] == id).unwrap_or_else(|| panic!("{} is missing", id))["whole_repo"].as_bool().unwrap()
    };
    for id in ["DOEFF149", "DOEFF161", "DOEFF163", "DOEFF166"] {
        assert!(whole(id), "{} needs the whole repo", id);
    }
    for id in ["DOEFF016", "DOEFF110", "DOEFF169"] {
        assert!(!whole(id), "{} is judged by a single file", id);
    }
}

/// `--list-rules` also prints whether a rule asks Jev (semantic): the gate separates Jev rules by this field, not by the ID prefix.
/// Deterministic rules whose ID starts with DOEFF2 (DOEFF206-209) are not Jev rules.
#[test]
fn list_rules_names_which_rules_ask_jev() {
    let output = Command::new(env!("CARGO_BIN_EXE_doeff-linter")).arg("--list-rules").output().unwrap();
    assert!(output.status.success(), "{}", String::from_utf8_lossy(&output.stderr));
    let listed: Value = serde_json::from_slice(&output.stdout).unwrap();
    let semantic = |id: &str| {
        listed.as_array().unwrap().iter().find(|r| r["id"] == id).unwrap_or_else(|| panic!("{} is missing", id))["semantic"]
            .as_bool()
            .unwrap_or_else(|| panic!("{} has no semantic field", id))
    };
    for id in ["DOEFF201", "DOEFF202", "DOEFF203", "DOEFF204", "DOEFF205"] {
        assert!(semantic(id), "{} asks Jev", id);
    }
    for id in ["DOEFF016", "DOEFF110", "DOEFF206", "DOEFF207", "DOEFF208", "DOEFF209"] {
        assert!(!semantic(id), "{} does not ask Jev", id);
    }
}

/// Failure case: the gate's `--split-rules` puts DOEFF206-209 into quick or whole (before the fix neither had them: 0 each; they were dropped as Jev rules because the ID
/// starts with DOEFF2). Jev rules (DOEFF201, 205) go into neither.
#[test]
fn split_rules_keeps_the_doeff2_rules_that_do_not_ask_jev() {
    let dir = baseline_repo();
    let output = Command::new(env!("CARGO_BIN_EXE_doeff-linter"))
        .args(["--split-rules", "--enable", "DOEFF201,DOEFF205,DOEFF206,DOEFF207,DOEFF208,DOEFF209", "app/queue/main.hy"])
        .current_dir(dir.path())
        .output()
        .unwrap();
    assert!(output.status.success(), "{}", String::from_utf8_lossy(&output.stderr));
    let split: Value = serde_json::from_slice(&output.stdout).unwrap();
    let listed = |side: &str| -> Vec<String> { split[side].as_array().unwrap().iter().map(|v| v.as_str().unwrap().to_string()).collect() };
    let (quick, whole) = (listed("quick"), listed("whole"));
    assert!(quick.contains(&"DOEFF208".to_string()), "{}", split);
    for id in ["DOEFF206", "DOEFF207", "DOEFF209"] {
        assert!(whole.contains(&id.to_string()), "{} {}", id, split);
    }
    for id in ["DOEFF201", "DOEFF205"] {
        assert!(!quick.contains(&id.to_string()) && !whole.contains(&id.to_string()), "{} {}", id, split);
    }
}

/// A repo with only rule `rule` enabled and :business-fakes declared (not yet committed).
fn repo_enabling_only(rule: &str) -> tempfile::TempDir {
    let dir = baseline_repo();
    let root = dir.path();
    write(root, "pyproject.toml", &format!("[tool.doeff-linter]\nenable = [\"{}\"]\n\n[tool.doeff-linter.commit_hook]\ntimeout_s = 120\n", rule));
    write(
        root,
        "architecture.hy",
        &ARCHITECTURE.replace(":foundation foundation", ":foundation foundation\n  :business-fakes {:simulation [\"app/sim/**\"] :tests [\"**/tests/**\"] :production [\"app/**\"] :business-modules [\"app.queue\"]}"),
    );
    dir
}

/// A repo with only rule `rule` enabled, :business-fakes declared, and the file `app/queue/wait.hy` (contents text) that has a hit committed as the baseline.
/// It is returned with one comment line added to that file and staged.
fn repo_with_an_old_hit(rule: &str, text: &str) -> tempfile::TempDir {
    let dir = repo_enabling_only(rule);
    let root = dir.path();
    write(root, "app/queue/wait.hy", text);
    git(root, &["add", "-A"]);
    git(root, &["commit", "-q", "-m", "baseline with a hit"]);
    write(root, "app/queue/wait.hy", &format!(";; add a comment\n{}", text));
    git(root, &["add", "app/queue/wait.hy"]);
    dir
}

/// A repo with only rule `rule` enabled and :business-fakes declared, with a baseline committed, and the file `app/queue/wait.hy` (contents text) that has a hit
/// newly added and staged (a hit whose key is not on HEAD).
fn repo_with_a_new_hit(rule: &str, text: &str) -> tempfile::TempDir {
    let dir = repo_enabling_only(rule);
    let root = dir.path();
    git(root, &["add", "-A"]);
    git(root, &["commit", "-q", "-m", "baseline without a hit"]);
    write(root, "app/queue/wait.hy", text);
    git(root, &["add", "app/queue/wait.hy"]);
    dir
}

const RECORD_WAIT: &str = "(import doeff_records.effects [WatchChanges])\n(defk wait-for [cursor left]\n  (WatchChanges #(\"message\") cursor :timeout left))\n";
const POLLING_LOOP: &str = "(defk poll []\n  (while True\n    (<- (Delay 5.0))\n    (<- (ReadQueue))))\n";
/// The head of the line for a hit that stops even though the same key is on HEAD, and the line with the fix guidance (cisco-c8's request, 2026-10-07).
const ALREADY_ON_HEAD: &str = "existing hit in a changed file (also on main; this rule is not lowered by the known list): ";
const FIX_IT_IN_THE_SAME_CHANGE: &str = "If you change this file, fix that hit in the same change (do not skip the hook)";

/// Failure case (cisco-c8's request, 2026-10-07): when stopping on a hit whose same key is also on HEAD, writing "not on HEAD" makes
/// the worker read it as a hook bug and nearly skip the hook. An existing hit of a rule applied to staged paths (DOEFF208) is written as "existing hit in a changed file",
/// one line of fix guidance is added, and "not on HEAD" is not written.
#[test]
fn an_old_hit_that_blocks_is_named_as_already_on_head() {
    let dir = repo_with_an_old_hit("DOEFF208", RECORD_WAIT);
    let (code, stderr) = hook(dir.path(), &[]);
    assert_eq!(code, 1, "{}", stderr);
    assert!(stderr.contains(&format!("{}app/queue/wait.hy::DOEFF208::wait-for::WatchChanges", ALREADY_ON_HEAD)), "{}", stderr);
    assert!(stderr.contains(FIX_IT_IN_THE_SAME_CHANGE), "{}", stderr);
    assert!(!stderr.contains("not on HEAD"), "{}", stderr);
}

/// Failure case (the counterpart of the above): a hit whose same key is not on HEAD (a hit in a newly added file) is written "not on HEAD" as before, and the existing-hit
/// line and guidance are not shown.
#[test]
fn a_new_hit_that_blocks_is_still_named_as_not_on_head() {
    let dir = repo_with_a_new_hit("DOEFF208", RECORD_WAIT);
    let (code, stderr) = hook(dir.path(), &[]);
    assert_eq!(code, 1, "{}", stderr);
    assert!(stderr.contains("not on HEAD critical: app/queue/wait.hy::DOEFF208::wait-for::WatchChanges"), "{}", stderr);
    assert!(!stderr.contains(ALREADY_ON_HEAD), "{}", stderr);
    assert!(!stderr.contains(FIX_IT_IN_THE_SAME_CHANGE), "{}", stderr);
}

/// Paired test: a hit of a rule that needs the whole repo (DOEFF209, the periodic patrol loop) does not get measured by the hook even if it is in a staged file
/// (a rule that builds the whole repo's inference world even when applied to one file); neither a newly added hit nor an existing hit stops it, and the whole-repo verification looks at it.
#[test]
fn a_whole_repo_rule_hit_in_a_staged_file_is_left_to_the_whole_verification() {
    for dir in [repo_with_an_old_hit("DOEFF209", POLLING_LOOP), repo_with_a_new_hit("DOEFF209", POLLING_LOOP)] {
        let (code, stderr) = hook(dir.path(), &[]);
        assert_eq!(code, 0, "{}", stderr);
        assert!(!stderr.contains("DOEFF209"), "{}", stderr);
    }
}

/// Unstage the file that has the hit and stage only a file without hits.
fn stage_only_an_untouched_file(root: &Path) {
    git(root, &["reset", "-q", "app/queue/wait.hy"]);
    write(root, "app/queue/main.hy", "(defk cycle [] 2)\n");
    git(root, &["add", "app/queue/main.hy"]);
}

/// Failure case (cisco-c8's approval): a DOEFF208 hit that a staged file has from the HEAD version also stops the commit (a rule without a known list
/// is not lowered by the baseline difference; new_critical of the column applied to staged paths). A commit that does not touch the file with a DOEFF208 hit does not
/// stop (per file).
#[test]
fn a_staged_file_with_an_old_record_wait_blocks_and_an_untouched_one_does_not() {
    let dir = repo_with_an_old_hit("DOEFF208", RECORD_WAIT);
    let root = dir.path();
    let (code, stderr) = hook(root, &[]);
    assert_eq!(code, 1, "{}", stderr);
    assert!(stderr.contains(&format!("{}app/queue/wait.hy::DOEFF208::wait-for::WatchChanges", ALREADY_ON_HEAD)), "{}", stderr);
    stage_only_an_untouched_file(root);
    let (code, stderr) = hook(root, &[]);
    assert_eq!(code, 0, "{}", stderr);
}

/// A commit that starts firing a rule: add a declaration forbidding json in the layer core, and also touch a core file in the same commit (it has an existing import),
/// and that file's existing import becomes a hit not in the HEAD version, so a commit with no specification in the body stops. If the body has a `Lint-Baseline: declarations` line with a reason,
/// the HEAD tree is measured with the current declarations, so the existing hit becomes one in the baseline and passes. If a new violation is added in the same commit,
/// it stops even with the specification.
#[test]
fn a_reasoned_baseline_trailer_lets_rules_start_ringing_but_still_blocks_new_hits() {
    let dir = baseline_repo();
    let root = dir.path();
    write(root, "pyproject.toml", &PYPROJECT.replace("enable = [\"DOEFF163\", \"DOEFF016\"]", "enable = [\"DOEFF163\", \"DOEFF016\", \"DOEFF102\"]"));
    let rule = format!("(val MODULE-TAGS {{:context \"queue\" :role \"judgment\"}})\n(import json)\n(defk parse [text]\n  {}\n  #())\n", JUDGED);
    write(root, "app/queue/core/rule.hy", &rule);
    git(root, &["add", "-A"]);
    git(root, &["commit", "-q", "-m", "baseline of core that imports json"]);
    let forbidding = ARCHITECTURE.replace("(layer core :roles [judgment] :imports [core])", "(layer core :roles [judgment] :imports [core] :forbid-modules [json])");
    write(root, "architecture.hy", &forbidding);
    write(root, "app/queue/core/rule.hy", &format!("{};; comment\n", rule));
    git(root, &["add", "architecture.hy", "app/queue/core/rule.hy"]);
    let (code, stderr) = hook(root, &[]);
    assert_eq!(code, 1, "{}", stderr);
    assert!(stderr.contains("app/queue/core/rule.hy::DOEFF102"), "{}", stderr);
    let message = root.join("MSG");
    std::fs::write(&message, "forbid json in core\n\nLint-Baseline: declarations — fix the existing import in a separate change\n").unwrap();
    let message_arg = message.to_string_lossy().into_owned();
    let (code, stderr) = hook(root, &["--commit-message", &message_arg]);
    assert_eq!(code, 0, "{}", stderr);
    assert!(stderr.contains("Lint-Baseline: declarations — measuring the HEAD tree with the current declarations"), "{}", stderr);
    // A line without a reason has no effect.
    std::fs::write(&message, "forbid json in core\n\nLint-Baseline: declarations\n").unwrap();
    let (code, stderr) = hook(root, &["--commit-message", &message_arg]);
    assert_eq!(code, 1, "{}", stderr);
    // Even with the specification, a new violation added in the same commit stops.
    std::fs::write(&message, "forbid json in core\n\nLint-Baseline: declarations — fix the existing import in a separate change\n").unwrap();
    write(root, "app/queue/core/more.hy", &format!("(val MODULE-TAGS {{:context \"queue\" :role \"judgment\"}})\n(import json)\n(defk more [text]\n  {}\n  #())\n", JUDGED));
    git(root, &["add", "app/queue/core/more.hy"]);
    let (code, stderr) = hook(root, &["--commit-message", &message_arg]);
    assert_eq!(code, 1, "{}", stderr);
    assert!(stderr.contains("app/queue/core/more.hy") && stderr.contains("DOEFF102"), "{}", stderr);
    assert!(!stderr.contains("app/queue/core/rule.hy::DOEFF102"), "{}", stderr);
}

/// The gate's `--split-rules --commit-message <body>` names the declaration files to project onto the baseline tree only when there is a Lint-Baseline line with a reason.
#[test]
fn split_rules_names_the_baseline_overlay_only_for_a_reasoned_trailer() {
    let dir = baseline_repo();
    let message = dir.path().join("MSG");
    let split = |text: &str| -> Value {
        std::fs::write(&message, text).unwrap();
        let output = Command::new(env!("CARGO_BIN_EXE_doeff-linter"))
            .args(["--split-rules", "--commit-message"])
            .arg(&message)
            .arg("architecture.hy")
            .current_dir(dir.path())
            .output()
            .unwrap();
        assert!(output.status.success(), "{}", String::from_utf8_lossy(&output.stderr));
        serde_json::from_slice(&output.stdout).unwrap()
    };
    assert_eq!(split("x\n\nLint-Baseline: declarations — start firing\n")["baseline_overlay"], serde_json::json!(["pyproject.toml", "architecture.hy"]));
    assert_eq!(split("x\n")["baseline_overlay"], serde_json::json!([]));
}

/// The gate's `--split-rules <changed paths>` prints the same rule split as the hook: naming architecture.hy puts even single-file rules (DOEFF016) in
/// whole; with only sources it stays quick.
#[test]
fn split_rules_tells_the_gate_the_same_split_as_the_hook() {
    let dir = baseline_repo();
    let split = |changed: &[&str]| -> Value {
        let output = Command::new(env!("CARGO_BIN_EXE_doeff-linter")).arg("--split-rules").args(changed).current_dir(dir.path()).output().unwrap();
        assert!(output.status.success(), "{}", String::from_utf8_lossy(&output.stderr));
        serde_json::from_slice(&output.stdout).unwrap()
    };
    let declared = split(&["architecture.hy"]);
    assert_eq!(declared["declaration_changed"], true, "{}", declared);
    assert_eq!(declared["quick"], serde_json::json!([]), "{}", declared);
    assert_eq!(declared["whole"], serde_json::json!(["DOEFF163", "DOEFF016"]), "{}", declared);
    let plain = split(&["app/queue/main.hy"]);
    assert_eq!(plain["declaration_changed"], false, "{}", plain);
    assert_eq!(plain["quick"], serde_json::json!(["DOEFF016"]), "{}", plain);
    assert_eq!(plain["whole"], serde_json::json!(["DOEFF163"]), "{}", plain);
}

/// A stand-in linter that records: for each run of the child linter, it writes the arguments one per line to a log, adds a separator line `===`, and then runs the real one.
fn recording_linter(dir: &Path) -> (std::path::PathBuf, std::path::PathBuf) {
    use std::os::unix::fs::PermissionsExt;
    let path = dir.join("recording-doeff-linter");
    let log = dir.join("runs.log");
    let script = format!("#!/bin/sh\nprintf '%s\\n' \"$@\" '===' >> '{}'\nexec '{}' \"$@\"\n", log.display(), env!("CARGO_BIN_EXE_doeff-linter"));
    std::fs::write(&path, script).unwrap();
    std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o755)).unwrap();
    (path, log)
}

/// For each run of a recorded child linter: (the --enable value, the list of paths after `--`).
fn recorded_runs(log: &Path) -> Vec<(String, Vec<String>)> {
    let text = std::fs::read_to_string(log).unwrap_or_default();
    text.split("===\n")
        .filter(|run| !run.is_empty())
        .map(|run| {
            let args: Vec<&str> = run.lines().collect();
            let enable = args.iter().position(|a| *a == "--enable").map(|i| args[i + 1].to_string()).unwrap_or_default();
            let paths = args.iter().position(|a| *a == "--").map(|i| args[i + 1..].iter().map(|p| p.to_string()).collect()).unwrap_or_default();
            (enable, paths)
        })
        .collect()
}

/// Failure case (2026-10-11, node-h load 102/24 core, decided by cisco-c8): the hook's child linter applies, to the staged paths only,
/// only rules decided by a single file (DOEFF016): it does not measure the whole repo (path `.`) and does not apply rules that declare they need the whole repo (DOEFF163).
/// Before the fix it measured the tip tree and the HEAD tree over the whole repo (45-100 seconds and 1.5-1.7 GB per run in agora-controllers), and the 23 conversations fixing the daily reds
/// committed at once and filled the machine. A whole-repo rule builds the whole repo's inference world even when applied to one file (DOEFF132 takes 28 seconds on 1 file),
/// so applying it to changed paths does not make it lighter: the hits of whole-repo rules are looked at by the verification every 4 hours.
#[test]
fn the_hook_lints_only_the_staged_paths_with_the_rules_judged_from_one_file() {
    let dir = baseline_repo();
    let root = dir.path();
    let side = tempfile::TempDir::new().unwrap();
    let (linter, log) = recording_linter(side.path());
    write(root, "app/queue/main.hy", "(defk cycle [] 2)\n");
    git(root, &["add", "app/queue/main.hy"]);
    let got = doeff_linter::commit_hook::assess(&options_with(root, &linter, &side.path().join("cache"), 60));
    assert_eq!(got.code, 0, "{:?}", got);
    let runs = recorded_runs(&log);
    assert!(!runs.is_empty(), "the child linter was not fired");
    for (enable, paths) in &runs {
        assert_eq!(enable, "DOEFF016", "a whole-repo rule was applied: {:?}", runs);
        assert_eq!(paths, &vec!["app/queue/main.hy".to_string()], "measured outside the staged paths: {:?}", runs);
    }
}

/// Whole-repo rules stay deferred for source-only commits. Changing a declaration can introduce
/// findings in untouched sources, so both an architecture edit and a layer ban must stop.
#[test]
fn declaration_changes_check_the_whole_repo_but_source_changes_stay_focused() {
    let dir = baseline_repo();
    let root = dir.path();
    write(root, "app/queue/lease_invariants.hy", &format!("(defk fenced-writes-renamed [writes]\n  {}\n  \"Fencing clause.\"\n  #())\n", JUDGED));
    git(root, &["add", "app/queue/lease_invariants.hy"]);
    let (code, stderr) = hook(root, &[]);
    assert_eq!(code, 0, "{}", stderr);
    assert!(!stderr.contains("whole-repo"), "{}", stderr);

    let dir = baseline_repo();
    let root = dir.path();
    write(root, "architecture.hy", &ARCHITECTURE.replace(" :invariants [\"app.queue.lease_invariants:fenced-writes\"]", ""));
    git(root, &["add", "architecture.hy"]);
    let (code, stderr) = hook(root, &[]);
    assert_eq!(code, 1, "{}", stderr);

    let dir = baseline_repo();
    let root = dir.path();
    write(root, "pyproject.toml", &PYPROJECT.replace("enable = [\"DOEFF163\", \"DOEFF016\"]", "enable = [\"DOEFF163\", \"DOEFF016\", \"DOEFF102\"]"));
    write(root, "app/queue/core/rule.hy", &format!("(val MODULE-TAGS {{:context \"queue\" :role \"judgment\"}})\n(import json)\n(defk parse [text]\n  {}\n  #())\n", JUDGED));
    git(root, &["add", "-A"]);
    git(root, &["commit", "-q", "-m", "baseline of core that imports json"]);
    let forbidding = ARCHITECTURE.replace("(layer core :roles [judgment] :imports [core])", "(layer core :roles [judgment] :imports [core] :forbid-modules [json])");
    write(root, "architecture.hy", &forbidding);
    git(root, &["add", "architecture.hy"]);
    let (code, stderr) = hook(root, &[]);
    assert_eq!(code, 1, "{}", stderr);
}

/// Failure case (2026-11-13): a declaration change measures the whole repo, and an error outside the registry that main already has in a file the commit does not touch
/// (DOEFF016 here; DOEFF004 and DOEFF119 in agora-controllers) stopped every declaration change, even a comment-only one -- the errors were not compared with the HEAD version.
/// It passes now; the same error still stops once the commit touches its file, and an error the declaration itself makes in an untouched file still stops
/// (declaration_changes_check_the_whole_repo_but_source_changes_stay_focused).
#[test]
fn a_declaration_change_does_not_stop_on_errors_main_already_has_in_untouched_files() {
    let dir = baseline_repo();
    let root = dir.path();
    write(root, "app/queue/tool.py", "from . import main\n");
    git(root, &["add", "-A"]);
    git(root, &["commit", "-q", "-m", "an error already on main"]);
    write(root, "architecture.hy", &format!(";; a comment\n{}", ARCHITECTURE));
    git(root, &["add", "architecture.hy"]);
    let (code, stderr) = hook(root, &[]);
    assert_eq!(code, 0, "{}", stderr);
    assert!(!stderr.contains("app/queue/tool.py"), "{}", stderr);

    write(root, "app/queue/tool.py", "from . import main\n# touched\n");
    git(root, &["add", "app/queue/tool.py"]);
    let (code, stderr) = hook(root, &[]);
    assert_eq!(code, 1, "{}", stderr);
    assert!(stderr.contains("violation in repo after declaration change: app/queue/tool.py:1: DOEFF016"), "{}", stderr);
}

/// A repo `repo` next to a path dependency `lib` (`[tool.uv.sources] lib = { path = "../lib" }`) whose effect `lib.Op` is re-exported from `lib._effects`, a lower-layer module;
/// the test-only handler of app/queue/tests is a DOEFF157 hit only when `lib.Op` resolves to its defining module. Committed as the baseline (the hit is on main).
fn repo_with_a_path_dependency_hit(outer: &Path) -> std::path::PathBuf {
    let root = outer.join("repo");
    write(outer, "lib/src/lib/__init__.py", "from lib._effects import Op as Op\n");
    write(outer, "lib/src/lib/_effects.py", "class Op:\n    pass\n");
    write(&root, "pyproject.toml", "[tool.doeff-linter]\nenable = [\"DOEFF157\"]\n\n[tool.doeff-linter.commit_hook]\ntimeout_s = 120\n\n[tool.uv.sources]\nlib = { path = \"../lib\", editable = true }\n");
    write(
        &root,
        "architecture.hy",
        &ARCHITECTURE.replace(
            ":foundation foundation",
            ":foundation foundation\n  :business-fakes {:simulation [\"app/sim/**\"] :tests [\"**/tests/**\"] :production [\"app/**\"] :business-modules [\"app.queue\"] :lower-layer-modules [\"lib._effects\"]}",
        ),
    );
    write(&root, "app/foundation/host.hy", "(val MODULE-TAGS {:context \"shared\" :role \"foundation\"})\n(defk with-host [body] body)\n");
    write(&root, "app/queue/main.hy", "(defk cycle [] 1)\n");
    write(&root, "app/queue/lease_invariants.hy", &format!("(defk fenced-writes [writes]\n  {}\n  \"Fencing clause.\"\n  #())\n", JUDGED));
    write(&root, "app/queue/tests/test_op.hy", "(import lib [Op])\n(defhandler test-op (Op [] (resume 1)))\n(deftest test-with-op (with-handlers [test-op] 1))\n");
    git(&root, &["init", "-q", "-b", "main"]);
    git(&root, &["add", "-A"]);
    git(&root, &["commit", "-q", "-m", "baseline with a test-only fake of a lower-layer effect"]);
    root
}

/// Failure case (2026-11-13): the HEAD tree is placed under the fact cache, where the relative path of `[tool.uv.sources]` does not reach the dependency and there is no
/// `.venv`, so a name defined in a path dependency resolved on the tip only: an existing DOEFF157 hit on such a name was missing from the HEAD version and every
/// declaration change stopped with "not on HEAD critical". The hook passes the checkout's dirs to the HEAD run as `--import-root`, the hit is on both sides, and a
/// comment-only declaration change passes. `--split-rules` prints the same dirs for the merge gate.
#[test]
fn a_name_from_a_path_dependency_resolves_on_the_head_tree_too() {
    let outer = tempfile::TempDir::new().unwrap();
    let root = repo_with_a_path_dependency_hit(outer.path());
    let output = Command::new(env!("CARGO_BIN_EXE_doeff-linter")).args(["--output-format", "editor-json", "--no-log"]).current_dir(&root).output().unwrap();
    let report: Value = serde_json::from_slice(&output.stdout).unwrap_or_else(|e| panic!("{}: {}", e, String::from_utf8_lossy(&output.stderr)));
    let keys: Vec<&str> = report["violations"].as_array().unwrap().iter().filter(|v| v["rule"] == "DOEFF157").filter_map(|v| v["key"].as_str()).collect();
    assert_eq!(keys, vec!["app/queue/tests/test_op.hy::DOEFF157::test-op::lib.Op"], "the fixture does not fire: {}", report["violations"]);

    write(&root, "architecture.hy", &format!(";; a comment\n{}", std::fs::read_to_string(root.join("architecture.hy")).unwrap()));
    git(&root, &["add", "architecture.hy"]);
    let (code, stderr) = hook(&root, &[]);
    assert_eq!(code, 0, "{}", stderr);
    assert!(!stderr.contains("not on HEAD"), "{}", stderr);

    let output = Command::new(env!("CARGO_BIN_EXE_doeff-linter")).args(["--split-rules", "architecture.hy"]).current_dir(&root).env_remove("GIT_DIR").output().unwrap();
    assert!(output.status.success(), "{}", String::from_utf8_lossy(&output.stderr));
    let split: Value = serde_json::from_slice(&output.stdout).unwrap();
    let lib = outer.path().join("lib").canonicalize().unwrap();
    let named: Vec<std::path::PathBuf> = split["import_roots"].as_array().unwrap().iter().map(|p| std::path::PathBuf::from(p.as_str().unwrap())).collect();
    assert_eq!(named, vec![lib.join("src"), lib], "{}", split);
}

/// If nothing is staged, exit 0 without firing the linter.
#[test]
fn nothing_staged_passes() {
    let dir = baseline_repo();
    let (code, stderr) = hook(dir.path(), &[]);
    assert_eq!(code, 0, "{}", stderr);
    assert!(stderr.is_empty(), "{}", stderr);
}

/// A limit of 0 seconds means no limit: it measures to the end without cutting off and stops on a new hit (previously it treated 0 seconds as "exceeded without running",
/// and passed as unmeasurable). The command that re-checks a commit stopped by the limit uses this form.
#[test]
fn a_zero_timeout_measures_to_the_end() {
    let dir = baseline_repo();
    let root = dir.path();
    write(root, "app/queue/tool.py", "from . import main\n");
    git(root, &["add", "app/queue/tool.py"]);
    let (code, stderr) = hook(root, &["--commit-hook-timeout-s", "0"]);
    assert_eq!(code, 1, "{}", stderr);
    assert!(stderr.contains("violation in staged file: app/queue/tool.py:1: DOEFF016"), "{}", stderr);
    assert!(!stderr.contains("could not measure"), "{}", stderr);
}

/// Runs the hook from a dir (cwd) outside the repo with `--root <root>` and returns (exit code, stderr) (the stores are under the repo's .git as in hook()).
fn hook_from(cwd: &Path, root: &Path, extra: &[&str]) -> (i32, String) {
    let output = Command::new(env!("CARGO_BIN_EXE_doeff-linter"))
        .arg("--commit-hook")
        .arg("--root")
        .arg(root)
        .args(extra)
        .current_dir(cwd)
        .env("DOEFF_LINTER_CACHE_DIR", root.join(".git").join("doeff-linter-cache")).env("DOEFF_LINTER_SWEEP_INLINE", "1")
        .env_remove("GIT_DIR")
        .env_remove("GIT_INDEX_FILE")
        .env_remove("GIT_WORK_TREE")
        .stdin(Stdio::null())
        .output()
        .unwrap();
    (output.status.code().unwrap_or(-1), String::from_utf8_lossy(&output.stderr).into_owned())
}

/// Reads the report file.
fn read_report(path: &Path) -> Value {
    let text = std::fs::read_to_string(path).unwrap_or_else(|e| panic!("report {} is missing: {}", path.display(), e));
    serde_json::from_str(&text).unwrap_or_else(|e| panic!("the report is not JSON ({}): {}", e, text))
}

/// The IDs of the rules a measurement judged.
fn judged_rules(measurement: &Value) -> Vec<&str> {
    measurement["judged_rules"].as_array().unwrap().iter().map(|r| r.as_str().unwrap()).collect()
}

/// Point 2 of the design record project-health-ide: `--report-out` writes the hook's two measurements (the HEAD version and the staged paths) into
/// one JSON: for each measurement, the subject measured (HEAD or stage), the list of paths measured, violations in editor-json's violation shape, and the rules judged. A fixing commit
/// (removing the HEAD version's DOEFF016 in stage) has 1 hit in the HEAD measurement (after the path is rewritten from the HEAD tree to under the working tree's root) and
/// 0 hits in the stage measurement: the material from which the conversation page reads "reduced". Whole-repo rules (DOEFF163) are not applied by the hook, so they do not appear in the judged rules either.
#[test]
fn the_report_out_writes_the_head_and_the_stage_measurements() {
    let dir = baseline_repo();
    let root = dir.path();
    write(root, "app/queue/tool.py", "from . import main\n");
    git(root, &["add", "app/queue/tool.py"]);
    git(root, &["commit", "-q", "-m", "HEAD with a relative import"]);
    write(root, "app/queue/tool.py", "import os\n");
    git(root, &["add", "app/queue/tool.py"]);
    let out = tempfile::TempDir::new().unwrap();
    let file = out.path().join("report.json");
    let (code, stderr) = hook(root, &["--report-out", file.to_str().unwrap()]);
    assert_eq!(code, 0, "{}", stderr);
    let report = read_report(&file);
    let canonical = root.canonicalize().unwrap();
    assert_eq!(report["root"], canonical.to_string_lossy().as_ref());
    assert_eq!(report["code"], 0);
    let measurements = report["measurements"].as_array().unwrap();
    let targets: Vec<&str> = measurements.iter().map(|m| m["target"].as_str().unwrap()).collect();
    assert_eq!(targets, vec!["HEAD", "stage"], "{}", report);
    for measurement in measurements {
        assert_eq!(measurement["paths"], serde_json::json!(["app/queue/tool.py"]), "{}", measurement);
        let judged = judged_rules(measurement);
        assert!(judged.contains(&"DOEFF016") && !judged.contains(&"DOEFF163"), "{:?}", judged);
    }
    let head = measurements[0]["violations"].as_array().unwrap();
    assert_eq!(head.len(), 1, "{}", report);
    assert_eq!(head[0]["rule"], "DOEFF016");
    assert_eq!(head[0]["path"], canonical.join("app/queue/tool.py").to_string_lossy().as_ref());
    for field in ["severity", "base_severity", "level", "standing", "range", "message", "source"] {
        assert!(head[0].get(field).is_some(), "editor-json's violation field {} is missing: {}", field, head[0]);
    }
    assert_eq!(measurements[1]["violations"], serde_json::json!([]), "{}", report);
}

/// A hook without `--report-out` writes the report nowhere (the current dir stays empty, and there is no new file in the working tree). Even if `--report-out` is added to the same
/// commit, the stop decision, exit code, and stderr text do not change by even one character, and a relative path is written from the current dir.
#[test]
fn without_report_out_the_hook_writes_no_report_and_its_stderr_is_unchanged() {
    let dir = baseline_repo();
    let root = dir.path();
    write(root, "app/queue/tool.py", "from . import main\n");
    git(root, &["add", "app/queue/tool.py"]);
    let cwd = tempfile::TempDir::new().unwrap();
    let (code, plain) = hook_from(cwd.path(), root, &[]);
    assert_eq!(code, 1, "{}", plain);
    assert!(plain.contains("doeff-linter commit-hook: violation in staged file: app/queue/tool.py:1: DOEFF016"), "{}", plain);
    assert_eq!(std::fs::read_dir(cwd.path()).unwrap().count(), 0, "the hook without arguments wrote a file into the current dir");
    let status = Command::new("git")
        .args(["status", "--porcelain", "--untracked-files=all"])
        .current_dir(root)
        .env_remove("GIT_DIR")
        .env_remove("GIT_INDEX_FILE")
        .env_remove("GIT_WORK_TREE")
        .output()
        .unwrap();
    assert_eq!(String::from_utf8_lossy(&status.stdout), "M  app/queue/tool.py\n", "the hook without arguments wrote a file into the working tree");

    let (code, with_report) = hook_from(cwd.path(), root, &["--report-out", "report.json"]);
    assert_eq!(code, 1, "{}", with_report);
    assert_eq!(with_report, plain, "the report outlet changed the stderr text");
    let report = read_report(&cwd.path().join("report.json"));
    assert_eq!(report["code"], 1);
    let stage = &report["measurements"][1];
    assert_eq!(stage["target"], "stage");
    let rules: Vec<&str> = stage["violations"].as_array().unwrap().iter().map(|v| v["rule"].as_str().unwrap()).collect();
    assert_eq!(rules, vec!["DOEFF016"], "{}", report);
}

/// A commit with nothing staged also writes the `--report-out` report: it fires no child, so both the HEAD and stage measurements have empty paths, violations, and judged rules.
#[test]
fn nothing_staged_writes_two_empty_measurements() {
    let dir = baseline_repo();
    let out = tempfile::TempDir::new().unwrap();
    let file = out.path().join("report.json");
    let (code, stderr) = hook(dir.path(), &["--report-out", file.to_str().unwrap()]);
    assert_eq!(code, 0, "{}", stderr);
    assert!(stderr.is_empty(), "{}", stderr);
    let empty = |target: &str| serde_json::json!({ "target": target, "paths": [], "violations": [], "judged_rules": [] });
    assert_eq!(read_report(&file)["measurements"], serde_json::json!([empty("HEAD"), empty("stage")]));
}

/// `--report-out` is only for `--commit-hook`: in other runs (and the gate's `--split-rules`) it is an argument error (exit code 2) and writes nothing.
#[test]
fn report_out_outside_the_commit_hook_is_an_argument_error() {
    let dir = baseline_repo();
    let root = dir.path();
    for extra in [vec!["--output-format", "editor-json"], vec!["--commit-hook", "--split-rules"]] {
        let output = Command::new(env!("CARGO_BIN_EXE_doeff-linter"))
            .args(&extra)
            .args(["--no-log", "--report-out", "report.json"])
            .current_dir(root)
            .stdin(Stdio::null())
            .output()
            .unwrap();
        let stderr = String::from_utf8_lossy(&output.stderr);
        assert_eq!(output.status.code(), Some(2), "{:?}: {}", extra, stderr);
        assert!(stderr.contains("--report-out can be used only with --commit-hook"), "{}", stderr);
        assert!(!root.join("report.json").exists(), "{:?}", extra);
    }
}

/// The heads of the 2 lines after the line for a cut-off stop (cisco-c8's approval, 2026-10-07).
const RERUN_COMMAND: &str = "Command to redo the same comparison without a limit: ";
const RERUN_GUIDE: &str = "Confirm that this command exits with code 0 (0 hits that stop the commit) before committing again. Do not use --no-verify";

/// From the line of the cut-off judgment, extracts the command without a limit.
fn rerun_command(lines: &[String]) -> String {
    lines.iter().find_map(|line| line.strip_prefix(RERUN_COMMAND)).map(str::to_string).unwrap_or_else(|| panic!("the line with the command without a limit is missing: {:?}", lines))
}

/// Runs the command without a limit in a shell and returns (exit code, stderr) (the cache is under the repo's .git as in hook()).
fn run_rerun(root: &Path, command: &str) -> (i32, String) {
    let output = Command::new("sh")
        .arg("-c")
        .arg(command)
        .env("DOEFF_LINTER_CACHE_DIR", root.join(".git").join("doeff-linter-cache")).env("DOEFF_LINTER_SWEEP_INLINE", "1")
        .env_remove("GIT_DIR")
        .env_remove("GIT_INDEX_FILE")
        .env_remove("GIT_WORK_TREE")
        .stdin(Stdio::null())
        .output()
        .unwrap();
    (output.status.code().unwrap_or(-1), String::from_utf8_lossy(&output.stderr).into_owned())
}

/// A slow stand-in linter: only when run on the HEAD tree (which the hook places at cache `commit-hook-tree/<name for the repo>/tree`)
/// does it sleep secs seconds and then run the real one (a stand-in for the case where measuring the HEAD version with a cold facts cache once exceeds the limit).
fn slow_head_linter(dir: &Path, secs: u64) -> std::path::PathBuf {
    use std::os::unix::fs::PermissionsExt;
    let path = dir.join("slow-doeff-linter");
    let script = format!(
        "#!/bin/sh\ncase \"$(pwd -P)\" in\n  */commit-hook-tree/*) sleep {} ;;\nesac\nexec '{}' \"$@\"\n",
        secs,
        env!("CARGO_BIN_EXE_doeff-linter")
    );
    std::fs::write(&path, script).unwrap();
    std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o755)).unwrap();
    path
}

/// A slow stand-in linter that uses CPU: only when run on the HEAD tree, it keeps spinning the CPU for secs seconds (wall) (busy-looping with only bash builtins:
/// the same process's CPU seconds accumulate) and then runs the real one. The limit is measured in the child's CPU seconds, so the case that exceeds the limit is made with this stand-in
/// rather than a sleeping one.
fn burning_head_linter(dir: &Path, secs: u64) -> std::path::PathBuf {
    use std::os::unix::fs::PermissionsExt;
    let path = dir.join("burning-doeff-linter");
    let script = format!(
        "#!/bin/bash\ncase \"$(pwd -P)\" in\n  */commit-hook-tree/*) SECONDS=0; while [ $SECONDS -lt {} ]; do :; done ;;\nesac\nexec '{}' \"$@\"\n",
        secs,
        env!("CARGO_BIN_EXE_doeff-linter")
    );
    std::fs::write(&path, script).unwrap();
    std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o755)).unwrap();
    path
}

/// Builds the hook's preconditions by swapping the child linter, the store root, and the limit (built the same way as the production entry main.rs: the root and the settings path are normalized).
fn options_with(root: &Path, linter: &Path, cache: &Path, timeout_s: u64) -> doeff_linter::commit_hook::CommitHookOptions {
    let root = root.canonicalize().unwrap();
    let loaded = doeff_linter::config::load_config_checked(None, &root).unwrap().unwrap();
    let config_path = loaded.path.canonicalize().unwrap();
    let mut options =
        doeff_linter::commit_hook::CommitHookOptions::new(root, Some((&loaded.config, config_path)), &[], &[], Some(timeout_s), linter.to_path_buf());
    options.cache = Some(cache.to_path_buf());
    options
}

/// Failure case (original issue #2723, cisco-c8's approval 2026-10-07): a commit whose child linter was cut off by the limit is stopped
/// (exit code 1; previously it passed with 0 and the worker did not re-check the commit that passed). The lines when stopping are 3: one line for the comparison that could not be measured, the command to
/// redo the same comparison without a limit, and "confirm 0 hits before committing again; do not use --no-verify". The command can be run in a shell as it is,
/// measures to the end without cutting off, and stops on a new hit (DOEFF016) in the staged file.
#[test]
fn a_cut_comparison_blocks_and_names_the_command_to_rerun_without_a_limit() {
    let dir = baseline_repo();
    let root = dir.path();
    let side = tempfile::TempDir::new().unwrap();
    let linter = burning_head_linter(side.path(), 6);
    write(root, "app/queue/tool.py", "from . import main\n");
    git(root, &["add", "app/queue/tool.py"]);
    let got = doeff_linter::commit_hook::assess(&options_with(root, &linter, &root.join(".git").join("doeff-linter-cache"), 2));
    assert_eq!(got.code, 1, "{:?}", got);
    assert_eq!(got.lines.len(), 3, "{:?}", got);
    for part in ["could not measure the HEAD version", "1 rule(s) (DOEFF016) applied on the HEAD tree", "limit of 2 s", "so the commit is stopped"] {
        assert!(got.lines[0].contains(part), "{:?} is missing: {:?}", part, got);
    }
    assert!(got.lines[1].starts_with(RERUN_COMMAND) && got.lines[2] == RERUN_GUIDE, "{:?}", got);
    let command = rerun_command(&got.lines);
    assert!(command.ends_with("--commit-hook-timeout-s 0"), "{}", command);
    let (code, stderr) = run_rerun(root, &command);
    assert_eq!(code, 1, "{}", stderr);
    assert!(stderr.contains("violation in staged file: app/queue/tool.py:1: DOEFF016"), "{}", stderr);
    assert!(!stderr.contains("could not measure"), "{}", stderr);
}

/// Pair of the above: a commit that finishes within the limit passes as before (exit code 0, no lines): a slow stand-in does not stop it as long as it does not reach the limit.
#[test]
fn a_comparison_within_the_limit_still_passes() {
    let dir = baseline_repo();
    let root = dir.path();
    let side = tempfile::TempDir::new().unwrap();
    let linter = slow_head_linter(side.path(), 1);
    write(root, "app/queue/main.hy", "(defk cycle [] 2)\n");
    git(root, &["add", "app/queue/main.hy"]);
    let got = doeff_linter::commit_hook::assess(&options_with(root, &linter, &side.path().join("cache"), 60));
    assert_eq!(got.code, 0, "{:?}", got);
    assert!(got.lines.is_empty(), "{:?}", got);
}

/// Failure case (2026-10-10, the form under load): when the machine is busy, the child linter's wall seconds stretch longer than its CPU seconds (on node-h at
/// load 42-73, a commit that would be rc 0 without a limit was cut off every time at the wall limit of 40 seconds). A child that waits beyond the limit in wall but uses almost no CPU
/// (a sleeping stand-in: standing in for a child waiting its turn on a busy machine) is within the limit in CPU seconds, so it is measured without cutting off and passes. CPU seconds can be read on Linux with /proc.
#[cfg(target_os = "linux")]
#[test]
fn a_linter_slow_on_the_wall_clock_but_within_the_cpu_limit_is_measured() {
    let dir = baseline_repo();
    let root = dir.path();
    let side = tempfile::TempDir::new().unwrap();
    let linter = slow_head_linter(side.path(), 3);
    write(root, "app/queue/main.hy", "(defk cycle [] 2)\n");
    git(root, &["add", "app/queue/main.hy"]);
    let got = doeff_linter::commit_hook::assess(&options_with(root, &linter, &side.path().join("cache"), 2));
    assert_eq!(got.code, 0, "{:?}", got);
    assert!(got.lines.is_empty(), "{:?}", got);
}

/// Settings: only DOEFF172 (smell of a mapping location, a major warning; it does not change the exit code).
const WARNING_PYPROJECT: &str = r#"[tool.doeff-linter]
enable = ["DOEFF172"]

[tool.doeff-linter.commit_hook]
timeout_s = 120
"#;

/// A file in the layer location (core): one definition builds a mapping outside a handler (DOEFF172 hits once).
const ONE_MAP: &str = "(defk table [n]\n  {\"a\" n})\n";

/// A baseline repo committed with one warning.
fn warning_repo() -> tempfile::TempDir {
    let dir = baseline_repo();
    let root = dir.path();
    write(root, "pyproject.toml", WARNING_PYPROJECT);
    write(root, "app/core/table.hy", ONE_MAP);
    git(root, &["add", "-A"]);
    git(root, &["commit", "-q", "-m", "baseline with one warning"]);
    dir
}

/// Failure case: a major warning does not change the exit code, so the hook compares the count per rule with the HEAD version. A commit that adds one DOEFF172 to the same file,
/// and a commit that adds a new file with a warning, stop with 1, naming the rule and the count.
#[test]
fn a_new_major_warning_blocks_and_names_the_rule_and_counts() {
    let dir = warning_repo();
    let root = dir.path();
    write(root, "app/core/table.hy", &format!("{}\n(defk other [n]\n  {{\"b\" n}})\n", ONE_MAP));
    git(root, &["add", "app/core/table.hy"]);
    let (code, stderr) = hook(root, &[]);
    assert_eq!(code, 1, "{}", stderr);
    assert!(
        stderr.contains("doeff-linter commit-hook: major warnings increased over the HEAD version (count over the set of staged files): DOEFF172 1 → 2"),
        "{}",
        stderr
    );

    git(root, &["reset", "-q", "--hard", "HEAD"]);
    write(root, "app/core/fresh.hy", ONE_MAP);
    git(root, &["add", "app/core/fresh.hy"]);
    let (code, stderr) = hook(root, &[]);
    assert_eq!(code, 1, "{}", stderr);
    // The set is only the staged files (table.hy is not staged); a new file has no HEAD version, so it starts from 0.
    assert!(stderr.contains("DOEFF172 0 → 1"), "a new file's warning also counts in the set's count: {}", stderr);
}

/// A change that does not increase the count passes: a rewrite with the same count, a move to another file (git mv: the HEAD version of the file moved from is counted too), and a change that fixes the warning.
/// After the fixing commit, that HEAD becomes the next baseline (it has no baseline file).
#[test]
fn a_same_count_change_a_move_and_a_fix_pass() {
    let dir = warning_repo();
    let root = dir.path();
    write(root, "app/core/table.hy", "(defk table [m]\n  {\"a\" m})\n");
    git(root, &["add", "app/core/table.hy"]);
    let (code, stderr) = hook(root, &[]);
    assert_eq!(code, 0, "{}", stderr);
    assert!(!stderr.contains("increased"), "{}", stderr);

    git(root, &["reset", "-q", "--hard", "HEAD"]);
    git(root, &["mv", "app/core/table.hy", "app/core/moved.hy"]);
    let (code, stderr) = hook(root, &[]);
    assert_eq!(code, 0, "a move does not increase the count: {}", stderr);

    git(root, &["reset", "-q", "--hard", "HEAD"]);
    write(root, "app/core/table.hy", "(defk table [n]\n  n)\n");
    git(root, &["add", "app/core/table.hy"]);
    let (code, stderr) = hook(root, &[]);
    assert_eq!(code, 0, "{}", stderr);
    git(root, &["commit", "-q", "-m", "fixed"]);
    // The HEAD after the fix is the baseline: a commit that puts the same mapping back stops at 0 → 1.
    write(root, "app/core/table.hy", ONE_MAP);
    git(root, &["add", "app/core/table.hy"]);
    let (code, stderr) = hook(root, &[]);
    assert_eq!(code, 1, "{}", stderr);
    assert!(stderr.contains("DOEFF172 0 → 1"), "{}", stderr);
}

/// The repo's HEAD commit (40 chars).
fn head_sha(root: &Path) -> String {
    let out = Command::new("git").args(["rev-parse", "HEAD"]).current_dir(root).env_remove("GIT_DIR").output().unwrap();
    String::from_utf8_lossy(&out.stdout).trim().to_string()
}

/// Prepares the HEAD tree at the test location (wait for the lock up to 1 second; the temporary tree is under scratch).
fn place(root: &Path, overlay: &[String], home: &Path, scratch: &Path) -> doeff_linter::commit_hook::HeadTree {
    doeff_linter::commit_hook::head_tree(root, overlay, home, scratch, std::time::Duration::from_secs(1)).unwrap().unwrap()
}

/// Failure case (#3858): running the hook twice leaves the HEAD tree at cache commit-hook-tree/<name for the repo>/tree, and the second run for the same HEAD uses the same tree without writing
/// anything (previously it wrote to a temporary dir and deleted it at the end, so no tree remained in the cache).
#[test]
fn the_hook_keeps_the_head_tree_under_the_cache_for_the_next_run() {
    let dir = baseline_repo();
    let root = dir.path();
    write(root, "app/queue/main.hy", "(defk cycle [] 2)\n");
    git(root, &["add", "app/queue/main.hy"]);
    let (code, stderr) = hook(root, &[]);
    assert_eq!(code, 0, "{}", stderr);
    let trees = kept_trees(root);
    assert_eq!(trees.len(), 1, "{:?}", trees);
    assert_eq!(trees[0].file_name().unwrap(), "tree");
    let record = std::fs::read_to_string(trees[0].parent().unwrap().join("tree.sha")).unwrap();
    assert_eq!(record, format!("{}\n", head_sha(root)));
    std::fs::write(trees[0].join("reused.mark"), "").unwrap();
    let (code, stderr) = hook(root, &[]);
    assert_eq!(code, 0, "{}", stderr);
    assert!(trees[0].join("reused.mark").is_file(), "the second run wrote the tree out again");
}

/// Failure case: the second run for the same HEAD writes nothing; a run that advanced HEAD writes only the changed paths; a tree with no record is written out entirely again.
#[test]
fn the_tree_is_written_whole_once_then_only_by_the_difference() {
    let dir = baseline_repo();
    let root = dir.path();
    let side = tempfile::TempDir::new().unwrap();
    let (home, scratch) = (side.path().join("commit-hook-tree"), side.path().join("scratch"));
    use doeff_linter::commit_hook::TreeUpdate;
    assert_eq!(place(root, &[], &home, &scratch).update, TreeUpdate::Rebuilt);
    assert_eq!(place(root, &[], &home, &scratch).update, TreeUpdate::Unchanged);
    write(root, "app/queue/main.hy", "(defk cycle [] 2)\n");
    std::fs::remove_file(root.join("app/queue/tool.py")).unwrap();
    git(root, &["add", "-A"]);
    git(root, &["commit", "-q", "-m", "next"]);
    let advanced = place(root, &[], &home, &scratch);
    assert_eq!(advanced.update, TreeUpdate::Advanced { rewritten: 1, removed: 1 });
    // Do not leave an emptied dir (git does not hold empty dirs).
    write(root, "app/gone/only.py", "x = 1\n");
    git(root, &["add", "-A"]);
    git(root, &["commit", "-q", "-m", "add"]);
    drop(advanced);
    let tree = place(root, &[], &home, &scratch).path;
    assert!(tree.join("app/gone/only.py").is_file());
    std::fs::remove_file(root.join("app/gone/only.py")).unwrap();
    git(root, &["add", "-A"]);
    git(root, &["commit", "-q", "-m", "remove"]);
    place(root, &[], &home, &scratch);
    assert!(!tree.join("app/gone").exists(), "an emptied dir remained");
}

/// Failure case: the overlaid declaration files (the one-time specification Lint-Baseline) are restored to the contents of the tree's sha the next time the tree is used; if not restored, the next commit's
/// baseline would be measured with the current declarations. A declaration file that is on the tip but not on HEAD is deleted when restoring.
#[test]
fn an_overlaid_declaration_is_restored_on_the_next_run() {
    let dir = baseline_repo();
    let root = dir.path();
    let side = tempfile::TempDir::new().unwrap();
    let (home, scratch) = (side.path().join("commit-hook-tree"), side.path().join("scratch"));
    write(root, "architecture.hy", "; tip declaration\n");
    write(root, "extra.hy", "; declaration only on the tip\n");
    let overlay = vec!["architecture.hy".to_string(), "extra.hy".to_string()];
    let overlaid = place(root, &overlay, &home, &scratch);
    assert_eq!(std::fs::read_to_string(overlaid.path.join("architecture.hy")).unwrap(), "; tip declaration\n");
    assert!(overlaid.path.join("extra.hy").is_file());
    drop(overlaid);
    let plain = place(root, &[], &home, &scratch);
    assert_eq!(std::fs::read_to_string(plain.path.join("architecture.hy")).unwrap(), ARCHITECTURE);
    assert!(!plain.path.join("extra.hy").exists(), "a declaration file not on HEAD remained");
}

/// Failure case: two concurrent runs: while another run holds the tree's lock, this run does not touch the placed tree and uses a tree written to this run's temporary dir (it does not rewrite the tree
/// that another run is measuring).
#[test]
fn a_tree_in_use_is_not_rewritten_and_a_temporary_tree_is_used() {
    let dir = baseline_repo();
    let root = dir.path();
    let side = tempfile::TempDir::new().unwrap();
    let (home, scratch) = (side.path().join("commit-hook-tree"), side.path().join("scratch"));
    let held = place(root, &[], &home, &scratch);
    let kept_path = held.path.clone();
    write(root, "app/queue/main.hy", "(defk cycle [] 2)\n");
    git(root, &["add", "-A"]);
    git(root, &["commit", "-q", "-m", "next"]);
    let other = doeff_linter::commit_hook::head_tree(root, &[], &home, &scratch, std::time::Duration::from_millis(200)).unwrap().unwrap();
    assert_eq!(other.update, doeff_linter::commit_hook::TreeUpdate::Temporary);
    assert!(other.path.starts_with(&scratch), "{:?}", other.path);
    assert_eq!(std::fs::read_to_string(other.path.join("app/queue/main.hy")).unwrap(), "(defk cycle [] 2)\n");
    assert_eq!(std::fs::read_to_string(kept_path.join("app/queue/main.hy")).unwrap(), "(defk cycle [] 1)\n", "rewrote the tree in use");
    drop(held);
    let after = place(root, &[], &home, &scratch);
    assert_eq!(after.path, kept_path);
    assert_eq!(std::fs::read_to_string(after.path.join("app/queue/main.hy")).unwrap(), "(defk cycle [] 2)\n");
}

/// Different working trees of the same repo share one tree (the name for the repo comes from git's shared dir). A repo dir whose recorded shared dir no longer exists is
/// deleted; things with other names are not touched.
#[test]
fn worktrees_of_one_repo_share_the_tree_and_vanished_repos_are_removed() {
    let dir = baseline_repo();
    let root = dir.path();
    let side = tempfile::TempDir::new().unwrap();
    let (home, scratch) = (side.path().join("commit-hook-tree"), side.path().join("scratch"));
    let linked = side.path().join("linked");
    git(root, &["worktree", "add", "-q", "--detach", &linked.to_string_lossy(), "HEAD"]);
    let main_tree = place(root, &[], &home, &scratch).path;
    let linked_tree = place(&linked, &[], &home, &scratch).path;
    assert_eq!(main_tree, linked_tree);
    let gone = home.join(format!("{:016x}", 5));
    std::fs::create_dir_all(&gone).unwrap();
    std::fs::write(gone.join("repo.path"), side.path().join("no-such-repo/.git").to_string_lossy().as_bytes()).unwrap();
    let unrecorded = home.join(format!("{:016x}", 6));
    std::fs::create_dir_all(&unrecorded).unwrap();
    place(root, &[], &home, &scratch);
    assert!(!gone.exists(), "the repo dir whose shared dir is gone remained");
    assert!(unrecorded.is_dir(), "deleted a dir with no record");
}

// ------------------------------------------------------------------ One HEAD tree per repo
// The facts cache (facts_cache) is placed in a dir per root path, keyed by each file's size and mtime. If the tree were placed in a dir per sha,
// in ordinary use where HEAD advances with every commit, each run would be a new root = a first run with no cache. One tree is placed per repo, and when HEAD advances only
// the changed files are rewritten.

/// The list of roots of HEAD trees that the hook placed in the cache (the facts cache root under the repo's .git) (dirs that have architecture.hy, up to depth 2).
fn kept_trees(root: &Path) -> Vec<std::path::PathBuf> {
    let home = root.join(".git").join("doeff-linter-cache").join("commit-hook-tree");
    let mut found = Vec::new();
    let mut walk = vec![(home, 0usize)];
    while let Some((dir, depth)) = walk.pop() {
        if dir.join("architecture.hy").is_file() {
            found.push(dir);
            continue;
        }
        if depth < 2 {
            for entry in std::fs::read_dir(&dir).into_iter().flatten().flatten() {
                if entry.file_type().map(|t| t.is_dir()).unwrap_or(false) {
                    walk.push((entry.path(), depth + 1));
                }
            }
        }
    }
    found.sort();
    found
}

/// A file's (inode, mtime ns, size): none of the files that were not rewritten change.
fn file_stamp(path: &Path) -> (u64, i128, u64) {
    use std::os::unix::fs::MetadataExt;
    let meta = std::fs::metadata(path).unwrap_or_else(|e| panic!("{}: {}", path.display(), e));
    (meta.ino(), i128::from(meta.mtime()) * 1_000_000_000 + i128::from(meta.mtime_nsec()), meta.len())
}

/// One ordinary-use run: change one line, stage it, and run the hook (check that it passes).
fn stage_and_hook(root: &Path, text: &str) {
    write(root, "app/queue/main.hy", text);
    git(root, &["add", "app/queue/main.hy"]);
    let (code, stderr) = hook(root, &[]);
    assert_eq!(code, 0, "{}", stderr);
}

/// Failure case: the second hook run, after HEAD advanced by one, advances the same single tree to the new sha: unchanged files are not rewritten (inode and mtime are
/// the same as before, so the facts cache hits), changed files have the new sha's contents, removed files are gone, and added files exist. Previously a separate tree was
/// written out per sha and even unchanged files became new files (the facts cache was a first run every time).
#[test]
fn advancing_head_rewrites_only_the_changed_files_of_the_one_kept_tree() {
    let dir = baseline_repo();
    let root = dir.path();
    stage_and_hook(root, "(defk cycle [] 2)\n");
    let first = kept_trees(root);
    assert_eq!(first.len(), 1, "{:?}", first);
    let tree = first[0].clone();
    let untouched = file_stamp(&tree.join("app/foundation/host.hy"));
    std::fs::remove_file(root.join("app/queue/tool.py")).unwrap();
    write(root, "app/queue/extra.py", "import sys\n");
    git(root, &["add", "-A"]);
    git(root, &["commit", "-q", "-m", "advance"]);
    stage_and_hook(root, "(defk cycle [] 33)\n");
    assert_eq!(kept_trees(root), vec![tree.clone()], "even when HEAD advances there is one tree at the same path");
    assert_eq!(file_stamp(&tree.join("app/foundation/host.hy")), untouched, "rewrote an unchanged file");
    assert_eq!(std::fs::read_to_string(tree.join("app/queue/main.hy")).unwrap(), "(defk cycle [] 2)\n");
    assert!(!tree.join("app/queue/tool.py").exists(), "a removed file remained");
    assert_eq!(std::fs::read_to_string(tree.join("app/queue/extra.py")).unwrap(), "import sys\n");
}

/// Failure case: for a file whose contents changed with the same size, the mtime in the facts cache key (size and mtime) always changes: if missed, it would read the facts of the previous
/// contents.
#[test]
fn a_same_size_change_gets_a_new_cache_key() {
    let dir = baseline_repo();
    let root = dir.path();
    stage_and_hook(root, "(defk cycle [] 2)\n");
    let tree = kept_trees(root).remove(0);
    let before = file_stamp(&tree.join("app/queue/main.hy"));
    git(root, &["commit", "-q", "-m", "same size"]);
    stage_and_hook(root, "(defk cycle [] 3)\n");
    assert_eq!(kept_trees(root), vec![tree.clone()]);
    let after = file_stamp(&tree.join("app/queue/main.hy"));
    assert_eq!(after.2, before.2, "the size is the same");
    assert_ne!(after.1, before.1, "the mtime of a file whose contents changed with the same size did not change");
    assert_eq!(std::fs::read_to_string(tree.join("app/queue/main.hy")).unwrap(), "(defk cycle [] 2)\n");
}

/// Failure case: a tree that crashed midway through a rewrite (no record of the tree's current sha) is not used but rebuilt.
#[test]
fn a_tree_left_in_the_middle_of_an_update_is_rebuilt() {
    let dir = baseline_repo();
    let root = dir.path();
    stage_and_hook(root, "(defk cycle [] 2)\n");
    let tree = kept_trees(root).remove(0);
    std::fs::write(tree.join("app/queue/main.hy"), "(defk half-written [] 0)\n").unwrap();
    // A form that removes the record during a rewrite: a crashed tree has no record.
    match std::fs::remove_file(tree.parent().unwrap().join("tree.sha")) {
        Ok(()) => {}
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => {}
        Err(e) => panic!("{}", e),
    }
    stage_and_hook(root, "(defk cycle [] 3)\n");
    assert_eq!(std::fs::read_to_string(tree.join("app/queue/main.hy")).unwrap(), "(defk cycle [] 1)\n", "used the crashed tree");
}

/// Failure case: trees per sha of the previous form (<sha>, <sha>-<16 digits>, leftover write-outs .partial-…) are deleted the first time the new form runs.
/// Things with other names are not touched.
#[test]
fn old_sha_named_trees_are_removed_on_the_first_run() {
    let dir = baseline_repo();
    let root = dir.path();
    let home = root.join(".git").join("doeff-linter-cache").join("commit-hook-tree");
    let old = [format!("{:040x}", 7), format!("{:040x}-{:016x}", 7, 9), ".partial-crashed".to_string()];
    for name in old.iter().chain(["notes".to_string()].iter()) {
        std::fs::create_dir_all(home.join(name)).unwrap();
    }
    stage_and_hook(root, "(defk cycle [] 2)\n");
    for name in &old {
        assert!(!home.join(name).exists(), "{} remained", name);
    }
    assert!(home.join("notes").is_dir(), "deleted something with another name");
}

/// A repo's declarations with the settings and architecture.hy placed in the dir `pkg/` under git's root (the form of dotfiles' agentcli/).
const SUBDIR_ARCHITECTURE: &str = r#"
(defarchitecture sub
  :root "src/app"
  :layers [(layer core :roles [judgment] :imports [core] :forbid-modules ["urllib.request"])
           (layer foundation :roles [foundation] :imports [foundation])]
  :shared "shared"
  :foundation foundation
  :extensions ["hy" "py"])
"#;

/// Settings: DOEFF102 (modules forbidden per layer) is a rule applied to staged paths; DOEFF114 (placement outside the declarations) has an existing hit.
const SUBDIR_PYPROJECT: &str = r#"[tool.doeff-linter]
enable = ["DOEFF102", "DOEFF114"]

[tool.doeff-linter.commit_hook]
timeout_s = 120
"#;

/// Baseline: commit the settings and declarations in `pkg/` and the file `pkg/src/app/flat.py` outside the declared locations (the existing DOEFF114 hit), and
/// a repo with a new core file `pkg/src/app/shared/core/fetch.py` (imports urllib.request: a new DOEFF102) staged.
fn subdir_repo_with_a_new_hit() -> tempfile::TempDir {
    let dir = tempfile::TempDir::new().unwrap();
    let root = dir.path();
    write(root, "pkg/pyproject.toml", SUBDIR_PYPROJECT);
    write(root, "pkg/architecture.hy", SUBDIR_ARCHITECTURE);
    write(root, "pkg/src/app/flat.py", "X = 1\n");
    write(root, "README.md", "root file\n");
    git(root, &["init", "-q", "-b", "main"]);
    git(root, &["add", "-A"]);
    git(root, &["commit", "-q", "-m", "baseline"]);
    write(root, "pkg/src/app/shared/core/fetch.py", "import urllib.request\n");
    git(root, &["add", "pkg/src/app/shared/core/fetch.py"]);
    dir
}

/// Failure case (cisco-c8's approval, 2026-10-07): when `--root` is a dir under git's root, the staged paths (relative to
/// git's root) are rewritten relative to that dir, and the HEAD tree is also measured with that dir as the root. Before the fix both were off: the new DOEFF102 of staged files was
/// not seen, and the HEAD hits were read as 0, so the existing DOEFF114 was stopped as "not on HEAD". With the form that names the settings and root from git's root (how dotfiles' hook
/// calls it), it stops only on the one new DOEFF102 and not on the existing hit.
#[test]
fn a_root_below_the_git_root_blocks_only_the_new_hit() {
    let dir = subdir_repo_with_a_new_hit();
    let (code, stderr) = hook(dir.path(), &["--config", "pkg/pyproject.toml", "--root", "pkg"]);
    assert_eq!(code, 1, "{}", stderr);
    assert!(stderr.contains("src/app/shared/core/fetch.py::DOEFF102"), "{}", stderr);
    assert!(!stderr.contains("flat.py::DOEFF114"), "{}", stderr);
}

/// For the same repo, a change that only adds a comment to a file with an existing hit passes (the existing DOEFF114 is visible daily, and the commit is not stopped).
#[test]
fn a_root_below_the_git_root_passes_a_change_to_a_file_with_an_old_hit() {
    let dir = subdir_repo_with_a_new_hit();
    let root = dir.path();
    git(root, &["reset", "-q", "pkg/src/app/shared/core/fetch.py"]);
    write(root, "pkg/src/app/flat.py", "# add a comment\nX = 1\n");
    git(root, &["add", "pkg/src/app/flat.py"]);
    let (code, stderr) = hook(root, &["--config", "pkg/pyproject.toml", "--root", "pkg"]);
    assert_eq!(code, 0, "{}", stderr);
}
