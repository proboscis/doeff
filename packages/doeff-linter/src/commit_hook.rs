//! Entry point of the commit hook (`doeff-linter --commit-hook`).
//!
//! Each repo (agora-controllers, merge-queue, and others) does not copy and keep the hook's logic; it calls this one place. All a repo writes is the config's
//! `[tool.doeff-linter.commit_hook]` (the limit in seconds). The original implementation = agora-controllers'
//! `scripts/commit_hook.hy` (lint-staged, whole-repo-fresh).
//!
//! There are 3 kinds of things that stop a commit (all within the staged paths):
//! - Among the staged sources (.hy, .hyk, .hyp, .py), errors of non-critical rules that are outside the registry (the linter lowers registry-known ones to warnings).
//! - Among the staged sources' criticals, those not in the HEAD version (`new_critical` of `--baseline-report`).
//! - Those for which the number of major warnings in the staged sources increased over the HEAD version, per rule (warnings do not change the exit code, so
//!   new warnings crept in and the count did not go down while fixing). The count is compared as a total over the set of staged files (the HEAD version of moved or deleted
//!   files is also counted) -- comparing per file would also stop moves and splits that do not increase warnings. The baseline is the HEAD version itself, so
//!   after a commit that reduced them, the next commit's baseline naturally goes down (no baseline file to keep, nothing to forget to lower).
//!
//! The hook applies the rules decided from one file to the staged paths. A declaration change (config, any architecture fragment, or registry) adds a whole-repo
//! comparison of only the rules that read what changed (promotion_of): the HEAD and tip declarations are read as the linter reads them and compared item by item
//! (project::declaration_items -- a comment-only change promotes no rule), and each rule names the items it reads (ProjectRule::reads_declarations). Rules the config
//! newly enables are promoted too, with no HEAD side (every hit of theirs is new). The promoted rules are measured over the whole repo on both trees under the same
//! limit (timeout_s), and an answer is kept per (git tree id, linter build, rules, what the run reads besides the tree) under the HEAD tree's place (`answers/`) and read
//! instead of measuring again -- the stage of a commit is the HEAD of the next one. Measuring every rule over the whole repo on both trees for any declaration change
//! cost about 7 CPU minutes and 2.9 GB per child in agora-controllers (2026-11-15); the rules a declaration item promotes cost 3-64 CPU seconds there per run
//! (2026-11-16). The merge gate (`--split-rules`, split_for_change) still compares every rule over the whole repo for a declaration change.
//!
//! Criticals of rules with no known list (DOEFF206-209, 211) stop in the second item above if they are in a staged path even when the same key is in HEAD. Those hits are written as
//! "existing hit in a changed file (also on main ...)" rather than "not on HEAD", with one line of guidance on how to fix added
//! (split_by_head -- the decision to stop is unchanged; only the sentence is split).
//!
//! The child linter has a limit per run (default 20 seconds; 0 means no limit), and the commit is stopped if it is exceeded (exit code 1;
//! the limit is measured in the child's CPU seconds -- on a busy machine only the wall seconds stretch, and with no limit 0-hit commits were cut off every time.
//! For a child that does not finish without using CPU, a wall backstop limit [limit x WALL_BACKSTOP_FACTOR] is kept, and on machines where the child's CPU seconds cannot be read [Macs
//! without /proc] it is measured in wall seconds -- the decision is in the one place cut_measure;
//! original issue #2723). Previously it let the commit through when exceeded, merely writing in one line what had been passed without checking -- workers did not
//! re-verify commits that passed. When stopping, it writes in one line which comparison, on which tree, which rules, and
//! cut off at what limit in seconds, followed by a command that redoes the same comparison without a limit (unbounded_command -- in a form that can be pasted and run as is) and guidance to
//! commit again after confirming the result is 0 hits (do not use --no-verify).
//! The tree on which the HEAD version is measured is placed, one per repo, at `commit-hook-tree/<name representing the repo>/tree` under the fact cache's root, and when HEAD advances only the files changed by git's diff
//! are rewritten (head_tree -- the root path and the mtimes of unchanged files are preserved, so the fact cache works even for the single run that measures the HEAD version).
//! The pure parts (the split of rules, the difference of identifiers, the selection of hits that stop, the sentence for a comparison that could not be measured, and the command without a limit) are separated into functions and verified by unit tests.
//!
//! Report output (argument `--report-out <file>`): the two measurements (the HEAD version and the staged paths) are written, each as the list of measured paths,
//! the violations (still in editor-json's violation shape -- the HEAD version after re-pointing to the tip's root path), and the IDs of the rules judged, into one JSON (report_of).
//! The conversation's commit hook sends this report to the record together with the conversation id, as material for reading, commit by commit, the violations the conversation added or removed
//! (finding 2 of the design record project-health-ide). The blocking decision, exit code, and stderr sentences are the same as without the argument, and when it cannot be written the exit code does not
//! change either (one line on stderr -- recording diagnostics does not stop a commit).

use crate::config::{CommitHookSection, Config};
use serde::Serialize;
use serde_json::Value;
use std::collections::BTreeSet;
use std::io::Read;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

/// Default limit per child linter run (seconds).
pub const DEFAULT_TIMEOUT_S: u64 = 20;
/// The seconds value that means no limit (argument --commit-hook-timeout-s, config timeout_s).
pub const UNBOUNDED_TIMEOUT_S: u64 = 0;
/// The extensions at the end of the staged paths that the linter reads.
pub const LINTED_SUFFIXES: [&str; 4] = [".hy", ".hyk", ".hyp", ".py"];
/// The head of the lines printed.
const PREFIX: &str = "doeff-linter commit-hook: ";

/// The enabled rules split into rules decided by a single file (quick -- the commit hook applies them to the staged paths) and rules that need the whole repo (whole -- the gate's interface `--split-rules`
/// applies them to the whole-repo comparison; the hook does not).
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct RuleSplit {
    pub quick: Vec<String>,
    pub whole: Vec<String>,
}

/// Pure: from the enabled rules, removes the semantic rules that ask Jev (the rule's declaration config::is_semantic -- not asked at commit; even just reading the cache is slow), and splits the rest into whole (those the rule declares as needing the whole repo, config::needs_whole_repo) and
/// quick (order preserved). It used to split by the hand-written list whole_repo_rules in the config, and hits of rules that were forgotten to be added to the list (DOEFF149, 161) got into main
/// without being caught at either stage. Semantic rules were also excluded by the ID prefix DOEFF2, so deterministic rules with the same prefix
/// (DOEFF206-209) fell into neither.
pub fn split_rules(enabled: &[String]) -> RuleSplit {
    let (whole, quick): (Vec<String>, Vec<String>) = enabled
        .iter()
        .filter(|r| !crate::config::is_semantic(r))
        .cloned()
        .partition(|r| crate::config::needs_whole_repo(r));
    RuleSplit { quick, whole }
}

/// Pure: the notice for when the retired config key whole_repo_rules is left in place (not read -- rules are split by their own declaration).
pub fn retired_whole_repo_rules_note(section: Option<&CommitHookSection>) -> Option<String> {
    section.filter(|s| !s.whole_repo_rules.is_empty()).map(|_| {
        "settings: whole_repo_rules is a retired key and is not read — which rules need a whole-repo comparison is declared by the linter rules themselves (`doeff-linter --list-rules`). Remove the key".to_string()
    })
}

/// Pure: among the staged paths, those that exist in the working tree (exists) and that the linter reads.
pub fn linted_paths(staged: &[String], exists: impl Fn(&str) -> bool) -> Vec<String> {
    staged.iter().filter(|p| LINTED_SUFFIXES.iter().any(|s| p.ends_with(s)) && exists(p)).cloned().collect()
}

/// Pure: among the editor-json output, the hits that stop -- errors of non-critical rules that are outside the registry (severity is error and level is not critical).
/// Criticals are judged only by the comparison with HEAD, not by the presence of the registry, so they are not included.
pub fn blocking_violations(report: &Value) -> Vec<&Value> {
    violations(report).filter(|v| v["severity"] == "error" && v["level"] != "critical").collect()
}

/// Pure: the lines of the errors that stop (blocking_violations of the tip, as violation_line) -- every one in a staged path, and, outside the staged paths, only those the HEAD
/// version (head, re-pointed to the tip's root) does not have on the same line. A declaration change measures the whole repo, and an untouched file is the same on both trees,
/// so its error is new only when the declaration made it (as for criticals, which are compared by new_critical). Without the comparison, every existing error on main
/// outside the registry stopped every declaration change (2026-11-13: DOEFF004 and DOEFF119 hits already on main stopped a comment-only change to architecture.hy).
/// A source-only commit measures only staged paths, so nothing changes for it (an existing error in a file you touch still stops).
pub fn stopping_errors(tip: &Value, head: &Value, staged: &[String]) -> Vec<String> {
    let on_head: BTreeSet<String> = blocking_violations(head).into_iter().map(|v| violation_line(head, v)).collect();
    blocking_violations(tip)
        .into_iter()
        .filter(|v| {
            let path = relative_to_root(tip, v["path"].as_str().unwrap_or(""));
            staged.iter().any(|p| p == path) || !on_head.contains(&violation_line(tip, v))
        })
        .map(|v| violation_line(tip, v))
        .collect()
}

fn violations(report: &Value) -> impl Iterator<Item = &Value> {
    report["violations"].as_array().into_iter().flatten()
}

/// Makes a violation's path relative to the report's root (as is if outside the root).
fn relative_to_root<'a>(report: &Value, path: &'a str) -> &'a str {
    match report["root"].as_str() {
        Some(root) => path.strip_prefix(root).map(|rest| rest.trim_start_matches('/')).unwrap_or(path),
        None => path,
    }
}

/// Pure: counts per rule the major warnings of the report (severity is warning and level is major -- criticals are handled by new_critical and errors outside the registry by
/// blocking_violations).
fn major_warning_counts(report: &Value) -> std::collections::BTreeMap<String, usize> {
    let mut counts = std::collections::BTreeMap::new();
    for v in violations(report).filter(|v| v["severity"] == "warning" && v["level"] == "major") {
        *counts.entry(v["rule"].as_str().unwrap_or("").to_string()).or_insert(0) += 1;
    }
    counts
}

/// Pure: over the set of staged files, one line each for the rules whose number of major warnings increased at the tip over the HEAD version (head)
/// (`<rule> <HEAD count> → <tip count>`, in the rules' dictionary order). Rules with the same or a decreased count are not listed.
pub fn grown_major_warnings(head: &Value, tip: &Value) -> Vec<String> {
    let before = major_warning_counts(head);
    major_warning_counts(tip)
        .into_iter()
        .filter_map(|(rule, n)| {
            let was = before.get(&rule).copied().unwrap_or(0);
            (n > was).then(|| format!("{} {} → {}", rule, was, n))
        })
        .collect()
}

/// A pair that splits the identifiers of hits that stop into those with no same identifier in the HEAD version (newly increased hits) and those with one (for rules with no known list, existing hits in
/// changed files) (both in dictionary order). Whether to stop is unchanged; only the sentences printed are split (request of cisco-c8,
/// 2026-10-07 -- existing hits were also written as "not on HEAD", and a worker read that as a hook defect and nearly skipped the hook).
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct ByHead {
    pub not_on_head: Vec<String>,
    pub already_on_head: Vec<String>,
}

impl ByHead {
    pub fn is_empty(&self) -> bool {
        self.not_on_head.is_empty() && self.already_on_head.is_empty()
    }
}

/// Pure: splits the identifiers of hits that stop (blocking -- new_critical of the run applied to the staged paths) by whether they are in the set of identifiers of the HEAD version (head).
pub fn split_by_head(head: &BTreeSet<String>, blocking: impl IntoIterator<Item = String>) -> ByHead {
    let all: BTreeSet<String> = blocking.into_iter().collect();
    let (already_on_head, not_on_head) = all.into_iter().partition(|id| head.contains(id));
    ByHead { not_on_head, already_on_head }
}

/// Pure: re-points the paths and root of a report's violations from the root `from` to the root `to` -- so that output run on the HEAD tree is compared with output run on the tip's root
/// by the same path (the identifier of a violation with no key includes an absolute path).
pub fn rebase_paths(report: &mut Value, from: &str, to: &str) {
    let rebase = |text: &str| -> Option<String> {
        let rest = text.strip_prefix(from)?;
        (rest.is_empty() || rest.starts_with('/')).then(|| format!("{}{}", to, rest))
    };
    if let Some(root) = report["root"].as_str().and_then(rebase) {
        report["root"] = Value::String(root);
    }
    if let Some(list) = report["violations"].as_array_mut() {
        for violation in list {
            if let Some(path) = violation["path"].as_str().and_then(rebase) {
                violation["path"] = Value::String(path);
            }
        }
    }
}

/// Pure: writes a hit that stops in one line (`<relative path>:<line>: <rule> <first line of message>`).
pub fn violation_line(report: &Value, violation: &Value) -> String {
    let path = relative_to_root(report, violation["path"].as_str().unwrap_or(""));
    let line = violation["range"]["start"]["line"].as_u64().unwrap_or(0) + 1;
    let message = violation["message"].as_str().unwrap_or("").lines().next().unwrap_or("");
    format!("{}:{}: {} {}", path, line, violation["rule"].as_str().unwrap_or(""), message)
}

