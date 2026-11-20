//! DOEFF214: the ledgers that let existing code pass only shrink, unless the user approved the line (docs/design/design-evidence v2 §8).
//!
//! DOEFF212's ledger of existing shapes and DOEFF213's ledger of existing substitutes each let what they list pass. A line added to a ledger (or a fingerprint rewritten,
//! or a count raised) lets new code pass without evidence, so it is the way around both rules. This rule compares each ledger with its version at the base — the
//! merge-base of HEAD and the declared `:guard {:base "<ref>"}` (`origin/main`), read with git — and reports:
//! - a ledger line that is new or changed (shapes) or raised (substitutes) and has no approval at the base;
//! - an approval entry added in the tree whose `review` does not have the declared spelling (`:approval-pattern`) or that has no `why` — the entry takes effect only once it is on the
//!   base (an approval added on the same branch does not count: it lands alone first);
//! - a change of the `:design-evidence` declaration itself (its spelling in architecture.hy differs from the base) without an approval — narrowing `:files`, widening `:except`,
//!   pointing a ledger at another file, or removing `:guard` are ways around the rules too.
//!
//! Removing a ledger line (a type that was removed or gained evidence, a substitute that was removed) passes. The approvals file
//! (`:guard {:approvals "<path>"}`, TOML `[[approval]]` with ledger, key, value, review, why) is also what lets DOEFF212 pass a field added to a type of the shapes ledger
//! (ledger = "fields", key = "<rel>::<Type>", value = "<field>").
//!
//! If the base cannot be read (no git, no such ref), the guard does not judge and the reason goes to the report's errors.

use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

use serde::Deserialize;

use super::architecture::{DesignEvidenceDecl, LedgerGuardDecl};
use super::design_evidence::ExistingShapes;
use super::type_substitutes::SubstituteCounts;

/// The environment variable that names the git checkout when the linter runs on a tree placed outside it (the commit hook's snapshot trees).
pub const GIT_ROOT_ENV: &str = "DOEFF_LINTER_GIT_ROOT";

/// The base the ledgers are compared with: a commit of a git checkout.
#[derive(Debug, Clone)]
pub struct Base {
    git_root: PathBuf,
    pub commit: String,
}

fn git(dir: &Path, args: &[&str]) -> Result<Vec<u8>, String> {
    let output = Command::new("git").args(args).current_dir(dir).stdin(Stdio::null()).output().map_err(|e| format!("cannot start git: {}", e))?;
    if output.status.success() {
        Ok(output.stdout)
    } else {
        Err(format!("git {} failed: {}", args.join(" "), String::from_utf8_lossy(&output.stderr).trim()))
    }
}

impl Base {
    /// The merge-base of HEAD and `reference` in the checkout at root (or the checkout named by DOEFF_LINTER_GIT_ROOT when root is not one).
    pub fn resolve(root: &Path, reference: &str) -> Result<Base, String> {
        let at = |dir: &Path| -> Result<Base, String> {
            let out = git(dir, &["merge-base", "HEAD", reference])?;
            let commit = String::from_utf8_lossy(&out).trim().to_string();
            if commit.is_empty() {
                return Err(format!("no merge-base of HEAD and {}", reference));
            }
            Ok(Base { git_root: dir.to_path_buf(), commit })
        };
        match at(root) {
            Ok(base) => Ok(base),
            Err(first) => match std::env::var_os(GIT_ROOT_ENV) {
                Some(dir) => at(Path::new(&dir)),
                None => Err(first),
            },
        }
    }

    /// A file's contents at the base (None if the file is not at the base).
    pub fn read(&self, rel: &str) -> Option<String> {
        git(&self.git_root, &["show", &format!("{}:{}", self.commit, rel)]).ok().map(|b| String::from_utf8_lossy(&b).into_owned())
    }
}

/// What an approval lets pass (a closed set — an approvals file naming another kind cannot be read, so it lets nothing pass).
#[derive(Debug, Clone, Copy, Deserialize, PartialEq, Eq)]
#[serde(try_from = "String")]
pub enum ApprovalLedger {
    /// A line of the shapes ledger (key = `<rel>::<Type>`, value = the fingerprint).
    Shapes,
    /// A line of the substitutes ledger (key = `<rel>::<kind>`, value = the count).
    Substitutes,
    /// A field added to a type of the shapes ledger (key = `<rel>::<Type>`, value = the field — DOEFF212's Grew).
    Fields,
    /// The `:design-evidence` declaration (key = `:design-evidence`, value = its fingerprint).
    Declaration,
}

