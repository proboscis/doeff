//! Semantic rules (DOEFF201, 202) — ask Jev (TypeSafe's System One model) about "what the code is doing", which deterministic rules cannot read.
//! How the target (URL, model, wire form, API key) is decided is a copy of doeff's packages/doeff-jev/src/doeff_jev/_target.py (resolve_target below);
//! the wire forms (direct = TypeSafe's /v1/systemone, gateway = Vercel AI Gateway's evaluation-model v4) are a copy of wire.py in the same package.
//!
//! - The question text (instructions, criteria) is declared in one place in this file (kept in English — same content as J2 and J3 in jev-lint's questions.py).
//!   For the DOEFF201, 202 and 205 questions, the repo's architecture.hy :semantic-lines (sentences for where a violation begins, plus firing and silent examples) are put
//!   into the `lines` of instructions (SemanticQuestion::wire_with — architecture.hy is the source of the sentences and examples).
//! - The gateway is called only for `--semantic` / `--semantic-all`. Runs of the deterministic rules (every editor save, hooks) only read the cache.
//! - A definition with no cached answer is not a violation; it is counted as "undecided" (never defaults to pass).
//! - Severity is only warning or info (no error until hit/miss rates are measured — it cannot be chosen in settings either).
//! - Calibration watch: every run that fires questions asks one known positive and one known negative example (data/semantic_calibration.json) once each; if the probability is outside the range, the cache is discarded and a warning is shown.
//! API key values are never written to settings, output, logs, or cache keys.
//!
//! Proxy that remembers Jev calls (repo proboscis/jev-proxy — moved from doeff's packages/doeff-jev-proxy on 2026-10-01):
//! - The target is pointed at by the per-repo setting `[tool.doeff-linter.semantic] proxy_url` (not a machine-wide environment variable — company repos are not pointed at it).
//!   If env JEV_BASE_URL is set, it wins. No Authorization header is sent to the proxy (nor the TypeSafe key — the proxy does not
//!   ask who the caller is; the proxy token was removed on 2026-10-02).
//! - A full run of the deterministic rules (hook, run without arguments) asks the proxy, "only if it remembers", about definitions missing from the local cache, and writes the returned answers into the local
//!   cache. The real Jev is not called. Questions are not sent per definition but in batches of proxy keys (proxy_key — sha256 of the normalized body)
//!   (POST <proxy_url>/peek, PEEK_BATCH at a time) — even a repo with thousands of definitions needs only a few round trips, and no body is sent.
//!   If the proxy is unreachable or times out (proxy_peek_timeout_ms; default DEFAULT_PROXY_PEEK_TIMEOUT_MS), only the local cache is used.
//! - Calibration watch questions do not use the proxy's memory (Cache-Control: no-cache — so the proxy's memory does not hide a change in the model itself).
//! - `--semantic-changed` asks only about definitions in the given files that have no answer in the local cache (= definitions whose content changed).
//!   Definitions containing a spot broken by the reader (work in progress) are never asked about in any run.

use std::collections::{BTreeMap, BTreeSet};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::{Duration, Instant};

use rayon::prelude::*;
use regex::Regex;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};

use super::layers::{LayerDescription, LayerId};

/// Definition of one boundary of a rule asked of Jev (`(line "name" :rules [DOEFF201 …] :text "…" :fires [example …] :silent [example …])` in `:semantic-lines`).
/// The sentence for where a violation begins, plus firing and silent examples, go into the instructions of that rule's question. The sentence and examples exist
/// only in the repo's declaration; the linter holds none (the single source is architecture.hy).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SemanticLine {
    /// Name of the boundary (appears in the question as a name — e.g. "line 1").
    pub name: String,
    /// The questions this boundary goes into (one of DOEFF201, 202, 205 — the closed set is SemanticQuestion::LINED).
    pub rules: Vec<SemanticQuestion>,
    /// Sentence of the boundary (must not be empty).
    pub text: String,
    /// Examples that are violations under this boundary (may be omitted).
    pub fires: Vec<LineExample>,
    /// Examples that are not violations under this boundary (may be omitted).
    pub silent: Vec<LineExample>,
}

/// One example of a boundary (`"<code>"` or `{:code "<code>" :why "…"}`). why = one sentence on why the example fires / stays silent, put into the question
/// paired with the code. With code alone, the example lost to the wording of the boundary sentence (send-task, list-limit-of and join-chat were listed in the question as examples that
/// should not fire, yet scored high; #1944).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LineExample {
    /// Code of the example (must not be empty).
    pub code: String,
    /// One sentence on why it fires / stays silent (None if not written — an empty sentence is a settings error).
    pub why: Option<String>,
}

/// Kind of semantic question (closed set).
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub enum SemanticQuestion {
    /// J2: whether the definition makes business decisions beyond rephrasing a request (applied to the protocol layer).
    BusinessDecision,
    /// J3: whether the definition knows the means of communication (applied to the core layer).
    TransportKnowledge,
    /// Choose, from architecture.hy's list of kinds plus none, the reason a deff truly has to be a plain function (Choice).
    PlainCallable,
    /// Whether a class with methods that do work is value / external-world / stateful / other (Choice, DOEFF204).
    ClassRole,
    /// Whether a definition whose role is judgment / program mixes checking the shape of input with business decisions (Choice, DOEFF205).
    MixedConcerns,
}

impl SemanticQuestion {
    /// All questions.
    pub const ALL: [SemanticQuestion; 2] = [SemanticQuestion::BusinessDecision, SemanticQuestion::TransportKnowledge];

    /// Questions into which :semantic-lines of architecture.hy can put boundaries (DOEFF201, 202, 205).
    pub const LINED: [SemanticQuestion; 3] = [SemanticQuestion::BusinessDecision, SemanticQuestion::TransportKnowledge, SemanticQuestion::MixedConcerns];

    /// Rule ID (DOEFF201 …).
    pub fn id(self) -> &'static str {
        match self {
            SemanticQuestion::BusinessDecision => "DOEFF201",
            SemanticQuestion::TransportKnowledge => "DOEFF202",
            SemanticQuestion::PlainCallable => "DOEFF203",
            SemanticQuestion::ClassRole => "DOEFF204",
            SemanticQuestion::MixedConcerns => "DOEFF205",
        }
    }

    /// Question with the boundaries put in — puts the name, sentence, firing examples and silent examples of the :semantic-lines entries matching this question into
    /// instructions, and writes how to read the boundaries and the direction of the answer into lines_note. An example is a `{"code", "why"}` pair (only examples with a why have it; #1995), and
    /// code is passed with :tags removed, as with the definition's source (so it is not dragged by the declaration).
    /// instructions is sent not as an object of keys but as a list of single-key objects in declaration order (#1995 — object keys sort by name, and for DOEFF205
    /// the general note came after the boundaries): the question's parts (in instruction_parts order) → lines_note → lines. The boundaries come last, so they win over the general sentences.
    /// If no boundary matches, it stays as wire() — the question and cache key of a repo with no declaration do not change.
    pub fn wire_with(self, lines: &[SemanticLine]) -> Value {
        let examples = |all: &[LineExample]| -> Vec<Value> {
            all.iter()
                .map(|example| match &example.why {
                    Some(why) => json!({"code": strip_tags(&example.code), "why": why}),
                    None => json!({"code": strip_tags(&example.code)}),
                })
                .collect()
        };
        let mine: Vec<Value> = lines
            .iter()
            .filter(|line| line.rules.contains(&self))
            .map(|line| {
                json!({
                    "name": line.name,
                    "text": line.text,
                    "violating_examples": examples(&line.fires),
                    "complying_examples": examples(&line.silent),
                })
            })
            .collect();
        let mut question = self.wire();
        let Some(note) = self.lines_note().filter(|_| !mine.is_empty()) else {
            return question;
        };
        let ordered: Vec<Value> = self
            .instruction_parts()
            .into_iter()
            .chain([("lines_note", Value::String(note.to_string())), ("lines", Value::Array(mine))])
            .map(|(name, content)| Value::Object(serde_json::Map::from_iter([(name.to_string(), content)])))
            .collect();
        question["instructions"] = Value::Array(ordered);
        question
    }

    /// Parts of the instructions of the questions that take boundaries (LINED), in declaration order (key, content — the question text is in English, in this one place). wire() sends the parts as an object of keys,
    /// and wire_with sends them as a list in this order.
    /// The instructions of questions that take no boundaries (DOEFF203, 204) live in one place in wire(); this is empty for them.
    fn instruction_parts(self) -> Vec<(&'static str, Value)> {
        match self {
            SemanticQuestion::BusinessDecision => vec![
                ("question", json!("Does the code in `definition.source` make a business decision, beyond rephrasing a request into the other party's way of talking?")),
                (
                    "business_decision_examples",
                    json!([
                        "deciding who is allowed to do something",
                        "enforcing a domain rule such as which participants a chat may have or where a reply may be posted",
                        "choosing recipients or a business outcome"
                    ]),
                ),
                (
                    "not_business_decision_examples",
                    json!([
                        "building URLs, headers or request bodies",
                        "parsing JSON, HTTP status codes or process output into typed values",
                        "turning transport failures into error values",
                        "retry and timeout policy",
                        "checking the shape of a payload or reading configuration"
                    ]),
                ),
            ],
            // The frame asks about two things with equal weight: the means of communication, and the untyped shape of outside data (dicts, reading or building JSON values by field name). The previous frame
            // covered only the means of communication, so even adding a field-name read that does not write json.loads via the repo's boundary and examples did not spread to other definitions of similar shape (#2044, #2052).
            // A default for a field is merely counted within "reading by field name"; who is responsible for falling back to the default is not stated (that is the DOEFF201 judgment question).
            SemanticQuestion::TransportKnowledge => vec![
                (
                    "question",
                    json!("Does the code in `definition.source` know either of these two things, which belong outside the core: (a) how communication is carried out: URLs or URL paths and query strings, HTTP methods, status codes or headers, JSON encoding and decoding, SQL, or network endpoint addresses; or (b) the untyped shape of outside data: reading fields of an untyped dict or JSON value by field name, parsing such a value into typed values, or building a dict by field names to become a JSON value?"),
                ),
                (
                    "note",
                    json!("(b) weighs the same as (a). Reading, parsing and building untyped dicts or JSON values by field name is shape checking, the protocol side's job; the core receives and returns only typed values. (b) counts whether or not the code calls json.loads or json.dumps: the value may have been decoded elsewhere and passed in as a dict, or be encoded elsewhere after the code builds it. A default for a missing field does not make the read typed; the code still reads the field by name. TOML, YAML and other document formats count the same as JSON. A dict that is not outside data does not count: a constant table in the code, or a tally the definition builds from typed values."),
                ),
                (
                    "transport_knowledge_examples",
                    json!([
                        "sending an HTTP request: a URL built from a base and a route, headers, and a body encoded with json.dumps",
                        "taking a dict that was decoded elsewhere (no json.loads in the code) and reading `payload[\"body\"]`, `(.get payload \"from\" \"\")` or `(get row \"accountKey\")` to build a typed value or to choose one",
                        "building `{\"name\" name \"state\" state}` by field names and passing it on to be stored or sent as JSON"
                    ]),
                ),
                (
                    "not_transport_knowledge_examples",
                    json!([
                        "a core function that receives typed values (records, enums, tuples of records), decides by their attributes with a business rule, and returns a typed value; the protocol side turned the outside data into those values and turns the result back",
                        "asking an outside system through a business-level effect or port, such as `(<- reply (SendMail draft))`, without its URL, field names or encoding"
                    ]),
                ),
            ],
            // The note is the same sentence as the exception of boundary 4 (agora-controllers' architecture.hy) (#1995 — the previous note called isinstance and dict reads shape checking without exception, which
            // contradicted the boundary: _on-text that branches on union cases, list-nodes on a dict of a mock world, and open-chat-room that refuses with the answer of a delegated check scored high).
            SemanticQuestion::MixedConcerns => vec![
                ("question", json!("What does the definition in `definition.source` do? `layer` describes what code in its layer should know and not know.")),
                (
                    "note",
                    json!("Shape checking means validating untyped input that came from outside: reading keys out of dicts or JSON payloads, isinstance checks, and empty, missing, length, format or vocabulary checks on those raw values before they can be used. These are not shape checking: branching on the cases of a typed union (for example isinstance between the declared alternatives of `A | B | None`); reading an untyped dict that did not come from outside (a constant in the code, or the definition's own tally); and a branch that only returns a refusal value built from the answer (the problem text) of one function that the shape check was left to. Business judgment means deciding by business rules (who may do what, which outcome, which write). Comparing already-typed values by a business rule is judgment, not shape checking."),
                ),
            ],
            SemanticQuestion::PlainCallable | SemanticQuestion::ClassRole => Vec::new(),
        }
    }

    /// The instructions with the parts made into an object of keys.
    fn instructions_of(parts: Vec<(&'static str, Value)>) -> Value {
        Value::Object(parts.into_iter().map(|(name, content)| (name.to_string(), content)).collect())
    }

    /// How to read the boundaries and the direction of the answer (the question text is in English, in this one place). None for questions that take no boundaries.
    fn lines_note(self) -> Option<&'static str> {
        match self {
            SemanticQuestion::BusinessDecision => Some(
                "`lines` are the boundaries this repository adopted for this question; where they differ from the general examples above, follow the lines. Code like a line's `violating_examples` makes a business decision (answer true); code like its `complying_examples` does not (answer false). An example's `why` says what in the line decides it; judge the definition by that reason, not by how much its code looks like the example.",
            ),
            SemanticQuestion::TransportKnowledge => Some(
                "`lines` are the boundaries this repository adopted for this question; where they differ from the general wording above, follow the lines. Code like a line's `violating_examples` knows how communication is carried out (answer true); code like its `complying_examples` does not (answer false). An example's `why` says what in the line decides it; judge the definition by that reason, not by how much its code looks like the example.",
            ),
            SemanticQuestion::MixedConcerns => Some(
                "`lines` are the boundaries this repository adopted for this question; where they differ from the note above, follow the lines. Code like a line's `violating_examples` is `mixed`; code like its `complying_examples` is not `mixed`. An example's `why` says what in the line decides it; judge the definition by that reason, not by how much its code looks like the example.",
            ),
            SemanticQuestion::PlainCallable | SemanticQuestion::ClassRole => None,
        }
    }

    /// The DOEFF203 question (Choice). criteria are the acceptable-reason and unacceptable-reason kinds of architecture.hy (name → description) plus none. The question text is in English, in this one place.
    pub fn plain_callable_wire(accepted: &[(String, String)], rejected: &[(String, String)]) -> Value {
        let mut criteria = serde_json::Map::new();
        for (name, description) in accepted {
            criteria.insert(name.clone(), Value::String(format!("Acceptable reason: {}", description)));
        }
        for (name, description) in rejected {
            criteria.insert(name.clone(), Value::String(format!("Not an acceptable reason: {}", description)));
        }
        criteria.insert(
            "none".to_string(),
            Value::String("No listed reason fits, and the stated reason does not show that code outside doeff must call this definition as a plain function; it could be a doeff Program (defk).".to_string()),
        );
        json!({
            "type": "choice",
            "instructions": {
                "question": "Is the reason in `stated_reason` an acceptable reason for the definition in `definition.source` to be a plain Python callable (deff) instead of a doeff Program (defk)? Choose the listed reason that actually describes the situation, judged by the code and by who calls it.",
                "note": "A plain callable is acceptable only when code outside doeff calls it directly with a fixed signature that cannot run a Program. Reading configuration or environment variables, test helpers, and assembling handler lists are not acceptable: those can be doeff Programs (Ask and other effects, deftest, a defk that returns the handlers)."
            },
            "criteria": Value::Object(criteria)
        })
    }

    /// The question passed to Jev (direct form — type is noul. It is mapped to the gateway form by gateway_question). Same content as J2 and J3 in jev-lint's questions.py.
    pub fn wire(self) -> Value {
        match self {
            SemanticQuestion::BusinessDecision => json!({
                "type": "noul",
                "instructions": Self::instructions_of(self.instruction_parts()),
                "criteria": {
                    "true": "The code decides a business matter (permission, domain rule, recipient, business outcome).",
                    "false": "The code only translates: it builds or reads the wire form, maps failures, or checks shapes."
                }
            }),
            SemanticQuestion::PlainCallable => Self::plain_callable_wire(&[], &[]),
            SemanticQuestion::MixedConcerns => json!({
                "type": "choice",
                "instructions": Self::instructions_of(self.instruction_parts()),
                "criteria": {
                    "mixed": "It both checks the shape of untyped input and makes business decisions in the same definition.",
                    "shape-only": "It only checks or parses the shape of untyped input; it makes no business decision.",
                    "judgment-only": "It only makes business decisions from typed values; it does not check the shape of untyped input.",
                    "neither": "None of the above (for example plain data plumbing or formatting)."
                }
            }),
            SemanticQuestion::ClassRole => json!({
                "type": "choice",
                "instructions": {
                    "question": "What is the class in `definition.source` in a doeff (algebraic effects) Hy code base? `definition.fields` lists its declared fields with their type annotations.",
                    "note": "Judge by what the methods do with the fields, not by the class name. A class that only receives a client, store, connection or session from outside and calls it in its methods is external-world even when no raw I/O call is visible."
                },
                "criteria": {
                    "value": "A value class: methods only compute new values from its own fields (like Point2D.norm or add); no fields are changed and no outside system is reached.",
                    "external-world": "A window to the outside world: it holds a client, store, connection, file handle or session (usually passed in from outside) as a field and its methods use it to read or write the outside world.",
                    "stateful": "It holds changing state: its methods change its own fields or change the state of another object passed to it (append, update, assignment).",
                    "other": "None of the above (for example a protocol adapter required by a library, or a pure namespace of helpers)."
                }
            }),
            SemanticQuestion::TransportKnowledge => json!({
                "type": "noul",
                "instructions": Self::instructions_of(self.instruction_parts()),
                "criteria": {
                    "true": "The code knows (a) or (b): for example a URL, a query string, an HTTP request or status code, json.loads or json.dumps of a wire body, an endpoint URL field, or fields of an untyped dict or JSON value read or built by field name, with or without json.loads.",
                    "false": "The code only receives, decides with and returns typed values and business-level request effects; naming an outside system without its transport details or its field names does not count."
                }
            }),
        }
    }

    /// Meaning of the question (Japanese inserted into the explanation text).
    pub fn meaning(self) -> &'static str {
        match self {
            SemanticQuestion::BusinessDecision => "makes business decisions beyond rephrasing the request (who is allowed, business rules, recipients, business outcomes)",
            SemanticQuestion::TransportKnowledge => "knows the means of communication (URLs and queries, HTTP methods and statuses, JSON wire form, SQL, endpoint addresses) or the untyped shape of outside data (reading or building dicts / JSON values by field name)",
            SemanticQuestion::PlainCallable => "the stated reason kind is not likely to justify a plain function",
            SemanticQuestion::ClassRole => "a class with methods that do work is likely a window to the outside world or something that holds state",
            SemanticQuestion::MixedConcerns => "a judgment definition likely mixes checking the shape of input with business decisions",
        }
    }
}