/// Pure: chooses the limit per child linter run from the config and arguments (the argument wins; otherwise the config; otherwise the default). 0 seconds means no limit (None).
pub fn resolve_timeout(section: Option<&CommitHookSection>, cli: Option<u64>) -> Option<Duration> {
    match cli.or_else(|| section.and_then(|s| s.timeout_s)).unwrap_or(DEFAULT_TIMEOUT_S) {
        UNBOUNDED_TIMEOUT_S => None,
        secs => Some(Duration::from_secs(secs)),
    }
}

/// Pure: the list of enabled rules after applying the config's enable and disable and the arguments (the same merge_config as normal runs; None means all rules).
pub fn effective_rules(config: Option<&Config>, cli_enable: &[String], cli_disable: &[String]) -> Vec<String> {
    crate::config::merge_config(config, cli_enable, cli_disable, &[]).0.unwrap_or_else(crate::config::get_all_rule_ids)
}

/// The declaration files and dirs (paths from the repo root) -- the config file, architecture.hy, and the registry tables. A commit that changes these can also attach the hits of rules decided by a single file
/// to files outside the change (adding a module forbidden to a layer turns an untouched core file red).
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct Declarations {
    pub files: Vec<String>,
    pub dirs: Vec<String>,
    /// The declarations that decide which rules are on or off and the layers (the config file and architecture.hy) -- what is copied to the baseline tree by the one-time directive that measures the baseline with the current declarations (Lint-Baseline)
    /// (the registry is not copied -- changes in the registry are compared as before).
    pub switches: Vec<String>,
}

/// The commit-body line (trailer) of the one-time directive that measures the baseline with the current declarations -- `Lint-Baseline: declarations <reason>`. For a commit that starts to fire a rule
/// (adding to enable, adding a layer declaration), all the existing hits become "hits not in the baseline", and that commit itself cannot pass the gate and the hook. Only for a change with this line,
/// the baseline tree is measured with the current declarations (switches), the existing hits are treated as present in the baseline, and only new hits stop (this is not a form of
/// putting them on the known list -- decision of cisco-c8, 2026-10-01). It is a form that would miss a change that attaches hits to untouched files by declaration alone (#2127),
/// so a line without a reason has no effect (leave in the body who used it and for what).
pub const BASELINE_TRAILER: &str = "Lint-Baseline: declarations";

/// Pure: if the commit body has a Lint-Baseline line with a reason, the list of declaration files to copy to the baseline tree (empty if none).
pub fn baseline_overlay(message: &str, declarations: &Declarations) -> Vec<String> {
    let requested = message.lines().any(|line| {
        line.trim_start().strip_prefix(BASELINE_TRAILER).is_some_and(|reason| !reason.trim_matches(|c: char| c.is_whitespace() || c == '—' || c == '-' || c == ':').is_empty())
    });
    if requested { declarations.switches.clone() } else { Vec::new() }
}

/// Gathers the declaration files and dirs from the config. Where architecture.hy lives is decided the same way as in the linter's normal run (the config's architecture, or the root's
/// architecture.hy). Registry dirs and files are from the root, and the config's registry files are from the config file's dir.
pub fn declarations_of(root: &Path, config: Option<(&Config, &Path)>) -> Declarations {
    let rel = |path: &Path| crate::project::paths::declared_rel(root, path);
    let config_dir = config.and_then(|(_, path)| path.parent().map(Path::to_path_buf)).unwrap_or_else(|| root.to_path_buf());
    // Every declaration file (a config architecture list is read as one declaration — a change to any of its files is a change to the declaration): the files a glob names now,
    // and the glob itself (a file the commit adds or deletes under it is a change to the declaration too).
    let spellings = crate::config::declaration_spellings(config.map(|(c, _)| c), &config_dir, root);
    let architecture: Vec<String> = spellings
        .iter()
        .flat_map(|spelling| std::iter::once(spelling.clone()).chain(crate::project::paths::expand_glob(spelling).into_iter().filter(move |f| f != spelling)))
        .map(|path| rel(&path))
        .fold(Vec::new(), |mut out, path| {
            if !out.contains(&path) {
                out.push(path);
            }
            out
        });
    let registry = config.and_then(|(c, _)| c.registry.as_ref());
    let files = config
        .map(|(_, path)| rel(path))
        .into_iter()
        .chain(architecture.iter().cloned())
        .chain(registry.into_iter().flat_map(|r| r.files.iter().cloned()))
        .chain(registry.into_iter().flat_map(|r| r.config_files.iter().map(|f| rel(&config_dir.join(f)))))
        .collect();
    let dirs = registry.map(|r| r.dirs.iter().map(|d| d.trim_end_matches('/').to_string()).collect()).unwrap_or_default();
    let switches = config.map(|(_, path)| rel(path)).into_iter().chain(architecture.iter().filter(|f| !f.contains('*')).cloned()).collect();
    Declarations { files, dirs, switches }
}

/// Pure: whether the changed paths (from the root) include a declaration file or a file under a declaration dir. A declaration file written as a glob (`architecture/*.hy`)
/// counts every path it matches.
pub fn touches_declaration(changed: &[String], declarations: &Declarations) -> bool {
    changed.iter().any(|path| {
        declarations.files.iter().any(|f| f == path || (f.contains('*') && glob_names(f, path)))
            || declarations.dirs.iter().any(|d| path.starts_with(&format!("{}/", d)))
    })
}

/// Pure: whether a glob from the root (`/`-separated) names the path from the root (the whole path is matched, not only the file name).
fn glob_names(glob: &str, path: &str) -> bool {
    let pattern: Vec<&str> = glob.split('/').filter(|p| !p.is_empty()).collect();
    crate::project::paths::segments_match(&pattern, &path.split('/').collect::<Vec<_>>())
}

/// Pure: the split of rules to apply to a change -- the one place shared by the gate and the commit hook. Normally split by the rules' own declarations (split_rules), and for a change that changed
/// a declaration file, rules decided by a single file are also put in the whole-repo comparison (quick is empty). There is no hand-written list.
pub fn split_for_change(enabled: &[String], changed: &[String], declarations: &Declarations) -> RuleSplit {
    let split = split_rules(enabled);
    if !touches_declaration(changed, declarations) {
        return split;
    }
    let whole = enabled.iter().filter(|r| split.quick.contains(r) || split.whole.contains(r)).cloned().collect();
    RuleSplit { quick: Vec::new(), whole }
}

/// The premises of one hook run.
#[derive(Debug, Clone)]
pub struct CommitHookOptions {
    /// The git working tree root (the child linter's cwd and the base of staged paths).
    pub root: PathBuf,
    /// The config file (absolute path; if none, the child looks for the config).
    pub config: Option<PathBuf>,
    /// The enabled rules (the split is decided by split_for_change looking at the staged paths).
    pub enabled: Vec<String>,
    pub declarations: Declarations,
    /// The current declaration files to copy to the baseline (the HEAD tree) (only when the commit body has a Lint-Baseline line -- not copied if empty).
    pub overlay: Vec<String>,
    /// The commit body file (argument --commit-message -- the same file is passed to the no-limit command too).
    pub message: Option<PathBuf>,
    /// The arguments --enable and --disable (passed as is to the no-limit command).
    pub cli_enable: Vec<String>,
    pub cli_disable: Vec<String>,
    /// The limit per child linter run (None = no limit).
    pub timeout: Option<Duration>,
    /// The linter fired as the child (normally the current binary).
    pub linter: PathBuf,
    /// The root of the store for the HEAD tree's results (the root of facts_cache -- if None, the store is not used and the HEAD tree is measured every time).
    pub cache: Option<PathBuf>,
    /// The repo's catalogs of real I/O handlers (absolute paths -- the tip config's world_catalogs from its dir, or the --world-catalog arguments).
    /// Passed to both children as --world-catalog: the HEAD tree is placed outside the checkout, where the config's relative paths do not reach the package's repo.
    pub world_catalogs: Vec<PathBuf>,
    /// The checkout's dirs outside the root for resolving where an effect name is defined (absolute paths -- origins::dependency_roots of the root, or the --import-root
    /// arguments). Passed to the HEAD child as --import-root: from the HEAD tree the relative paths of `[tool.uv.sources]` do not reach the package's repo and there is no `.venv`.
    pub import_roots: Vec<PathBuf>,
}

impl CommitHookOptions {
    /// Builds the premises from the config and arguments.
    pub fn new(root: PathBuf, config: Option<(&Config, PathBuf)>, cli_enable: &[String], cli_disable: &[String], cli_timeout: Option<u64>, linter: PathBuf) -> Self {
        let section = config.as_ref().and_then(|(c, _)| c.commit_hook.as_ref());
        let enabled = effective_rules(config.as_ref().map(|(c, _)| *c), cli_enable, cli_disable);
        if let Some(note) = retired_whole_repo_rules_note(section) {
            eprintln!("{}{}", PREFIX, note);
        }
        let world_catalogs = config
            .as_ref()
            .map(|(c, path)| crate::config::repo_world_catalogs(&[], Some(*c), path.parent().unwrap_or(Path::new("."))))
            .unwrap_or_default();
        CommitHookOptions {
            declarations: declarations_of(&root, config.as_ref().map(|(c, path)| (*c, path.as_path()))),
            enabled,
            overlay: Vec::new(),
            message: None,
            cli_enable: cli_enable.to_vec(),
            cli_disable: cli_disable.to_vec(),
            timeout: resolve_timeout(section, cli_timeout),
            import_roots: crate::origins::dependency_roots(&root),
            config: config.map(|(_, path)| path),
            root,
            linter,
            cache: crate::project::facts_cache::cache_base(),
            world_catalogs,
        }
    }
}

/// The tree on which the child linter is fired. The spelling of the measured target in the report (report_of) is `HEAD` or `stage`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
pub enum Side {
    /// The HEAD tree (the baseline placed at commit-hook-tree/<name representing the repo>/tree under the fact cache's root -- head_tree).
    #[serde(rename = "HEAD")]
    Head,
    /// The tip tree (the git working tree itself -- measures the staged paths).
    #[serde(rename = "stage")]
    Tip,
}

impl Side {
    fn name(self) -> &'static str {
        match self {
            Side::Head => "HEAD tree",
            Side::Tip => "tip tree (working tree)",
        }
    }
}

/// One request for a child linter -- which tree, which rules to apply, and the arguments passed to the child.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LintRun {
    pub side: Side,
    pub rules: Vec<String>,
    pub args: Vec<String>,
}

/// The yardstick by which the cut-off was judged (the limit is measured in the child's CPU seconds).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Measure {
    /// The child's CPU seconds (utime and stime of the child and its waited-for descendants) exceeded the limit.
    Cpu,
    /// On a machine where the child's CPU seconds cannot be read (a Mac without /proc), the wall seconds exceeded the limit.
    Wall,
    /// The CPU seconds are within the limit, but the wall seconds exceeded the backstop limit (limit x WALL_BACKSTOP_FACTOR) -- a child that does not finish without using CPU.
    WallBackstop,
}

/// The multiplier of the wall backstop limit that cuts off a child that does not finish without using CPU (how many times the CPU-seconds limit). A child within the CPU-seconds limit is not cut off by wall
/// until a core's share on a busy machine falls below 1/4 (node-h has 36 cores and load 73, giving a share of about 1/2).
pub const WALL_BACKSTOP_FACTOR: u32 = 4;

/// Pure: the decision whether to cut off one child linter run -- wall = wall seconds since it was started, cpu = the child's CPU seconds (None on machines where it cannot be read),
/// limit = the limit. If CPU seconds can be read, measure by CPU seconds and look at wall only for the backstop limit (limit x WALL_BACKSTOP_FACTOR). If not, measure by wall.
pub fn cut_measure(wall: Duration, cpu: Option<Duration>, limit: Duration) -> Option<Measure> {
    match cpu {
        Some(cpu) if cpu >= limit => Some(Measure::Cpu),
        Some(_) if wall >= limit * WALL_BACKSTOP_FACTOR => Some(Measure::WallBackstop),
        Some(_) => None,
        None if wall >= limit => Some(Measure::Wall),
        None => None,
    }
}

/// Pure: reads CPU seconds from the contents of /proc/<pid>/stat -- the sum of utime, stime, cutime, and cstime (fields 14 to 17 of the whole; the unit is USER_HZ = 1/100
/// second -- the kernel's ABI, independent of the machine). The name field (the 2nd) can contain ')', so count from after the last ')'.
pub fn cpu_of_stat(text: &str) -> Option<Duration> {
    let rest = &text[text.rfind(')')? + 1..];
    let fields: Vec<&str> = rest.split_whitespace().collect();
    // The first one after ')' is state (the 3rd of the whole) -- the 14th to 17th of the whole are the 11th to 14th here (counting from 0).
    let ticks = fields.get(11..15)?.iter().map(|field| field.parse::<u64>().ok()).sum::<Option<u64>>()?;
    Some(Duration::from_millis(ticks * 10))
}

/// The child process's CPU seconds (only on Linux with /proc -- on other machines and when unreadable it is None, and the cut-off is measured by wall).
#[cfg(target_os = "linux")]
fn child_cpu(pid: u32) -> Option<Duration> {
    std::fs::read_to_string(format!("/proc/{}/stat", pid)).ok().as_deref().and_then(cpu_of_stat)
}

#[cfg(not(target_os = "linux"))]
fn child_cpu(_pid: u32) -> Option<Duration> {
    None
}

/// One child linter run cut off by the limit -- on which tree, which rules, at what limit in seconds, and by which yardstick.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Cut {
    pub side: Side,
    pub rules: Vec<String>,
    pub limit: Duration,
    pub measure: Measure,
}

