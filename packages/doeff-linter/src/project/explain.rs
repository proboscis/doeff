//! Explanation of violations: builds the "what this is (subject)" and "why it is a violation (reason)" sentences.
//!
//! The sentence template for each rule is written in only this one place, and the layer name, layer description, role description, and law text are inserted from the settings (Rust does not
//! hold the meaning of a layer). For a layer with no description in the settings, the sentence is built from only the name, the location, and the rule's convention.
//! The linter says what a violation is and why it is a violation.

use serde::Serialize;

use super::law::LawSpec;
use super::layers::{LayerId, LayerSettings};
use super::raw_settings::RawSettingsSpec;

/// Which layer a file is in, and the grounds for it (the location of the path, and the role declared by the tag).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Placement {
    pub layer: LayerId,
    /// The actual dir of the location that matched (e.g. controllers/land_notice/core).
    pub dir: String,
    /// The name of the service that matched the `*` segment of the location (None if the layer comes first in the path).
    pub service: Option<String>,
    /// The effective tag roles (duplicates removed, in order of appearance).
    pub roles: Vec<String>,
    /// Whether the layer was inferred from the tag role rather than the path (a module outside the declared locations).
    pub by_tags: bool,
}

/// The latter half of the DOEFF120 subject (" used in 3 places (first on line 2, others on lines 5, 9)"). Other lines are listed up to 20, and the rest are given as a count only.
fn json_value_where(uses: &JsonValueUses) -> String {
    const SHOWN: usize = 20;
    let others = match uses.other_lines.len() {
        0 if uses.count <= 1 => return format!(" used in 1 place (line {})", uses.first_line),
        0 => return format!(" used in {} places (all on line {})", uses.count, uses.first_line),
        n if n > SHOWN => format!(
            "{}, … ({} more lines)",
            uses.other_lines[..SHOWN].iter().map(usize::to_string).collect::<Vec<_>>().join(", "),
            n - SHOWN
        ),
        _ => uses.other_lines.iter().map(usize::to_string).collect::<Vec<_>>().join(", "),
    };
    format!(" used in {} places (first on line {}, others on lines {})", uses.count, uses.first_line, others)
}

/// Capitalizes the first character (to start a sentence with a phrase such as "layer core").
fn capitalize(text: &str) -> String {
    let mut chars = text.chars();
    match chars.next() {
        Some(first) => first.to_uppercase().collect::<String>() + chars.as_str(),
        None => String::new(),
    }
}

/// The kind of file in a location that is not declared (DOEFF114).
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum PlaceProblem {
    /// A file directly under the root.
    DirectlyUnderRoot,
    /// A file directly under a service (or shared) dir: it is not inside a layer dir.
    DirectlyUnderService { service: String },
    /// A file inside a dir not in the declarations (a layer-first dir, a dir of an old feature, or an undeclared layer inside a service).
    InUndeclaredDirectory { dir: String },
}

/// The kind of dir not in the declarations (DOEFF115).
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum DirectoryProblem {
    /// A dir directly under the root that is neither a declared service nor shared, foundation, or legacy.
    ServiceNotDeclared { dir: String, services: Vec<String> },
    /// A dir inside a service that is not a layer the service declared.
    LayerNotDeclared { service: String, layer: String, declared: Vec<String> },
}

/// The shape of the class DOEFF119 saw (for the explanation text).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ClassShapeFacts {
    /// The spelling of the written bases (each is a class inside the repo, or absent).
    pub bases: Vec<String>,
    /// Whether a dataclass decorator is attached.
    pub dataclass: bool,
}

/// The DOEFF119 verdict (a closed set; allowed classes make no violation, so they are not here).
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ClassVerdict {
    /// Touches the outside world (a method or a field's initial value has strong evidence of a raw side effect): error. The evidence is spelled `definition: name`.
    ExternalWorld { evidence: Vec<String> },
    /// Holds mutable state (a method rewrites a field of self): warning. Spelled `method: field`.
    Stateful { mutations: Vec<String> },
    /// Fields only (no method with behavior other than dunder methods): an info that recommends defrecord.
    DataOnly,
}

/// Why DOEFF120 did not allow JsonValue in a module (a closed set; allowed modules make no violation, so they are not here).
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum JsonValueRefusal {
    /// It matches none of the :wire-modules in architecture.hy (and it is not a built-in resolver either).
    NotListed,
    /// It matches a pattern of :wire-modules but is not in the foundation layer (only foundation does the sending and receiving itself).
    ListedOutsideFoundation { pattern: String, foundation_dir: Option<String> },
}

/// The uses of JsonValue that DOEFF120 counted (per module). Lines are 1-based.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct JsonValueUses {
    /// The names that appeared (JsonValue, JsonObject, …; duplicates removed, in order of appearance).
    pub names: Vec<String>,
    /// The number of uses (all words in imports, definitions, annotations, and string types).
    pub count: usize,
    /// The line of the first use.
    pub first_line: usize,
    /// The lines of the other uses (duplicates removed; the first line is not included).
    pub other_lines: Vec<usize>,
}

/// Problems with the reason comment of deff (DOEFF111).
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum DeffReasonProblem {
    /// There is no comment.
    Missing,
    /// The reason is empty or "ditto" and its variants (not a reason specific to that definition).
    NoDetail { detail: String },
}

impl DeffReasonProblem {
    /// A short single line (for the message).
    pub fn short(&self) -> String {
        match self {
            DeffReasonProblem::Missing => "no reason comment".to_string(),
            DeffReasonProblem::NoDetail { detail } if detail.is_empty() => "the reason is empty".to_string(),
            DeffReasonProblem::NoDetail { detail } => format!("the reason is \"{}\" (not specific to this definition)", detail),
        }
    }
}

