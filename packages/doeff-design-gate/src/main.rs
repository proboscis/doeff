//! doeff-design-gate — the merge-time gate of DOEFF212 (a type changes only with its design evidence), DOEFF213 (values that stand in for a type), DOEFF214 (the
//! ledgers only shrink), DOEFF215 (a type the change made is at most 5 fields wide), and DOEFF216 (a field an existing type gains is used by its own module). Called by the repo-charter gate before a merge (`exec doeff-design-gate --base {base} --paths {paths}`) and by the commit hook.
//!
//! Why a separate binary: the gate has 5 seconds, and doeff-linter builds the project's facts (minutes on a cold tree) even for one file. This binary reads only the
//! changed paths, their sibling evidence, and the declaration, ledgers, and approvals at the base — never the facts.
//!
//! Exit code: 0 = pass, 1 = refuse (the reasons on stdout), 2 = cannot judge (the reason on stderr — the gate holds the change; nothing passes unjudged).
//!
//! `--format json` writes the verdict, the hits, and the facts of the changed types (width, gained fields, fields the defining module never touches, LCOM4 before and
//! after) as one JSON object on stdout, with the same exit code; `--classify-trigger` adds whether the change needs the Tier 2 SRP review and why
//! (`srp_review_required`, `triggers`). DOEFF217 (LCOM4 rose) refuses by default; `--disable DOEFF217` keeps it to the facts (the way back without a pin change).
#![forbid(unsafe_code)]

mod declaration;
mod facts;
mod git;
mod judge;

use std::path::PathBuf;
use std::process::ExitCode;

use facts::{Fact, Trigger};
use judge::Hit;
use serde::Serialize;

/// The outcome of one run.
enum Verdict {
    Pass,
    Reject(Vec<Hit>),
    Unjudgeable(String),
}

/// How the outcome is written.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Format {
    /// One line per hit on stdout, the reason it cannot judge on stderr.
    Text,
    /// One JSON object on stdout.
    Json,
    /// One JSON object on stdout with whether the Tier 2 review is required.
    JsonClassified,
}

/// Whether DOEFF217 refuses a change or is only a fact.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Lcom4 {
    Refuses,
    FactOnly,
}

/// The choices of a run besides what it judges.
struct Report {
    format: Format,
    lcom4: Lcom4,
}

#[derive(Serialize)]
struct JsonHit<'a> {
    path: &'a str,
    rule: &'a str,
    detail: &'a str,
    reason: &'a str,
}

#[derive(Serialize)]
struct JsonOutcome<'a> {
    verdict: &'static str,
    #[serde(skip_serializing_if = "Option::is_none")]
    reason: Option<&'a str>,
    hits: Vec<JsonHit<'a>>,
    facts: &'a [Fact],
    #[serde(skip_serializing_if = "Option::is_none")]
    srp_review_required: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    triggers: Option<Vec<Trigger>>,
}

fn exit_code(verdict: &Verdict) -> ExitCode {
    match verdict {
        Verdict::Pass => ExitCode::SUCCESS,
        Verdict::Reject(_) => ExitCode::from(1),
        Verdict::Unjudgeable(_) => ExitCode::from(2),
    }
}

fn write_text(verdict: &Verdict) {
    match verdict {
        Verdict::Pass => {}
        Verdict::Reject(hits) => {
            for hit in hits {
                println!("{}: {} {} — {}", hit.rel, hit.rule, hit.detail, hit.reason);
            }
            println!("doeff-design-gate: {} hit(s) — the change is refused", hits.len());
        }
        Verdict::Unjudgeable(reason) => eprintln!("doeff-design-gate: cannot judge — {}", reason),
    }
}

