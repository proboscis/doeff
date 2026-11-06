//! DOEFF209 — where a Program in production code repeatedly fetches the same thing with time waits in between (all such fetch
//! loops are to be fixed, as a last resort only, and this policy is enforced with doeff-linter and defjevrule).
//!
//! The population is the same declaration as DOEFF208 (Hy files of production code under `:business-fakes` in architecture.hy — `business_fakes::production_code`).
//! Unlike DOEFF208, the doeff-records package is not excluded either (this rule looks at the writers of waits themselves — if the owner of the effects and sources also has a loop that refetches on time,
//! it hits).
//!
//! What it looks at is "where the waking time comes from" (the spellings of Delay and sleep alone would miss the form that arms an ArmTimer at now + a fixed number of seconds and wakes on TimerFired):
//! 1. Spellings of time waits — `(Delay seconds)`, `(WaitTicks _ seconds …)` (a yield with a literal 0 seconds and a deadline wait of the form `(- deadline now)` are excluded), and
//!    `ArmTimer` / `ArmedTimer` whose time (2nd positional or `:at`) contains `(+ now …)` (now = a name bound from GetTime, now-ms, or clock-ms in the same definition,
//!    from a definition with no parameters whose body calls them [`(defk wall-ms-now [] …)`], or from a conversion call that takes exactly one such name),
//!    or contains a name bound from `(+ now …)` (the `at` of `(<- at (time-of-epoch-ms (+ now …)))`). For a wait inside a repetition, only a now bound inside the repetition
//!    is counted (a time computed from a now read once before the repetition is the same time every iteration and is not a form that refetches on time).
//! 2. Helper definitions — collected on the first reading, and callers are matched from the second (not held in a registry; re-read until nothing changes):
//!    - Helpers that take a time: definitions whose parameter flows (following rebinding) into the time position. If the caller's argument contains `(+ now …)`, that call is a wait spelling.
//!    - Helpers that take a now: definitions whose time contains `(+ parameter …)` or a name bound from `(+ parameter …)`. If the caller passes a now name to that parameter, that call is a wait spelling.
//!    - Helpers that return a wait: definitions that have a wait spelling outside a repetition. A call to one is a wait spelling.
//!    A call head is resolved to a definition in the same file or by the name in `(import module [name])`; an unresolvable `a.b` spelling is resolved when exactly one name matches.
//! 3. Repetition — a wait spelling is inside a `while` or `loop` body or an `event-loop` clause, or the definition calls itself (recursion). Something that sets it only once and
//!    exits is not matched (that definition becomes a helper that returns a wait, and the caller inside a repetition is matched).
//!    Inside a call that starts another task (`Spawn`, or a wrapper with spawn in its name) is not looked at — the wait of a Program handed over is that task's single wait, and if the handed-over
//!    Program repeats by itself, it is matched inside that definition.
//!    An effect that a production handler (`defhandler`) clause always answers (without going through a branch) with a wait outside a repetition is also treated the same as a helper that returns a wait, and the place that emits that effect inside a repetition
//!    is matched (the form that turns the sleep between periods into an AwaitNextTick effect and Delays in the handler — clauses that answer WatchChanges and WatchEvents are on DOEFF208's side).
//!    Python's `time.sleep` / `asyncio.sleep` (`sleep` with a module name) also count as sleeps.
//! 4. The key argument `:poll-seconds` that passes a re-query interval to a library (a literal non-0 value) is matched regardless of repetition (the library does the repeating).
//!
//! Not matched: waits for a deadline derived from a row or a schedule (the time does not contain `(+ now …)`), the upper-bound seconds when waiting for changes (the seconds of WatchChanges /
//! WatchEvents — DOEFF208's side), a request's timeout, a `match` pattern, the head of a handler clause (`(Delay [seconds] …)`), inside quote and `#_`, and
//! timeout deadlines (a tag that is a top-level name, where every branch body that compares against that tag is a `(stop …)` that exits the repetition — the upper-bound
//! seconds for waiting on a stop signal. A tag with a body that repeats the same read after it arrives is not excluded — stopping_tags).
//!
//! Category (the last segment of the hit's key detail, and the message): `periodic` (refetches purely on time) and `retry` (refetching after being unreachable — the wait spelling, the deadline's
//! tag, or the enclosing match clause's type spelling contains retry / unreachable / unavailable. A helper call counts only when all waits inside the helper are retry).
//!
//! Reasons that may be written at the place to pass: on the hit's line, or on a comment-only line just before it, `; 時間で取り直す理由: <word>` (placed the same way as DOEFF111's `; cannot be a defk: <reason>`).
//! The words are a closed set — `相手に変更の知らせが無い: <party> — <how it was verified>` (both the party's name and the basis of the verification [a section of the party's API documentation,
//! a line of the party's code, or a command tried and its result] must be non-empty. A party known to have change notifications [PARTIES_WITH_CHANGE_NOTICE —
//! Kubernetes, the coordinator's GET /watch, the doeff-events infrastructure, the records service's change signal] is not passed — Mac coordinator's condition 2026-10-07),
//! `書くだけ: 生存の印` / `書くだけ: 報告` / `書くだけ: 期限の延長`,
//! `届かない間だけの繋ぎ直し` (only for hits of category retry — a form that, only while an unreachable marker or a failure answer exists, waits and tries whether the connection works, does not go
//! fetch data, and clears the marker once it returns). Words outside these and words that do not fit the category are not passed, and the reason is added to the hit's message.

use std::collections::{BTreeSet, HashMap};
use std::path::Path;

use doeff_indexer::hy_index::reader::{Form, Node, Prefix, Reader};
use rayon::prelude::*;

use super::architecture::BusinessFakes;
use super::business_fakes::production_code;
use super::hy_files;
use super::paths::relative_path;
use crate::position::{LineIndex, Range};

/// Heads of calls that return now (last segment).
const CLOCK_HEADS: &[&str] = &["GetTime", "now-ms", "clock-ms"];
/// Effects that set a deadline, and the deadline value (the time is the 2nd positional or `:at`).
const TIMER_SINKS: &[&str] = &["ArmTimer", "ArmedTimer"];
const TIMER_AT_POSITION: usize = 1;
/// Effects that sleep on time, and the index of the seconds positional argument.
const SLEEPS: &[(&str, usize)] = &[("Delay", 0), ("WaitTicks", 1)];
/// Branch forms (below one in a handler clause, a wait is not a certain wait).
const BRANCHES: &[&str] = &["if", "when", "unless", "cond", "match", "try", "and", "or", "while", "loop"];
/// Effects that wait on record changes (the seconds are DOEFF208's side — waits in handler clauses that answer these are not counted).
const RECORD_WAITS: &[&str] = &["WatchChanges", "WatchEvents"];
/// Key arguments that pass a re-query interval to a library.
const POLL_KEY: &str = ":poll-seconds";
/// Repetition forms, and the first index of the repeated contents.
/// `for` is not counted — it goes over a finite collection one by one and is not a form that refetches on time (a few attempts when unreachable are written with while or recursion).
const REPEATING: &[(&str, usize)] = &[("while", 1), ("loop", 1), ("event-loop", 2)];
/// Heads of callable definitions (those that can be helpers returning a wait).
const CALLABLE_HEADS: &[&str] = &["defk", "deff", "defn", "defp", "defn/a"];
/// Spellings that claim refetching after being unreachable (compared in lowercase).
const RETRY_WORDS: &[&str] = &["retry", "unreachable", "unavailable"];
/// The marker of the reason comment written at the place to pass (Japanese: "reason for refetching on time:" — the vocabulary is matched by the code and used in repos' Hy comments, so it is kept).
pub const REASON_MARKER: &str = "時間で取り直す理由:";
/// Upper bound on re-reading the helpers (the depth of the chain of helpers — cut off when exceeded).
const MAX_ROUNDS: usize = 16;

/// Category of a hit (closed set).
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub enum Refetch {
    /// Purely refetching on time.
    Periodic,
    /// Refetching after being unreachable (a deadline of now + retry seconds).
    Retry,
}