/// A comparison that could not be measured -- the name of the comparison (such as "the HEAD version") and the one run that was cut off.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Unmeasured {
    pub what: String,
    pub cut: Cut,
}

/// Pure: writes in one line a comparison that could not be measured (what was stopped without being checked).
pub fn unmeasured_line(unmeasured: &Unmeasured) -> String {
    let Cut { side, rules, limit, measure } = &unmeasured.cut;
    let secs = limit.as_secs();
    let how = match measure {
        Measure::Cpu => format!("cut off at the CPU-seconds limit of {} s", secs),
        Measure::Wall => format!("cut off at the wall limit of {} s (child CPU seconds cannot be read on this machine)", secs),
        Measure::WallBackstop => format!(
            "cut off at the wall backstop limit of {} s ({} times the CPU-seconds limit of {} s; a child that does not finish without using CPU)",
            secs * u64::from(WALL_BACKSTOP_FACTOR),
            WALL_BACKSTOP_FACTOR,
            secs
        ),
    };
    format!(
        "could not measure {} — the child linter for {} rule(s) ({}) applied on the {} was {}. The hits of these rules have not been checked, so the commit is stopped (limit = timeout_s in [tool.doeff-linter.commit_hook] of the settings: child CPU seconds; 0 means no limit)",
        unmeasured.what,
        rules.len(),
        rules.join(","),
        side.name(),
        how
    )
}

/// Pure: one word in a form that can be pasted into a shell as is (as is if only safe characters; otherwise enclosed in ').
fn shell_word(word: &str) -> String {
    let plain = !word.is_empty() && word.bytes().all(|b| b.is_ascii_alphanumeric() || b"_@%+=:,./-".contains(&b));
    if plain { word.to_string() } else { format!("'{}'", word.replace('\'', "'\\''")) }
}

/// Pure: the command that redoes the same hook judgment without a limit (one line that a worker can paste and run as is) -- adds no limit (--commit-hook-timeout-s 0) to the same linter, root, config,
/// --enable / --disable, and commit body file. The stage reads the same index.
pub fn unbounded_command(options: &CommitHookOptions) -> String {
    let path = |p: &Path| p.to_string_lossy().into_owned();
    let mut words = vec![path(&options.linter), "--commit-hook".to_string(), "--root".to_string(), path(&options.root)];
    match &options.config {
        Some(config) => words.extend(["--config".to_string(), path(config)]),
        None => words.push("--no-config".to_string()),
    }
    if !options.cli_enable.is_empty() {
        words.extend(["--enable".to_string(), options.cli_enable.join(",")]);
    }
    if !options.cli_disable.is_empty() {
        words.extend(["--disable".to_string(), options.cli_disable.join(",")]);
    }
    if let Some(message) = &options.message {
        words.extend(["--commit-message".to_string(), path(message)]);
    }
    for catalog in &options.world_catalogs {
        words.extend(["--world-catalog".to_string(), path(catalog)]);
    }
    words.extend(["--commit-hook-timeout-s".to_string(), UNBOUNDED_TIMEOUT_S.to_string()]);
    words.iter().map(|w| shell_word(w)).collect::<Vec<_>>().join(" ")
}

/// Pure: the lines for when the commit was stopped by a cut-off -- one line for the comparison that could not be measured, the no-limit command, and the guidance to redo.
pub fn cut_lines(options: &CommitHookOptions, unmeasured: &Unmeasured) -> Vec<String> {
    vec![
        unmeasured_line(unmeasured),
        format!("Command to redo the same comparison without a limit: {}", unbounded_command(options)),
        "Confirm that this command exits with code 0 (0 hits that stop the commit) before committing again. Do not use --no-verify".to_string(),
    ]
}

/// The answer of one child linter run.
#[derive(Debug)]
pub enum Measured {
    Report(Value),
    /// The limit was exceeded (stop the commit -- write the one run that was cut off).
    TimedOut(Cut),
    /// It did not run, or the output is not JSON (the reason).
    Failed(String),
}

/// Runs the child linter with editor-json, and if it finishes within the limit, reads the output (if timeout is None, waits until it finishes; the limit is measured in the child's CPU seconds --
/// cut_measure). Exit codes 0, 1, 3, and 4 count as measured (judged by the contents), and anything else is a failure with a reason.
pub fn run_linter(linter: &Path, cwd: &Path, run: &LintRun, timeout: Option<Duration>) -> Measured {
    let started = Instant::now();
    // The child is part of the hook -- it does not take the machine's check slot (editor-json is the form of a heavy mode, so without the mark it would wait for the slot).
    let spawned = Command::new(linter)
        .args(["--output-format", "editor-json", "--no-log"])
        .args(&run.args)
        .env(crate::check_slot::GATED_ENV, "1")
        .current_dir(cwd)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn();
    let mut child = match spawned {
        Ok(child) => child,
        Err(error) => return Measured::Failed(format!("cannot start {}: {}", linter.display(), error)),
    };
    // The output is large (the whole repo's JSON), so read it to the end in a separate thread even while waiting, so the pipe does not clog.
    let reader = |stream: Option<Box<dyn Read + Send>>| {
        std::thread::spawn(move || {
            let mut buf = Vec::new();
            if let Some(mut stream) = stream {
                let _ = stream.read_to_end(&mut buf);
            }
            buf
        })
    };
    let stdout = reader(child.stdout.take().map(|s| Box::new(s) as Box<dyn Read + Send>));
    let stderr = reader(child.stderr.take().map(|s| Box::new(s) as Box<dyn Read + Send>));
    let status = loop {
        match child.try_wait() {
            Ok(Some(status)) => break status,
            Ok(None) => {
                if let Some(limit) = timeout {
                    if let Some(measure) = cut_measure(started.elapsed(), child_cpu(child.id()), limit) {
                        let _ = child.kill();
                        let _ = child.wait();
                        return Measured::TimedOut(Cut { side: run.side, rules: run.rules.clone(), limit, measure });
                    }
                }
                std::thread::sleep(Duration::from_millis(20));
            }
            Err(error) => return Measured::Failed(format!("cannot wait for the child linter: {}", error)),
        }
    };
    let out = stdout.join().unwrap_or_default();
    let err = stderr.join().unwrap_or_default();
    // The child's stderr is carried into the reason in all its lines (for a config error, the first line is only the heading "config errors:" and the naming starts from the next line -- with the first line alone,
    // which declaration is wrong would be lost).
    let err_line = String::from_utf8_lossy(&err).lines().map(str::trim).filter(|l| !l.is_empty()).collect::<Vec<_>>().join(" / ");
    match status.code() {
        Some(0 | 1 | 3 | 4) => match serde_json::from_slice::<Value>(&out) {
            Ok(value) => Measured::Report(value),
            Err(error) => Measured::Failed(format!("output is not JSON ({}){}", error, err_line)),
        },
        code => Measured::Failed(format!("exit code {:?} — {}", code, err_line)),
    }
}

/// A temporary dir (only one this run made -- removed at the end).
struct Scratch {
    path: PathBuf,
}

impl Scratch {
    /// Made with a unique name under std::env::temp_dir() (an existing name is not used).
    fn create() -> Result<Scratch, String> {
        let path = unique_dir(&std::env::temp_dir(), &format!("doeff-linter-commit-hook-{}", std::process::id()))?;
        let path = path.canonicalize().unwrap_or(path);
        Ok(Scratch { path })
    }
}

impl Drop for Scratch {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.path);
    }
}

/// Makes a dir with the unique name `<prefix>-<ns>-<n>` under base (an existing name is not used).
fn unique_dir(base: &Path, prefix: &str) -> Result<PathBuf, String> {
    let nanos = SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_nanos()).unwrap_or(0);
    for attempt in 0..100u32 {
        let path = base.join(format!("{}-{}-{}", prefix, nanos, attempt));
        match std::fs::create_dir(&path) {
            Ok(()) => return Ok(path),
            Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => continue,
            Err(error) => return Err(format!("cannot create temporary dir {}: {}", path.display(), error)),
        }
    }
    Err("cannot make a unique name for a temporary dir".to_string())
}

// Where the HEAD tree is placed. doeff-linter's fact cache (project::facts_cache) is placed in a dir per root path, keyed by each file's
// size and mtime. If the tree were written out to a temporary dir or a dir per sha, then in the usual usage where HEAD advances at every commit, every run would be
// a new root = a first run with no cache (about 66 seconds of CPU in agora-controllers). The tree is placed at one fixed path per repo, and when HEAD advances only the files changed by
// git's diff are rewritten -- unchanged files are not touched, so mtimes are preserved and the fact cache hits.
// The shape is aligned with agora-controllers' pre-merge check for main (repo-base-tree in scripts/land_lint_gate.hy):
//   Location = under the fact cache's root (facts_cache::cache_base -- by default `$XDG_CACHE_HOME/doeff-linter`, otherwise `~/.cache/doeff-linter`)
//   `commit-hook-tree/<name representing the repo>/`: the tree `tree/`, the record `tree.sha` of the tree's current sha and the declaration files overlaid on it, the lock file `lock`, and
//   the record `repo.path` of the absolute path of the repo's git common dir.
//   The tree is the contents of the root (--root) dir, and if the root is a dir under the working tree's root, the name is the first 16 hex digits of the sha256 of the path made by joining that position (`git rev-parse --show-prefix`)
//   to the common dir (roots at different positions do not fight over one tree).
//   Name representing the repo = the first 16 hex digits of the sha256 of the absolute, normalized path of the repo's git common dir (`git rev-parse --git-common-dir`). Other
//   working trees of the same repo have the same name and share one tree -- there are hundreds of working trees (561 for agora-controllers on node-h), so per-working-tree trees would run out of disk (one tree is
//   about 35MB), and even the first commit in a new working tree can advance, by a diff only, from a tree advanced by other working trees (near the main line).
//   The mtime of rewritten files is set to the time they were written (tar's -m) -- with the commit's time (second granularity), two commits in the same second would give
//   files of the same size with different contents the same key, and the facts of the previous contents would be read.
//   During a rewrite the record `tree.sha` is removed, and after finishing it is put in place by renaming a temporary file. A tree where the record is absent or unreadable, or the diff cannot
//   be taken from the recorded sha, is treated as a tree that crashed midway and everything is written out again (everything is written only in this case and the very first time). The overlaid declaration files (the one-time directive
//   Lint-Baseline) are put in the record, and restored to the contents of the tree's sha when next advancing.
//   Two at the same time: while using the tree (from the rewrite until the child linter finishes measuring), hold the exclusive lock of `lock`. If it cannot be taken after waiting LOCK_WAIT,
//   write the tree out into this run's temporary dir and use that (do not touch the placed tree -- the fact cache does not work, but it does not stop).
//   Cleanup: delete trees per sha in the previous form (`<sha>`, `<sha>-<16 digits>`, and leftover write-outs `.partial-…`) when found. Also delete repo dirs whose recorded `repo.path` common dir
//   no longer exists (only those whose lock could be taken). Only dirs of this name shape are deleted (other things in this location are not touched).
//   The location dir itself is given the mark that facts_cache uses, and is included in the cleanup of the overall cap on the fact cache's root (sweep_over_cap).

/// The name of the dir where the HEAD tree is placed (directly under the fact cache's root).
pub const TREE_HOME_DIR: &str = "commit-hook-tree";
/// The name of the tree's dir inside the repo's dir.
pub const TREE_DIR: &str = "tree";
/// The record, inside the repo's dir, of the tree's current sha and the overlaid declaration files.
pub const TREE_RECORD: &str = "tree.sha";
/// The lock file inside the repo's dir.
pub const TREE_LOCK: &str = "lock";
/// The record, inside the repo's dir, of the absolute path of the repo's git common dir.
pub const REPO_RECORD: &str = "repo.path";
/// The dir, inside the repo's dir, of the whole-repo answers (answer_key).
pub const ANSWERS_DIR: &str = "answers";
/// How many whole-repo answers are kept per repo (the oldest are removed when one is stored).
pub const ANSWERS_KEPT: usize = 32;
/// The upper limit of waiting when another run at the same time is using the tree.
pub const LOCK_WAIT: Duration = Duration::from_secs(30);
/// If more paths than this need rewriting, write everything out instead of advancing by diff (just before git's argument length limit).
const REWRITE_LIMIT: usize = 4000;
/// The number of paths passed to git archive at once.
const ARCHIVE_CHUNK: usize = 500;

/// How the tree was prepared.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum TreeUpdate {
    /// Wrote everything out (the first time, the record is absent or unreadable, the diff could not be taken, or too many paths changed).
    Rebuilt,
    /// Advanced by diff from the recorded sha -- rewritten = the number of files written, removed = the number of paths removed.
    Advanced { rewritten: usize, removed: usize },
    /// The recorded sha is the same as HEAD, and there are no declaration files to restore or overlay -- nothing was written.
    Unchanged,
    /// Another run at the same time was using the tree -- a tree written out into this run's temporary dir (the placed tree is not touched).
    Temporary,
}

/// The prepared HEAD tree -- path = the tree's root. The lock is held until the tree is no longer used (this value is dropped).
#[derive(Debug)]
pub struct HeadTree {
    pub path: PathBuf,
    pub update: TreeUpdate,
    /// Where the whole-repo answers of this repo are kept (`<name representing the repo>/answers` -- None for a temporary tree, whose path is not stable).
    pub answers: Option<PathBuf>,
    _lock: Option<std::fs::File>,
}

/// The tree's record -- the tree's current sha and the declaration files overlaid on it from the tip.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TreeRecord {
    pub sha: String,
    pub overlay: Vec<String>,
}

