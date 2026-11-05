//! DOEFF208 — where production code makes waiting on record changes the means of waking a job (the design of writing a record and
//! waiting for the record is found by the linter and forbidden. Separate recording from launching; launching is received from an event infrastructure such as Redis or RabbitMQ and answered immediately).
//!
//! It applies to Hy files of production code in a repo that wrote `:business-fakes` in architecture.hy (`business_fakes::production_code` — the same scope declaration as DOEFF143's
//! production entry and DOEFF207's non-job writers. Tests, mock environments, and files not read are excluded). The following 2 are hits:
//! - Where record changes are made an event source — calls to doeff-records' signal-source factories (`read-signal-handler`, `records-signal-handler`).
//! - Where record changes are waited on — `WatchChanges` / `WatchEvents` issued with seconds (`:timeout` or the 3rd positional argument) that is a literal non-0 value
//!   (a variable, an expression, or a splat `#*` / `#**` also hits since it "cannot be said to be 0"). The form that does not write seconds is the default 0 (read once without waiting), so it does not hit.
//!
//! Not hits: reading once without waiting (seconds 0 — the form where the receiver catches up by reading records at startup or reconnection), writing to records (the input line so as not to drop anything),
//! a wait re-issued inside a handler that answers a record effect (a defhandler whose clause head is `WatchChanges` / `WatchEvents`), the spelling of a match pattern, and
//! inside the doeff-records package (dir `doeff_records`) — this rule looks at users. The owner of the effects and sources removes the source itself when switching to the event infrastructure.

use std::collections::BTreeSet;
use std::path::Path;

use doeff_indexer::hy_index::reader::{Form, Node, Prefix, Reader};
use rayon::prelude::*;

use super::architecture::BusinessFakes;
use super::business_fakes::production_code;
use super::hy_files;
use super::paths::relative_path;
use crate::position::{LineIndex, Range};

/// The factory that makes record changes an event source (doeff-records' event_source.hy) — the last segment of the call head's spelling.
const SIGNAL_SOURCES: &[&str] = &["read-signal-handler", "records-signal-handler"];
/// The effects that wait on record changes (doeff-records' effects.hy) — in both, seconds is the 3rd positional argument (`tables cursor timeout` / `stream after timeout`).
const RECORD_WAITS: &[&str] = &["WatchChanges", "WatchEvents"];
const TIMEOUT_POSITION: usize = 2;
/// The dir of the package that owns the effects and sources (below this is outside the population).
const OWNER_PACKAGE_DIR: &str = "doeff_records";

/// Kind of hit (closed set).
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum WaitUse {
    /// A call to a signal-source factory (the last segment of the head's spelling).
    SignalSource { factory: String },
    /// A record-change wait whose seconds is not 0 (effect name, spelling of seconds).
    LongPoll { effect: String, timeout: String },
}

/// One hit (path from the root, position of the call head, name of the enclosing top-level definition, kind).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct WaitFinding {
    pub rel: String,
    pub range: Range,
    pub definition: String,
    pub what: WaitUse,
}

impl WaitFinding {
    /// Key detail (definition name::head spelling).
    pub fn detail(&self) -> String {
        let head = match &self.what {
            WaitUse::SignalSource { factory } => factory,
            WaitUse::LongPoll { effect, .. } => effect,
        };
        format!("{}::{}", self.definition, head)
    }

    /// Subject (what, where).
    pub fn subject(&self) -> String {
        match &self.what {
            WaitUse::SignalSource { factory } => format!("definition {} ({} …)", self.definition, factory),
            WaitUse::LongPoll { effect, timeout } => format!("definition {} ({} … :timeout {})", self.definition, effect, timeout),
        }
    }

    /// The violation text.
    pub fn describe(&self) -> String {
        match &self.what {
            WaitUse::SignalSource { .. } => format!(
                "{} — makes record changes an event source (wakes a job by waiting on record changes). Records are records, launching is launching — wake through a notification from the event infrastructure",
                self.subject()
            ),
            WaitUse::LongPoll { .. } => format!(
                "{} — waits on record changes with a non-0 wait (wakes a job by waiting on record changes). Records are records, launching is launching — read once without waiting (seconds 0), or wake through a notification from the event infrastructure",
                self.subject()
            ),
        }
    }
}

/// The last segment of the head's spelling (`event-source.read-signal-handler` -> `read-signal-handler`).
fn last_segment(spelled: &str) -> &str {
    spelled.rsplit('.').next().unwrap_or(spelled)
}

/// The contents excluding forms discarded with `#_`.
fn visible(items: &[Form]) -> Vec<&Form> {
    items.iter().filter(|f| !matches!(f.node, Node::Discarded)).collect()
}