/// The kind of case where the subject of a violation is only a definition name (a business file's name, or a definition's name).
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum NameSubject {
    File { stem: String },
    Definition { name: String, kind: &'static str },
}

/// The material for the explanation of each rule (only facts that the rule's judgment saw).
#[derive(Debug, Clone, PartialEq)]
pub enum Explain {
    /// DOEFF101: imported a module of a layer that is not allowed.
    ImportDirection { placement: Placement, target: String, target_layer: LayerId, target_dir: String },
    /// DOEFF102: imported a module forbidden in the layer.
    ForbiddenModule { placement: Placement, module: String },
    /// DOEFF168: a definition in a business layer branches by comparing with an environment-name value or by a dry-run flag (the kind of spelling that matched).
    EnvironmentBranch { placement: Placement, hit: super::env_branch::BranchHit },
    /// DOEFF169: a keyword field name of a match class pattern has a `-` (the class and the field name).
    MatchFieldHyphen { placement: Placement, class: String, field: String },
    /// DOEFF170 and 171: a defwire in a layer that passes values by type only (wire is true), or a :pre / :post / field-type mapping or untyped tuple (the reason).
    WireFree { placement: Placement, wire: bool, problem: String },
    /// DOEFF172: a field mapping (field is true), or a definition that builds a mapping outside a handler (the reason).
    DictSmell { placement: Placement, field: bool, problem: String },
    /// DOEFF103: put a function in a types-only layer.
    TypesOnly { placement: Placement, functions: Vec<String> },
    /// DOEFF104: there are definitions without tags.
    UntaggedDefinitions { placement: Placement, names: Vec<String> },
    /// DOEFF104: the module declares nothing.
    NoTags { placement: Placement },
    /// DOEFF105: the tag role does not fit the layer (or the role / context is missing).
    RoleMismatch { placement: Placement, role: Option<String>, context: Option<String> },
    /// DOEFF106: a definition directly touches a raw side effect.
    RawDirect { placement: Placement, definition: String, kind: &'static str, evidence: String, category: &'static str, weak: bool },
    /// DOEFF131: a definition outside the permitted roster names a real-I/O handler of doeff listed in :wraps.
    WorldHandlerNamed { placement: Placement, definition: String, wrapped: String, listed_by: String },
    /// DOEFF132: a definition in the permitted roster does not exist or is outside foundation.
    WorldHandlerMisplaced { definition: String, problem: String },
    /// DOEFF133: the kind of test and its mark disagree (edge = an edge but no mark / local but has a mark).
    TestKindMismatch { test: String, edge: bool, mark: String, reached: Vec<String> },
    /// DOEFF137: there is no edge test (a deftest with non-empty :interpreters) that reaches a handler (the roster's spelling) in the permitted roster.
    WorldHandlerWithoutContractTest { handler: String, breach: super::contract_breach::ContractTestBreach },
    /// DOEFF137: there is no contract test (a deftest with non-empty :interpreters) that reaches both of a stand-in roster pair (stand-in, real spelling).
    StandInWithoutContractTest { stand_in: String, real: String, breach: super::contract_breach::ContractTestBreach },
    /// DOEFF135: a test form other than deftest.
    TestFormNotDeftest { form: &'static str, detail: String },
    /// DOEFF146: a vocabulary whose judgment was confined to one place is outside :except.
    VocabularyOutsideSinglePoint { group: String, count: usize, instead: String },
    /// DOEFF148: a spelling restricted to the files where it may be written (group name, reason, kind of hit).
    /// bound = names derived from the group's :names-bound-in (empty = none derived).
    ConfinedSpelling { group: String, why: String, problem: super::confined_spellings::ConfinedProblem, bound: Vec<String> },
    /// DOEFF161: a spelling whose count is fixed (declaration name, reason, kind of hit).
    CountedSpelling { group: String, why: String, problem: super::counted_spellings::CountProblem },
    /// DOEFF162: the whole of an effect declaration (list name, reason, kind of hit).
    EffectCensus { group: String, why: String, problem: super::effect_census::CensusProblem },
    /// DOEFF149: the roster of classes that hold a typed field (list name, spelling of the type, reason, kind of hit).
    FieldHolders { group: String, type_name: String, why: String, problem: super::field_holders::HolderProblem },
    /// DOEFF150: a spelling decided not to be used (group name, spelling that matched, replacement word, and for :in names the definition name).
    RetiredWord { group: String, spelling: String, instead: String, name: Option<String>, place: super::architecture::WordPlace },
    /// DOEFF151: a call decided not to be used.
    RetiredCall { group: String, call: String, instead: String },
    /// DOEFF141: a definition judged only from decided material (declared spelling, reason, kind of hit).
    BlindDefinition { declared: String, why: String, problem: super::blind::BlindProblem },
    /// DOEFF147: a definition whose callable heads are decided (declared spelling, reason, kind of hit).
    AllowedHeads { declared: String, why: String, problem: super::allowed_heads::HeadProblem },
    /// DOEFF159: where and how many times a head may be called (head, reason, kind of hit).
    CallSites { head: String, why: String, problem: super::call_sites::CallSiteProblem },
    /// DOEFF160: groups where broad exception catches are not placed (group name, reason, kind of hit).
    BroadCatches { group: String, why: String, problem: super::broad_catches::BroadCatchProblem },
    /// DOEFF140: a dependency on a module outside the locations (this file's location, the module read, and that file's path from the root).
    PlacedDependency { placement: Placement, owner: String, owner_rel: String },
    /// DOEFF144: a bare mapping or bare tuple in a type annotation of the public surface (where the annotation is, the name, the reason it is red).
    UntypedStructuredValue { what: String, name: String, problem: String },
    /// DOEFF145: an @dataclass in .pyi without kw_only=True (the class name, how the .hy side is written).
    RecordStubNotKwOnly { class: String, form: String },
    ServiceUntestedOnSim { service: String, entry: String, definitions: usize, sim: String },
    /// DOEFF163: a gap in the invariant declarations of a service that has code (service name, text of the gap).
    ServiceInvariantsMissing { service: String, gap: String },
    /// DOEFF173: a gap in the system declarations of a service that has code (service name, text of the gap).
    ServiceSystemMissing { service: String, gap: String },
    /// DOEFF164: a service without counterexamples (service, entry dir, number of sections in the counterexample table, number of sections that answer that service's effects or the foundation's effects).
    ServiceWithoutCounterexample { service: String, entry: String, counterexamples: usize, candidates: usize },
    /// DOEFF167: a gap in counterexamples per clause (service, clause: None for a gap in the declaration, text of the gap).
    ClauseWithoutCounterexample { service: String, clause: Option<String>, gap: String },
    /// DOEFF142: a defhandler's argument takes a client or a mutable store.
    HandlerArgumentHoldsState { handler: String, param: String, kind: &'static str, type_text: String },
    /// DOEFF143, 157, 158: a business-effect fake, a rotten table, a test-only fake, and the responder of an intent's effect.
    BusinessEffectFake { subject: String, reason: String },
    /// DOEFF206: a section where the verification environment itself answers an intent-layer effect, or a row of a table that lets a violation pass lets an intent-layer effect through (subject and reason).
    IntentFakedInVerification { subject: String, reason: String },
    /// DOEFF207: a gap in the read/write fields of a production system's job, or a read with no writer (subject = system::job, the reason text).
    SystemAccessUnwritten { subject: String, reason: String },
    /// DOEFF208: production code wakes a job on a wait for a record change (subject = the definition and the call, the reason text).
    RecordChangeWakesJob { subject: String, reason: String },
    /// DOEFF209: fetching the same thing repeatedly with a time wait in between (subject = the definition and the call, the reason text).
    PollingOnTimer { subject: String, reason: String },
    /// DOEFF210: a program sleeps on the wall clock (subject = the program's name and the sleeping function, the reason text).
    ProgramSleepsOnWallClock { subject: String, reason: String },
    /// DOEFF211: a test sleeps on the wall clock (subject = the name of the wrapping definition and the sleeping function, the reason text).
    TestSleepsOnWallClock { subject: String, reason: String },
    /// DOEFF166: a registry key (source = the registry file it is listed in, rules = the IDs of the rules judged in this run that the key's delimiter points to) matches no finding.
    RegistryEntryStale { key: String, source: String, rules: Vec<String> },
    /// DOEFF165: one row of the intent-effect coverage table (the effect, the owning service, three columns, and the phrase for the gap).
    IntentEffectCoverage { effect: String, service: String, columns: String, gaps: String },
    /// DOEFF155 and 156: a break in the shape of assembly; the translation target or the foundation's answerer is a business effect.
    AssemblyShape { subject: String, reason: String },
    /// A hit from applying DOEFF106 and 131 to a file outside the layer locations (there is no layer description subject, so the message itself is used as the subject).
    WorldOutsideLayers { subject: String },
    /// DOEFF107: a definition reaches a raw side effect through the definitions it calls.
    RawVia { placement: Placement, definition: String, through: Vec<String>, evidence: String, category: &'static str },
    /// DOEFF108: a business name has an environment word.
    EnvironmentName { subject: NameSubject, words: Vec<String> },
    /// DOEFF109: imported a layer guarded by another service.
    ServiceBoundary {
        placement: Placement,
        target: String,
        target_service: String,
        target_layer: LayerId,
        target_dir: String,
        open_layers: Vec<LayerId>,
        shared: Vec<String>,
    },
    /// DOEFF110: a definition of defn / defn/a.
    DefnForbidden { name: String, head: String, declared_kind: Option<super::architecture::ReasonKind> },
    /// DOEFF111: a deff with no reason comment or with a wrong form.
    DeffWithoutReason { name: String, marker: String, problem: DeffReasonProblem, kinds: Vec<super::architecture::ReasonKind> },
    /// DOEFF112: a required key is missing from the definition's :tags.
    DefinitionTagsMissing { name: String, head: String, missing: Vec<String>, has_tags: bool, module_default: bool },
    /// DOEFF114: a file in a location that is not declared.
    UndeclaredPlace { rel: String, problem: PlaceProblem, root: String, destination: String },
    /// DOEFF115: a dir not in the declarations.
    UndeclaredDirectory { dir: String, problem: DirectoryProblem },
    /// DOEFF116: an import that violates the service's dependency declaration.
    ServiceDependency {
        placement: Placement,
        own: String,
        target: String,
        target_service: String,
        target_layer: LayerId,
        target_dir: String,
        declared: bool,
        /// The target already depends on this service (adding it to :depends-on would make the dependency between services a cycle).
        reverse: bool,
        depends_on: Vec<String>,
        /// The layers the target allows this file's layer to read (the layer's :dependency-layers or :open-layers).
        open_layers: Vec<String>,
        /// Whether this file's layer is a layer that :dependency-layers lets read wider than :open-layers (the assembly entry).
        widened: bool,
    },
    /// DOEFF117: a dependency that was declared but is not used.
    UnusedDependency { service: String, dependency: String },
    /// DOEFF201 and 202: Jev's judgment (semantic rules).
    Semantic {
        placement: Placement,
        definition: String,
        kind: &'static str,
        question: super::semantic::SemanticQuestion,
        probability: f64,
    },
    /// DOEFF203: the kind of reason of a declared bare function is unlikely to hold according to Jev's judgment.
    /// DOEFF118: a test function in a test location that is not a deftest.
    TestNotDeftest { name: String, head: String },
    /// DOEFF119: a defclass in business code (touches the outside world / holds mutable state / fields only).
    ClassShape { name: String, shape: ClassShapeFacts, verdict: ClassVerdict },
    /// DOEFF126: a defk definition is called bare.
    BareDefkCall { call: super::bare_calls::BareCall },
    /// DOEFF126: a function received as an argument is called bare (the caller passes a defk or fnk).
    ParamCalledBare { call: super::param_calls::ParamCall },
    /// DOEFF175: an old branch that splits, as a value, a type declared as an effect's absence or failure, on a variable that bound that answer.
    StaleBranch { branch: super::stale_outcome_branches::StaleBranch },
    /// DOEFF127: a defk's `:effects` declaration does not match the inference.
    EffectMismatch { mismatch: super::signatures::EffectMismatch },
    /// DOEFF130: a translation-layer handler emits a business intent (the layer name comes from the settings).
    TranslationIntent { intent: super::signatures::TranslationIntent, handler_layer: String, intent_layer: String },
    /// DOEFF174: a business-layer definition emits an effect of a lower layer, or wraps that package's handler (the layer name, the declaration of the lower-layer module).
    LowerLayerUse { found: super::signatures::LowerLayerUse, layer: String },
    /// DOEFF121-125: smell rules (checking the shape).
    Smell { smell: super::smells::Smell },
    /// DOEFF205: Jev judged that a judgment definition mixes shape checking with business judgment.
    MixedConcerns { name: String, kind: &'static str, probability: f64, layer: String },
    /// DOEFF204: Jev judged that a class with methods that do processing is a window to the outside world or holds state.
    ClassRoleDoubt { name: String, chosen: String, probability: f64, fields: Vec<String> },
    /// DOEFF120: a module that is not allowed uses JsonValue.
    JsonValueUse { module: String, uses: JsonValueUses, refusal: JsonValueRefusal },
    PlainCallableDoubt {
        definition: String,
        kind: &'static str,
        /// The stated reason text.
        stated: String,
        /// The answer Jev chose (an accepted reason, a rejected type, or none) and its probability.
        chosen: String,
        chosen_probability: f64,
        /// Whether the chosen answer is an accepted reason.
        chosen_accepted: bool,
        /// The description of the chosen answer (None for none).
        chosen_description: Option<String>,
        /// How to fix a rejected type (:fix in architecture.hy).
        fix: Option<String>,
        /// The sum of the probabilities of the rejected answers.
        rejected_total: f64,
    },
    /// DOEFF113: the tag's :context disagrees with the service of the location. `service` = the name of the service checked (the name if :dir makes the dir differ from the name).
    ContextMismatch { placement: Placement, service: String, contexts: Vec<String> },
}

/// The explanation of one violation (the explanation field of the output).
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct Explanation {
    pub subject: String,
    pub reason: String,
    /// The verbatim :statement of the ADR law linked to it (null if none).
    pub law_statement: Option<String>,
}

/// A tool for building sentences (the layer settings, and the layers allowed raw side effects).
pub struct Narrator<'a> {
    pub layers: Option<&'a LayerSettings>,
    pub raw: Option<&'a RawSettingsSpec>,
}

impl<'a> Narrator<'a> {
    /// Builds the explanation (if a law is linked, its :statement is attached verbatim).
    pub fn explain(&self, explain: &Explain, law: Option<&LawSpec>) -> Explanation {
        let (subject, reason) = match explain {
            Explain::ImportDirection { placement, target, target_layer, target_dir } => (
                format!("import target {} is in {} (path under {}/) — {}", target, self.layer_phrase(*target_layer), target_dir, self.file_subject(placement)),
                format!(
                    "{} {} may import only {}. {} reading it from {} makes {} touch {} (even if handlers are swapped in the simulation, only that place touches the real thing).",
                    self.character(placement.layer),
                    self.name(placement.layer),
                    self.allowed_imports(placement.layer),
                    self.target_character(*target_layer),
                    self.name(placement.layer),
                    self.name(placement.layer),
                    format!("what layer {} holds", self.name(*target_layer))
                ),
            ),
            Explain::MatchFieldHyphen { placement, class, field } => (
                format!("match class pattern ({} :{} …) — {}", class, field, self.file_subject(placement)),
                "Hy's match emits the class pattern's keyword into Python's case without mangling it to an attribute name (`:a-b` becomes `case Class(a-b=…)`). No value has an attribute `a-b`, so that clause never matches whatever the value is and silently falls through to the next clause (often the default `_`). A match inside defk is not flagged because doeff-hy rewrites field names to attribute names (this hit is outside defk).".to_string(),
            ),
            Explain::DictSmell { placement, field, problem } => (
                if *field { format!("field mapping ({}) — {}", problem, self.file_subject(placement)) } else { format!("{} — {}", problem, self.file_subject(placement)) },
                "agora's data lives in the DB and is read through effects. An in-memory mapping exists only for caching, and the cache is held by the effect layer (handler). A definition that builds a mapping outside a handler, or a field mapping, is a smell of carrying data around in an untyped form. A single point whose purpose is to build a mapping (a definition that spells it out at the JSON boundary, where :post is the mapping) is not counted.".to_string()
            ),
            Explain::WireFree { placement, wire, problem } => (
                if *wire { format!("defwire declaration — {}", self.file_subject(placement)) } else { format!("{} — {}", problem, self.file_subject(placement)) },
                "This layer passes values by type only (defwire is not placed in core; a mapping that fetches data is not passed but made a key -> value function or an effect). The spelling that builds the wire's type and mapping is placed at one point in the translation layer, and this layer only receives and returns types that have field names and types (defrecord). If a mapping or an untyped tuple gets through, checks that read which fields exist creep into this layer.".to_string()
            ),
            Explain::EnvironmentBranch { placement, hit } => (
                match hit {
                    super::env_branch::BranchHit::Value(value) => format!("comparison with the environment-name value \"{}\" — {}", value, self.file_subject(placement)),
                    super::env_branch::BranchHit::Flag(flag) => format!("branch on the dry-run flag {} — {}", flag, self.file_subject(placement)),
                },
                "If a business layer branches on an environment name or a dry-run flag, the business layer comes to know the environment and takes different paths in production and in the simulation (the path checked in the simulation is no longer the production path). Express differences between environments only by swapping the set of handlers (:environment-branches in architecture.hy).".to_string(),
            ),
            Explain::ForbiddenModule { placement, module } => (
                format!("import target {} (an I/O module forbidden in layer {}) — {}", module, self.name(placement.layer), self.file_subject(placement)),
                format!(
                    "{} {} is a module that touches the outside world directly, so it is not used in this layer. I/O is held by the handlers of {}, and this layer emits effects.",
                    self.character(placement.layer),
                    module,
                    self.io_layers()
                ),
            ),
            Explain::TypesOnly { placement, functions } => (
                format!("definition {} (function / handler) — {}", functions.join(", "), self.file_subject(placement)),
                format!(
                    "{} This layer holds only type declarations, so functions and handlers that contain processing cannot be placed here (move them to another layer).",
                    self.character(placement.layer)
                ),
            ),
            Explain::UntaggedDefinitions { placement, names } => (
                format!("definition {} (no tags) — {}", names.join(", "), self.file_subject(placement)),
                format!(
                    "The layer dir decides only the distance from the outside world; context and role are declared by a definition's tags. Without tags, it cannot be checked whether this definition has a role allowed in layer {} ({}).",
                    self.name(placement.layer),
                    self.allowed_roles(placement.layer)
                ),
            ),
            Explain::NoTags { placement } => (
                format!("this module declares no tags at all — {}", self.file_subject(placement)),
                format!(
                    "The layer dir decides only the distance from the outside world; context and role are declared by tags. Without tags, it cannot be checked whether this module has a role allowed in layer {} ({}).",
                    self.name(placement.layer),
                    self.allowed_roles(placement.layer)
                ),
            ),
            Explain::RoleMismatch { placement, role, context } => (self.role_subject(placement, role, context), self.role_reason(placement, role, context)),
            Explain::RawDirect { placement, definition, kind, evidence, category, weak } => (
                format!(
                    "definition {} ({}) directly touches {} (a raw side effect of {}; {}) — {}",
                    definition,
                    kind,
                    evidence,
                    category,
                    if *weak { "weak evidence found only from a method name" } else { "strong evidence: a name via an import, or a built-in" },
                    self.file_subject(placement)
                ),
                format!(
                    "{} Doing {} I/O directly means that, even if handlers are swapped in the simulation, only this definition touches the real world. Emit an effect and let the handlers of {} answer.",
                    self.character(placement.layer),
                    category,
                    self.io_layers()
                ),
            ),
            Explain::WorldHandlerNamed { placement, definition, wrapped, listed_by } => (
                format!("{} names doeff's real-I/O handler {} — {}", definition, wrapped, self.file_subject(placement)),
                format!(
                    "{} is a handler that touches the outside world, and in the permitted roster only {} may run it inside. Wrapping it directly here means that, even if the set of handlers is swapped in the simulation, only this place touches the real world. Use the roster's definition {}, or add this definition to the roster (place it in the foundation layer).",
                    wrapped, listed_by, listed_by
                ),
            ),
            Explain::WorldHandlerMisplaced { definition, problem } => (
                format!("definition {} in the permitted roster — {}", definition, problem),
                "Definitions allowed to touch the outside world are placed only in layer foundation (in a fixed dir, under rules). If the roster has an entry with no real definition, or the definition is outside foundation, the roster no longer pins down where the outside world is touched.".to_string()
            ),
            Explain::TestKindMismatch { test, edge, mark, reached } => (
                if *edge {
                    format!("test {} reaches the outside world (edge) but lacks the mark {}", test, mark)
                } else {
                    format!("test {} does not reach the outside world (local) but has the mark {}", test, mark)
                },
                if *edge {
                    format!("Path reached: {}. An edge test is excluded from the default pytest by a mark; without the mark, real I/O gets mixed into the list of local tests.", reached.join(" → "))
                } else {
                    "The mark identifies an edge test; attaching it to a local test removes it from the default pytest so it no longer runs.".to_string()
                },
            ),
            Explain::WorldHandlerWithoutContractTest { handler, breach: super::contract_breach::ContractTestBreach::NoEdgeTest } => (
                format!("handler {} in the permitted roster has no edge test", handler),
                "Edge test = a test that lines up the real one (this handler) and the simulated interpreter in :interpreters and runs the same deftest through both. Without it, nobody checks that the simulation answers the same as the real one, and even if the local tests are green, production's answers have not been verified. For a handler that needs no test, write a :contract-test with a reason on the roster's row (how to write it is in the fix for DOEFF137).".to_string(),
            ),
            Explain::WorldHandlerWithoutContractTest { handler, breach: super::contract_breach::ContractTestBreach::NoneWithoutReason } => (
                format!("handler {} in the permitted roster: :contract-test none has no reason", handler),
                "A none without a reason leaves nobody able to verify the grounds for not requiring an edge test. Write the name of the test that verifies the contract as the reason (how to write it is in the fix for DOEFF137).".to_string(),
            ),
            Explain::StandInWithoutContractTest { stand_in, real, breach: super::contract_breach::ContractTestBreach::NoEdgeTest } => (
                format!("stand-in {} has no contract test with the real one {}", stand_in, real),
                "Contract test = a test that lines up the real one and the stand-in's interpreters in :interpreters, runs the same deftest through both, and compares the answers (verified fake, Pact style). Without it, even if the real one changes the stand-in keeps its old answers and the simulation stays green, and code that passed meets different answers in production. The stand-in's owner maintains the test, and only for pairs where the real one cannot be prepared, a :contract-test with a reason is written on the roster's row (how to write it is in the fix for DOEFF137).".to_string(),
            ),
            Explain::StandInWithoutContractTest { stand_in, breach: super::contract_breach::ContractTestBreach::NoneWithoutReason, .. } => (
                format!("stand-in {}: :contract-test none has no reason", stand_in),
                "A none without a reason leaves nobody able to verify the grounds for not requiring the stand-in's contract test. Write the name of the test that verifies the contract as the reason (how to write it is in the fix for DOEFF137).".to_string(),
            ),
            Explain::TestFormNotDeftest { form, detail } => (
                format!("test form {} — {}", form, detail),
                "Tests are deftest only. Checks that run outside pytest and pytest-style Python forms go unnoticed even when they turn red (measured in #1104: five checks outside pytest were left red).".to_string(),
            ),
            Explain::VocabularyOutsideSinglePoint { group, count, instead } => (
                format!("vocabulary {} has {} lines outside :except — {}", group, count, instead),
                "The rule is that the judgment on this vocabulary is confined to the one point that :single-point-vocabulary in architecture.hy names. If another file reads the same vocabulary and copies the judgment, then when it is fixed the copy is forgotten and the two judgments disagree.".to_string(),
            ),
            Explain::ConfinedSpelling { group, why, problem, bound } => match problem {
                super::confined_spellings::ConfinedProblem::Outside { count } => (
                    format!("spelling group {} appears in {} places outside the files where it may be written", group, count),
                    format!(
                        "The files where this spelling may be written are decided by :except of :confined-spellings in architecture.hy. Writing it in an outside file creates a second place that does the same thing without going through that entry point. {}Reason: {}",
                        if bound.is_empty() {
                            String::new()
                        } else {
                            format!("The group's spellings also include names bound to a value that matches the group's spelling in the files of :names-bound-in (derived names: {}). ", bound.join(", "))
                        },
                        why
                    ),
                ),
                super::confined_spellings::ConfinedProblem::Missing => (
                    format!("no file matches :files of spelling group {}", group),
                    format!("If there is no file to read, the rule turns green without seeing anything (a population of 0 is not made green). Reason: {}", why),
                ),
            },
            Explain::CountedSpelling { group, why, problem } => match problem {
                super::counted_spellings::CountProblem::Mismatch { found, wanted, within } => (
                    match within {
                        Some(name) => format!("counted spelling {} appears {} times inside definition {} (expected {})", group, found, name, wanted.spelling()),
                        None => format!("counted spelling {} appears {} times (expected {})", group, found, wanted.spelling()),
                    },
                    format!("The count of this spelling is fixed by :counted-spellings in architecture.hy. If the count changes, either more places doing the same thing have appeared outside the decided entry point, or the decided entry point is gone. Reason: {}", why),
                ),
                super::counted_spellings::CountProblem::Missing { reason } => (
                    format!("counted spelling {} has nothing to count ({})", group, reason),
                    format!("If there is no file or definition to count, the rule turns green without seeing anything (a population of 0 is not made green). Reason: {}", why),
                ),
            },
            Explain::EffectCensus { group, why, problem } => {
                let rule = "The whole of effects is closed by the list of :effect-census in architecture.hy. An effect that grew silently creates a new request to the outside beyond the decided entry point. ";
                match problem {
                    super::effect_census::CensusProblem::Unlisted { effect } => {
                        (format!("effect {} is declared outside list {}", effect, group), format!("{}Reason: {}", rule, why))
                    }
                    super::effect_census::CensusProblem::Twice { effect } => {
                        (format!("effect {} is declared twice (list {})", effect, group), format!("{}Reason: {}", rule, why))
                    }
                    super::effect_census::CensusProblem::Undeclared { effect } => {
                        (format!("the declaration of effect {} in list {} is missing", effect, group), format!("{}Reason: {}", rule, why))
                    }
                    super::effect_census::CensusProblem::NoFiles => (
                        format!("no file matches :files of list {}", group),
                        format!("If there is no file to read, the rule turns green without seeing anything (a population of 0 is not made green). Reason: {}", why),
                    ),
                }
            }
            Explain::FieldHolders { group, type_name, why, problem } => {
                let rule = format!(
                    "The classes that may hold a field of {} are closed by the list of :field-holders in architecture.hy. If another class grows the same field, that type's values have two homes. ",
                    type_name
                );
                match problem {
                    super::field_holders::HolderProblem::Unlisted { class } => {
                        (format!("class {} holds a field of {} (outside the holder list {})", class, type_name, group), format!("{}Reason: {}", rule, why))
                    }
                    super::field_holders::HolderProblem::Absent { class } => {
                        (format!("class {} in holder list {} does not hold a field of {}", class, group, type_name), format!("{}Reason: {}", rule, why))
                    }
                    super::field_holders::HolderProblem::NoClass { class } => {
                        (format!("class {} named by holder list {} does not exist", class, group), format!("{}Reason: {}", rule, why))
                    }
                    super::field_holders::HolderProblem::NoFiles => (
                        format!("no Python file matches :files of holder list {}", group),
                        format!("If there is no file to read, the rule turns green without seeing anything (a population of 0 is not made green). Reason: {}", why),
                    ),
                }
            }
            Explain::RetiredWord { group, spelling, instead, name, place } => (
                match (place, name) {
                    (super::architecture::WordPlace::Paths, _) => format!("file-name spelling {} (a name decided not to be used — group {})", spelling, group),
                    (_, Some(name)) => format!("definition name {} (spelling {} decided not to be used — group {})", name, spelling, group),
                    (_, None) => format!("spelling {} (a word decided not to be used — group {})", spelling, group),
                },
                format!("This repo decided in :retired-words of architecture.hy not to use this spelling (replacement: {}). If an old word remains, code and documents multiply that call the same thing by two names, and readers mistake one for another.", instead),
            ),
            Explain::BlindDefinition { declared, why, problem } => (
                match problem {
                    super::blind::BlindProblem::ReadsWord { reached, word } => {
                        format!("body spelling {} of definition {} reached from {}", word, reached, declared)
                    }
                    super::blind::BlindProblem::Imports { module } => format!("import {} in the module of {}", module, declared),
                    super::blind::BlindProblem::Missing { reason } => format!("declared definition {} ({})", declared, reason),
                },
                match problem {
                    super::blind::BlindProblem::Missing { .. } => format!(
                        "If there is no definition that :blind-definitions in architecture.hy names, the rule turns green without seeing anything (a population of 0 is not made green). Reason: {}",
                        why
                    ),
                    super::blind::BlindProblem::ReadsWord { .. } | super::blind::BlindProblem::Imports { .. } => format!(
                        "This definition was declared in :blind-definitions of architecture.hy to be judged only from decided material. If a definition it reaches reads other material, or the module has a dependency, the judgment changes with material outside the declaration. Reason: {}",
                        why
                    ),
                },
            ),
            Explain::CallSites { head, why, problem } => (
                super::call_sites::describe(head, problem),
                match problem {
                    super::call_sites::CallSiteProblem::Missing { .. } | super::call_sites::CallSiteProblem::Empty => format!(
                        "If there is no place that :call-sites in architecture.hy names, or no file to search, the rule turns green without seeing anything (a population of 0 is not made green). Reason: {}",
                        why
                    ),
                    super::call_sites::CallSiteProblem::Outside { .. }
                    | super::call_sites::CallSiteProblem::Count { .. }
                    | super::call_sites::CallSiteProblem::Parent { .. }
                    | super::call_sites::CallSiteProblem::Branch { .. } => format!(
                        "Where and how many times this head may be called is decided by :call-sites in architecture.hy. A call outside the places, or a mismatch in count, means the boundary confined to one point has silently grown a second one, or the boundary has come undone. Reason: {}",
                        why
                    ),
                },
            ),
            Explain::BroadCatches { group, why, problem } => (
                super::broad_catches::describe(group, problem),
                match problem {
                    super::broad_catches::BroadCatchProblem::Missing { .. } | super::broad_catches::BroadCatchProblem::Empty => format!(
                        "If there is no carrier boundary that :broad-catches in architecture.hy names, or no file to search, the rule turns green without seeing anything (a population of 0 is not made green). Reason: {}",
                        why
                    ),
                    super::broad_catches::BroadCatchProblem::Outside { .. } | super::broad_catches::BroadCatchProblem::Unbound { .. } => format!(
                        "A broad exception catch swallows even contract violations (:pre / :post, type bindings, assert). The only thing permitted is, per :carriers of :broad-catches in architecture.hy, a boundary that binds the caught exception to a name, puts it on a decided event, and carries it to the queue. Reason: {}",
                        why
                    ),
                },
            ),
            Explain::AllowedHeads { declared, why, problem } => match problem {
                super::allowed_heads::HeadProblem::Unlisted { head } => (
                    format!("({} …) inside {} (outside the list of heads that may be called)", head, declared),
                    format!(
                        "The heads that may be called inside this definition are decided by :allowed-heads in architecture.hy. If a call outside the list raises an exception, the whole process goes down outside the boundary that would catch it. Reason: {}",
                        why
                    ),
                ),
                super::allowed_heads::HeadProblem::Missing { reason } => (
                    format!("declared definition {} ({})", declared, reason),
                    format!("If there is no definition that :allowed-heads in architecture.hy names, the rule turns green without seeing anything (a population of 0 is not made green). Reason: {}", why),
                ),
            },
            Explain::RetiredCall { group, call, instead } => (
                format!("call ({} …) (a call decided not to be used — group {})", call, group),
                format!("This repo retired this call in :retired-calls of architecture.hy (replacement: {}). If places that call a retired thing remain, two things with the same role sit side by side, and coordinates and answers silently disagree.", instead),
            ),
            Explain::PlacedDependency { placement, owner, owner_rel } => (
                format!("import target {} (path {}; outside the layer locations) — {}", owner, owner_rel, self.file_subject(placement)),
                format!("Depends on module {} ({}) whose location is not decided. The layers of :placed-dependencies in architecture.hy depend only on modules inside the layer locations. A module outside the locations is outside the layer rules (direction, tags, service boundaries), so reading it bypasses those rules.", owner, owner_rel),
            ),
            Explain::UntypedStructuredValue { what, name, problem } => (
                format!("type of {} {} — {}", what, name, problem),
                "Carrying a structured value as a bare mapping or bare tuple hides field names and types statically, and spelling mistakes and field mismatches are not found until runtime (type checking protects nothing). Express it with a type that has field names and types.".to_string(),
            ),
            Explain::RecordStubNotKwOnly { class, form } => (
                format!("type declaration class {} (the .hy side is {})", class, form),
                "The runtime __init__ accepts fields only by name, but the type declaration allows positional arguments. Type checking passes calls with positional arguments, but at runtime they raise TypeError: the type declaration misrepresents the runtime shape.".to_string(),
            ),
            Explain::ServiceUntestedOnSim { service, entry, definitions, sim } => (
                format!("assembly {} of service {} ({} definitions)", entry, service, definitions),
                format!("It is not reached by following calls and references from any deftest under {}. A service that is not run with only the handlers swapped while keeping the production assembly has not verified its business invariants (do not deploy unverified).", sim),
            ),
            Explain::ServiceInvariantsMissing { service, gap } => (
                format!("service {} :invariants", service),
                format!("{}. For business logic to be \"tested\", each service needs a pure invariant function that takes a record and returns a list of violations — if the declaration is missing, the naming is off, or the function is not a judgment, the linter cannot count what was verified.", gap)
            ),
            Explain::ServiceSystemMissing { service, gap } => (
                format!("service {} :system", service),
                format!("{}. If a defsystem exists, that system is automatically placed in the test of the generic simulated cluster and run — a system whose declaration is missing, that is not a defsystem, or whose base is not a single typed one cannot have its base assembled by the test and is not placed (#2187).", gap)
            ),
            Explain::ServiceWithoutCounterexample { service, entry, counterexamples, candidates } => (
                format!("assembly {} of service {}", entry, service),
                format!(
                    "Of the {} sections of the counterexample table, the number that answer this service's effects or the foundation's effects is {} — no deftest reaching any of them reaches {}. A service that has not verified that its invariants turn red with a deliberately broken handler cannot tell whether its tests can distinguish a broken answer even when they are green.",
                    counterexamples, candidates, entry
                ),
            ),
            Explain::ClauseWithoutCounterexample { service, clause, gap } => (
                match clause {
                    Some(clause) => format!("clause {} of service {}", clause, service),
                    None => format!("service {} :clauses", service),
                },
                format!("{}. Having just one counterexample for a service cannot tell whether broken answers that violate other clauses are distinguished — each clause needs a counterexample from a deliberately broken handler that violates that clause (the `breaks:` of a counterexample table row), or a reason for having no counterexample (:clause-exemptions) (ADR R2).", gap)
            ),
            Explain::HandlerArgumentHoldsState { handler, param, kind, type_text } => (
                format!("argument {} of handler {} ({}{})", param, handler, kind, if type_text.is_empty() { String::new() } else { format!(" {}", type_text) }),
                "A client or store received as an argument cannot be swapped or observed from the outer handler, so the simulated environment cannot run by swapping handlers alone. Read the connection target, credentials, and settings with Ask, create the client only once with (session val client …) at the top of the handler body, and hold state with (session var …).".to_string(),
            ),
            Explain::BusinessEffectFake { subject, reason } => (subject.clone(), reason.clone()),
            Explain::IntentFakedInVerification { subject, reason } => (subject.clone(), reason.clone()),
            Explain::SystemAccessUnwritten { subject, reason } => (subject.clone(), reason.clone()),
            Explain::RecordChangeWakesJob { subject, reason } => (subject.clone(), reason.clone()),
            Explain::PollingOnTimer { subject, reason } => (subject.clone(), reason.clone()),
            Explain::ProgramSleepsOnWallClock { subject, reason } => (subject.clone(), reason.clone()),
            Explain::TestSleepsOnWallClock { subject, reason } => (subject.clone(), reason.clone()),
            Explain::RegistryEntryStale { key, source, rules } => (
                format!("key {} of registry {}", key, source),
                format!("This run applied rules {} to the whole repo, but no finding matches this key. If a row is forgotten after the listed violation is fixed, then even when the violation returns at the same place the registry silently lowers it to a warning (a registry that is supposed to only shrink stays loose).", rules.join(", "))
            ),
            Explain::IntentEffectCoverage { effect, service, columns, gaps } => (
                format!("intent effect {} of service {} ({})", effect, service, gaps),
                format!("{}. An intent effect becomes a business operation tested locally only when a definition reachable from the local tests emits it and there is an answerer reachable from both the simulated root and the production entry.", columns)
            ),
            Explain::AssemblyShape { subject, reason } => (subject.clone(), reason.clone()),
            Explain::WorldOutsideLayers { subject } => (
                subject.clone(),
                "Even for files outside the layer locations, only the definitions of :world-handlers in architecture.hy may touch the outside world. Emit an effect and let the roster definition's handler answer, or move the location to foundation and add it to the roster.".to_string()
            ),
            Explain::RawVia { placement, definition, through, evidence, category } => (
                format!("definition {} reaches, through {}, {} (a raw side effect of {}) — {}", definition, through.join(" → "), evidence, category, self.file_subject(placement)),
                format!(
                    "{} does not touch it directly itself, but beyond the definition {} that it calls, it reaches {} I/O (a notice, not a violation). If the end of the path is replaced with an effect, {} stays away from the outside world.",
                    definition,
                    through.first().map(String::as_str).unwrap_or("?"),
                    category,
                    self.name(placement.layer)
                ),
            ),
            Explain::ServiceBoundary { placement, target, target_service, target_layer, target_dir, open_layers, shared } => {
                let own = placement.service.clone().unwrap_or_default();
                let open: Vec<String> = open_layers.iter().map(|id| format!("layer {}", self.name(*id))).collect();
                let mut readable = open.clone();
                if !shared.is_empty() {
                    readable.push(format!("shared location {}", shared.join(", ")));
                }
                (
                    format!(
                        "import target {} is in service {}'s {} (path under {}/) — {}",
                        target,
                        target_service,
                        self.layer_phrase(*target_layer),
                        target_dir,
                        self.file_subject(placement)
                    ),
                    format!(
                        "Service {} (layer {}) is reading service {} (layer {}). Reading judgments or translations across services means that when the contents of {} change, {} breaks. Requests to {} go through {}'s intents (the only things another service may read are {}).",
                        own,
                        self.name(placement.layer),
                        target_service,
                        self.name(*target_layer),
                        target_service,
                        own,
                        target_service,
                        target_service,
                        if readable.is_empty() { "none".to_string() } else { readable.join(", ") }
                    ),
                )
            }
            Explain::ContextMismatch { placement, service, contexts } => (
                format!("tag :context = {} — {}", contexts.join(", "), self.file_subject(placement)),
                format!(
                    "The dir points to service {}, but the tag declares context {}. The same file looks like a different service when looked up by tag and when looked at by dir. Match :context to the service name, or move the file to the dir of {} (notice).",
                    service,
                    contexts.join(", "),
                    contexts.join(", ")
                ),
            ),
            Explain::ClassShape { name, shape, verdict } => {
                let mut parts = vec!["defclass".to_string()];
                if shape.dataclass {
                    parts.push("dataclass".to_string());
                }
                if !shape.bases.is_empty() {
                    parts.push(format!("base {} (in the repo)", shape.bases.join(", ")));
                }
                match verdict {
                    ClassVerdict::ExternalWorld { evidence } => (
                        format!("definition {} ({}) — a class that touches the outside world (evidence {})", name, parts.join(", "), evidence.join(", ")),
                        "A method or a field's initial value touches a raw side effect (http, DB, file, process, clock …). If a window to the outside world is written with a class and methods, it does not go through the swapping of effects and handlers, so even when swapped in the simulation only this class touches the real thing, and it appears in neither the browsing panel nor the linter's layer rules.".to_string(),
                    ),
                    ClassVerdict::Stateful { mutations } => (
                        format!("definition {} ({}) — a class that holds mutable state (rewrites {})", name, parts.join(", "), mutations.join(", ")),
                        "A method rewrites a field of self. If state is scattered inside values, the effect and handler records cannot trace where and what changed, and the simulation and the real thing cannot be verified to follow the same flow.".to_string(),
                    ),
                    ClassVerdict::DataOnly => (
                        format!("definition {} ({}; fields only)", name, parts.join(", ")),
                        "A data class with fields only can be written with defrecord. defrecord declares context and role with :tags and checks values with :check (move __post_init__ checks to :check). (A notice, not a violation)".to_string(),
                    ),
                }
            }
            Explain::Smell { smell } => smell_text(smell),
            Explain::ParamCalledBare { call } => (
                format!(
                    "definition {} ({}) calls argument {} bare — caller {} passes {} there",
                    call.definition, call.container, call.param, call.caller, call.passed
                ),
                format!(
                    "The caller passes {} a function that returns a Program ({}), so calling {} bare returns a Program, not an answer. That Program flows on as a value without failing on a type error, and quietly goes wrong where the answer was supposed to be used (isinstance is always false, an index is always empty …). Receive it with (<- x ({} …)).",
                    call.param, call.passed, call.param, call.param
                ),
            ),
            Explain::EffectMismatch { mismatch } => match mismatch {
                super::signatures::EffectMismatch::Undeclared { definition, effect, via, .. } => (
                    match via {
                        Some(via) => format!("defk {} raises effect {} via {}", definition, effect, via),
                        None => format!("defk {} fires effect {}", definition, effect),
                    },
                    format!(
                        "{} declares :effects, but {} is not in it. :effects is a declaration of the effects a definition raises, and is material for checking whether a set of handlers receives them: if it raises an effect that is not declared, the set of handlers assembled trusting the declaration cannot receive it all. The inference collects the calls fired in the body ((<- …) and (! …)) following into defk (the part received by a handler is not subtracted).",
                        definition, effect
                    ),
                ),
                super::signatures::EffectMismatch::Unused { definition, effect, .. } => (
                    format!("{} in :effects of defk {}", effect, definition),
                    format!(
                        "{} writes {} in :effects, but following the calls fired in the body into defk, it does not raise {}. A declaration of an effect that is not raised demands a receiver the set of handlers does not need.",
                        definition, effect, effect
                    ),
                ),
            },
            Explain::TranslationIntent { intent, handler_layer, intent_layer } => (
                match intent.via() {
                    Some(via) => format!(
                        "handler {} (layer {}) emits intent {} of layer {} via {}",
                        intent.handler, handler_layer, intent.effect(), intent_layer, via
                    ),
                    None => format!("handler {} (layer {}) emits intent {} of layer {}", intent.handler, handler_layer, intent.effect(), intent_layer),
                },
                format!(
                    "A handler of layer {} is a translation that re-emits the intents it receives as doeff's generic effects (HttpRequest, reading and writing records, clock, file, process, Ask …), and has no business flow. {} is a business intent of layer {}, so emitting it makes the translating handler hold business judgment and flow, and the boundary of responsibility collapses. The same holds when going through an imported function: the inference follows the calls executed in the body ((<- …) and (! …)) beyond defk. Place the flow that emits intents in a program of layer core.",
                    handler_layer, intent.effect(), intent_layer
                ),
            ),
            Explain::LowerLayerUse { found, layer } => (
                match (&found.kind, found.via()) {
                    (super::signatures::LowerLayerKind::Handler, _) => format!("definition {} (layer {}) wraps the lower layer's handler {}", found.definition, layer, found.name()),
                    (_, Some(via)) => format!("definition {} (layer {}) emits the lower layer's effect {} via {}", found.definition, layer, found.name(), via),
                    (_, None) => format!("definition {} (layer {}) emits the lower layer's effect {}", found.definition, layer, found.name()),
                },
                format!(
                    "{} belongs to a module listed in :lower-layer-modules of architecture.hy (raw operations of the record service). Reading and writing raw data is the handler's job: a business layer emits domain effects, and a translation layer's handler rewrites them into raw operations. If a business layer emits raw effects, or wraps a handler that answers raw effects itself, the business flow comes to know how records are placed, and the boundary that exposes only the domain API to the outside collapses. The same holds when going through an imported defk: the inference follows the calls executed in the body ((<- …) and (! …)) beyond defk.",
                    found.qualified
                ),
            ),
            Explain::StaleBranch { branch } => (
                format!("{} of definition {} (effect {} declared {} as {})", branch.form.word(), branch.definition, branch.effect, branch.ty, branch.declared_word()),
                format!(
                    "{} declared {} as {}. <- and ! do not bind the declared type among the answers of {}; they let it escape to the caller's scope as {} (the bound {} holds only the success value), so this branch that splits {} as a value ({}) never matches. It is an old branch left after the effect was switched to the declared version, and without matching it silently falls through to the next branch (often the success branch).",
                    branch.effect,
                    branch.ty,
                    branch.declared_word(),
                    branch.effect,
                    match branch.declared {
                        super::smells::DeclaredAs::Absent => "Absent",
                        super::smells::DeclaredAs::Failure => "Raise",
                    },
                    branch.subject,
                    branch.ty,
                    branch.form.word()
                ),
            ),
            Explain::BareDefkCall { call } => (
                format!("definition {} ({}) calls defk {} bare", call.definition, call.container, call.callee),
                format!(
                    "{} is a defk, so calling it bare returns a Program, not an answer. That Program flows on as a value without failing on a type error, and quietly goes wrong where the answer was supposed to be used (a dict's .get, a comparison, embedding in a string). Call it where a Program is passed: the right-hand side of (<- x …), (! …), (return …), or an argument of a call that receives a Program. Even as an argument of a call to a defk of the repo, if the receiving argument's :pre type cannot accept a Program (a built-in value type, None, or a repo record), it is a place where the answer is used: an unexecuted Call is passed along and falls over only at run time, when it first meets the :pre type check. Make it a value with (<- x …) and then pass it.",
                    call.callee
                ),
            ),
            Explain::MixedConcerns { name, kind, probability, layer } => (
                format!("definition {} ({})", name, kind),
                format!(
                    "Jev's judgment p={:.2}: likely that a definition mixes checking of the input's shape (reading dictionary keys, isinstance, emptiness checks) with business judgment. A definition in layer {} receives typed values and only judges: if shape checks are scattered inside judgments, the type cannot separate what is a difference in input shape from what is a business refusal. (A semantic judgment — if wrong, add it to the list of misjudgments)",
                    probability, layer
                ),
            ),
            Explain::ClassRoleDoubt { name, chosen, probability, fields } => (
                format!(
                    "definition {} (defclass; fields {})",
                    name,
                    if fields.is_empty() { "none".to_string() } else { fields.join(", ") }
                ),
                match chosen.as_str() {
                    "external-world" => format!(
                        "Jev's judgment p={:.2}: a window to the outside world — likely holds a client or store passed from outside as a field and uses it in methods (even if no raw call is visible). Making it a class method does not go through the swapping of effects and handlers. (A semantic judgment — if wrong, add it to the list of misjudgments)",
                        probability
                    ),
                    _ => format!(
                        "Jev's judgment p={:.2}: a class that holds state — likely that a method changes the state of itself or of something else. If state is scattered inside values, the effect and handler records cannot trace where and what changed. (A semantic judgment — if wrong, add it to the list of misjudgments)",
                        probability
                    ),
                },
            ),
            Explain::JsonValueUse { module, uses, refusal } => (
                format!("module {} — {}{}", module, uses.names.join(", "), json_value_where(uses)),
                format!(
                    "JsonValue is a type that merely wraps a bare dict, list, str … with a name, and promises nothing about the shape inside. If the user side takes it apart and reads it by hand, mismatches of shape leak out in a different form at each place that reads. Only the generic resolver that defwire generates (doeff_hy._wire, doeff_records._wire) and the sending and receiving modules of the foundation layer listed in :wire-modules of architecture.hy may touch JSON; other modules (protocol, intent, core …) see only typed values whose shape the resolver checked once. {}",
                    match refusal {
                        JsonValueRefusal::NotListed => "This module is not in :wire-modules.".to_string(),
                        JsonValueRefusal::ListedOutsideFoundation { pattern, foundation_dir: Some(dir) } => {
                            format!("This module matches {} in :wire-modules but is not in the foundation layer ({}/), so it is not allowed: only foundation does the sending and receiving itself.", pattern, dir)
                        }
                        JsonValueRefusal::ListedOutsideFoundation { pattern, foundation_dir: None } => {
                            format!("This module matches {} in :wire-modules, but architecture.hy has no foundation layer, so it is not allowed.", pattern)
                        }
                    }
                ),
            ),
            Explain::TestNotDeftest { name, head } => (
                format!("definition {} ({}) — a function in a test location whose name starts with test", name, head),
                "Write tests only with deftest. deftest runs as a doeff Program and checks with an explicit combination of handlers. Tests that are bare functions or fn bindings are called by pytest outside a Program, so they do not go through the swapping of effects and handlers.".to_string(),
            ),
            Explain::DefnForbidden { name, head, .. } => (
                format!("definition {} ({})", name, head),
                "defn cannot have a contract dict and cannot write :tags — it cannot declare layer, role, and context by tags, so it appears in neither the browsing panel nor the linter's role rules. Write it with defk (use deff only when a bare callable cannot be avoided).".to_string(),
            ),
            Explain::DeffWithoutReason { name, marker, problem, kinds } => {
                let marker = marker.trim_end_matches([':', '\u{ff1a}']);
                let list = if kinds.is_empty() {
                    String::new()
                } else {
                    format!(" Accepted reasons: {}.", kinds.iter().map(|k| format!("{} ({})", k.name, k.description)).collect::<Vec<_>>().join(", "))
                };
                let subject = match problem {
                    DeffReasonProblem::Missing => format!("definition {} (deff) — the reason comment `; {}: <reason>` is on neither the definition's line nor the line just before", name, marker),
                    DeffReasonProblem::NoDetail { detail } if detail.is_empty() => format!("definition {} (deff) — the reason in the reason comment is empty", name),
                    DeffReasonProblem::NoDetail { detail } => format!("definition {} (deff) — the reason comment says \"{}\", which is not specific to this definition", name, detail),
                };
                (
                    subject,
                    format!(
                        "deff is an escape hatch that drops defk's contract (:pre, :post and the effect check), so write why it must be a bare function in a sentence specific to this definition (whether it is accepted is decided by Jev's DOEFF203, which looks at the reason sentence and the source).{}",
                        list
                    ),
                )
            }
            Explain::DefinitionTagsMissing { name, head, missing, has_tags, module_default } => (
                format!(
                    "definition {} ({}) lacks {} in :tags{}",
                    name,
                    head,
                    missing.join(", "),
                    if *has_tags { "" } else { " (the contract dict has no :tags at all)" }
                ),
                format!(
                    "A definition declares its context and role with :tags. Without tags, neither the linter nor the browsing panel can know which role in which layer this definition has. {}",
                    if *module_default {
                        "The module-head tag does not fill it in either."
                    } else {
                        "In this repo's settings the module-head tag cannot fill it in (module_default = false), so write it per definition."
                    }
                ),
            ),
            Explain::UndeclaredPlace { rel, problem, root, .. } => (
                match problem {
                    PlaceProblem::DirectlyUnderRoot => format!("module {} — sits directly under root {}/ and is in no service's layer", rel, root),
                    PlaceProblem::DirectlyUnderService { service } => {
                        format!("module {} — sits directly under the dir of service {} and is not in a layer dir (core, intent …)", rel, service)
                    }
                    PlaceProblem::InUndeclaredDirectory { dir } => format!("module {} — dir {} is not a location declared in architecture.hy", rel, dir),
                },
                format!(
                    "Code goes in a service -> layer dir ({}/<service>/<layer>/) or shared or foundation. Layer-first dirs (core, protocol …) and dirs of old features are outside the declarations: which layer of which service it is cannot be told from the location, and neither the layer rules nor browsing can be decided by location. Existing ones are accepted by the registry, but are always visible in the editor.",
                    root
                ),
            ),
            Explain::UndeclaredDirectory { dir, problem } => match problem {
                DirectoryProblem::ServiceNotDeclared { dir: name, services } => (
                    format!("dir {} — service {} is not declared in architecture.hy (declared services = {})", dir, name, if services.is_empty() { "none".to_string() } else { services.join(", ") }),
                    "A service dir not in the declarations does not show what its job is or what it depends on. Declare it with defservice, or move the modules inside to the layer dirs of a declared service.".to_string(),
                ),
                DirectoryProblem::LayerNotDeclared { service, layer, declared } => (
                    format!("dir {} — {} inside service {} is not in the layers the service declared ({})", dir, layer, service, declared.join(", ")),
                    format!("Dirs inside a service are cut only by layer (distance from the outside world). {} is not a declared layer, so the layer of the modules inside cannot be decided. Add it to the service's :layers or move it to a layer dir.", layer),
                ),
            },
            Explain::ServiceDependency { placement, own, target, target_service, target_layer, target_dir, declared, depends_on, open_layers, widened, .. } => (
                format!(
                    "import target {} is in service {}'s {} (path under {}/) — {}",
                    target,
                    target_service,
                    self.layer_phrase(*target_layer),
                    target_dir,
                    self.file_subject(placement)
                ),
                if *declared {
                    format!(
                        "Service {} declares that it depends on {}, but modules in {} may read, of {}, only {}. Reading {}'s judgments or translations directly means that when the contents of {} change, {} breaks. Requests to {} go through {}'s intents.{}",
                        own,
                        target_service,
                        self.layer_phrase(placement.layer),
                        target_service,
                        open_layers.join(", "),
                        target_service,
                        target_service,
                        own,
                        target_service,
                        target_service,
                        if *widened {
                            ""
                        } else {
                            " (the other layers of the target can be read only by a layer that :dependency-layers in the layer of architecture.hy widens: the assembly layer that builds the whole)"
                        }
                    )
                } else {
                    format!(
                        "The declaration of service {}'s dependencies (:depends-on = {}) does not include {}. An undeclared dependency makes the direction between services unreadable from architecture.hy. If the dependency is needed, add it to :depends-on and make requests through {}'s intents; if not, remove the import.",
                        own,
                        if depends_on.is_empty() { "none".to_string() } else { depends_on.join(", ") },
                        target_service,
                        target_service
                    )
                },
            ),
            Explain::UnusedDependency { service, dependency } => (
                format!("{} of :depends-on of service {}", dependency, service),
                format!("No module of {} reads {} (a notice). A declaration of an unused dependency makes the direction between services look larger than the real one. If it is not needed, remove it from :depends-on.", service, dependency),
            ),
            Explain::Semantic { placement, definition, kind, question, probability } => (
                format!("definition {} ({}) — {}", definition, kind, self.file_subject(placement)),
                format!(
                    "Jev's judgment p={:.2} — {}. {} (This is not a deterministic rule but a semantic judgment, and its hit rate is still being measured — if wrong, add it to the list of misjudgments)",
                    probability,
                    question.meaning(),
                    self.character(placement.layer)
                ),
            ),
            Explain::PlainCallableDoubt { definition, kind, stated, chosen, chosen_probability, chosen_accepted, chosen_description, fix, rejected_total } => (
                format!("definition {} ({}) — stated reason \"{}\"", definition, kind, stated),
                match (chosen_accepted, chosen.as_str()) {
                    (true, _) => format!(
                        "Jev's judgment: close to the accepted reason {} ({}) (p={:.2}), but the sum of the probabilities of the rejected answers is {:.2}. Rewrite the reason sentence specifically for this definition, or check whether it can be a defk. (A semantic judgment — if wrong, add it to the list of misjudgments)",
                        chosen,
                        chosen_description.clone().unwrap_or_default(),
                        chosen_probability,
                        rejected_total
                    ),
                    (false, "none") => format!(
                        "Jev's judgment p={:.2}: this reason is not accepted — it matches none of the declared accepted reasons, and no reason why it must be a bare function is visible. Likely it can be a defk by making the caller a Program. (A semantic judgment — if wrong, add it to the list of misjudgments)",
                        chosen_probability
                    ),
                    (false, _) => format!(
                        "Jev's judgment p={:.2}: this reason is not accepted — the closest type is {} ({}). {}(A semantic judgment — if wrong, add it to the list of misjudgments)",
                        chosen_probability,
                        chosen,
                        chosen_description.clone().unwrap_or_default(),
                        fix.as_ref().map(|f| format!("How to fix: {}. ", f)).unwrap_or_default()
                    ),
                },
            ),
            Explain::EnvironmentName { subject, words } => (
                match subject {
                    NameSubject::File { stem } => format!("name of business file {} (contains environment words {})", stem, words.join(", ")),
                    NameSubject::Definition { name, kind } => format!("name of definition {} ({}) (contains environment words {})", name, kind, words.join(", ")),
                },
                format!(
                    "Business code does not know the environment (production, simulation, local). The word(s) {} express a difference of environment, so if they enter a business name, the business answerers split per environment, and a green in the simulation no longer verifies the production path. Express differences of environment only by swapping the foundation's generic handlers.",
                    words.join(", ")
                ),
            ),
        };
        let law_statement = law.map(|l| l.statement.clone()).filter(|s| !s.is_empty());
        Explanation { subject, reason, law_statement }
    }

    /// The fix that varies per violation (if none, the rule's default single line).
    pub fn hint(&self, explain: &Explain) -> Option<String> {
        const WORLD_FIX: &str = "Make the window to the outside world a foundation handler: hold resources (connections, clients, file handles) directly under defhandler with (session val …) and answer effects such as ListRows and PutRow (in the simulation, the simulation's foundation handler)";
        const STATE_FIX: &str = "Values are defrecord (immutable), behavior is pure functions that return new values, state goes in one place in a handler's (session var …) such as world, and changes flow as effects. Even when rewriting is needed for speed, rewrite only inside the handler";
        const PROGRAM_WAYS: &str = "make an assembly (a list of handlers) `(defk handlers-of [foundation])`, a test a deftest, and a helper that builds values a defk called with `(<- …)`";
        match explain {
            Explain::DefnForbidden { declared_kind: Some(kind), .. } => {
                Some(format!("Make it a deff (reason {} — {}). On the same line write `; cannot be a defk: <who calls it and how>` and :tags", kind.name, kind.description))
            }
            Explain::DefnForbidden { declared_kind: None, .. } => {
                Some(format!("Make it a defk (no reason is visible why it must be a bare function): {}", PROGRAM_WAYS))
            }
            Explain::ClassShape { verdict: ClassVerdict::ExternalWorld { .. }, .. } => Some(WORLD_FIX.to_string()),
            Explain::ClassShape { verdict: ClassVerdict::Stateful { .. }, .. } => Some(STATE_FIX.to_string()),
            Explain::Smell { smell } => Some(
                match &smell.kind {
                    super::smells::SmellKind::ShapeCheck { .. } => "Parse shape checks into a defwire type at the protocol boundary (if the shape does not fit, fail where it is resolved), and have this definition receive typed values and only judge",
                    super::smells::SmellKind::FailureRethrow { .. } => "Raise a failure with (<- (Raise failure-value)) (do not re-return it to the caller by hand), and only at the place that receives and maps it, receive it on the calling side with (on-raise body (Refusal r) target) (ADR-DOE-CORE-EFFECTS-003 R3, R7)",
                    super::smells::SmellKind::BindThenReturn { .. } => "Merge it into a single (return (! (f …))), or for a failure raise it with (<- (Raise …))",
                    super::smells::SmellKind::FieldsJoined { .. } => "Pass typed values as they are (do not flatten fields into a string): turn them into a sentence only at the one boundary that shows them to people",
                    super::smells::SmellKind::RebuiltAccumulator { .. } => "Build the accumulation in one go with a comprehension (lfor): do not keep rebuilding with (+ xs #(…)) inside a loop",
                }
                .to_string(),
            ),
            Explain::RetiredWord { instead, .. } => Some(format!(
                "Rewrite it to {}. If it is a contract spelling (a wire field name or a contract value), check whether it is in a contract file of the group's :contract-files (if so, strings and defwire field definitions are not counted)",
                instead
            )),
            Explain::RetiredCall { instead, .. } => Some(format!("Replace it with {}", instead)),
            Explain::StaleBranch { branch } => Some(match branch.declared {
                super::smells::DeclaredAs::Absent => format!(
                    "Remove the {} branch and keep only the success path. Where something should be done on absence, use (<- {} ({} …) :absent <failure>) or absent-as; where absence is carried around as a value, wrap {} in (maybe ({} …)) and receive Nothing (R14 (3))",
                    branch.ty, branch.subject, branch.effect, branch.effect, branch.effect
                ),
                super::smells::DeclaredAs::Failure => format!(
                    "Remove the {} branch and keep only the success path. Where something should be done on failure, use on-raise (on-raise body ({} d) target); where it is carried around as a value, wrap it in (result ({} …)) and receive Err (R14 (3))",
                    branch.ty, branch.ty, branch.effect
                ),
            }),
            Explain::MatchFieldHyphen { field, .. } => Some(format!("Write :{} as :{}, or move that match into a defk (defk rewrites field names to attribute names). Fixing it makes clauses that did not match until now match, so run the tests of that definition", field, field.replace('-', "_"))),
            Explain::WireFree { wire: true, .. } => Some("Move the defwire to the translation layer (protocol), place in this layer a defrecord with no field names, and have protocol read it and pass it".to_string()),
            Explain::WireFree { .. } => Some("Make the mapping a defrecord with field names and types, or make a mapping that fetches data (an index or table) a key -> value function or an effect".to_string()),
            Explain::DictSmell { field: true, .. } => Some("Make the field mapping a defrecord with field names and types".to_string()),
            Explain::DictSmell { .. } => Some("Make a mapping that fetches data a key -> value function or an effect, and put the cache in the handler's session; when building JSON, gather it at the one point where the :post spelling is a mapping".to_string()),
            Explain::EnvironmentBranch { .. } => Some("Make behavior that varies by environment an effect and let the handler of each environment (production, simulation, dry-run) answer it: definitions in a business layer read neither the environment name nor the dry-run flag".to_string()),
            Explain::PlacedDependency { owner_rel, .. } => Some(format!("Move {} into a layer location (<root>/<service>/<layer>/), or move the type you need into an intent and read it from there; list existing hits that cannot be fixed in the registry", owner_rel)),
            Explain::MixedConcerns { .. } => Some("Parse shape checks into a defwire type at the protocol boundary (if the shape does not fit, fail where it is resolved), and have this definition receive typed values and only judge".to_string()),
            Explain::ClassRoleDoubt { chosen, .. } if chosen == "external-world" => Some(WORLD_FIX.to_string()),
            Explain::ClassRoleDoubt { .. } => Some(STATE_FIX.to_string()),
            Explain::ClassShape { verdict: ClassVerdict::DataOnly, .. } => Some("Make it a defrecord (declare context and role with :tags, check values with :check)".to_string()),
            Explain::UndeclaredPlace { destination, .. } => Some(format!("Move it to {} (a proposal inferred from the :context tag for the service and the definition's :role tag for the layer; the current location's layer only when it cannot be inferred)", destination)),
            Explain::ServiceDependency { own, target, target_service, declared: false, reverse: true, .. } => Some(format!(
                "{} already depends on {}, so adding it to :depends-on would make the dependency between services a cycle: move the type {} into {}'s intent or into shared and have {} read it there (align with the existing direction {} -> {})",
                target_service, own, target, own, target_service, target_service, own
            )),
            Explain::JsonValueUse { refusal: JsonValueRefusal::ListedOutsideFoundation { pattern, foundation_dir: Some(dir) }, .. } => Some(format!(
                "Move the sending and receiving itself into a module under {}/ and have :wire-modules point there, or remove {} from :wire-modules and have this module use defwire's typed values",
                dir, pattern
            )),
            Explain::PlainCallableDoubt { chosen_accepted: false, fix: Some(fix), .. } => Some(fix.clone()),
            Explain::PlainCallableDoubt { .. } => Some(format!("Check whether it can be a defk: {}", PROGRAM_WAYS)),
            Explain::DeffWithoutReason { problem: DeffReasonProblem::Missing, .. } => {
                Some(format!("If it must be a bare function, write `; cannot be a defk: <who calls it and how>`. Otherwise make it a defk: {}", PROGRAM_WAYS))
            }
            Explain::DeffWithoutReason { problem: DeffReasonProblem::NoDetail { .. }, .. } => {
                Some(format!("Write a reason specific to this definition (who calls it and how); do not use \"同上\" (\"ditto\"). If you cannot write one, make it a defk: {}", PROGRAM_WAYS))
            }
            _ => None,
        }
    }

    /// What decided a module's layer (the location of the path, a tag, or a mismatch between the two).
    pub fn layer_reason(&self, placement: &Placement) -> String {
        if placement.by_tags {
            return format!(
                "Decided by tag — {}/ is not a location declared in architecture.hy, so it was inferred from tag role = {} as {}",
                placement.dir,
                placement.roles.join(", "),
                self.site_phrase(placement)
            );
        }
        let base = format!("Decided by the path location — under {}/ is {}", placement.dir, self.site_phrase(placement));
        if placement.roles.is_empty() {
            return format!("{} (the role is not declared by a tag)", base);
        }
        let foreign: Vec<&String> = placement.roles.iter().filter(|r| !self.role_allowed(placement.layer, r)).collect();
        if foreign.is_empty() {
            return format!("{}. The tag role = {} is also a role of this layer", base, placement.roles.join(", "));
        }
        let homes: Vec<String> = foreign.iter().map(|r| format!("tag role {} is {}", r, self.role_home(r))).collect();
        format!(
            "the path and the tag disagree — at the path location {}/ it is {}, {} (the layer is decided by the path)",
            placement.dir,
            self.site_phrase(placement),
            homes.join(", ")
        )
    }

    // --- Parts -------------------------------------------------------------------------

    /// The layer's name.
    fn name(&self, id: LayerId) -> String {
        self.layers.and_then(|l| l.layers.get(id.0)).map(|l| l.name.clone()).unwrap_or_else(|| "?".to_string())
    }

    /// "Layer Y of service X (summary)" (just the layer if there is no service segment).
    fn site_phrase(&self, placement: &Placement) -> String {
        match &placement.service {
            Some(service) => format!("{} of service {}", self.layer_phrase(placement.layer), service),
            None => self.layer_phrase(placement.layer),
        }
    }

    /// "Layer X (summary)".
    fn layer_phrase(&self, id: LayerId) -> String {
        let summary = self.layers.and_then(|l| l.layers.get(id.0)).and_then(|l| l.description.summary.clone());
        match summary {
            Some(summary) => format!("layer {} ({})", self.name(id), summary),
            None => format!("layer {}", self.name(id)),
        }
    }

    /// Distance from the outside world (from the order of layers).
    fn distance(&self, id: LayerId) -> String {
        let count = self.layers.map(|l| l.layers.len()).unwrap_or(0);
        match id.0 {
            0 => "the layer farthest from the outside world".to_string(),
            index if count > 0 && index + 1 == count => "the layer closest to the outside world".to_string(),
            index => format!("layer number {} in order of distance from the outside world", index + 1),
        }
    }

    /// The sentence describing a layer's character ("Layer core (summary) is the layer farthest from the outside world; it knows X and does not know Y.").
    fn character(&self, id: LayerId) -> String {
        let description = self.layers.and_then(|l| l.layers.get(id.0)).map(|l| l.description.clone()).unwrap_or_default();
        let mut text = format!("{} is {}", capitalize(&self.layer_phrase(id)), self.distance(id));
        match (&description.knows, &description.does_not_know) {
            (Some(knows), Some(not)) => text.push_str(&format!("; it knows {} and does not know {}.", knows, not)),
            (Some(knows), None) => text.push_str(&format!("; it knows {}.", knows)),
            (None, Some(not)) => text.push_str(&format!("; it does not know {}.", not)),
            (None, None) => text.push('.'),
        }
        text
    }

    /// The character of the import target's layer ("The import target, layer foundation (summary), has X, so").
    fn target_character(&self, id: LayerId) -> String {
        let knows = self.layers.and_then(|l| l.layers.get(id.0)).and_then(|l| l.description.knows.clone());
        match knows {
            Some(knows) => format!("The import target, {}, has {}, so", self.layer_phrase(id), knows),
            None => format!("The import target, {}, is {}, so", self.layer_phrase(id), self.distance(id)),
        }
    }

    /// The subject sentence of a file ("This file is in layer core — path is under controllers/core/, tag role = translation").
    fn file_subject(&self, placement: &Placement) -> String {
        let tags = if placement.roles.is_empty() {
            "the role is not declared by a tag".to_string()
        } else {
            format!("tag role = {}", placement.roles.join(", "))
        };
        format!("This file is in {} — path is under {}/, {}", self.site_phrase(placement), placement.dir, tags)
    }

    /// The list of layers a layer may import.
    fn allowed_imports(&self, id: LayerId) -> String {
        let spec = self.layers.and_then(|l| l.layers.get(id.0));
        match spec.and_then(|s| s.allowed.as_ref()) {
            Some(allowed) => {
                let names: Vec<String> = allowed.iter().map(|a| format!("layer {}", self.name(*a))).collect();
                names.join(", ")
            }
            None => "any layer".to_string(),
        }
    }

    /// The list of roles a layer allows.
    fn allowed_roles(&self, id: LayerId) -> String {
        let spec = self.layers.and_then(|l| l.layers.get(id.0));
        match spec.and_then(|s| s.roles.as_ref()) {
            Some(roles) => roles.iter().cloned().collect::<Vec<_>>().join(" / "),
            None => "not in the settings".to_string(),
        }
    }

    /// Whether a role is allowed in that layer.
    fn role_allowed(&self, id: LayerId, role: &str) -> bool {
        self.layers.and_then(|l| l.layers.get(id.0)).and_then(|s| s.roles.as_ref()).is_none_or(|roles| roles.contains(role))
    }

    /// The phrase for the layers that allow a role ("a role of layer protocol", or "not a role of any layer" if none).
    fn role_home(&self, role: &str) -> String {
        let homes: Vec<String> = self
            .layers
            .map(|l| l.layers.iter().filter(|s| s.roles.as_ref().is_some_and(|r| r.contains(role))).map(|s| format!("layer {}", s.name)).collect())
            .unwrap_or_default();
        if homes.is_empty() {
            "not a role of any layer (not in the current list of roles)".to_string()
        } else {
            format!("a role of {}", homes.join(", "))
        }
    }

    /// The description of a role (if it is in the settings, "role X (description)").
    fn role_phrase(&self, role: &str) -> String {
        match self.layers.and_then(|l| l.role_descriptions.get(role)) {
            Some(text) => format!("role {} ({})", role, text),
            None => format!("role {}", role),
        }
    }

    /// The phrase for the layers allowed raw side effects.
    fn io_layers(&self) -> String {
        if self.raw.is_some_and(|r| r.world_modules.is_some()) {
            return "the modules of the definitions in :world-handlers of architecture.hy (the permitted roster of definitions allowed to touch the outside world)".to_string();
        }
        match self.raw.filter(|r| !r.allowed.is_empty()) {
            Some(raw) => raw.allowed.iter().map(|id| format!("layer {}", self.name(*id))).collect::<Vec<_>>().join(", "),
            None => "the layers allowed to touch the outside world".to_string(),
        }
    }

    /// The subject of DOEFF105 (states the mismatch).
    fn role_subject(&self, placement: &Placement, role: &Option<String>, context: &Option<String>) -> String {
        let role_text = match role.as_deref().filter(|r| !r.is_empty()) {
            Some(role) => format!("tag role = {}", role),
            None => "the tag has no role".to_string(),
        };
        let context_text = match context.as_deref().filter(|c| !c.is_empty()) {
            Some(context) => format!("context = {}", context),
            None => "no context".to_string(),
        };
        let mismatch = match role.as_deref().filter(|r| !r.is_empty() && !self.role_allowed(placement.layer, r)) {
            Some(role) => format!(" — the path and the tag disagree (role {} is {})", role, self.role_home(role)),
            None => String::new(),
        };
        format!(
            "This file is in {} — path is under {}/, {}, {}{}",
            self.site_phrase(placement),
            placement.dir,
            role_text,
            context_text,
            mismatch
        )
    }

    /// The reason for DOEFF105.
    fn role_reason(&self, placement: &Placement, role: &Option<String>, context: &Option<String>) -> String {
        let allowed = self.allowed_roles(placement.layer);
        match (role.as_deref().filter(|r| !r.is_empty()), context.as_deref().filter(|c| !c.is_empty())) {
            (Some(role), _) if !self.role_allowed(placement.layer, role) => format!(
                "{} is {}. {}, so it cannot be placed here. Roles allowed in layer {}: {}.",
                self.role_phrase(role),
                self.role_home(role),
                self.character(placement.layer).trim_end_matches('.').to_string(),
                self.name(placement.layer),
                allowed
            ),
            (None, _) => format!(
                "Without declaring a role, it cannot be checked whether this definition may be placed in layer {}. Roles allowed in layer {}: {}.",
                self.name(placement.layer),
                self.name(placement.layer),
                allowed
            ),
            (Some(_), None) => "Without declaring a context, it cannot be looked up by tag which business a definition belongs to. Write :context too.".to_string(),
            (Some(_), Some(_)) => format!("The tag fits the rules of layer {}.", self.name(placement.layer)),
        }
    }
}

/// The subject and reason of one smell (DOEFF121-125).
fn smell_text(smell: &super::smells::Smell) -> (String, String) {
    use super::smells::SmellKind;
    match &smell.kind {
        SmellKind::ShapeCheck { field, holder } => (
            format!("definition {} — reads the field \"{}\" of {} with a string key and checks the shape with isinstance", smell.definition, field, holder),
            "A check of the JSON shape has entered a layer of judgment. If shape checks are scattered inside judgments, the type cannot separate what is a difference in input shape from what is a business refusal, and the same check is copied at every place that calls it. Resolve the shape into a typed value at the communication boundary, and let judgments see only typed values. (A notice)".to_string(),
        ),
        SmellKind::FailureRethrow { failure_type, subject } => (
            format!("definition {} — a match arm receives the failure type {} and returns {} as it is (or rewrapped)", smell.definition, failure_type, subject),
            "Re-returning a received failure by hand one level at a time is the form of hand-writing the re-raise of an exception. The same arm is needed at every calling level, and if even one place forgets to copy it, the failure flows into the success path. Raise a failure with Raise, and receive it with on-raise only at the boundary where you want to receive and map it. (Failure types are taken from defrecord's :failure True and the :failure / :absent declarations of defeffect)".to_string(),
        ),
        SmellKind::BindThenReturn { name } => (
            format!("definition {} — (<- {} …) is directly followed by only (return {})", smell.definition, name, name),
            format!("{} is a name used only for returning and is not used elsewhere. If the binding and the return are merged into one, no name is needed; if it is returning a failure, the correct form is to raise it with Raise.", name),
        ),
        SmellKind::FieldsJoined { value, fields } => (
            format!("definition {} — joins the fields {} of {} into a single string", smell.definition, fields.join(", "), value),
            "Flattening a typed value into a string leaves the receiver unable to read the fields again (it can only split the string), and the meaning of the fields disappears from the type. Pass the value as it is, and turn it into a sentence only at the boundary that shows it to people.".to_string(),
        ),
        SmellKind::RebuiltAccumulator { name } => (
            format!("definition {} — rebuilds {} every time with (+ {} …) inside a loop", smell.definition, name, name),
            "Rebuilding the accumulation every time repeats copies proportional to the length, which makes the work quadratic, and also adds more rebinding of the name. If it is built in one go with a comprehension, neither rebinding nor rebuilding is needed.".to_string(),
        ),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::project::layers::{LayerDescription, LayerSpec, TagReading};
    use std::collections::{BTreeMap, BTreeSet};

    /// Settings with two layers (core has a description; foundation has none).
    fn layers() -> LayerSettings {
        let spec = |name: &str, dir: &str, roles: &[&str], description: LayerDescription| LayerSpec {
            name: name.to_string(),
            places: vec![crate::project::layers::PlacePattern::parse(dir).unwrap()],
            allowed: Some([LayerId(0)].into_iter().collect()),
            forbid_modules: BTreeSet::new(),
            types_only: false,
            wire_free: false,
            roles: Some(roles.iter().map(|r| r.to_string()).collect()),
            description,
        };
        LayerSettings {
            layers: vec![
                spec(
                    "core",
                    "app/core",
                    &["judgment"],
                    LayerDescription {
                        summary: Some("business judgment".into()),
                        knows: Some("business judgment".into()),
                        does_not_know: Some("means of communication".into()),
                        question: None,
                    },
                ),
                spec("foundation", "app/foundation", &["foundation", "translation"], LayerDescription::default()),
            ],
            infer_root: None,
            role_descriptions: [("translation".to_string(), "translation handler".to_string())].into_iter().collect::<BTreeMap<_, _>>(),
            exclude: BTreeSet::new(),
            extensions: BTreeSet::new(),
            tags: TagReading {
                module_variable_hy: String::new(),
                module_variable_py: String::new(),
                contract_definers: BTreeSet::new(),
                plain_definers: BTreeSet::new(),
                effect_definers: BTreeSet::new(),
                record_definers: BTreeSet::new(),
                function_definers: BTreeSet::new(),
                required: Vec::new(),
                module_default: true,
                require_on: BTreeSet::new(),
            },
        }
    }

    #[test]
    fn role_mismatch_names_both_the_path_and_the_tag() {
        let settings = layers();
        let narrator = Narrator { layers: Some(&settings), raw: None };
        let placement = Placement { layer: LayerId(0), dir: "app/core".into(), service: None, roles: vec!["translation".into()], by_tags: false };
        let explain = Explain::RoleMismatch { placement: placement.clone(), role: Some("translation".into()), context: Some("peer".into()) };
        let law = LawSpec { name: "role-law".into(), adr: None, statement: "role fits the layer".into(), rules: Vec::new(), layers: BTreeSet::new() };
        let out = narrator.explain(&explain, Some(&law));
        assert_eq!(
            out.subject,
            "This file is in layer core (business judgment) — path is under app/core/, tag role = translation, context = peer — the path and the tag disagree (role translation is a role of layer foundation)"
        );
        assert_eq!(
            out.reason,
            "role translation (translation handler) is a role of layer foundation. Layer core (business judgment) is the layer farthest from the outside world; it knows business judgment and does not know means of communication, so it cannot be placed here. Roles allowed in layer core: judgment."
        );
        assert_eq!(out.law_statement.as_deref(), Some("role fits the layer"));
        assert_eq!(
            narrator.layer_reason(&placement),
            "the path and the tag disagree — at the path location app/core/ it is layer core (business judgment), tag role translation is a role of layer foundation (the layer is decided by the path)"
        );
    }

    #[test]
    fn import_direction_uses_descriptions_and_falls_back_without_them() {
        let settings = layers();
        let narrator = Narrator { layers: Some(&settings), raw: None };
        let placement = Placement { layer: LayerId(0), dir: "app/core".into(), service: None, roles: vec!["judgment".into()], by_tags: false };
        let out = narrator.explain(
            &Explain::ImportDirection { placement, target: "app.foundation.io.send".into(), target_layer: LayerId(1), target_dir: "app/foundation".into() },
            None,
        );
        assert!(out.subject.starts_with("import target app.foundation.io.send is in layer foundation (path under app/foundation/)"), "{}", out.subject);
        // foundation has no description: from the order of layers it is only said to be "the layer closest to the outside world".
        assert_eq!(
            out.reason,
            "Layer core (business judgment) is the layer farthest from the outside world; it knows business judgment and does not know means of communication. core may import only layer core. The import target, layer foundation, is the layer closest to the outside world, so reading it from core makes core touch what layer foundation holds (even if handlers are swapped in the simulation, only that place touches the real thing)."
        );
        assert_eq!(out.law_statement, None);
        let untagged = Placement { layer: LayerId(1), dir: "app/foundation".into(), service: None, roles: Vec::new(), by_tags: false };
        assert_eq!(narrator.layer_reason(&untagged), "Decided by the path location — under app/foundation/ is layer foundation (the role is not declared by a tag)");
    }
}