/// Pure: whether it is n hex digits.
fn is_hex(text: &str, n: usize) -> bool {
    text.len() == n && text.bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

/// Pure: reads the contents of the record file -- line 1 is the sha (40 or 64 hex digits), and the following lines are the overlaid declaration files. None if the shape differs (a crashed tree).
pub fn parse_tree_record(text: &str) -> Option<TreeRecord> {
    let mut lines = text.lines();
    let sha = lines.next()?.trim();
    (is_hex(sha, 40) || is_hex(sha, 64)).then(|| TreeRecord { sha: sha.to_string(), overlay: lines.filter(|l| !l.is_empty()).map(str::to_string).collect() })
}

/// Pure: the contents of the record file.
pub fn render_tree_record(record: &TreeRecord) -> String {
    std::iter::once(record.sha.as_str()).chain(record.overlay.iter().map(String::as_str)).map(|line| format!("{}\n", line)).collect()
}

/// Pure: the name representing the repo -- the first 16 hex digits of the sha256 of the absolute path of the git common dir (the note above).
pub fn repo_key(common_dir: &Path) -> String {
    use sha2::{Digest, Sha256};
    let digest = Sha256::digest(common_dir.as_os_str().as_encoded_bytes());
    digest.iter().take(8).map(|b| format!("{:02x}", b)).collect()
}

/// Pure: whether it is the name of a tree per sha in the previous form (`<40 digits>`, `<40 digits>-<16 digits>`, and leftover write-outs `.partial-…`).
pub fn is_sha_tree_name(name: &str) -> bool {
    name.starts_with(".partial-")
        || match name.split_once('-') {
            None => is_hex(name, 40),
            Some((sha, suffix)) => is_hex(sha, 40) && is_hex(suffix, 16),
        }
}

/// The list of dirs of the location home (symlinks excluded).
fn home_dirs(home: &Path) -> Vec<(String, PathBuf)> {
    let Ok(entries) = std::fs::read_dir(home) else { return Vec::new() };
    entries
        .flatten()
        .filter(|entry| std::fs::symlink_metadata(entry.path()).is_ok_and(|meta| meta.is_dir()))
        .map(|entry| (entry.file_name().to_string_lossy().into_owned(), entry.path()))
        .collect()
}

/// Deletes the trees per sha in the previous form in the location home and returns the number deleted (the note above).
pub fn retire_sha_trees(home: &Path) -> usize {
    home_dirs(home).into_iter().filter(|(name, path)| is_sha_tree_name(name) && std::fs::remove_dir_all(path).is_ok()).count()
}

/// Deletes, in the location home, the repo dirs whose recorded git common dir no longer exists, and returns the number deleted (the note above) -- keep (the current repo), dirs with no record, and
/// dirs whose lock cannot be taken are not touched.
pub fn retire_vanished_repos(home: &Path, keep: &str) -> usize {
    home_dirs(home)
        .into_iter()
        .filter(|(name, path)| {
            name != keep
                && is_hex(name, 16)
                && std::fs::read_to_string(path.join(REPO_RECORD)).is_ok_and(|recorded| !Path::new(recorded.trim()).exists())
                && acquire_lock(&path.join(TREE_LOCK), Duration::ZERO).is_ok_and(|lock| lock.is_some())
                && std::fs::remove_dir_all(path).is_ok()
        })
        .count()
}

/// Opens the lock file and takes the exclusive lock, waiting up to wait. None if it cannot be taken.
fn acquire_lock(path: &Path, wait: Duration) -> Result<Option<std::fs::File>, String> {
    let file = std::fs::OpenOptions::new()
        .create(true)
        .truncate(false)
        .write(true)
        .open(path)
        .map_err(|e| format!("cannot open {}: {}", path.display(), e))?;
    let deadline = Instant::now() + wait;
    loop {
        match file.try_lock() {
            Ok(()) => return Ok(Some(file)),
            Err(std::fs::TryLockError::WouldBlock) if Instant::now() < deadline => std::thread::sleep(Duration::from_millis(50)),
            Err(std::fs::TryLockError::WouldBlock) => return Ok(None),
            Err(std::fs::TryLockError::Error(error)) => return Err(format!("cannot take the lock on {}: {}", path.display(), error)),
        }
    }
}

/// Prepares the HEAD tree at one fixed path per repo (`<name representing the repo>/tree` in the location home) and returns its root (the note above). If the tree exists, only the files that changed from the record's
/// sha to HEAD are rewritten; if it is absent or crashed, everything is written out. If another run at the same time does not release the tree until wait,
/// returns a temporary tree written out under scratch. None if there is no HEAD (the first commit).
pub fn head_tree(root: &Path, overlay: &[String], home: &Path, scratch: &Path, wait: Duration) -> Result<Option<HeadTree>, String> {
    let Ok(head) = git(root, &["rev-parse", "--verify", "-q", "HEAD^{commit}"]) else { return Ok(None) };
    let head = String::from_utf8_lossy(&head).trim().to_string();
    let common = git(root, &["rev-parse", "--path-format=absolute", "--git-common-dir"])?;
    let common = PathBuf::from(String::from_utf8_lossy(&common).trim());
    let common = common.canonicalize().unwrap_or(common);
    std::fs::create_dir_all(home).map_err(|e| format!("cannot create {}: {}", home.display(), e))?;
    let home = home.canonicalize().map_err(|e| format!("cannot read {}: {}", home.display(), e))?;
    retire_sha_trees(&home);
    // The tree is the contents of the root's dir, so even for the same repo a different root position gives a different tree (the position goes into the name -- for a working tree root, the same name as before).
    let prefix = root_prefix(root)?;
    let key = if prefix.is_empty() { repo_key(&common) } else { repo_key(&common.join(prefix.trim_end_matches('/'))) };
    retire_vanished_repos(&home, &key);
    let repo_dir = home.join(&key);
    std::fs::create_dir_all(&repo_dir).map_err(|e| format!("cannot create {}: {}", repo_dir.display(), e))?;
    let Some(lock) = acquire_lock(&repo_dir.join(TREE_LOCK), wait)? else {
        let path = scratch.join("head-tree");
        std::fs::create_dir_all(&path).map_err(|e| format!("cannot create {}: {}", path.display(), e))?;
        write_whole_tree(root, &head, overlay, &path)?;
        return Ok(Some(HeadTree { path, update: TreeUpdate::Temporary, answers: None, _lock: None }));
    };
    let recorded = common.to_string_lossy();
    if std::fs::read_to_string(repo_dir.join(REPO_RECORD)).ok().as_deref().map(str::trim) != Some(recorded.as_ref()) {
        std::fs::write(repo_dir.join(REPO_RECORD), format!("{}\n", recorded)).map_err(|e| format!("cannot write {}: {}", REPO_RECORD, e))?;
    }
    let tree = repo_dir.join(TREE_DIR);
    let record_path = repo_dir.join(TREE_RECORD);
    let record = std::fs::read_to_string(&record_path).ok().and_then(|text| parse_tree_record(&text)).filter(|_| tree.is_dir());
    let update = match record {
        Some(record) => match advance_tree(root, &record, &head, overlay, &tree, &record_path) {
            Ok(update) => update,
            // A tree whose diff cannot be taken or that failed midway through a rewrite is written out again in full (the record is gone).
            Err(_) => rebuild_tree(root, &head, overlay, &tree, &record_path)?,
        },
        None => rebuild_tree(root, &head, overlay, &tree, &record_path)?,
    };
    Ok(Some(HeadTree { path: tree, update, answers: Some(repo_dir.join(ANSWERS_DIR)), _lock: Some(lock) }))
}

/// Deletes the tree, writes out all of HEAD again, and places the record.
fn rebuild_tree(root: &Path, head: &str, overlay: &[String], tree: &Path, record_path: &Path) -> Result<TreeUpdate, String> {
    remove_record(record_path)?;
    if std::fs::symlink_metadata(tree).is_ok() {
        std::fs::remove_dir_all(tree).map_err(|e| format!("cannot delete {}: {}", tree.display(), e))?;
    }
    std::fs::create_dir_all(tree).map_err(|e| format!("cannot create {}: {}", tree.display(), e))?;
    write_whole_tree(root, head, overlay, tree)?;
    write_record(record_path, &TreeRecord { sha: head.to_string(), overlay: overlay.to_vec() })?;
    Ok(TreeUpdate::Rebuilt)
}

/// Writes all of HEAD into the existing dir tree, and overlays the declaration files from the tip.
fn write_whole_tree(root: &Path, head: &str, overlay: &[String], tree: &Path) -> Result<(), String> {
    extract(root, head, &[], tree)?;
    lay_overlay(root, overlay, tree)
}

/// Rewrites to HEAD's contents only the paths that changed from the record's sha to HEAD, plus the declaration files overlaid before and to be overlaid now (deleted paths are deleted).
/// The record is removed during the rewrite. If too many paths changed, Err (the caller writes everything out again).
fn advance_tree(root: &Path, record: &TreeRecord, head: &str, overlay: &[String], tree: &Path, record_path: &Path) -> Result<TreeUpdate, String> {
    // Read relative to the root (the tree is the contents of the root's dir -- when git archive is fired from the root dir, it writes out that dir's contents relative to the root).
    let changed = git(root, &["diff-tree", "-r", "-z", "--no-renames", "--name-only", "--relative", &record.sha, head])?;
    let refresh: BTreeSet<String> = split_z(&changed).into_iter().chain(record.overlay.iter().cloned()).chain(overlay.iter().cloned()).collect();
    if refresh.is_empty() {
        return Ok(TreeUpdate::Unchanged);
    }
    if refresh.len() > REWRITE_LIMIT {
        return Err(format!("{} paths changed (limit {})", refresh.len(), REWRITE_LIMIT));
    }
    let refresh: Vec<String> = refresh.into_iter().collect();
    let present: Vec<String> = present_paths(root, head, &refresh)?.into_iter().collect::<BTreeSet<String>>().into_iter().collect();
    remove_record(record_path)?;
    for rel in &refresh {
        remove_path(tree, rel)?;
    }
    if !present.is_empty() {
        extract(root, head, &present, tree)?;
    }
    lay_overlay(root, overlay, tree)?;
    write_record(record_path, &TreeRecord { sha: head.to_string(), overlay: overlay.to_vec() })?;
    Ok(TreeUpdate::Advanced { rewritten: present.len(), removed: refresh.len().saturating_sub(present.len()) })
}

/// Turns NUL-separated git output into a list of paths.
fn split_z(out: &[u8]) -> Vec<String> {
    out.split(|b| *b == 0).filter(|n| !n.is_empty()).map(|n| String::from_utf8_lossy(n).into_owned()).collect()
}

/// Among paths, those that exist in commit sha (git ls-tree).
fn present_paths(root: &Path, sha: &str, paths: &[String]) -> Result<Vec<String>, String> {
    let mut present = Vec::new();
    for chunk in paths.chunks(ARCHIVE_CHUNK) {
        let args: Vec<&str> = ["--literal-pathspecs", "ls-tree", "-r", "-z", "--name-only", sha, "--"].into_iter().chain(chunk.iter().map(String::as_str)).collect();
        present.extend(split_z(&git(root, &args)?));
    }
    Ok(present)
}

/// Deletes a path in the tree (the whole thing if a dir), and deletes emptied parent dirs up to just before the tree's root.
fn remove_path(tree: &Path, rel: &str) -> Result<(), String> {
    let path = tree.join(rel);
    let removed = match std::fs::symlink_metadata(&path) {
        Ok(meta) if meta.is_dir() => std::fs::remove_dir_all(&path),
        Ok(_) => std::fs::remove_file(&path),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(error) => Err(error),
    };
    removed.map_err(|e| format!("cannot delete {}: {}", path.display(), e))?;
    let mut parent = path.parent();
    while let Some(dir) = parent.filter(|dir| *dir != tree && dir.starts_with(tree)) {
        if std::fs::remove_dir(dir).is_err() {
            break;
        }
        parent = dir.parent();
    }
    Ok(())
}

/// Writes the paths of commit sha (all if empty) into the existing dir dest (`git archive --format=tar <sha> -- <paths> | tar -x -m -C dest` --
/// the files' mtime is the time of writing).
fn extract(root: &Path, sha: &str, paths: &[String], dest: &Path) -> Result<(), String> {
    let whole: [Vec<String>; 1] = [Vec::new()];
    let chunks: Vec<&[String]> = if paths.is_empty() { whole.iter().map(Vec::as_slice).collect() } else { paths.chunks(ARCHIVE_CHUNK).collect() };
    for chunk in chunks {
        let mut archive = Command::new("git")
            .args(["--literal-pathspecs", "archive", "--format=tar", sha, "--"])
            .args(chunk)
            .current_dir(root)
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .spawn()
            .map_err(|e| format!("cannot start git archive: {}", e))?;
        let pipe = archive.stdout.take().ok_or("cannot read the output of git archive")?;
        let extracted = Command::new("tar").arg("-x").arg("-m").arg("-C").arg(dest).stdin(pipe).status();
        let archived = archive.wait().map_err(|e| format!("cannot wait for git archive: {}", e))?;
        let extracted = extracted.map_err(|e| format!("cannot start tar: {}", e))?;
        if !archived.success() || !extracted.success() {
            return Err(format!("cannot write out the HEAD tree (git archive {}, tar {})", archived, extracted));
        }
    }
    Ok(())
}

/// Copies the declaration files from the tip to the tree (files not on the tip are not copied).
fn lay_overlay(root: &Path, overlay: &[String], tree: &Path) -> Result<(), String> {
    for rel in overlay {
        let from = root.join(rel);
        if from.is_file() {
            let to = tree.join(rel);
            if let Some(parent) = to.parent() {
                std::fs::create_dir_all(parent).map_err(|e| format!("cannot create {}: {}", parent.display(), e))?;
            }
            std::fs::copy(&from, &to).map_err(|e| format!("cannot copy {} to the HEAD tree: {}", rel, e))?;
        }
    }
    Ok(())
}

/// Removes the record (does nothing if absent).
fn remove_record(path: &Path) -> Result<(), String> {
    match std::fs::remove_file(path) {
        Err(error) if error.kind() != std::io::ErrorKind::NotFound => Err(format!("cannot delete {}: {}", path.display(), error)),
        _ => Ok(()),
    }
}

/// Places the record from a temporary file by rename (so a half-written record is not read).
fn write_record(path: &Path, record: &TreeRecord) -> Result<(), String> {
    let partial = path.with_extension(format!("partial-{}", std::process::id()));
    std::fs::write(&partial, render_tree_record(record)).map_err(|e| format!("cannot write {}: {}", partial.display(), e))?;
    std::fs::rename(&partial, path).map_err(|e| format!("cannot place {}: {}", path.display(), e))
}

/// Fires git at root and returns stdout if it succeeds.
fn git(root: &Path, args: &[&str]) -> Result<Vec<u8>, String> {
    let output = Command::new("git").args(args).current_dir(root).stdin(Stdio::null()).output().map_err(|e| format!("cannot start git: {}", e))?;
    if output.status.success() {
        Ok(output.stdout)
    } else {
        Err(format!("git {} failed: {}", args.join(" "), String::from_utf8_lossy(&output.stderr).trim()))
    }
}

/// The root of the git working tree of the current dir.
pub fn git_toplevel(cwd: &Path) -> Result<PathBuf, String> {
    let out = git(cwd, &["rev-parse", "--show-toplevel"])?;
    Ok(PathBuf::from(String::from_utf8_lossy(&out).trim()))
}

/// The position of the root root from the git working tree's root (`git rev-parse --show-prefix` -- empty if the root is the working tree's root; in the form `pkg/` for a dir below).
/// When the root is a dir under the working tree's root (the form where the config is placed in dotfiles' agentcli/), this is the one place read to align the root of the paths git answers with
/// and the tree's location to the root.
fn root_prefix(root: &Path) -> Result<String, String> {
    let out = git(root, &["rev-parse", "--show-prefix"])?;
    Ok(String::from_utf8_lossy(&out).trim().to_string())
}

/// Staged paths (`git diff --cached --name-only --relative --diff-filter=ACMRD -z` -- relative to the root; paths outside the root are not included).
fn staged_paths(root: &Path) -> Result<Vec<String>, String> {
    let out = git(root, &["diff", "--cached", "--name-only", "--relative", "--diff-filter=ACMRD", "-z"])?;
    Ok(out.split(|b| *b == 0).filter(|n| !n.is_empty()).map(|n| String::from_utf8_lossy(n).into_owned()).collect())
}

/// Paths deleted by the stage (including the old side of a rename -- the D of `--no-renames`). So that the HEAD side of the count of major warnings also counts the HEAD version of moved or deleted
/// files (otherwise a move that does not increase warnings would look like an increase).
fn removed_paths(root: &Path) -> Result<Vec<String>, String> {
    let out = git(root, &["diff", "--cached", "--name-only", "--relative", "--no-renames", "--diff-filter=D", "-z"])?;
    Ok(out.split(|b| *b == 0).filter(|n| !n.is_empty()).map(|n| String::from_utf8_lossy(n).into_owned()).collect())
}

/// The config argument passed to the child (tip = the config file itself; HEAD tree = the same relative file inside the tree, or the tip's file if absent).
fn config_args(options: &CommitHookOptions, tree: Option<&Path>) -> Vec<String> {
    let Some(config) = &options.config else { return Vec::new() };
    let chosen = match (tree, config.strip_prefix(&options.root)) {
        (Some(tree), Ok(rel)) if tree.join(rel).is_file() => tree.join(rel),
        _ => config.clone(),
    };
    vec!["--config".to_string(), chosen.to_string_lossy().into_owned()]
}

/// Builds a request to fire the child (config, rules, remaining arguments, paths). If a tree is given it is the HEAD tree, otherwise the tip tree.
fn lint_args(options: &CommitHookOptions, tree: Option<&Path>, rules: &[String], extra: &[String], paths: &[String]) -> LintRun {
    let args = config_args(options, tree)
        .into_iter()
        .chain(options.world_catalogs.iter().flat_map(|catalog| ["--world-catalog".to_string(), catalog.to_string_lossy().into_owned()]))
        // The tip derives these dirs from its own files; the HEAD tree placed elsewhere cannot (CommitHookOptions::import_roots).
        .chain(tree.into_iter().flat_map(|_| options.import_roots.iter()).flat_map(|dir| ["--import-root".to_string(), dir.to_string_lossy().into_owned()]))
        .chain(["--enable".to_string(), rules.join(",")])
        .chain(extra.iter().cloned())
        .chain(std::iter::once("--".to_string()))
        .chain(paths.iter().cloned())
        .collect();
    LintRun { side: if tree.is_some() { Side::Head } else { Side::Tip }, rules: rules.to_vec(), args }
}

/// The list of things that stop (by kind).
#[derive(Debug, Default)]
struct Blocking {
    staged: Vec<String>,
    fresh_critical: ByHead,
    grown_warnings: Vec<String>,
    /// Among the staged Hy files, those outside the range the linter walks -- only names that they were not measured, and does not stop.
    out_of_scope: Vec<String>,
    /// The errors of the whole-repo comparison of a declaration change that stop (stopping_errors -- every one in a staged path, and elsewhere those HEAD does not have).
    declared: Vec<String>,
    /// The rules whose major warnings over the whole repo grew in the whole-repo comparison of a declaration change.
    grown_whole: Vec<String>,
    /// Lines that name what was measured for a declaration change (the promoted rules, a reused answer) -- they do not stop.
    notes: Vec<String>,
}

/// Pure: the `out_of_scope` of the child linter's report (editor-json) -- among the named Hy files, those outside the range the linter walks (spec section 1
/// "outside the named scope"). Empty if the field is absent or null.
pub fn out_of_scope_of(report: &Value) -> Vec<String> {
    report["out_of_scope"].as_array().into_iter().flatten().filter_map(|v| v.as_str().map(str::to_string)).collect()
}

/// The reason it could not be measured midway (exceeding the limit is exit code 1; a failure is exit code 2).
enum Stop {
    TimedOut(Unmeasured),
    Failed(String),
}

fn measured(result: Measured, what: &str) -> Result<Value, Stop> {
    match result {
        Measured::Report(value) => Ok(value),
        Measured::TimedOut(cut) => Err(Stop::TimedOut(Unmeasured { what: what.trim_end().to_string(), cut })),
        Measured::Failed(reason) => Err(Stop::Failed(format!("could not measure {} with doeff-linter: {}", what, reason))),
    }
}

fn empty_report() -> Value {
    serde_json::json!({ "violations": [] })
}

/// One measurement by the child linter -- one entry written to the report output (--report-out).
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct Measurement {
    /// The measured target (`HEAD` = the HEAD version, `stage` = the staged paths).
    pub target: Side,
    /// The paths passed to the child linter (relative to the root). Empty if the child was not fired (only files not in the HEAD version, the first commit, an empty stage).
    pub paths: Vec<String>,
    /// The violations (still in the shape of the violations of the child linter's editor-json).
    pub violations: Vec<Value>,
    /// The IDs of the rules judged (judged_rules of the child linter's editor-json).
    pub judged_rules: Vec<String>,
}

impl Measurement {
    /// Pure: builds a measurement from the child linter's report (editor-json) (the report's violations are re-pointed -- not copied).
    pub fn of(target: Side, paths: Vec<String>, mut report: Value) -> Measurement {
        let violations = match report.get_mut("violations").map(Value::take) {
            Some(Value::Array(list)) => list,
            _ => Vec::new(),
        };
        let judged_rules = report["judged_rules"].as_array().into_iter().flatten().filter_map(|v| v.as_str().map(str::to_string)).collect();
        Measurement { target, paths, violations, judged_rules }
    }

    /// A measurement where the child was not fired (paths, violations, and judged rules are empty).
    fn none(target: Side) -> Measurement {
        Measurement::of(target, Vec::new(), empty_report())
    }
}

/// Pure: the JSON written to the report output -- the tip's root (the root of the violations' paths), the hook's exit code, and the list of measurements. The measurements are the 2 entries HEAD and stage when judging finished,
/// and empty when cut off by the limit or when it could not be measured (exit codes 1 and 2 -- not judged).
pub fn report_of(root: &Path, assessment: &Assessment) -> Value {
    serde_json::json!({ "root": root.to_string_lossy(), "code": assessment.code, "measurements": assessment.measurements })
}

/// One judgment of the hook -- the exit code, the lines to print (excluding the line head `doeff-linter commit-hook: `), and the measurements (written to the report output).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Assessment {
    pub code: u8,
    pub lines: Vec<String>,
    pub measurements: Vec<Measurement>,
}