fn spelled<'s>(source: &'s str, form: &Form) -> &'s str {
    source.get(form.span.start..form.span.end).unwrap_or("")
}

/// The last segment of a call head symbol's spelling (None if not a call).
fn call_head<'s>(source: &'s str, form: &Form) -> Option<&'s str> {
    let items = form.paren_items()?;
    let head = visible(items).into_iter().next()?;
    matches!(head.node, Node::Symbol).then(|| last_segment(spelled(source, head)))
}

/// Whether it is a defhandler that answers record-change waits (the head of the clause directly under it is the wait effect).
fn answers_record_waits(source: &str, items: &[&Form]) -> bool {
    items.first().is_some_and(|head| spelled(source, head) == "defhandler")
        && items.iter().skip(2).any(|clause| call_head(source, clause).is_some_and(|h| RECORD_WAITS.contains(&h)))
}

fn is_keyword(source: &str, form: &Form, word: &str) -> bool {
    matches!(form.node, Node::Keyword) && spelled(source, form) == word
}

/// The position of the pattern in `(match subject pattern body pattern :if condition body …)` (index into the contents).
fn match_patterns(source: &str, items: &[&Form]) -> BTreeSet<usize> {
    if items.first().map(|head| spelled(source, head)) != Some("match") {
        return BTreeSet::new();
    }
    std::iter::successors(Some(2), |&at| Some(if items.get(at + 1).is_some_and(|f| is_keyword(source, f, ":if")) { at + 4 } else { at + 2 }))
        .take_while(|&at| at < items.len())
        .collect()
}

/// Whether the value of seconds is a literal 0 (`0`, `0.0`, `0.`, etc.).
fn literal_zero(source: &str, form: &Form) -> bool {
    matches!(form.node, Node::Number) && spelled(source, form).replace('_', "").parse::<f64>().is_ok_and(|v| v == 0.0)
}

/// From the arguments of a wait effect (after the head), returns the spelling of seconds that cannot be said to be 0 (None if 0, or the default 0 with no seconds written).
fn nonzero_timeout(source: &str, args: &[&Form]) -> Option<String> {
    // Skip keyword arguments together with their values, and list only the indices of positional arguments.
    let positional: Vec<usize> = std::iter::successors(Some(0), |&at| args.get(at).map(|f| if matches!(f.node, Node::Keyword) { at + 2 } else { at + 1 }))
        .take_while(|&at| at < args.len())
        .filter(|&at| !matches!(args[at].node, Node::Keyword))
        .collect();
    let keyword = args.iter().position(|f| is_keyword(source, f, ":timeout")).and_then(|at| args.get(at + 1).copied());
    let unpacked = args.iter().find(|f| matches!(f.node, Node::Prefixed { prefix: Prefix::Unpack | Prefix::UnpackMapping, .. }));
    let value = keyword.or_else(|| positional.get(TIMEOUT_POSITION).map(|&at| args[at]));
    match (value, unpacked) {
        (Some(value), _) => (!literal_zero(source, value)).then(|| spelled(source, value).to_string()),
        (None, Some(unpacked)) => Some(spelled(source, unpacked).to_string()),
        (None, None) => None,
    }
}