impl Refetch {
    /// The last segment of the key detail.
    pub fn word(self) -> &'static str {
        match self {
            Refetch::Periodic => "periodic",
            Refetch::Retry => "retry",
        }
    }

    /// The name of the category in the message.
    pub fn label(self) -> &'static str {
        match self {
            Refetch::Periodic => "refetch on a timer",
            Refetch::Retry => "refetch after unreachable",
        }
    }
}

/// Kinds of write-only loops (closed set).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum WriteOnly {
    Liveness,
    Report,
    LeaseExtension,
}

/// Reasons that may be written at the place to pass (closed vocabulary).
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Reason {
    /// "The other party has no change notification" (the party's name and how it was verified — both non-empty).
    NoChangeNotice { party: String, evidence: String },
    /// Write-only (liveness marker, report, lease extension).
    WriteOnly(WriteOnly),
    /// Reconnecting only while unreachable (category retry only).
    ReconnectWhileUnreachable,
}

/// The head of the word "The other party has no change notification" (Japanese, matched by the code and used in repos' Hy comments, so it is kept).
pub const NO_CHANGE_NOTICE: &str = "相手に変更の知らせが無い:";
/// The separator between the party's name and the basis of the verification.
pub const EVIDENCE_SEPARATOR: char = '—';

/// Parties known to have change notifications (closed list — the name to show, the spellings to match). How it is matched: the written party's name is lowercased, whitespace is removed, and `_` is aligned to
/// `-`; if that spelling contains any of the spellings to match, it hits (Mac coordinator's condition 2026-10-07).
pub const PARTIES_WITH_CHANGE_NOTICE: &[(&str, &[&str])] = &[
    ("Kubernetes", &["kubernetes", "k8s", "kube-apiserver"]),
    ("coordinator GET /watch", &["coordinator", "/watch"]),
    ("doeff-events infrastructure", &["doeff-events"]),
    ("records service change signal", &["記録のservice", "記録の変更の合図", "agora-record", "doeff-records"]),
];

/// If the party's name is a party known to have change notifications, the name to show for it.
pub fn party_with_change_notice(party: &str) -> Option<&'static str> {
    let spelled: String = party.chars().filter(|c| !c.is_whitespace()).collect::<String>().to_lowercase().replace('_', "-");
    PARTIES_WITH_CHANGE_NOTICE.iter().find(|(_, words)| words.iter().any(|w| spelled.contains(w))).map(|(name, _)| *name)
}

/// Reasons for not passing a reason (closed set).
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ReasonRefusal {
    /// Outside the closed vocabulary (the written word itself).
    OutsideVocabulary(String),
    /// "The other party has no change notification" lacks the party's name or the basis of the verification.
    MissingPartyOrEvidence,
    /// The party is known to have change notifications (the name to show).
    PartyHasChangeNotice(&'static str),
}

/// Reads a reason word.
pub fn parse_reason(text: &str) -> Result<Reason, ReasonRefusal> {
    let text = text.trim();
    if let Some(rest) = text.strip_prefix(NO_CHANGE_NOTICE) {
        let (party, evidence) = rest.split_once(EVIDENCE_SEPARATOR).map(|(p, e)| (p.trim(), e.trim())).unwrap_or((rest.trim(), ""));
        if party.is_empty() || evidence.is_empty() {
            return Err(ReasonRefusal::MissingPartyOrEvidence);
        }
        if let Some(known) = party_with_change_notice(party) {
            return Err(ReasonRefusal::PartyHasChangeNotice(known));
        }
        return Ok(Reason::NoChangeNotice { party: party.to_string(), evidence: evidence.to_string() });
    }
    match text {
        "書くだけ: 生存の印" => Ok(Reason::WriteOnly(WriteOnly::Liveness)),
        "書くだけ: 報告" => Ok(Reason::WriteOnly(WriteOnly::Report)),
        "書くだけ: 期限の延長" => Ok(Reason::WriteOnly(WriteOnly::LeaseExtension)),
        "届かない間だけの繋ぎ直し" => Ok(Reason::ReconnectWhileUnreachable),
        _ => Err(ReasonRefusal::OutsideVocabulary(text.to_string())),
    }
}

/// Judgment of the reason written at the hit's place (none, passed, or the reason for not passing).
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ReasonRead {
    Absent,
    Accepted,
    Rejected(String),
}

/// The origin of a wait spelling (to show a wait inside a helper in the caller's body).
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct Origin {
    pub rel: String,
    /// Line counted from 1.
    pub line: usize,
    pub text: String,
    pub kind: Refetch,
}

/// Kinds of hit spelling (closed set).
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum SiteKind {
    /// A sleep by Delay or WaitTicks.
    Sleep,
    /// A deadline of now + a fixed number of seconds (a direct ArmTimer / ArmedTimer, or a call to a helper that takes a time or a helper that takes a now).
    Timer,
    /// A call to a helper that returns a wait (the origin of the wait inside the helper).
    Helper(Vec<Origin>),
    /// A key argument that passes a re-query interval to a library.
    PollSeconds,
}

/// One hit.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RefetchFinding {
    pub rel: String,
    pub range: Range,
    pub definition: String,
    pub head: String,
    pub kind: SiteKind,
    pub refetch: Refetch,
    /// The reason why the reason written at the place was not passed (None if none was written).
    pub rejected_reason: Option<String>,
    /// The remaining lines of unpassed hits of the same head and same category in the same definition (lines counted from 1 — hits are merged into one per key and shown at the first place).
    pub also_at: Vec<usize>,
}

impl RefetchFinding {
    /// Detail of the key (definition name::head spelling::category).
    pub fn detail(&self) -> String {
        format!("{}::{}::{}", self.definition, self.head, self.refetch.word())
    }

    /// Subject (what, where).
    pub fn subject(&self) -> String {
        match &self.kind {
            SiteKind::PollSeconds => format!("definition {} ({} … :poll-seconds …)", self.definition, self.head),
            _ => format!("definition {} ({} …)", self.definition, self.head),
        }
    }

    /// The violation text.
    pub fn describe(&self) -> String {
        let what = match &self.kind {
            SiteKind::Sleep => "repeats after sleeping on time".to_string(),
            SiteKind::Timer => "sets a deadline of now + a fixed number of seconds inside a repetition".to_string(),
            SiteKind::Helper(origins) => {
                let shown: Vec<String> = origins.iter().take(3).map(|o| format!("{}:{} {}", o.rel, o.line, o.text)).collect();
                let more = if origins.len() > 3 { format!(" and {} more places", origins.len() - 3) } else { String::new() };
                format!("calls, inside a repetition, a helper that sets a time wait inside (wait origins: {}{})", shown.join(", "), more)
            }
            SiteKind::PollSeconds => "passes a re-query interval to a library (the library re-queries on time)".to_string(),
        };
        let rejected = self.rejected_reason.as_ref().map(|why| format!(". The reason written is not passed: {}", why)).unwrap_or_default();
        let also = if self.also_at.is_empty() {
            String::new()
        } else {
            format!("(the same form in the same definition also appears on lines {} — each needs the same fix or a reason)", self.also_at.iter().map(|l| l.to_string()).collect::<Vec<_>>().join(", "))
        };
        format!(
            "{} — category {}: {}{}. It fetches the same thing repeatedly with time waits in between — make it wake on the other party's change notification (an event). Only where the other party has no way to announce changes, write `; {} <word>` on that line or the comment line just before it{}",
            self.subject(),
            self.refetch.label(),
            what,
            also,
            REASON_MARKER,
            rejected
        )
    }
}

fn last_segment(spelled: &str) -> &str {
    spelled.rsplit('.').next().unwrap_or(spelled)
}

/// Comparison form of a name (Hy's `-` and `_` are read as the same).
fn norm(name: &str) -> String {
    name.replace('-', "_")
}

/// The head segment of a symbol spelling (`beat.seconds` -> `beat`) as a comparison form.
fn root_name(spelled: &str) -> String {
    norm(spelled.split('.').next().unwrap_or(spelled))
}

fn visible(items: &[Form]) -> Vec<&Form> {
    items.iter().filter(|f| !matches!(f.node, Node::Discarded)).collect()
}

fn spelled<'s>(source: &'s str, form: &Form) -> &'s str {
    source.get(form.span.start..form.span.end).unwrap_or("")
}

fn head_symbol<'s>(source: &'s str, items: &[&Form]) -> Option<&'s str> {
    items.first().filter(|h| matches!(h.node, Node::Symbol)).map(|h| spelled(source, h))
}