impl ApprovalLedger {
    const ALL: [ApprovalLedger; 4] = [ApprovalLedger::Shapes, ApprovalLedger::Substitutes, ApprovalLedger::Fields, ApprovalLedger::Declaration];

    /// The word of the approvals file (the one spelling — reading goes through it).
    pub fn word(self) -> &'static str {
        match self {
            ApprovalLedger::Shapes => "shapes",
            ApprovalLedger::Substitutes => "substitutes",
            ApprovalLedger::Fields => "fields",
            ApprovalLedger::Declaration => "declaration",
        }
    }
}

impl TryFrom<String> for ApprovalLedger {
    type Error = String;

    fn try_from(word: String) -> Result<Self, String> {
        Self::ALL.into_iter().find(|l| l.word() == word).ok_or_else(|| format!("ledger = {:?} is not one of shapes, substitutes, fields, declaration", word))
    }
}

/// One user approval: the ledger line (or field, or declaration) it lets pass, the id of the request the user approved, and why.
#[derive(Debug, Clone, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct Approval {
    pub ledger: ApprovalLedger,
    pub key: String,
    #[serde(default)]
    pub value: String,
    #[serde(default)]
    pub review: String,
    #[serde(default)]
    pub why: String,
}

impl Approval {
    /// What makes the entry not an approval (None = it names a request of the declared spelling and says why). The one place both uses read: an entry added on the
    /// branch with a problem is red (`unspelled_approvals`), and an entry at the base with a problem lets nothing pass (`Approvals::spelled`).
    pub fn problem(&self, pattern: Option<&regex::Regex>) -> Option<String> {
        let review = self.review.trim();
        if review.is_empty() {
            Some("has no review (the id of the request the user approved)".to_string())
        } else if pattern.is_some_and(|p| !p.is_match(review)) {
            Some(format!("has review {} that does not have the declared spelling of an approved request", review))
        } else if self.why.trim().is_empty() {
            Some("has no why".to_string())
        } else {
            None
        }
    }
}

/// The approvals file.
#[derive(Debug, Clone, Default, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Approvals {
    /// The file's schema (1; absent = 1).
    #[serde(default)]
    pub schema: Option<u32>,
    #[serde(default)]
    pub approval: Vec<Approval>,
}

impl Approvals {
    pub fn parse(text: &str) -> Result<Self, String> {
        let approvals: Approvals = toml::from_str(text).map_err(|error| error.to_string().trim().to_string())?;
        match approvals.schema {
            None | Some(1) => Ok(approvals),
            Some(other) => Err(format!("schema = {} (this linter reads schema = 1)", other)),
        }
    }

    /// The entries that count: those without a problem (an entry whose review is not of the declared spelling — a remark, a conversation id — lets nothing pass, even
    /// at the base). Only the file at the base is passed here.
    pub fn spelled(self, pattern: Option<&regex::Regex>) -> BaseApprovals {
        BaseApprovals { approval: self.approval.into_iter().filter(|a| a.problem(pattern).is_none()).collect() }
    }
}

/// The approvals that let something pass: the entries of the approvals file at the base that have no problem (made only by `Approvals::spelled`, so an
/// approvals file of the tree cannot be asked what it covers).
#[derive(Debug, Clone, Default)]
pub struct BaseApprovals {
    approval: Vec<Approval>,
}

impl BaseApprovals {
    /// Whether an approval covers (ledger, key, value).
    pub fn covers(&self, ledger: ApprovalLedger, key: &str, value: &str) -> bool {
        self.approval.iter().any(|a| a.ledger == ledger && a.key == key && a.value == value)
    }
}

/// The declared spelling of a request id (None = not declared, or not a regex — then any non-empty review is of the spelling).
pub fn approval_pattern(guard: &LedgerGuardDecl) -> Option<regex::Regex> {
    guard.approval_pattern.as_deref().and_then(|p| regex::Regex::new(p).ok())
}

/// The approvals at the base that are of the declared spelling (empty if the guard declares no approvals file or the file is not at the base). Only these count.
pub fn base_approvals(guard: &LedgerGuardDecl, base: &Base) -> Result<BaseApprovals, String> {
    match guard.approvals.as_deref().and_then(|rel| base.read(rel).map(|text| (rel, text))) {
        None => Ok(BaseApprovals::default()),
        Some((rel, text)) => Approvals::parse(&text).map(|a| a.spelled(approval_pattern(guard).as_ref())).map_err(|reason| format!("{} at the base: {}", rel, reason)),
    }
}

/// One hit: the file it is reported at (from the root), the key detail, and the reason.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct GuardHit {
    pub rel: String,
    pub detail: String,
    pub reason: String,
}