/// One judgment that finished -- the things that stop, and the two measurements of the HEAD version and the staged paths.
struct Judged {
    blocking: Blocking,
    measurements: Vec<Measurement>,
}

impl Judged {
    /// One judgment finished without firing the child (the stage is empty, there are no rules to apply, or there are no paths the linter reads).
    fn unmeasured() -> Judged {
        Judged { blocking: Blocking::default(), measurements: vec![Measurement::none(Side::Head), Measurement::none(Side::Tip)] }
    }
}

/// The hook's judgment (does not print). Exit code 0 = pass, 1 = stop (including when cut off by the limit and could not measure -- the no-limit command is attached),
/// 2 = git or the linter did not run.
pub fn assess(options: &CommitHookOptions) -> Assessment {
    match judge(options) {
        Ok(Judged { blocking, measurements }) => {
            let code = u8::from(
                !(blocking.staged.is_empty()
                    && blocking.declared.is_empty()
                    && blocking.fresh_critical.is_empty()
                    && blocking.grown_warnings.is_empty()
                    && blocking.grown_whole.is_empty()),
            );
            let lines = blocking
                .notes
                .iter()
                .cloned()
                .chain(blocking.staged.iter().map(|line| format!("violation in staged file: {}", line)))
                .chain(blocking.declared.iter().map(|line| format!("violation in the repo after the declaration change: {}", line)))
                .chain(blocking.fresh_critical.not_on_head.iter().map(|ident| format!("not on HEAD critical: {}", ident)))
                .chain(blocking.grown_warnings.iter().map(|line| format!("major warnings increased over the HEAD version (count over the set of staged files): {}", line)))
                .chain(blocking.grown_whole.iter().map(|line| format!("major warnings increased over the HEAD version (count over the whole repo, after the declaration change): {}", line)))
                .chain(already_on_head_lines(&blocking))
                .chain(blocking.out_of_scope.iter().map(|path| format!("out of scope (outside the range the linter walks -- the layer rules did not measure this file): {}", path)))
                .collect();
            Assessment { code, lines, measurements }
        }
        Err(Stop::TimedOut(unmeasured)) => Assessment { code: 1, lines: cut_lines(options, &unmeasured), measurements: Vec::new() },
        Err(Stop::Failed(reason)) => Assessment { code: 2, lines: vec![reason], measurements: Vec::new() },
    }
}

/// The hook's body -- prints the lines of the judgment (assess) to stderr, writes the report (report_of) if there is a report output (report_out), and returns the exit code.
/// If the report cannot be written, adds one line to stderr and does not change the exit code (recording diagnostics does not stop a commit).
pub fn run(options: &CommitHookOptions, report_out: Option<&Path>) -> u8 {
    let assessment = assess(options);
    for line in &assessment.lines {
        eprintln!("{}{}", PREFIX, line);
    }
    if let Some(path) = report_out {
        let root = options.root.canonicalize().unwrap_or_else(|_| options.root.clone());
        if let Err(error) = std::fs::write(path, report_of(&root, &assessment).to_string()) {
            eprintln!("{}cannot write report {} (exit code unchanged): {}", PREFIX, path.display(), error);
        }
    }
    assessment.code
}

/// Pure: the lines for hits that stop although the same identifier exists in the HEAD version (existing hits in changed files, for rules with no known list), and if any, one line of guidance on how to
/// fix.
fn already_on_head_lines(blocking: &Blocking) -> Vec<String> {
    let hits: Vec<String> = blocking
        .fresh_critical
        .already_on_head
        .iter()
        .map(|ident| format!("existing hit in a changed file (also on main; this rule is not lowered by the known list): {}", ident))
        .collect();
    let guide = (!hits.is_empty()).then(|| "If you change this file, fix that hit in the same change (do not skip the hook)".to_string());
    hits.into_iter().chain(guide).collect()
}

/// The rules a declaration change compares over the whole repo (promoted -- declaration_items::promoted), those of them whose HEAD side is not measured (newly -- the
/// HEAD config does not enable them), and the lines naming the choice.
#[derive(Debug, Default)]
struct Promotion {
    promoted: Vec<String>,
    newly: Vec<String>,
    notes: Vec<String>,
}