fn is_keyword(source: &str, form: &Form, word: &str) -> bool {
    matches!(form.node, Node::Keyword) && norm(spelled(source, form)) == norm(word)
}

fn literal_zero(source: &str, form: &Form) -> bool {
    matches!(form.node, Node::Number) && spelled(source, form).replace('_', "").parse::<f64>().is_ok_and(|v| v == 0.0)
}

/// Whether it is a call that starts another task (doeff's `Spawn`, and wrappers with spawn in the name — such as `spawn-work`).
fn spawns(head: &str) -> bool {
    last_segment(head).to_lowercase().contains("spawn")
}

fn has_retry_word(text: &str) -> bool {
    let lower = text.to_lowercase();
    RETRY_WORDS.iter().any(|w| lower.contains(w))
}

/// From a call's arguments (after the head), returns the value of key `keyword` or the argument at position `position`.
fn argument<'f>(source: &str, args: &[&'f Form], position: usize, keyword: Option<&str>) -> Option<&'f Form> {
    if let Some(word) = keyword {
        if let Some(at) = args.iter().position(|f| is_keyword(source, f, word)) {
            return args.get(at + 1).copied();
        }
    }
    let positional: Vec<usize> = std::iter::successors(Some(0), |&at| args.get(at).map(|f| if matches!(f.node, Node::Keyword) { at + 2 } else { at + 1 }))
        .take_while(|&at| at < args.len())
        .filter(|&at| !matches!(args[at].node, Node::Keyword))
        .collect();
    positional.get(position).map(|&at| args[at])
}

/// The children of a form tree (quote is not looked into).
fn children(form: &Form) -> Vec<&Form> {
    match &form.node {
        Node::Seq { items, .. } => visible(items),
        Node::Prefixed { prefix: Prefix::Quote | Prefix::Quasiquote, .. } => Vec::new(),
        Node::Prefixed { inner: Some(inner), .. } | Node::Tagged { inner: Some(inner) } => vec![inner.as_ref()],
        Node::Annotated { annotation, target } => [annotation, target].into_iter().flatten().map(|b| b.as_ref()).collect(),
        _ => Vec::new(),
    }
}

/// Whether the head segment of any symbol in the form is in names.
fn mentions(source: &str, form: &Form, names: &BTreeSet<String>) -> bool {
    match form.node {
        Node::Symbol => names.contains(&root_name(spelled(source, form))),
        _ => children(form).into_iter().any(|c| mentions(source, c, names)),
    }
}

/// Among the arguments of `(+ a …)`, the symbols that are in names (the whole form tree).
fn plus_operands(source: &str, form: &Form, names: &BTreeSet<String>, found: &mut BTreeSet<String>) {
    if let Some(items) = form.paren_items().map(visible) {
        if head_symbol(source, &items) == Some("+") {
            for item in &items[1..] {
                if matches!(item.node, Node::Symbol) {
                    let name = root_name(spelled(source, item));
                    if names.contains(&name) {
                        found.insert(name);
                    }
                }
            }
        }
    }
    for child in children(form) {
        plus_operands(source, child, names, found);
    }
}

fn has_now_plus(source: &str, form: &Form, clocks: &BTreeSet<String>) -> bool {
    let mut found = BTreeSet::new();
    plus_operands(source, form, clocks, &mut found);
    !found.is_empty()
}

/// Whether it contains the form `(- deadline … now)` (a deadline wait).
fn deadline_difference(source: &str, form: &Form, clocks: &BTreeSet<String>) -> bool {
    let own = form.paren_items().map(visible).is_some_and(|items| {
        head_symbol(source, &items) == Some("-")
            && items.iter().skip(2).any(|i| matches!(i.node, Node::Symbol) && clocks.contains(&root_name(spelled(source, i))))
    });
    own || children(form).into_iter().any(|c| deadline_difference(source, c, clocks))
}

/// `(match subject pattern body pattern :if condition body …)` as (index of pattern, index of body).
fn match_clauses(source: &str, items: &[&Form]) -> Vec<(usize, usize)> {
    let mut clauses = Vec::new();
    let mut at = 2;
    while at < items.len() {
        let guarded = items.get(at + 1).is_some_and(|f| is_keyword(source, f, ":if"));
        let body = if guarded { at + 3 } else { at + 1 };
        clauses.push((at, body));
        at = body + 1;
    }
    clauses
}

/// Names of a definition's parameters (comparison form, in order — `*` and `/` are not counted).
fn parameters(source: &str, items: &[&Form]) -> Vec<String> {
    let Some(vector) = items.iter().skip(2).find_map(|f| f.bracket_items()) else { return Vec::new() };
    fn name_of(source: &str, form: &Form) -> Option<String> {
        match &form.node {
            Node::Symbol => Some(spelled(source, form).to_string()),
            Node::Annotated { target: Some(target), .. } => name_of(source, target),
            Node::Prefixed { prefix: Prefix::Unpack | Prefix::UnpackMapping, inner: Some(inner) } => name_of(source, inner),
            Node::Seq { .. } => form.bracket_items().and_then(|inner| visible(inner).first().and_then(|f| name_of(source, f))),
            _ => None,
        }
    }
    visible(vector)
        .into_iter()
        .filter(|f| !matches!(f.node, Node::Annotated { target: None, .. }))
        .filter_map(|f| name_of(source, f))
        .filter(|name| name != "*" && name != "/")
        .map(|name| norm(&name))
        .collect()
}

/// The name of a top-level form (the name for `(defk name …)` and `(setv name …)`, the head's spelling otherwise).
fn definition_name(source: &str, form: &Form) -> String {
    let items = form.paren_items().map(visible).unwrap_or_default();
    let head = items.first().map(|h| spelled(source, h)).unwrap_or("");
    let named = head.starts_with("def") || matches!(head, "setv" | "val" | "var");
    items
        .iter()
        .skip(1)
        .find(|f| matches!(f.node, Node::Symbol))
        .filter(|_| named)
        .map(|f| spelled(source, f).to_string())
        .unwrap_or_else(|| head.to_string())
}

/// One top-level definition.
struct Definition {
    name: String,
    key: String,
    params: Vec<String>,
    callable: bool,
    form: usize,
}

/// One file of the population (the forms read, the definitions, and import name -> module).
struct Unit {
    rel: String,
    source: String,
    forms: Vec<Form>,
    definitions: Vec<Definition>,
    by_key: HashMap<String, usize>,
    imports: HashMap<String, String>,
}

impl Unit {
    fn read(rel: String, source: String) -> Unit {
        let forms = Reader::new(&source, 0, source.len()).read_all();
        let mut definitions = Vec::new();
        let mut imports = HashMap::new();
        for (at, form) in forms.iter().enumerate() {
            let items = form.paren_items().map(visible).unwrap_or_default();
            let head = head_symbol(&source, &items).unwrap_or("");
            if head == "import" {
                read_imports(&source, &items[1..], &mut imports);
            }
            let name = definition_name(&source, form);
            let callable = CALLABLE_HEADS.contains(&head);
            definitions.push(Definition { key: norm(&name), params: if callable { parameters(&source, &items) } else { Vec::new() }, name, callable, form: at });
        }
        let by_key = definitions.iter().enumerate().filter(|(_, d)| d.callable).map(|(at, d)| (d.key.clone(), at)).collect();
        Unit { rel, source, forms, definitions, by_key, imports }
    }
}

/// Name -> module for `(import module [name name :as alias …] module …)`.
fn read_imports(source: &str, items: &[&Form], into: &mut HashMap<String, String>) {
    let mut module: Option<String> = None;
    for item in items {
        match &item.node {
            Node::Symbol => module = Some(spelled(source, item).to_string()),
            Node::Seq { .. } => {
                if let (Some(module), Some(names)) = (module.as_ref(), item.bracket_items()) {
                    let names = visible(names);
                    let mut at = 0;
                    while at < names.len() {
                        if matches!(names[at].node, Node::Symbol) {
                            let original = spelled(source, names[at]);
                            let aliased = names.get(at + 1).is_some_and(|f| is_keyword(source, f, ":as"));
                            let local = if aliased { names.get(at + 2).map(|f| spelled(source, f)).unwrap_or(original) } else { original };
                            into.insert(norm(local), format!("{}::{}", module, norm(original)));
                            at += if aliased { 3 } else { 1 };
                        } else {
                            at += 1;
                        }
                    }
                }
            }
            _ => {}
        }
    }
}

