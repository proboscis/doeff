//! `doeff-complexity`: Hy cyclomatic complexity counter and merge/commit ratchet gate.
//!
//! Subcommands:
//! - `count [--repo <dir>] [--rev <rev>] [--out <file>]`
//!   Measures all production `.hy` files and writes compact aggregate JSON (`summary` + per-module
//!   aggregates) to `--out` or stdout.
//! - `gate --base <commit> --counts <file> [--repo <dir>] --paths [path...]`
//!   Merge-time charter gate mode: reads thresholds from `<base>:repo-charter.toml`, reads base
//!   aggregates from `--counts`, measures changed working-tree `.hy` files, and reads base blobs
//!   only when needed for exact type/function comparison.
//! - `gate --staged [--base <commit>] [--repo <dir>] [--paths [path...]]`
//!   Commit-hook mode: compares `<base>` (default `HEAD`) and the git index (`:0:<path>`) via one
//!   `git cat-file --batch`, without requiring a `--counts` file.
//!
//! Exit codes:
//! - `0`: pass (or `count` succeeded)
//! - `1`: rejected (violations printed on stdout)
//! - `2`: cannot judge / unreadable input (reason printed on stderr)
#![forbid(unsafe_code)]

pub mod charter;
pub mod counts;
pub mod git;
pub mod judge;
pub mod measure;

use std::collections::{BTreeMap, BTreeSet};
use std::path::{Path, PathBuf};
use std::process::ExitCode;

use charter::{parse_gate_thresholds, parse_optional_thresholds, OptionalThresholds};
use counts::{CountsBuilder, CountsFile};
use git::{FileRead, GitRepo};
use judge::{judge_changes, Violation};
use measure::{is_prod_hy, measure_source, normalize_path, FileMetrics};

const CHARTER_FILE: &str = "repo-charter.toml";

/// Outcome of one CLI invocation.
pub enum CliOutcome {
    Pass,
    Reject(Vec<Violation>),
    Unjudgeable(String),
}

impl From<CliOutcome> for ExitCode {
    fn from(outcome: CliOutcome) -> Self {
        match outcome {
            CliOutcome::Pass => ExitCode::SUCCESS,
            CliOutcome::Reject(violations) => {
                for v in &violations {
                    println!("{}", v);
                }
                println!(
                    "doeff-complexity: {} violation(s) — the change is refused",
                    violations.len()
                );
                ExitCode::from(1)
            }
            CliOutcome::Unjudgeable(reason) => {
                eprintln!("doeff-complexity: cannot judge — {}", reason);
                ExitCode::from(2)
            }
        }
    }
}

enum Command {
    Count(CountArgs),
    Gate(GateArgs),
    Help,
}

struct CountArgs {
    repo: PathBuf,
    rev: Option<String>,
    out: Option<PathBuf>,
}

struct GateArgs {
    repo: PathBuf,
    base: Option<String>,
    counts: Option<PathBuf>,
    staged: bool,
    paths: Vec<String>,
    has_paths_flag: bool,
}

fn usage() -> &'static str {
    "usage:\n  \
     doeff-complexity count [--repo <dir>] [--rev <rev>] [--out <path>]\n  \
     doeff-complexity gate --base <commit> --counts <path> [--repo <dir>] --paths [path...]\n  \
     doeff-complexity gate --staged [--base <commit>] [--repo <dir>] [--paths [path...]]"
}

fn parse_args(mut given: impl Iterator<Item = String>) -> Result<Command, String> {
    let Some(sub) = given.next() else {
        return Err(format!("subcommand required (count or gate)\n{}", usage()));
    };
    match sub.as_str() {
        "-h" | "--help" | "help" => Ok(Command::Help),
        "count" => parse_count_args(given).map(Command::Count),
        "gate" => parse_gate_args(given).map(Command::Gate),
        other => Err(format!("unknown subcommand {:?}\n{}", other, usage())),
    }
}

fn parse_count_args(mut given: impl Iterator<Item = String>) -> Result<CountArgs, String> {
    let mut repo = PathBuf::from(".");
    let mut rev = None;
    let mut out = None;
    while let Some(word) = given.next() {
        match word.as_str() {
            "--repo" => repo = PathBuf::from(given.next().ok_or("--repo needs a directory")?),
            "--rev" => rev = Some(given.next().ok_or("--rev needs a git revision")?),
            "--out" => out = Some(PathBuf::from(given.next().ok_or("--out needs a path")?)),
            other => return Err(format!("unknown argument {:?} for count\n{}", other, usage())),
        }
    }
    Ok(CountArgs { repo, rev, out })
}