/// Settings of one question in `[tool.doeff-linter.semantic]` (as read).
#[derive(Debug, Deserialize, Serialize, Default, Clone)]
pub struct QuestionSection {
    /// Names of the layers the question applies to.
    #[serde(default)]
    pub layers: Vec<String>,
    /// warning at or above this probability.
    pub warning: Option<f64>,
    /// info at or above this probability.
    pub info: Option<f64>,
    /// A field that cannot be set (Jev's judgment is never an error) — read so that, if written, it is a settings error rather than skipped as an unknown key.
    pub error: Option<f64>,
}

/// `[tool.doeff-linter.semantic]` (as read). Severity is only warning and info; there is no error field. The target, model and key are not written here
/// (same decision as doeff-jev — env JEV_* and ~/.config/jev/client.json). The exception is proxy_url, the target of the proxy that remembers Jev calls (pointed per repo).
#[derive(Debug, Deserialize, Serialize, Default, Clone)]
pub struct SemanticSection {
    #[serde(default)]
    pub business_decision: QuestionSection,
    #[serde(default)]
    pub transport_knowledge: QuestionSection,
    /// DOEFF203: threshold for accepting a reason (warning / info by the probability of a non-accepting answer).
    pub plain_callable: Option<PlainCallableSection>,
    /// DOEFF204: threshold for the probability that Jev chose external-world / stateful (warning_min, info_min).
    pub class_role: Option<PlainCallableSection>,
    /// DOEFF205: role of the definitions to ask about, the layer used as a yardstick, and thresholds.
    pub mixed_concerns: Option<MixedConcernsSection>,
    pub workers: Option<usize>,
    pub timeout_seconds: Option<u64>,
    pub source_limit: Option<usize>,
    /// Target of the proxy that remembers Jev calls (e.g. http://jev-proxy.example:8878/v1/systemone). If absent, no proxy is used.
    pub proxy_url: Option<String>,
    /// Time limit for "only if remembered" questions (ms; default 5000 — the limit for all batches combined).
    pub proxy_peek_timeout_ms: Option<u64>,
    /// Directory of the false-positive list (relative to the repo root; one file per key; line 2 onward is the human's reason for the judgment). Jev hits listed there are not shown and not counted.
    #[serde(default)]
    pub false_positives: Vec<String>,
    /// Directory of the list of hits a human judged to be "real violations" (same form as the false-positive list). It does not change how violations are shown; it is read as positive examples for measuring hits and misses.
    #[serde(default)]
    pub true_positives: Vec<String>,
}

/// `[tool.doeff-linter.semantic] mixed_concerns` (as read).
#[derive(Debug, Deserialize, Serialize, Default, Clone)]
pub struct MixedConcernsSection {
    /// Role of the definitions to ask about (the tag's :role — default judgment, program).
    #[serde(default)]
    pub roles: Vec<String>,
    /// Layer used as a yardstick (that layer's description is passed to Jev — e.g. core).
    pub layer: String,
    /// Warning when Jev chooses mixed and its probability is at least this (default 0.7).
    pub warning_min: Option<f64>,
    /// Info at or above this (default 0.5).
    pub info_min: Option<f64>,
}

/// DOEFF205 settings (after validation).
#[derive(Debug, Clone)]
pub struct MixedConcernsSettings {
    pub roles: BTreeSet<String>,
    pub layer: LayerId,
    pub warning_min: f64,
    pub info_min: f64,
}

/// Proxy settings (after validation).
#[derive(Debug, Clone)]
pub struct ProxySettings {
    pub url: String,
    pub peek_timeout: Duration,
}

/// Default time limit for "only if remembered" questions (ms — the limit for all batches combined; long enough to send thousands of keys from a machine on a slow network).
pub const DEFAULT_PROXY_PEEK_TIMEOUT_MS: u64 = 5000;
/// Number of keys in one "only if remembered" batch (within the proxy's limit of 20000 — one key is about 67 bytes).
pub const PEEK_BATCH: usize = 1000;
/// Version of the proxy's key rules (same as KEY-VERSION in proboscis/jev-proxy's src/doeff_jev_proxy/key.hy).
pub const PROXY_KEY_VERSION: &str = "jev-proxy-key-1";

/// `[tool.doeff-linter.semantic] plain_callable` (as read).
#[derive(Debug, Deserialize, Serialize, Default, Clone)]
pub struct PlainCallableSection {
    /// Warning when Jev chooses a non-accepting answer (a non-accepting kind or none) and its probability is at least this (default 0.4).
    pub warning_min: Option<f64>,
    /// Info when the sum of the probabilities of non-accepting answers is at least this even if an accepting answer was chosen (default 0.4).
    pub info_min: Option<f64>,
}

/// DOEFF203 settings (after validation).
#[derive(Debug, Clone, Copy)]
pub struct PlainCallableSettings {
    pub warning_min: f64,
    pub info_min: f64,
}

/// Settings of one question (after validation).
#[derive(Debug, Clone)]
pub struct QuestionSettings {
    pub layers: BTreeSet<LayerId>,
    pub warning: f64,
    pub info: f64,
}

/// Settings of the semantic rules (after validation).
#[derive(Debug, Clone)]
pub struct SemanticSettings {
    pub plain_callable: Option<PlainCallableSettings>,
    pub class_role: Option<PlainCallableSettings>,
    pub mixed_concerns: Option<MixedConcernsSettings>,
    pub questions: BTreeMap<SemanticQuestion, QuestionSettings>,
    pub workers: usize,
    pub timeout: Duration,
    pub source_limit: usize,
    /// Proxy that remembers Jev calls (not used if absent).
    pub proxy: Option<ProxySettings>,
    /// Directory of the false-positive list (relative to the repo root).
    pub false_positives: Vec<String>,
    /// Directory of the list of hits a human judged to be real violations (relative to the repo root).
    pub true_positives: Vec<String>,
    /// Boundaries put into Jev's questions (architecture.hy :semantic-lines — empty if absent, and the questions stay as they are).
    /// Not written in the TOML section; the settings assembly (config.rs) pulls them in from architecture.hy — the sentences are defined in one place, architecture.hy.
    pub lines: Vec<SemanticLine>,
}