/// Reads the declarations of the HEAD tree and of the tip as the linter reads them and chooses the rules to compare over the whole repo. A declaration that cannot be
/// read on either side counts as a change of where files sit (every rule -- the child linter then names the reading error).
fn promotion_of(options: &CommitHookOptions, tree: &Path) -> Promotion {
    use crate::project::declaration_items::{changes, newly_enabled, promoted, Change, Decl, DeclarationView};
    let head_config = options.config.as_ref().map(|config| match config.strip_prefix(&options.root) {
        Ok(rel) => tree.join(rel),
        Err(_) => config.clone(),
    });
    let head = DeclarationView::load(tree, head_config.as_deref());
    let tip = DeclarationView::load(&options.root, options.config.as_deref());
    let changed = match (&head, &tip) {
        (Ok(head), Ok(tip)) => changes(head, tip),
        _ => [Change::Item(Decl::Population)].into_iter().collect(),
    };
    let head_enabled = effective_rules(head.as_ref().ok().and_then(|view| view.config.as_ref()), &options.cli_enable, &options.cli_disable);
    let promoted = promoted(&options.enabled, &head_enabled, &changed);
    let newly = newly_enabled(&promoted, &head_enabled);
    let note = if promoted.is_empty() {
        "declaration change: no rule reads what changed (comments, order, or prose only) — measuring the staged paths only".to_string()
    } else {
        let named: Vec<String> = changed
            .iter()
            .map(|change| match change {
                Change::Item(item) => format!("{:?}", item),
                Change::Rule(rule) => rule.clone(),
            })
            .collect();
        format!(
            "declaration change: comparing the whole repo for {} rule(s) that read what changed ({}) — {}{}",
            promoted.len(),
            if named.is_empty() { "rules newly enabled".to_string() } else { named.join(", ") },
            promoted.join(","),
            if newly.is_empty() { String::new() } else { format!(" (newly enabled, every hit is new: {})", newly.join(",")) }
        )
    };
    Promotion { promoted, newly, notes: vec![note] }
}

/// Pure: the key of a whole-repo answer -- the measured tree (its git tree id), the linter (its build), the rules, and what the run reads besides the tree (the tip
/// declarations laid on the HEAD tree, the catalogs and dirs outside the root, the config's place). The same key answers the same, so it is not measured again: the HEAD
/// version of the next commit is the tree this commit stages.
pub fn answer_key(tree_id: &str, linter: &str, rules: &[String], overlay: &[(String, String)], outside: &[String]) -> String {
    use sha2::{Digest, Sha256};
    let mut rules: Vec<&String> = rules.iter().collect();
    rules.sort();
    let mut hasher = Sha256::new();
    for part in std::iter::once(tree_id.to_string())
        .chain(std::iter::once(linter.to_string()))
        .chain(rules.iter().map(|r| format!("rule {}", r)))
        .chain(overlay.iter().map(|(path, digest)| format!("overlay {} {}", path, digest)))
        .chain(outside.iter().map(|o| format!("outside {}", o)))
    {
        hasher.update(part.as_bytes());
        hasher.update([0u8]);
    }
    hasher.finalize().iter().map(|b| format!("{:02x}", b)).collect()
}

/// The build of the child linter (`--version`); a hand build (`+dirty`, `unknown`) adds the binary's size and mtime. None = cannot tell (answers are not kept).
fn linter_identity(linter: &Path) -> Option<String> {
    let output = Command::new(linter).arg("--version").stdin(Stdio::null()).output().ok().filter(|o| o.status.success())?;
    let version = String::from_utf8_lossy(&output.stdout).trim().to_string();
    if !version.contains("dirty") && !version.contains("unknown") {
        return Some(version);
    }
    let meta = std::fs::metadata(linter.canonicalize().ok()?).ok()?;
    let mtime = meta.modified().ok()?.duration_since(UNIX_EPOCH).ok()?.as_nanos();
    Some(format!("{} {} {}", version, meta.len(), mtime))
}

/// What the runs read besides the tree (catalogs, dirs outside the root, the config's place) -- part of the answer key.
fn outside_of(options: &CommitHookOptions) -> Vec<String> {
    let rel = options.config.as_ref().map(|c| c.strip_prefix(&options.root).map(|r| r.to_string_lossy().into_owned()).unwrap_or_else(|_| c.to_string_lossy().into_owned()));
    rel.into_iter()
        .map(|r| format!("config {}", r))
        .chain(options.world_catalogs.iter().map(|c| format!("catalog {}", c.display())))
        .chain(options.import_roots.iter().map(|d| format!("import-root {}", d.display())))
        .collect()
}

/// Reads a kept answer (None if absent or unreadable). Its paths are under the HEAD tree's path.
fn read_answer(answers: &Path, key: &str) -> Option<Value> {
    std::fs::read(answers.join(format!("{}.json", key))).ok().and_then(|bytes| serde_json::from_slice(&bytes).ok())
}

/// Keeps an answer (paths under the HEAD tree's path) -- placed by rename, and the oldest beyond ANSWERS_KEPT removed. A failure to keep only loses the reuse.
fn keep_answer(answers: &Path, key: &str, report: &Value) {
    if std::fs::create_dir_all(answers).is_err() {
        return;
    }
    let partial = answers.join(format!("{}.partial-{}", key, std::process::id()));
    if std::fs::write(&partial, report.to_string()).is_err() || std::fs::rename(&partial, answers.join(format!("{}.json", key))).is_err() {
        let _ = std::fs::remove_file(&partial);
        return;
    }
    let Ok(entries) = std::fs::read_dir(answers) else { return };
    let mut kept: Vec<(SystemTime, PathBuf)> = entries
        .flatten()
        .filter(|e| e.path().extension().is_some_and(|x| x == "json"))
        .filter_map(|e| Some((e.metadata().ok()?.modified().ok()?, e.path())))
        .collect();
    kept.sort();
    let surplus = kept.len().saturating_sub(ANSWERS_KEPT);
    for (_, path) in kept.into_iter().take(surplus) {
        let _ = std::fs::remove_file(path);
    }
}

/// The git tree id of the stage when the working tree is exactly the stage (no unstaged change, no untracked file -- the whole-repo run reads the working tree), else None.
fn clean_stage_tree(root: &Path) -> Option<String> {
    let untracked = git(root, &["ls-files", "-z", "--others", "--exclude-standard"]).ok()?;
    let unstaged = git(root, &["diff", "--name-only", "-z"]).ok()?;
    if !untracked.is_empty() || !unstaged.is_empty() {
        return None;
    }
    git(root, &["write-tree"]).ok().map(|out| String::from_utf8_lossy(&out).trim().to_string())
}

/// The whole-repo comparison of a declaration change: the promoted rules on the HEAD tree (without the newly enabled ones) and on the tip, each from a kept answer when
/// there is one. Both reports come back with paths under the tip's root.
struct Whole {
    head: Value,
    tip: Value,
    notes: Vec<String>,
}

fn measure_whole(options: &CommitHookOptions, held: &HeadTree, promotion: &Promotion) -> Result<Whole, Stop> {
    let root = &options.root;
    let tree = held.path.as_path();
    let tree_text = tree.to_string_lossy().into_owned();
    let tip_root = root.canonicalize().unwrap_or_else(|_| root.clone()).to_string_lossy().into_owned();
    let identity = held.answers.as_ref().and_then(|_| linter_identity(&options.linter));
    let outside = outside_of(options);
    let mut notes = Vec::new();
    let head_rules: Vec<String> = promotion.promoted.iter().filter(|r| !promotion.newly.contains(r)).cloned().collect();
    let head = if head_rules.is_empty() {
        empty_report()
    } else {
        let overlay: Vec<(String, String)> = options
            .overlay
            .iter()
            .map(|rel| {
                use sha2::{Digest, Sha256};
                let digest = std::fs::read(root.join(rel)).map(|b| Sha256::digest(&b).iter().map(|x| format!("{:02x}", x)).collect::<String>()).unwrap_or_default();
                (rel.clone(), digest)
            })
            .collect();
        let head_tree_id = git(root, &["rev-parse", "HEAD^{tree}"]).ok().map(|out| String::from_utf8_lossy(&out).trim().to_string());
        let key = match (&held.answers, &identity, head_tree_id) {
            (Some(_), Some(identity), Some(tree_id)) => Some(answer_key(&tree_id, identity, &head_rules, &overlay, &outside)),
            _ => None,
        };
        let kept = held.answers.as_deref().zip(key.as_deref()).and_then(|(answers, key)| read_answer(answers, key));
        let mut report = match kept {
            Some(report) => {
                notes.push(format!("reused the kept whole-repo answer of the HEAD version for {}", head_rules.join(",")));
                report
            }
            None => {
                let report = measured(
                    run_linter(&options.linter, tree, &lint_args(options, Some(tree), &head_rules, &[], &[".".to_string()]), options.timeout),
                    "the HEAD version of the whole repo",
                )?;
                if let (Some(answers), Some(key)) = (&held.answers, &key) {
                    keep_answer(answers, key, &report);
                }
                report
            }
        };
        rebase_paths(&mut report, &tree_text, &tip_root);
        report
    };
    let stage_tree = held.answers.as_ref().and(identity.as_ref()).and_then(|_| clean_stage_tree(root));
    let key = match (&identity, &stage_tree) {
        (Some(identity), Some(tree_id)) => Some(answer_key(tree_id, identity, &promotion.promoted, &[], &outside)),
        _ => None,
    };
    let kept = held.answers.as_deref().zip(key.as_deref()).and_then(|(answers, key)| read_answer(answers, key));
    let tip = match kept {
        Some(mut report) => {
            notes.push(format!("reused the kept whole-repo answer of the staged tree for {}", promotion.promoted.join(",")));
            rebase_paths(&mut report, &tree_text, &tip_root);
            report
        }
        None => {
            let report = measured(
                run_linter(&options.linter, root, &lint_args(options, None, &promotion.promoted, &[], &[".".to_string()]), options.timeout),
                "the whole repo after the declaration change",
            )?;
            if let (Some(answers), Some(key)) = (&held.answers, &key) {
                let mut stored = report.clone();
                rebase_paths(&mut stored, &tip_root, &tree_text);
                keep_answer(answers, key, &stored);
            }
            report
        }
    };
    Ok(Whole { head, tip, notes })
}

/// Pure: the identifiers of the criticals that stop in the whole-repo comparison -- those not in the HEAD version (baseline::new_criticals, a move counted as the same
/// hit), and, in a staged path, every critical of a rule with no known list (the same as the staged run's new_critical -- baseline::blocks_regardless_of_baseline).
pub fn whole_criticals(head: &Value, tip: &Value, staged: &[String]) -> Result<Vec<String>, String> {
    let fresh = crate::baseline::new_criticals(&crate::baseline::baseline_identities(head)?, &crate::baseline::baseline_identities(tip)?);
    let unlisted: Vec<Value> = violations(tip)
        .filter(|v| {
            let level = v["level"].as_str().and_then(crate::project::rule::RuleLevel::parse);
            level.is_some_and(|level| crate::baseline::blocks_regardless_of_baseline(v["rule"].as_str().unwrap_or(""), level))
                && staged.iter().any(|p| p == relative_to_root(tip, v["path"].as_str().unwrap_or("")))
        })
        .cloned()
        .collect();
    let unlisted = crate::baseline::baseline_identities(&serde_json::json!({ "violations": unlisted }))?;
    Ok(fresh.into_iter().chain(unlisted).collect::<BTreeSet<String>>().into_iter().collect())
}

/// Pure: one measurement made of two runs of the same tree with disjoint rules (the staged paths and the whole repo) -- paths, violations, and judged rules joined.
fn joined(target: Side, paths: Vec<String>, first: Value, second: Option<(Vec<String>, Value)>) -> Measurement {
    let mut measurement = Measurement::of(target, paths, first);
    if let Some((paths, report)) = second {
        let other = Measurement::of(target, paths, report);
        measurement.paths.extend(other.paths);
        measurement.violations.extend(other.violations);
        for rule in other.judged_rules {
            if !measurement.judged_rules.contains(&rule) {
                measurement.judged_rules.push(rule);
            }
        }
    }
    measurement
}