/// The lines of the shapes ledger in the tree that are new or changed against the base and have no approval.
pub fn grown_shapes(rel: &str, now: &ExistingShapes, base: &ExistingShapes, approvals: &BaseApprovals) -> Vec<GuardHit> {
    now.lines()
        .filter(|(key, value)| base.line(key) != Some(*value) && !approvals.covers(ApprovalLedger::Shapes, key, value))
        .map(|(key, value)| GuardHit {
            rel: rel.to_string(),
            detail: format!("shapes:{}", key),
            reason: match base.line(key) {
                None => format!("{} adds the line {} = {} — a new ledger line lets a type pass without design evidence", rel, key, value),
                Some(was) => format!("{} rewrites {} from {} to {} — a rewritten fingerprint lets a reshaped type pass without design evidence", rel, key, was, value),
            },
        })
        .collect()
}

/// The lines of the substitutes ledger in the tree that are new or raised against the base and have no approval.
pub fn grown_substitutes(rel: &str, now: &SubstituteCounts, base: &SubstituteCounts, approvals: &BaseApprovals) -> Vec<GuardHit> {
    let base_of = |key: &str| base.lines().find(|(k, _)| *k == key).map(|(_, v)| v);
    now.lines()
        .filter(|(key, count)| base_of(key).map_or(true, |was| *count > was) && !approvals.covers(ApprovalLedger::Substitutes, key, &count.to_string()))
        .map(|(key, count)| GuardHit {
            rel: rel.to_string(),
            detail: format!("substitutes:{}", key),
            reason: format!(
                "{} raises {} from {} to {} — a raised count lets a new value that stands in for a type pass",
                rel,
                key,
                base_of(key).map_or("nothing".to_string(), |v| v.to_string()),
                count
            ),
        })
        .collect()
}

/// The approval entries added in the tree (not at the base) that lack a review of the declared spelling or a why.
pub fn unspelled_approvals(rel: &str, now: &Approvals, base: &Approvals, pattern: Option<&regex::Regex>) -> Vec<GuardHit> {
    now.approval
        .iter()
        .filter(|a| !base.approval.contains(a))
        .filter_map(|a| {
            a.problem(pattern).map(|problem| GuardHit {
                rel: rel.to_string(),
                detail: format!("approval:{}:{}:{}", a.ledger.word(), a.key, a.value),
                reason: format!("{} adds an approval for {} {} {} that {}", rel, a.ledger.word(), a.key, a.value, problem),
            })
        })
        .collect()
}

/// The spelling of the `:design-evidence` value in an architecture.hy, with runs of whitespace and comments collapsed (None if absent).
pub fn declaration_spelling(source: &str) -> Option<String> {
    use doeff_indexer::hy_index::reader::{Node, Reader};
    let mut reader = Reader::new(source, 0, source.len());
    let forms = reader.read_all();
    fn find(source: &str, items: &[doeff_indexer::hy_index::reader::Form]) -> Option<String> {
        let live: Vec<_> = items.iter().filter(|f| !matches!(f.node, Node::Discarded)).collect();
        for (i, item) in live.iter().enumerate() {
            if matches!(item.node, Node::Keyword) && source.get(item.span.start..item.span.end) == Some(":design-evidence") {
                let value = live.get(i + 1)?;
                return Some(spell(source, value));
            }
            if let Node::Seq { items, .. } = &item.node {
                if let Some(found) = find(source, items) {
                    return Some(found);
                }
            }
        }
        None
    }
    fn spell(source: &str, form: &doeff_indexer::hy_index::reader::Form) -> String {
        match &form.node {
            Node::Seq { delim, items } => {
                let inner: Vec<String> = items.iter().filter(|f| !matches!(f.node, Node::Discarded)).map(|f| spell(source, f)).collect();
                let close = match delim.opener_text() {
                    "(" | "#(" => ")",
                    "[" => "]",
                    _ => "}",
                };
                format!("{}{}{}", delim.opener_text(), inner.join(" "), close)
            }
            _ => source.get(form.span.start..form.span.end).unwrap_or("").to_string(),
        }
    }
    find(source, &forms)
}

/// The first 16 hex digits of the sha256 of a declaration's spelling (the value of a `declaration` approval).
pub fn declaration_fingerprint(spelling: &str) -> String {
    use sha2::{Digest, Sha256};
    Sha256::digest(spelling.as_bytes()).iter().take(8).map(|b| format!("{:02x}", b)).collect()
}

/// A file the guard reads that cannot be read as what it should be (reported as a hit, not as an error: a guard that passes on an unreadable ledger is a way around it).
fn unreadable(rel: &str, reason: String) -> GuardHit {
    GuardHit { rel: rel.to_string(), detail: format!("unreadable:{}", rel), reason: format!("{} cannot be read by DOEFF214 ({}) — the ledgers are not judged while it cannot", rel, reason) }
}