/// The JSON outcome. A change that cannot be judged needs the review (fail closed).
fn write_json(verdict: &Verdict, facts: &[Fact], classified: bool) {
    let (name, reason, hits): (&str, Option<&str>, &[Hit]) = match verdict {
        Verdict::Pass => ("pass", None, &[]),
        Verdict::Reject(hits) => ("reject", None, hits),
        Verdict::Unjudgeable(reason) => ("unjudgeable", Some(reason.as_str()), &[]),
    };
    let triggers = classified.then(|| facts::triggers(facts));
    let outcome = JsonOutcome {
        verdict: name,
        reason,
        hits: hits.iter().map(|h| JsonHit { path: &h.rel, rule: h.rule, detail: &h.detail, reason: &h.reason }).collect(),
        facts,
        srp_review_required: triggers.as_ref().map(|t| !t.is_empty() || matches!(verdict, Verdict::Unjudgeable(_))),
        triggers,
    };
    match serde_json::to_string(&outcome) {
        Ok(text) => println!("{}", text),
        Err(error) => eprintln!("doeff-design-gate: cannot write the JSON outcome — {}", error),
    }
}

const USAGE: &str = "usage: doeff-design-gate --base <commit> [--repo <dir>] [--format text|json] [--classify-trigger] [--disable DOEFF217] --paths <path>…";

/// The arguments: `--base <commit>` (required), `--repo <dir>` (default the current dir), `--paths <path>…` (the rest).
struct Args {
    base: String,
    repo: PathBuf,
    paths: Vec<String>,
}

fn args(given: impl Iterator<Item = String>) -> Result<(Args, Report), String> {
    let mut base = None;
    let mut repo = PathBuf::from(".");
    let mut paths = Vec::new();
    let (mut json, mut classify, mut lcom4) = (false, false, Lcom4::Refuses);
    let mut given = given.peekable();
    while let Some(word) = given.next() {
        match word.as_str() {
            "--base" => base = Some(given.next().ok_or("--base needs a commit")?),
            "--repo" => repo = PathBuf::from(given.next().ok_or("--repo needs a dir")?),
            "--format" => match given.next().as_deref() {
                Some("text") => json = false,
                Some("json") => json = true,
                other => return Err(format!("--format takes text or json, not {:?} ({})", other, USAGE)),
            },
            "--classify-trigger" => classify = true,
            "--disable" => match given.next().as_deref() {
                Some("DOEFF217") => lcom4 = Lcom4::FactOnly,
                other => return Err(format!("--disable takes DOEFF217, not {:?} ({})", other, USAGE)),
            },
            "--paths" => paths.extend(given.by_ref()),
            other => return Err(format!("unknown argument {} ({})", other, USAGE)),
        }
    }
    let format = match (json, classify) {
        (false, false) => Format::Text,
        (true, false) => Format::Json,
        (true, true) => Format::JsonClassified,
        (false, true) => return Err(format!("--classify-trigger needs --format json ({})", USAGE)),
    };
    Ok((Args { base: base.ok_or("--base is required")?, repo, paths }, Report { format, lcom4 }))
}

fn run(args: Args, lcom4: Lcom4) -> (Verdict, Vec<Fact>) {
    let base = match git::BaseTree::open(&args.repo, &args.base) {
        Ok(base) => base,
        Err(reason) => return (Verdict::Unjudgeable(reason), Vec::new()),
    };
    let declaration = match declaration::at_base(&base) {
        Ok(declaration) => declaration,
        Err(reason) => return (Verdict::Unjudgeable(reason), Vec::new()),
    };
    let facts = match facts::facts(&base, &declaration.decl, &args.paths) {
        Ok(facts) => facts,
        Err(reason) => return (Verdict::Unjudgeable(reason), Vec::new()),
    };
    let verdict = match judge::judge(&base, &declaration, &args.paths) {
        Ok(mut hits) => {
            if lcom4 == Lcom4::Refuses {
                hits.extend(facts::lcom4_hits(&facts));
            }
            if hits.is_empty() {
                Verdict::Pass
            } else {
                Verdict::Reject(hits)
            }
        }
        Err(reason) => Verdict::Unjudgeable(reason),
    };
    (verdict, facts)
}

fn main() -> ExitCode {
    let (verdict, facts, format) = match args(std::env::args().skip(1)) {
        Ok((args, report)) => {
            let (verdict, facts) = run(args, report.lcom4);
            (verdict, facts, report.format)
        }
        Err(reason) => (Verdict::Unjudgeable(reason), Vec::new(), Format::Text),
    };
    match format {
        Format::Text => write_text(&verdict),
        Format::Json => write_json(&verdict, &facts, false),
        Format::JsonClassified => write_json(&verdict, &facts, true),
    }
    exit_code(&verdict)
}