fn parse_gate_args(mut given: impl Iterator<Item = String>) -> Result<GateArgs, String> {
    let mut repo = PathBuf::from(".");
    let mut base = None;
    let mut counts = None;
    let mut staged = false;
    let mut paths = Vec::new();
    let mut has_paths_flag = false;
    while let Some(word) = given.next() {
        match word.as_str() {
            "--repo" => repo = PathBuf::from(given.next().ok_or("--repo needs a directory")?),
            "--base" => base = Some(given.next().ok_or("--base needs a commit")?),
            "--counts" => {
                counts = Some(PathBuf::from(given.next().ok_or("--counts needs a path")?))
            }
            "--staged" => staged = true,
            "--paths" => {
                has_paths_flag = true;
                paths.extend(given.by_ref());
            }
            other => return Err(format!("unknown argument {:?} for gate\n{}", other, usage())),
        }
    }
    if !staged && base.is_none() {
        return Err("--base is required unless --staged is set".to_string());
    }
    if !staged && counts.is_none() {
        return Err("--counts <path> or --staged is required".to_string());
    }
    Ok(GateArgs { repo, base, counts, staged, paths, has_paths_flag })
}

fn read_charter_optional(git: &GitRepo, rev: Option<&str>) -> Result<OptionalThresholds, String> {
    let read = match rev {
        Some(commit) => git.read_blob(&format!("{}:{}", commit, CHARTER_FILE)),
        None => git.read_worktree(CHARTER_FILE),
    };
    match read {
        FileRead::Present(text) => parse_optional_thresholds(&text),
        FileRead::Absent => Ok(OptionalThresholds::default()),
        FileRead::Unreadable(reason) => Err(format!("cannot read {}: {}", CHARTER_FILE, reason)),
    }
}

fn run_count(args: CountArgs) -> CliOutcome {
    let git = GitRepo::new(args.repo);
    let resolved_rev = match &args.rev {
        Some(rev) => match git.resolve_commit(rev) {
            Ok(sha) => Some(sha),
            Err(reason) => return CliOutcome::Unjudgeable(reason),
        },
        None => None,
    };

    let thresholds = match read_charter_optional(&git, resolved_rev.as_deref()) {
        Ok(t) => t,
        Err(reason) => return CliOutcome::Unjudgeable(reason),
    };

    let paths = match &resolved_rev {
        Some(commit) => git.list_tree_prod_hy(commit),
        None => git.list_tracked_prod_hy(),
    };
    let paths = match paths {
        Ok(p) => p,
        Err(reason) => return CliOutcome::Unjudgeable(reason),
    };

    let mut builder = CountsBuilder::new(thresholds);
    for path in paths {
        let read = match &resolved_rev {
            Some(commit) => git.read_blob(&format!("{}:{}", commit, path)),
            None => git.read_worktree(&path),
        };
        let src = match read {
            FileRead::Present(text) => text,
            FileRead::Absent => {
                return CliOutcome::Unjudgeable(format!(
                    "{}: tracked file is missing from working tree",
                    path
                ));
            }
            FileRead::Unreadable(reason) => return CliOutcome::Unjudgeable(reason),
        };
        let metrics = match measure_source(&path, &src) {
            Ok(m) => m,
            Err(reason) => return CliOutcome::Unjudgeable(reason),
        };
        builder.add(path, &metrics);
    }

    let counts = builder.finish();
    let json = match counts.to_json_string() {
        Ok(s) => s,
        Err(reason) => return CliOutcome::Unjudgeable(reason),
    };

    match args.out {
        Some(out_path) => {
            if let Some(parent) = out_path.parent() {
                if !parent.as_os_str().is_empty() {
                    if let Err(err) = std::fs::create_dir_all(parent) {
                        return CliOutcome::Unjudgeable(format!(
                            "cannot create {}: {}",
                            parent.display(),
                            err
                        ));
                    }
                }
            }
            if let Err(err) = write_atomic(&out_path, json.as_bytes()) {
                return CliOutcome::Unjudgeable(format!(
                    "cannot write {}: {}",
                    out_path.display(),
                    err
                ));
            }
        }
        None => {
            print!("{}", json);
        }
    }
    CliOutcome::Pass
}

fn write_atomic(path: &Path, bytes: &[u8]) -> std::io::Result<()> {
    let tmp_name = format!(
        ".{}.{}.tmp",
        path.file_name().and_then(|s| s.to_str()).unwrap_or("counts"),
        std::process::id()
    );
    let tmp = path.with_file_name(tmp_name);
    std::fs::write(&tmp, bytes)?;
    if let Err(err) = std::fs::rename(&tmp, path) {
        let _ = std::fs::remove_file(&tmp);
        return Err(err);
    }
    Ok(())
}