/// Parses a file of the tree and its version at the base (a missing file is empty; a file that cannot be parsed is a hit).
fn both<T: Default>(rel: &str, now: Option<String>, was: Option<String>, parse: impl Fn(&str) -> Result<T, String>, hits: &mut Vec<GuardHit>) -> Option<(T, T)> {
    let now = now.map(|t| parse(&t)).transpose().map_err(|r| unreadable(rel, r));
    let was = was.map(|t| parse(&t)).transpose().map_err(|r| unreadable(rel, format!("at the base: {}", r)));
    match (now, was) {
        (Ok(now), Ok(was)) => Some((now.unwrap_or_default(), was.unwrap_or_default())),
        (now, was) => {
            hits.extend(now.err());
            hits.extend(was.err());
            None
        }
    }
}

/// The judgment of the whole guard (Err with the reason only if the base cannot be resolved — then nothing is judged).
pub fn judge(root: &Path, architecture_rel: &str, decl: &DesignEvidenceDecl) -> Result<Vec<GuardHit>, String> {
    let Some(guard) = &decl.guard else { return Ok(Vec::new()) };
    let base = Base::resolve(root, &guard.base).map_err(|reason| format!("cannot read the DOEFF214 base {} ({})", guard.base, reason))?;
    let read_now = |rel: &str| std::fs::read_to_string(root.join(rel)).ok();
    let mut hits = Vec::new();
    let approvals_rel = guard.approvals.clone().unwrap_or_default();
    let approvals = match &guard.approvals {
        None => Some((Approvals::default(), Approvals::default())),
        Some(rel) => both(rel, read_now(rel), base.read(rel), Approvals::parse, &mut hits),
    };
    let Some((approvals_now, approvals_at_base)) = approvals else { return Ok(hits) };
    let pattern = approval_pattern(guard);
    let approvals = approvals_at_base.clone().spelled(pattern.as_ref());
    if let Some(rel) = &decl.existing {
        if let Some((now, was)) = both(rel, read_now(rel), base.read(rel), ExistingShapes::parse, &mut hits) {
            hits.extend(grown_shapes(rel, &now, &was, &approvals));
        }
    }
    if let Some(rel) = &decl.existing_substitutes {
        if let Some((now, was)) = both(rel, read_now(rel), base.read(rel), SubstituteCounts::parse, &mut hits) {
            hits.extend(grown_substitutes(rel, &now, &was, &approvals));
        }
    }
    if guard.approvals.is_some() {
        hits.extend(unspelled_approvals(&approvals_rel, &approvals_now, &approvals_at_base, pattern.as_ref()));
    }
    if let (Some(now), Some(was)) = (read_now(architecture_rel).and_then(|t| declaration_spelling(&t)), base.read(architecture_rel).and_then(|t| declaration_spelling(&t))) {
        let fingerprint = declaration_fingerprint(&now);
        if now != was && !approvals.covers(ApprovalLedger::Declaration, ":design-evidence", &fingerprint) {
            hits.push(GuardHit {
                rel: architecture_rel.to_string(),
                detail: "declaration:design-evidence".to_string(),
                reason: format!(
                    "{} changes the :design-evidence declaration from the base (fingerprint {}) — a narrowed population, a moved ledger, or a removed guard is a way around DOEFF212 to 214",
                    architecture_rel, fingerprint
                ),
            });
        }
    }
    Ok(hits)
}