impl SemanticSettings {
    /// Validate the sections as read (layer names are looked up with find; thresholds are 0 to 1 with info ≤ warning).
    pub fn validate(section: &SemanticSection, find: &mut dyn FnMut(&str, &str) -> Option<LayerId>, problems: &mut Vec<String>) -> SemanticSettings {
        let mut questions = BTreeMap::new();
        for (question, part, name, (warning, info)) in [
            (SemanticQuestion::BusinessDecision, &section.business_decision, "business_decision", (0.8, 0.6)),
            (SemanticQuestion::TransportKnowledge, &section.transport_knowledge, "transport_knowledge", (0.6, 0.4)),
        ] {
            let layers: BTreeSet<LayerId> =
                part.layers.iter().filter_map(|layer| find(layer, &format!("semantic.{}.layers", name))).collect();
            if part.error.is_some() {
                problems.push(format!("semantic.{}.error: Jev's judgment severity is only warning and info (never error)", name));
            }
            let warning = part.warning.unwrap_or(warning);
            let info = part.info.unwrap_or(info);
            if !(0.0..=1.0).contains(&warning) || !(0.0..=1.0).contains(&info) || info > warning {
                problems.push(format!("semantic.{}: thresholds are 0 to 1 with info ≤ warning (warning = {}, info = {})", name, warning, info));
            }
            if !layers.is_empty() {
                questions.insert(question, QuestionSettings { layers, warning, info });
            }
        }
        let mut thresholds = |section: Option<&PlainCallableSection>, name: &str, warning: f64, info: f64| {
            section.map(|p| {
                let settings = PlainCallableSettings { warning_min: p.warning_min.unwrap_or(warning), info_min: p.info_min.unwrap_or(info) };
                if !(0.0..=1.0).contains(&settings.warning_min) || !(0.0..=1.0).contains(&settings.info_min) {
                    problems.push(format!("semantic.{}: thresholds are 0 to 1 (warning_min = {}, info_min = {})", name, settings.warning_min, settings.info_min));
                }
                settings
            })
        };
        let plain_callable = thresholds(section.plain_callable.as_ref(), "plain_callable", 0.4, 0.4);
        let class_role = thresholds(section.class_role.as_ref(), "class_role", 0.7, 0.5);
        let mixed_concerns = section.mixed_concerns.as_ref().and_then(|m| {
            let layer = find(&m.layer, "semantic.mixed_concerns.layer")?;
            let settings = MixedConcernsSettings {
                roles: if m.roles.is_empty() { ["judgment", "program"].iter().map(|r| r.to_string()).collect() } else { m.roles.iter().cloned().collect() },
                layer,
                warning_min: m.warning_min.unwrap_or(0.7),
                info_min: m.info_min.unwrap_or(0.5),
            };
            if !(0.0..=1.0).contains(&settings.warning_min) || !(0.0..=1.0).contains(&settings.info_min) || settings.info_min > settings.warning_min {
                problems.push(format!(
                    "semantic.mixed_concerns: thresholds are 0 to 1 with info_min ≤ warning_min (warning_min = {}, info_min = {})",
                    settings.warning_min, settings.info_min
                ));
            }
            Some(settings)
        });
        let proxy = section.proxy_url.as_ref().and_then(|url| {
            if !(url.starts_with("http://") || url.starts_with("https://")) {
                problems.push(format!("semantic.proxy_url must be an http:// or https:// URL: {}", url));
                return None;
            }
            Some(ProxySettings {
                url: url.clone(),
                peek_timeout: Duration::from_millis(section.proxy_peek_timeout_ms.unwrap_or(DEFAULT_PROXY_PEEK_TIMEOUT_MS)),
            })
        });
        if section.proxy_url.is_none() && section.proxy_peek_timeout_ms.is_some() {
            problems.push("semantic.proxy_peek_timeout_ms must be written together with proxy_url".to_string());
        }
        SemanticSettings {
            proxy,
            false_positives: section.false_positives.clone(),
            true_positives: section.true_positives.clone(),
            lines: Vec::new(),
            plain_callable,
            class_role,
            mixed_concerns,
            questions,
            workers: section.workers.unwrap_or(8).clamp(1, 32),
            timeout: Duration::from_secs(section.timeout_seconds.unwrap_or(30)),
            source_limit: section.source_limit.unwrap_or(1800),
        }
    }

    /// DOEFF203: decide severity — warning if a non-accepting answer is chosen with probability at least warning_min; info if a non-accepting answer is chosen below that, or
    /// if, even with an accepting answer chosen, the sum of non-accepting probabilities is at least info_min. Never error.
    pub fn plain_callable_severity(&self, chosen_accepted: bool, chosen_probability: f64, rejected_total: f64) -> Option<crate::models::Severity> {
        let spec = self.plain_callable?;
        match chosen_accepted {
            false if chosen_probability >= spec.warning_min => Some(crate::models::Severity::Warning),
            false => Some(crate::models::Severity::Info),
            true if rejected_total >= spec.info_min => Some(crate::models::Severity::Info),
            true => None,
        }
    }

    /// DOEFF204: decide severity from the answer Jev chose and its probability — external-world / stateful gives warning at warning_min or more, info at info_min or more.
    /// value and other are not shown. Never error.
    pub fn class_role_severity(&self, chosen: &str, probability: f64) -> Option<crate::models::Severity> {
        let spec = self.class_role?;
        match chosen {
            "external-world" | "stateful" if probability >= spec.warning_min => Some(crate::models::Severity::Warning),
            "external-world" | "stateful" if probability >= spec.info_min => Some(crate::models::Severity::Info),
            _ => None,
        }
    }

    /// DOEFF205: decide severity from the probability that Jev chose mixed (other answers are not shown; never error).
    pub fn mixed_concerns_severity(&self, chosen: &str, probability: f64) -> Option<crate::models::Severity> {
        let spec = self.mixed_concerns.as_ref()?;
        match chosen {
            "mixed" if probability >= spec.warning_min => Some(crate::models::Severity::Warning),
            "mixed" if probability >= spec.info_min => Some(crate::models::Severity::Info),
            _ => None,
        }
    }

    /// Decide severity from a probability (None if it does not reach the thresholds).
    pub fn severity(&self, question: SemanticQuestion, probability: f64) -> Option<crate::models::Severity> {
        let spec = self.questions.get(&question)?;
        if probability >= spec.warning {
            Some(crate::models::Severity::Warning)
        } else if probability >= spec.info {
            Some(crate::models::Severity::Info)
        } else {
            None
        }
    }
}

/// Definition for one question (state and cache key).
#[derive(Debug, Clone)]
pub struct SemanticItem {
    pub question: SemanticQuestion,
    /// The question JSON passed to Jev (DOEFF203 includes the list of reason kinds).
    pub question_json: Value,
    /// DOEFF203: the reason kind stated (None for other questions).
    pub declared: Option<String>,
    pub rel: String,
    pub path: PathBuf,
    pub name: String,
    pub kind: &'static str,
    pub range: doeff_indexer::hy_index::Range,
    pub layer: LayerId,
    pub state: Value,
    pub key: String,
    /// Whether the definition's source is not broken by the reader (i.e. not work in progress) — broken definitions are not asked about.
    pub readable: bool,
}

/// Whether the definition's source has no spot broken by the Hy reader (unclosed parenthesis, unmatched closer, unclosed string).
pub fn readable(source: &str) -> bool {
    let mut reader = doeff_indexer::hy_index::reader::Reader::new(source, 0, source.len());
    let forms = reader.read_all();
    !forms.is_empty() && reader.issues.is_empty()
}

/// Remove the declaration's :tags from the source (showing the declaration drags Jev — same as TAGS in jev-lint).
fn strip_tags(source: &str) -> String {
    static TAGS: std::sync::OnceLock<Regex> = std::sync::OnceLock::new();
    TAGS.get_or_init(|| Regex::new(r"\s*:tags\s+\{[^}]*\}").unwrap_or_else(|_| Regex::new("$^").unwrap())).replace_all(source, "").into_owned()
}

/// Truncate by character count (at a char boundary).
fn truncate(text: &str, limit: usize) -> String {
    text.chars().take(limit).collect()
}

/// JSON in a fixed spelling (for the cache key — object keys are sorted).
fn canonical(value: &Value) -> String {
    match value {
        Value::Object(map) => {
            let mut keys: Vec<&String> = map.keys().collect();
            keys.sort();
            let parts: Vec<String> = keys.into_iter().map(|k| format!("{}:{}", Value::String(k.clone()), canonical(&map[k]))).collect();
            format!("{{{}}}", parts.join(","))
        }
        Value::Array(items) => format!("[{}]", items.iter().map(canonical).collect::<Vec<_>>().join(",")),
        other => other.to_string(),
    }
}

/// Proxy key of the question body (64 lowercase hex digits) = sha256(PROXY_KEY_VERSION + "\n" + body in the fixed spelling). Yields the same key as the proxy's rules (proboscis/jev-proxy
/// src/doeff_jev_proxy/key.hy normalize-request — object keys sorted by code point, no separating whitespace,
/// characters kept as UTF-8). The body must have a model (the proxy adds a default name to a body without a model before spelling it). Decimals are spelled
/// differently per language, so they would not yield the same key (it just misses and never hits the answer of another question — the linter's body has no decimals).
/// That the keys match is confirmed by the checks of both repos reading the proxy's sample (source of truth = tests/key_contract.json in proboscis/jev-proxy; copy = this package's
/// tests/proxy_key_contract.json).
pub fn proxy_key(body: &Value) -> String {
    let mut hasher = Sha256::new();
    hasher.update(PROXY_KEY_VERSION.as_bytes());
    hasher.update(b"\n");
    hasher.update(canonical(body).as_bytes());
    hasher.finalize().iter().map(|b| format!("{:02x}", b)).collect()
}

/// Build the state and cache key of one definition. Key = sha256(model, question JSON (with boundaries put in), layer description, source with tags removed). The declared role is not part of the key.
#[allow(clippy::too_many_arguments)]
pub fn item(
    settings: &SemanticSettings,
    model: &str,
    question: SemanticQuestion,
    rel: &str,
    path: &Path,
    name: &str,
    kind: &'static str,
    range: doeff_indexer::hy_index::Range,
    source: &str,
    layer: LayerId,
    layer_name: &str,
    description: &LayerDescription,
) -> SemanticItem {
    let stripped = truncate(&strip_tags(source), settings.source_limit);
    let layer_json = json!({
        "name": layer_name,
        "summary": description.summary,
        "knows": description.knows,
        "does_not_know": description.does_not_know,
        "test": description.question,
    });
    let state = json!({
        "definition": {"name": name, "kind": kind, "file": rel, "source": stripped},
        "layer": layer_json,
    });
    let question_json = question.wire_with(&settings.lines);
    let mut hasher = Sha256::new();
    for part in [model.to_string(), canonical(&question_json), canonical(&layer_json), stripped] {
        hasher.update(part.as_bytes());
        hasher.update(b"\n");
    }
    let key = hasher.finalize().iter().map(|b| format!("{:02x}", b)).collect();
    SemanticItem { question, question_json, declared: None, rel: rel.to_string(), path: path.to_path_buf(), name: name.to_string(), kind, range, layer, state, key, readable: readable(source) }
}

/// Build the state and cache key of one DOEFF203 definition (state = definition and the stated reason. Key = sha256(model, question JSON, state)).
#[allow(clippy::too_many_arguments)]
pub fn plain_callable_item(
    settings: &SemanticSettings,
    model: &str,
    accepted: &[(String, String)],
    rejected: &[(String, String)],
    rel: &str,
    path: &Path,
    name: &str,
    kind: &'static str,
    range: doeff_indexer::hy_index::Range,
    source: &str,
    stated_kind: Option<&str>,
    stated: &str,
) -> SemanticItem {
    let stripped = truncate(&strip_tags(source), settings.source_limit);
    let question_json = SemanticQuestion::plain_callable_wire(accepted, rejected);
    let state = json!({
        "definition": {"name": name, "kind": kind, "file": rel, "source": stripped},
        "stated_reason": {"text": stated, "kind": stated_kind},
    });
    let mut hasher = Sha256::new();
    for part in [model.to_string(), canonical(&question_json), canonical(&state)] {
        hasher.update(part.as_bytes());
        hasher.update(b"\n");
    }
    let key = hasher.finalize().iter().map(|b| format!("{:02x}", b)).collect();
    SemanticItem {
        question: SemanticQuestion::PlainCallable,
        question_json,
        declared: stated_kind.map(str::to_string),
        rel: rel.to_string(),
        path: path.to_path_buf(),
        name: name.to_string(),
        kind,
        range,
        layer: LayerId(0),
        state,
        key,
        readable: readable(source),
    }
}

