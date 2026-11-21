//! doeff-design-gate — the merge-time gate of DOEFF212 (a type changes only with its design evidence), DOEFF213 (values that stand in for a type), and DOEFF214 (the
//! ledgers only shrink). Called by the repo-charter gate before a merge (`exec doeff-design-gate --base {base} --paths {paths}`) and by the commit hook.
//!
//! Why a separate binary: the gate has 5 seconds, and doeff-linter builds the project's facts (minutes on a cold tree) even for one file. This binary reads only the
//! changed paths, their sibling evidence, and the declaration, ledgers, and approvals at the base — never the facts.
//!
//! Exit code: 0 = pass, 1 = refuse (the reasons on stdout), 2 = cannot judge (the reason on stderr — the gate holds the change; nothing passes unjudged).
#![forbid(unsafe_code)]

mod declaration;
mod git;
mod judge;

use std::path::PathBuf;
use std::process::ExitCode;

use judge::Hit;

/// The outcome of one run.
enum Verdict {
    Pass,
    Reject(Vec<Hit>),
    Unjudgeable(String),
}

impl From<Verdict> for ExitCode {
    fn from(verdict: Verdict) -> ExitCode {
        match verdict {
            Verdict::Pass => ExitCode::SUCCESS,
            Verdict::Reject(hits) => {
                for hit in &hits {
                    println!("{}: {} {} — {}", hit.rel, hit.rule, hit.detail, hit.reason);
                }
                println!("doeff-design-gate: {} hit(s) — the change is refused", hits.len());
                ExitCode::from(1)
            }
            Verdict::Unjudgeable(reason) => {
                eprintln!("doeff-design-gate: cannot judge — {}", reason);
                ExitCode::from(2)
            }
        }
    }
}

/// The arguments: `--base <commit>` (required), `--repo <dir>` (default the current dir), `--paths <path>…` (the rest).
struct Args {
    base: String,
    repo: PathBuf,
    paths: Vec<String>,
}

fn args(given: impl Iterator<Item = String>) -> Result<Args, String> {
    let mut base = None;
    let mut repo = PathBuf::from(".");
    let mut paths = Vec::new();
    let mut given = given.peekable();
    while let Some(word) = given.next() {
        match word.as_str() {
            "--base" => base = Some(given.next().ok_or("--base needs a commit")?),
            "--repo" => repo = PathBuf::from(given.next().ok_or("--repo needs a dir")?),
            "--paths" => paths.extend(given.by_ref()),
            other => return Err(format!("unknown argument {} (usage: doeff-design-gate --base <commit> [--repo <dir>] --paths <path>…)", other)),
        }
    }
    Ok(Args { base: base.ok_or("--base is required")?, repo, paths })
}

fn run(args: Args) -> Verdict {
    let base = match git::BaseTree::open(&args.repo, &args.base) {
        Ok(base) => base,
        Err(reason) => return Verdict::Unjudgeable(reason),
    };
    let declaration = match declaration::at_base(&base) {
        Ok(declaration) => declaration,
        Err(reason) => return Verdict::Unjudgeable(reason),
    };
    match judge::judge(&base, &declaration, &args.paths) {
        Ok(hits) if hits.is_empty() => Verdict::Pass,
        Ok(hits) => Verdict::Reject(hits),
        Err(reason) => Verdict::Unjudgeable(reason),
    }
}

fn main() -> ExitCode {
    match args(std::env::args().skip(1)) {
        Ok(args) => run(args).into(),
        Err(reason) => Verdict::Unjudgeable(reason).into(),
    }
}