fn read_base_thresholds(git: &GitRepo, base_commit: &str) -> Result<charter::Thresholds, String> {
    match git.read_blob(&format!("{}:{}", base_commit, CHARTER_FILE)) {
        FileRead::Present(text) => parse_gate_thresholds(&text),
        FileRead::Absent => Err(format!("{} does not exist at base {}", CHARTER_FILE, base_commit)),
        FileRead::Unreadable(reason) => {
            Err(format!("cannot read {} at base {}: {}", CHARTER_FILE, base_commit, reason))
        }
    }
}

fn run_gate_counts(args: GateArgs) -> CliOutcome {
    let git = GitRepo::new(args.repo);
    let base_ref = args.base.as_deref().unwrap_or("HEAD");
    let base_commit = match git.resolve_commit(base_ref) {
        Ok(sha) => sha,
        Err(reason) => return CliOutcome::Unjudgeable(reason),
    };
    let thresholds = match read_base_thresholds(&git, &base_commit) {
        Ok(t) => t,
        Err(reason) => return CliOutcome::Unjudgeable(reason),
    };
    let counts_path = match &args.counts {
        Some(p) => p,
        None => return CliOutcome::Unjudgeable("--counts is required".to_string()),
    };
    let counts = match CountsFile::read_file(counts_path) {
        Ok(c) => c,
        Err(reason) => return CliOutcome::Unjudgeable(reason),
    };

    // Normalize and filter passed paths to production .hy files.
    let mut prod_paths: Vec<String> = args
        .paths
        .iter()
        .map(|p| normalize_path(p))
        .filter(|p| is_prod_hy(p))
        .collect();
    prod_paths.sort();
    prod_paths.dedup();

    if prod_paths.is_empty() {
        return CliOutcome::Pass;
    }

    // Measure post-change working tree content for every changed prod .hy path.
    let mut after_files: BTreeMap<String, FileMetrics> = BTreeMap::new();
    let mut has_missing_on_disk = false;
    let mut has_new_not_in_counts = false;
    for path in &prod_paths {
        if !counts.modules.contains_key(path) {
            has_new_not_in_counts = true;
        }
        match git.read_worktree(path) {
            FileRead::Present(src) => match measure_source(path, &src) {
                Ok(m) => {
                    after_files.insert(path.clone(), m);
                }
                Err(reason) => return CliOutcome::Unjudgeable(reason),
            },
            FileRead::Absent => {
                has_missing_on_disk = true;
            }
            FileRead::Unreadable(reason) => return CliOutcome::Unjudgeable(reason),
        }
    }

    // Detect git renames (-M) only when at least one path is absent on disk or absent from counts.
    let mut rename_map: BTreeMap<String, String> = BTreeMap::new();
    if has_new_not_in_counts || has_missing_on_disk {
        match git.renames_vs_worktree(&base_commit, &prod_paths) {
            Ok(found) => rename_map = found,
            Err(reason) => return CliOutcome::Unjudgeable(reason),
        }
        // If a new file was passed without its old path, check all *.hy renames once.
        let any_unmapped_new = after_files
            .keys()
            .any(|p| !counts.modules.contains_key(p) && !rename_map.contains_key(p));
        if any_unmapped_new {
            match git.renames_vs_worktree(&base_commit, &[]) {
                Ok(all_renames) => {
                    for p in after_files.keys() {
                        if let Some(old) = all_renames.get(p) {
                            rename_map.insert(p.clone(), old.clone());
                        }
                    }
                }
                Err(reason) => return CliOutcome::Unjudgeable(reason),
            }
        }
    }

    // Base module WMC map directly from counts (no git blob reads needed for module checks).
    let mut base_module_wmcs: BTreeMap<String, usize> = BTreeMap::new();
    for path in after_files.keys() {
        let base_path = rename_map.get(path).unwrap_or(path);
        if let Some(agg) = counts.modules.get(base_path) {
            base_module_wmcs.insert(base_path.clone(), agg.wmc);
        }
    }

    // Decide whether any changed file needs detailed base type/fn comparison.
    let any_over_fn_or_type = after_files
        .values()
        .any(|m| m.fn_max() > thresholds.max_fn_cc || m.type_max() >= thresholds.max_type_wmc);

    let mut base_files: BTreeMap<String, FileMetrics> = BTreeMap::new();
    if any_over_fn_or_type {
        // Load base blobs ONLY for:
        // (a) the base_path of any after file that has fn_max > F or type_max >= T, AND
        // (b) any changed path (or rename old path) whose counts entry had fn_max > F or type_max >= T
        //     (the only files that could possibly supply a deleted fn > F or deleted type >= T).
        let mut to_load: BTreeSet<String> = BTreeSet::new();
        for (path, after) in &after_files {
            if after.fn_max() > thresholds.max_fn_cc || after.type_max() >= thresholds.max_type_wmc
            {
                let base_path = rename_map.get(path).unwrap_or(path);
                if counts.modules.contains_key(base_path) {
                    to_load.insert(base_path.clone());
                }
            }
        }
        for path in prod_paths.iter().chain(rename_map.values()) {
            if let Some(agg) = counts.modules.get(path) {
                if agg.fn_max > thresholds.max_fn_cc || agg.type_max >= thresholds.max_type_wmc {
                    to_load.insert(path.clone());
                }
            }
        }

        for base_path in to_load {
            match git.read_blob(&format!("{}:{}", base_commit, base_path)) {
                FileRead::Present(src) => match measure_source(&base_path, &src) {
                    Ok(m) => {
                        base_files.insert(base_path, m);
                    }
                    Err(reason) => return CliOutcome::Unjudgeable(reason),
                },
                FileRead::Absent => {}
                FileRead::Unreadable(reason) => return CliOutcome::Unjudgeable(reason),
            }
        }
    }

    let violations =
        judge_changes(thresholds, &base_module_wmcs, &base_files, &after_files, &rename_map);
    if violations.is_empty() {
        CliOutcome::Pass
    } else {
        CliOutcome::Reject(violations)
    }
}