/// Build the state and cache key of one DOEFF204 class (state = the class's source (tags removed, truncated) and the field declarations).
#[allow(clippy::too_many_arguments)]
pub fn class_item(
    settings: &SemanticSettings,
    model: &str,
    rel: &str,
    path: &Path,
    name: &str,
    range: doeff_indexer::hy_index::Range,
    source: &str,
    fields: &[String],
) -> SemanticItem {
    let stripped = truncate(&strip_tags(source), settings.source_limit);
    let question = SemanticQuestion::ClassRole;
    let question_json = question.wire();
    let state = json!({
        "definition": {"name": name, "kind": "defclass", "file": rel, "source": stripped, "fields": fields},
    });
    let mut hasher = Sha256::new();
    for part in [model.to_string(), canonical(&question_json), canonical(&state)] {
        hasher.update(part.as_bytes());
        hasher.update(b"\n");
    }
    let key = hasher.finalize().iter().map(|b| format!("{:02x}", b)).collect();
    SemanticItem {
        question,
        question_json,
        declared: None,
        rel: rel.to_string(),
        path: path.to_path_buf(),
        name: name.to_string(),
        kind: "defclass",
        range,
        layer: LayerId(0),
        state,
        key,
        readable: readable(source),
    }
}

/// Build the state and cache key of one DOEFF205 definition (state = the definition's source (tags removed, truncated) and the description of the yardstick layer; the question is the one with boundaries put in).
#[allow(clippy::too_many_arguments)]
pub fn mixed_item(
    settings: &SemanticSettings,
    model: &str,
    rel: &str,
    path: &Path,
    name: &str,
    kind: &'static str,
    range: doeff_indexer::hy_index::Range,
    source: &str,
    layer: LayerId,
    layer_name: &str,
    description: &LayerDescription,
) -> SemanticItem {
    let stripped = truncate(&strip_tags(source), settings.source_limit);
    let question = SemanticQuestion::MixedConcerns;
    let question_json = question.wire_with(&settings.lines);
    let state = json!({
        "definition": {"name": name, "kind": kind, "file": rel, "source": stripped},
        "layer": {"name": layer_name, "summary": description.summary, "knows": description.knows, "does_not_know": description.does_not_know},
    });
    let mut hasher = Sha256::new();
    for part in [model.to_string(), canonical(&question_json), canonical(&state)] {
        hasher.update(part.as_bytes());
        hasher.update(b"\n");
    }
    let key = hasher.finalize().iter().map(|b| format!("{:02x}", b)).collect();
    SemanticItem { question, question_json, declared: None, rel: rel.to_string(), path: path.to_path_buf(), name: name.to_string(), kind, range, layer, state, key, readable: readable(source) }
}

/// The probability compared with thresholds — the calibration watch and the comparison with human judgments (`labeled`) both use this one. Noul uses the probability of the answer; a choice question uses
/// the probability of the target word (DOEFF204 = external-world, DOEFF205 = mixed). The `probability` of a choice answer is the probability of the chosen word, so using it as is would
/// make an answer of judgment-only 0.97 / mixed 0.02 look like 0.97 (#1994 — the DOEFF205 threshold table was built on this misreading).
pub fn target_probability(question: SemanticQuestion, answer: &Answer) -> f64 {
    match question {
        SemanticQuestion::ClassRole => answer.probabilities.as_ref().and_then(|p| p.get("external-world").copied()).unwrap_or(0.0),
        SemanticQuestion::MixedConcerns => answer.probabilities.as_ref().and_then(|p| p.get("mixed").copied()).unwrap_or(0.0),
        SemanticQuestion::BusinessDecision | SemanticQuestion::TransportKnowledge | SemanticQuestion::PlainCallable => answer.probability,
    }
}

/// Jev's answer (probability, cost in USD and token counts reported in the answer, and the model that answered). The form written to the local cache.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Answer {
    pub probability: f64,
    /// Cost reported in the answer (USD) — Some only when upstream put it in the answer (Vercel AI Gateway's providerMetadata.gateway.cost). Direct TypeSafe does not
    /// report it, so it is None, distinguished from 0. The old cache field cost_usd wrote 0 even when nothing was reported, so it is not read
    /// (reading it would mix "cost 0" with "unknown" — old answers are read as unknown).
    #[serde(default)]
    pub reported_cost_usd: Option<f64>,
    /// Input token count (Some only when present in the answer's usage — an answer without it is not written as 0).
    #[serde(default)]
    pub input_tokens: Option<u64>,
    /// Output token count (Some only when present in the answer's usage — material for estimating cost by multiplying by unit prices; #1892 plan B).
    #[serde(default)]
    pub output_tokens: Option<u64>,
    /// Versioned name of the model that answered (the model of a direct answer — e.g. jev-1.13.0; the gateway does not return it).
    #[serde(default)]
    pub served_model: Option<String>,
    /// The Choice answer (the chosen kind).
    #[serde(default)]
    pub choice: Option<String>,
    /// Probability per Choice kind.
    #[serde(default)]
    pub probabilities: Option<BTreeMap<String, f64>>,
}

/// How to use the proxy's memory (the Cache-Control header to the proxy). Nothing is added for a target that is not a proxy.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Freshness {
    /// If remembered, the remembered answer; otherwise the real Jev (no header).
    Remembered,
    /// Do not use the memory and ask the real Jev again (no-cache — calibration watch).
    Fresh,
}

/// Result of a batch of "only if remembered" questions.
#[derive(Debug, Clone, PartialEq)]
pub enum PeekedMany {
    /// Answers the proxy remembered (proxy key → answer — keys not remembered are absent; the real Jev was not called).
    Remembered(BTreeMap<String, Answer>),
    /// The proxy is unreachable, timed out, the target is not a proxy, or an answer is unreadable (the reason).
    Unreachable(String),
}

/// Header that sends the caller's name (same name as CALLER_HEADER in doeff's packages/doeff-jev/src/doeff_jev/_wire.py). The proxy counts questions separately by caller and rule.
/// Putting it in the body would change the proxy's key (proxy_key), so it is sent in a header.
pub const CALLER_HEADER: &str = "x-jev-caller";
/// Caller name of a batch of "only if remembered" questions (a batch spans rules, so no rule id is attached).
pub const PEEK_CALLER: &str = "doeff-linter/peek";

/// Caller name of a rule's question (`doeff-linter/DOEFF205` — the same `<component>/<rule>` form as doeff-jev's caller).
pub fn caller_of(question: SemanticQuestion) -> String {
    format!("doeff-linter/{}", question.id())
}

/// Caller name of a calibration watch question (`doeff-linter/DOEFF205/calibration` — counted separately from the rule's questions).
pub fn calibration_caller_of(question: SemanticQuestion) -> String {
    format!("{}/calibration", caller_of(question))
}

/// The mouth that asks Jev (HTTP for the real one, a fake in checks).
pub trait Gateway: Sync {
    /// Fire one question for one definition (the answer, and what that call made upstream pay). caller = the caller's name sent in the header CALLER_HEADER
    /// (the value of caller_of / calibration_caller_of; not sent if empty).
    fn ask(&self, state: &Value, question: &Value, freshness: Freshness, caller: &str) -> Result<Asked, String>;
    /// Proxy key of the question body (proxy_key). None if the target is not a proxy (an "only if remembered" question is fired only at a proxy).
    fn proxy_key(&self, state: &Value, question: &Value) -> Option<String>;
    /// Ask the proxy for a batch of proxy keys "only if remembered" (the real Jev is not called; no re-asking). A target that is not a proxy gives Unreachable.
    fn peek_many(&self, keys: &[String], timeout: Duration) -> PeekedMany;
    /// Model name of the target (for the cache key and output).
    fn model(&self) -> String;
}

// ---- How the target is decided (a copy of doeff's packages/doeff-jev/src/doeff_jev/_target.py — the decision follows the order and defaults in the docstring of _target.py) ----
// How it is resolved (the upper one wins):
// 1. Environment variables JEV_BASE_URL / JEV_MODEL / JEV_WIRE / JEV_API_KEY / JEV_API_KEY_FILE
// 2. Settings file ~/.config/jev/client.json (fields base_url / model / wire / api_key_file)
// 3. Default = TypeSafe direct (/v1/systemone, model jev-latest). The key is TYPESAFE_API_KEY → ~/.config/jev/api_key.
//    When the gateway (Vercel AI Gateway's evaluation-model) is named, model is typesafe-ai/jev and the key is AI_GATEWAY_API_KEY → ~/jev_key.

pub const DIRECT_URL: &str = "https://api.typesafe.ai/v1/systemone";
pub const DIRECT_MODEL: &str = "jev-latest";
pub const GATEWAY_URL: &str = "https://ai-gateway.vercel.sh/v4/ai/evaluation-model";
pub const GATEWAY_MODEL: &str = "typesafe-ai/jev";
const GATEWAY_HOST: &str = "ai-gateway.vercel.sh";
const CONFIG_FILE: &str = "~/.config/jev/client.json";
const DIRECT_KEY_FILE: &str = "~/.config/jev/api_key";
const GATEWAY_KEY_FILE: &str = "~/jev_key";

/// Wire form.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum Wire {
    Direct,
    Gateway,
}

/// The resolved target (the key value is not shown in Debug).
#[derive(Clone)]
pub struct JevTarget {
    pub base_url: String,
    pub model: String,
    pub wire: Wire,
    api_key: Option<String>,
    /// env / file / default / repo (for records — repo = the proxy from the repo's settings).
    pub source: &'static str,
    /// Whether the target is a proxy that remembers Jev calls ("only if remembered" questions and the no-cache header are sent only to a proxy).
    pub proxy: bool,
}

impl std::fmt::Debug for JevTarget {
    /// Debug that does not show the key value.
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("JevTarget")
            .field("base_url", &self.base_url)
            .field("model", &self.model)
            .field("wire", &self.wire)
            .field("api_key", &self.api_key.as_ref().map(|_| "<set>"))
            .field("source", &self.source)
            .field("proxy", &self.proxy)
            .finish()
    }
}

/// Decide the wire form from the URL and the declaration (target.py's _wire_of).
fn wire_of(url: &str, declared: Option<&str>) -> Wire {
    match declared {
        Some("direct") => Wire::Direct,
        Some("gateway") => Wire::Gateway,
        _ if url.contains(GATEWAY_HOST) => Wire::Gateway,
        _ => Wire::Direct,
    }
}

/// Resolve the target (pure — reads of the environment and files are received as arguments. Same order as target.py's resolve_target).
pub fn resolve_target(env: &dyn Fn(&str) -> Option<String>, read_text: &dyn Fn(&str) -> Option<String>, wire: Option<&str>) -> JevTarget {
    let file_cfg: serde_json::Map<String, Value> =
        read_text(CONFIG_FILE).and_then(|text| serde_json::from_str::<Value>(&text).ok()).and_then(|v| v.as_object().cloned()).unwrap_or_default();
    let file_str = |key: &str| file_cfg.get(key).and_then(Value::as_str).filter(|s| !s.is_empty()).map(str::to_string);
    let env_str = |key: &str| env(key).filter(|s| !s.is_empty());
    let declared_wire = env_str("JEV_WIRE").or_else(|| file_str("wire")).or_else(|| wire.map(str::to_string));
    let mut url = env_str("JEV_BASE_URL").or_else(|| file_str("base_url"));
    let source = if env_str("JEV_BASE_URL").is_some() {
        "env"
    } else if file_str("base_url").is_some() {
        "file"
    } else {
        "default"
    };
    if url.is_none() {
        url = Some(if declared_wire.as_deref() == Some("gateway") { GATEWAY_URL } else { DIRECT_URL }.to_string());
    }
    let url = url.unwrap_or_default();
    let resolved_wire = wire_of(&url, declared_wire.as_deref());
    let model = env_str("JEV_MODEL").or_else(|| file_str("model")).unwrap_or_else(|| {
        match resolved_wire {
            Wire::Gateway => GATEWAY_MODEL,
            Wire::Direct => DIRECT_MODEL,
        }
        .to_string()
    });
    let mut key = env_str("JEV_API_KEY");
    if key.is_none() {
        if let Some(key_file) = env_str("JEV_API_KEY_FILE").or_else(|| file_str("api_key_file")) {
            key = read_text(&key_file);
        }
    }
    if key.is_none() {
        key = match resolved_wire {
            Wire::Gateway => env_str("AI_GATEWAY_API_KEY").or_else(|| read_text(GATEWAY_KEY_FILE)),
            Wire::Direct => env_str("TYPESAFE_API_KEY").or_else(|| read_text(DIRECT_KEY_FILE)),
        };
    }
    JevTarget { base_url: url, model, wire: resolved_wire, api_key: key.map(|k| k.trim().to_string()).filter(|k| !k.is_empty()), source, proxy: false }
}