type DefId = (usize, usize);

/// The helper tables (grow on each re-reading).
#[derive(Default, Clone, PartialEq, Eq)]
struct Helpers {
    /// Helpers that take a time — (argument index, argument name).
    passthrough: HashMap<DefId, BTreeSet<(usize, String)>>,
    /// Helpers that take a now — (argument index, argument name, category).
    now_param: HashMap<DefId, BTreeSet<(usize, String, Refetch)>>,
    /// Helpers that return a wait — the origin of the wait.
    waits: HashMap<DefId, BTreeSet<Origin>>,
    /// Effects that a production handler's clause answers with a time wait outside a repetition (names in comparison form) — the origin of the wait.
    effects: HashMap<String, BTreeSet<Origin>>,
}

/// Module spelling -> file (those where exactly one matches by trailing path segments); definition name -> definition (those unique across the whole).
struct Index {
    modules: HashMap<String, Vec<usize>>,
    unique: HashMap<String, Vec<DefId>>,
    /// Heads of calls that return now (comparison form) — CLOCK_HEADS, and definitions with no parameters whose body calls them (`(defk wall-ms-now [] …)`).
    clock_heads: BTreeSet<String>,
    /// Tags of timeout deadlines (comparison form — stopping_tags).
    stopping_tags: BTreeSet<String>,
}

impl Index {
    fn new(units: &[Unit]) -> Index {
        let mut modules: HashMap<String, Vec<usize>> = HashMap::new();
        let mut unique: HashMap<String, Vec<DefId>> = HashMap::new();
        for (file, unit) in units.iter().enumerate() {
            let stem = unit.rel.strip_suffix(".hy").unwrap_or(&unit.rel);
            let parts: Vec<&str> = stem.split('/').collect();
            for from in 0..parts.len() {
                modules.entry(norm(&parts[from..].join("."))).or_default().push(file);
            }
            for (def, d) in unit.definitions.iter().enumerate().filter(|(_, d)| d.callable) {
                unique.entry(d.key.clone()).or_default().push((file, def));
            }
        }
        let mut clock_heads: BTreeSet<String> = CLOCK_HEADS.iter().map(|h| norm(h)).collect();
        loop {
            let found: Vec<String> = units
                .iter()
                .flat_map(|unit| {
                    unit.definitions
                        .iter()
                        .filter(|d| d.callable && d.params.is_empty() && !clock_heads.contains(&d.key))
                        .filter(|d| calls_any(&unit.source, &unit.forms[d.form], &clock_heads))
                        .map(|d| d.key.clone())
                })
                .collect();
            if found.is_empty() {
                break;
            }
            clock_heads.extend(found);
        }
        Index { modules, unique, clock_heads, stopping_tags: stopping_tags(units) }
    }

    /// The definition that the call head's spelling in file `file` points to.
    fn resolve(&self, units: &[Unit], file: usize, head: &str) -> Option<DefId> {
        let key = norm(last_segment(head));
        let unit = &units[file];
        if !head.contains('.') {
            if let Some(&def) = unit.by_key.get(&key) {
                return Some((file, def));
            }
            let imported = unit.imports.get(&key)?;
            let (module, original) = imported.split_once("::")?;
            let files = self.modules.get(&norm(module)).filter(|f| f.len() == 1)?;
            return units[files[0]].by_key.get(original).map(|&def| (files[0], def));
        }
        self.unique.get(&key).filter(|found| found.len() == 1).map(|found| found[0])
    }
}

/// Tags of timeout deadlines — among names bound at top level (`(val STOP-WAIT-TAG "…")`), those for which at least one branch that compares against the tag (`(cond (= tag name) body …)`,
/// `(if (= tag name) body …)`) exists in the population and whose bodies are all a `(stop …)` that exits the repetition. A deadline for the upper-bound seconds of waiting for a stop signal
/// only cuts the wait short and exits when it arrives, and is not a repetition that fetches the same thing. A tag that has even one body that
/// repeats the same read after it arrives is not excluded.
fn stopping_tags(units: &[Unit]) -> BTreeSet<String> {
    let mut seen: HashMap<String, bool> = HashMap::new();
    for unit in units {
        let source = unit.source.as_str();
        for form in &unit.forms {
            tag_branches(source, form, &mut |tested, body| {
                let exits = body.paren_items().map(visible).is_some_and(|items| head_symbol(source, &items) == Some("stop"));
                for name in tested {
                    *seen.entry(name).or_insert(true) &= exits;
                }
            });
        }
    }
    let constants: BTreeSet<String> = units
        .iter()
        .flat_map(|unit| {
            let source = unit.source.as_str();
            unit.forms.iter().filter_map(move |form| {
                let items = form.paren_items().map(visible)?;
                let head = head_symbol(source, &items)?;
                let name = items.get(1).filter(|f| matches!(f.node, Node::Symbol) && matches!(head, "val" | "setv"))?;
                Some(norm(spelled(source, name)))
            })
        })
        .collect();
    seen.into_iter().filter(|(name, exits)| *exits && constants.contains(name)).map(|(name, _)| name).collect()
}

/// Passes to visit, from a form tree, the (name of the compared symbol [comparison form, last segment], body) of branches compared with `(= a b)` (the pairs of `cond` and the then of `if`).
fn tag_branches<'f>(source: &str, form: &'f Form, visit: &mut impl FnMut(Vec<String>, &'f Form)) {
    if let Some(items) = form.paren_items().map(visible) {
        let pairs: Vec<(&Form, &Form)> = match head_symbol(source, &items) {
            Some("cond") => items[1..].chunks(2).filter(|pair| pair.len() == 2).map(|pair| (pair[0], pair[1])).collect(),
            Some("if") if items.len() >= 3 => vec![(items[1], items[2])],
            _ => Vec::new(),
        };
        for (test, body) in pairs {
            let Some(compared) = test.paren_items().map(visible).filter(|t| head_symbol(source, t) == Some("=")) else { continue };
            let names: Vec<String> =
                compared[1..].iter().filter(|f| matches!(f.node, Node::Symbol)).map(|f| norm(last_segment(spelled(source, f)))).collect();
            visit(names, body);
        }
    }
    for child in children(form) {
        tag_branches(source, child, visit);
    }
}

/// The wait spellings inside one definition.
#[derive(Debug, Clone)]
struct Site {
    start: usize,
    end: usize,
    head: String,
    kind: SiteKind,
    refetch: Refetch,
    repeated: bool,
}

/// The result of reading one definition.
#[derive(Default)]
struct Facts {
    sites: Vec<Site>,
    passthrough: BTreeSet<(usize, String)>,
    now_param: BTreeSet<(usize, String, Refetch)>,
}

/// The tool that walks one definition.
struct Walk<'a> {
    units: &'a [Unit],
    index: &'a Index,
    helpers: &'a Helpers,
    file: usize,
    me: DefId,
    params: &'a [String],
    /// Names of now read from all the bindings in the definition (used for wait spellings outside a repetition).
    anywhere: Moments,
    /// Names of now read from only the bindings inside a repetition (used for wait spellings inside a repetition — a time computed from a now read once before the repetition is
    /// the same time every iteration and is not a form that refetches on time).
    in_loop: Moments,
    recursive: bool,
    derived: Vec<BTreeSet<String>>,
    /// Per parameter, a name bound from `(+ parameter …)` (the `at` of `(<- at (time-of-epoch-ms (+ now retry-ms)))` — the time of a helper that takes a now).
    plus_derived: Vec<BTreeSet<String>>,
    facts: Facts,
}