fn judge(options: &CommitHookOptions) -> Result<Judged, Stop> {
    let root = &options.root;
    let staged = staged_paths(root).map_err(Stop::Failed)?;
    if staged.is_empty() {
        return Ok(Judged::unmeasured());
    }
    // Rules decided from one file on the staged paths. A declaration change adds the whole-repo comparison of the rules that read what changed (promotion_of) --
    // the merge gate compares every rule over the whole repo for it.
    let declaration_changed = touches_declaration(&staged, &options.declarations);
    let quick_all = split_rules(&options.enabled).quick;
    let paths = linted_paths(&staged, |p| root.join(p).exists());
    if !declaration_changed && (quick_all.is_empty() || paths.is_empty()) {
        return Ok(Judged::unmeasured());
    }
    let timeout = options.timeout;
    let mut blocking = Blocking::default();
    let scratch = Scratch::create().map_err(Stop::Failed)?;
    // The HEAD tree is placed one per repo at commit-hook-tree/<name representing the repo>/tree under the fact cache's root and advanced by diff (head_tree, #3858).
    // The tree's lock is held until this function exits (until the child linter finishes measuring the tree). A run that does not use the cache (DOEFF_LINTER_NO_CACHE)
    // uses this run's temporary dir as the location (deleted at the end). The tree for the one-time directive (Lint-Baseline) measures the HEAD tree with the current declarations -- existing hits
    // become present in the baseline, and only new hits stop (the overlaid declaration files are restored to HEAD's contents when next advancing).
    let home = match &options.cache {
        Some(base) => {
            let home = base.join(TREE_HOME_DIR);
            crate::project::facts_cache::mark_used(&home);
            home
        }
        None => scratch.path.clone(),
    };
    let held = head_tree(root, &options.overlay, &home, &scratch.path, LOCK_WAIT).map_err(Stop::Failed)?;
    let tree = held.as_ref().map(|h| h.path.as_path());
    if tree.is_some() && !options.overlay.is_empty() {
        eprintln!("{}{} — measuring the HEAD tree with the current declarations ({}) (one time only)", PREFIX, BASELINE_TRAILER, options.overlay.join(", "));
    }
    let tip_root = root.canonicalize().unwrap_or_else(|_| root.clone());
    let promotion = match (declaration_changed, tree) {
        (true, Some(t)) => promotion_of(options, t),
        _ => Promotion::default(),
    };
    blocking.notes = promotion.notes.clone();
    let quick: Vec<String> = quick_all.into_iter().filter(|r| !promotion.promoted.contains(r)).collect();

    // Fire the HEAD version (only paths that exist in HEAD) with the same rules, and re-point it to the tip's root paths to make the baseline. The HEAD version of files deleted by the stage (including the old
    // side of a rename) is also fired -- so that the count of major warnings is compared including moved and deleted files.
    let (head_paths, base, tip, paths) = if quick.is_empty() || paths.is_empty() {
        (Vec::new(), empty_report(), empty_report(), Vec::new())
    } else {
        let removed = removed_paths(root).map_err(Stop::Failed)?;
        let head_paths: Vec<String> = tree
            .map(|t| {
                paths
                    .iter()
                    .chain(linted_paths(&removed, |p| t.join(p).is_file()).iter())
                    .filter(|p| t.join(p).is_file())
                    .cloned()
                    .collect::<BTreeSet<String>>()
                    .into_iter()
                    .collect()
            })
            .unwrap_or_default();
        let base = match tree {
            Some(t) if !head_paths.is_empty() => {
                let mut report = measured(run_linter(&options.linter, t, &lint_args(options, Some(t), &quick, &[], &head_paths), timeout), "the HEAD version")?;
                rebase_paths(&mut report, &t.to_string_lossy(), &tip_root.to_string_lossy());
                report
            }
            _ => empty_report(),
        };
        let baseline = scratch.path.join("baseline.json");
        std::fs::write(&baseline, base.to_string()).map_err(|e| Stop::Failed(format!("cannot write {}: {}", baseline.display(), e)))?;
        let extra = vec!["--baseline-report".to_string(), baseline.to_string_lossy().into_owned()];
        let tip = measured(run_linter(&options.linter, root, &lint_args(options, None, &quick, &extra, &paths), timeout), "the staged files")?;
        blocking.staged = stopping_errors(&tip, &base, &staged);
        // Read the identifiers of the HEAD version with the same spelling the child linter uses to read the baseline file (baseline::baseline_identities), and split the hits that stop.
        let head_criticals = crate::baseline::baseline_identities(&base).map_err(|reason| Stop::Failed(format!("cannot read the report of the HEAD version: {}", reason)))?;
        blocking.fresh_critical =
            split_by_head(&head_criticals, tip["new_critical"].as_array().into_iter().flatten().filter_map(|v| v.as_str().map(str::to_string)));
        blocking.grown_warnings = grown_major_warnings(&base, &tip);
        blocking.out_of_scope = out_of_scope_of(&tip);
        (head_paths, base, tip, paths)
    };

    // The rules a declaration change promoted, over the whole repo on both trees (measure_whole -- a kept answer is read instead of measuring again).
    let whole = match (&held, promotion.promoted.is_empty()) {
        (Some(held), false) => Some(measure_whole(options, held, &promotion)?),
        _ => None,
    };
    if let Some(whole) = &whole {
        blocking.notes.extend(whole.notes.iter().cloned());
        blocking.declared = stopping_errors(&whole.tip, &whole.head, &staged);
        let head_criticals =
            crate::baseline::baseline_identities(&whole.head).map_err(|reason| Stop::Failed(format!("cannot read the report of the HEAD version: {}", reason)))?;
        let fresh = whole_criticals(&whole.head, &whole.tip, &staged).map_err(|reason| Stop::Failed(format!("cannot read the whole-repo reports: {}", reason)))?;
        let split = split_by_head(&head_criticals, fresh);
        blocking.fresh_critical.not_on_head.extend(split.not_on_head);
        blocking.fresh_critical.already_on_head.extend(split.already_on_head);
        blocking.grown_whole = grown_major_warnings(&whole.head, &whole.tip);
    }
    let second = |head: bool| whole.as_ref().map(|w| (vec![".".to_string()], if head { w.head.clone() } else { w.tip.clone() }));
    let measurements = vec![joined(Side::Head, head_paths, base, second(true)), joined(Side::Tip, paths, tip, second(false))];
    Ok(Judged { blocking, measurements })
}