/// Resolve the target with the repo settings' proxy layered on (pure). If env JEV_BASE_URL is set, it wins (the proxy is not used). No key is sent to the proxy
/// (real keys such as TYPESAFE_API_KEY are not sent either). model is the resolved direct model (the direct default if the gateway was resolved).
pub fn resolve_repo_target(env: &dyn Fn(&str) -> Option<String>, read_text: &dyn Fn(&str) -> Option<String>, proxy: Option<&ProxySettings>) -> JevTarget {
    let base = resolve_target(env, read_text, None);
    match proxy {
        Some(proxy) if base.source != "env" => JevTarget {
            base_url: proxy.url.clone(),
            model: match base.wire {
                Wire::Direct => base.model,
                Wire::Gateway => DIRECT_MODEL.to_string(),
            },
            wire: Wire::Direct,
            api_key: None,
            source: "repo",
            proxy: true,
        },
        _ => base,
    }
}

/// Expand `~` and read the file (None if absent — target.py's read_text_from_disk).
pub fn read_text_from_disk(path: &str) -> Option<String> {
    let expanded = match path.strip_prefix("~/") {
        Some(rest) => PathBuf::from(std::env::var("HOME").ok()?).join(rest),
        None => PathBuf::from(path),
    };
    std::fs::read_to_string(expanded).ok().map(|t| t.trim().to_string()).filter(|t| !t.is_empty())
}

/// Resolve the target from this process's environment and files in home (I/O called only by the assembly point — target.py's target_from_process_environment).
pub fn target_from_process_environment() -> JevTarget {
    resolve_target(&|key| std::env::var(key).ok(), &read_text_from_disk, None)
}

/// Resolve the target from this process's environment, files in home, and the repo's proxy settings (I/O called only by the assembly point).
pub fn target_for_repo(proxy: Option<&ProxySettings>) -> JevTarget {
    resolve_repo_target(&|key| std::env::var(key).ok(), &read_text_from_disk, proxy)
}

/// Map the question to the gateway's spelling (noul becomes boolean — wire.py's _gateway_question).
fn gateway_question(question: &Value) -> Value {
    let mut out = question.clone();
    if let Some(map) = out.as_object_mut() {
        if map.get("type").and_then(Value::as_str) == Some("noul") {
            map.insert("type".into(), Value::String("boolean".into()));
        }
    }
    out
}

/// The real implementation that calls Jev's mouth over HTTP (both the direct and gateway forms — a copy of wire.py's prepare and parse).
pub struct HttpGateway {
    target: JevTarget,
    agent: ureq::Agent,
}

impl HttpGateway {
    /// Build the mouth from a target. Return the reason if there is no key for a TypeSafe or Vercel target (the value is not shown).
    pub fn new(target: JevTarget, timeout: Duration) -> Result<HttpGateway, String> {
        let needs_key = target.base_url.contains("api.typesafe.ai") || target.base_url.contains(GATEWAY_HOST);
        if needs_key && target.api_key.is_none() {
            return Err(match target.wire {
                Wire::Direct => "no Jev API key (JEV_API_KEY, JEV_API_KEY_FILE, TYPESAFE_API_KEY, ~/.config/jev/api_key)".to_string(),
                Wire::Gateway => "no Jev API key (JEV_API_KEY, JEV_API_KEY_FILE, AI_GATEWAY_API_KEY, ~/jev_key)".to_string(),
            });
        }
        Ok(HttpGateway { target, agent: ureq::AgentBuilder::new().timeout(timeout).build() })
    }
}

impl HttpGateway {
    /// Body of the question (direct and gateway forms — an "only if remembered" question sends the same body, so the proxy keys match).
    fn body(&self, state: &Value, question: &Value) -> Value {
        match self.target.wire {
            Wire::Direct => json!({"state": state, "questions": {"q": question}, "model": self.target.model}),
            Wire::Gateway => json!({"state": state, "questions": {"q": gateway_question(question)}}),
        }
    }
}

impl Gateway for HttpGateway {
    /// Ask once (429 and 5xx are retried up to 3 times with gaps). Cache-Control: no-cache is added for the proxy only when Fresh. If the proxy's answer
    /// header x-jev-proxy says remembered answer or coalesced, upstream was not called that time (cost and tokens are not counted).
    fn ask(&self, state: &Value, question: &Value, freshness: Freshness, caller: &str) -> Result<Asked, String> {
        let body = self.body(state, question);
        let mut last = String::new();
        for attempt in 0..4u64 {
            let mut request = self.agent.post(&self.target.base_url).set("content-type", "application/json");
            if let Some(key) = &self.target.api_key {
                request = request.set("authorization", &format!("Bearer {}", key));
            }
            if !caller.is_empty() {
                request = request.set(CALLER_HEADER, caller);
            }
            if self.target.proxy && freshness == Freshness::Fresh {
                request = request.set("cache-control", "no-cache");
            }
            if self.target.wire == Wire::Gateway {
                request = request
                    .set("ai-gateway-protocol-version", "0.0.1")
                    .set("ai-evaluation-model-specification-version", "4")
                    .set("ai-model-id", &self.target.model);
            }
            match request.send_string(&body.to_string()) {
                Ok(ok) => {
                    let remembered = self.target.proxy && answered_from_memory(ok.header("x-jev-proxy"));
                    let text = ok.into_string().map_err(|e| format!("cannot read the answer: {}", e))?;
                    let answer = parse_answer(&text)?;
                    let charge = charge_of(&answer, remembered);
                    return Ok(Asked { answer, charge });
                }
                Err(ureq::Error::Status(code, answer)) if [429, 500, 502, 503, 504, 529].contains(&code) && attempt < 3 => {
                    last = format!("HTTP {}: {}", code, answer.into_string().unwrap_or_default().chars().take(200).collect::<String>());
                    std::thread::sleep(Duration::from_millis(1500 * (attempt + 1)));
                }
                Err(ureq::Error::Status(code, answer)) => {
                    return Err(format!("HTTP {}: {}", code, answer.into_string().unwrap_or_default().chars().take(300).collect::<String>()))
                }
                Err(error) => return Err(format!("cannot reach Jev: {}", error)),
            }
        }
        Err(last)
    }

    /// Proxy key (only for a proxy target — the body is spelled the same as in ask).
    fn proxy_key(&self, state: &Value, question: &Value) -> Option<String> {
        self.target.proxy.then(|| proxy_key(&self.body(state, question)))
    }

    /// Ask a batch of keys "only if remembered" with POST <proxy_url>/peek (no re-asking). Not fired at a target that is not a proxy (so the real Jev is not called).
    fn peek_many(&self, keys: &[String], timeout: Duration) -> PeekedMany {
        if !self.target.proxy {
            return PeekedMany::Unreachable("the target is not a proxy (an \"only if remembered\" question is fired only at a proxy)".to_string());
        }
        let url = format!("{}/peek", self.target.base_url.trim_end_matches('/'));
        let request =
            self.agent.post(&url).timeout(timeout).set("content-type", "application/json").set(CALLER_HEADER, PEEK_CALLER);
        match request.send_string(&json!({ "keys": keys }).to_string()) {
            Ok(ok) => match ok.into_string().map_err(|e| e.to_string()).and_then(|text| parse_remembered(&text)) {
                Ok(answers) => PeekedMany::Remembered(answers),
                Err(reason) => PeekedMany::Unreachable(format!("cannot read the proxy's answer: {}", reason)),
            },
            Err(ureq::Error::Status(code, _)) => PeekedMany::Unreachable(format!("the proxy returned HTTP {}", code)),
            Err(error) => PeekedMany::Unreachable(format!("cannot reach the proxy: {}", error)),
        }
    }

    /// Model name of the target.
    fn model(&self) -> String {
        self.target.model.clone()
    }
}

/// Read the probability from the answer JSON (direct: answers.q.noul; gateway: answers.q.probability — wire.py's _parse_answer).
pub fn parse_answer(text: &str) -> Result<Answer, String> {
    let value: Value = serde_json::from_str(text).map_err(|e| format!("the answer is not JSON: {}", e))?;
    let raw = value.pointer("/answers/q").ok_or_else(|| format!("the answer has no answers.q: {}", text.chars().take(200).collect::<String>()))?;
    let choice = raw.get("choice").and_then(Value::as_str).map(str::to_string);
    let probabilities: Option<BTreeMap<String, f64>> =
        raw.get("probabilities").and_then(Value::as_object).map(|m| m.iter().filter_map(|(k, v)| v.as_f64().map(|p| (k.clone(), p))).collect());
    let probability = match raw.get("noul").or_else(|| raw.get("probability")).and_then(Value::as_f64) {
        Some(p) => p,
        None if choice.is_some() => choice.as_ref().and_then(|c| probabilities.as_ref()?.get(c).copied()).unwrap_or(0.0),
        None => return Err(format!("the answer has neither a noul probability nor a choice: {}", text.chars().take(200).collect::<String>())),
    };
    let reported_cost_usd =
        value.pointer("/providerMetadata/gateway/cost").and_then(|c| c.as_str().and_then(|s| s.parse::<f64>().ok()).or_else(|| c.as_f64()));
    let usage = value.get("usage");
    let tokens = |snake: &str, camel: &str| usage.and_then(|u| u.get(snake).or_else(|| u.get(camel))).and_then(Value::as_u64);
    let input_tokens = tokens("input_tokens", "inputTokens");
    let output_tokens = tokens("output_tokens", "outputTokens");
    let served_model = value.get("model").and_then(Value::as_str).map(str::to_string);
    Ok(Answer { probability, reported_cost_usd, input_tokens, output_tokens, served_model, choice, probabilities })
}

/// What one question made upstream pay (separate from the answer's fields — the proxy's remembered answer carries the original question's cost in its body, but this call did not call upstream).
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum Charge {
    /// Upstream was called and the answer carried the cost (USD).
    Reported(f64),
    /// Upstream was called but the answer carries no cost (direct TypeSafe — unknown, not 0).
    Unreported,
    /// Upstream was not called (the proxy's remembered answer, joining the same question, or an "only if remembered" question) — cost and tokens for this call are 0.
    Remembered,
}

/// The answer to one question, and the cost of that call.
#[derive(Debug, Clone, PartialEq)]
pub struct Asked {
    pub answer: Answer,
    pub charge: Charge,
}

/// Whether the proxy's answer header x-jev-proxy marks "answered without calling upstream" (hit = remembered answer, coalesced = joined the same question — words of the header in proboscis/jev-proxy's
/// service.hy). No header (a target that is not a proxy), miss and refreshed mean upstream was called.
pub fn answered_from_memory(marker: Option<&str>) -> bool {
    matches!(marker, Some("hit") | Some("coalesced"))
}

/// Decide the cost of one question (pure): Remembered if upstream was not called; Reported if it was called and the answer carries a cost; Unreported otherwise.
pub fn charge_of(answer: &Answer, remembered: bool) -> Charge {
    match (remembered, answer.reported_cost_usd) {
        (true, _) => Charge::Remembered,
        (false, Some(cost)) => Charge::Reported(cost),
        (false, None) => Charge::Unreported,
    }
}

/// Read the proxy's batch answer {"answers": {key: body of Jev's answer}} to "only if remembered" questions (if even one is unreadable, the whole is treated as unreadable).
pub fn parse_remembered(text: &str) -> Result<BTreeMap<String, Answer>, String> {
    let value: Value = serde_json::from_str(text).map_err(|e| format!("the answer is not JSON: {}", e))?;
    let answers = value.get("answers").and_then(Value::as_object).ok_or_else(|| "the answer has no answers object".to_string())?;
    answers.iter().map(|(key, answer)| parse_answer(&answer.to_string()).map(|a| (key.clone(), a))).collect()
}

/// Location of the cache (.doeff-linter/semantic-cache/ at the repo root).
pub fn cache_dir(root: &Path) -> PathBuf {
    root.join(".doeff-linter").join("semantic-cache")
}

/// Read an answer from the cache (None if absent or unreadable).
pub fn read_cache(root: &Path, key: &str) -> Option<Answer> {
    let text = std::fs::read_to_string(cache_dir(root).join(format!("{}.json", key))).ok()?;
    serde_json::from_str(&text).ok()
}

/// Write an answer to the cache.
fn write_cache(root: &Path, key: &str, answer: &Answer) -> Result<(), String> {
    let dir = cache_dir(root);
    std::fs::create_dir_all(&dir).map_err(|e| format!("cannot create the cache dir: {}", e))?;
    let text = serde_json::to_string(answer).map_err(|e| e.to_string())?;
    std::fs::write(dir.join(format!("{}.json", key)), text).map_err(|e| format!("cannot write the cache: {}", e))
}

/// One calibration watch example (data/semantic_calibration.json).
#[derive(Debug, Clone, Deserialize)]
pub struct CalibrationExample {
    pub rule: String,
    pub id: String,
    pub expect: bool,
    pub name: String,
    pub kind: String,
    pub path: String,
    pub layer: String,
    pub source: String,
    /// Declaration of the fields of a DOEFF204 example (`name: type`).
    #[serde(default)]
    pub fields: Vec<String>,
}

/// The bundled calibration examples.
#[derive(Debug, Clone, Deserialize)]
struct CalibrationFile {
    items: Vec<CalibrationExample>,
}