impl<'a> Walk<'a> {
    fn source(&self) -> &'a str {
        &self.units[self.file].source
    }

    fn moments(&self, in_loop: bool) -> &Moments {
        if in_loop { &self.in_loop } else { &self.anywhere }
    }

    /// Judges a time expression `at` (Some if now + seconds — the content is whether the bound expression claims refetching after being unreachable). Collects the parameters that take a now or a time.
    fn time_argument(&mut self, at: &Form, kind: Refetch, in_loop: bool) -> Option<bool> {
        let source = self.source();
        let moments = self.moments(in_loop);
        if has_now_plus(source, at, &moments.clocks) || mentions(source, at, &moments.relative) {
            return Some(mentions(source, at, &moments.retry_named));
        }
        let params: BTreeSet<String> = self.params.iter().filter(|p| !self.anywhere.clocks.contains(*p)).cloned().collect();
        let mut plus = BTreeSet::new();
        plus_operands(source, at, &params, &mut plus);
        for (index, name) in self.params.iter().enumerate() {
            if params.contains(name) && mentions(source, at, &self.plus_derived[index]) {
                plus.insert(name.clone());
            }
        }
        if !plus.is_empty() {
            for (index, name) in self.params.iter().enumerate().filter(|(_, n)| plus.contains(*n)) {
                self.facts.now_param.insert((index, name.clone(), kind));
            }
            return None;
        }
        for (index, name) in self.params.iter().enumerate() {
            if mentions(source, at, &self.derived[index]) {
                self.facts.passthrough.insert((index, name.clone()));
            }
        }
        None
    }

    fn walk(&mut self, form: &Form, in_loop: bool, retry_hint: bool) {
        let source = self.source();
        let Some(items) = form.paren_items().map(visible) else {
            for child in children(form) {
                self.walk(child, in_loop, retry_hint);
            }
            return;
        };
        let head = head_symbol(source, &items).map(str::to_string);
        if head.as_deref().is_some_and(spawns) {
            // A wait inside a Program handed to another task is that task's single wait — it is neither the passing side's repetition nor the passing side's wait (if the handed-over
            // Program repeats by itself, it is matched inside that definition).
            return;
        }
        if let Some(head) = head.as_deref() {
            self.site_of(form, &items, head, in_loop, retry_hint);
        }
        let start = head.as_deref().and_then(|h| REPEATING.iter().find(|(name, _)| *name == h)).map(|(_, from)| *from);
        let clauses = if head.as_deref() == Some("match") { match_clauses(source, &items) } else { Vec::new() };
        for (at, item) in items.iter().enumerate() {
            if clauses.iter().any(|(pattern, _)| *pattern == at) {
                continue;
            }
            let pattern_hint =
                clauses.iter().find(|(_, body)| *body == at).is_some_and(|(pattern, _)| has_retry_word(spelled(source, items[*pattern])));
            let inside = in_loop || start.is_some_and(|from| at >= from);
            self.walk(item, inside, retry_hint || pattern_hint);
        }
    }

    fn push(&mut self, at: &Form, head: &str, kind: SiteKind, refetch: Refetch, repeated: bool) {
        self.facts.sites.push(Site { start: at.span.start, end: at.span.end, head: last_segment(head).to_string(), kind, refetch, repeated });
    }

    /// Whether a call is a wait spelling.
    fn site_of(&mut self, form: &Form, items: &[&Form], head: &str, in_loop: bool, retry_hint: bool) {
        let source = self.source();
        let repeated = in_loop || self.recursive;
        let name = last_segment(head);
        let args = &items[1..];
        if args.first().is_some_and(|f| f.bracket_items().is_some()) {
            // The head of a handler clause (`(Delay [seconds] …)`) and the parameter list of `fn` / `let` — not calls.
            return;
        }
        let named_retry = retry_hint || has_retry_word(spelled(source, form));
        let kind_of = |retry: bool| if retry { Refetch::Retry } else { Refetch::Periodic };
        if let Some(at) = args.iter().position(|f| is_keyword(source, f, POLL_KEY)) {
            if args.get(at + 1).is_some_and(|v| !literal_zero(source, v)) && !head.starts_with("def") {
                self.push(items[0], head, SiteKind::PollSeconds, Refetch::Periodic, repeated);
            }
        }
        let sleep_position =
            SLEEPS.iter().find(|(sleep, _)| *sleep == name).map(|(_, at)| *at).or_else(|| (name == "sleep" && head.contains('.')).then_some(0));
        if let Some(position) = sleep_position {
            let seconds = argument(source, args, position, None);
            if seconds.is_some_and(|s| !literal_zero(source, s) && !deadline_difference(source, s, &self.anywhere.clocks)) {
                self.push(items[0], head, SiteKind::Sleep, kind_of(named_retry), repeated);
            }
            return;
        }
        if TIMER_SINKS.contains(&name) {
            let tag = argument(source, args, 0, Some(":tag")).filter(|t| matches!(t.node, Node::Symbol));
            if tag.is_some_and(|t| self.index.stopping_tags.contains(&norm(last_segment(spelled(source, t))))) {
                // A timeout deadline (it only exits the repetition when it arrives) — not a repetition that fetches the same thing.
                return;
            }
            let refetch = kind_of(named_retry);
            if let Some(at) = argument(source, args, TIMER_AT_POSITION, Some(":at")) {
                if let Some(bound_retry) = self.time_argument(at, refetch, in_loop) {
                    self.push(items[0], head, SiteKind::Timer, kind_of(named_retry || bound_retry), repeated);
                }
            }
            return;
        }
        let target = self.index.resolve(self.units, self.file, head);
        if target == Some(self.me) {
            return;
        }
        let Some(target) = target else {
            // A head that is not a definition — if it is an effect that a production handler's clause answers with a time wait, the place that emits that effect is the same as a call to a helper that returns a wait.
            if let Some(origins) = self.helpers.effects.get(&norm(name)).cloned() {
                let all_retry = origins.iter().all(|o| o.kind == Refetch::Retry);
                let refetch = kind_of(retry_hint || all_retry || has_retry_word(name));
                self.push(items[0], head, SiteKind::Helper(origins.into_iter().collect()), refetch, repeated);
            }
            return;
        };
        let mut named_retry = named_retry;
        let mut timer = false;
        for (index, param) in self.helpers.passthrough.get(&target).cloned().unwrap_or_default() {
            if let Some(at) = argument(source, args, index, Some(&format!(":{}", param))) {
                if let Some(bound_retry) = self.time_argument(at, kind_of(named_retry), in_loop) {
                    timer = true;
                    named_retry |= bound_retry;
                }
            }
        }
        let refetch = kind_of(named_retry);
        for (index, param, kind) in self.helpers.now_param.get(&target).cloned().unwrap_or_default() {
            let Some(at) = argument(source, args, index, Some(&format!(":{}", param))) else { continue };
            if !matches!(at.node, Node::Symbol) {
                continue;
            }
            let passed = root_name(spelled(source, at));
            if self.moments(in_loop).clocks.contains(&passed) {
                timer = true;
            } else if let Some(mine) = self.params.iter().position(|p| *p == passed) {
                self.facts.now_param.insert((mine, passed, kind));
            }
        }
        if timer {
            let kinds: BTreeSet<Refetch> = self.helpers.now_param.get(&target).map(|s| s.iter().map(|(_, _, k)| *k).collect()).unwrap_or_default();
            let refetch = if named_retry || (!kinds.is_empty() && kinds.iter().all(|k| *k == Refetch::Retry)) { Refetch::Retry } else { refetch };
            self.push(items[0], head, SiteKind::Timer, refetch, repeated);
            return;
        }
        if let Some(origins) = self.helpers.waits.get(&target) {
            let all_retry = origins.iter().all(|o| o.kind == Refetch::Retry);
            let refetch = kind_of(retry_hint || all_retry || has_retry_word(name));
            self.push(items[0], head, SiteKind::Helper(origins.iter().cloned().collect()), refetch, repeated);
        }
    }
}

/// The bindings of names in a definition (`(<- name [type] expr)`, `(val name expr)`, `(setv name expr)`, `(var name expr)`, `(:= name expr)`) in the order they appear.
fn bindings<'f>(source: &str, form: &'f Form, in_loop: bool, out: &mut Vec<Binding<'f>>) {
    let Some(items) = form.paren_items().map(visible) else {
        for child in children(form) {
            bindings(source, child, in_loop, out);
        }
        return;
    };
    {
        // `:=` is read by Hy's reader as a keyword, so the head is compared by spelling, not limited to symbols.
        let head = items.first().map(|h| spelled(source, h)).unwrap_or("");
        let bound = match head {
            "<-" if items.len() >= 3 => Some((items[1], *items.last().unwrap())),
            "val" | "setv" | "var" | ":=" if items.len() == 3 => Some((items[1], items[2])),
            _ => None,
        };
        if let Some((name, expr)) = bound.filter(|(name, _)| matches!(name.node, Node::Symbol)) {
            out.push(Binding { name: norm(spelled(source, name)), expr, in_loop });
        }
    }
    let start = head_symbol(source, &items).and_then(|h| REPEATING.iter().find(|(name, _)| *name == h)).map(|(_, from)| *from);
    for (at, child) in items.iter().enumerate() {
        bindings(source, child, in_loop || start.is_some_and(|from| at >= from), out);
    }
}