#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn ids(list: &[&str]) -> Vec<String> {
        list.iter().map(|s| s.to_string()).collect()
    }

    #[test]
    fn commit_hook_split_rules_drops_semantic_and_separates_whole_repo_rules() {
        let enabled = ids(&["DOEFF016", "DOEFF163", "DOEFF201", "DOEFF110", "DOEFF149", "DOEFF205", "doeff166", "DOEFF999"]);
        let split = split_rules(&enabled);
        // Unknown IDs (DOEFF999) are on the single-file side -- they are not in the list of enabled rules, and DOEFF100 reports them.
        assert_eq!(split.quick, ids(&["DOEFF016", "DOEFF110", "DOEFF999"]));
        // The whole-repo comparison is the list the rules declare -- DOEFF149, which was not in the hand-written list, is also included. DOEFF166 in the list applies
        // the rule that a line names in the same run (#2033).
        assert_eq!(split.whole, ids(&["DOEFF163", "DOEFF149", "doeff166"]));
    }

    /// Whether it is a Jev rule is decided by the rule's declaration (ProjectRule::is_semantic), not by the ID prefix -- deterministic rules whose prefix is DOEFF2
    /// (DOEFF206-209) go into quick or whole, and only the Jev rules (DOEFF201-205) go into neither.
    #[test]
    fn commit_hook_split_rules_keeps_deterministic_rules_whose_id_starts_with_doeff2() {
        let enabled = ids(&["DOEFF201", "DOEFF202", "DOEFF203", "DOEFF204", "DOEFF205", "DOEFF206", "DOEFF207", "DOEFF208", "DOEFF209"]);
        let split = split_rules(&enabled);
        assert_eq!(split.quick, ids(&["DOEFF208"]));
        assert_eq!(split.whole, ids(&["DOEFF206", "DOEFF207", "DOEFF209"]));
    }

    #[test]
    fn commit_hook_a_declaration_change_moves_every_rule_to_the_whole_repo() {
        let declarations = Declarations {
            files: ids(&["pyproject.toml", "architecture.hy"]),
            dirs: ids(&["scripts/doeff_lint/REG"]),
            switches: ids(&["pyproject.toml", "architecture.hy"]),
        };
        let enabled = ids(&["DOEFF016", "DOEFF102", "DOEFF201", "DOEFF149"]);
        // A change that does not touch declarations keeps the split by the rules' declarations.
        let plain = split_for_change(&enabled, &ids(&["app/core/x.hy"]), &declarations);
        assert_eq!(plain, RuleSplit { quick: ids(&["DOEFF016", "DOEFF102"]), whole: ids(&["DOEFF149"]) });
        // A change that changed a declaration file or a file under a registry dir also moves rules decided by a single file to whole (semantic rules stay excluded; the order is that of enable).
        for changed in [ids(&["architecture.hy"]), ids(&["pyproject.toml", "app/core/x.hy"]), ids(&["scripts/doeff_lint/REG/k.txt"])] {
            let moved = split_for_change(&enabled, &changed, &declarations);
            assert_eq!(moved, RuleSplit { quick: Vec::new(), whole: ids(&["DOEFF016", "DOEFF102", "DOEFF149"]) }, "{:?}", changed);
        }
        // A name prefix match alone does not count as touching (a different dir that has a dir's name at its head).
        assert!(!touches_declaration(&ids(&["scripts/doeff_lint/REGISTRY-OTHER/k.txt", "architecture.hy.bak"]), &declarations));
    }

    #[test]
    fn commit_hook_baseline_overlay_needs_a_reasoned_trailer() {
        let declarations = Declarations { files: Vec::new(), dirs: Vec::new(), switches: ids(&["pyproject.toml", "architecture.hy"]) };
        // Only lines with a reason take effect (anywhere in the body is fine; leading whitespace on the line is read).
        let reasoned = "start firing a rule\n\nLint-Baseline: declarations — start firing DOEFF170-172 (#2143)\nCo-Authored-By: x\n";
        assert_eq!(baseline_overlay(reasoned, &declarations), ids(&["pyproject.toml", "architecture.hy"]));
        // Failure cases: a line without a reason, a mention mid-line, and a different trailer do not take effect (the baseline stays at HEAD's declarations -- do not create the miss of #2127).
        for message in ["x\n\nLint-Baseline: declarations\n", "x\n\nLint-Baseline: declarations — \n", "The body mentions Lint-Baseline: declarations\n", "Registry-Grows: 1 reason\n"] {
            assert!(baseline_overlay(message, &declarations).is_empty(), "{:?}", message);
        }
    }

    #[test]
    fn commit_hook_names_a_retired_hand_list() {
        let kept = CommitHookSection { whole_repo_rules: ids(&["DOEFF163"]), timeout_s: None };
        assert!(retired_whole_repo_rules_note(Some(&kept)).is_some_and(|line| line.contains("retired")));
        assert_eq!(retired_whole_repo_rules_note(Some(&CommitHookSection::default())), None);
        assert_eq!(retired_whole_repo_rules_note(None), None);
    }

    #[test]
    fn commit_hook_linted_paths_keep_existing_source_files() {
        let staged = ids(&["a.hy", "b.py", "gone.hy", "registry/keys.txt", "c.hyk", "d.hyp"]);
        assert_eq!(linted_paths(&staged, |p| p != "gone.hy"), ids(&["a.hy", "b.py", "c.hyk", "d.hyp"]));
    }

    fn v(key: &str, severity: &str, level: &str) -> Value {
        json!({ "key": key, "severity": severity, "level": level, "path": "/r/a.hy", "rule": "DOEFF1", "message": "m",
                "range": { "start": { "line": 2, "character": 0 } } })
    }

    #[test]
    fn commit_hook_blocking_ignores_warnings_and_criticals() {
        let report = json!({ "root": "/r", "violations": [
            v("k-error", "error", "major"),
            v("k-warning", "warning", "major"),
            v("k-critical", "error", "critical"),
            v("k-info", "info", "info"),
        ]});
        let picked: Vec<&str> = blocking_violations(&report).iter().map(|v| v["key"].as_str().unwrap()).collect();
        assert_eq!(picked, vec!["k-error"]);
        assert_eq!(violation_line(&report, blocking_violations(&report)[0]), "a.hy:3: DOEFF1 m");
    }

    fn w(rule: &str, path: &str, severity: &str, level: &str) -> Value {
        json!({ "rule": rule, "path": path, "severity": severity, "level": level, "message": "m" })
    }

    /// Major warnings are compared with the HEAD version by per-rule count -- one more is named; the same count and a decreased count are not listed;
    /// just moving to another file (move or split) has the same count so is not listed. Critical, error, minor, and info are not counted (another stage or out of scope).
    #[test]
    fn commit_hook_grown_major_warnings_counts_per_rule() {
        let head = json!({ "violations": [
            w("DOEFF172", "/h/a.hy", "warning", "major"),
            w("DOEFF113", "/h/old.hy", "warning", "major"),
            w("DOEFF113", "/h/old.hy", "warning", "major"),
        ]});
        // Increased: DOEFF172 from 1 to 2 (same file). The new rule DOEFF105 from 0 to 1. DOEFF113 moved both from old.hy to new.hy (same count).
        let grown = json!({ "violations": [
            w("DOEFF172", "/r/a.hy", "warning", "major"),
            w("DOEFF172", "/r/a.hy", "warning", "major"),
            w("DOEFF105", "/r/b.hy", "warning", "major"),
            w("DOEFF113", "/r/new.hy", "warning", "major"),
            w("DOEFF113", "/r/new.hy", "warning", "major"),
        ]});
        assert_eq!(grown_major_warnings(&head, &grown), ids(&["DOEFF105 0 → 1", "DOEFF172 1 → 2"]));
        // The same count and a decreased count do not stop (after a commit that decreased them, that HEAD becomes the baseline).
        assert!(grown_major_warnings(&head, &head).is_empty());
        let fewer = json!({ "violations": [w("DOEFF113", "/r/new.hy", "warning", "major")] });
        assert!(grown_major_warnings(&head, &fewer).is_empty());
        // Things that are not major warnings are not counted: critical (the new_critical stage), error (the blocking_violations stage), minor, and info.
        let others = json!({ "violations": [
            w("DOEFF172", "/r/a.hy", "warning", "major"),
            w("DOEFF999", "/r/a.hy", "warning", "critical"),
            w("DOEFF998", "/r/a.hy", "error", "major"),
            w("DOEFF997", "/r/a.hy", "warning", "minor"),
            w("DOEFF996", "/r/a.hy", "info", "info"),
        ]});
        assert!(grown_major_warnings(&head, &others).is_empty());
        // If there is no HEAD version (only new files), the baseline is 0.
        assert_eq!(grown_major_warnings(&json!({ "violations": [] }), &fewer), ids(&["DOEFF113 0 → 1"]));
    }

    /// The report's measurement carries over the child linter's editor-json violations as they are, with the IDs of the judged rules and the list of paths passed added.
    /// The report is the root, exit code, and list of measurements, and the measured target is spelled `HEAD` or `stage`. A measurement where the child was not fired (a report with no field) has empty violations and rules.
    #[test]
    fn commit_hook_report_carries_each_measurement_in_the_editor_json_shape() {
        let hit = v("k-error", "error", "major");
        let tip = json!({ "root": "/r", "violations": [hit.clone()], "judged_rules": ["DOEFF016", "NOQA001"], "new_critical": [] });
        let stage = Measurement::of(Side::Tip, ids(&["a.hy"]), tip);
        assert_eq!(stage.violations, vec![hit.clone()]);
        assert_eq!(stage.judged_rules, ids(&["DOEFF016", "NOQA001"]));
        let head = Measurement::none(Side::Head);
        assert!(head.paths.is_empty() && head.violations.is_empty() && head.judged_rules.is_empty(), "{:?}", head);

        let assessment = Assessment { code: 1, lines: ids(&["x"]), measurements: vec![head, stage] };
        assert_eq!(
            report_of(Path::new("/r"), &assessment),
            json!({ "root": "/r", "code": 1, "measurements": [
                { "target": "HEAD", "paths": [], "violations": [], "judged_rules": [] },
                { "target": "stage", "paths": ["a.hy"], "violations": [hit], "judged_rules": ["DOEFF016", "NOQA001"] },
            ]})
        );
        // A run that was cut off or could not be measured has empty measurements (declaring that the exit code did not judge).
        let cut = Assessment { code: 1, lines: Vec::new(), measurements: Vec::new() };
        assert_eq!(report_of(Path::new("/r"), &cut)["measurements"], json!([]));
    }

    #[test]
    fn commit_hook_rebase_paths_moves_only_paths_under_the_tree() {
        let mut report = json!({ "root": "/tmp/t/tree", "violations": [
            { "path": "/tmp/t/tree/a.hy" }, { "path": "/tmp/t/treeish/b.hy" }, { "path": "/elsewhere/c.hy" }
        ]});
        rebase_paths(&mut report, "/tmp/t/tree", "/repo");
        assert_eq!(report["root"], "/repo");
        assert_eq!(report["violations"][0]["path"], "/repo/a.hy");
        assert_eq!(report["violations"][1]["path"], "/tmp/t/treeish/b.hy");
        assert_eq!(report["violations"][2]["path"], "/elsewhere/c.hy");
    }

    #[test]
    fn commit_hook_unmeasured_line_names_the_comparison_tree_rules_and_limit() {
        let cut = Cut { side: Side::Head, rules: ids(&["DOEFF163", "DOEFF167"]), limit: Duration::from_secs(20), measure: Measure::Cpu };
        let line = unmeasured_line(&Unmeasured { what: "the HEAD version".to_string(), cut });
        for part in ["could not measure the HEAD version", "2 rule(s) (DOEFF163,DOEFF167) applied on the HEAD tree", "cut off at the CPU-seconds limit of 20 s", "so the commit is stopped"] {
            assert!(line.contains(part), "{:?} is missing: {}", part, line);
        }
        let tip = Cut { side: Side::Tip, rules: ids(&["DOEFF016"]), limit: Duration::from_secs(1), measure: Measure::Wall };
        assert!(unmeasured_line(&Unmeasured { what: "the staged files".to_string(), cut: tip })
            .contains("1 rule(s) (DOEFF016) applied on the tip tree (working tree) was cut off at the wall limit of 1 s (child CPU seconds cannot be read on this machine)"));
        let idle = Cut { side: Side::Tip, rules: ids(&["DOEFF016"]), limit: Duration::from_secs(2), measure: Measure::WallBackstop };
        assert!(unmeasured_line(&Unmeasured { what: "the staged files".to_string(), cut: idle })
            .contains("cut off at the wall backstop limit of 8 s (4 times the CPU-seconds limit of 2 s"));
    }

    /// If CPU seconds can be read, measure by CPU seconds -- a child that only stretched in wall time due to load (wall 9 seconds, CPU 3 seconds, limit 4 seconds) is not cut off, and it is cut off once CPU seconds
    /// reach the limit. Wall looks only at the backstop limit (4 times the limit). Machines where it cannot be read measure by wall as before.
    #[test]
    fn commit_hook_cut_measure_reads_cpu_seconds_and_keeps_a_wall_backstop() {
        let s = Duration::from_secs;
        assert_eq!(cut_measure(s(9), Some(s(3)), s(4)), None);
        assert_eq!(cut_measure(s(5), Some(s(4)), s(4)), Some(Measure::Cpu));
        assert_eq!(cut_measure(s(15), Some(s(1)), s(4)), None);
        assert_eq!(cut_measure(s(16), Some(s(1)), s(4)), Some(Measure::WallBackstop));
        assert_eq!(cut_measure(s(3), None, s(4)), None);
        assert_eq!(cut_measure(s(4), None, s(4)), Some(Measure::Wall));
    }

    /// Reads the sum of utime, stime, cutime, and cstime of /proc/<pid>/stat (the 14th to 17th; 1/100 second). Not confused by ')' and spaces in the name field.
    #[test]
    fn commit_hook_cpu_of_stat_sums_own_and_waited_children_ticks() {
        let stat = "4242 (doeff (lin) ter) R 1 4242 4242 0 -1 4194304 100 0 0 0 250 30 15 5 20 0 8 0 12345 1000 200";
        assert_eq!(cpu_of_stat(stat), Some(Duration::from_millis(3000)));
        assert_eq!(cpu_of_stat("4242 (x) R 1"), None);
        assert_eq!(cpu_of_stat("no paren"), None);
    }

    #[test]
    fn commit_hook_timeout_prefers_cli_then_config_then_default() {
        let section = CommitHookSection { whole_repo_rules: Vec::new(), timeout_s: Some(7) };
        assert_eq!(resolve_timeout(Some(&section), Some(3)), Some(Duration::from_secs(3)));
        assert_eq!(resolve_timeout(Some(&section), None), Some(Duration::from_secs(7)));
        assert_eq!(resolve_timeout(None, None), Some(Duration::from_secs(DEFAULT_TIMEOUT_S)));
        // 0 seconds means no limit (whether by argument or config).
        assert_eq!(resolve_timeout(Some(&section), Some(0)), None);
        assert_eq!(resolve_timeout(Some(&CommitHookSection { whole_repo_rules: Vec::new(), timeout_s: Some(0) }), None), None);
    }

    /// The key of a whole-repo answer changes with the tree, the linter build, the rule set, the declarations laid on the tree, and what is read outside it, and not with
    /// the order the rules are named in.
    #[test]
    fn answer_key_names_everything_the_answer_depends_on() {
        let rules = vec!["DOEFF102".to_string(), "DOEFF101".to_string()];
        let key = answer_key("t1", "0.1 (doeff abc)", &rules, &[], &[]);
        assert_eq!(key, answer_key("t1", "0.1 (doeff abc)", &["DOEFF101".to_string(), "DOEFF102".to_string()], &[], &[]));
        assert_ne!(key, answer_key("t2", "0.1 (doeff abc)", &rules, &[], &[]));
        assert_ne!(key, answer_key("t1", "0.1 (doeff abd)", &rules, &[], &[]));
        assert_ne!(key, answer_key("t1", "0.1 (doeff abc)", &rules[..1], &[], &[]));
        assert_ne!(key, answer_key("t1", "0.1 (doeff abc)", &rules, &[("architecture.hy".to_string(), "d".to_string())], &[]));
        assert_ne!(key, answer_key("t1", "0.1 (doeff abc)", &rules, &[], &["catalog /x".to_string()]));
    }

    /// Failure case: the whole-repo comparison stops on a critical HEAD does not have (also when it sits in an untouched file), not on one HEAD has in an untouched file,
    /// and, in a staged path, on every critical of a rule with no known list (DOEFF208 — as the staged run does).
    #[test]
    fn whole_criticals_are_the_new_ones_and_the_unlisted_ones_in_staged_paths() {
        let hit = |rel: &str, rule: &str| json!({"rule": rule, "level": "critical", "severity": "error", "path": format!("/r/{}", rel), "key": format!("{}::{}::x", rel, rule), "message": "m"});
        let head = json!({"root": "/r", "violations": [hit("a.hy", "DOEFF102"), hit("w.hy", "DOEFF208")]});
        let tip = json!({"root": "/r", "violations": [hit("a.hy", "DOEFF102"), hit("b.hy", "DOEFF102"), hit("w.hy", "DOEFF208")]});
        assert_eq!(whole_criticals(&head, &tip, &[]).unwrap(), vec!["b.hy::DOEFF102::x".to_string()]);
        assert_eq!(whole_criticals(&head, &tip, &["w.hy".to_string()]).unwrap(), vec!["b.hy::DOEFF102::x".to_string(), "w.hy::DOEFF208::x".to_string()]);
    }

    fn error_at(root: &str, rel: &str, line: u64, rule: &str) -> Value {
        serde_json::json!({"rule": rule, "severity": "error", "level": "major", "path": format!("{}/{}", root, rel), "range": {"start": {"line": line}}, "message": "m"})
    }

    /// An error outside the staged paths stops only when the HEAD version does not have it on the same line; one in a staged path stops either way.
    #[test]
    fn commit_hook_errors_already_on_head_stop_only_in_staged_paths() {
        let head = serde_json::json!({"root": "/r", "violations": [error_at("/r", "old.py", 3, "DOEFF004"), error_at("/r", "touched.py", 1, "DOEFF004")]});
        let tip = serde_json::json!({"root": "/r", "violations": [
            error_at("/r", "old.py", 3, "DOEFF004"),
            error_at("/r", "touched.py", 1, "DOEFF004"),
            error_at("/r", "old.py", 9, "DOEFF004"),
            error_at("/r", "untouched.py", 1, "DOEFF119"),
        ]});
        assert_eq!(
            stopping_errors(&tip, &head, &ids(&["touched.py", "architecture.hy"])),
            vec!["touched.py:2: DOEFF004 m".to_string(), "old.py:10: DOEFF004 m".to_string(), "untouched.py:2: DOEFF119 m".to_string()]
        );
        assert_eq!(stopping_errors(&tip, &tip, &[]), Vec::<String>::new());
    }

    /// The no-limit command is one line made by adding no limit to the same linter, root, config, --enable / --disable, and commit body file,
    /// written so that paths containing spaces or ' can be pasted into a shell as is. If there is no config, --no-config.
    #[test]
    fn commit_hook_unbounded_command_repeats_the_same_hook_without_a_limit() {
        let mut options = CommitHookOptions {
            root: PathBuf::from("/repo"),
            config: Some(PathBuf::from("/repo/pyproject.toml")),
            enabled: Vec::new(),
            declarations: Declarations::default(),
            overlay: Vec::new(),
            message: None,
            cli_enable: Vec::new(),
            cli_disable: Vec::new(),
            timeout: Some(Duration::from_secs(20)),
            linter: PathBuf::from("/bin/doeff-linter"),
            cache: None,
            world_catalogs: Vec::new(),
            import_roots: Vec::new(),
        };
        assert_eq!(unbounded_command(&options), "/bin/doeff-linter --commit-hook --root /repo --config /repo/pyproject.toml --commit-hook-timeout-s 0");
        options.root = PathBuf::from("/my repo/it's");
        options.config = None;
        options.cli_enable = ids(&["DOEFF163", "DOEFF016"]);
        options.cli_disable = ids(&["DOEFF110"]);
        options.message = Some(PathBuf::from("/my repo/it's/.git/COMMIT_EDITMSG"));
        assert_eq!(
            unbounded_command(&options),
            "/bin/doeff-linter --commit-hook --root '/my repo/it'\\''s' --no-config --enable DOEFF163,DOEFF016 --disable DOEFF110 \
             --commit-message '/my repo/it'\\''s/.git/COMMIT_EDITMSG' --commit-hook-timeout-s 0"
        );
    }

    #[test]
    fn commit_hook_tree_record_round_trips_and_refuses_a_broken_record() {
        let record = TreeRecord { sha: "a".repeat(40), overlay: ids(&["architecture.hy", "pyproject.toml"]) };
        assert_eq!(parse_tree_record(&render_tree_record(&record)), Some(record));
        let plain = TreeRecord { sha: "b".repeat(64), overlay: Vec::new() };
        assert_eq!(parse_tree_record(&render_tree_record(&plain)), Some(plain));
        for broken in ["", "\n", "abc\n", &format!("{}\n", "g".repeat(40))] {
            assert_eq!(parse_tree_record(broken), None, "{:?}", broken);
        }
    }

    #[test]
    fn commit_hook_names_only_the_old_sha_trees_and_keys_repos_by_the_common_dir() {
        let sha = "0".repeat(40);
        for old in [sha.clone(), format!("{}-{}", sha, "f".repeat(16)), ".partial-x".to_string()] {
            assert!(is_sha_tree_name(&old), "{}", old);
        }
        for other in ["notes", "0123456789abcdef", &format!("{}-x", sha), &"0".repeat(39)] {
            assert!(!is_sha_tree_name(other), "{}", other);
        }
        let key = repo_key(Path::new("/repos/a/.git"));
        assert!(is_hex(&key, 16), "{}", key);
        assert_eq!(key, repo_key(Path::new("/repos/a/.git")));
        assert_ne!(key, repo_key(Path::new("/repos/b/.git")));
    }
}