/// Lower bound of the probability of a positive example and upper bound of a negative example (the default range).
pub const CALIBRATION_POSITIVE_MIN: f64 = 0.8;
pub const CALIBRATION_NEGATIVE_MAX: f64 = 0.2;

/// Read the bundled calibration examples.
pub fn calibration_examples() -> Vec<CalibrationExample> {
    serde_json::from_str::<CalibrationFile>(include_str!("../../data/semantic_calibration.json")).map(|f| f.items).unwrap_or_default()
}

/// Result of a run that fires questions.
#[derive(Debug, Clone, Default, Serialize)]
pub struct SemanticSummary {
    pub model: String,
    /// The wire form and where the target came from (env / file / default) — the key value is not shown.
    pub wire: String,
    /// Number of definitions with an answer (from the cache or this run's questions).
    pub judged: usize,
    /// Number of definitions with no answer (undecided — not a pass).
    pub unjudged: usize,
    /// Number of definitions that were to be asked this time but got no answer (Jev unreachable, no key, calibration could not be fired — "unmeasured". Shown separately from green).
    pub unmeasured: usize,
    /// Number fired at the gateway this time (including calibration).
    pub asked: usize,
    /// Number of answers the proxy remembered that were received and written to the local cache this time ("only if remembered" questions — the real Jev was not called).
    pub peeked: usize,
    /// Cost this time (sum of USD over the calls where upstream put a cost in the answer). Calls that do not report it (direct TypeSafe) are counted in cost_unreported, and 0 is not added here
    /// — if cost_unreported is not 0, this sum is a lower bound.
    pub cost_usd: f64,
    /// Number of calls this time where upstream was called but the answer carried no cost (cost unknown — not 0).
    pub cost_unreported: usize,
    /// Number of calls this time answered by the proxy's remembered answer or by joining another question (upstream not called — cost and tokens not counted).
    pub remembered: usize,
    /// Sum of input tokens of the calls that called upstream this time (only calls whose answer had usage).
    pub input_tokens: u64,
    /// Sum of output tokens of the calls that called upstream this time (only calls whose answer had usage).
    pub output_tokens: u64,
    /// Versioned name of the model that answered this time (direct only; null for a run that fires nothing).
    pub served_model: Option<String>,
    /// Result of the calibration watch (not-run, ok, drifted, failed).
    pub calibration: String,
    /// Number of hits removed from violations because they are on the false-positive list (not shown and not counted).
    pub false_positives: usize,
    /// Comparison of human judgments (false-positive list = negative examples, positive-example list = positive examples) with Jev's answers.
    pub labeled: LabeledSummary,
}

/// Comparison of human judgments with Jev's answers — whether each is a hit at the current thresholds, and the probability per judgment (material for deciding thresholds).
#[derive(Debug, Clone, Default, Serialize)]
pub struct LabeledSummary {
    /// Positive examples (what a human judged to be a real violation).
    pub positives: LabelCount,
    /// Negative examples (the false-positive list).
    pub negatives: LabelCount,
    /// Probability per judgment that has an answer (in key order).
    pub items: Vec<LabeledAnswer>,
}

/// Counts per kind of judgment.
#[derive(Debug, Clone, Default, Serialize)]
pub struct LabelCount {
    /// Number of keys on the list.
    pub listed: usize,
    /// Of those, the number with a Jev answer (the current definition was judged).
    pub judged: usize,
    /// Of those, the number that are hits at the current thresholds (hits for positive examples, false positives for negative examples).
    pub flagged: usize,
}

/// One judgment and Jev's answer.
#[derive(Debug, Clone, Serialize)]
pub struct LabeledAnswer {
    pub key: String,
    pub rule: String,
    /// Human judgment (true = real violation, false = false positive).
    pub expect: bool,
    /// Probability compared with thresholds (`target_probability` — the probability of the answer for Noul, of external-world for DOEFF204, of mixed for DOEFF205).
    pub probability: f64,
    /// Whether it is a hit at the current thresholds.
    pub flagged: bool,
}

/// What to fire.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum SemanticMode {
    /// Only read the cache (deterministic runs while editing in the editor).
    CacheOnly,
    /// Read the cache, and ask the proxy "only if remembered" about definitions not in it (full runs and hooks — the same as CacheOnly if there is no proxy).
    Peek,
    /// Fire the definitions of the given files (paths from the repo root).
    Ask(BTreeSet<String>),
    /// Fire only the definitions of the given files that have no answer in the local cache (definitions whose content changed).
    AskChanged(BTreeSet<String>),
    /// Fire all definitions.
    AskAll,
    /// Default of a full run without flags: fire only the definitions of the given files that have no answer in the local cache (same as AskChanged),
    /// and ask the proxy "only if remembered" about the remaining definitions with no answer (same as Peek — receive answers from other workers).
    PeekThenAskChanged(BTreeSet<String>),
}

/// Result of the semantic rules (definitions with answers, and the summary and reasons).
pub struct SemanticOutcome {
    pub answered: Vec<(SemanticItem, Answer)>,
    pub summary: SemanticSummary,
    pub errors: Vec<String>,
}

impl SemanticSummary {
    /// To add the cost and tokens of one question to the summary — calls that did not call upstream (the proxy's memory) are not counted, and calls that report no cost are counted as unknown.
    pub fn tally(&mut self, asked: &Asked) {
        match asked.charge {
            Charge::Remembered => {
                self.remembered += 1;
                return;
            }
            Charge::Reported(cost) => self.cost_usd += cost,
            Charge::Unreported => self.cost_unreported += 1,
        }
        if let Some(tokens) = asked.answer.input_tokens {
            self.input_tokens += tokens;
        }
        if let Some(tokens) = asked.answer.output_tokens {
            self.output_tokens += tokens;
        }
    }
}

/// Read the questions of definitions from the cache, and fire the ones the mode fires at the gateway (`workers` at a time). A run that fires first fires the calibration watch.
pub fn evaluate(
    root: &Path,
    settings: &SemanticSettings,
    items: Vec<SemanticItem>,
    mode: &SemanticMode,
    gateway: Option<&dyn Gateway>,
    calibration: &[(SemanticItem, bool)],
) -> SemanticOutcome {
    let mut summary = SemanticSummary {
        model: gateway.map(|g| g.model()).unwrap_or_default(),
        calibration: "not-run".to_string(),
        ..SemanticSummary::default()
    };
    let mut errors = Vec::new();
    // Definitions that are work in progress and cannot be read are not asked about in any run (counted as undecided).
    let wants_ask = |item: &SemanticItem| {
        item.readable
            && match mode {
                SemanticMode::CacheOnly | SemanticMode::Peek => false,
                SemanticMode::AskAll => true,
                SemanticMode::Ask(targets) => targets.contains(&item.rel),
                SemanticMode::AskChanged(targets) | SemanticMode::PeekThenAskChanged(targets) => {
                    targets.contains(&item.rel) && read_cache(root, &item.key).is_none()
                }
            }
    };
    let asking = items.iter().any(wants_ask);
    // Report named Hy files that have no definition that becomes a question (outside the asked layers, no top-level definition, work in progress) — so that when a named file
    // becomes no question and the run ends with "fired this time: 0", it does not stay silent about why it is 0.
    if let SemanticMode::Ask(targets) | SemanticMode::AskChanged(targets) = mode {
        let silent: Vec<&str> = targets
            .iter()
            .filter(|rel| rel.ends_with(".hy") && !items.iter().any(|item| item.readable && &item.rel == *rel))
            .map(String::as_str)
            .collect();
        if !silent.is_empty() {
            errors.push(format!(
                "semantic rules: {} of the named files have no definition to ask about (outside the asked layers, no top-level definition, or work in progress) — not counted in the number fired this time: {}",
                silent.len(),
                silent.join(", ")
            ));
        }
    }
    let pool = rayon::ThreadPoolBuilder::new().num_threads(settings.workers).build();
    if asking {
        match (gateway, &pool) {
            (Some(gateway), Ok(pool)) => {
                let results: Vec<(bool, Result<Asked, String>)> = pool.install(|| {
                    calibration
                        .par_iter()
                        .map(|(c, expect)| {
                            (*expect, gateway.ask(&c.state, &c.question_json, Freshness::Fresh, &calibration_caller_of(c.question)))
                        })
                        .collect()
                });
                summary.asked += results.len();
                let mut drifted = Vec::new();
                for ((expect, result), (example, _)) in results.into_iter().zip(calibration) {
                    match result {
                        Ok(asked) => {
                            summary.tally(&asked);
                            let answer = asked.answer;
                            if answer.served_model.is_some() {
                                summary.served_model = answer.served_model.clone();
                            }
                            let probability = target_probability(example.question, &answer);
                            let inside = if expect { probability >= CALIBRATION_POSITIVE_MIN } else { probability <= CALIBRATION_NEGATIVE_MAX };
                            if !inside {
                                drifted.push(format!("{} {} (expected {}) is p={:.2}", example.question_id(), example.name, if expect { "true" } else { "false" }, probability));
                            }
                        }
                        Err(reason) => {
                            summary.calibration = "failed".to_string();
                            errors.push(format!("semantic rules: cannot fire the calibration question: {}", reason));
                        }
                    }
                }
                if summary.calibration != "failed" {
                    if drifted.is_empty() {
                        summary.calibration = "ok".to_string();
                    } else {
                        summary.calibration = "drifted".to_string();
                        let removed = std::fs::remove_dir_all(cache_dir(root)).is_ok();
                        errors.push(format!(
                            "semantic rules: calibration left the default range (positive p ≥ {}, negative p ≤ {}) — the cache was discarded on suspicion that the model changed{}: {}",
                            CALIBRATION_POSITIVE_MIN,
                            CALIBRATION_NEGATIVE_MAX,
                            if removed { "" } else { " (there was no cache to discard)" },
                            drifted.join(", ")
                        ));
                    }
                }
            }
            (None, _) => errors.push("semantic rules: the gateway mouth cannot be built, so nothing is fired (only the cache is read)".to_string()),
            (_, Err(error)) => errors.push(format!("semantic rules: cannot build the parallel pool: {}", error)),
        }
    }
    let can_ask = asking && gateway.is_some() && summary.calibration != "failed";
    let resolved: Vec<(SemanticItem, Option<Resolved>)> = match (&pool, gateway) {
        (Ok(pool), Some(gateway)) if can_ask => pool.install(|| {
            items
                .into_par_iter()
                .map(|item| {
                    if wants_ask(&item) {
                        let result = match gateway.ask(&item.state, &item.question_json, Freshness::Remembered, &caller_of(item.question)) {
                            Ok(asked) => Resolved::Asked(asked),
                            Err(reason) => Resolved::Failed(reason),
                        };
                        (item, Some(result))
                    } else {
                        let cached = read_cache(root, &item.key).map(Resolved::Known);
                        (item, cached)
                    }
                })
                .collect()
        }),
        _ => items.into_iter().map(|item| {
            let cached = read_cache(root, &item.key).map(Resolved::Known);
            (item, cached)
        }).collect(),
    };
    // Full runs and hooks: ask the proxy "only if remembered" about readable definitions missing from the local cache, in batches of proxy keys (the real Jev is not called).
    let resolved = match (mode, gateway, &pool, settings.proxy.as_ref()) {
        (SemanticMode::Peek | SemanticMode::PeekThenAskChanged(_), Some(gateway), Ok(pool), Some(proxy)) => {
            let wanted: Vec<Option<String>> = resolved
                .iter()
                .map(|(item, cached)| match cached {
                    None if item.readable => gateway.proxy_key(&item.state, &item.question_json),
                    _ => None,
                })
                .collect();
            let keys: Vec<String> = wanted.iter().flatten().cloned().collect::<BTreeSet<String>>().into_iter().collect();
            let peeked = if keys.is_empty() { Peeked::default() } else { peek_remembered(gateway, &keys, proxy.peek_timeout, pool) };
            if let Some(reason) = &peeked.missed_reason {
                errors.push(format!(
                    "semantic rules: the batch reading the proxy's memory did not return ({}) — definitions for {} keys could not be measured (not undecided; wait = semantic.proxy_peek_timeout_ms {} ms)",
                    reason,
                    peeked.missed.len(),
                    proxy.peek_timeout.as_millis()
                ));
            }
            resolved
                .into_iter()
                .zip(wanted)
                .map(|((item, cached), key)| match key.as_ref().and_then(|k| peeked.answers.get(k)) {
                    Some(answer) => {
                        summary.peeked += 1;
                        if let Err(reason) = write_cache(root, &item.key, answer) {
                            errors.push(reason);
                        }
                        ((item, Some(Resolved::Known(answer.clone()))), false)
                    }
                    None => {
                        let missed = key.as_ref().is_some_and(|k| peeked.missed.contains(k));
                        ((item, cached), missed)
                    }
                })
                .unzip()
        }
        _ => {
            let unmissed = vec![false; resolved.len()];
            (resolved, unmissed)
        }
    };
    let (resolved, peek_missed): (Vec<(SemanticItem, Option<Resolved>)>, Vec<bool>) = resolved;
    let mut answered = Vec::new();
    for ((item, result), missed) in resolved.into_iter().zip(peek_missed) {
        match result {
            Some(Resolved::Asked(asked)) => {
                summary.asked += 1;
                summary.tally(&asked);
                if let Err(reason) = write_cache(root, &item.key, &asked.answer) {
                    errors.push(reason);
                }
                summary.judged += 1;
                answered.push((item, asked.answer));
            }
            Some(Resolved::Known(answer)) => {
                summary.judged += 1;
                answered.push((item, answer));
            }
            Some(Resolved::Failed(reason)) => {
                summary.asked += 1;
                summary.unjudged += 1;
                summary.unmeasured += 1;
                errors.push(format!("{} {}: cannot ask Jev: {}", item.rel, item.name, reason));
            }
            // A definition whose proxy-memory batch did not return within the wait (unreachable, timed out) has not been measured for whether an answer exists — count it as "unmeasured", not
            // as undecided (on a machine where the batch exceeded 5 seconds, a repo with 6813 judged silently showed 7697 undecided).
            None if missed => summary.unmeasured += 1,
            None => {
                summary.unjudged += 1;
                // A definition that was to be asked ended without being asked, through the gateway, calibration or pool (unmeasured).
                if wants_ask(&item) {
                    summary.unmeasured += 1;
                }
            }
        }
    }
    SemanticOutcome { answered, summary, errors }
}