/// One binding (name, expression, and whether it is inside a repetition).
struct Binding<'f> {
    name: String,
    expr: &'f Form,
    in_loop: bool,
}

/// The now names, and the names bound from now + seconds (the `at` of `(<- at (time-of-epoch-ms (+ now …)))`).
#[derive(Default)]
struct Moments {
    clocks: BTreeSet<String>,
    relative: BTreeSet<String>,
    /// Among relative, the names whose bound expression's spelling claims refetching after being unreachable.
    retry_named: BTreeSet<String>,
}

impl Moments {
    fn read<'f>(source: &str, clock_heads: &BTreeSet<String>, binds: impl Iterator<Item = &'f Binding<'f>> + Clone) -> Moments {
        let clocks = clock_names(source, clock_heads, binds.clone());
        let mut relative = BTreeSet::new();
        let mut retry_named = BTreeSet::new();
        for bind in binds {
            if has_now_plus(source, bind.expr, &clocks) || mentions(source, bind.expr, &relative) {
                relative.insert(bind.name.clone());
                if has_retry_word(spelled(source, bind.expr)) || mentions(source, bind.expr, &retry_named) {
                    retry_named.insert(bind.name.clone());
                }
            }
        }
        Moments { clocks, relative, retry_named }
    }
}

/// The now names (names bound by a call that returns now, or by a conversion call that takes exactly one now name).
fn clock_names<'f>(source: &str, clock_heads: &BTreeSet<String>, binds: impl Iterator<Item = &'f Binding<'f>>) -> BTreeSet<String> {
    let mut clocks = BTreeSet::new();
    for bind in binds {
        let Some(items) = bind.expr.paren_items().map(visible) else { continue };
        let Some(head) = head_symbol(source, &items) else { continue };
        let args: Vec<&&Form> = items[1..].iter().filter(|f| !matches!(f.node, Node::Keyword)).collect();
        let clock = clock_heads.contains(&norm(last_segment(head)))
            || (args.len() == 1 && matches!(args[0].node, Node::Symbol) && clocks.contains(&root_name(spelled(source, args[0]))));
        if clock {
            clocks.insert(bind.name.clone());
        }
    }
    clocks
}

/// Reads one definition with the now helper table.
fn read_definition(units: &[Unit], index: &Index, helpers: &Helpers, me: DefId) -> Facts {
    let unit = &units[me.0];
    let definition = &unit.definitions[me.1];
    let form = &unit.forms[definition.form];
    let source = unit.source.as_str();
    let mut binds = Vec::new();
    bindings(source, form, false, &mut binds);
    let anywhere = Moments::read(source, &index.clock_heads, binds.iter());
    let in_loop = Moments::read(source, &index.clock_heads, binds.iter().filter(|b| b.in_loop));
    let plus_derived = definition
        .params
        .iter()
        .map(|param| {
            let own: BTreeSet<String> = [param.clone()].into();
            let mut names = BTreeSet::new();
            for bind in &binds {
                let mut found = BTreeSet::new();
                plus_operands(source, bind.expr, &own, &mut found);
                if !found.is_empty() || mentions(source, bind.expr, &names) {
                    names.insert(bind.name.clone());
                }
            }
            names
        })
        .collect();
    let derived = definition
        .params
        .iter()
        .map(|param| {
            let mut names: BTreeSet<String> = [param.clone()].into();
            for bind in &binds {
                if mentions(source, bind.expr, &names) {
                    names.insert(bind.name.clone());
                }
            }
            names
        })
        .collect();
    let recursive = definition.callable && calls_itself(source, form, &definition.key, true);
    let mut walk = Walk { units, index, helpers, file: me.0, me, params: &definition.params, anywhere, in_loop, recursive, derived, plus_derived, facts: Facts::default() };
    walk.walk(form, false, false);
    walk.facts
}

/// Whether the form tree has a call whose head's last segment is in heads.
fn calls_any(source: &str, form: &Form, heads: &BTreeSet<String>) -> bool {
    let own = form.paren_items().map(visible).is_some_and(|items| head_symbol(source, &items).is_some_and(|h| heads.contains(&norm(last_segment(h)))));
    own || children(form).into_iter().any(|c| calls_any(source, c, heads))
}

/// Whether a definition's body calls itself (the head form itself is not counted).
fn calls_itself(source: &str, form: &Form, key: &str, top: bool) -> bool {
    let own = !top && form.paren_items().map(visible).is_some_and(|items| head_symbol(source, &items).is_some_and(|h| !h.contains('.') && norm(h) == key));
    own || children(form).into_iter().any(|c| calls_itself(source, c, key, false))
}

/// Builds the helper table by re-reading until nothing changes.
fn settle(units: &[Unit], index: &Index) -> (Helpers, Vec<(DefId, Facts)>) {
    let ids: Vec<DefId> = units.iter().enumerate().flat_map(|(file, unit)| (0..unit.definitions.len()).map(move |def| (file, def))).collect();
    let mut helpers = Helpers::default();
    for _ in 0..MAX_ROUNDS {
        let read: Vec<(DefId, Facts)> = ids.par_iter().map(|&id| (id, read_definition(units, index, &helpers, id))).collect();
        let mut next = Helpers::default();
        for (id, facts) in &read {
            for (effect, origins) in clause_waits(&units[id.0], &units[id.0].definitions[id.1], facts) {
                next.effects.entry(effect).or_default().extend(origins);
            }
            if !units[id.0].definitions[id.1].callable {
                continue;
            }
            if !facts.passthrough.is_empty() {
                next.passthrough.insert(*id, facts.passthrough.clone());
            }
            if !facts.now_param.is_empty() {
                next.now_param.insert(*id, facts.now_param.clone());
            }
            let once: BTreeSet<Origin> = facts
                .sites
                .iter()
                .filter(|s| !s.repeated && s.kind != SiteKind::PollSeconds)
                .flat_map(|s| match &s.kind {
                    SiteKind::Helper(origins) => origins.clone(),
                    _ => vec![site_origin(&units[id.0], s)],
                })
                .collect();
            if !once.is_empty() {
                next.waits.insert(*id, once);
            }
        }
        if next == helpers {
            return (helpers, read);
        }
        helpers = next;
    }
    let read = ids.par_iter().map(|&id| (id, read_definition(units, index, &helpers, id))).collect();
    (helpers, read)
}

/// Per defhandler clause, the origins of wait spellings outside a repetition (effect names in comparison form). Clauses that answer waits on record changes (WatchChanges, WatchEvents) are
/// not counted — the upper-bound seconds when waiting for changes are DOEFF208's side.
fn clause_waits(unit: &Unit, definition: &Definition, facts: &Facts) -> Vec<(String, BTreeSet<Origin>)> {
    let source = unit.source.as_str();
    let items = unit.forms[definition.form].paren_items().map(visible).unwrap_or_default();
    if head_symbol(source, &items) != Some("defhandler") {
        return Vec::new();
    }
    items
        .iter()
        .skip(2)
        .filter_map(|clause| {
            let parts = clause.paren_items().map(visible)?;
            let effect = head_symbol(source, &parts)?;
            parts.get(1).and_then(|f| f.bracket_items())?;
            if RECORD_WAITS.contains(&last_segment(effect)) {
                return None;
            }
            let mut plain = BTreeSet::new();
            for statement in &parts[2..] {
                unconditional_heads(source, statement, &mut plain);
            }
            let origins: BTreeSet<Origin> = facts
                .sites
                .iter()
                .filter(|s| !s.repeated && s.kind != SiteKind::PollSeconds && plain.contains(&s.start))
                .flat_map(|s| match &s.kind {
                    SiteKind::Helper(origins) => origins.clone(),
                    _ => vec![site_origin(unit, s)],
                })
                .collect();
            (!origins.is_empty()).then(|| (norm(last_segment(effect)), origins))
        })
        .collect()
}