/// Whether a path is one the guard reads (a single-file run of one of these judges the guard).
pub fn reads(rel: &str, architecture_rel: &str, decl: &DesignEvidenceDecl) -> bool {
    decl.guard.is_some()
        && (rel == architecture_rel
            || decl.existing.as_deref() == Some(rel)
            || decl.existing_substitutes.as_deref() == Some(rel)
            || decl.guard.as_ref().and_then(|g| g.approvals.as_deref()) == Some(rel))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn approvals(text: &str) -> Approvals {
        Approvals::parse(text).expect("approvals")
    }

    fn none() -> BaseApprovals {
        BaseApprovals::default()
    }

    #[test]
    fn a_shapes_line_added_or_rewritten_is_a_hit_and_a_removed_one_is_not() {
        let base = ExistingShapes::parse("[shapes]\n\"m.hy::A\" = \"aaaa\"\n\"m.hy::B\" = \"bbbb\"\n").unwrap();
        let removed = ExistingShapes::parse("[shapes]\n\"m.hy::A\" = \"aaaa\"\n").unwrap();
        assert!(grown_shapes("l.toml", &removed, &base, &none()).is_empty());
        let grown = ExistingShapes::parse("[shapes]\n\"m.hy::A\" = \"aaaa\"\n\"m.hy::B\" = \"cccc\"\n\"m.hy::C\" = \"dddd\"\n").unwrap();
        let details: Vec<String> = grown_shapes("l.toml", &grown, &base, &none()).into_iter().map(|h| h.detail).collect();
        assert_eq!(details, vec!["shapes:m.hy::B", "shapes:m.hy::C"]);
        let approved = approvals("[[approval]]\nledger = \"shapes\"\nkey = \"m.hy::C\"\nvalue = \"dddd\"\nreview = \"req-1\"\nwhy = \"moved file\"\n");
        let details: Vec<String> = grown_shapes("l.toml", &grown, &base, &approved.spelled(None)).into_iter().map(|h| h.detail).collect();
        assert_eq!(details, vec!["shapes:m.hy::B"]);
    }

    #[test]
    fn a_substitutes_count_raised_is_a_hit_and_a_lowered_one_is_not() {
        let base = SubstituteCounts::parse("[counts]\n\"m.hy::static-key-dict\" = 3\n").unwrap();
        let lowered = SubstituteCounts::parse("[counts]\n\"m.hy::static-key-dict\" = 2\n").unwrap();
        assert!(grown_substitutes("s.toml", &lowered, &base, &none()).is_empty());
        let raised = SubstituteCounts::parse("[counts]\n\"m.hy::static-key-dict\" = 4\n\"n.hy::answer-tuple\" = 1\n").unwrap();
        assert_eq!(grown_substitutes("s.toml", &raised, &base, &none()).len(), 2);
    }

    #[test]
    fn an_added_approval_needs_the_declared_review_spelling_and_a_why() {
        let pattern = regex::Regex::new("^req-[0-9]+$").unwrap();
        let now = approvals(
            "[[approval]]\nledger = \"shapes\"\nkey = \"k\"\nvalue = \"v\"\nreview = \"req-42\"\nwhy = \"w\"\n\
             [[approval]]\nledger = \"shapes\"\nkey = \"k2\"\nvalue = \"v\"\nreview = \"ok by me\"\nwhy = \"w\"\n\
             [[approval]]\nledger = \"shapes\"\nkey = \"k3\"\nvalue = \"v\"\nreview = \"req-42\"\n",
        );
        let details: Vec<String> = unspelled_approvals("a.toml", &now, &Approvals::default(), Some(&pattern)).into_iter().map(|h| h.detail).collect();
        assert_eq!(details, vec!["approval:shapes:k2:v", "approval:shapes:k3:v"]);
        // An approval added on the branch does not let the ledger line pass: only the base's approvals are consulted (the caller passes base_approvals).
        assert!(!none().covers(ApprovalLedger::Shapes, "k", "v"));
        // At the base, an entry whose review is not of the spelling lets nothing pass.
        assert!(!now.clone().spelled(Some(&pattern)).covers(ApprovalLedger::Shapes, "k2", "v"));
        assert!(now.spelled(Some(&pattern)).covers(ApprovalLedger::Shapes, "k", "v"));
        assert!(Approvals::parse("[[approval]]\nledger = \"shape\"\nkey = \"k\"\n").is_err(), "a misspelt ledger word cannot be read");
    }

    #[test]
    fn an_approvals_file_with_a_schema_reads_and_an_unreadable_ledger_is_a_hit() {
        assert!(Approvals::parse("schema = 1\n").is_ok());
        assert!(Approvals::parse("schema = 2\n").is_err());
        let mut hits = Vec::new();
        assert!(both("l.toml", Some("[shapes]\nx = 1\n".to_string()), None, ExistingShapes::parse, &mut hits).is_none());
        assert_eq!(hits.iter().map(|h| h.detail.as_str()).collect::<Vec<_>>(), vec!["unreadable:l.toml"]);
    }

    #[test]
    fn the_declaration_spelling_ignores_layout_and_comments() {
        let a = "(defarchitecture x\n  :design-evidence {:files [\"a/**/*.hy\"]\n ;; note\n :existing \"l.toml\"})";
        let b = "(defarchitecture x :design-evidence {:files [\"a/**/*.hy\"] :existing \"l.toml\"})";
        assert_eq!(declaration_spelling(a), declaration_spelling(b));
        assert_ne!(declaration_spelling(a), declaration_spelling(&b.replace("a/**", "a/core/**")));
        assert_eq!(declaration_spelling("(defarchitecture x)"), None);
    }
}