/// Where one definition's answer came from — an answer asked this time (cost is counted and written to the local cache), an answer read from the local cache or the proxy's memory (this time
/// upstream was not called — cost not counted), or the reason it could not be asked this time.
enum Resolved {
    Asked(Asked),
    Known(Answer),
    Failed(String),
}

/// Result of reading the proxy's memory: answers = remembered answers (proxy key → answer) / missed = keys of batches that did not return (unreachable, timed out, stopped without being fired)
/// — whether an answer exists has not been measured / missed_reason = the reason of the first batch that did not return.
#[derive(Default)]
struct Peeked {
    answers: BTreeMap<String, Answer>,
    missed: BTreeSet<String>,
    missed_reason: Option<String>,
}

/// Split the batch of proxy keys into PEEK_BATCH at a time, ask the proxy "only if remembered" in parallel, and collect the remembered answers. With a time
/// limit covering all batches combined; if any batch is unreachable, the remaining batches are not fired. Keys of batches that did not return are collected in missed (counted separately from undecided; #1885).
fn peek_remembered(gateway: &dyn Gateway, keys: &[String], timeout: Duration, pool: &rayon::ThreadPool) -> Peeked {
    let deadline = Instant::now() + timeout;
    let unreachable = AtomicBool::new(false);
    let batches: Vec<&[String]> = keys.chunks(PEEK_BATCH).collect();
    let missed_batch = |batch: &[String], reason: String| Peeked { answers: BTreeMap::new(), missed: batch.iter().cloned().collect(), missed_reason: Some(reason) };
    pool.install(|| {
        batches
            .par_iter()
            .map(|batch| {
                let left = deadline.saturating_duration_since(Instant::now());
                if left.is_zero() {
                    return missed_batch(batch, format!("used up the wait of {} ms", timeout.as_millis()));
                }
                if unreachable.load(Ordering::Relaxed) {
                    return missed_batch(batch, "not fired because an earlier batch did not return".to_string());
                }
                match gateway.peek_many(batch, left) {
                    PeekedMany::Remembered(answers) => Peeked { answers, ..Peeked::default() },
                    PeekedMany::Unreachable(reason) => {
                        unreachable.store(true, Ordering::Relaxed);
                        missed_batch(batch, reason)
                    }
                }
            })
            .reduce(Peeked::default, |mut all, part| {
                all.answers.extend(part.answers);
                all.missed.extend(part.missed);
                if all.missed_reason.is_none() {
                    all.missed_reason = part.missed_reason;
                }
                all
            })
    })
}