/// The positions of call heads reachable without going through a branch form (`if`, `when`, `unless`, `cond`, `match`, `try`, `and`, `or`) (where a clause surely waits — a clause that
/// waits only in a failure branch does not make that effect a wait).
fn unconditional_heads(source: &str, form: &Form, into: &mut BTreeSet<usize>) {
    let Some(items) = form.paren_items().map(visible) else { return };
    let head = head_symbol(source, &items);
    if head.is_some_and(|h| BRANCHES.contains(&h) || spawns(h)) {
        return;
    }
    if let Some(first) = items.first().filter(|_| head.is_some()) {
        into.insert(first.span.start);
    }
    for item in &items[1..] {
        unconditional_heads(source, item, into);
    }
}

fn site_origin(unit: &Unit, site: &Site) -> Origin {
    let line = unit.source[..site.start.min(unit.source.len())].matches('\n').count() + 1;
    let line_text = unit.source.lines().nth(line - 1).unwrap_or("").trim();
    Origin { rel: unit.rel.clone(), line, text: line_text.chars().take(80).collect(), kind: site.refetch }
}

/// Reads the reason comment on the hit's line, or on a comment-only line just before it.
pub fn reason_at(source: &str, line: usize, refetch: Refetch) -> ReasonRead {
    let lines: Vec<&str> = source.lines().collect();
    let mut candidates = vec![lines.get(line).copied().unwrap_or("")];
    let mut at = line;
    while at > 0 && lines[at - 1].trim_start().starts_with(';') {
        at -= 1;
        candidates.push(lines[at]);
    }
    let Some(text) = candidates.iter().find_map(|l| l.find(REASON_MARKER).map(|from| &l[from + REASON_MARKER.len()..])) else {
        return ReasonRead::Absent;
    };
    match parse_reason(text) {
        Ok(Reason::ReconnectWhileUnreachable) if refetch != Refetch::Retry => ReasonRead::Rejected(
            "\"届かない間だけの繋ぎ直し\" can be written only for the category \"refetch after unreachable\" (a place that tries only while an unreachable marker or a failure answer exists)".to_string(),
        ),
        Ok(_) => ReasonRead::Accepted,
        Err(ReasonRefusal::OutsideVocabulary(word)) => ReasonRead::Rejected(format!(
            "\"{}\" is outside the closed vocabulary ({} <party> {} <how it was verified> / 書くだけ: 生存の印・報告・期限の延長 / 届かない間だけの繋ぎ直し)",
            word, NO_CHANGE_NOTICE, EVIDENCE_SEPARATOR
        )),
        Err(ReasonRefusal::MissingPartyOrEvidence) => ReasonRead::Rejected(format!(
            "\"{}\" needs both the party's name and, after {}, how it was verified (a section of the party's API documentation, a line of the party's code, or a command tried and its result)",
            NO_CHANGE_NOTICE.trim_end_matches(':'),
            EVIDENCE_SEPARATOR
        )),
        Err(ReasonRefusal::PartyHasChangeNotice(known)) => {
            ReasonRead::Rejected(format!("this party has change notifications ({}) — make it wake on those notifications", known))
        }
    }
}

/// Produces hits from the files read (the entry point for tests — passes file contents directly).
pub fn judge_sources(files: Vec<(String, String)>) -> Vec<RefetchFinding> {
    let units: Vec<Unit> = files.into_par_iter().map(|(rel, source)| Unit::read(rel, source)).collect();
    let index = Index::new(&units);
    let (_, read) = settle(&units, &index);
    let mut found: Vec<RefetchFinding> = read
        .into_iter()
        .flat_map(|(id, facts)| {
            let unit = &units[id.0];
            let lines = LineIndex::new(&unit.source);
            let definition = unit.definitions[id.1].name.clone();
            facts
                .sites
                .into_iter()
                .filter(|s| s.repeated || s.kind == SiteKind::PollSeconds)
                .filter_map(|s| {
                    let range = lines.range(s.start, s.end);
                    let rejected_reason = match reason_at(&unit.source, range.start.line as usize, s.refetch) {
                        ReasonRead::Accepted => return None,
                        ReasonRead::Absent => None,
                        ReasonRead::Rejected(why) => Some(why),
                    };
                    Some(RefetchFinding {
                        rel: unit.rel.clone(),
                        range,
                        definition: definition.clone(),
                        head: s.head,
                        kind: s.kind,
                        refetch: s.refetch,
                        rejected_reason,
                        also_at: Vec::new(),
                    })
                })
                .collect::<Vec<_>>()
        })
        .collect();
    found.sort_by(|a, b| (a.rel.as_str(), a.range.start.line, a.range.start.character).cmp(&(b.rel.as_str(), b.range.start.line, b.range.start.character)));
    let mut merged: Vec<RefetchFinding> = Vec::new();
    let mut first: HashMap<(String, String), usize> = HashMap::new();
    for finding in found {
        let key = (finding.rel.clone(), finding.detail());
        match first.get(&key) {
            Some(&at) => merged[at].also_at.push(finding.range.start.line as usize + 1),
            None => {
                first.insert(key, merged.len());
                merged.push(finding);
            }
        }
    }
    merged
}