fn run_gate_staged(args: GateArgs) -> CliOutcome {
    let git = GitRepo::new(args.repo);
    let base_ref = args.base.as_deref().unwrap_or("HEAD");
    let base_commit = match git.resolve_commit(base_ref) {
        Ok(sha) => sha,
        Err(reason) => return CliOutcome::Unjudgeable(reason),
    };
    let thresholds = match read_base_thresholds(&git, &base_commit) {
        Ok(t) => t,
        Err(reason) => return CliOutcome::Unjudgeable(reason),
    };

    let (staged_paths, rename_map) = match git.staged_changes(&base_commit) {
        Ok(res) => res,
        Err(reason) => return CliOutcome::Unjudgeable(reason),
    };

    let target_paths: Vec<String> = if args.has_paths_flag {
        let mut requested: BTreeSet<String> = args
            .paths
            .iter()
            .map(|p| normalize_path(p))
            .filter(|p| is_prod_hy(p))
            .collect();
        // Include rename sources for any requested rename destination.
        let extra_old: Vec<String> = requested
            .iter()
            .filter_map(|p| rename_map.get(p).cloned())
            .collect();
        requested.extend(extra_old);
        requested.into_iter().collect()
    } else {
        staged_paths
    };

    if target_paths.is_empty() {
        return CliOutcome::Pass;
    }

    let mut base_files: BTreeMap<String, FileMetrics> = BTreeMap::new();
    let mut base_module_wmcs: BTreeMap<String, usize> = BTreeMap::new();
    let mut after_files: BTreeMap<String, FileMetrics> = BTreeMap::new();

    for path in &target_paths {
        // Read HEAD / base version via cat-file --batch.
        match git.read_blob(&format!("{}:{}", base_commit, path)) {
            FileRead::Present(src) => match measure_source(path, &src) {
                Ok(m) => {
                    base_module_wmcs.insert(path.clone(), m.module_wmc);
                    base_files.insert(path.clone(), m);
                }
                Err(reason) => return CliOutcome::Unjudgeable(reason),
            },
            FileRead::Absent => {}
            FileRead::Unreadable(reason) => return CliOutcome::Unjudgeable(reason),
        }

        // Read staged index version (`:0:<path>`) via the same cat-file --batch.
        match git.read_blob(&format!(":0:{}", path)) {
            FileRead::Present(src) => match measure_source(path, &src) {
                Ok(m) => {
                    after_files.insert(path.clone(), m);
                }
                Err(reason) => return CliOutcome::Unjudgeable(reason),
            },
            FileRead::Absent => {}
            FileRead::Unreadable(reason) => return CliOutcome::Unjudgeable(reason),
        }
    }

    let violations =
        judge_changes(thresholds, &base_module_wmcs, &base_files, &after_files, &rename_map);
    if violations.is_empty() {
        CliOutcome::Pass
    } else {
        CliOutcome::Reject(violations)
    }
}

/// Shared CLI entry point called by both `doeff-complexity` and `doeff-linter complexity`.
pub fn run_cli(args: impl Iterator<Item = String>) -> ExitCode {
    let outcome = match parse_args(args) {
        Ok(Command::Help) => {
            println!("{}", usage());
            CliOutcome::Pass
        }
        Ok(Command::Count(c)) => run_count(c),
        Ok(Command::Gate(g)) if g.staged => run_gate_staged(g),
        Ok(Command::Gate(g)) => run_gate_counts(g),
        Err(reason) => CliOutcome::Unjudgeable(reason),
    };
    outcome.into()
}