impl SemanticItem {
    /// ID of the question (for the calibration notice text).
    fn question_id(&self) -> &'static str {
        self.question.id()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn strips_tags_truncates_and_keys_do_not_see_declared_roles() {
        let settings = SemanticSettings::validate(&SemanticSection::default(), &mut |_, _| None, &mut Vec::new());
        let description = LayerDescription { summary: Some("translation".into()), ..LayerDescription::default() };
        let range = { let p = doeff_indexer::hy_index::Position { line: 0, character: 0 }; doeff_indexer::hy_index::Range { start: p, end: p } };
        let one = item(&settings, "jev-latest", SemanticQuestion::BusinessDecision, "a.hy", Path::new("/r/a.hy"), "f", "defk", range, "(defk f [x] {:tags {:role \"protocol\"}} x)", LayerId(0), "protocol", &description);
        let other = item(&settings, "jev-latest", SemanticQuestion::BusinessDecision, "a.hy", Path::new("/r/a.hy"), "f", "defk", range, "(defk f [x] {:tags {:role \"judgment\"}} x)", LayerId(0), "protocol", &description);
        assert_eq!(one.key, other.key, "the declared role is not part of the key");
        assert_eq!(one.state["definition"]["source"], "(defk f [x] {} x)");
        let long = item(&settings, "jev-latest", SemanticQuestion::BusinessDecision, "a.hy", Path::new("/r/a.hy"), "f", "defk", range, &"a".repeat(5000), LayerId(0), "protocol", &description);
        assert_eq!(long.state["definition"]["source"].as_str().unwrap().chars().count(), 1800);
        assert_eq!(
            parse_answer(r#"{"answers":{"q":{"type":"boolean","probability":0.93}},"providerMetadata":{"gateway":{"cost":"0.00006"}},"usage":{"inputTokens":10}}"#).unwrap(),
            Answer {
                probability: 0.93,
                reported_cost_usd: Some(0.00006),
                input_tokens: Some(10),
                output_tokens: None,
                served_model: None,
                choice: None,
                probabilities: None
            }
        );
        assert_eq!(parse_answer(r#"{"answers":{"q":{"noul":0.2}},"usage":{"input_tokens":7},"model":"jev-1"}"#).unwrap().probability, 0.2);
        assert!(parse_answer("{}").is_err());
        assert_eq!(calibration_examples().len(), 8);
    }

    // ---- Cost of an answer: a cost exists only when upstream put it in the answer; an answer without it is "unknown", not 0 ----

    /// An answer with a cost (Vercel AI Gateway form) — read the cost and tokens, and count that cost for a call that called upstream.
    #[test]
    fn an_answer_with_a_cost_is_charged_that_cost() {
        let answer = parse_answer(
            r#"{"answers":{"q":{"type":"boolean","probability":0.9}},"providerMetadata":{"gateway":{"cost":"0.00004"}},"usage":{"inputTokens":900,"outputTokens":3}}"#,
        )
        .unwrap();
        assert_eq!((answer.reported_cost_usd, answer.input_tokens, answer.output_tokens), (Some(0.00004), Some(900), Some(3)));
        let asked = Asked { charge: charge_of(&answer, false), answer };
        assert_eq!(asked.charge, Charge::Reported(0.00004));
        let mut summary = SemanticSummary::default();
        summary.tally(&asked);
        assert_eq!((summary.cost_usd, summary.cost_unreported, summary.input_tokens, summary.output_tokens), (0.00004, 0, 900, 3));
    }

    /// An answer with neither cost nor usage — the cost is unknown, not 0, and tokens are not written as 0 either.
    #[test]
    fn an_answer_without_a_cost_is_unreported_not_zero() {
        let answer = parse_answer(r#"{"answers":{"q":{"noul":0.2}},"model":"jev-1.13.0"}"#).unwrap();
        assert_eq!((answer.reported_cost_usd, answer.input_tokens, answer.output_tokens), (None, None, None));
        let asked = Asked { charge: charge_of(&answer, false), answer };
        assert_eq!(asked.charge, Charge::Unreported);
        let mut summary = SemanticSummary::default();
        summary.tally(&asked);
        assert_eq!((summary.cost_usd, summary.cost_unreported, summary.input_tokens), (0.0, 1, 0));
        // Even in the form written to the cache it stays "no cost" (0 is not written).
        let written = serde_json::to_value(&asked.answer).unwrap();
        assert_eq!(written["reported_cost_usd"], Value::Null);
    }

    /// An answer with only usage (token counts) (direct TypeSafe form) — tokens are counted and the cost is counted as unknown (material for multiplying by unit prices; #1892 plan B).
    #[test]
    fn a_usage_only_answer_keeps_tokens_and_leaves_the_cost_unreported() {
        let answer = parse_answer(r#"{"answers":{"q":{"noul":0.7}},"usage":{"input_tokens":1234,"output_tokens":5},"model":"jev-1.13.0"}"#).unwrap();
        assert_eq!((answer.reported_cost_usd, answer.input_tokens, answer.output_tokens), (None, Some(1234), Some(5)));
        let asked = Asked { charge: charge_of(&answer, false), answer };
        let mut summary = SemanticSummary::default();
        summary.tally(&asked);
        assert_eq!((summary.cost_usd, summary.cost_unreported, summary.input_tokens, summary.output_tokens), (0.0, 1, 1234, 5));
    }

    /// A call answered by the proxy's remembered answer or by joining another question did not call upstream — even if the body has the original question's cost, that call's cost and tokens are not counted.
    #[test]
    fn a_remembered_answer_is_not_charged_again() {
        assert!(answered_from_memory(Some("hit")) && answered_from_memory(Some("coalesced")));
        assert!(!answered_from_memory(Some("miss")) && !answered_from_memory(Some("refreshed")) && !answered_from_memory(None));
        let answer = parse_answer(
            r#"{"answers":{"q":{"type":"boolean","probability":0.9}},"providerMetadata":{"gateway":{"cost":"0.00004"}},"usage":{"inputTokens":900}}"#,
        )
        .unwrap();
        let asked = Asked { charge: charge_of(&answer, true), answer };
        assert_eq!(asked.charge, Charge::Remembered);
        let mut summary = SemanticSummary::default();
        summary.tally(&asked);
        assert_eq!((summary.cost_usd, summary.cost_unreported, summary.remembered, summary.input_tokens), (0.0, 0, 1, 0));
    }

    /// An old cache answer (which wrote cost_usd as 0 even when nothing was reported) is read with the cost as "unknown". Token counts are read as they are.
    #[test]
    fn an_old_cache_answer_reads_its_cost_as_unknown() {
        let old: Answer = serde_json::from_str(r#"{"probability":0.1,"cost_usd":0.0,"input_tokens":1000,"served_model":"jev-1.13.0"}"#).unwrap();
        assert_eq!((old.reported_cost_usd, old.input_tokens, old.output_tokens), (None, Some(1000), None));
    }

    #[test]
    fn target_resolution_matches_doeff_jev() {
        let env = |pairs: &'static [(&'static str, &'static str)]| move |key: &str| pairs.iter().find(|(k, _)| *k == key).map(|(_, v)| v.to_string());
        let no_files = |_: &str| None;
        // Default = direct TypeSafe; the key is TYPESAFE_API_KEY.
        let direct = resolve_target(&env(&[("TYPESAFE_API_KEY", "k1")]), &no_files, None);
        assert_eq!((direct.base_url.as_str(), direct.model.as_str(), direct.wire, direct.source), (DIRECT_URL, DIRECT_MODEL, Wire::Direct, "default"));
        assert_eq!(direct.api_key.as_deref(), Some("k1"));
        assert!(!format!("{:?}", direct).contains("k1"), "the key value must not appear in Debug");
        // The default when the gateway is named.
        let gateway = resolve_target(&env(&[("JEV_WIRE", "gateway"), ("AI_GATEWAY_API_KEY", "g")]), &no_files, None);
        assert_eq!((gateway.base_url.as_str(), gateway.model.as_str(), gateway.wire), (GATEWAY_URL, GATEWAY_MODEL, Wire::Gateway));
        // Environment variables win over the settings file, and the settings file wins over the default.
        let files = |path: &str| (path == "~/.config/jev/client.json").then(|| r#"{"base_url":"http://seimf:8000/v1/systemone","model":"file-model"}"#.to_string());
        let from_file = resolve_target(&env(&[]), &files, None);
        assert_eq!((from_file.base_url.as_str(), from_file.model.as_str(), from_file.source), ("http://seimf:8000/v1/systemone", "file-model", "file"));
        let from_env = resolve_target(&env(&[("JEV_BASE_URL", "http://x/v1"), ("JEV_MODEL", "m")]), &files, None);
        assert_eq!((from_env.base_url.as_str(), from_env.model.as_str(), from_env.source), ("http://x/v1", "m", "env"));
    }

    #[test]
    fn repo_proxy_gets_no_key_and_env_url_wins() {
        let env = |pairs: &'static [(&'static str, &'static str)]| move |key: &str| pairs.iter().find(|(k, _)| *k == key).map(|(_, v)| v.to_string());
        let proxy = ProxySettings { url: "http://proxy:8878/v1/systemone".into(), peek_timeout: Duration::from_millis(1500) };
        let files = |path: &str| (path == "~/.config/jev/api_key").then(|| "file-key\n".to_string());
        // No key is sent to the proxy (nor does it fall back to the TypeSafe key in env or the file in home). The mouth can be built without a key.
        let via = resolve_repo_target(&env(&[("TYPESAFE_API_KEY", "real-key")]), &files, Some(&proxy));
        assert_eq!((via.base_url.as_str(), via.model.as_str(), via.wire, via.source, via.proxy), ("http://proxy:8878/v1/systemone", DIRECT_MODEL, Wire::Direct, "repo", true));
        assert_eq!(via.api_key, None);
        assert!(HttpGateway::new(via, Duration::from_secs(1)).is_ok());
        // If env JEV_BASE_URL is set, the repo's proxy is not used.
        let env_wins = resolve_repo_target(&env(&[("JEV_BASE_URL", "http://seimf/v1"), ("TYPESAFE_API_KEY", "k")]), &files, Some(&proxy));
        assert_eq!((env_wins.base_url.as_str(), env_wins.proxy), ("http://seimf/v1", false));
        // Even in an environment that names the gateway, the proxy is asked in the direct form with the direct model.
        let gateway_env = resolve_repo_target(&env(&[("JEV_WIRE", "gateway")]), &files, Some(&proxy));
        assert_eq!((gateway_env.wire, gateway_env.model.as_str()), (Wire::Direct, DIRECT_MODEL));
    }

    /// For a repo with no :semantic-lines in architecture.hy, the question stays as wire(). The DOEFF201 cache key is the same as before boundaries were added
    /// — the key value was built with the code from before boundaries were added (if it changes, all definitions of that repo go back to "no answer"). The DOEFF205 key
    /// changed in #1995 when the general note was aligned with the exception of boundary 4 (the value was built with the code of #1995). The DOEFF202 key changed in #2059 when the frame gained the untyped shape of outside
    /// data (the value was built with the code of #2059).
    #[test]
    fn without_lines_the_questions_and_keys_stay_as_before() {
        let settings = SemanticSettings::validate(&SemanticSection::default(), &mut |_, _| None, &mut Vec::new());
        let description = LayerDescription {
            summary: Some("翻訳".into()),
            knows: Some("相手の話し方".into()),
            does_not_know: Some("本物か模擬か".into()),
            question: Some("言い換えだけか?".into()),
        };
        let range = { let p = doeff_indexer::hy_index::Position { line: 0, character: 0 }; doeff_indexer::hy_index::Range { start: p, end: p } };
        let source = "(defk f [x] {:tags {:role \"protocol\"}} (when (in x allowed) x))";
        let layered = |question| item(&settings, "jev-latest", question, "a.hy", Path::new("/r/a.hy"), "f", "defk", range, source, LayerId(0), "protocol", &description);
        let decision = layered(SemanticQuestion::BusinessDecision);
        let transport = layered(SemanticQuestion::TransportKnowledge);
        let mixed = mixed_item(&settings, "jev-latest", "a.hy", Path::new("/r/a.hy"), "f", "defk", range, source, LayerId(0), "core", &description);
        for one in [&decision, &transport, &mixed] {
            assert_eq!(one.question_json, one.question.wire(), "a question with no boundaries stays the current question");
        }
        assert_eq!(
            [decision.key.as_str(), transport.key.as_str(), mixed.key.as_str()],
            [
                "2801ee2d98aaee9974cea9bc225c176e997ab2a3743765c359bc9e5ef6a1b1d6",
                "c1ab2a63e708828c1272dc662d53b45b6fd17191cc2832b77f3b7252506684fb",
                "d87343a27410d6d05620233e1bb7ca2998b286adb57c69b148c9d903c16a9050"
            ]
        );
    }

    /// From the parts of the instructions of a question with boundaries (a list of single-key objects), pull out the content of key `key`.
    fn part<'a>(question: &'a Value, key: &str) -> &'a Value {
        question["instructions"].as_array().and_then(|parts| parts.iter().find_map(|p| p.get(key))).unwrap_or(&Value::Null)
    }

    /// Keys of the parts of the instructions of a question with boundaries (in the order sent).
    fn part_keys(question: &Value) -> Vec<String> {
        question["instructions"].as_array().map(|parts| parts.iter().filter_map(|p| p.as_object()?.keys().next().cloned()).collect()).unwrap_or_default()
    }

    /// One boundary (the firing example has a why; the silent example has only a code).
    fn sample_line(name: &str, rules: Vec<SemanticQuestion>) -> SemanticLine {
        SemanticLine {
            name: name.into(),
            rules,
            text: format!("{} sentence", name),
            fires: vec![LineExample { code: "(defk f [x] {:tags {:role \"protocol\"}} (g x))".into(), why: Some(format!("{} fires reason", name)) }],
            silent: vec![LineExample { code: "(defk h [x] x)".into(), why: None }],
        }
    }

    /// A boundary goes only into the questions of its rules (name, sentence, firing examples, silent examples, direction of the answer). :tags in an example's code is removed, and an example
    /// with a why is carried as `{"code", "why"}`, one without as `{"code"}` (#1995). A question with boundaries also changes the cache key (changing a boundary goes back to
    /// no answer — as intended).
    #[test]
    fn lines_go_into_the_instructions_of_their_questions_only() {
        use SemanticQuestion::{BusinessDecision, MixedConcerns, PlainCallable, TransportKnowledge};
        let line = sample_line;
        let lines = vec![line("one", vec![BusinessDecision]), line("two", vec![TransportKnowledge]), line("five", vec![MixedConcerns, BusinessDecision])];
        let names = |question: &Value| -> Vec<String> {
            part(question, "lines").as_array().map(|all| all.iter().filter_map(|l| l["name"].as_str().map(str::to_string)).collect()).unwrap_or_default()
        };
        let decision = BusinessDecision.wire_with(&lines);
        assert_eq!(names(&decision), ["one", "five"]);
        assert_eq!(
            part(&decision, "lines")[0],
            json!({"name": "one", "text": "one sentence", "violating_examples": [{"code": "(defk f [x] {} (g x))", "why": "one fires reason"}], "complying_examples": [{"code": "(defk h [x] x)"}]})
        );
        assert_eq!(part(&decision, "question"), &BusinessDecision.wire()["instructions"]["question"]);
        assert_eq!(decision["criteria"], BusinessDecision.wire()["criteria"]);
        assert!(part(&decision, "lines_note").as_str().is_some_and(|note| note.contains("answer true") && note.contains("`why`")));
        let transport = TransportKnowledge.wire_with(&lines);
        assert_eq!(names(&transport), ["two"]);
        assert_eq!(part(&transport, "question"), &TransportKnowledge.wire()["instructions"]["question"]);
        let mixed = MixedConcerns.wire_with(&lines);
        assert_eq!(names(&mixed), ["five"]);
        assert!(part(&mixed, "lines_note").as_str().is_some_and(|note| note.contains("is `mixed`")));
        // Questions that take no boundaries, and questions with no matching boundary, stay as they are.
        assert_eq!(PlainCallable.wire_with(&lines), PlainCallable.wire());
        assert_eq!(TransportKnowledge.wire_with(&lines[..1]), TransportKnowledge.wire());
        let bare = SemanticSettings::validate(&SemanticSection::default(), &mut |_, _| None, &mut Vec::new());
        let lined = SemanticSettings { lines, ..bare.clone() };
        let description = LayerDescription::default();
        let range = { let p = doeff_indexer::hy_index::Position { line: 0, character: 0 }; doeff_indexer::hy_index::Range { start: p, end: p } };
        let asked = |settings: &SemanticSettings| {
            item(settings, "m", BusinessDecision, "a.hy", Path::new("/r/a.hy"), "f", "defk", range, "(defk f [x] x)", LayerId(0), "protocol", &description)
        };
        let (before, after) = (asked(&bare), asked(&lined));
        assert_ne!(before.key, after.key);
        assert_eq!(after.question_json, BusinessDecision.wire_with(&lined.lines));
    }

    /// The instructions of a question with boundaries are sent as a list in declaration order (the question text → general examples and note → how to read the boundaries → the boundaries) —
    /// as an object of keys they would sort by key name, the DOEFF205 general note would come after the boundaries (lines), and "if it differs from the note above, follow the boundaries"
    /// would read upside down. The order also holds in the body string that is sent (a list keeps its order in spelling too).
    #[test]
    fn lined_instructions_go_in_the_declared_order_with_the_lines_last() {
        use SemanticQuestion::{BusinessDecision, MixedConcerns, TransportKnowledge};
        let lines = vec![sample_line("one", vec![BusinessDecision, TransportKnowledge, MixedConcerns])];
        for (question, keys) in [
            (BusinessDecision, vec!["question", "business_decision_examples", "not_business_decision_examples", "lines_note", "lines"]),
            (TransportKnowledge, vec!["question", "note", "transport_knowledge_examples", "not_transport_knowledge_examples", "lines_note", "lines"]),
            (MixedConcerns, vec!["question", "note", "lines_note", "lines"]),
        ] {
            let wired = question.wire_with(&lines);
            assert_eq!(part_keys(&wired), keys, "order of the parts of {}", question.id());
            // Even in the spelling of the body sent (the JSON string sent to the proxy), the content of the parts comes out in declaration order.
            let sent = serde_json::to_string(&wired).unwrap();
            let at: Vec<usize> = keys.iter().map(|key| sent.find(&format!("\"{}\":", key)).unwrap_or_else(|| panic!("{} is missing: {}", key, sent))).collect();
            assert!(at.windows(2).all(|pair| pair[0] < pair[1]), "the order of {} in the sent body differs from the declaration order: {:?}\n{}", question.id(), at, sent);
        }
    }

    /// The DOEFF205 general note states the exceptions of boundary 4 (branching on cases of a typed union, a dict that did not come from outside, a branch that only
    /// refuses with the answer of a function the shape check was delegated to) in the same direction — the previous note called isinstance and dict reads shape checking without exception, contradicting the boundary.
    #[test]
    fn the_mixed_concerns_note_states_the_exceptions_of_the_lines() {
        let note = SemanticQuestion::MixedConcerns.wire()["instructions"]["note"].as_str().unwrap_or_default().to_string();
        for needle in ["untyped input that came from outside", "branching on the cases of a typed union", "did not come from outside", "the shape check was left to"] {
            assert!(note.contains(needle), "the note lacks {:?}: {}", needle, note);
        }
    }

    /// The DOEFF202 frame asks with equal weight about the means of communication (a) and the untyped shape of outside data (b — dicts, reading or building JSON values by field name).
    /// The previous frame covered only the means of communication, and a field-name read that does not write json.loads did not spread even when the repo's boundary and examples were added. The true examples
    /// include a field-name read without json.loads, and the false examples include a core that decides with typed values only.
    #[test]
    fn the_transport_knowledge_frame_weighs_untyped_field_reads_like_transport() {
        let wired = SemanticQuestion::TransportKnowledge.wire();
        let instructions = &wired["instructions"];
        let question = instructions["question"].as_str().unwrap_or_default();
        for needle in ["(a) how communication is carried out", "(b) the untyped shape of outside data", "by field name"] {
            assert!(question.contains(needle), "the question text lacks {:?}: {}", needle, question);
        }
        let note = instructions["note"].as_str().unwrap_or_default();
        for needle in ["(b) weighs the same as (a)", "whether or not the code calls json.loads", "the protocol side's job"] {
            assert!(note.contains(needle), "the note lacks {:?}: {}", needle, note);
        }
        let texts = |key: &str| -> Vec<String> {
            instructions[key].as_array().map(|all| all.iter().filter_map(|t| t.as_str().map(str::to_string)).collect()).unwrap_or_default()
        };
        assert!(texts("transport_knowledge_examples").iter().any(|t| t.contains("no json.loads in the code")), "the true examples lack a field-name read without json.loads");
        assert!(texts("not_transport_knowledge_examples").iter().any(|t| t.contains("receives typed values")), "the false examples lack a core that decides with typed values only");
        assert!(wired["criteria"]["true"].as_str().is_some_and(|t| t.contains("with or without json.loads")));
    }

    #[test]
    fn half_written_definitions_are_not_readable() {
        assert!(readable("(defk f [x] (+ x 1))"));
        assert!(!readable("(defk f [x] (+ x"));
        assert!(!readable("(defk f [x] \"open"));
        assert!(!readable("(defk f [x] x))"));
        assert!(!readable("   "));
    }
}