/// Judges all Hy files of the repo's production code (hits, and reasons for unreadable files).
pub fn find(root: &Path, decl: &BusinessFakes) -> (Vec<RefetchFinding>, Vec<String>) {
    let files: Vec<(String, std::path::PathBuf)> = hy_files::collect(root)
        .into_iter()
        .filter_map(|path| relative_path(root, &path).map(|rel| (rel, path)))
        .filter(|(rel, _)| production_code(rel, decl))
        .collect();
    let read: Vec<Result<(String, String), String>> = files
        .into_par_iter()
        .map(|(rel, path)| std::fs::read_to_string(&path).map(|source| (rel.clone(), source)).map_err(|error| format!("{}: cannot read: {}", rel, error)))
        .collect();
    let (sources, errors): (Vec<_>, Vec<_>) = read.into_iter().partition(Result::is_ok);
    (judge_sources(sources.into_iter().filter_map(Result::ok).collect()), errors.into_iter().filter_map(Result::err).collect())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn details(files: &[(&str, &str)]) -> Vec<String> {
        judge_sources(files.iter().map(|(r, s)| (r.to_string(), s.to_string())).collect()).iter().map(RefetchFinding::detail).collect()
    }

    #[test]
    fn timers_on_now_plus_seconds_in_loops_are_hit_and_one_shot_deadlines_are_not() {
        let source = "(defk poll [s]\n  (while True\n    (<- now (GetTime))\n    (<- (ArmTimer TAG (+ now (timedelta :seconds s))))\n    (<- (WaitForEvent TimerFired))\n    (<- (Read))))\n\
                      (defk once [s]\n  (<- now (GetTime))\n  (<- (ArmTimer TAG (+ now (timedelta :seconds s)))))\n\
                      (defk until [row]\n  (while True\n    (<- (ArmTimer TAG row.deadline))\n    (<- (Read))))\n\
                      (defk sleeps []\n  (while True (<- (Delay 5.0)) (<- (Delay 0)) (<- (Read))))\n";
        assert_eq!(details(&[("a.hy", source)]), vec!["poll::ArmTimer::periodic", "sleeps::Delay::periodic"]);
    }

    #[test]
    fn helpers_are_collected_then_their_callers_in_loops_are_hit() {
        let helper = "(defk arm-next [s]\n  (<- now (GetTime))\n  (<- (ArmTimer TAG (+ now (timedelta :seconds s)))))\n\
                      (defk at-ms [tag moment]\n  (<- at (time-of-epoch-ms moment))\n  (ArmedTimer tag at))\n\
                      (defk wanted [now wait]\n  #((ArmedTimer :tag PULSE :at (+ now wait))))\n";
        let caller = "(import app.help [arm-next at-ms wanted])\n\
                      (defk loop-a [s] (while True (<- (arm-next s)) (<- (Read))))\n\
                      (defk loop-b [] (while True (<- now int (now-ms)) (<- (at-ms RETRY-TAG (+ now 2000))) (<- (Read))))\n\
                      (defk loop-c [] (event-loop [x X (start)] (Moved) (do (<- now (GetTime)) (! (wanted now 5)))))\n\
                      (defk loop-d [row] (while True (<- (at-ms END-TAG row.end)) (<- (Read))))\n";
        assert_eq!(
            details(&[("app/help.hy", helper), ("app/use.hy", caller)]),
            vec!["loop-a::arm-next::periodic", "loop-b::at-ms::retry", "loop-c::wanted::periodic"]
        );
    }

    #[test]
    fn moments_fixed_before_the_loop_are_not_hit_and_moments_bound_in_the_loop_are() {
        let source = "(defk armed-at [tag moment]\n  (<- at (time-of-epoch-ms moment))\n  (ArmedTimer tag at))\n\
                      (defk run []\n  (<- started (now-ms))\n  (val end-at (+ started 1000))\n  (var again-at None)\n  (while True\n\
                      \x20   (<- (armed-at END-TAG end-at))\n    (<- (armed-at PATIENCE-TAG (+ started 500)))\n    (<- now (now-ms))\n\
                      \x20   (:= again-at (+ now 2000))\n    (<- (armed-at WAKE-TAG again-at))\n    (<- (Read))))\n";
        assert_eq!(details(&[("a.hy", source)]), vec!["run::armed-at::periodic"]);
        let found = judge_sources(vec![("a.hy".into(), source.into())]);
        assert_eq!(found[0].range.start.line, 12);
    }

    #[test]
    fn now_bound_through_names_and_clock_helpers_are_followed() {
        let source = "(defk wall-ms-now []\n  (<- t (GetTime))\n  (epoch-ms-of-time t))\n\
                      (defk retry-deadlines [now retry-ms]\n  (<- at (time-of-epoch-ms (+ now retry-ms)))\n  #((ArmedTimer RETRY-TAG at)))\n\
                      (defk wanted [now retry-ms]\n  (<- retries (retry-deadlines now retry-ms))\n  retries)\n\
                      (defk run []\n  (while True\n    (<- now (wall-ms-now))\n    (<- (rearm (wanted now 2000)))\n    (<- (Read))))\n";
        assert_eq!(details(&[("a.hy", source)]), vec!["run::wanted::retry"]);
    }

    #[test]
    fn same_shape_twice_in_one_definition_is_one_finding() {
        let source = "(defk ticked []\n  (<- now (GetTime))\n  (<- (ArmTimer TAG (+ now (timedelta :seconds 5)))))\n\
                      (defk run []\n  (event-loop [s S (! (ticked))]\n    (Moved) (! (ticked))\n    (TimerFired) (! (ticked))))\n";
        let found = judge_sources(vec![("a.hy".into(), source.into())]);
        assert_eq!(found.iter().map(RefetchFinding::detail).collect::<Vec<_>>(), vec!["run::ticked::periodic"]);
        assert_eq!((found[0].range.start.line, found[0].also_at.clone()), (5, vec![7]));
    }

    #[test]
    fn effects_answered_by_a_timed_wait_and_module_sleeps_are_followed() {
        let handler = "(defk tick-pause [s]\n  (<- (Delay s)))\n\
                       (defhandler pauses\n  (AwaitNextTick [s]\n    (<- (tick-pause s))\n    (resume None))\n  \
                       (WatchChanges [t c timeout]\n    (<- (Delay timeout))\n    (resume None)))\n";
        let user = "(defk run []\n  (while True\n    (<- (AwaitNextTick 1.0))\n    (<- (WatchChanges T C 5))\n    (<- (Read))))\n\
                    (defhandler os [p]\n  (Wait [x]\n    (while (alive? p)\n      (time.sleep 0.02))\n    (resume None)))\n";
        assert_eq!(details(&[("h.hy", handler), ("u.hy", user)]), vec!["run::AwaitNextTick::periodic", "os::sleep::periodic"]);
    }

    /// Rule: a deadline for the upper bound of waiting on a stop signal (it only exits the repetition when it arrives — the deadline-passed form of pumping-loop in agora-controllers'
    /// task_attempt.hy) is not matched. Even the same form is matched if its body repeats the same read after the deadline arrives.
    #[test]
    fn a_deadline_that_only_ends_the_wait_is_not_hit_but_one_that_reads_again_is() {
        let shape = |after_stop_wait: &str| {
            format!(
                "(val DEADLINE-TAG \"t:deadline\")\n(val STOP-WAIT-TAG \"t:stop-wait\")\n\
                 (defk deadline-passed [p]\n  (<- read (stopped-by p))\n  (when (not (finished? read))\n    (<- now (GetTime))\n    \
                 (<- (ArmTimer STOP-WAIT-TAG (+ now (timedelta :seconds STOP-WAIT-SECONDS)))))\n  read)\n\
                 (defk pumping-loop [first]\n  (event-loop [p P first]\n    (Moved) (! (read-on p))\n    (TimerFired :tag tag) (cond\n      \
                 (= tag DEADLINE-TAG) (let [read (! (deadline-passed p))] (if (! (finished? read)) (stop read) read))\n      \
                 (= tag STOP-WAIT-TAG) {}\n      True p)))\n",
                after_stop_wait
            )
        };
        assert!(details(&[("a.hy", &shape("(stop p)"))]).is_empty(), "{:?}", details(&[("a.hy", &shape("(stop p)"))]));
        assert_eq!(details(&[("a.hy", &shape("(! (read-on p))"))]), vec!["pumping-loop::deadline-passed::periodic"]);
    }

    #[test]
    fn reasons_are_a_closed_vocabulary() {
        // Passes if the party and the basis of the verification exist.
        assert_eq!(
            parse_reason(" 相手に変更の知らせが無い: custody-store — no watch in section 3 of custody's API documentation"),
            Ok(Reason::NoChangeNotice { party: "custody-store".into(), evidence: "no watch in section 3 of custody's API documentation".into() })
        );
        // No basis, or no party = not passed.
        for text in ["相手に変更の知らせが無い: custody-store", "相手に変更の知らせが無い: custody-store — ", "相手に変更の知らせが無い: — section 3 of the documentation"] {
            assert_eq!(parse_reason(text), Err(ReasonRefusal::MissingPartyOrEvidence), "{}", text);
        }
        // A party known to have change notifications = not passed (regardless of case, whitespace, or alias).
        for (party, known) in [
            ("Kubernetes", "Kubernetes"),
            ("kubernetes API", "Kubernetes"),
            ("K8s", "Kubernetes"),
            ("doeff-cluster Coordinator", "coordinator GET /watch"),
            ("doeff_events infrastructure", "doeff-events infrastructure"),
            ("記録の service", "records service change signal"),
        ] {
            let text = format!("相手に変更の知らせが無い: {} — read the documentation", party);
            assert_eq!(parse_reason(&text), Err(ReasonRefusal::PartyHasChangeNotice(known)), "{}", text);
        }
        assert_eq!(parse_reason("書くだけ: 生存の印"), Ok(Reason::WriteOnly(WriteOnly::Liveness)));
        // Words outside the closed vocabulary are not passed (spellings removed from the closed vocabulary also come in here).
        assert_eq!(parse_reason("cannot be helped"), Err(ReasonRefusal::OutsideVocabulary("cannot be helped".into())));
        let known = "(defk a []\n  (while True\n    ;; 時間で取り直す理由: 相手に変更の知らせが無い: Kubernetes — tried it\n    (<- (Delay 1.0))\n    (<- (Read))))\n";
        assert!(matches!(reason_at(known, 3, Refetch::Periodic), ReasonRead::Rejected(why) if why.contains("this party has change notifications")));
        let source = "(defk a []\n  (while True\n    ;; 時間で取り直す理由: 届かない間だけの繋ぎ直し\n    (<- (Delay 1.0))\n    (<- (Read))))\n";
        assert!(matches!(reason_at(source, 3, Refetch::Periodic), ReasonRead::Rejected(_)));
        assert_eq!(reason_at(source, 3, Refetch::Retry), ReasonRead::Accepted);
        assert_eq!(reason_at(source, 1, Refetch::Retry), ReasonRead::Absent);
    }
}