/// Hits in a form's tree (byte range and kind). Does not look inside a defhandler that answers record waits, or at match patterns.
fn hits(source: &str, form: &Form) -> Vec<(usize, usize, WaitUse)> {
    match &form.node {
        Node::Seq { items, .. } => {
            let items = visible(items);
            if answers_record_waits(source, &items) {
                return Vec::new();
            }
            let own = form.paren_items().and(items.first()).filter(|head| matches!(head.node, Node::Symbol)).and_then(|head| {
                let name = last_segment(spelled(source, head));
                let at = |what: WaitUse| (head.span.start, head.span.end, what);
                if SIGNAL_SOURCES.contains(&name) {
                    Some(at(WaitUse::SignalSource { factory: name.to_string() }))
                } else if RECORD_WAITS.contains(&name) {
                    nonzero_timeout(source, &items[1..]).map(|timeout| at(WaitUse::LongPoll { effect: name.to_string(), timeout }))
                } else {
                    None
                }
            });
            let patterns = match_patterns(source, &items);
            let inner = items.iter().enumerate().filter(|(at, _)| !patterns.contains(at)).flat_map(|(_, item)| hits(source, item));
            own.into_iter().chain(inner).collect()
        }
        Node::Prefixed { inner: Some(inner), .. } | Node::Tagged { inner: Some(inner) } => hits(source, inner),
        Node::Annotated { annotation, target } => [annotation, target].into_iter().flatten().flat_map(|part| hits(source, part)).collect(),
        Node::Prefixed { inner: None, .. } | Node::Tagged { inner: None } | Node::Symbol | Node::Keyword | Node::Str { .. } | Node::Number | Node::Discarded => {
            Vec::new()
        }
    }
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

/// Hits of one file (path from the root and contents).
pub fn judge(rel: &str, source: &str) -> Vec<WaitFinding> {
    let lines = LineIndex::new(source);
    let mut reader = Reader::new(source, 0, source.len());
    reader
        .read_all()
        .iter()
        .flat_map(|form| {
            let definition = definition_name(source, form);
            hits(source, form)
                .into_iter()
                .map(move |(start, end, what)| (definition.clone(), start, end, what))
                .collect::<Vec<_>>()
        })
        .map(|(definition, start, end, what)| WaitFinding { rel: rel.to_string(), range: lines.range(start, end), definition, what })
        .collect()
}

/// Whether it is a file in the population — production code (the `:business-fakes` declaration) and outside the package that owns the effects and sources.
fn in_population(rel: &str, decl: &BusinessFakes) -> bool {
    production_code(rel, decl) && !rel.split('/').any(|segment| segment == OWNER_PACKAGE_DIR)
}

/// Judges all Hy files of the repo's production code (hits, and reasons for unreadable files).
pub fn find(root: &Path, decl: &BusinessFakes) -> (Vec<WaitFinding>, Vec<String>) {
    let files: Vec<(String, std::path::PathBuf)> = hy_files::collect(root)
        .into_iter()
        .filter_map(|path| relative_path(root, &path).map(|rel| (rel, path)))
        .filter(|(rel, _)| in_population(rel, decl))
        .collect();
    let judged: Vec<Result<Vec<WaitFinding>, String>> = files
        .into_par_iter()
        .map(|(rel, path)| match std::fs::read_to_string(&path) {
            Ok(source) => Ok(judge(&rel, &source)),
            Err(error) => Err(format!("{}: cannot read: {}", rel, error)),
        })
        .collect();
    let (found, errors): (Vec<_>, Vec<_>) = judged.into_iter().partition(Result::is_ok);
    (found.into_iter().flat_map(Result::unwrap_or_default).collect(), errors.into_iter().filter_map(Result::err).collect())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn details(source: &str) -> Vec<String> {
        judge("x.hy", source).iter().map(WaitFinding::detail).collect()
    }

    #[test]
    fn signal_sources_are_hit_with_or_without_the_module_name() {
        let source = "(defk a [b] (with-handlers [(read-signal-handler B s)] b))\n(defk c [b] (with-handlers [(event-source.records-signal-handler B s)] b))\n\
                      (import doeff_records.event_source [read-signal-handler])\n";
        assert_eq!(details(source), vec!["a::read-signal-handler", "c::records-signal-handler"]);
    }

    #[test]
    fn waits_are_hit_unless_the_timeout_is_a_literal_zero() {
        let source = "(defk a [c left] (WatchChanges #(\"t\") c :timeout left))\n(defk b [c] (WatchEvents \"s\" c 5))\n\
                      (defk d [c] (WatchChanges #(\"t\") c :timeout 0.0))\n(defk e [c] (WatchEvents \"s\" 0 :timeout 0))\n\
                      (defk f [c] (WatchChanges #(\"t\") c))\n(defk g [c] (WatchChanges #(\"t\") c :limit 5 :timeout 0))\n\
                      (defk h [kw] (WatchChanges #** kw))\n(defk i [c] (WatchChanges #(\"t\") c #_ 5))\n";
        assert_eq!(details(source), vec!["a::WatchChanges", "b::WatchEvents", "h::WatchChanges"]);
        let found = judge("x.hy", source);
        assert_eq!(found[0].what, WaitUse::LongPoll { effect: "WatchChanges".into(), timeout: "left".into() });
        assert_eq!(found[0].range.start.line, 0);
    }

    #[test]
    fn handlers_that_answer_waits_and_match_patterns_are_not_hit() {
        let source = "(defhandler relay\n  (WatchChanges [tables cursor timeout limit] (resume (WatchChanges tables cursor :timeout timeout))))\n\
                      (defk waited [ask] (match ask (WatchChanges :timeout t) (float t) (WatchEvents :timeout t) :if (> t 0) t _ 0.0))\n\
                      (defhandler other\n  (Ping [] (resume (WatchEvents \"s\" 0 :timeout 3))))\n";
        assert_eq!(details(source), vec!["other::WatchEvents"]);
    }
}
